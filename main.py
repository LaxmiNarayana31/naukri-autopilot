"""
Naukri Profile Automation
"""

import os
import sys
import time
import json
import shutil
import random
import logging
import traceback
import threading
import schedule
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
from groq import Groq

try:
    import PyPDF2
    PDF_SUPPORT = True
except ImportError:
    PDF_SUPPORT = False

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ════════════════════════════════════════════════════════════════
# CONFIG
# ════════════════════════════════════════════════════════════════

RESUME_DIR         = r"Resume"
UPDATED_RESUME_DIR = r"Naukri_Updated_Resume"

NAUKRI_LOGIN_URL   = "https://www.naukri.com/nlogin/login"
NAUKRI_PROFILE_URL = "https://www.naukri.com/mnjuser/profile"

SCHEDULE_TIMES = ["08:30", "12:30", "14:30", "18:00"]
GROQ_MODEL     = "llama-3.3-70b-versatile"

# Will be set dynamically from resume
RESUME_OWNER_NAME = "Resume_Owner"

# ════════════════════════════════════════════════════════════════
# LOAD SECRETS
# ════════════════════════════════════════════════════════════════

load_dotenv(verbose=True)
GROQ_API_KEY    = os.getenv("GROQ_API_KEY")
NAUKRI_EMAIL    = os.getenv("NAUKRI_EMAIL")
NAUKRI_PASSWORD = os.getenv("NAUKRI_PASSWORD")
ADMIN_PASSWORD  = os.getenv("ADMIN_PASSWORD", "")
USE_SESSION_STATE = os.getenv("USE_SESSION_STATE", "True").strip().lower() == "true"
HEADLESS        = True
AUTH_STATE_CACHE: dict | None = None
AUTH_STATE_LOCK = threading.Lock()

# ════════════════════════════════════════════════════════════════
# LOGGING
# ════════════════════════════════════════════════════════════════

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("naukri")


def catch(e: Exception) -> None:
    _, _, tb = sys.exc_info()
    line = tb.tb_lineno if tb else "?"
    log.error("%s: %s  (line %s)", type(e).__name__, e, line)
    log.debug(traceback.format_exc())


# ════════════════════════════════════════════════════════════════
# STEALTH INIT SCRIPT
# Runs in every page before any site JS executes.
# Hides the main bot-detection signals Naukri checks.
# ════════════════════════════════════════════════════════════════

STEALTH_SCRIPT = """
// 1. Hide webdriver flag — the #1 signal checked
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });

// 2. Fake plugins array (real Chrome always has plugins; headless has 0)
Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });

// 3. Fake languages
Object.defineProperty(navigator, 'languages', { get: () => ['en-IN', 'en-US', 'en'] });

// 4. Fake chrome runtime object (absent in headless by default)
window.chrome = { runtime: {}, loadTimes: function(){}, csi: function(){}, app: {} };

// 5. Override permissions so 'notifications' returns real value, not 'denied'
const _origQuery = window.navigator.permissions.query.bind(navigator.permissions);
window.navigator.permissions.query = (p) =>
    p.name === 'notifications'
        ? Promise.resolve({ state: Notification.permission })
        : _origQuery(p);
"""

# ════════════════════════════════════════════════════════════════
# RESUME  —  date-based rename
# ════════════════════════════════════════════════════════════════

def get_today_resume_path() -> Path:
    today    = datetime.today()
    name_part = RESUME_OWNER_NAME.replace(" ", "_").replace(",", "")
    filename = (
        f"{name_part}_"
        f"{today.day}_{today.strftime('%B')}_{today.strftime('%Y')}_Resume.pdf"
    )
    return Path(RESUME_DIR) / filename


def find_latest_resume() -> "Path | None":
    folder = Path(RESUME_DIR)
    if not folder.exists():
        return None
    pdfs = sorted(
        (p for p in folder.glob("*_Resume.pdf") if not p.name.startswith("_patched_")),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return pdfs[0] if pdfs else None


def extract_resume_text(pdf_path: str) -> str:
    """Extract text from PDF resume preserving structure, paragraphs, and line breaks."""
    if not PDF_SUPPORT:
        log.warning("PyPDF2 not installed — cannot extract resume text. Install with: pip install PyPDF2")
        return ""
    try:
        text_parts = []
        with open(pdf_path, 'rb') as f:
            reader = PyPDF2.PdfReader(f)
            for page_num, page in enumerate(reader.pages):
                page_text = page.extract_text()
                if page_text:
                    # Clean up excessive whitespace while preserving structure
                    lines = [line.strip() for line in page_text.split('\n') if line.strip()]
                    page_text_clean = '\n'.join(lines)
                    text_parts.append(page_text_clean)
        
        # Join pages with clear separator to maintain structure
        result = "\n\n".join(text_parts)
        log.info("Extracted %d characters from resume (%d pages)", len(result), len(reader.pages))
        return result
    except Exception as e:
        log.error("Failed to extract resume text: %s", e)
        return ""


def extract_resume_owner_name(client: Groq, resume_text: str) -> str:
    """Extract resume owner's name using LLM."""
    try:
        name_system = (
            "Extract the candidate's full name from the provided resume text. "
            "Return ONLY the name as a JSON object with key 'name'. "
            "Example: {\"name\": \"John Doe\"}\n"
            "No markdown, no code fences, no explanations."
        )
        name_prompt = f"Extract the full name from this resume:\n\n{resume_text[:1500]}"
        
        raw = _groq_call(client, name_system, name_prompt)
        result = _json_from_text(raw)
        name = result.get("name", "").strip()
        
        if name:
            log.info("Extracted resume owner name: %s", name)
            return name
    except Exception as e:
        log.warning("Could not extract name from resume via LLM: %s", e)
    
    return "Resume_Owner"


def prepare_resume() -> str:
    today_path = get_today_resume_path()
    Path(RESUME_DIR).mkdir(exist_ok=True)

    if today_path.exists():
        log.info("Today's resume already exists: %s", today_path.name)
    else:
        latest = find_latest_resume()
        if not latest:
            log.error("No resume PDF found in '%s'. Add one and retry.", RESUME_DIR)
            sys.exit(1)
        shutil.copy2(latest, today_path)
        log.info("Renamed  %s  →  %s", latest.name, today_path.name)
        # Remove the original so the folder stays clean with only one PDF
        if latest.resolve() != today_path.resolve():
            latest.unlink()
            log.info("Cleaned up original: %s", latest.name)

    log.info("Resume selected for upload: %s", today_path.name)
    return str(today_path.resolve())


def archive_uploaded_resume(resume_path: str) -> None:
    archive_dir = Path(UPDATED_RESUME_DIR)
    archive_dir.mkdir(exist_ok=True)
    src = Path(resume_path)
    dest = archive_dir / src.name
    shutil.copy2(src, dest)
    log.info("Uploaded resume archived: %s", dest)


# ════════════════════════════════════════════════════════════════
# GROQ
# ════════════════════════════════════════════════════════════════

def _groq_call(client: Groq, system: str, user: str) -> str:
    resp = client.chat.completions.create(
        model=GROQ_MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user",   "content": user},
        ],
        temperature=0.9,
        max_tokens=300,
    )
    return resp.choices[0].message.content.strip()


