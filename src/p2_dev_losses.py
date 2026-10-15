"""
p2_dev_losses.py -- DEV TOOL (not part of the daily run). Error budget: why words go wrong, by cause.

WHAT IT DOES
    For every gold page of one set, every real article word is put in one bucket:
      ok                                   in the right story
      split: in another story              placed, but not in its story's main piece (stories split/merged)
      grey: body size, no headline above   body text the rules couldn't give to any headline
      caption zone                         body-size text dropped because it sits just under a photo
      body size, dropped (pull-quote?)     body-size text dropped for another reason
      smaller than body / between body and headline
                                           text in an odd size the rules ignored
      NOT word in a story                  text your key calls "not an article" that ended up inside a story
    Prints the buckets per page, then totals for the whole set.

WHY IT EXISTS
    To fix the biggest cause first instead of the one noticed first. On 14 Oct it reordered the plan: left-over
    text cost 2,050 words and wrong-story text 1,770, captions only 371 (the cause originally planned first).

WHEN TO RUN
    Before choosing the next rules fix, and after a fix to see which bucket shrank. TUNE pages only.

USAGE (from ~/news-intel)
    .venv/bin/python src/p2_dev_losses.py
    .venv/bin/python src/p2_dev_losses.py --split HELD-BACK     # diagnosis only, never to choose fixes
"""

import argparse
import glob
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parent))
from p2_check import read_key, _inside
from p2_extract import page_blocks
import p2_extract_rules as R

ROOT = Path(__file__).resolve().parent.parent


def page_buckets(page, labels):
    res = R.segment_page(page)
    bs = res["body_size"]
    blocks = page_blocks(page)
    photos = [pymupdf.Rect(i["bbox"]) for i in page.get_image_info() if pymupdf.Rect(i["bbox"]).width > 40]
    words = [w for w in page.get_text("words") if w[4].strip()]
    if not words:
        return Counter()
    C = np.array([((w[0] + w[2]) / 2, (w[1] + w[3]) / 2) for w in words])
    IB = _inside(C, [tuple(b["bbox"]) for b in blocks])
    truth = []
    for i in range(len(words)):
        h = np.flatnonzero(IB[i])
        truth.append(labels.get(blocks[min(h, key=lambda j: blocks[j]["bbox"].get_area())]["id"], "NOT")
                     if len(h) else "NOT")
    pred = [None] * len(words)
    for k, a in enumerate(res["articles"], 1):
        IN = _inside(C, [tuple(r) for r in a["_boxes"]] + [tuple(a["_hbox"])])
        for i in np.flatnonzero(IN.any(axis=1)):
            if pred[i] is None:
                pred[i] = k
    G = (_inside(C, [tuple(s["bbox"]) for s in res["grey"]]).any(axis=1) if res["grey"]
         else np.zeros(len(words), bool))
    sp = R._spans(page)
    S = _inside(C, [tuple(s["bbox"]) for s in sp])
    dom = defaultdict(Counter)                      # each true story's main predicted piece
    for t, p in zip(truth, pred):
        if t != "NOT" and p:
            dom[t][p] += 1
    c = Counter()
    for i, (t, p) in enumerate(zip(truth, pred)):
        if t == "NOT":
            if p:
                c["NOT word in a story"] += 1
            continue
        if p:
            c["ok" if dom[t].most_common(1)[0][0] == p else "split: in another story"] += 1
            continue
        if G[i]:
            c["grey: body size, no headline above"] += 1
            continue
        h = np.flatnonzero(S[i])
        sz = sp[h[0]]["size"] if len(h) else None
        if sz is None:
            c["no span"] += 1
        elif abs(sz - bs) < R.BODY_SIZE_TOL:
            r = pymupdf.Rect(words[i][:4])
            cap = any(0 <= r.y0 - ph.y1 < R.CAPTION_ZONE + 3 and r.x0 < ph.x1 and r.x1 > ph.x0 for ph in photos)
            c["caption zone" if cap else "body size, dropped (pull-quote?)"] += 1
        elif sz < bs:
            c["smaller than body"] += 1
        else:
            c["between body and headline"] += 1
    return c


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--split", default="TUNE", choices=["TUNE", "HELD-BACK"])
    ap.add_argument("--root", default=str(ROOT))
    ap.add_argument("--papers", default=None, help="folder of PDFs (default: <root>/archive)")
    a = ap.parse_args()
    root = Path(a.root)
    papers = Path(a.papers or root / "archive")
    tot = Counter()
    for kp in sorted(glob.glob(str(root / "eval" / "*.answer_key.txt"))):
        stem = Path(kp).name.replace(".answer_key.txt", "")
        pdfs = glob.glob(str(papers / "*" / f"{stem}.pdf"))
        if not pdfs:
            continue
        doc = pymupdf.open(pdfs[0])
        for pn, info in read_key(kp).items():
            if info["split"] != a.split:
                continue
            c = page_buckets(doc[pn - 1], info["labels"])
            tot += c
            bad = sorted(((k, v) for k, v in c.items() if k != "ok"), key=lambda x: -x[1])
            print(f"{stem[11:]:18s} p{pn:<3d} ok {c['ok']:5d}  " + ", ".join(f"{k} {v}" for k, v in bad))
    print(f"\nTOTAL {tot['ok']} words ok")
    for k, v in tot.most_common():
        if k != "ok":
            print(f"  {v:6d}  {k}")


if __name__ == "__main__":
    main()
