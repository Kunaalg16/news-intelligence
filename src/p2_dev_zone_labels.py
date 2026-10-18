"""
p2_dev_zone_labels.py -- dev tool for 2.1a Screen: list the short printed labels each paper uses, across the archive.

WHAT   Reads every main-edition PDF under archive/ (read-only) and collects short label-like lines:
       1-5 words, set in CAPITALS or clearly bigger than body text, bold or capitals.
       ("SUDOKU", "PUBLIC NOTICE", "BL TWO-WAY CROSSWORD 2739", "TENDERS INVITED", "MINT MEDIA MARKETING INITIATIVE")
WHY    Screen's cheapest tier is an exact list of printed zone labels per paper. A label that appears in many issues
       of one paper is a house-style fact (allowed per paper, 04_DECISIONS 15 Oct), not a fitted number.
       Running over the whole archive (not just the gold pages) finds labels the gold set never showed.
WHEN   Once, before writing the per-paper label list (task 5). Re-run when a new paper is added.
HOW    cd ~/news-intel && .venv/bin/python src/p2_dev_zone_labels.py archive
       options: --min-issues 3   keep labels seen in at least this many issues of a paper (default 3)
WRITES review/zone_labels.csv  (paper, label, issues seen, pages seen, typical size, typical vertical position, example page)
       Prints the top labels per paper. Changes nothing else; makes no API calls.
Numbers are normalised (digits -> #) so "CROSSWORD 2739" and "CROSSWORD 2740" count as one label.
"""

import argparse
import csv
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parent))
from p2_extract import parse_name                                   # same filename rules as extraction
from p2_extract_rules import segment_page


def label_lines(page, body_size):
    """Short label-like lines on one page: (normalised text, size, y as share of page height)."""
    out = []
    H = page.rect.height
    for b in page.get_text("dict")["blocks"]:
        for l in b.get("lines", []):
            if abs(l["dir"][1]) > 0.1:
                continue
            sp = [s for s in l["spans"] if s["text"].strip()]
            if not sp:
                continue
            t = " ".join(s["text"].strip() for s in sp)
            n = len(t.split())
            z = max(s["size"] for s in sp)
            caps = t.upper() == t and sum(c.isalpha() for c in t) >= 3
            bold = any(s["flags"] & 16 or "Bold" in s["font"] or "Black" in s["font"] for s in sp)
            big = bool(body_size) and z >= 1.3 * body_size
            if 1 <= n <= 5 and (caps or big) and (bold or caps):
                norm = re.sub(r"\d+", "#", re.sub(r"\s+", " ", t)).strip(" .:|-»>")
                if len(norm) >= 3:
                    out.append((norm.upper(), round(z, 1), round(l["bbox"][1] / H, 2)))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("archive")
    ap.add_argument("--root", default=str(Path(__file__).resolve().parent.parent))
    ap.add_argument("--min-issues", type=int, default=3)
    args = ap.parse_args()
    pdfs = sorted(p for p in Path(args.archive).rglob("*.pdf") if parse_name(p.stem)[2] is None)
    if not pdfs:
        sys.exit(f"No main-edition PDFs under {args.archive}")
    issues = defaultdict(set)          # (paper, label) -> {date}
    pages = Counter()                  # (paper, label) -> pages seen
    sizes = defaultdict(list)
    ys = defaultdict(list)
    example = {}
    for i, pdf in enumerate(pdfs, 1):
        date, pub, _ = parse_name(pdf.stem)
        doc = pymupdf.open(pdf)
        for pn, page in enumerate(doc, 1):
            try:
                bs = segment_page(page)["body_size"]
            except Exception:
                bs = None
            seen = set()
            for norm, z, y in label_lines(page, bs):
                k = (pub, norm)
                issues[k].add(date)
                if norm not in seen:
                    pages[k] += 1
                    seen.add(norm)
                sizes[k].append(z)
                ys[k].append(y)
                example.setdefault(k, f"{pdf.stem} p{pn}")
        print(f"[{i}/{len(pdfs)}] {pdf.name}", file=sys.stderr)
    rows = []
    for k, ds in issues.items():
        if len(ds) < args.min_issues:
            continue
        s, y = sorted(sizes[k]), sorted(ys[k])
        rows.append({"paper": k[0], "label": k[1], "issues": len(ds), "pages": pages[k],
                     "size": s[len(s) // 2], "y_share": y[len(y) // 2], "example": example[k]})
    rows.sort(key=lambda r: (r["paper"], -r["issues"], -r["pages"]))
    out = Path(args.root) / "review" / "zone_labels.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else ["paper"])
        w.writeheader()
        w.writerows(rows)
    by = defaultdict(list)
    for r in rows:
        by[r["paper"]].append(r)
    for pub, rs in by.items():
        print(f"\n== {pub}: {len(rs)} labels seen in >= {args.min_issues} issues")
        for r in rs[:40]:
            print(f"  {r['issues']:3d} issues {r['pages']:4d} pages  {r['size']:5.1f}pt  y {r['y_share']:.2f}  {r['label']}")
    print(f"\nwrote {out} ({len(rows)} rows)")


if __name__ == "__main__":
    main()