def _limit_text(text: str, max_chars: int) -> str:
    text = " ".join(text.split())
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rsplit(" ", 1)[0].rstrip(".,;:-") + "."


def _json_from_text(text: str) -> dict:
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start:end + 1])
        raise


def generate_profile_content(client: Groq, resume_text: str) -> dict:
    """Generate profile content and extract skills from resume using unified LLM instruction."""
    
    global RESUME_OWNER_NAME
    
    # Extract owner name from resume
    owner_name = extract_resume_owner_name(client, resume_text)
    RESUME_OWNER_NAME = owner_name
    log.info("Using resume owner name: %s", RESUME_OWNER_NAME)
    
    # Single unified system instruction
    unified_system = (
        "You are an expert Naukri profile optimizer for AI/ML engineers. "
        "Analyze the provided resume and generate ONLY valid JSON with these keys:\n"
        "1. headline: One punchy Naukri profile headline (max 249 chars, use the full length wisely). Mention the top 2-3 most important technologies/methodologies from the resume.\n"
        "2. summary: A professional 5-7 sentence profile summary (max 999 chars, use the full length — aim for at least 800 chars) based on actual resume experience. Include real technologies mentioned. Personalize with the candidate's actual background.\n"
        "3. project_details: One professional project description (max 999 chars) based on actual projects in the resume. Include architecture, technologies, and business impact.\n"
        "4. skills_to_add: JSON array of 4-6 technical skills extracted from the resume (most relevant and frequently mentioned). Use exact skill names as they appear in resume.\n"
        "5. skills_to_remove: JSON array of 2-3 generic or outdated skills to remove (suggest generic ones if needed for cleanup).\n"
        "6. profile_focus: Brief description of the main technical focus/angle from resume (e.g., 'GenAI/LLMs', 'Backend Systems', etc).\n"
        "Return ONLY valid JSON. No markdown, no code fences. All text should be factual based on resume content."
    )
    
    # Build user prompt with resume context
    user_prompt = f"Candidate: {owner_name}\n\nHere is the resume content:\n\n{resume_text[:4000]}\n\nGenerate the Naukri profile content based ONLY on this resume."
    
    try:
        raw = _groq_call(client, unified_system, user_prompt)
        generated = _json_from_text(raw)
        
        headline = _limit_text(str(generated.get("headline", "")).strip(), 249)
        summary = _limit_text(str(generated.get("summary", "")).strip(), 999)
        project_details = _limit_text(str(generated.get("project_details", "")).strip(), 999)
        skills_to_add = generated.get("skills_to_add", [])
        skills_to_remove = generated.get("skills_to_remove", [])
        
        # Ensure lists
        if not isinstance(skills_to_add, list):
            skills_to_add = [str(skills_to_add)]
        if not isinstance(skills_to_remove, list):
            skills_to_remove = [str(skills_to_remove)]
        
        if not headline or not summary or not project_details:
            raise ValueError("Missing generated content field")
            
        log.info("Generated from resume → Headline: %s", headline[:60])
        log.info("Generated from resume → Summary: %s", summary[:60])
        log.info("Generated from resume → Project: %s", project_details[:60])
        log.info("Extracted skills to ADD → %s", skills_to_add)
        log.info("Extracted skills to REMOVE → %s", skills_to_remove)
        
    except Exception as ex:
        log.warning("Profile generation from resume failed (%s); using fallback.", ex)
        headline = "AI Engineer building production GenAI, RAG and LLM systems"
        summary = _limit_text(
            f"{owner_name} is an AI Engineer with production experience in GenAI, RAG pipelines, and backend systems using Python, FastAPI, LangChain, vector databases, and API integrations.",
            999,
        )
        project_details = _limit_text(
            "Built a production GenAI backend for document intelligence using Python, FastAPI, LangChain, vector retrieval, and LLM orchestration.",
            999,
        )
        skills_to_add = ["Python", "FastAPI", "LangChain", "RAG Pipelines"]
        skills_to_remove = []
    
    return {
        "headline": headline,
        "summary": summary,
        "project_details": project_details,
        "skills_to_add": skills_to_add,
        "skills_to_remove": skills_to_remove,
    }


# ════════════════════════════════════════════════════════════════
# BROWSER SETUP  —  real Chrome + stealth
# ════════════════════════════════════════════════════════════════

