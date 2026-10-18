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

USAGE (on the Mac add  --root . --papers archive)
    python src/p2_check.py                 # tuning (practice) pages only -- the default since 18 Oct
    python src/p2_check.py --held-back     # gate (exam) pages only: task 7, once, after the freeze
    python src/p2_check.py --referee       # pages the referee has answered (review/<stem>/referee_pNN.json)
    python src/p2_check.py --whole-blocks  # D9: score the whole-block text (17 Oct)
    python src/p2_check.py --screen        # 2.1a Screen v0 first, then split (17 Oct)
    python src/p2_check.py --screen --whole-blocks --tune-only   # both; task 5 runs tuning pages only

MODES (17 Oct; WRITTEN, NOT RUN on gold -- run together in task 5, decision D11)
  --whole-blocks  a block the rules already kept >= WB_KEPT_SHARE of goes to that story whole (as p2_extract
                  --whole-blocks builds the text). "moved" counts words taken from another story that way.
  --screen        p2_screen.screen_page decides first; a skipped page has no stories, a screened zone's text is
                  blacked out on a copy of the page BEFORE splitting (as extraction will do it). "screened art"
                  = true article words removed by Screen: the H1 line (coverage drop <= 0.5 on every page).
  With any mode, each row is printed twice, base and mode, plus the D7 verdict: weighted average (coverage x2,
  precision x2, recall x1, intact share x1) rises >= 0.5 AND no metric falls > 1 point.
  Pages listed in eval/gold_v2_screen_and_charts.txt as not scored for splitting are skipped as before (they
  have no [page] section in a key).
"""

import argparse
import os
import glob
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parent))
import p2_extract
from p2_extract import page_blocks, assign_blocks
from p2_extract_rules import segment_page

WB_KEPT_SHARE = getattr(p2_extract, "WB_KEPT_SHARE", 0.5)   # same knob as extraction once D9 is installed

GATE = 0.90
DROPPED = {"Economic Times"}   # 18 Oct: publishers out of the first pass (D6) are not scored or pooled
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


def _screened_copy(page, verdict):
    """A one-page copy of `page` with the screened zones' text removed (redaction), for splitting."""
    doc = pymupdf.open()
    doc.insert_pdf(page.parent, from_page=page.number, to_page=page.number)
    p = doc[0]
    for z in verdict["zones"]:
        p.add_redact_annot(z)
    if verdict["zones"]:
        p.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE, graphics=pymupdf.PDF_REDACT_LINE_ART_NONE)
    return doc, p


