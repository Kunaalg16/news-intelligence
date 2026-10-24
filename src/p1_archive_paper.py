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
project folder.

Changed 13 Oct 2026 (evening) after the first backlog run:
  - Dates: three independent signals (page text, model, filename). A date is
    used only if at least two agree; future dates are thrown out. Before,
    one signal was trusted alone when the other was missing, which filed a
    10 Oct Mint under 18 Oct.
  - Page-text dates now also read "October 10 2026" / "October 10, 2026".
  - New publishers: Times of India, Business Standard (English).
  - Hindi editions are skipped (by filename or by the model's answer).
  - Business Line is no longer mistaken for The Hindu (its masthead reads
    "The Hindu BusinessLine").
  - Editions: International and School editions keep an edition suffix,
    e.g. 2026-10-12_The Hindu_School.pdf. No suffix = main (Delhi) edition.

Changed 21 Oct 2026 (three fixes found while filing 16-19 Oct papers):
  - A. Unknown words in the filename -> needs_review/ (fail-closed). Before,
    any edition word other than International/School fell through to "main
    edition", so "TH North India" or "TH Education Plus" could take the main
    Delhi paper's slot. Now every word in the filename must be a publisher
    word, a known filler word (HD, Editable, Full, Edition, Delhi, New, Tr) or
    a known edition; anything else stops for a human. Checked against all 64
    filenames ever archived: none would be stopped.
  - B. Wrap pages: if the model says the first readable page is not a front
    page, ask once more with the next page that has >= 200 words. A wrap ad
    whose only text is printer's marks (TH 17 Oct: 77 words) counted as
    "readable". Papers whose wrap page already worked are unchanged (the
    first call is the same as before); one extra call only on a "no".
  - C. Filename dates also accept the separators "‹" and "›"
    ("TH ● Delhi ● 19‹10‹2026"), so the filename gets its vote again.

Changed 23 Oct 2026 (found while filing 19-22 Oct papers):
  - "english" is a filler word: Business Standard's main Delhi edition is named
    "BS English-Delhi ..." / "Business Standard English_Delhi_...".
  - Hyderabad is a known edition: filed with a suffix, e.g. 2026-10-19_Business Line_Hyderabad.pdf
    (used when the Delhi edition is missing; the suffix keeps the source visible).
"""

import logging
import os
import re
import shutil
import sys
from collections import Counter
from datetime import date, datetime, timedelta
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
# "10 October 2026", "10OCTOBER2026" (day first) and "October 10 2026", "October 10, 2026" (month first)
DATE_DAY_FIRST = re.compile(rf'(?<!\d)(\d{{1,2}})\s*({MONTHS})\s*,?\s*(\d{{4}})(?!\d)', re.IGNORECASE)
DATE_MONTH_FIRST = re.compile(rf'({MONTHS})\s*(\d{{1,2}})\s*,?\s*(\d{{4}})(?!\d)', re.IGNORECASE)
# dates in filenames, Indian order: 07-10-2026, 11~10~2026, 10_10_2026
FILENAME_DATE = re.compile(r'(?<!\d)(\d{1,2})[-~_. ‹›](\d{1,2})[-~_. ‹›](\d{4})(?!\d)')   # 21 Oct: + ‹ ›
SAFE_NAME = re.compile(r'^[A-Za-z0-9 ,.\-_]{1,80}$')
SAFE_DATE = re.compile(r'^\d{4}-\d{2}-\d{2}$')

KNOWN_PUBLISHERS = {
    "economic times": "Economic Times", "the economic times": "Economic Times", "et": "Economic Times",
    "mint": "Mint",
    "the hindu": "The Hindu", "th": "The Hindu",
    "business line": "Business Line", "businessline": "Business Line", "bl": "Business Line",
    "the hindu businessline": "Business Line", "the hindu business line": "Business Line",
    "times of india": "Times of India", "the times of india": "Times of India", "toi": "Times of India",
    "business standard": "Business Standard", "bs": "Business Standard",
    "financial express": "Financial Express", "fe": "Financial Express",
    "indian express": "Indian Express", "ie": "Indian Express",
}

# Non-main editions get a suffix in the archive name. No suffix = main (Delhi) edition.
EDITIONS = {"international": "International", "school": "School", "hyderabad": "Hyderabad"}   # 23 Oct: + Hyderabad
# 21 Oct (fix A): words allowed in a filename besides publisher words and editions. Anything else -> needs_review/.
# Built from the 64 filenames archived so far (logs/archive.log). Add a word here only after checking a real file.
FILLER_WORDS = {"delhi", "new", "hd", "editable", "full", "edition", "tr", "english"}   # 23 Oct: + english (BS)
PUBLISHER_WORDS = {w for key in KNOWN_PUBLISHERS for w in key.split()}
MIN_FRONT_PAGE_WORDS = 200   # 21 Oct (fix B): the retry page must have at least this many words


def _find_publisher(text):
    """Longest known publisher name that appears as a whole word in the text."""
    text = text.lower()
    for key in sorted(KNOWN_PUBLISHERS, key=len, reverse=True):
        if re.search(rf'(?<![a-z0-9]){re.escape(key)}(?![a-z0-9])', text):
            return KNOWN_PUBLISHERS[key]
    return None


def canonicalize_name(raw_name):
    # exact match first; otherwise look for a known name inside the model's answer,
    # e.g. "Bennett, Coleman & Co. Ltd. (The Times of India)" -> Times of India.
    # "Bennett, Coleman & Co. Ltd." alone stays unknown: that company also owns ET.
    key = raw_name.strip().lower()
    return KNOWN_PUBLISHERS.get(key) or _find_publisher(key)


def filename_hint_name(filename):
    return _find_publisher(filename)


def edition_from_filename(filename):
    stem = filename.lower()
    for key, label in EDITIONS.items():
        if re.search(rf'(?<![a-z0-9]){key}(?![a-z0-9])', stem):
            return label
    return None


def unknown_filename_words(filename):
    """Fix A: words in the filename that are not a publisher word, filler word or edition, e.g. ['north']."""
    words = re.findall(r"[a-z]+", Path(filename).stem.lower())
    return [w for w in words if w not in PUBLISHER_WORDS | FILLER_WORDS | set(EDITIONS)]


def is_hindi(*texts):
    return any("hindi" in (t or "").lower() for t in texts)


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


def readable_pages(pdf_path, max_pages=MAX_PAGES_TO_CHECK):
    """[(page number, text)] for the first pages that have usable text."""
    out = []
    with pdfplumber.open(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages[:max_pages], start=1):
            text = page.extract_text() or ""
            if text.strip() and not is_mostly_garbage(text):
                out.append((i, text))
    return out


def extract_page_text(pdf_path, max_pages=MAX_PAGES_TO_CHECK):
    """First page with usable text (unchanged behaviour)."""
    pages = readable_pages(pdf_path, max_pages)
    return pages[0][1] if pages else "", pages[0][0] if pages else None


def retry_page(pages, first_page_num):
    """Fix B: the next readable page after the first one with >= MIN_FRONT_PAGE_WORDS words, or (None, '')."""
    for num, text in pages:
        if num > first_page_num and len(text.split()) >= MIN_FRONT_PAGE_WORDS:
            return num, text
    return None, ""


def _iso(day, month, year):
    """Return YYYY-MM-DD, or None if it isn't a real date (e.g. 31 February)."""
    try:
        return datetime.strptime(f"{int(day)} {month} {year}", "%d %B %Y").strftime("%Y-%m-%d")
    except ValueError:
        return None


def regex_extract_date(text):
    """Earliest date written out in words on the page (the masthead date is near the top)."""
    found = []
    for m in DATE_DAY_FIRST.finditer(text):
        found.append((m.start(), _iso(m.group(1), m.group(2).capitalize(), m.group(3))))
    for m in DATE_MONTH_FIRST.finditer(text):
        found.append((m.start(), _iso(m.group(2), m.group(1).capitalize(), m.group(3))))
    found = [f for f in found if f[1]]
    return min(found)[1] if found else None


def filename_date(filename):
    m = FILENAME_DATE.search(filename)
    if not m:
        return None
    day, month, year = m.groups()
    try:
        return date(int(year), int(month), int(day)).isoformat()
    except ValueError:
        return None


def decide_date(signals, pdf_name):
    """Majority vote over independent date signals.

    signals: {"page text": "2026-10-10" or None, "model": ..., "filename": ...}
    A date is accepted only if at least two signals agree on it. Malformed and
    future dates are thrown out first. Returns (date, None) or (None, reason).
    """
    today = date.today().isoformat()
    usable = {}
    for source, value in signals.items():
        if not value:
            continue
        if not SAFE_DATE.match(value):
            logger.warning(f"{pdf_name}: {source} date {value!r} is malformed, ignored.")
            continue
        if value > today:
            logger.warning(f"{pdf_name}: {source} date {value} is in the future, ignored.")
            continue
        usable[source] = value
    if not usable:
        return None, "no usable date from any signal"
    best, votes = Counter(usable.values()).most_common(1)[0]
    if votes < 2:
        return None, f"fewer than two date signals agree: {usable}"
    outvoted = {s: v for s, v in usable.items() if v != best}
    if outvoted:
        logger.warning(f"{pdf_name}: date {best} agreed by {votes} signals; outvoted: {outvoted}")
    return best, None


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


def _flag(pdf_path):
    """Move a doubtful file to needs_review/ (a no-op if it is already there)."""
    REVIEW_ROOT.mkdir(exist_ok=True)
    shutil.move(str(pdf_path), str(REVIEW_ROOT / pdf_path.name))
    return "flagged"


def process_pdf(pdf_path):
    pdf_path = Path(pdf_path)
    if is_hindi(pdf_path.name):
        logger.info(f"{pdf_path.name}: Hindi edition, not supported — skipping.")
        return "skipped"

    pages = readable_pages(pdf_path)
    if not pages:
        logger.error(f"No extractable text in first {MAX_PAGES_TO_CHECK} pages of {pdf_path.name} — skipping.")
        return "skipped"
    page_num, text = pages[0]
    if page_num != 1:
        logger.info(f"{pdf_path.name}: page 1 had no usable text, used page {page_num} instead.")

    llm_result = llm_extract(text)
    if llm_result is None:
        logger.error(f"LLM call failed for {pdf_path.name} — leaving in place.")
        return "error"

    if not llm_result.get("is_newspaper_front_page"):
        # Fix B (21 Oct): the first readable page may be a wrap ad with only printer's marks. Ask once more.
        retry_num, retry_text = retry_page(pages, page_num)
        if retry_num is None:
            logger.info(f"{pdf_path.name}: not a newspaper — leaving untouched in Downloads.")
            return "ignored"
        logger.info(f"{pdf_path.name}: page {page_num} ({len(text.split())} words) is not a front page; "
                    f"trying page {retry_num} ({len(retry_text.split())} words).")
        retry_result = llm_extract(retry_text)
        if retry_result is None:
            logger.error(f"LLM call failed for {pdf_path.name} — leaving in place.")
            return "error"
        if not retry_result.get("is_newspaper_front_page"):
            logger.info(f"{pdf_path.name}: not a newspaper (pages {page_num} and {retry_num}) — leaving untouched in Downloads.")
            return "ignored"
        page_num, text, llm_result = retry_num, retry_text, retry_result

    regex_date = regex_extract_date(text)

    raw_name = llm_result.get("newspaper_name") or ""
    if is_hindi(raw_name):
        logger.info(f"{pdf_path.name}: model says '{raw_name}', a Hindi edition, not supported — skipping.")
        return "skipped"
    text_name = canonicalize_name(raw_name)
    hint_name = filename_hint_name(pdf_path.name)

    # Business Line's masthead reads "The Hindu BusinessLine", so the model sometimes
    # answers "The Hindu". When the filename clearly says Business Line, trust it.
    if text_name == "The Hindu" and hint_name == "Business Line":
        logger.info(f"{pdf_path.name}: model said 'The Hindu', filename says Business Line — using Business Line.")
        text_name = "Business Line"

    if text_name and hint_name and text_name != hint_name:
        logger.warning(f"Name mismatch for {pdf_path.name}: page text said '{text_name}', filename suggests '{hint_name}'. Flagging.")
        return _flag(pdf_path)

    name = text_name or hint_name
    if name is None:
        logger.warning(f"Could not determine publisher for {pdf_path.name} (page text: '{raw_name}'). Flagging for review.")
        return _flag(pdf_path)

    final_date, problem = decide_date(
        {"page text": regex_date, "model": llm_result.get("date"), "filename": filename_date(pdf_path.name)},
        pdf_path.name,
    )
    if final_date is None:
        logger.warning(f"Date problem for {pdf_path.name}: {problem}. Flagging.")
        return _flag(pdf_path)

    unknown = unknown_filename_words(pdf_path.name)
    if unknown:   # fix A (21 Oct): fail-closed, never let an unknown edition take the main paper's slot
        logger.warning(f"Unknown word(s) in filename {pdf_path.name}: {', '.join(unknown)} "
                       f"(an edition or supplement?). Flagging for review.")
        return _flag(pdf_path)

    edition = edition_from_filename(pdf_path.name)
    suffix = f"_{edition}" if edition else ""
    new_filename = f"{final_date}_{name}{suffix}.pdf".replace("/", "-")
    try:
        dest = safe_destination(ARCHIVE_ROOT, final_date, new_filename)
    except ValueError as e:
        logger.error(f"Safety check failed for {pdf_path.name}: {e}")
        return "error"

    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        logger.warning(f"Destination already exists for {pdf_path.name} -> {dest}. Flagging as possible duplicate.")
        return _flag(pdf_path)

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