def make_browser(pw):
    """
    Prefer real installed Google Chrome (channel='chrome') over
    Playwright's bundled Chromium. Real Chrome has a proper TLS
    fingerprint and extension list that passes bot-detection.
    Falls back to bundled Chromium if Chrome is not installed.
    """
    launch_args = [
        "--disable-blink-features=AutomationControlled",
        "--disable-dev-shm-usage",
        "--no-sandbox",
        "--disable-setuid-sandbox",
        "--disable-infobars",
        "--disable-notifications",
        "--disable-popup-blocking",
    ]

    try:
        browser = pw.chromium.launch(
            channel="chrome",
            headless=HEADLESS,
            args=launch_args,
            slow_mo=60,
        )
        log.info("Launched real Google Chrome (headless=%s)", HEADLESS)
    except Exception as ex:
        log.warning("Real Chrome not found (%s) — using bundled Chromium.", ex)
        browser = pw.chromium.launch(
            headless=HEADLESS,
            args=launch_args,
            slow_mo=60,
        )

    context_options = {
        "viewport": {"width": 1366, "height": 768},
        "user_agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "locale": "en-IN",
        "timezone_id": "Asia/Kolkata",
    }
    with AUTH_STATE_LOCK:
        cached_state = AUTH_STATE_CACHE
    if USE_SESSION_STATE and cached_state:
        context_options["storage_state"] = cached_state
        log.info("Loaded cached browser session from memory.")

    ctx = browser.new_context(**context_options)
    ctx.add_init_script(STEALTH_SCRIPT)
    return browser, ctx


# ════════════════════════════════════════════════════════════════
# HELPERS
# ════════════════════════════════════════════════════════════════

def _loc(page, sel: str):
    """Return a Playwright Locator, handling plain CSS or XPath."""
    stripped = sel.lstrip()
    is_xpath = stripped.startswith(("/", "("))
    return page.locator(f"xpath={sel}") if is_xpath else page.locator(sel)


def _first_usable_locator(page, sel: str, timeout: int = 15_000):
    """Pick a visible/enabled match instead of blindly using the first DOM match."""
    loc = _loc(page, sel)
    loc.first.wait_for(state="attached", timeout=timeout)

    count = loc.count()
    for i in range(count):
        candidate = loc.nth(i)
        try:
            if candidate.is_visible(timeout=1_000) and candidate.is_enabled(timeout=1_000):
                return candidate
        except Exception:
            pass

    return loc.first


def wait_and_fill(page, selectors: list, value: str, timeout: int = 15_000) -> bool:
    for sel in selectors:
        try:
            loc = _first_usable_locator(page, sel, timeout=timeout)
            loc.scroll_into_view_if_needed(timeout=5_000)
            loc.click(timeout=5_000, force=True)
            loc.fill(value, timeout=5_000)
            return True
        except Exception as fill_error:
            log.debug("Normal fill failed for %s: %s", sel, fill_error)
            try:
                loc = _first_usable_locator(page, sel, timeout=3_000)
                loc.evaluate(
                    """(el, value) => {
                        const nativeInputValueSetter =
                            Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
                        nativeInputValueSetter.call(el, value);
                        el.dispatchEvent(new Event('input', { bubbles: true }));
                        el.dispatchEvent(new Event('change', { bubbles: true }));
                    }""",
                    value,
                )
                return True
            except Exception as js_error:
                log.debug("JS fill failed for %s: %s", sel, js_error)
    return False


def wait_and_click(page, selectors: list, timeout: int = 10_000) -> bool:
    for sel in selectors:
        try:
            loc = _first_usable_locator(page, sel, timeout=timeout)
            loc.scroll_into_view_if_needed(timeout=5_000)
            loc.click(timeout=5_000, force=True)
            return True
        except Exception as click_error:
            log.debug("Click failed for %s: %s", sel, click_error)
    return False


def dismiss_popups(page) -> None:
    try:
        closed_pro_popup = page.evaluate(
            """() => {
                const hasProText = [...document.querySelectorAll('body *')]
                    .some(el => /Power up your\\s+profile with/i.test(el.innerText || ''));
                if (!hasProText) return false;

                const visible = el => {
                    const r = el.getBoundingClientRect();
                    return r.width > 0 && r.height > 0;
                };
                const dialog = [...document.querySelectorAll('body *')]
                    .filter(el => visible(el) && /Power up your\\s+profile with/i.test(el.innerText || ''))
                    .sort((a, b) => {
                        const ar = a.getBoundingClientRect();
                        const br = b.getBoundingClientRect();
                        return (br.width * br.height) - (ar.width * ar.height);
                    })[0];
                if (!dialog) return false;

                const bounds = dialog.getBoundingClientRect();
                const candidates = [...document.querySelectorAll('button, span, div, em, svg, i')]
                    .filter(el => visible(el))
                    .filter(el => {
                        const r = el.getBoundingClientRect();
                        return r.left >= bounds.right - 95 &&
                               r.right <= bounds.right + 8 &&
                               r.top >= bounds.top + 15 &&
                               r.bottom <= bounds.top + 95 &&
                               r.width <= 90 &&
                               r.height <= 90;
                    })
                    .sort((a, b) => {
                        const ar = a.getBoundingClientRect();
                        const br = b.getBoundingClientRect();
                        const ac = Math.hypot((ar.left + ar.right) / 2 - (bounds.right - 44), (ar.top + ar.bottom) / 2 - (bounds.top + 44));
                        const bc = Math.hypot((br.left + br.right) / 2 - (bounds.right - 44), (br.top + br.bottom) / 2 - (bounds.top + 44));
                        return ac - bc;
                    });
                if (candidates[0]) {
                    candidates[0].click();
                    return true;
                }

                const x = bounds.right - 44;
                const y = bounds.top + 44;
                const target = document.elementFromPoint(x, y);
                if (!target) return false;
                target.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, clientX: x, clientY: y }));
                target.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, clientX: x, clientY: y }));
                target.dispatchEvent(new MouseEvent('click', { bubbles: true, clientX: x, clientY: y }));
                return true;
            }"""
        )
        if closed_pro_popup:
            time.sleep(0.8)
    except Exception:
        pass

    close_selectors = [
        "button:has-text('Close')",
        "//span[contains(@class,'crossIcon')]",
        "//span[contains(@class,'cross-icon')]",
        "//span[contains(@class,'close')]",
        "//div[contains(@class,'close')]",
        "//*[contains(@class,'cross')]",
        "//*[@alt='cross-icon']",
        "//button[contains(@class,'close-btn')]",
    ]
    for sel in close_selectors:
        try:
            loc = _loc(page, sel)
            if loc.count() > 0:
                loc.first.click(timeout=2_000, force=True)
                time.sleep(0.4)
        except Exception:
            pass

    try:
        page.keyboard.press("Escape")
        time.sleep(0.2)
    except Exception:
        pass


