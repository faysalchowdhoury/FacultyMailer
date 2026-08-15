import re
import time
from dataclasses import dataclass, asdict
from typing import List, Optional, Set
from urllib.parse import urljoin, urlparse
import requests
from bs4 import BeautifulSoup

EMAIL_RE = re.compile(
    r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b",
    re.I,
)
TITLE_RE = re.compile(
    r"\b("
    r"professor|prof\.|associate professor|assistant professor|"
    r"lecturer|senior lecturer|assistant lecturer|instructor|"
    r"research professor|research scientist|scientist|"
    r"distinguished professor|emeritus professor|"
    r"faculty|postdoctoral researcher|researcher"
    r")\b",
    re.I,
)
BAD_NAME_WORDS = {
    "faculty", "faculty members", "people", "our people", "staff",
    "directory", "contact", "contact us", "research", "department",
    "about", "academics", "home", "login", "search", "menu", "next", "previous"
}

# Words that, if they appear ANYWHERE in a candidate "name", mean it's really a
# navigation link / page section / org unit, not a person. Kept deliberately to
# UNAMBIGUOUS org/nav terms -- words that essentially never appear inside a real
# personal name -- so we don't accidentally drop people. The email is the real
# gate for who gets contacted; this list only declutters the table.
NON_NAME_KEYWORDS = {
    "college of", "school of", "department of", "division of", "office of",
    "center for", "centre for", "advisory board", "administration",
    "organization chart", "feedback form", "quicklinks", "quick links",
    "main navigation", "faculty position", "funding sources",
    "career fair", "mentoring program", "facts and rankings",
    "current students", "future students", "give to", "news & events",
    "website feedback", "student engagement", "employer recruiting",
    "tutoring assistance", "international studies", "sitemap",
}

# Class/id tokens that mark shared page chrome (kept conservative on purpose so
# we never strip a faculty card or a "search-results" list that holds people).
BOILERPLATE_CLASS_RE = re.compile(
    r"(?:^|[\s_\-])("
    r"nav|navbar|navigation|mainmenu|main-menu|menu|"
    r"site-?header|masthead|site-?footer|footer|"
    r"breadcrumbs?|social|skip-?link|megamenu|cookie|banner|utility-?nav"
    r")(?:$|[\s_\-])",
    re.I,
)
BOILERPLATE_ROLES = {"navigation", "banner", "contentinfo"}


@dataclass
class Faculty:
    full_name: str
    first_name: str = ""
    last_name: str = ""
    title: str = ""
    email: str = ""
    profile_url: str = ""
    source_url: str = ""
    email_source: str = ""
    bio_text: str = ""
    candidate_links: list = None  # all same-domain links in the container (for email hunting)

    def to_dict(self):
        d = asdict(self)
        d.pop("candidate_links", None)  # working field, not for export
        return d