def page_words(page, key_labels, whole_blocks=False, screen=False, info=None):
    """Words of the page with (truth, predicted) labels. info (dict) collects mode counters."""
    words = [w for w in page.get_text("words") if w[4].strip()]
    if not words:
        return []
    C = np.array([((w[0] + w[2]) / 2, (w[1] + w[3]) / 2) for w in words])
    # truth: the block (as numbered by p2_extract.page_blocks) containing the word
    blocks = page_blocks(page)
    IB = _inside(C, [tuple(b["bbox"]) for b in blocks])
    truth, home = [], []
    for i in range(len(words)):
        hits = np.flatnonzero(IB[i])
        if len(hits):                                    # smallest containing block wins
            h = min(hits, key=lambda j: blocks[j]["bbox"].get_area())
            truth.append(key_labels.get(blocks[h]["id"], "NOT"))
            home.append(h)
        else:
            truth.append("NOT")
            home.append(None)
    # predicted: the story whose span boxes / headline box contain the word
    pred = [None] * len(words)
    seg_page, keep = page, None
    if screen:
        from p2_screen import screen_page
        verdict = screen_page(page)
        if verdict["skip_page"]:
            if info is not None:
                info["screened_art"] = info.get("screened_art", 0) + sum(t != "NOT" for t in truth)
                info["why"] = verdict["why"]
            return list(zip(truth, pred))
        if verdict["zones"]:
            keep, seg_page = _screened_copy(page, verdict)       # keep: hold the copy open while used
            if info is not None:
                info["why"] = verdict["why"]
    res = segment_page(seg_page)
    for k, a in enumerate(res["articles"], 1):
        IN = _inside(C, [tuple(r) for r in a["_boxes"]] + [tuple(a["_hbox"])])
        for i in np.flatnonzero(IN.any(axis=1)):
            if pred[i] is None:
                pred[i] = f"P{k}"
    if screen and info is not None and keep is not None:
        gone = {w[:4] for w in words} - {w[:4] for w in seg_page.get_text("words") if w[4].strip()}
        info["screened_art"] = info.get("screened_art", 0) + sum(
            1 for i, w in enumerate(words) if w[:4] in gone and truth[i] != "NOT")
    if whole_blocks and res["articles"]:
        for k, a in enumerate(res["articles"], 1):
            a["number"] = k
        sb = page_blocks(seg_page)
        assign_blocks(sb, res)
        moved = 0
        for b in sb:
            st = b.get("story", "")
            if not st.startswith("s"):
                continue
            lab = "P" + st[1:]
            r = b["bbox"]
            inb = [i for i in range(len(words)) if r.x0 - 0.5 <= C[i, 0] <= r.x1 + 0.5 and r.y0 - 0.5 <= C[i, 1] <= r.y1 + 0.5]
            if inb and sum(pred[i] == lab for i in inb) >= WB_KEPT_SHARE * len(inb):
                for i in inb:
                    if pred[i] != lab:
                        moved += pred[i] is not None
                        pred[i] = lab
        if info is not None:
            info["moved"] = info.get("moved", 0) + moved
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
    ap.add_argument("--whole-blocks", action="store_true", help="D9: score whole-block text against the base")
    ap.add_argument("--screen", action="store_true", help="2.1a: Screen v0 before splitting, against the base")
    ap.add_argument("--tune-only", action="store_true",
                    help="skip held-back (gate) pages; automatic with --whole-blocks / --screen unless --held-back")
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
                if split == "held" and not args.held_back:
                    continue                       # 18 Oct: exam pages only with --held-back
                q = [(f"{stem}p{pn}:{t}", f"{stem}p{pn}:{p}" if p else None)
                     for t, p in page_words_referee(doc[pn - 1], info["labels"], ref["labels"])]
                per[(stem[5:], split)] += q
                per[("POOLED", split)] += q
                rows.append((stem[5:], pn, split, score(q)))
        for (name, split), q in sorted(per.items(), key=lambda x: (x[0][0] == "POOLED", x[0])):
            rows.append((name, "ALL", split, score(q)))
    modes = args.whole_blocks or args.screen
    tune_only = not args.held_back       # 18 Oct: practice pages unless --held-back is given (protects the exam)
    pooled = defaultdict(lambda: defaultdict(list))                 # variant -> split -> pairs (all papers)
    info_all = defaultdict(dict)
    skipped = []
    for kp in ([] if args.referee else sorted(glob.glob(f"{args.root}/eval/*.answer_key.txt"))):
        stem = Path(kp).name.replace(".answer_key.txt", "")
        if stem.split("_", 1)[1] in DROPPED:
            skipped.append(f"{stem} (publisher dropped)")
            continue
        pdfs = glob.glob(f"{args.papers}/*/{stem}.pdf")
        if not pdfs:                       # 18 Oct: say which paper is missing instead of crashing
            skipped.append(f"{stem} (no PDF at {args.papers}/*/{stem}.pdf)")
            continue
        doc = pymupdf.open(pdfs[0])
        per = defaultdict(lambda: defaultdict(list))
        for pn, info in read_key(kp).items():
            split = "held" if info["split"] == "HELD-BACK" else "tune"
            if tune_only and split == "held":
                continue
            if not args.held_back and split == "held":
                continue
            variants = [("base", {})] + ([("mode", {"whole_blocks": args.whole_blocks, "screen": args.screen})] if modes else [])
            for var, kw in variants:
                inf = info_all[(stem, pn, var)]
                wp = page_words(doc[pn - 1], info["labels"], info=inf, **kw)
                q = [(f"{stem}p{pn}:{t}", f"{stem}p{pn}:{p}" if p else None) for t, p in wp]
                per[var][split] += q
                pooled[var][split] += q
                rows.append((stem[5:], pn, split, var, score(q), inf))
        for split in ("tune", "held"):
            for var in per:
                if per[var][split]:
                    rows.append((stem[5:], "ALL", split, var, score(per[var][split]), {}))
    for split in ("tune", "held"):
        for var in pooled:
            if pooled[var][split]:
                rows.append(("POOLED", "ALL", split, var, score(pooled[var][split]), {}))
    if args.referee:
        rows = [(n, pn, sp, "base", s, {}) for n, pn, sp, s in rows]
    print("(gate / exam pages only)" if args.held_back else "(tuning pages only: gate / exam pages not scored)")
    for x in skipped:
        print(f"skipped: {x}")
    print(f"{'paper':24s} {'page':>4s} {'set':5s} {'var':4s} {'words':>6s} {'cover':>6s} {'prec':>6s} {'recall':>6s} {'intact':>8s}  gate"
          + ("  D7   notes" if modes else ""))
    last = {}
    for name, pn, split, var, s, inf in rows:
        if args.held_back and split != "held":
            continue
        ok = all(s[k] >= GATE for k in ("coverage", "precision", "recall"))
        line = (f"{name:24s} {str(pn):>4s} {split:5s} {var:4s} {s['words']:6d} {s['coverage']:6.1%} {s['precision']:6.1%} "
                f"{s['recall']:6.1%} {str(s['intact']) + '/' + str(s['stories']):>8s}  {'PASS' if ok else 'fail'}")
        if modes and var == "mode" and (name, pn, split) in last:
            line += f"  {d7(last[(name, pn, split)], s):4s}"
            notes = []
            if inf.get("moved"):
                notes.append(f"moved {inf['moved']}")
            if inf.get("screened_art"):
                notes.append(f"screened art {inf['screened_art']} ({inf['screened_art'] / max(1, s['words']):.1%})")
            if inf.get("why"):
                notes.append("; ".join(inf["why"])[:60])
            line += "  " + " | ".join(notes)
        last[(name, pn, split)] = s
        print(line + ("  <== paper" if pn == "ALL" and var == "base" and not modes else ""))


def d7(base, new):
    """D7 pass rule: weighted average (coverage x2, precision x2, recall x1, intact share x1) up >= 0.5 point,
    and no single metric down by more than 1 point."""
    def m(s):
        return {"coverage": s["coverage"], "precision": s["precision"], "recall": s["recall"],
                "intact": s["intact"] / max(1, s["stories"])}
    b, n = m(base), m(new)
    w = {"coverage": 2, "precision": 2, "recall": 1, "intact": 1}
    avg = lambda x: sum(w[k] * x[k] for k in w) / sum(w.values())
    up = (avg(n) - avg(b)) * 100
    worst = min((n[k] - b[k]) * 100 for k in w)
    if worst < -1:
        return "DROP"          # a metric fell by more than 1 point: fails D7 whatever the gain
    if up >= 0.5:
        return "PASS"
    return "same" if abs(up) < 0.05 else "<0.5"   # moved, but by less than the D7 bar


if __name__ == "__main__":
    main()