def _wait_for_profile_ready(page, timeout: int = 12_000) -> None:
    ready_selectors = [
        "//div[@id='lazyResumeHead']",
        "//div[contains(@class,'keySkills')]",
        "//*[contains(normalize-space(.),'Resume headline')]",
        "//*[contains(normalize-space(.),'Quick links')]",
    ]
    for sel in ready_selectors:
        try:
            page.wait_for_selector(f"xpath={sel}", timeout=timeout)
            return
        except Exception:
            pass


def goto_profile(page) -> None:
    """Navigate to profile and wait for React to render."""
    if "/mnjuser/profile" in page.url:
        _wait_for_profile_ready(page, timeout=12_000)
        dismiss_popups(page)
        return
    page.goto(NAUKRI_PROFILE_URL, wait_until="domcontentloaded", timeout=30_000)
    try:
        page.wait_for_load_state("networkidle", timeout=6_000)
    except PWTimeout:
        pass
    _wait_for_profile_ready(page, timeout=12_000)
    time.sleep(random.uniform(0.6, 1.0))
    dismiss_popups(page)


def human_delay(lo: float = 0.5, hi: float = 1.5) -> None:
    time.sleep(random.uniform(lo, hi))


# ════════════════════════════════════════════════════════════════
# LOGIN
# ════════════════════════════════════════════════════════════════

def login(page) -> bool:
    """
    Email + password login.
    Naukri login selectors (verified from navchandar/Naukri repo & Medium article):
        #usernameField  — email
        #passwordField  — password
        //*[@type='submit' and normalize-space()='Login']  — button
    The page is React-rendered so we MUST wait for networkidle first.
    """
    try:
        log.info("Opening Naukri login page...")
        page.goto(NAUKRI_LOGIN_URL, wait_until="domcontentloaded", timeout=45_000)

        # Critical: wait for React to inject the login form into the DOM
        try:
            page.wait_for_load_state("networkidle", timeout=25_000)
        except PWTimeout:
            log.warning("networkidle timeout — continuing anyway.")

        human_delay(2.0, 3.0)

        # ── Email ────────────────────────────────────────────────
        email_selectors = [
            "#usernameField",
            "input[type='email']",
            "input[placeholder*='Email' i]",
            "input[placeholder*='email' i]",
            "//input[@id='usernameField']",
            "//input[@type='email']",
            "//input[contains(@placeholder,'Email')]",
            "//input[contains(@placeholder,'email')]",
        ]
        if not wait_and_fill(page, email_selectors, NAUKRI_EMAIL, timeout=20_000):
            # Diagnostic: print all inputs visible on the page
            log.error("Email field NOT FOUND. Diagnosing visible inputs:")
            try:
                inputs = page.locator("input").all()
                for i, inp in enumerate(inputs[:15]):
                    log.error(
                        "  input[%d] id=%r type=%r placeholder=%r name=%r",
                        i,
                        inp.get_attribute("id"),
                        inp.get_attribute("type"),
                        inp.get_attribute("placeholder"),
                        inp.get_attribute("name"),
                    )
                log.error("Page URL: %s", page.url)
                log.error("Page title: %s", page.title())
            except Exception:
                pass
            return False

        log.info("Email filled ✓")
        human_delay(0.6, 1.2)

        # ── Password ─────────────────────────────────────────────
        pass_selectors = [
            "#passwordField",
            "input[type='password']",
            "input[placeholder*='Password' i]",
            "//input[@id='passwordField']",
            "//input[@type='password']",
        ]
        if not wait_and_fill(page, pass_selectors, NAUKRI_PASSWORD, timeout=10_000):
            log.error("Password field not found.")
            return False

        log.info("Password filled ✓")
        human_delay(0.6, 1.2)

        # ── Login button ──────────────────────────────────────────
        login_btn_selectors = [
            "//*[@type='submit' and normalize-space()='Login']",
            "//button[@type='submit' and contains(normalize-space(),'Login')]",
            "//input[@type='submit' and contains(@value,'Login')]",
            "button[type='submit']",
            "//*[@type='submit']",
        ]
        if not wait_and_click(page, login_btn_selectors, timeout=10_000):
            log.error("Login button not found.")
            return False

        log.info("Login button clicked ✓")
        human_delay(4.0, 6.0)

        # Dismiss post-login popups
        skip_selectors = [
            "//*[normalize-space(text())='SKIP AND CONTINUE']",
            "//*[normalize-space(text())='Skip']",
            "//*[normalize-space(text())='SKIP']",
        ]
        wait_and_click(page, skip_selectors, timeout=5_000)
        dismiss_popups(page)
        human_delay(1.0, 2.0)

        # ── Verify ────────────────────────────────────────────────
        current_url = page.url
        log.info("Post-login URL: %s", current_url)

        if "nlogin" in current_url or current_url.rstrip("/").endswith("/login"):
            try:
                err_text = page.locator(
                    "//span[contains(@class,'error')] | //div[contains(@class,'errmsg')]"
                ).first.inner_text(timeout=3_000)
                log.error("Login error on page: %s", err_text)
            except Exception:
                log.error("Still on login page — login likely failed. Check credentials.")
            return False

        log.info("Login successful ✓")
        return True

    except Exception as e:
        catch(e)
        return False


