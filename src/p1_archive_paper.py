"""
p1_archive_paper.py -- Phase 1 (Archive): newspaper PDFs saved from WhatsApp
into ~/Downloads are checked, renamed and filed into archive/<date>/.

Lives in:  ~/news-intel/src/
Run with:  cd ~/news-intel && .venv/bin/python src/p1_archive_paper.py
           (add a path to a PDF to process just that one file)
Reads:     ~/Downloads (PDFs newer than the last run), data/p1_last_run.txt,
           API key from ~/.config/news-intel/.env
Writes:    archive/, needs_review/, logs/archive.log, data/p1_last_run.txt

Changed 13 Oct 2026 for the new folder layout: paths now start from the
project folder (one level above src/), the log moved to logs/, the
last-run time moved to data/, and the API key is read from outside the
project folder. The pipeline logic itself is unchanged from 22 Sep.
"""

import logging
import os
import re
import shutil
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pdfplumber
from dotenv import load_dotenv
from anthropic import Anthropic

PROJECT_ROOT = Path(__file__).resolve().parent.parent   # ~/news-intel (this file is in src/)
ENV_FILE = Path.home() / ".config" / "news-intel" / ".env"

load_dotenv(ENV_FILE)
if not os.environ.get("ANTHROPIC_API_KEY"):
    sys.exit(f"No ANTHROPIC_API_KEY found. Expected it in {ENV_FILE}")
client = Anthropic()

ARCHIVE_ROOT = PROJECT_ROOT / "archive"
REVIEW_ROOT = PROJECT_ROOT / "needs_review"
LOG_PATH = PROJECT_ROOT / "logs" / "archive.log"
STATE_FILE = PROJECT_ROOT / "data" / "p1_last_run.txt"
DOWNLOADS_DIR = Path.home() / "Downloads"
FIRST_RUN_LOOKBACK_HOURS = 24

LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
STATE_FILE.parent.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH, mode='a'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

MAX_PAGES_TO_CHECK = 5

MONTHS = "JANUARY|FEBRUARY|MARCH|APRIL|MAY|JUNE|JULY|AUGUST|SEPTEMBER|OCTOBER|NOVEMBER|DECEMBER"
DATE_PATTERN = re.compile(rf'(\d{{1,2}})({MONTHS})(\d{{4}})', re.IGNORECASE)
SAFE_NAME = re.compile(r'^[A-Za-z0-9 ,.\-_]{1,80}$')
SAFE_DATE = re.compile(r'^\d{4}-\d{2}-\d{2}$')

KNOWN_PUBLISHERS = {
    "economic times": "Economic Times", "et": "Economic Times",
    "mint": "Mint",
    "the hindu": "The Hindu", "th": "The Hindu",
    "business line": "Business Line", "businessline": "Business Line", "bl": "Business Line",
    "financial express": "Financial Express", "fe": "Financial Express",
    "indian express": "Indian Express", "ie": "Indian Express",
}


def canonicalize_name(raw_name):
    return KNOWN_PUBLISHERS.get(raw_name.strip().lower())


def filename_hint_name(filename):
    stem = filename.lower()
    for key in sorted(KNOWN_PUBLISHERS, key=len, reverse=True):
        pattern = rf'(?<![a-z0-9]){re.escape(key)}(?![a-z0-9])'
        if re.search(pattern, stem):
            return KNOWN_PUBLISHERS[key]
    return None


def get_last_run_time():
    if STATE_FILE.exists():
        return datetime.fromisoformat(STATE_FILE.read_text().strip())
    return datetime.now() - timedelta(hours=FIRST_RUN_LOOKBACK_HOURS)


def set_last_run_time(dt):
    STATE_FILE.write_text(dt.isoformat())


def find_new_pdfs(downloads_dir, since):
    candidates = [
        p for p in downloads_dir.iterdir()
        if p.suffix.lower() == ".pdf" and datetime.fromtimestamp(p.stat().st_mtime) > since
    ]
    return sorted(candidates, key=lambda p: p.stat().st_mtime)


def is_mostly_garbage(text, threshold=0.1):
    if not text:
        return True
    return (text.count('\ufffd') / len(text)) > threshold


def extract_page_text(pdf_path, max_pages=MAX_PAGES_TO_CHECK):
    with pdfplumber.open(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages[:max_pages], start=1):
            text = page.extract_text() or ""
            if text.strip() and not is_mostly_garbage(text):
                return text, i
    return "", None


def regex_extract_date(text):
    match = DATE_PATTERN.search(text)
    if not match:
        return None
    day, month, year = match.groups()
    parsed = datetime.strptime(f"{day} {month.capitalize()} {year}", "%d %B %Y")
    return parsed.strftime("%Y-%m-%d")


