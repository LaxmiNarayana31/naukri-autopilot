"""Streamlit control panel for Naukri profile automation."""

from __future__ import annotations

import threading
from datetime import datetime, time as dt_time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import streamlit as st
from dotenv import load_dotenv

import main

load_dotenv(verbose=True)

IST = ZoneInfo("Asia/Kolkata")
SCHEDULE_TIMES = [
    dt_time(8, 30),
    dt_time(12, 30),
    dt_time(14, 30),
    dt_time(18, 0),
]

SCHED_LOCK = threading.Lock()
SCHED_THREAD: threading.Thread | None = None
SCHED_STOP: threading.Event | None = None


def ist_now() -> datetime:
    return datetime.now(tz=IST)


def next_runs(count: int = 4) -> list[datetime]:
    now = ist_now()
    runs: list[datetime] = []
    cursor = now
    while len(runs) < count:
        for slot in SCHEDULE_TIMES:
            candidate = datetime.combine(cursor.date(), slot, tzinfo=IST)
            if candidate > now:
                runs.append(candidate)
                if len(runs) >= count:
                    break
        cursor = datetime.combine(cursor.date() + timedelta(days=1), dt_time(0, 0), tzinfo=IST)
    return runs


def _scheduler_loop(stop_event: threading.Event) -> None:
    executed: set[str] = set()
    last_date = None
    while not stop_event.is_set():
        now = ist_now()
        today = now.date()
        current_minute = now.strftime("%H:%M")
        if last_date is None or today != last_date:
            executed.clear()
            last_date = today
        for run_time in SCHEDULE_TIMES:
            key = f"{today.isoformat()}_{run_time.strftime('%H:%M')}"
            if key in executed:
                continue
            if current_minute == run_time.strftime("%H:%M"):
                try:
                    main.run_once()
                    st.session_state.last_update_time = ist_now().isoformat()
                except Exception as exc:
                    main.catch(exc)
                executed.add(key)
        stop_event.wait(20)


def scheduler_status() -> tuple[bool, int | None]:
    with SCHED_LOCK:
        alive = SCHED_THREAD is not None and SCHED_THREAD.is_alive()
        tid = SCHED_THREAD.native_id if alive else None
        return alive, tid


def ensure_scheduler_running() -> None:
    global SCHED_THREAD, SCHED_STOP
    with SCHED_LOCK:
        if SCHED_THREAD is not None and SCHED_THREAD.is_alive():
            return
        SCHED_STOP = threading.Event()
        SCHED_THREAD = threading.Thread(
            target=_scheduler_loop, args=(SCHED_STOP,),
            daemon=True, name="naukri-ist-scheduler",
        )
        SCHED_THREAD.start()


def save_uploaded_resume(uploaded_file) -> Path:
    resume_dir = Path(main.RESUME_DIR)
    resume_dir.mkdir(exist_ok=True)
    dest = resume_dir / uploaded_file.name
    dest.write_bytes(uploaded_file.getbuffer())
    return dest


# ── Session state ────────────────────────────────────────────────
_defaults: dict = {
    "page": "gate",
    "is_admin": False,
    "last_update_time": None,
    "uploaded_resume_name": None,
    "user_groq_key": "",
    "user_email": "",
    "user_password": "",
    "last_run_result": None,   # dict returned by run_once()
}
for k, v in _defaults.items():
    if k not in st.session_state:
        st.session_state[k] = v

# ── Page config ──────────────────────────────────────────────────
st.set_page_config(page_title="Naukri Autopilot", page_icon="🚀", layout="wide")

# ── Global CSS ───────────────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');
html, body, [class*="css"] { font-family: 'Inter', sans-serif; }
#MainMenu, footer, header { visibility: hidden; }

/* kill the default streamlit top padding */
.block-container { padding-top: 1.5rem !important; padding-bottom: 0.5rem !important; }