def ensure_logged_in(page, ctx) -> bool:
    """
    Reuse the in-memory browser session when it is still valid. Fall back to
    full login only when Naukri redirects the profile page to login.
    """
    try:
        log.info("Checking saved Naukri session...")
        page.goto(NAUKRI_PROFILE_URL, wait_until="domcontentloaded", timeout=30_000)
        human_delay(0.8, 1.2)
        dismiss_popups(page)

        if "nlogin" not in page.url and "login" not in page.url:
            log.info("Saved session is valid; skipping login.")
            return True

        log.info("Saved session expired or missing; logging in.")
        if not login(page):
            return False

        if USE_SESSION_STATE:
            try:
                with AUTH_STATE_LOCK:
                    global AUTH_STATE_CACHE
                    AUTH_STATE_CACHE = ctx.storage_state()
                log.info("Saved browser session in memory.")
            except Exception as ex:
                log.warning("Could not save browser session: %s", ex)
        return True

    except Exception as e:
        catch(e)
        return False


# ════════════════════════════════════════════════════════════════
# NAUKRI ACTIONS
# ════════════════════════════════════════════════════════════════

def upload_resume(page, resume_path: str) -> bool:
    try:
        log.info("Uploading resume...")
        goto_profile(page)

        # Naukri uses lazyAttachCV or attachCV as the file input ID
        upload_selectors = [
            "//input[@id='lazyAttachCV']",
            "//input[@id='attachCV']",
            "//input[@type='file']",
        ]

        uploaded = False
        for sel in upload_selectors:
            try:
                loc = page.locator(f"xpath={sel}")
                if loc.count() > 0:
                    loc.first.set_input_files(resume_path)
                    log.info("File sent via: %s", sel)
                    uploaded = True
                    break
            except Exception as ex:
                log.debug("Upload attempt (%s): %s", sel, ex)

        if not uploaded:
            log.error("No file upload input found on profile page.")
            return False

        human_delay(2.0, 3.0)

        try:
            page.wait_for_selector("xpath=//*[contains(@class,'updateOn')]", timeout=20_000)
            label = page.locator("xpath=//*[contains(@class,'updateOn')]").first.inner_text()
            today = datetime.today()
            if str(today.day) in label or today.strftime("%b") in label:
                log.info("Resume upload confirmed ✓  (%s)", label.strip())
            else:
                log.warning("Upload label: %s", label.strip())
            archive_uploaded_resume(resume_path)
            dismiss_popups(page)
            return True
        except Exception:
            log.warning("Could not verify upload timestamp — assuming success.")
            archive_uploaded_resume(resume_path)
            dismiss_popups(page)
            return True

    except Exception as e:
        catch(e)
        return False


def _open_section_edit(page, edit_xpaths: list) -> bool:
    for xp in edit_xpaths:
        try:
            loc = _first_usable_locator(page, xp, timeout=4_000)
            loc.scroll_into_view_if_needed(timeout=3_000)
            loc.click(timeout=3_000, force=True)
            log.info("Section edit opened: %s", xp)
            return True
        except Exception:
            pass
    return False


def _open_edit_and_wait_for_field(page, edit_xpaths: list, field_selector: str, section_name: str) -> bool:
    for attempt in range(1, 3):
        dismiss_popups(page)
        _close_modal_if_open(page)

        if not _open_section_edit(page, edit_xpaths):
            continue

        try:
            _loc(page, field_selector).first.wait_for(state="visible", timeout=6_000)
            return True
        except Exception:
            log.warning("%s modal field not visible after edit click (attempt %d).", section_name, attempt)
            dismiss_popups(page)
            _close_modal_if_open(page)

    return False


def _load_profile_section(page, quick_link_text: str, ready_xpath: str, fallback_quick_link: str | None = None) -> bool:
    dismiss_popups(page)
    link_texts = [quick_link_text]
    if fallback_quick_link and fallback_quick_link not in link_texts:
        link_texts.append(fallback_quick_link)

    for text in link_texts:
        try:
            quick_link = page.locator(
                f"xpath=//li[.//span[normalize-space()='{text}']] | "
                f"//span[normalize-space()='{text}']"
            )
            if quick_link.count() > 0:
                dismiss_popups(page)
                quick_link.first.scroll_into_view_if_needed(timeout=3_000)
                quick_link.first.click(timeout=3_000, force=True)
                page.wait_for_selector(f"xpath={ready_xpath}", timeout=5_000)
                return True
        except Exception as ex:
            log.debug("Quick link load failed for %s: %s", text, ex)

    try:
        page.mouse.wheel(0, 3500)
        page.wait_for_selector(f"xpath={ready_xpath}", timeout=5_000)
        return True
    except Exception:
        return False


