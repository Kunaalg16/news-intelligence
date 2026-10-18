"""
p2_dev_junk.py -- DEV TOOL (not part of the daily run). The junk-page check for 2.1a Screen (18 Oct 2026).

WHAT IT DOES
    The grader (p2_check.py) only scores pages that have an answer key. Junk pages (all ads, tenders, notices,
    stock tables) have no key, because they hold no stories, so the grader never sees what Screen does there.
    This tool fills that gap. For every junk PAGE listed in the Screen label files it:
      1. splits the page with the rules as they are            -> "without Screen": fake articles, their words
      2. runs Screen first (skip the page, or blank its zones on a copy), then splits -> "with Screen"
    and prints one line per page plus a total. Target (agreed 18 Oct): at most 1 fake article per junk page.

READS
    eval/gold_v2_screen_and_charts.txt  (sheet B: lines "T10  2026-10-14_Times of India p17  ..." under
                                         "## Screen PAGES"; G = gate page, T = practice page)
    eval/gold_v2_sheet_c_screen.txt     (sheet C: lines "PAGE  C5  2026-10-09_Times of India p23  ...  TUNE")
    archive/<date>/<paper>.pdf

PRACTICE PAGES ONLY by default (exam pages stay sealed). --held-back is for the frozen gate run only.

USAGE (from ~/news-intel)
    .venv/bin/python src/p2_dev_junk.py
"""

import argparse
import glob
import re
import sys
from pathlib import Path

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parent))
from p2_extract_rules import segment_page
from p2_screen import screen_page
from p2_check import _screened_copy

ROOT = Path(__file__).resolve().parent.parent


def junk_pages(root):
    """-> [(tag, stem, page, split)] from the two Screen label files."""
    out = []
    b = root / "eval" / "gold_v2_screen_and_charts.txt"
    if b.exists():
        section = None
        for line in b.read_text(encoding="utf-8").splitlines():
            if line.startswith("## "):
                section = line
                continue
            m = re.match(r"^([GT]\d+)\s+(\d{4}-\d\d-\d\d_.+?) p(\d+)\s+(.*)$", line)
            if m and section and "Screen PAGES" in section and "chart" not in m[4].lower():
                out.append((m[1], m[2], int(m[3]), "HELD-BACK" if m[1].startswith("G") else "TUNE"))
    c = root / "eval" / "gold_v2_sheet_c_screen.txt"
    if c.exists():
        for line in c.read_text(encoding="utf-8").splitlines():
            m = re.match(r"^PAGE\s+(C\d+)\s+(\d{4}-\d\d-\d\d_.+?) p(\d+)\s+.*?(TUNE|HELD-BACK)\s*$", line)
            if m:
                out.append((m[1], m[2], int(m[3]), m[4]))
    return out


def split_count(page):
    res = segment_page(page)
    return len(res["articles"]), sum(a["words"] for a in res["articles"])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=str(ROOT))
    ap.add_argument("--papers", default=None, help="folder of PDFs (default: <root>/archive)")
    ap.add_argument("--held-back", action="store_true", help="exam pages only (task 7, once)")
    a = ap.parse_args()
    root = Path(a.root)
    papers = Path(a.papers or root / "archive")
    want = "HELD-BACK" if a.held_back else "TUNE"
    print("(exam pages only)" if a.held_back else "(practice pages only: exam pages not shown)")
    print(f"{'page':5s} {'paper':28s} {'pg':>3s}  {'without Screen':>16s}  {'with Screen':>16s}  why")
    tot = [0, 0, 0, 0]
    n = 0
    for tag, stem, pn, split in junk_pages(root):
        if split != want:
            continue
        pdfs = glob.glob(str(papers / "*" / f"{stem}.pdf"))
        if not pdfs:
            print(f"{tag:5s} {stem:28s} {pn:3d}  skipped: no PDF")
            continue
        page = pymupdf.open(pdfs[0])[pn - 1]
        a0, w0 = split_count(page)
        v = screen_page(page)
        if v["skip_page"]:
            a1, w1 = 0, 0
        elif v["zones"]:
            keep, p2 = _screened_copy(page, v)
            a1, w1 = split_count(p2)
        else:
            a1, w1 = a0, w0
        why = "; ".join(v["why"])[:70] or "-"
        print(f"{tag:5s} {stem[5:]:28s} {pn:3d}  {a0:4d} art {w0:6d} w  {a1:4d} art {w1:6d} w  {why}")
        tot = [tot[0] + a0, tot[1] + w0, tot[2] + a1, tot[3] + w1]
        n += 1
    if n:
        print(f"TOTAL {n} junk pages: without Screen {tot[0]} fake articles ({tot[1]} words), "
              f"with Screen {tot[2]} ({tot[3]} words); target <= {n} (1 per page)")


if __name__ == "__main__":
    main()
