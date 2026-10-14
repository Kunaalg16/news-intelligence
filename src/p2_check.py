"""
p2_check.py -- Phase 2.2 Check: score 2.1 Extract against the gold set (eval/*.answer_key.txt).

Scores WORDS, not blocks, so lines lost inside a correctly placed block still count.
  truth      every word -> its story from the answer key (Gn counts as story n), or NOT (not an article)
  predicted  every word -> the story the rules put it in, or none
  coverage   share of true article words that are in SOME predicted story
  precision  of word pairs the rules put together, share that truly belong together
             (words the key calls NOT count as wrong company: each is its own true group)
  recall     of word pairs that truly belong together, share the rules kept together
  intact     real stories that came out >= 90% in one piece, with that piece >= 90% this story
Pair counts come from a contingency table: sum of C(n,2) over cells / rows / columns.

USAGE
    python src/p2_check.py                 # all answer keys, tuning + held-back pages
    python src/p2_check.py --held-back     # gate pages only
    python src/p2_check.py --referee       # pages the referee has answered (review/<stem>/referee_pNN.json)
"""

import argparse
import glob
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parent))
from p2_extract import page_blocks
from p2_extract_rules import segment_page

GATE = 0.90
INTACT = 0.90   # a story is intact if one piece holds >= 90% of it and is >= 90% pure


def read_key(path):
    """-> {page: {'split': 'TUNE'|'HELD-BACK', 'labels': {block_id: label}}}"""
    pages, cur = {}, None
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        m = re.match(r"\[page (\d+)\] (\S+)", line)
        if m:
            cur = int(m.group(1))
            pages[cur] = {"split": m.group(2), "labels": {}}
        elif cur and re.match(r"^(S\d+|G\d+|NOT):", line):
            lab, ids = line.split(":", 1)
            truth = "NOT" if lab == "NOT" else "S" + lab[1:]          # G3 -> S3 for grouping
            for i in ids.split():
                pages[cur]["labels"][i] = truth
    return pages


def _inside(centres, rects):
    """bool matrix: word centre (n,2) inside rect (m,4)."""
    if not len(rects):
        return np.zeros((len(centres), 0), bool)
    R = np.array(rects)
    x, y = centres[:, :1], centres[:, 1:]
    return (x >= R[:, 0] - 0.5) & (x <= R[:, 2] + 0.5) & (y >= R[:, 1] - 0.5) & (y <= R[:, 3] + 0.5)


def page_words(page, key_labels):
    """Words of the page with (truth, predicted) labels."""
    words = [w for w in page.get_text("words") if w[4].strip()]
    if not words:
        return []
    C = np.array([((w[0] + w[2]) / 2, (w[1] + w[3]) / 2) for w in words])
    # truth: the block (as numbered by p2_extract.page_blocks) containing the word
    blocks = page_blocks(page)
    IB = _inside(C, [tuple(b["bbox"]) for b in blocks])
    truth = []
    for i in range(len(words)):
        hits = np.flatnonzero(IB[i])
        if len(hits):                                    # smallest containing block wins
            h = min(hits, key=lambda j: blocks[j]["bbox"].get_area())
            truth.append(key_labels.get(blocks[h]["id"], "NOT"))
        else:
            truth.append("NOT")
    # predicted: the story whose span boxes / headline box contain the word
    res = segment_page(page)
    pred = [None] * len(words)
    for k, a in enumerate(res["articles"], 1):
        IN = _inside(C, [tuple(r) for r in a["_boxes"]] + [tuple(a["_hbox"])])
        for i in np.flatnonzero(IN.any(axis=1)):
            if pred[i] is None:
                pred[i] = f"P{k}"
    return list(zip(truth, pred))


def page_words_referee(page, key_labels, ref_labels):
    """Like page_words, but the prediction is the referee's label of the block holding the word.
    'rN' = referee article N, 'd:sN' = kept the rules' draft story N, NOT / d:grey / d:other = in no story."""
    words = [w for w in page.get_text("words") if w[4].strip()]
    if not words:
        return []
    C = np.array([((w[0] + w[2]) / 2, (w[1] + w[3]) / 2) for w in words])
    blocks = page_blocks(page)
    IB = _inside(C, [tuple(b["bbox"]) for b in blocks])
    out = []
    for i in range(len(words)):
        hits = np.flatnonzero(IB[i])
        if not len(hits):
            out.append(("NOT", None))
            continue
        bid = blocks[min(hits, key=lambda j: blocks[j]["bbox"].get_area())]["id"]
        r = ref_labels.get(bid, "NOT")
        pred = None if r in ("NOT", "d:grey", "d:other") else r
        out.append((key_labels.get(bid, "NOT"), pred))
    return out


