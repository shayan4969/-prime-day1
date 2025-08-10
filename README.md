# AltParts Finder

A tiny Flask app that takes an electronic component part number and searches the web for likely alternative/equivalent parts using heuristic parsing.

## Setup

1. Create a virtual environment (optional):

```bash
python3 -m venv .venv
source .venv/bin/activate
```

2. Install dependencies:

```bash
pip3 install -r requirements.txt
```

3. Run the app:

```bash
python3 app.py
```

Then open `http://localhost:7860` in your browser.

## Notes
- Uses DuckDuckGo HTML results and simple heuristics. Results are best-effort and should be verified against datasheets.
- You can adjust environment variables:
  - `ALT_PARTS_USER_AGENT`: custom HTTP user agent
  - `ALT_PARTS_TIMEOUT_SECS`: per-request timeout
  - `ALT_PARTS_MAX_PAGES`: cap fetched pages
  - `PORT`: web server port (default 7860)