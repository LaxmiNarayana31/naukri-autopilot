# 🚀 Naukri Autopilot

> AI-powered Naukri profile automation — upload your resume, and let it auto-update your headline, summary, skills, and projects daily using your own credentials.

**This app only updates your Naukri profile. It does NOT apply to any jobs on your behalf.**

---

## ✨ What it does

Every time it runs, it:

1. **Extracts your name** from the uploaded resume using an LLM and renames the file as `Firstname_Lastname_DD_Mon_YYYY_Resume.pdf`
2. **Uploads the resume** to your Naukri profile
3. **Generates a headline** (up to 249 chars) tailored to your resume content
4. **Generates a profile summary** (up to 999 chars) based on your actual experience
5. **Updates skills** — adds relevant ones, removes generic ones
6. **Updates project descriptions** based on your resume
7. **Runs automatically** at `08:30`, `12:30`, `14:30`, and `18:00` IST — keeping your profile fresh and visible to recruiters

---

## 🖥️ Streamlit Web UI (Recommended)

The app has a clean 3-page flow:

```
Gate (password) → [Admin] → Autopilot page
                → [Own credentials] → Credentials form → Autopilot page
```

### Admin mode
If you're the owner/deployer, enter the admin password → no need to type Naukri credentials each time. They're pre-loaded from `.env`.

### User mode
Anyone can click **"Use my own credentials"**, enter their Groq API key, Naukri email, and password → the automation runs on **their** Naukri account, not yours.

---

## 🛠️ Local Setup

### Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (recommended) or pip
- A free [Groq API key](https://console.groq.com/keys)
- Your Naukri account credentials

### 1. Clone the repo

```bash
git clone https://github.com/your-username/naukri-autopilot.git
cd naukri-autopilot
```

### 2. Install dependencies

```bash
# With uv (recommended)
uv sync

# Or with pip
pip install -r requirements.txt
```

### 3. Install Playwright browsers

```bash
playwright install chromium
# or
uv run playwright install chromium
```

### 4. Create your `.env` file

```bash
cp .env.example .env
```

Edit `.env` with your values:

```env
GROQ_API_KEY=gsk_...          # From https://console.groq.com/keys
NAUKRI_EMAIL=you@example.com  # Your Naukri login email
NAUKRI_PASSWORD=yourpassword  # Your Naukri login password
ADMIN_PASSWORD=choose_a_secret_password  # Password to access admin mode in the UI
```

> **Note:** `HEADLESS` is hardcoded to `True` — the browser always runs in the background. No config needed.

### 5. Run the Streamlit UI

```bash
uv run streamlit run app.py
# or
streamlit run app.py
```

Open `http://localhost:8501` in your browser.

### 6. Upload your resume & run

1. Enter the admin password (or click "Use my own credentials")
2. Upload your PDF resume on the main page
3. Click **🚀 Run now**
4. Switch to the **📊 Last Results** tab to see what was updated

---

## 🗂️ Project Structure

```
naukri-autopilot/
├── app.py              # Streamlit UI (3-page flow: gate → credentials → autopilot)
├── main.py             # Core automation engine (Playwright + Groq)
├── pyproject.toml      # Dependencies
├── .env.example        # Environment variable template
├── .gitignore
└── README.md
```

### Key folders (auto-created, gitignored)

| Folder | Purpose |
|--------|---------|
| `Resume/` | Drop your PDF here — always keeps exactly 1 file (the latest dated copy) |
| `Naukri_Updated_Resume/` | Archive of every resume that was successfully uploaded |

---

## ⚙️ How the resume renaming works

When you upload `MyResume.pdf`:
1. LLM extracts your name → e.g. `Laxmi Narayana Pattanayak`
2. File is renamed to `Laxmi_Narayana_Pattanayak_18_May_2026_Resume.pdf`
3. Original is deleted — folder stays clean with 1 file
4. After upload, a copy is archived to `Naukri_Updated_Resume/`

---

## 🔑 Getting a Free Groq API Key

1. Go to [console.groq.com/keys](https://console.groq.com/keys)
2. Sign up / log in (free)
3. Click **Create API Key**
4. Copy and paste it into the credentials form or `.env`

The LLM model used is `llama-3.3-70b-versatile` — free tier is generous.

---

## ⏰ Scheduler

The scheduler runs automatically inside the Streamlit app (background thread). It fires at these IST times every day:

| Time | IST |
|------|-----|
| Morning | 08:30 |
| Afternoon | 12:30 |
| Post-lunch | 14:30 |
| Evening | 18:00 |

Keep the Streamlit tab open (or deploy it) to keep the scheduler alive.

---

## 📋 Notes

- Runs with real Google Chrome if installed, falls back to Playwright's bundled Chromium
- In-memory session cache avoids re-logging in on every run
- The browser is run in headless mode by default (`HEADLESS=True`) — set to `False` to watch it work
- Logs are printed to the console

---

## 📄 License

MIT — free to use, modify, and deploy.