class UniversalFacultyScraper:
    def __init__(self, max_pages: int = 50, max_profiles: int = 150, delay: float = 0.5,
                 timeout: int = 10, render: str = "auto"):
        self.max_pages = max_pages
        self.max_profiles = max_profiles
        self.delay = delay
        self.timeout = timeout
        # render: "auto" -> use a real browser only when the static page looks
        # empty (JS-built directories like TAMU); "always" -> render every entry
        # page; "never" -> plain HTTP only.
        self.render = render
        self._render_checked = False
        self._render_ok = False
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": ("text/html,application/xhtml+xml,application/xml;q=0.9,"
                       "image/avif,image/webp,*/*;q=0.8"),
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
        })

    def fetch(self, url: str, progress_cb=None) -> Optional[str]:
        last_exc = None
        for attempt in (1, 2):  # one quick retry on timeout/connection error
            try:
                res = self.session.get(url, timeout=self.timeout, allow_redirects=True)
                break
            except requests.Timeout as e:
                last_exc = e
                if attempt == 1:
                    continue  # retry once
                if progress_cb:
                    progress_cb(f"[skip] {url} timed out twice; moving on")
                return None
            except requests.RequestException as e:
                if progress_cb:
                    progress_cb(f"[fetch error] {url} -> {type(e).__name__}: {e}")
                return None

        if res.status_code != 200:
            if progress_cb:
                progress_cb(f"[fetch] {url} returned HTTP {res.status_code}")
            return None

        ctype = res.headers.get("Content-Type", "").lower()
        # Only skip when the server explicitly declares a NON-html type.
        # Some servers omit Content-Type or mislabel it; don't drop those.
        if ctype and "html" not in ctype and "text" not in ctype:
            if progress_cb:
                progress_cb(f"[fetch] {url} skipped, Content-Type={ctype!r}")
            return None

        return res.text

    def _auto_expand(self, page):
        """Click 'load more' buttons and scroll to trigger lazy-loaded lists."""
        load_more = re.compile(r"(load more|show more|view more|see more|more results)", re.I)
        last_h = 0
        for _ in range(25):
            clicked = False
            try:
                for el in page.query_selector_all("button, a"):
                    try:
                        t = (el.inner_text() or "").strip()
                    except Exception:
                        continue
                    if t and load_more.search(t) and el.is_visible():
                        try:
                            el.click(timeout=2000)
                            page.wait_for_timeout(700)
                            clicked = True
                            break
                        except Exception:
                            pass
            except Exception:
                pass
            try:
                page.mouse.wheel(0, 20000)
                page.wait_for_timeout(500)
                h = page.evaluate("document.body.scrollHeight")
            except Exception:
                break
            if h == last_h and not clicked:
                break
            last_h = h

    def fetch_rendered(self, url: str, progress_cb=None) -> Optional[str]:
        """Fetch a page through a headless browser so JavaScript-built
        directories (which return an empty HTML shell to plain requests) are
        fully populated before we parse. Falls back to None if Playwright
        isn't installed, so the app keeps working in static mode."""
        try:
            from playwright.sync_api import sync_playwright
        except Exception:
            if not self._render_checked:
                self._render_checked = True
                if progress_cb:
                    progress_cb("[render] Playwright not installed - JS sites can't be read. "
                                "Install with:  pip install playwright  &&  python -m playwright install chromium")
            return None
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch()
                page = browser.new_page(user_agent=self.session.headers.get("User-Agent"))
                try:
                    page.goto(url, wait_until="networkidle", timeout=self.timeout * 1000)
                except Exception:
                    try:
                        page.goto(url, timeout=self.timeout * 1000)
                    except Exception:
                        browser.close()
                        return None
                self._auto_expand(page)
                html = page.content()
                browser.close()
                self._render_ok = True
                return html
        except Exception as e:
            if progress_cb:
                progress_cb(f"[render] browser failed: {type(e).__name__}: {e}")
            return None

    @staticmethod
    def extract_emails(text: str) -> List[str]:
        if not text:
            return []
        matches = EMAIL_RE.findall(text)
        return sorted({e.lower().strip().rstrip(".,;:)]}") for e in matches})

    @staticmethod
    def mailto_email(soup: BeautifulSoup) -> str:
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if href.lower().startswith("mailto:"):
                email = href[7:].split("?")[0]
                if EMAIL_RE.fullmatch(email):
                    return email.lower()
        return ""

    @staticmethod
    def _strip_boilerplate(soup: BeautifulSoup) -> BeautifulSoup:
        """
        Remove shared page chrome (nav / header / footer / breadcrumb / social)
        BEFORE we mine names and emails. This is what stops menu items like
        "About Us" / "Leadership" and footer contacts like dept-general@ or
        webmaster@ from being scored as faculty. It is deliberately conservative:
        it never removes a <header>/<footer> nested inside an <article>/<li>
        (those are parts of a real person card), and it does not touch
        "search-results"-style containers that may hold the actual people.
        """
        def nested_in_card(tag):
            try:
                return tag.find_parent(["article", "li"]) is not None
            except Exception:
                return False

        for tag in soup.find_all(["script", "style", "noscript"]):
            try:
                tag.decompose()
            except Exception:
                pass
        for tag in soup.find_all("nav"):
            try:
                if not nested_in_card(tag):
                    tag.decompose()
            except Exception:
                pass
        for tag in soup.find_all(["header", "footer"]):
            try:
                if not nested_in_card(tag):
                    tag.decompose()
            except Exception:
                pass
        for tag in soup.find_all(attrs={"role": True}):
            try:
                if tag.get("role", "").lower() in BOILERPLATE_ROLES and not nested_in_card(tag):
                    tag.decompose()
            except Exception:
                pass
        for tag in soup.find_all(attrs={"class": True}):
            try:
                if BOILERPLATE_CLASS_RE.search(" ".join(tag.get("class", []))) and not nested_in_card(tag):
                    tag.decompose()
            except Exception:
                pass
        for tag in soup.find_all(attrs={"id": True}):
            try:
                if BOILERPLATE_CLASS_RE.search(tag.get("id", "")) and not nested_in_card(tag):
                    tag.decompose()
            except Exception:
                pass
        return soup

    @staticmethod
    def clean_name(name: str) -> str:
        name = re.sub(r"\s+", " ", name).strip()
        return name.strip(" -–—|:")

    @classmethod
    def looks_like_name(cls, name: str) -> bool:
        """
        A LIGHT filter to reduce obvious clutter in the results table only.
        It is deliberately NOT the thing that decides who is a real person --
        that decision is made by whether the row ends up with a valid email
        (enforced downstream). So this errs on the side of KEEPING anything
        that could plausibly be a name, and only drops clear navigation/org
        rows. A real "Grace Church" or "Wei Chen" is kept; whether they're
        actually contacted depends on having an email, not on this check.
        """
        name = cls.clean_name(name)
        if not name or not (2 <= len(name) <= 100):
            return False
        low = name.lower()
        if low in BAD_NAME_WORDS:
            return False
        # Drop clear navigation / org-unit / page-section rows.
        if any(kw in low for kw in NON_NAME_KEYWORDS):
            return False
        # These characters indicate an org name, form, or section, not a person.
        if any(c in name for c in "&/@|!?;{}[]"):
            return False
        # Reasonable word count for a personal name.
        words = name.split()
        if not (1 <= len(words) <= 8):
            return False
        # Must contain at least one alphabetic character (not pure numbers/symbols).
        if not any(ch.isalpha() for ch in name):
            return False
        return True

    @staticmethod
    def split_name(name: str):
        name = name.strip()
        if "," in name:
            parts = name.split(",", 1)
            return parts[1].strip(), parts[0].strip()
        name = re.sub(r"^(Dr\.?|Prof\.?|Professor)\s+", "", name, flags=re.I)
        parts = name.split()
        if len(parts) == 2:
            return parts[0], parts[1]
        if len(parts) > 2:
            return " ".join(parts[:-1]), parts[-1]
        return name, ""

    @staticmethod
    def same_domain(base_url: str, candidate_url: str) -> bool:
        base = urlparse(base_url).netloc.lower()
        cand = urlparse(candidate_url).netloc.lower()
        return cand == base or cand.endswith("." + base) or base.endswith("." + cand)

    # Path sections that introduce an individual's page.
    _PROFILE_SECTION_RE = re.compile(r"/(people|profile|profiles|person|member|members|bio|faculty|staff|directory|team)/([^/?#]+)", re.I)
    _TILDE_USER_RE = re.compile(r"/~[\w.-]+/?$")
    # Slugs that are listing/section pages, NOT a specific person.
    _NON_PERSON_SLUGS = {
        "faculty", "staff", "people", "phd", "phd-students", "students",
        "all", "index", "list", "directory", "adjunct", "emeritus",
        "lecturers", "postdocs", "affiliated", "page",
    }

    @classmethod
    def is_individual_profile_url(cls, url: str) -> bool:
        """
        True only for an INDIVIDUAL's profile page (e.g. /people/joy-arulraj
        or /~jarulraj/), not a listing/pagination page (/people/faculty,
        /people/faculty?page=2, /people/phd).
        """
        if not url:
            return False
        low = url.lower()
        # Pagination / query listings are never a person.
        if "?page=" in low or low.rstrip("/").endswith(("/faculty", "/people", "/staff")):
            return False
        if cls._TILDE_USER_RE.search(low):
            return True
        m = cls._PROFILE_SECTION_RE.search(low)
        if not m:
            return False
        slug = m.group(2).strip("/")
        # slug may carry a file extension (lupoli-shawn.html); test the core.
        slug_core = slug.rsplit(".", 1)[0] if "." in slug else slug
        if not slug or slug_core in cls._NON_PERSON_SLUGS:
            return False
        # A person slug typically has a hyphen (first-last) or a dot, and isn't
        # a bare listing word. Require it to look like a name slug.
        return ("-" in slug) or ("." in slug) or (len(slug) >= 4 and slug.isalpha())

    @classmethod
    def profile_score(cls, container, candidate_name, candidate_link, emails, title, source_url):
        """
        Score how much a container STRUCTURALLY looks like a person entry,
        rather than judging the name text. This is layout-based and works
        across arbitrary faculty sites because it counts several independent
        signals; different sites will hit different ones. A nav link or page
        section typically hits 0-1; a real faculty card hits 2+.
        """
        score = 0

        # 1. Has a profile-style link (/people/<slug>, /faculty/<slug>, /~user).
        if candidate_link and cls.is_individual_profile_url(candidate_link):
            score += 1

        # 2. The name is itself a link (faculty names almost always link to a
        #    profile; nav items link to section pages with different text).
        #    Detect: an <a> whose visible text matches the candidate name.
        name_low = (candidate_name or "").lower()
        for a in container.find_all("a", href=True):
            if a.get_text(" ", strip=True).lower() == name_low and name_low:
                score += 1
                break

        # 3. Has an image (faculty cards have a headshot; nav items don't).
        if container.find("img"):
            score += 1

        # 4. Has an academic title nearby ("Professor", "Lecturer", etc.).
        if title:
            score += 1

        # 5. Has an email (mailto or in text).
        if emails:
            score += 1

        return score

    def parse_containers(self, soup: BeautifulSoup, source_url: str) -> List[Faculty]:
        results = []
        containers = soup.find_all(["article", "li", "tr", "section", "div"])
        seen_local = set()

        for container in containers:
            text = container.get_text(" ", strip=True)
            if not text:
                continue

            emails = self.extract_emails(text)
            mailto = self.mailto_email(container)
            if mailto:
                emails = [mailto]

            candidate_name = ""
            candidate_link = ""

            for elem in container.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "a"]):
                c_name = self.clean_name(elem.get_text(" ", strip=True))
                if self.looks_like_name(c_name):
                    candidate_name = c_name
                    # The profile link may be: the element itself (<a>), a child
                    # <a> inside a heading (<h4><a href=...>Name</a></h4>, as on
                    # Georgia Tech), or an ancestor <a> wrapping the name.
                    if elem.name == "a" and elem.has_attr("href"):
                        candidate_link = urljoin(source_url, elem["href"])
                    else:
                        child_a = elem.find("a", href=True)
                        if child_a:
                            candidate_link = urljoin(source_url, child_a["href"])
                        elif elem.find_parent("a", href=True):
                            candidate_link = urljoin(source_url, elem.find_parent("a")["href"])
                    break

            if not candidate_name:
                continue

            title_match = TITLE_RE.search(text)
            title = title_match.group(0) if title_match else ""

            # STRUCTURAL GATE: require the container to look like a person entry
            # by layout (2+ independent signals), not by the name text. This is
            # what separates "Grace Church the professor" (photo + profile link
            # + title) from "Advisory Board" the nav item (none of those).
            score = self.profile_score(
                container, candidate_name, candidate_link, emails, title, source_url
            )
            if score < 2:
                continue

            first, last = self.split_name(candidate_name)
            email = emails[0] if emails else ""
            key = email or candidate_name.lower()

            if key in seen_local:
                continue
            seen_local.add(key)

            # Collect ALL same-domain links inside this container, ordered so the
            # most profile-like ones come first. Enrichment will try each in turn
            # until it finds an email -- this catches cases where the email lives
            # behind the photo link or a "Profile"/"Read more" link rather than
            # the name link.
            all_links = []
            for a in container.find_all("a", href=True):
                href = a["href"].strip()
                if href.lower().startswith(("mailto:", "tel:", "#", "javascript:")):
                    continue
                abs_url = urljoin(source_url, href)
                if not self.same_domain(source_url, abs_url):
                    continue
                if abs_url not in all_links:
                    all_links.append(abs_url)
            # Put individual-profile-looking links first.
            all_links.sort(key=lambda u: 0 if self.is_individual_profile_url(u) else 1)
            # Make sure the primary candidate_link is included and first.
            if candidate_link:
                if candidate_link in all_links:
                    all_links.remove(candidate_link)
                all_links.insert(0, candidate_link)

            results.append(Faculty(
                full_name=f"{first} {last}".strip(),
                first_name=first,
                last_name=last,
                title=title,
                email=email,
                profile_url=candidate_link,
                source_url=source_url,
                email_source="directory" if email else "",
                bio_text=text[:1000],
                candidate_links=all_links,
            ))
        return results

    @staticmethod
    def deduplicate(records: List[Faculty]) -> List[Faculty]:
        result = []
        by_email = {}
        by_name = {}

        for rec in records:
            email = rec.email.lower().strip()
            name_key = re.sub(r"\W+", "", rec.full_name.lower())

            if email:
                if email in by_email:
                    existing = by_email[email]
                    if not existing.profile_url: existing.profile_url = rec.profile_url
                    if not existing.title: existing.title = rec.title
                    continue
                by_email[email] = rec
                result.append(rec)
            else:
                if name_key in by_name:
                    existing = by_name[name_key]
                    if not existing.email and rec.email: existing.email = rec.email
                    if not existing.profile_url: existing.profile_url = rec.profile_url
                    continue
                by_name[name_key] = rec
                result.append(rec)

        return result

    def scrape(self, start_url: str, university_name: str = "", progress_cb=None) -> List[Faculty]:
        if not start_url.startswith(("http://", "https://")):
            start_url = "https://" + start_url

        visited_pages: Set[str] = set()
        pages_to_visit = [start_url]
        all_records = []

        while pages_to_visit and len(visited_pages) < self.max_pages:
            curr_url = pages_to_visit.pop(0)
            if curr_url in visited_pages:
                continue

            visited_pages.add(curr_url)
            if progress_cb:
                progress_cb(f"Scanning page {len(visited_pages)}: {curr_url}")

            html = self.fetch(curr_url, progress_cb=progress_cb)
            if not html:
                if progress_cb:
                    progress_cb(f"No HTML for {curr_url} (see reason above); skipping.")
                continue

            soup = BeautifulSoup(html, "lxml")
            # Parse names/emails from a copy with shared nav/header/footer removed,
            # so page chrome can't masquerade as faculty.
            content_soup = self._strip_boilerplate(BeautifulSoup(html, "lxml"))
            records = self.parse_containers(content_soup, curr_url)

            # Entry page looks empty? It's probably a JS-built directory (TAMU,
            # etc.). Re-fetch through a real browser and parse the rendered DOM.
            first_page = (len(visited_pages) == 1)
            want_render = self.render == "always" or (self.render == "auto" and len(records) < 3)
            if first_page and want_render and self.render != "never":
                if progress_cb:
                    progress_cb("Page looks empty in static mode; rendering with a browser...")
                r_html = self.fetch_rendered(curr_url, progress_cb=progress_cb)
                if r_html:
                    html = r_html
                    soup = BeautifulSoup(html, "lxml")
                    content_soup = self._strip_boilerplate(BeautifulSoup(html, "lxml"))
                    records = self.parse_containers(content_soup, curr_url)
                    if progress_cb:
                        progress_cb(f"Rendered page yielded {len(records)} candidate rows.")

            all_records.extend(records)
            all_records = self.deduplicate(all_records)

            if progress_cb:
                progress_cb(f"Found {len(all_records)} unique faculty members...")

            for link in soup.find_all("a", href=True):
                href = link["href"].strip()
                abs_url = urljoin(curr_url, href)
                if self.same_domain(start_url, abs_url):
                    text = link.get_text(" ", strip=True).lower()
                    if any(kw in text for kw in ["next", "›", "»", ">"]) or "page=" in abs_url.lower():
                        if abs_url not in visited_pages and abs_url not in pages_to_visit:
                            pages_to_visit.append(abs_url)

            time.sleep(self.delay)

        # Enrichment pass 1: for records with no email but an individual
        # profile link, visit the profile page and pull the email from it.
        # This is authoritative and preferred.
        all_records = self.enrich_from_profiles(all_records, progress_cb=progress_cb)

        # Enrichment pass 2 (fallback): for records STILL missing an email,
        # search the web for "<name> email site:<domain>". Less reliable than
        # the profile page, so it only runs on what pass 1 couldn't resolve.
        domain = urlparse(start_url).netloc.lower()
        if domain.startswith("www."):
            domain = domain[4:]
        all_records = self.enrich_from_search(all_records, domain,
                                              university_name=university_name,
                                              progress_cb=progress_cb)

        return all_records

    def enrich_from_search(self, records: List[Faculty], university_domain: str,
                           university_name: str = "", progress_cb=None) -> List[Faculty]:
        """
        Fallback: for records that still have no email after profile-visiting,
        search the web for "<name> <university> email". Reliability is lower than
        the profile page, so this runs last and only fills genuinely-missing
        emails. Junk rows (no real name) are skipped.
        """
        needing = [r for r in records
                   if not r.email and r.full_name and self.looks_like_name(r.full_name)]
        if not needing or not university_domain:
            return records

        try:
            from search.fallback_search import FallbackEmailSearch
        except Exception:
            try:
                from fallback_search import FallbackEmailSearch
            except Exception:
                if progress_cb:
                    progress_cb("Web-search fallback unavailable (module not found); skipping.")
                return records

        # Build a readable university name from the domain if none was provided
        # (e.g. "gatech.edu" -> "gatech"). The caller can pass a better one.
        if not university_name and university_domain:
            university_name = university_domain.split(".")[0]

        searcher = FallbackEmailSearch()
        if progress_cb:
            progress_cb(f"Web-searching for {len(needing)} still-missing email(s)...")

        for i, rec in enumerate(needing, 1):
            if progress_cb:
                progress_cb(f"  Search {i}/{len(needing)}: {rec.full_name}")
            try:
                email = searcher.find_email(rec.full_name, university_domain, university_name)
            except Exception:
                email = None
            if email:
                rec.email = email
                rec.email_source = "search"
                if progress_cb:
                    progress_cb(f"    found: {email}")
            time.sleep(self.delay)

        return self.deduplicate(records)

    # URL path fragments that indicate a page is NOT an individual's profile.
    NON_PROFILE_URL_HINTS = (
        "advisory", "administration", "opportunit", "board", "chart",
        "funding", "career", "mentoring", "outreach", "rankings",
        "facts-and", "organization", "feedback", "studies", "resources",
        "navigation", "position", "give", "news", "events", "about",
        "current-student", "future-student", "/content/", "qualtrics",
    )

    def _looks_like_profile_url(self, url: str) -> bool:
        # Delegate to the single strict definition of an individual profile URL.
        return self.is_individual_profile_url(url)

    @staticmethod
    def _name_tokens(full_name):
        import re as _re
        parts = [p for p in _re.split(r"[\s,.\-']+", (full_name or "").lower()) if len(p) >= 2]
        return parts

    def _email_matches_name(self, email, full_name):
        """True if the email's local-part plausibly belongs to this person."""
        local = email.split("@", 1)[0].lower()
        local_alpha = "".join(ch for ch in local if ch.isalpha())
        tokens = self._name_tokens(full_name)
        if not tokens:
            return False
        first = tokens[0]
        last = tokens[-1]
        # Common patterns: jsmith, smithj, john.smith, jsmith3, arulraj (last),
        # jarulraj (first-initial + last).
        if last and last in local_alpha:
            return True
        if first and last and (first[0] + last) in local_alpha:
            return True
        if first and last and (last + first[0]) in local_alpha:
            return True
        if first and first in local_alpha and len(first) >= 4:
            return True
        return False

    def _extract_email_from_html(self, html, page_url, full_name=""):
        """
        Pull the best email from a fetched page. If a name is given, PREFER the
        email whose local-part matches that person -- profile pages sometimes
        list other faculty (co-authors, related people), and we must not grab
        someone else's address.
        """
        soup = self._strip_boilerplate(BeautifulSoup(html, "lxml"))

        # Gather all candidate emails: mailto links first, then text.
        candidates = []
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if href.lower().startswith("mailto:"):
                addr = href[7:].split("?")[0].strip()
                if addr:
                    candidates.append(addr)
        candidates += self.extract_emails(soup.get_text(" ", strip=True))

        # de-dupe, keep order
        seen = set(); ordered = []
        for e in candidates:
            el = e.lower()
            if el not in seen:
                seen.add(el); ordered.append(e)

        if not ordered:
            return ""

        host = urlparse(page_url).netloc.lower()
        host_root = ".".join(host.split(".")[-2:]) if host else ""
        on_domain = [e for e in ordered if host_root and host_root in e.lower()]
        pool = on_domain or ordered

        if full_name:
            # 1st choice: on-domain email that matches the person's name.
            name_matched = [e for e in pool if self._email_matches_name(e, full_name)]
            if name_matched:
                return name_matched[0].lower()
            # If there are MULTIPLE emails and none matches the name, it's risky
            # to guess -- likely other people's addresses. Return nothing rather
            # than a wrong email.
            if len(pool) > 1:
                return ""

        return pool[0].lower()

    def _extract_title_from_html(self, html: str) -> str:
        """Pull a concise academic title line from a profile page (used when the
        listing had only a name+link, e.g. TAMU, so the title lives on the
        person's own page)."""
        try:
            soup = self._strip_boilerplate(BeautifulSoup(html, "lxml"))
        except Exception:
            return ""
        for el in soup.find_all(["li", "p", "span", "div", "h2", "h3"]):
            t = el.get_text(" ", strip=True)
            if t and len(t) <= 120 and TITLE_RE.search(t):
                return t
        m = TITLE_RE.search(soup.get_text(" ", strip=True))
        return m.group(0).title() if m else ""

    def enrich_from_profiles(self, records: List[Faculty], progress_cb=None) -> List[Faculty]:
        # A record needs enrichment if it has no email yet but has at least one
        # link we can follow (any same-domain link from its container, not just
        # the name link).
        def links_for(rec):
            links = list(rec.candidate_links or [])
            if rec.profile_url and rec.profile_url not in links:
                links.insert(0, rec.profile_url)
            seen = set(); ordered = []
            for u in links:
                if u and u not in seen:
                    seen.add(u); ordered.append(u)
            return ordered

        needing = [r for r in records
                   if not r.email and self.looks_like_name(r.full_name) and links_for(r)]
        if not needing:
            if progress_cb:
                progress_cb("No links to follow for missing emails.")
            return records

        if progress_cb:
            progress_cb(f"Finding emails for {len(needing)} faculty...")

        # OPTIMIZATION: every faculty page on the same site uses the same template,
        # so once we learn WHICH link position holds the email (e.g. "the first
        # profile-type link"), we try that position FIRST for everyone else and
        # usually succeed on the first fetch -- no need to try all links each time.
        learned_index = None  # index into each person's link list that worked

        for i, rec in enumerate(needing, 1):
            links = links_for(rec)
            if progress_cb:
                progress_cb(f"  {i}/{len(needing)}: {rec.full_name}")

            # Build the order to try: learned position first (if any), then the rest.
            order = list(range(len(links)))
            if learned_index is not None and learned_index < len(links):
                order.remove(learned_index)
                order.insert(0, learned_index)

            found = ""
            for pos in order[:5]:  # cap attempts per person
                link = links[pos]
                html = self.fetch(link, progress_cb=progress_cb)
                if not html:
                    continue
                email = self._extract_email_from_html(html, link, rec.full_name)
                if email:
                    found = email
                    rec.email = email
                    rec.email_source = "profile"
                    rec.profile_url = rec.profile_url or link
                    if not rec.title:
                        t = self._extract_title_from_html(html)
                        if t:
                            rec.title = t
                    # Learn: this link position works for this site's template.
                    if learned_index is None:
                        learned_index = pos
                        if progress_cb:
                            progress_cb(f"    found: {email} (learned link position #{pos}; "
                                        f"will try it first for the rest)")
                    else:
                        if progress_cb:
                            progress_cb(f"    found: {email}")
                    break
                time.sleep(self.delay)

            if not found:
                time.sleep(self.delay)

        return self.deduplicate(records)