def _save_modal(page, section_name: str) -> bool:
    save_selectors = [
        "button:has-text('Save')",
        "button:has-text('SAVE')",
        "//button[@type='submit' and contains(normalize-space(),'Save')]",
        "//button[contains(normalize-space(),'Save')]",
        "//*[@role='button' and contains(normalize-space(),'Save')]",
        "//button[contains(@class,'btn-dark-ot') and @type='submit']",
        "//button[contains(@class,'save')]",
        "//button[@type='submit']",
    ]
    if wait_and_click(page, save_selectors, timeout=5_000):
        human_delay(0.8, 1.3)
        try:
            page.wait_for_function("!window.location.href.includes('action=modalOpen')", timeout=8_000)
        except Exception:
            pass
        _close_modal_if_open(page)
        dismiss_popups(page)
        dismiss_popups(page)
        log.info("%s saved ✓", section_name)
        return True

    log.warning("%s save button not found.", section_name)
    return False


def _close_modal_if_open(page) -> None:
    if "action=modalOpen" not in page.url:
        return
    close_selectors = [
        "//span[contains(@class,'crossIcon')]",
        "//span[contains(@class,'cross-icon')]",
        "//*[contains(@class,'crossLayer')]",
        "//*[@alt='cross-icon']",
        "//button[contains(@class,'close')]",
    ]
    wait_and_click(page, close_selectors, timeout=2_000)
    try:
        page.keyboard.press("Escape")
    except Exception:
        pass
    try:
        page.wait_for_function("!window.location.href.includes('action=modalOpen')", timeout=5_000)
    except Exception:
        pass


def update_headline(page, headline: str) -> bool:
    try:
        log.info("Updating headline...")
        goto_profile(page)
        dismiss_popups(page)

        edit_xpaths = [
            "//div[@id='lazyResumeHead']//span[contains(@class,'edit')]",
            "//div[contains(@class,'resumeHead')]//span[contains(@class,'edit')]",
            "//span[@title='Edit resume headline']",
            # widgetHead containing 'Resume Headline' text
            "(//div[contains(@class,'widgetHead') and descendant::*[contains(text(),'Resume Headline')]])[1]//span[contains(@class,'edit')]",
        ]

        if not _open_edit_and_wait_for_field(page, edit_xpaths, "#resumeHeadlineTxt", "Headline"):
            log.warning("Headline edit button not found — skipping.")
            return False

        if not wait_and_fill(page, ["#resumeHeadlineTxt"], headline, timeout=5_000):
            log.warning("Headline textarea not found.")
            return False

        human_delay(0.3, 0.6)
        return _save_modal(page, "Headline")

    except Exception as e:
        catch(e)
        return False


def update_summary(page, summary: str) -> bool:
    try:
        log.info("Updating summary...")
        goto_profile(page)
        dismiss_popups(page)
        summary = _limit_text(summary, 999)
        _load_profile_section(
            page,
            quick_link_text="Profile summary",
            ready_xpath="//div[@id='lazyProfileSummary']",
        )

        edit_xpaths = [
            "//div[@id='lazyProfileSummary']//span[contains(@class,'edit')]",
            "//div[contains(@id,'ProfileSummary')]//span[contains(@class,'edit')]",
            "//div[contains(@id,'profileSummary')]//span[contains(@class,'edit')]",
            "//div[contains(@class,'resumeSummary')]//span[contains(@class,'edit')]",
            "//div[contains(@class,'profileSummary')]//span[contains(@class,'edit')]",
            "//section[contains(@id,'profileSummary')]//span[contains(@class,'edit')]",
            "//span[@title='Edit profile summary']",
            "//span[contains(@title,'Profile Summary') or contains(@title,'profile summary')]",
            "(//div[contains(@class,'widgetHead') and descendant::*[contains(translate(text(),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'profile summary')]])[1]//span[contains(@class,'edit')]",
        ]

        if not _open_edit_and_wait_for_field(page, edit_xpaths, "#profileSummaryTxt", "Summary"):
            log.warning("Summary edit button not found — skipping.")
            try:
                page.screenshot(path="summary_edit_not_found.png", full_page=True)
                log.warning("Saved diagnostic screenshot: summary_edit_not_found.png")
            except Exception:
                pass
            return False

        if not wait_and_fill(page, ["#profileSummaryTxt"], summary, timeout=5_000):
            log.warning("Summary textarea not found.")
            return False

        human_delay(0.3, 0.6)
        return _save_modal(page, "Summary")

    except Exception as e:
        catch(e)
        return False


