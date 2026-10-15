"""
p2_dev_pick_gold.py -- DEV TOOL (not part of the daily run). Picks the pages for gold-set expansion (D3) and cuts
them into one small PDF to upload for labelling.

WHAT IT DOES
    1. Looks at every page of every MAIN edition of the first-pass papers in archive/ (TH, TOI, BS, BL, Mint;
       ET is dropped). Skips pages that already have an answer key in eval/, ad pages and each paper's front page.
    2. Finds candidate pages for each TYPE below, using rules written in advance (not by looking at pages):
         puzzles       page text contains CROSSWORD, SUDOKU, PUZZLE, KAKURO, WORD SEARCH or QUIZ
         market_data   30% or more of the page's words are numbers (stock/market tables)
         briefs        page text contains IN BRIEF, BRIEFS, QUICKLY, SHORT TAKES, YEARS AGO,
                       or the rules find 3+ short headlines (<= 8 words) stacked in one column
         two_fonts     two different body-text font families, each with 20%+ of the body text
         ad_beside     images cover 20-45% of the page (a partial ad) AND the page has an article under 150 words
    3. Picks AT RANDOM, with fixed seeds (anyone re-running gets the same pages):
         Batch A  gate     : 2 random ordinary pages per paper  (held back, never tuned on)
         Batch B  tuning   : 5 random ordinary pages per paper
         Batch C  targeted : 4 per type (2 tuning + 2 held back), chosen at random among that type's candidates
       "Ordinary" = not a data page (< 50% number words) and 300+ body words. No page is picked twice.
    4. Writes gold_v2_pages.pdf (all picked pages, in order) and gold_v2_manifest.csv (for each page of the new
       PDF: batch, type, tuning or held-back, original paper and page number). Answer keys are later written
       against the ORIGINAL paper and page, so they work with archive/ directly.

WHY IT EXISTS
    Decided 15 Oct (D3): the gold set grows from 18 to ~73 usable pages so rules, gate and router aren't judged on
    3-4 pages per paper. Pages are chosen by written criteria + a random seed, never by hand: hand-picking drifts
    toward clear, easy examples (selection bias), and a seed makes the choice reproducible and auditable.

WHEN TO RUN
    Once, for this expansion. Re-running with the same seeds gives the same pages.

USAGE (from ~/news-intel)
    .venv/bin/python src/p2_dev_pick_gold.py                 # pick, cut, write files to ~/Desktop
    .venv/bin/python src/p2_dev_pick_gold.py --dry-run       # only list candidates and picks, write nothing
"""

import argparse
import csv
import glob
import random
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parent))
from p2_check import read_key
from p2_extract import parse_name, is_ad_page
import p2_extract_rules as R

ROOT = Path(__file__).resolve().parent.parent
PAPERS = ["The Hindu", "Times of India", "Business Standard", "Business Line", "Mint"]
SEED_RANDOM, SEED_TYPES = 16, 17
N_GATE, N_TUNE, N_PER_TYPE = 2, 5, 4
P_PUZZLE = re.compile(r"\b(CROSSWORD|SUDOKU|PUZZLE|KAKURO|WORD SEARCH|QUIZ)\b", re.I)
P_BRIEFS = re.compile(r"\b(IN BRIEF|BRIEFS|QUICKLY|SHORT TAKES|YEARS AGO)\b", re.I)


