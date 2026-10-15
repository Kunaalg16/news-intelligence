"""
p2_dev_knobs.py -- DEV TOOL (not part of the daily run). Knob search: tries other values for the rules' settings.

WHAT IT DOES
    The rules have numeric settings ("knobs", top of p2_extract_rules.py), e.g. CAPTION_ZONE = 30.
    This tool changes ONE knob at a time to each value in GRID below, scores all TUNE pages each time, and keeps a
    new value only if the score improves; then moves to the next knob; repeats until a full pass changes nothing.
    That method is called coordinate descent. It never edits the rules file: it prints what it would change.
    Score used: first, how many papers pass the 90% gate; then the average of each paper's worst metric.

WHY IT EXISTS
    To check whether better numbers would help. On 14 Oct it found a flat optimum (0.903 -> 0.908): the results
    hardly depend on the numbers. Only DECK_GAP_MAX 60 -> 40 (+0.4 points) was adopted; changes under ~0.3 points
    are treated as noise and NOT adopted, because 14 pages can't tell them apart from luck.

WHEN TO RUN
    After rule changes, and for the per-paper knob test (decided 15 Oct): only if a paper fails with the shared
    knobs, and only adopted if its own knobs beat the shared ones on its held-back page by more than noise.
    Takes about 5 minutes. TUNE pages only.

USAGE (from ~/news-intel)
    .venv/bin/python src/p2_dev_knobs.py
    .venv/bin/python src/p2_dev_knobs.py --paper "Business Line"     # search on one paper's tuning pages only
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import p2_extract_rules as R
import p2_dev_score

ROOT = Path(__file__).resolve().parent.parent
GRID = {
    "HEADLINE_RATIO": [1.25, 1.3, 1.35, 1.4, 1.5],
    "RULE_MIN_FRAC": [0.6, 0.7, 0.8, 0.9],
    "RULE_IGNORE_ZONE": [60, 90, 120],
    "CAPTION_ZONE": [20, 30, 40],
    "DECK_GAP_MAX": [30, 40, 60],
    "ADOPT_DIST": [15, 25, 40],
    "EXTRA_BODY_SHARE": [0.1, 0.15, 0.25],
    "PHOTO_FRAME": [40, 60, 80],
    "BRIEF_MIN_LINES": [3, 4, 6],
    "COLUMN_MERGE": [20, 30, 40],
}
NOISE = 0.003          # gains below 0.3 points of the objective are not adopted


def objective(root, papers, paper=None):
    out = p2_dev_score.run("TUNE", root, papers, verbose=False)
    pubs = [s for (n, p), s in out.items() if p == "ALL" and (paper is None or n == paper)]
    if not pubs:
        sys.exit(f"No tuning pages found{' for ' + paper if paper else ''}.")
    passing = sum(all(s[k] >= 0.9 for k in ("coverage", "precision", "recall")) for s in pubs)
    worst = sum(min(s["coverage"], s["precision"], s["recall"]) for s in pubs) / len(pubs)
    return passing, worst


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--paper", default=None, help='one paper only, e.g. "Business Line"')
    ap.add_argument("--root", default=str(ROOT))
    ap.add_argument("--papers", default=None, help="folder of PDFs (default: <root>/archive)")
    a = ap.parse_args()
    root = Path(a.root)
    start = {k: getattr(R, k) for k in GRID}
    best = objective(root, a.papers, a.paper)
    print(f"start {best[0]} passing, worst-metric average {best[1]:.4f}", flush=True)
    t0 = time.time()
    for sweep in range(2):
        improved = False
        for k, vals in GRID.items():
            cur = getattr(R, k)
            for v in vals:
                if v == cur:
                    continue
                setattr(R, k, v)
                sc = objective(root, a.papers, a.paper)
                if sc[0] > best[0] or (sc[0] == best[0] and sc[1] > best[1] + NOISE):
                    print(f"  {k}: {cur} -> {v}   {best[1]:.4f} -> {sc[1]:.4f} ({sc[0]} passing)", flush=True)
                    best, cur, improved = sc, v, True
            setattr(R, k, cur)
        print(f"sweep {sweep + 1} done ({time.time() - t0:.0f}s)", flush=True)
        if not improved:
            break
    changed = {k: (start[k], getattr(R, k)) for k in GRID if start[k] != getattr(R, k)}
    print("\nRESULT:", "no change worth adopting (flat optimum)" if not changed else
          "; ".join(f"{k} {a_} -> {b_}" for k, (a_, b_) in changed.items()))
    print("Nothing was edited. To adopt a change, edit the knob at the top of src/p2_extract_rules.py, "
          "then re-score and check the held-back page once.")


if __name__ == "__main__":
    main()