def update_skills(page, skills_to_add: list, skills_to_remove: list) -> bool:
    try:
        log.info("Updating skills...")
        goto_profile(page)
        dismiss_popups(page)

        edit_xpaths = [
            "//div[contains(@class,'keySkills')]//span[contains(@class,'edit')]",
            "//div[contains(@id,'keySkills')]//span[contains(@class,'edit')]",
            "//span[@title='Edit key skills']",
            "(//div[contains(@class,'widgetHead') and descendant::*[contains(text(),'Key Skills')]])[1]//span[contains(@class,'edit')]",
        ]

        if not _open_section_edit(page, edit_xpaths):
            log.warning("Skills edit button not found — skipping.")
            return False

        human_delay(0.8, 1.2)

        # Remove skills
        for skill in skills_to_remove:
            remove_xpaths = [
                f"//li[contains(normalize-space(.),'{ skill }')]//span[contains(@class,'delete') or contains(@class,'close') or contains(@class,'cross')]",
                f"//span[contains(@class,'chip') and contains(normalize-space(.),'{ skill }')]//span",
            ]
            removed = False
            for xp in remove_xpaths:
                try:
                    loc = page.locator(f"xpath={xp}")
                    if loc.count() > 0:
                        loc.first.click(timeout=3_000)
                        log.info("Removed skill: %s", skill)
                        human_delay(0.3, 0.6)
                        removed = True
                        break
                except Exception:
                    pass
            if not removed:
                log.info("Skill not present (skip remove): %s", skill)

        # Add skills - improved with better waiting and debugging
        skill_input_selectors = [
            "input[placeholder*='skill' i]",
            "input[id*='skill' i]",
            "input[name*='skill' i]",
            "//input[contains(translate(@placeholder,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'skill')]",
        ]

        inp_loc = None
        for sel in skill_input_selectors:
            try:
                loc = _loc(page, sel)
                loc.first.wait_for(state="visible", timeout=5_000)
                inp_loc = loc.first
                log.info("Found skill input field: %s", sel)
                break
            except Exception as e:
                log.debug("Skill input not found with %s: %s", sel, e)

        if inp_loc is None:
            log.warning("Skills input not found — skipping add step.")
        else:
            for skill in skills_to_add:
                try:
                    # Clear input and type skill
                    inp_loc.fill("")
                    human_delay(0.2, 0.4)
                    inp_loc.type(skill, delay=50)  # Slower typing to allow suggestions to load
                    human_delay(0.6, 1.0)  # Wait for dropdown to appear

                    # Log current page state for debugging
                    log.info("Typed skill: %s — waiting for suggestions", skill)

                    # Search for skill suggestions on Naukri with multiple strategies
                    suggestion_xpaths = [
                        # Naukri dropdown suggestions
                        f"//li[contains(text(), '{skill}')]",
                        f"//div[contains(@class,'suggestion')]//span[contains(text(), '{skill}')]",
                        f"//div[@role='option' and contains(text(), '{skill}')]",
                        f"//ul[contains(@class,'suggest')]//li[contains(normalize-space(.), '{skill}')]",
                        f"//*[contains(@class,'ui-menu-item') and contains(normalize-space(.), '{skill}')]",
                        f"//div[contains(@class,'dropDown')]//span[contains(normalize-space(.), '{skill}')]",
                        # Generic dropdown option
                        f"//*[contains(@class,'dropdown') or contains(@class,'menu')]//li[contains(., '{skill}')]",
                    ]
                    
                    selected = False
                    for xp in suggestion_xpaths:
                        try:
                            sug_locs = page.locator(f"xpath={xp}")
                            count = sug_locs.count()
                            if count > 0:
                                log.info("Found %d suggestion(s) for '%s' using xpath", count, skill)
                                sug_locs.first.click(timeout=2_000)
                                log.info("✓ Added verified skill: %s", skill)
                                selected = True
                                human_delay(0.3, 0.6)
                                break
                        except Exception as e:
                            log.debug("Suggestion xpath failed (%s): %s", xp[:50], e)

                    if not selected:
                        # Fallback: try pressing Enter or Tab to add the typed skill
                        try:
                            inp_loc.press("Enter")
                            log.info("Added skill via Enter key (no match found): %s", skill)
                            human_delay(0.3, 0.5)
                        except Exception as e:
                            log.warning("Could not add skill '%s' - no suggestion found and Enter failed: %s", skill, e)
                            # Capture screenshot for debugging
                            try:
                                filename = f"skill_add_failed_{skill.replace(' ', '_')}.png"
                                page.screenshot(path=filename, full_page=True)
                                log.warning("Saved diagnostic screenshot for skill failure: %s", filename)
                            except Exception:
                                pass

                except Exception as ex:
                    log.warning("Error processing skill '%s': %s", skill, ex)

        return _save_modal(page, "Skills")

    except Exception as e:
        catch(e)
        return False


def _fill_current_project_details_modal(page, project_details: str) -> bool:
    if not wait_and_fill(page, ["#projectDetails"], project_details, timeout=5_000):
        log.warning("Project details textarea not found.")
        return False

    human_delay(0.3, 0.6)
    if wait_and_click(page, ["#submitProject"], timeout=4_000):
        human_delay(0.8, 1.3)
        try:
            page.wait_for_function("!window.location.href.includes('action=modalOpen')", timeout=8_000)
        except Exception:
            pass
        _close_modal_if_open(page)
        dismiss_popups(page)
        dismiss_popups(page)
        return True

    return _save_modal(page, "Project details")


def update_project_details(page, project_details: str) -> bool:
    try:
        log.info("Updating project details for all projects...")
        goto_profile(page)
        _close_modal_if_open(page)
        dismiss_popups(page)
        project_details = _limit_text(project_details, 999)

        if not _load_profile_section(
            page,
            quick_link_text="Projects",
            fallback_quick_link="Profile summary",
            ready_xpath="//div[contains(@class,'project-list')]//span[contains(@class,'edit')]",
        ):
            log.warning("Project section not loaded — skipping.")
            try:
                page.screenshot(path="project_edit_not_found.png", full_page=True)
                log.warning("Saved diagnostic screenshot: project_edit_not_found.png")
            except Exception:
                pass
            return False

        project_edit_xpath = "//div[contains(@class,'project-list')]//span[contains(@class,'edit')]"
        total_projects = page.locator(f"xpath={project_edit_xpath}").count()
        if total_projects == 0:
            log.warning("No project edit buttons found — skipping.")
            return False

        updated = 0
        for index in range(1, total_projects + 1):
            log.info("Updating project %d of %d...", index, total_projects)
            _close_modal_if_open(page)
            dismiss_popups(page)
            _load_profile_section(
                page,
                quick_link_text="Projects",
                fallback_quick_link="Profile summary",
                ready_xpath=project_edit_xpath,
            )

            edit_xpaths = [f"({project_edit_xpath})[{index}]"]
            if not _open_edit_and_wait_for_field(page, edit_xpaths, "#projectDetails", f"Project {index}"):
                log.warning("Project %d edit button not found — skipping.", index)
                continue

            if _fill_current_project_details_modal(page, project_details):
                updated += 1
                log.info("Project %d details saved ✓", index)
            else:
                log.warning("Project %d details update failed.", index)

        log.info("Projects updated: %d/%d", updated, total_projects)
        return updated > 0

    except Exception as e:
        catch(e)
        return False


