import re
import time
from typing import Optional

EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b", re.I)

# Obviously-generic addresses we never want to return as a person's email.
_GENERIC_LOCALPARTS = {
    "info", "contact", "admin", "webmaster", "help", "support", "office",
    "no-reply", "noreply", "hr", "recruiting", "press", "media", "news",
}


class FallbackEmailSearch:
    """
    Last-resort email finder: when a faculty member's email isn't on their
    profile page, search the web for "<full name> <university> email" and pull
    a matching address from the results.

    Less reliable than the profile page (can surface the wrong person or a
    stale address), so callers should treat these as best-effort.
    """

    def __init__(self):
        self._ddgs = None

    def _engine(self):
        if self._ddgs is None:
            from duckduckgo_search import DDGS
            self._ddgs = DDGS()
        return self._ddgs

    def _pick_email(self, text, university_domain):
        matches = EMAIL_RE.findall(text or "")
        # Prefer an address on the university domain, and not a generic mailbox.
        dom = (university_domain or "").lower().lstrip("@")
        dom_root = ".".join(dom.split(".")[-2:]) if dom else ""

        def ok(e):
            local = e.split("@", 1)[0].lower()
            return local not in _GENERIC_LOCALPARTS

        on_domain = [e for e in matches if dom_root and dom_root in e.lower() and ok(e)]
        if on_domain:
            return on_domain[0].lower()
        any_ok = [e for e in matches if ok(e)]
        return any_ok[0].lower() if any_ok else None

    def find_email(self, full_name: str, university_domain: str = "",
                   university_name: str = "") -> Optional[str]:
        """
        Try several query formulations. university_name (e.g. "Georgia Tech")
        is used in the human-style query; university_domain (e.g. "gatech.edu")
        is used to validate/prefer the resulting address.
        """
        if not full_name:
            return None

        queries = []
        if university_domain:
            queries.append(f'"{full_name}" email site:{university_domain}')
        if university_name:
            queries.append(f'{full_name} {university_name} email')
        if university_domain:
            queries.append(f'{full_name} {university_domain} email')
        queries.append(f'{full_name} faculty email')

        for q in queries:
            try:
                results = list(self._engine().text(q, max_results=4))
            except Exception:
                # Rate-limited or network error; small pause and try next query.
                time.sleep(1.0)
                continue
            for res in results:
                blob = " ".join([
                    res.get("body", "") or "",
                    res.get("title", "") or "",
                    res.get("href", "") or "",
                ])
                email = self._pick_email(blob, university_domain)
                if email:
                    return email
        return None
