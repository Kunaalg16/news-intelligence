"""
p2_dev_diag.py -- DEV TOOL (not part of the daily run). Why does ONE gold page score badly?

WHAT IT DOES
    For one page that has an answer key, prints two views:
      1. STORIES THE RULES MADE: each predicted story (P1, P2 ...) with its headline and how many of its words
         belong to which true story (S1, S2 ...) or are NOT article text. A P with several S's = stories merged;
         one S spread over several P's = a story split. "lost" = true article words in no predicted story.
      2. BLOCKS (with --blocks): every block of every true story, with where its words went:
         P3 = predicted story 3, grey = body text left unplaced, drop(sz8.9) = text of size 8.9 the rules threw out.

WHY IT EXISTS
    It found the causes behind most 14 Oct fixes, e.g. TOI p12's 7.5-pt text being ignored (rounding bug, F3),
    the crossword inside BL's GST story, and Mint p7's dropped headline line.

WHEN TO RUN
    When a page scores badly and the cause isn't clear. Tuning pages freely; on a held-back page only to
    classify an error type after the gate has been run, never to design a fix.

USAGE (from ~/news-intel)
    .venv/bin/python src/p2_dev_diag.py "2026-10-13_Mint" 16
    .venv/bin/python src/p2_dev_diag.py "2026-10-13_Mint" 16 --blocks
"""

import argparse
import glob
import sys
from collections import Counter, defaultdict
from pathlib import Path

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parent))
from p2_check import read_key, page_words
from p2_extract import page_blocks
import p2_extract_rules as R

ROOT = Path(__file__).resolve().parent.parent


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paper", help='file name without .pdf, e.g. "2026-10-13_Mint"')
    ap.add_argument("page", type=int)
    ap.add_argument("--blocks", action="store_true", help="also list every block of every true story")
    ap.add_argument("--root", default=str(ROOT))
    ap.add_argument("--papers", default=None, help="folder of PDFs (default: <root>/archive)")
    a = ap.parse_args()
    root = Path(a.root)
    papers = Path(a.papers or root / "archive")
    pdf = glob.glob(str(papers / "*" / f"{a.paper}.pdf"))
    key = root / "eval" / f"{a.paper}.answer_key.txt"
    if not pdf or not key.exists():
        sys.exit("Need both the PDF in the archive and its answer key in eval/.")
    info = read_key(key).get(a.page)
    if not info:
        sys.exit(f"Page {a.page} has no answer key.")
    page = pymupdf.open(pdf[0])[a.page - 1]
    res = R.segment_page(page)
    wp = page_words(page, info["labels"])
    c = Counter(wp)

    print(f"{a.paper} p{a.page} ({info['split']}) · body size {res['body_size']}")
    print("\n1. STORIES THE RULES MADE")
    for k, art in enumerate(res["articles"], 1):
        comp = {t: n for (t, p), n in c.items() if p == f"P{k}"}
        print(f"  P{k:<2d} {art['headline'][:50]!r:54s} {comp}")
    lost = {t: n for (t, p), n in c.items() if p is None and t != "NOT"}
    print(f"  lost (true article words in no story): {lost or 'none'}")
    if res["labels"]:
        print("  headline-like text not made a story:", ", ".join(repr(l["headline"][:30]) for l in res["labels"][:12]))

    if a.blocks:
        print("\n2. BLOCKS OF EACH TRUE STORY")
        pred = {}
        for k, art in enumerate(res["articles"], 1):
            for r in art["_boxes"] + [art["_hbox"]]:
                pred[tuple(r)] = f"P{k}"
        grey = {tuple(s["bbox"]) for s in res["grey"]}
        spans = R._spans(page)
        by_truth = defaultdict(list)
        for b in page_blocks(page):
            t = info["labels"].get(b["id"], "NOT")
            if t == "NOT":
                continue
            d = Counter()
            for s in spans:
                if b["bbox"].contains(s["bbox"].tl + (0.5, 0.5)):
                    key_ = tuple(s["bbox"])
                    d[pred.get(key_) or ("grey" if key_ in grey else f"drop(sz{s['size']:.1f})")] += len(s["text"].split())
            by_truth[t].append((b["id"], dict(d), b["text"][:50]))
        for t in sorted(by_truth, key=lambda x: int(x[1:])):
            print(f"  {t}")
            for bid, d, tx in by_truth[t]:
                print(f"     {bid:5s} {d}  {tx!r}")


if __name__ == "__main__":
    main()