def page_facts(page):
    """Everything the selection rules need about one page (computed once)."""
    res = R.segment_page(page)
    words = page.get_text().split()
    num = sum(bool(R.NUM_WORD.match(w)) for w in words) / max(1, len(words))
    body = sum(a["words"] for a in res["articles"]) + sum(len(s["text"].split()) for s in res["grey"])
    text = page.get_text()
    # stacked short headlines in one column
    heads = sorted((a["_hbox"] for a in res["articles"] if len(a["headline"].split()) <= 8), key=lambda r: r.y0)
    stacked = max((sum(1 for o in heads if o.x0 < h.x1 and o.x1 > h.x0 and 0 <= o.y0 - h.y0 < 400) for h in heads),
                  default=0)
    # body fonts by family
    bs = res["body_size"] or 0
    fam = Counter()
    for s in R._spans(page):
        if bs and abs(s["size"] - bs) < R.BODY_SIZE_TOL:
            fam[R._family(s["font"])] += len(s["text"])
    tot = sum(fam.values()) or 1
    two_fonts = sum(1 for n in fam.values() if n / tot >= 0.2) >= 2
    area = page.rect.width * page.rect.height
    img = sum(pymupdf.Rect(i["bbox"]).intersect(page.rect).get_area() for i in page.get_image_info()) / area
    return {
        "ad": is_ad_page(page, res), "num": num, "body": body,
        "puzzles": bool(P_PUZZLE.search(text)),
        "market_data": num >= 0.30,
        "briefs": bool(P_BRIEFS.search(text)) or stacked >= 3,
        "two_fonts": two_fonts,
        "ad_beside": 0.20 <= img < 0.45 and any(a["words"] < 150 for a in res["articles"]),
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=str(ROOT))
    ap.add_argument("--papers", default=None, help="folder of PDFs (default: <root>/archive)")
    ap.add_argument("--out", default=str(Path.home() / "Desktop"), help="where to write the PDF and manifest")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    root = Path(a.root)
    papers = Path(a.papers or root / "archive")

    labelled = set()
    for kp in glob.glob(str(root / "eval" / "*.answer_key.txt")):
        stem = Path(kp).name.replace(".answer_key.txt", "")
        labelled |= {(stem, pn) for pn in read_key(kp)}

    facts, ordinary = {}, defaultdict(list)          # (stem, page) -> facts ; paper -> [(stem, page)]
    pdfs = sorted(p for p in papers.rglob("*.pdf") if parse_name(p.stem)[2] is None and parse_name(p.stem)[1] in PAPERS)
    for pdf in pdfs:
        doc = pymupdf.open(pdf)
        front_seen = False
        for pn, page in enumerate(doc, 1):
            try:
                f = page_facts(page)
            except Exception as e:
                print(f"  skipped {pdf.stem} p{pn}: {e}")
                continue
            if f["ad"]:
                continue
            if not front_seen:                       # first non-ad page = front page
                front_seen = True
                continue
            if (pdf.stem, pn) in labelled:
                continue
            facts[(pdf.stem, pn)] = f
            if f["num"] < 0.5 and f["body"] >= 300:
                ordinary[parse_name(pdf.stem)[1]].append((pdf.stem, pn))
        print(f"  scanned {pdf.name}: {len(doc)} pages")

    picks, used = [], set()
    rng = random.Random(SEED_RANDOM)
    for paper in PAPERS:
        pool = sorted(ordinary[paper])
        chosen = rng.sample(pool, min(len(pool), N_GATE + N_TUNE))
        for i, key in enumerate(chosen):
            batch, split = ("A", "HELD-BACK") if i < N_GATE else ("B", "TUNE")
            picks.append((batch, "random", split, *key))
            used.add(key)
    rng = random.Random(SEED_TYPES)
    for t in ["puzzles", "market_data", "briefs", "two_fonts", "ad_beside"]:
        pool = sorted(k for k, f in facts.items() if f[t] and k not in used)
        chosen = rng.sample(pool, min(len(pool), N_PER_TYPE))
        print(f"  type {t:12s}: {len(pool):3d} candidates -> {len(chosen)} picked")
        for i, key in enumerate(chosen):
            picks.append(("C", t, "TUNE" if i < N_PER_TYPE // 2 else "HELD-BACK", *key))
            used.add(key)

    print(f"\n{len(picks)} pages picked:")
    for b, t, sp, stem, pn in picks:
        print(f"  {b} {t:12s} {sp:9s} {stem} p{pn}")
    if a.dry_run:
        return
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    new = pymupdf.open()
    with open(out / "gold_v2_manifest.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["new_page", "batch", "type", "split", "paper", "page"])
        for i, (b, t, sp, stem, pn) in enumerate(picks, 1):
            src = pymupdf.open(next(papers.rglob(f"{stem}.pdf")))
            new.insert_pdf(src, from_page=pn - 1, to_page=pn - 1)
            w.writerow([i, b, t, sp, stem, pn])
    new.save(out / "gold_v2_pages.pdf", garbage=4, deflate=True)
    size = (out / "gold_v2_pages.pdf").stat().st_size / 1e6
    print(f"\nWrote {out / 'gold_v2_pages.pdf'} ({len(picks)} pages, {size:.0f} MB) and gold_v2_manifest.csv")


if __name__ == "__main__":
    main()