/* ── badges ── */
.badge-admin {
    background: linear-gradient(90deg,#7c3aed,#6366f1); color:#fff;
    border-radius:8px; padding:4px 14px; font-size:12px; font-weight:700; display:inline-block;
}
.badge-user {
    background:#1e3a5f; color:#93c5fd; border:1px solid #3b82f6;
    border-radius:8px; padding:4px 14px; font-size:12px; font-weight:700; display:inline-block;
}
/* ── sidebar pills ── */
.pill-green {
    background:#14532d; color:#86efac; border:1px solid #16a34a;
    border-radius:999px; padding:3px 12px; font-size:12px; font-weight:600; display:inline-block;
}
.pill-amber {
    background:#451a03; color:#fcd34d; border:1px solid #d97706;
    border-radius:999px; padding:3px 12px; font-size:12px; font-weight:600; display:inline-block;
}
.section-label {
    font-size:10px; font-weight:700; letter-spacing:.12em;
    text-transform:uppercase; color:#64748b; margin-bottom:6px;
}
.run-row {
    display:flex; align-items:center; gap:8px; padding:4px 0;
    border-bottom:1px solid #1e293b; font-size:12px; color:#cbd5e1;
}
.run-dot { width:6px; height:6px; border-radius:50%; background:#6366f1; flex-shrink:0; }

/* ── result cards ── */
.result-card {
    background:#0f172a; border:1px solid #1e293b; border-radius:12px;
    padding:14px 16px; margin-bottom:10px;
}
.result-label {
    font-size:10px; font-weight:700; letter-spacing:.1em;
    text-transform:uppercase; color:#6366f1; margin-bottom:4px;
}
.result-value { font-size:13px; color:#e2e8f0; line-height:1.55; }
.status-ok   { color:#4ade80; font-weight:700; }
.status-fail { color:#f87171; font-weight:700; }
.skill-chip {
    display:inline-block; background:#1e3a5f; color:#93c5fd;
    border-radius:6px; padding:2px 10px; font-size:12px; margin:2px 3px 2px 0;
}
.skill-chip-red {
    display:inline-block; background:#3b0d0d; color:#fca5a5;
    border-radius:6px; padding:2px 10px; font-size:12px; margin:2px 3px 2px 0;
}
/* ── sidebar: no scroll ── */
section[data-testid="stSidebar"] > div:first-child {
    overflow-y: hidden !important;
}
</style>
""", unsafe_allow_html=True)


# ════════════════════════════════════════════════════════════════
# PAGE 1 — GATE
# ════════════════════════════════════════════════════════════════
if st.session_state.page == "gate":
    _, mid, _ = st.columns([1, 1.6, 1])
    with mid:
        st.markdown("<br>", unsafe_allow_html=True)
        st.markdown("## 🚀 Naukri Autopilot")
        st.markdown("AI-powered daily Naukri profile updates — headline, summary, skills, and resume, all automated.")
        st.divider()
        st.markdown("#### Sign in")

        entered_pw = st.text_input(
            "Admin password", type="password",
            placeholder="Enter admin password…",
            label_visibility="collapsed",
        )
        col1, col2 = st.columns(2)
        with col1:
            if st.button("🔑  Login as Admin", type="primary", use_container_width=True):
                if main.ADMIN_PASSWORD and entered_pw == main.ADMIN_PASSWORD:
                    st.session_state.is_admin = True
                    st.session_state.page = "main"
                    st.rerun()
                else:
                    st.error("Incorrect password.")
        with col2:
            if st.button("👤  Use my own credentials", use_container_width=True):
                st.session_state.is_admin = False
                st.session_state.page = "credentials"
                st.rerun()

        st.caption("Don't have the admin password? Click **Use my own credentials**.")
    st.stop()


# ════════════════════════════════════════════════════════════════
# PAGE 2 — CREDENTIALS
# ════════════════════════════════════════════════════════════════
if st.session_state.page == "credentials":
    _, mid, _ = st.columns([1, 1.4, 1])
    with mid:
        st.markdown("<br>", unsafe_allow_html=True)
        st.markdown("## 🔑 Your Credentials")
        st.caption("Used only for this session. Never stored on the server.")
        st.divider()

        st.session_state.user_groq_key = st.text_input(
            "Groq API Key", value=st.session_state.user_groq_key,
            type="password", placeholder="gsk_…",
        )
        st.session_state.user_email = st.text_input(
            "Naukri Email", value=st.session_state.user_email,
            placeholder="you@example.com",
        )
        st.session_state.user_password = st.text_input(
            "Naukri Password", value=st.session_state.user_password,
            type="password", placeholder="Your Naukri password",
        )
        st.caption("🔒 Get a free Groq key at [console.groq.com/keys](https://console.groq.com/keys)")
        st.info("ℹ️ **This app only updates your Naukri profile** (resume, headline, summary, skills). It does **not** apply to any jobs on your behalf.")
        st.write("")

        col1, col2 = st.columns([1, 2])
        with col1:
            if st.button("← Back", use_container_width=True):
                st.session_state.page = "gate"
                st.rerun()
        with col2:
            all_filled = bool(
                st.session_state.user_groq_key
                and st.session_state.user_email
                and st.session_state.user_password
            )
            if st.button("Continue →", type="primary", disabled=not all_filled, use_container_width=True):
                st.session_state.page = "main"
                st.rerun()

        if not all_filled:
            st.caption("Fill all three fields to continue.")
    st.stop()


# ════════════════════════════════════════════════════════════════
# PAGE 3 — MAIN AUTOPILOT
# ════════════════════════════════════════════════════════════════
ensure_scheduler_running()

# ── Sidebar ──────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## 🚀 Autopilot")
    st.caption(f"🕐 {ist_now().strftime('%d %b %Y, %H:%M')} IST")

    if st.session_state.is_admin:
        st.markdown('<span class="badge-admin">👑 Admin</span>', unsafe_allow_html=True)
    else:
        st.markdown(f'<span class="badge-user">🙋 {st.session_state.user_email}</span>', unsafe_allow_html=True)

    st.divider()

    alive, _ = scheduler_status()
    st.markdown('<div class="section-label">Scheduler</div>', unsafe_allow_html=True)
    if alive:
        st.markdown('<span class="pill-green">● Active</span>', unsafe_allow_html=True)
    else:
        st.markdown('<span class="pill-amber">⚠ Stopped</span>', unsafe_allow_html=True)

    st.divider()

    st.markdown('<div class="section-label">Last Update</div>', unsafe_allow_html=True)
    if st.session_state.last_update_time:
        try:
            dt_obj = datetime.fromisoformat(st.session_state.last_update_time)
            st.success(dt_obj.strftime("%d %b %Y, %H:%M IST"))
        except Exception:
            st.info(st.session_state.last_update_time)
    else:
        st.caption("No updates yet")

    st.divider()

    st.markdown('<div class="section-label">Next Scheduled Runs</div>', unsafe_allow_html=True)
    for run_at in next_runs(4):
        st.markdown(
            f'<div class="run-row"><div class="run-dot"></div>{run_at.strftime("%a %d %b  %H:%M")}</div>',
            unsafe_allow_html=True,
        )

    st.divider()
    if st.button("🚪 Sign out", use_container_width=True):
        for k, v in _defaults.items():
            st.session_state[k] = v
        st.rerun()

# ── Disclaimer banner ───────────────────────────────────────────
st.info("ℹ️ **This app only updates your Naukri profile** (resume, headline, summary & skills) to keep it fresh and increase your recruiter visibility. It does ⬇️ **not** apply to any jobs on your behalf.")

# ── Header ───────────────────────────────────────────────────────
st.markdown("### 🚀 Naukri Autopilot")
if st.session_state.is_admin:
    st.markdown('<span class="badge-admin">👑 Admin — pre-configured credentials</span>', unsafe_allow_html=True)
else:
    st.markdown(f'<span class="badge-user">🙋 {st.session_state.user_email}</span>', unsafe_allow_html=True)

st.divider()

# ── Two tabs: Run | Last Results ─────────────────────────────────
tab_run, tab_result = st.tabs(["⚡ Run", "📊 Last Results"])

# ── TAB 1: Run ───────────────────────────────────────────────────
with tab_run:
    left, right = st.columns([1.1, 1], gap="large")

    with left:
        st.markdown("**📄 Upload Resume**")
        st.caption("Upload a PDF — the filename is built automatically from the AI-extracted name.")

        uploaded = st.file_uploader("Choose a PDF", type=["pdf"], label_visibility="collapsed")
        if uploaded is not None:
            save_uploaded_resume(uploaded)
            st.session_state.uploaded_resume_name = uploaded.name
            st.success(f"✅ **{uploaded.name}** ready")
        elif st.session_state.uploaded_resume_name:
            st.info(f"📎 **{st.session_state.uploaded_resume_name}**")

        existing = sorted(Path(main.RESUME_DIR).glob("*.pdf")) if Path(main.RESUME_DIR).exists() else []
        if existing:
            with st.expander(f"📁 Folder ({len(existing)} PDF{'s' if len(existing) != 1 else ''})"):
                st.caption("At least one resume must remain for the process to work.")
                for i, pdf in enumerate(existing):
                    size_kb = pdf.stat().st_size // 1024
                    is_last = len(existing) == 1
                    c_name, c_del = st.columns([5, 1])
                    with c_name:
                        st.markdown(
                            f"`{pdf.name}` <span style='color:#64748b;font-size:11px'>{size_kb} KB</span>",
                            unsafe_allow_html=True,
                        )
                    with c_del:
                        if st.button(
                            "\U0001f5d1\ufe0f", key=f"del_{i}",
                            disabled=is_last,
                            help="Cannot delete the last resume" if is_last else f"Delete {pdf.name}",
                            use_container_width=True,
                        ):
                            pdf.unlink()
                            if st.session_state.uploaded_resume_name == pdf.name:
                                st.session_state.uploaded_resume_name = None
                            st.rerun()

    with right:
        st.markdown("**⚡ Trigger Update**")
        st.caption("Runs resume upload, headline, summary, skills, and projects end-to-end.")
        st.write("")

        has_resume = bool(existing or uploaded)
        if not has_resume:
            st.warning("Upload a resume first ←")

        run_clicked = st.button(
            "🚀  Run now", type="primary",
            disabled=not has_resume, use_container_width=True,
        )

        if run_clicked:
            with st.spinner("Updating Naukri profile… this may take a minute."):
                try:
                    if st.session_state.is_admin:
                        result = main.run_once()
                    else:
                        result = main.run_once(
                            groq_api_key=st.session_state.user_groq_key,
                            naukri_email=st.session_state.user_email,
                            naukri_password=st.session_state.user_password,
                        )
                    st.session_state.last_update_time = ist_now().isoformat()
                    st.session_state.last_run_result = result
                    st.success("✅ Done! See **Last Results** tab for details.")
                except RuntimeError as exc:
                    st.error(f"❌ {exc}")
                except Exception as exc:
                    st.error(f"❌ Unexpected error: {exc}")

# ── TAB 2: Last Results ──────────────────────────────────────────
with tab_result:
    r = st.session_state.last_run_result

    if not r:
        st.info("No run yet this session. Click **Run now** to see results here.")
    else:
        ran_at = r.get("ran_at", "")
        st.caption(f"Last run: **{ran_at}**")

        # Status row
        statuses = {
            "Resume":   r.get("resume_ok"),
            "Headline": r.get("headline_ok"),
            "Summary":  r.get("summary_ok"),
            "Projects": r.get("project_ok"),
            "Skills":   r.get("skills_ok"),
        }
        cols = st.columns(len(statuses))
        for col, (label, ok) in zip(cols, statuses.items()):
            with col:
                icon = "✅" if ok else "❌"
                color = "#4ade80" if ok else "#f87171"
                st.markdown(
                    f"<div style='text-align:center;background:#0f172a;border:1px solid #1e293b;"
                    f"border-radius:10px;padding:10px 4px'>"
                    f"<div style='font-size:20px'>{icon}</div>"
                    f"<div style='font-size:11px;color:{color};font-weight:700;margin-top:4px'>{label}</div>"
                    f"</div>",
                    unsafe_allow_html=True,
                )

        st.write("")

        # Resume filename
        if r.get("resume_file"):
            st.markdown(
                f'<div class="result-card"><div class="result-label">📄 Resume Uploaded As</div>'
                f'<div class="result-value"><code>{r["resume_file"]}</code></div></div>',
                unsafe_allow_html=True,
            )

        # Headline
        if r.get("headline"):
            st.markdown(
                f'<div class="result-card"><div class="result-label">📝 Headline Updated</div>'
                f'<div class="result-value">{r["headline"]}</div></div>',
                unsafe_allow_html=True,
            )

        # Summary
        if r.get("summary"):
            st.markdown(
                f'<div class="result-card"><div class="result-label">📋 Profile Summary Updated</div>'
                f'<div class="result-value">{r["summary"]}</div></div>',
                unsafe_allow_html=True,
            )

        # Skills
        added = r.get("skills_added", [])
        removed = r.get("skills_removed", [])
        if added or removed:
            chips_added = "".join(f'<span class="skill-chip">+ {s}</span>' for s in added)
            chips_removed = "".join(f'<span class="skill-chip-red">- {s}</span>' for s in removed)
            st.markdown(
                f'<div class="result-card"><div class="result-label">🛠 Skills Updated</div>'
                f'<div class="result-value" style="margin-top:6px">'
                f'{chips_added}{chips_removed}'
                f'</div></div>',
                unsafe_allow_html=True,
            )
