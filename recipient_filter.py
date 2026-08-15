"""
recipient_filter.py

Turns a raw scraped faculty list into a safe, targeted send list.

Why this exists: scraping a department directory returns EVERYONE -- the
university president, emeritus professors, department heads, staff, people in
other countries. Blasting all of them a near-identical cold email from a
personal Gmail is both ineffective and a fast way to get your account
suspended. This module:

  1. Drops obvious non-targets (president, dean, chair, emeritus, etc.).
  2. Deduplicates by email so nobody is contacted twice.
  3. Optionally keeps only faculty whose bio/title matches your research
     keywords, so you email people your work actually relates to.
  4. Enforces a hard maximum recipient count.

Nothing here sends email. It only decides who *should* be emailed, and returns
a preview you can show the user for approval before anything goes out.
"""

import re
from dataclasses import dataclass
from typing import List, Optional


# Roles/titles that should never receive a cold postdoc inquiry.
EXCLUDE_TITLE_PATTERNS = [
    r"\bpresident\b",
    r"\bdean\b",
    r"\bprovost\b",
    r"\bchair\b",
    r"\bhead\b",
    r"\bdirector\b",
    r"\bemeritus\b",
    r"\bemerita\b",
    r"\badjunct\b",
    r"\bstaff\b",
    r"\badministrat",       # administrator/administrative
    r"\bassistant to\b",
]

# Local-parts that are clearly role accounts, not individuals.
EXCLUDE_EMAIL_LOCALPARTS = {
    "president", "dean", "provost", "info", "contact", "admin",
    "webmaster", "hr", "recruiting", "office", "help", "support",
}

# Names that are directory labels / nav text the scraper grabbed by mistake,
# not real people. Compared case-insensitively against the full name.
JUNK_NAMES = {
    "all faculty", "faculty", "all csd", "staff", "people", "directory",
    "top quicklinks", "current students", "visit carnegie mellon",
    "marketing & communications", "employer recruiting", "at a glance",
    "maps & information", "visitor parking",
}


@dataclass
class PreviewRow:
    name: str
    email: str
    title: str
    reason: str  # why included, or why excluded


def _matches_any(text: str, patterns) -> Optional[str]:
    low = (text or "").lower()
    for pat in patterns:
        if re.search(pat, low):
            return pat.strip("\\b")
    return None


def _localpart(email: str) -> str:
    return (email or "").split("@", 1)[0].strip().lower()


def filter_recipients(
    faculty,
    keywords: Optional[List[str]] = None,
    max_recipients: int = 25,
    require_keyword_match: bool = True,
):
    """
    faculty: iterable of objects with .full_name, .email, .title, .bio_text
    keywords: research keywords to match against title+bio (case-insensitive).
              If None/empty, keyword filtering is skipped.
    max_recipients: hard cap on how many are returned as "included".
    require_keyword_match: if True and keywords given, only keyword matches are kept.

    Returns (included, excluded) as two lists of PreviewRow.
    'included' is already capped and deduped; 'excluded' explains every drop.
    """
    kws = [k.strip().lower() for k in (keywords or []) if k.strip()]
    included: List[PreviewRow] = []
    excluded: List[PreviewRow] = []
    seen_emails = set()

    for fac in faculty:
        name = getattr(fac, "full_name", "") or ""
        email = (getattr(fac, "email", "") or "").strip().lower()
        title = getattr(fac, "title", "") or ""
        bio = getattr(fac, "bio_text", "") or ""

        # No email -> can't send.
        if not email:
            excluded.append(PreviewRow(name, "", title, "no email address"))
            continue

        # Directory-label / nav junk the scraper grabbed as if it were a person.
        name_norm = name.strip().lower()
        # Distinctive multi-word labels: safe to match as a substring.
        junk_phrases = (
            "all faculty", "all csd", "top quicklinks", "current students",
            "visit carnegie mellon", "marketing & communications",
            "employer recruiting", "at a glance", "maps & information",
            "visitor parking",
        )
        if name_norm in JUNK_NAMES or any(p in name_norm for p in junk_phrases):
            excluded.append(PreviewRow(name, email, title, "not a person (directory label)"))
            continue

        # Dedupe.
        if email in seen_emails:
            excluded.append(PreviewRow(name, email, title, "duplicate email"))
            continue

        # Role-account local parts.
        if _localpart(email) in EXCLUDE_EMAIL_LOCALPARTS:
            excluded.append(PreviewRow(name, email, title, f"role account ({_localpart(email)})"))
            continue

        # Excluded titles/roles.
        hit = _matches_any(title, EXCLUDE_TITLE_PATTERNS)
        if hit:
            excluded.append(PreviewRow(name, email, title, f"excluded role: {hit}"))
            continue

        # Keyword relevance. Whole-word match so short terms (e.g. "ev")
        # don't match as substrings inside unrelated words ("Levin", "achievement").
        if kws and require_keyword_match:
            haystack = f"{title} {bio}".lower()
            matched = [k for k in kws
                       if re.search(r"\b" + re.escape(k) + r"\b", haystack)]
            if not matched:
                excluded.append(PreviewRow(name, email, title, "no research-keyword match"))
                continue
            reason = "matches: " + ", ".join(matched)
        else:
            reason = "included"

        seen_emails.add(email)
        included.append(PreviewRow(name, email, title, reason))

    # Enforce the hard cap; anything past it is moved to excluded with a clear reason.
    if len(included) > max_recipients:
        overflow = included[max_recipients:]
        included = included[:max_recipients]
        for row in overflow:
            excluded.append(PreviewRow(row.name, row.email, row.title,
                                       f"over cap of {max_recipients}"))

    return included, excluded


def format_preview(included, excluded, max_show_excluded: int = 10) -> str:
    """Human-readable text summary to show before sending."""
    lines = []
    lines.append(f"WILL SEND to {len(included)} recipient(s):")
    lines.append("")
    for r in included:
        lines.append(f"  - {r.name:<28} {r.email:<30} [{r.reason}]")
    lines.append("")
    lines.append(f"SKIPPED {len(excluded)} (showing up to {max_show_excluded}):")
    for r in excluded[:max_show_excluded]:
        who = r.email or r.name
        lines.append(f"  - {who:<40} [{r.reason}]")
    if len(excluded) > max_show_excluded:
        lines.append(f"  ...and {len(excluded) - max_show_excluded} more")
    return "\n".join(lines)
