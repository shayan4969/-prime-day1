from flask import Flask, render_template, request
import os
import re
import time
from urllib.parse import quote_plus, urlparse
import requests
from bs4 import BeautifulSoup
from collections import Counter, defaultdict

app = Flask(__name__)

USER_AGENT = os.getenv(
    "ALT_PARTS_USER_AGENT",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
REQUEST_TIMEOUT_SECS = float(os.getenv("ALT_PARTS_TIMEOUT_SECS", "7"))
MAX_PAGES_TO_FETCH = int(os.getenv("ALT_PARTS_MAX_PAGES", "8"))

QUERY_TEMPLATES = [
    "{q} equivalent",
    "{q} cross reference",
    "{q} replacement",
    "{q} substitute",
    "{q} alternative",
]

HEADERS = {"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9", "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}

ALLOWLIST_DOMAINS = set([
    # Common electronics resources (best-effort; we still allow all, but we prioritize these)
    "digikey.com", "mouser.com", "arrow.com", "octopart.com", "findchips.com",
    "all-transistors.com", "alltransistors.com", "transistordatasheets.com",
    "alldatasheet.com", "datasheetcatalog.com", "datasheet4u.com", "datasheetspdf.com",
    "electronics.stackexchange.com", "electronics-tutorials.ws", "electronicsforu.com",
])

PART_PATTERN = re.compile(r"\b(?!PDF\b|IC\b|SMD\b)([A-Z0-9]{1,4}[A-Z0-9-]{1,16}[A-Z0-9])\b")

KEYWORDS_NEARBY = ["equivalent", "replacement", "substitute", "alternate", "alternatives", "cross", "similar"]


def normalize_part(part: str) -> str:
    return part.strip().upper()


def looks_like_part(token: str) -> bool:
    token_up = token.upper()
    if len(token_up) < 3 or len(token_up) > 20:
        return False
    if not any(ch.isdigit() for ch in token_up):
        return False
    if not any(ch.isalpha() for ch in token_up):
        return False
    if token_up in {"PDF", "IC", "SMD", "LED", "SMT", "BOM", "DIY", "PCB"}:
        return False
    if token_up.endswith("V") or token_up.endswith("A"):
        return False
    return True


def search_duckduckgo(query: str):
    url = f"https://duckduckgo.com/html/?q={quote_plus(query)}"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT_SECS)
        resp.raise_for_status()
    except Exception:
        return []

    soup = BeautifulSoup(resp.text, "lxml")
    results = []

    for res in soup.select("div.result__body"):
        a = res.select_one("a.result__a") or res.find("a")
        if not a or not a.get("href"):
            continue
        link = a.get("href")
        title = a.get_text(strip=True)
        snippet_elem = res.select_one("a.result__snippet") or res.select_one("div.result__snippet") or res.find("span")
        snippet = snippet_elem.get_text(" ", strip=True) if snippet_elem else ""
        results.append({"title": title, "link": link, "snippet": snippet})

    # Fallback generic parsing when selectors do not match
    if not results:
        for a in soup.select("a"):
            href = a.get("href", "")
            if href.startswith("http") and "duckduckgo.com" not in urlparse(href).netloc:
                title = a.get_text(strip=True)
                if len(title) > 6:
                    results.append({"title": title, "link": href, "snippet": ""})
                    if len(results) >= 10:
                        break

    return results


def fetch_text_from_url(url: str) -> str:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT_SECS)
        resp.raise_for_status()
        # Prefer visible text; BeautifulSoup get_text will be used
        soup = BeautifulSoup(resp.text, "lxml")
        # Remove script/style/nav/footer
        for tag in soup(["script", "style", "nav", "footer", "header", "noscript"]):
            tag.decompose()
        text = soup.get_text(" ", strip=True)
        return text
    except Exception:
        return ""


def fetch_html(url: str) -> str:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT_SECS)
        resp.raise_for_status()
        return resp.text
    except Exception:
        return ""


