"""
p2_dev_score.py -- DEV TOOL (not part of the daily run). Compact scores of the rules on the gold set.

WHAT IT DOES
    Runs the current rules (p2_extract_rules.py) on every gold page in eval/ and prints, per paper, the four
    scores against your answer keys: coverage, precision, recall, stories intact. Also one POOLED row: all pages of
    that set counted together. "PASS" = coverage, precision and recall all >= 90% (the gate).
    It uses the same scoring code as p2_check.py; it only prints less and adds the pooled row.

WHY IT EXISTS
    To compare two versions of the rules quickly: before/after a fix, or with one fix switched off (ablation).
    Every rules number in STATUS since 14 Oct comes from it (e.g. tuning pooled 82.7/95.2/58.2 -> 97.5/94.3/91.1).

WHEN TO RUN
    Only when changing the rules. Use TUNE pages while developing; HELD-BACK pages only for the gate, once.

USAGE (from ~/news-intel)
    .venv/bin/python src/p2_dev_score.py                    # tuning pages, one row per paper + pooled
    .venv/bin/python src/p2_dev_score.py --pages            # also one row per page
    .venv/bin/python src/p2_dev_score.py --split HELD-BACK  # gate pages (don't tune on these)
    NI_OFF=F5 .venv/bin/python src/p2_dev_score.py          # same, with rule fix F5 switched off (ablation)
"""

import argparse
import glob
import sys
from pathlib import Path

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parent))
from p2_check import read_key, page_words, score

ROOT = Path(__file__).resolve().parent.parent


def run(split="TUNE", root=ROOT, papers=None, show_pages=False, verbose=True):
    """-> {(paper, page or 'ALL'): scores, ('POOLED', ''): scores}"""
    papers = Path(papers or root / "archive")
    allp, out = [], {}
    for kp in sorted(glob.glob(str(root / "eval" / "*.answer_key.txt"))):
        stem = Path(kp).name.replace(".answer_key.txt", "")
        pdfs = glob.glob(str(papers / "*" / f"{stem}.pdf"))
        if not pdfs:
            continue
        doc = pymupdf.open(pdfs[0])
        pp = []
        for pn, info in read_key(kp).items():
            if info["split"] != split:
                continue
            q = [(f"{stem}p{pn}:{t}", f"{stem}p{pn}:{p}" if p else None)
                 for t, p in page_words(doc[pn - 1], info["labels"])]
            pp += q
            out[(stem[11:], pn)] = score(q)
        if pp:
            out[(stem[11:], "ALL")] = score(pp)
            allp += pp
    if allp:
        out[("POOLED", "")] = score(allp)
    if verbose:
        for (n, p), s in out.items():
            if p == "ALL" or n == "POOLED" or show_pages:
                ok = all(s[k] >= 0.9 for k in ("coverage", "precision", "recall"))
                print(f"{n:18s} {str(p):>4s} cov {s['coverage']:6.1%} prec {s['precision']:6.1%} "
                      f"rec {s['recall']:6.1%} intact {s['intact']:3d}/{s['stories']:<3d} {'PASS' if ok else ''}")
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--split", default="TUNE", choices=["TUNE", "HELD-BACK"])
    ap.add_argument("--pages", action="store_true", help="also print one row per page")
    ap.add_argument("--root", default=str(ROOT))
    ap.add_argument("--papers", default=None, help="folder of PDFs (default: <root>/archive)")
    a = ap.parse_args()
    run(a.split, Path(a.root), a.papers, a.pages)
