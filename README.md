# FacultyMailer

A Windows desktop app (PySide6) that automates the mechanics of reaching out
to faculty for HigherStudy/Masters/PhD/postdoc/research-position inquiries: it scrapes a department's
faculty directory, filters the list down to people worth emailing, optionally
personalizes each message with AI, and sends through Gmail with a
human-approved preview step and a daily send cap.

It exists to remove the tedious parts of a large outreach campaign (copying
names/emails out of a directory page, writing the same email 50 times) while
keeping a human in the loop before anything actually goes out.

## How it works

1. **Scrape** — point it at a university department's faculty directory URL;
   `scraper/universal_scraper.py` parses names, emails, titles, and bio text
   from the page (works across differently-structured university sites, not
   one specific template).
2. **Filter** — `recipient_filter.py` turns the raw scrape into a safe,
   targeted send list:
   - drops non-targets (president, dean, chair, emeritus, adjunct, staff,
     role accounts like `info@`, `webmaster@`)
   - drops directory-label junk the scraper occasionally picks up ("All
     Faculty", "Current Students", etc.)
   - dedupes by email
   - optionally keeps only faculty whose title/bio matches your research
     keywords
   - enforces a hard cap on recipients per run
   - produces a preview you must approve before any email is sent
3. **Personalize (optional)** — `ai_personalizer.py` calls the Gemini API to
   generate a 1–2 sentence, specific connection between your background and
   each professor's research, from their scraped bio text.
4. **Load the campaign** — `parser/folder_loader.py` reads a campaign folder
   containing:
   - `Subject.txt` — the email subject line
   - `Email.docx` — the email body template
   - `CV_FAC.docx` or `CV_FAC.pdf` — the CV to attach
5. **Send** — `mailer/gmail_sender.py` sends through Gmail SMTP (SSL, one
   reused connection, per-send delay) using a Gmail **App Password** (not
   your account password).
6. **Track** — sends are logged to `send_history.json` (rolling 30-day
   window) to support a daily send cap, and a run summary can be exported to
   Excel.

## Project structure

```
FacultyMailer/
├── main.py                  # PySide6 GUI entry point
├── ai_personalizer.py       # Gemini-based email personalization
├── recipient_filter.py      # Filters/dedupes/caps the scraped list
├── build_installer.py       # PyInstaller build script -> dist/FacultyMailer-Setup.exe
├── FacultyMailer-Setup.spec # PyInstaller spec
├── requirements.txt
├── scraper/
│   └── universal_scraper.py # Faculty directory scraper
├── parser/
│   └── folder_loader.py     # Loads Subject.txt / Email.docx / CV from a campaign folder
├── mailer/
│   └── gmail_sender.py      # Gmail SMTP sending
└── search/
    └── fallback_search.py   # Fallback web search when a directory page can't be found directly
```

## Setup

Requires Python 3.12+.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python main.py
```

On first run, the app creates a local `settings.json` (via the GUI) to store
your Gmail address, Gmail app password, and Gemini API key. **This file is
gitignored and must never be committed** — see Security below.

### Gmail App Password

The app sends via Gmail SMTP, which requires an **App Password**, not your
normal Gmail password:

1. Enable 2-Step Verification on the sending Google account.
2. Generate a 16-character app password at
   [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords).
3. Paste it into the app's Gmail settings field.

A `534 Application-specific password required` error means this step hasn't
been done yet.

### Gemini API key (optional)

Only needed if you enable AI personalization. Get a key from
[Google AI Studio](https://aistudio.google.com/) and paste it into the app's
AI settings field.

## Building a standalone installer

```powershell
build_env\Scripts\Activate.ps1
python build_installer.py --debug
```

Produces `dist/FacultyMailer-Setup.exe` (a windowed, one-file PyInstaller
build). Pass `--debug` to keep a console window attached for troubleshooting.

## Security & responsible use

- `settings.json` and `send_history.json` are runtime-generated and
  gitignored — they hold your Gmail app password and Gemini API key in
  plaintext locally. Never commit them. If either credential is ever
  exposed, rotate it immediately (Gmail: revoke the app password and
  generate a new one; Gemini: regenerate the key in AI Studio).
- The filtering step exists specifically to avoid mass-emailing people who
  didn't ask for it (department heads, deans, role accounts) and to cap
  volume — always review the preview before sending, and keep the daily cap
  reasonable to stay within Gmail's sending limits and avoid your account
  being flagged.
- This tool is intended for targeted, individually-relevant academic outreach
  (e.g. postdoc inquiries to faculty whose research you've actually read),
  not bulk/unsolicited marketing email.