# ════════════════════════════════════════════════════════════════
# MAIN RUN
# ════════════════════════════════════════════════════════════════

def run_once(
    groq_api_key: str | None = None,
    naukri_email: str | None = None,
    naukri_password: str | None = None,
) -> dict:
    """Run one full Naukri profile update cycle.

    Credentials priority: explicit args > .env values.
    This allows the Streamlit UI to pass per-user credentials
    while the scheduler uses the admin's .env credentials.
    """
    # Declare globals at the very top — Python requires this before any use
    global NAUKRI_EMAIL, NAUKRI_PASSWORD, RESUME_OWNER_NAME

    log.info("=" * 60)
    log.info("Naukri Run  —  %s", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    log.info("=" * 60)

    # Resolve credentials: prefer explicit args, fall back to env
    _groq_key = groq_api_key  or GROQ_API_KEY
    _email    = naukri_email  or NAUKRI_EMAIL
    _password = naukri_password or NAUKRI_PASSWORD

    if not _groq_key:
        log.error("GROQ_API_KEY missing — aborting.")
        raise RuntimeError("GROQ_API_KEY is required. Enter it in the credentials form.")
    if not _email or not _password:
        log.error("Naukri credentials missing — aborting.")
        raise RuntimeError("Naukri email and password are required.")

    # Temporarily override globals so login() and other helpers use the right credentials
    _prev_email, _prev_password = NAUKRI_EMAIL, NAUKRI_PASSWORD
    NAUKRI_EMAIL    = _email
    NAUKRI_PASSWORD = _password

    try:
        groq_client = Groq(api_key=_groq_key)

        # ── Step 1: find the latest (un-renamed) PDF to read its text ──────────
        Path(RESUME_DIR).mkdir(exist_ok=True)
        raw_latest = find_latest_resume()
        if not raw_latest:
            log.error("No resume PDF found in '%s'. Add one and retry.", RESUME_DIR)
            raise RuntimeError(f"No resume PDF found in '{RESUME_DIR}'. Upload one first.")

        # ── Step 2: extract text & owner name BEFORE renaming ─────────────────
        resume_text = extract_resume_text(str(raw_latest))
        if not resume_text:
            log.warning("Could not extract resume text — using fallback generation")
            resume_text = "AI Engineer with experience in Python, FastAPI, LangChain, and LLM systems"

        # Set the global owner name so prepare_resume() builds the right filename
        RESUME_OWNER_NAME = extract_resume_owner_name(groq_client, resume_text)
        log.info("Resume owner name detected: %s", RESUME_OWNER_NAME)

        # ── Step 3: rename/copy to today's dated filename with the real name ───
        resume_path = prepare_resume()

        # ── Step 4: generate full profile content ─────────────────────────────
        content = generate_profile_content(groq_client, resume_text)

        with sync_playwright() as pw:
            browser, ctx = make_browser(pw)
            page = ctx.new_page()

            # Block heavy media only — keep JS/CSS so React renders the login form
            page.route(
                "**/*.{png,jpg,jpeg,gif,webp,ico,mp4,webm,woff,woff2,ttf,otf}",
                lambda r: r.abort()
            )

            try:
                if not ensure_logged_in(page, ctx):
                    log.error("Could not establish Naukri session — aborting run.")
                    return

                resume_ok   = upload_resume(page, resume_path)
                headline_ok = update_headline(page, content["headline"])
                summary_ok  = update_summary(page, content["summary"])
                project_ok  = update_project_details(page, content["project_details"])
                skills_ok   = update_skills(page, content["skills_to_add"], content["skills_to_remove"])

                log.info("─" * 40)
                log.info("Resume upload  : %s", "✓" if resume_ok   else "✗")
                log.info("Headline update: %s", "✓" if headline_ok else "✗")
                log.info("Summary update : %s", "✓" if summary_ok  else "✗")
                log.info("Project update : %s", "✓" if project_ok  else "✗")
                log.info("Skills update  : %s", "✓" if skills_ok   else "✗")
                log.info("─" * 40)

                result = {
                    "headline":      content["headline"],
                    "summary":       content["summary"],
                    "project":       content["project_details"],
                    "skills_added":  content["skills_to_add"],
                    "skills_removed": content["skills_to_remove"],
                    "resume_file":   Path(resume_path).name,
                    "resume_ok":     resume_ok,
                    "headline_ok":   headline_ok,
                    "summary_ok":    summary_ok,
                    "project_ok":    project_ok,
                    "skills_ok":     skills_ok,
                    "ran_at":        datetime.now().strftime("%d %b %Y, %H:%M"),
                }

            except Exception as e:
                catch(e)
                result = {}
            finally:
                ctx.close()
                browser.close()
                log.info("Browser closed.")

        log.info("Run complete.\n")
        return result

    finally:
        # Restore previous globals so the scheduler (env-based) is unaffected
        NAUKRI_EMAIL    = _prev_email
        NAUKRI_PASSWORD = _prev_password


# ════════════════════════════════════════════════════════════════
# LAST UPDATE TRACKING
# ════════════════════════════════════════════════════════════════

# ════════════════════════════════════════════════════════════════
# SCHEDULER
# ════════════════════════════════════════════════════════════════

def run_scheduler() -> None:
    log.info("Scheduler started. Will run at: %s (IST)", ", ".join(SCHEDULE_TIMES))
    for t in SCHEDULE_TIMES:
        schedule.every().day.at(t).do(run_once)
    run_once()
    while True:
        schedule.run_pending()
        time.sleep(30)


# ════════════════════════════════════════════════════════════════
# ENTRY POINT
# ════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    if "--schedule" in sys.argv:
        run_scheduler()
    else:
        run_once()
