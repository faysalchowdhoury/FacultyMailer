# FacultyMailer patch — where each file goes

This folder mirrors your project layout. Copy files into your
`FacultyMailer/` project at the SAME relative path shown below.

Your project root is:  C:\Users\sheel\Downloads\FacultyMailer\

---

## 1. Files to copy (drop-in replacements / new files)

| File in this patch                 | Copy to (in your project)          | Action   |
| ---------------------------------- | ---------------------------------- | -------- |
| `mailer/gmail_sender.py`           | `mailer/gmail_sender.py`           | Replace  |
| `ai_personalizer.py`               | `ai_personalizer.py`   (root)      | Replace  |
| `recipient_filter.py`              | `recipient_filter.py`  (root, NEW) | Add      |

That's it for copying. Overwrite when prompted.

---

## 2. Manual edits to `main.py`

`send_emails_patch.txt` is NOT a file to copy — it's the code you paste
into `main.py`. Two edits:

### Edit A — add one import
Near the top of `main.py`, with the other imports, add:

    from recipient_filter import filter_recipients, format_preview

(If you instead placed `recipient_filter.py` inside a package such as
`search/`, use `from search.recipient_filter import filter_recipients, format_preview`.
The table above puts it at the root, which matches the import line above.)

### Edit B — replace the send_emails method
In `main.py`, find the existing method:

    def send_emails(self):
        ...

Delete it entirely and paste the replacement from `send_emails_patch.txt`
in its place. The three ALL-CAPS constants at the top of the patch
(`RESEARCH_KEYWORDS`, `MAX_RECIPIENTS_PER_RUN`, `SEND_DELAY_SECONDS`) belong
INSIDE the `FacultyMailer` class body, at the same indentation as the methods.

Tune those three to taste:
  - RESEARCH_KEYWORDS      -> your research terms (energy / EV / emissions / etc.)
  - MAX_RECIPIENTS_PER_RUN -> hard cap per run (start small)
  - SEND_DELAY_SECONDS     -> pause between emails

---

## 3. Rebuild

From an activated 3.12 venv in the project root:

    build_env\Scripts\Activate.ps1
    python build_installer.py

If the rebuilt exe throws `ModuleNotFoundError: recipient_filter` (or for a
package like `mailer`), add it to the build with a hidden-import flag in
`build_installer.py`, e.g. `--hidden-import=recipient_filter`, and rebuild.

---

## 4. Before the first real send — checklist

- [ ] Gmail **App Password** is valid (2-Step Verification on; 16-char app
      password from myaccount.google.com/apppasswords). The `534
      Application-specific password required` error means this isn't set yet.
- [ ] You rotated the Gmail app password AND the Gemini API key that were in
      the shared settings.json. Replace them regardless of anything else.
- [ ] Run once, read the preview dialog, and confirm the recipient list is
      who you actually intend to email before clicking Yes.

## What each change fixes

- gmail_sender.py: fixes the `'ascii' codec can't encode '\xa0'` crash;
  reuses one SMTP connection; adds a per-send delay.
- ai_personalizer.py: replaces the retired `gemini-2.5-flash` (the 404 on
  every row) with `gemini-3.5-flash`; model name now configurable.
- recipient_filter.py + send_emails patch: filters out president/dean/
  emeritus/adjunct/staff/role accounts, dedupes, keyword-matches to your
  research, enforces a cap, and shows a preview you must approve before sending.