def c2(n):
    return n * (n - 1) // 2


def score(pairs):
    """pairs: list of (truth, pred) over all words of the pages being scored (labels page-qualified)."""
    art = [(t, p) for t, p in pairs if not t.endswith("NOT")]
    covered = sum(1 for t, p in art if p is not None)
    # contingency over words that are in a predicted story
    inpred = [(t if not t.endswith("NOT") else f"{t}#{i}", p) for i, (t, p) in enumerate(pairs) if p is not None]
    cell, row, col = Counter(inpred), Counter(t for t, _ in inpred), Counter(p for _, p in inpred)
    same = sum(c2(n) for n in cell.values())
    prec = same / max(1, sum(c2(n) for n in col.values()))
    # recall: truth groups over ALL article words (unpredicted words break their group)
    trow = Counter(t for t, _ in art)
    rec = same / max(1, sum(c2(n) for n in trow.values()))
    # stories kept intact: one predicted piece holds >= 90% of the story AND is >= 90% this story
    pred_size = Counter(p for _, p in pairs if p is not None)
    by_story = defaultdict(Counter)
    for t, p in art:
        by_story[t][p] += 1
    intact = 0
    for t, c in by_story.items():
        best, n = max(((p, n) for p, n in c.items() if p is not None), key=lambda x: x[1], default=(None, 0))
        if best and n >= INTACT * sum(c.values()) and n >= INTACT * pred_size[best]:
            intact += 1
    return {"words": len(art), "coverage": covered / max(1, len(art)), "precision": prec, "recall": rec,
            "intact": intact, "stories": len(by_story)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="ni")
    ap.add_argument("--papers", default="papers")
    ap.add_argument("--held-back", action="store_true")
    ap.add_argument("--referee", action="store_true",
                    help="score the referee's answers (review/<stem>/referee_pNN.json) instead of the rules")
    args = ap.parse_args()
    rows = []
    if args.referee:
        per = defaultdict(list)
        for kp in sorted(glob.glob(f"{args.root}/eval/*.answer_key.txt")):
            stem = Path(kp).name.replace(".answer_key.txt", "")
            pdfs = glob.glob(f"{args.papers}/*/{stem}.pdf")
            if not pdfs:
                continue
            doc = pymupdf.open(pdfs[0])
            for pn, info in read_key(kp).items():
                rf = Path(args.root) / "review" / stem / f"referee_p{pn:02d}.json"
                if not rf.exists():
                    continue
                ref = json.loads(rf.read_text(encoding="utf-8"))
                split = "held" if info["split"] == "HELD-BACK" else "tune"
                q = [(f"{stem}p{pn}:{t}", f"{stem}p{pn}:{p}" if p else None)
                     for t, p in page_words_referee(doc[pn - 1], info["labels"], ref["labels"])]
                per[(stem[11:], split)] += q
                rows.append((stem[11:], pn, split, score(q)))
        for (name, split), q in sorted(per.items()):
            rows.append((name, "ALL", split, score(q)))
        args.held_back = False
    for kp in ([] if args.referee else sorted(glob.glob(f"{args.root}/eval/*.answer_key.txt"))):
        stem = Path(kp).name.replace(".answer_key.txt", "")
        pdf = glob.glob(f"{args.papers}/*/{stem}.pdf")[0]
        doc = pymupdf.open(pdf)
        per = defaultdict(list)
        for pn, info in read_key(kp).items():
            split = "held" if info["split"] == "HELD-BACK" else "tune"
            wp = page_words(doc[pn - 1], info["labels"])
            per[split] += [(f"p{pn}:{t}", f"p{pn}:{p}" if p else None) for t, p in wp]
            s = score([(f"p{pn}:{t}", f"p{pn}:{p}" if p else None) for t, p in wp])
            rows.append((stem[11:], pn, split, s))
        for split in ("tune", "held"):
            if per[split]:
                rows.append((stem[11:], "ALL", split, score(per[split])))
    print(f"{'paper':18s} {'page':>4s} {'set':5s} {'words':>6s} {'cover':>6s} {'prec':>6s} {'recall':>6s} {'intact':>8s}  gate")
    for name, pn, split, s in rows:
        if args.held_back and split != "held":
            continue
        ok = all(s[k] >= GATE for k in ("coverage", "precision", "recall"))
        bold = pn == "ALL"
        print(f"{name:18s} {str(pn):>4s} {split:5s} {s['words']:6d} {s['coverage']:6.1%} {s['precision']:6.1%} {s['recall']:6.1%} {str(s['intact']) + '/' + str(s['stories']):>8s}  "
              f"{'PASS' if ok else 'fail'}{'  <== paper' if bold else ''}")


if __name__ == "__main__":
    main()