def llm_extract(text):
    tool = {
        "name": "record_paper_info",
        "description": "Determine whether this text is a newspaper front page, and if so, record its name and date.",
        "input_schema": {
            "type": "object",
            "properties": {
                "is_newspaper_front_page": {
                    "type": "boolean",
                    "description": "True only if this is clearly the front page of a newspaper or similar periodical. False for bills, receipts, tickets, brochures, or any unrelated document."
                },
                "newspaper_name": {
                    "type": "string",
                    "description": "The newspaper's name, e.g. 'Economic Times'. Use an empty string if is_newspaper_front_page is false."
                },
                "date": {
                    "type": "string",
                    "description": "Publication date in YYYY-MM-DD format. Use an empty string if is_newspaper_front_page is false."
                }
            },
            "required": ["is_newspaper_front_page", "newspaper_name", "date"]
        }
    }

    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=200,
            tools=[tool],
            tool_choice={"type": "tool", "name": "record_paper_info"},
            extra_body={"temperature": 0},
            messages=[{
                "role": "user",
                "content": f"Front page text of a PDF:\n\n{text}\n\nIs this a newspaper front page? If so, extract its name and date."
            }]
        )
    except Exception as e:
        logger.error(f"API call failed: {e}")
        return None

    for block in response.content:
        if block.type == "tool_use":
            return block.input
    return None


def safe_destination(base_dir, date_str, filename):
    if not SAFE_DATE.match(date_str) or not SAFE_NAME.match(filename):
        raise ValueError(f"Rejected unsafe name/date: {filename!r} / {date_str!r}")
    base = Path(base_dir).resolve()
    dest = (base / date_str / filename).resolve()
    if base not in dest.parents:
        raise ValueError("Rejected: path escapes archive folder")
    return dest


def process_pdf(pdf_path):
    pdf_path = Path(pdf_path)
    text, page_num = extract_page_text(pdf_path)
    if not text.strip():
        logger.error(f"No extractable text in first {MAX_PAGES_TO_CHECK} pages of {pdf_path.name} — skipping.")
        return "skipped"
    if page_num != 1:
        logger.info(f"{pdf_path.name}: page 1 had no usable text, used page {page_num} instead.")

    regex_date = regex_extract_date(text)
    llm_result = llm_extract(text)
    if llm_result is None:
        logger.error(f"LLM call failed for {pdf_path.name} — leaving in place.")
        return "error"

    if not llm_result.get("is_newspaper_front_page"):
        logger.info(f"{pdf_path.name}: not a newspaper — leaving untouched in Downloads.")
        return "ignored"

    llm_date = llm_result.get("date")
    raw_name = llm_result.get("newspaper_name", "")
    text_name = canonicalize_name(raw_name)
    hint_name = filename_hint_name(pdf_path.name)

    if regex_date and llm_date and regex_date != llm_date:
        logger.warning(f"Date mismatch for {pdf_path.name}: regex={regex_date} vs llm={llm_date}. Flagging.")
        REVIEW_ROOT.mkdir(exist_ok=True)
        shutil.move(str(pdf_path), str(REVIEW_ROOT / pdf_path.name))
        return "flagged"

    if text_name and hint_name and text_name != hint_name:
        logger.warning(f"Name mismatch for {pdf_path.name}: page text said '{text_name}', filename suggests '{hint_name}'. Flagging.")
        REVIEW_ROOT.mkdir(exist_ok=True)
        shutil.move(str(pdf_path), str(REVIEW_ROOT / pdf_path.name))
        return "flagged"

    name = text_name or hint_name
    if name is None:
        logger.warning(f"Could not determine publisher for {pdf_path.name} (page text: '{raw_name}'). Flagging for review.")
        REVIEW_ROOT.mkdir(exist_ok=True)
        shutil.move(str(pdf_path), str(REVIEW_ROOT / pdf_path.name))
        return "flagged"

    final_date = regex_date or llm_date
    if not final_date:
        logger.warning(f"No date found at all for {pdf_path.name} — flagging.")
        REVIEW_ROOT.mkdir(exist_ok=True)
        shutil.move(str(pdf_path), str(REVIEW_ROOT / pdf_path.name))
        return "flagged"

    new_filename = f"{final_date}_{name}.pdf".replace("/", "-")
    try:
        dest = safe_destination(ARCHIVE_ROOT, final_date, new_filename)
    except ValueError as e:
        logger.error(f"Safety check failed for {pdf_path.name}: {e}")
        return "error"

    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        logger.warning(f"Destination already exists for {pdf_path.name} -> {dest}. Flagging as possible duplicate.")
        REVIEW_ROOT.mkdir(exist_ok=True)
        shutil.move(str(pdf_path), str(REVIEW_ROOT / pdf_path.name))
        return "flagged"

    shutil.move(str(pdf_path), str(dest))
    logger.info(f"Archived: {pdf_path.name} -> {dest}")
    return "archived"


def run_batch():
    since = get_last_run_time()
    started_at = datetime.now()
    new_pdfs = find_new_pdfs(DOWNLOADS_DIR, since)

    if not new_pdfs:
        logger.info(f"No new PDFs in Downloads since {since.isoformat()}.")
        set_last_run_time(started_at)
        return

    logger.info(f"Found {len(new_pdfs)} new PDF(s) since {since.isoformat()}.")
    results = [process_pdf(p) for p in new_pdfs]

    set_last_run_time(started_at)
    summary = {status: results.count(status) for status in set(results)}
    logger.info(f"Batch complete: {summary}")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        process_pdf(sys.argv[1])
    else:
        run_batch()