def extract_candidate_parts(text: str, original_part: str):
    found = []
    original_up = normalize_part(original_part)

    # Primary regex based extraction
    for match in PART_PATTERN.finditer(text.upper()):
        token = match.group(1)
        if token == original_up:
            continue
        if looks_like_part(token):
            found.append(token)

    # Context-boosting: tokens near keywords get higher weight
    boost_counts = defaultdict(int)
    text_up = text.upper()
    for kw in KEYWORDS_NEARBY:
        idx = 0
        kw_up = kw.upper()
        while True:
            pos = text_up.find(kw_up.upper(), idx)
            if pos == -1:
                break
            window_start = max(0, pos - 120)
            window_end = min(len(text_up), pos + 120)
            window = text_up[window_start:window_end]
            for match in PART_PATTERN.finditer(window):
                token = match.group(1)
                if token != original_up and looks_like_part(token):
                    boost_counts[token] += 2
            idx = pos + len(kw_up)

    counts = Counter(found)
    for token, boost in boost_counts.items():
        counts[token] += boost

    return counts


def gather_from_alltransistors(part_number: str) -> Counter:
    counts = Counter()
    search_url = f"https://alltransistors.com/search.php?search={quote_plus(part_number)}"
    html = fetch_html(search_url)
    if not html:
        return counts
    soup = BeautifulSoup(html, "lxml")
    # Find first transistor page link
    page_url = None
    for a in soup.select('a[href*="transistor.php?transistor="]'):
        name = (a.get_text(strip=True) or "").upper()
        if looks_like_part(name) and part_number.upper().split()[0] in name:
            page_url = a.get("href")
            if page_url and page_url.startswith("/"):
                page_url = f"https://alltransistors.com{page_url}"
            break
    if not page_url:
        # fallback: take any first matching link
        a = soup.select_one('a[href*="transistor.php?transistor="]')
        if a:
            u = a.get("href", "")
            page_url = f"https://alltransistors.com{u}" if u.startswith("/") else u
    if not page_url:
        return counts

    detail_html = fetch_html(page_url)
    if not detail_html:
        return counts
    dsoup = BeautifulSoup(detail_html, "lxml")
    for a in dsoup.select('a[href*="transistor.php?transistor="]'):
        token = (a.get_text(strip=True) or "").upper()
        if token and token != part_number.upper() and looks_like_part(token):
            counts[token] += 3  # give stronger weight to curated links on AT
    return counts


def gather_alternatives(part_number: str):
    aggregated = Counter()
    sources = {}
    fetched_pages = 0

    queries = [t.format(q=part_number) for t in QUERY_TEMPLATES]

    seen_urls = set()
    for q in queries:
        results = search_duckduckgo(q)
        for r in results[:6]:
            if fetched_pages >= MAX_PAGES_TO_FETCH:
                break
            url = r.get("link")
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)

            # Prioritize allowlisted domains by fetching them first
            domain = urlparse(url).netloc
            prefer = any(d in domain for d in ALLOWLIST_DOMAINS)
            if not prefer and fetched_pages >= MAX_PAGES_TO_FETCH // 2:
                continue

            text = fetch_text_from_url(url)
            if not text:
                continue
            counts = extract_candidate_parts(text, part_number)
            if counts:
                aggregated.update(counts)
                for token, c in counts.items():
                    sources.setdefault(token, set()).add(url)
            fetched_pages += 1
            time.sleep(0.2)  # be polite

    # Direct provider fallbacks (no search engine required)
    at_counts = gather_from_alltransistors(part_number)
    if at_counts:
        aggregated.update(at_counts)
        for token in at_counts.keys():
            sources.setdefault(token, set()).add("https://alltransistors.com/")

    # Prepare ranked list
    suggestions = []
    for token, score in aggregated.most_common():
        suggestions.append({
            "part": token,
            "score": int(score),
            "sources": sorted(list(sources.get(token, [])))[:5],
        })

    return suggestions


@app.route("/", methods=["GET", "POST"]) 
def index():
    if request.method == "POST":
        part = request.form.get("part_number", "").strip()
        if not part:
            return render_template("index.html", error="Please enter a part number.")
        suggestions = gather_alternatives(part)
        return render_template("results.html", part=part, suggestions=suggestions)
    return render_template("index.html")


if __name__ == "__main__":
    port = int(os.getenv("PORT", "7860"))
    app.run(host="0.0.0.0", port=port, debug=False)