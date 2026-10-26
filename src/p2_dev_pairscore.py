"""
p2_dev_pairscore.py -- 2.4 pair test, step 2: score every model x variant on the labelled pairs
  (v0.1, 25 Oct 2026 · v0.2, 25 Oct 2026: step 4 M3 route also needs a bootstrap range clear of zero
   · v0.3, 26 Oct 2026: --min-words N, the long-article subgroup check, exploratory, rule fixed before any number
   · v0.3.1, 26 Oct 2026: an opus_<set>.json without an "audit" key (opus_v1.json, labels only) is no longer read as a triage record)

WHAT IT DOES
  Reads the labelled pairs and the vectors, and runs the pre-registered protocol
  (drafts/2026-10-24_2.4_pair_test_protocol.md) in its order:
    0  sanity checks   >= 30 Same-event · random pairs (P5) labelled Unrelated and scored low · teaser <-> full-story
                       links (related_id) scored high · no model with near-constant scores · top-up audit (opus_*.json)
    1  label reliability   re-label agreement on Same event yes/no (needs >= 12/15)
    2  E2 per model    V1 vs V2 on M2 (paired bootstrap); tie -> V1; V0 / V1i reported, chosen only if clearly best
    3  local pick      smallest model whose M2 is tied with the best (range includes 0 or gap < 1 point)
    4  API check       voyage-4 vs the local pick, only if voyage vectors cover every pair (else: OPEN, parked)
                       v0.2 amendment (decided 25 Oct, after the local results, BEFORE any voyage result):
                       the M3 route (>= 5 points) also needs its paired-bootstrap range to exclude zero.
    6  report          M4 judge band per day, M5 follow-ups, V0 and V1i columns
  It prints a decision proposal; Kunaal decides; the decision is written into 04_DECISIONS.md by hand.

METRICS (scores = cosine similarity of the two texts' vectors)
  M1  ROC-AUC  Same event vs every other labelled pair
  M2  ROC-AUC  Same event vs Same subject, different event          (the main number)
  M3  recall of Same event at the highest line where >= 95% of pairs above it are Same event
  M4  same-day cross-paper pairs per day between the reject line (keeps >= 98% of Same-event pairs) and the
      M3 line (the band a judge would have to read)
  M5  ROC-AUC  Follow-up vs Same subject + Unrelated
  AUC = Mann-Whitney: the share of (positive, negative) pairings where the positive scores higher (ties count 1/2).
  Uncertainty: paired bootstrap, 1,000 resamples of the labelled pairs (the same resample for every model),
  95% range (2.5th-97.5th percentile) of each DIFFERENCE. Range includes 0 -> tie.

INPUTS (all read-only)
  data/pairs/pairs_<set>.jsonl · review/pairs/labels_<set>.json for each --sets (default v1,v1x)
  review/pairs/labels_v1_relabel.json (optional) · data/pairs/opus_<set>.json (optional audit record)
  data/vectors.db · data/news.db (related_id links, M4 day sizes)
  "Can't tell" labels and pairs without a label (set aside by the audit) are left out of every metric.

HOW TO RUN (from ~/news-intel)
  .venv-embed/bin/python src/p2_dev_pairscore.py                  full report
  Options: --sets v1,v1x · --boot 1000 · --seed 2025 · --ignore-audit (only if Kunaal decides so; logged in output)

LONG-ARTICLE SUBGROUP CHECK (v0.3, exploratory; STATUS A5, rule written 26 Oct before any number)
  .venv-embed/bin/python src/p2_dev_pairscore.py --min-words 400
  Keeps only pairs where at least one text has MORE than N body words (counted from texts.body in news.db,
  whitespace split), prints the label counts, the metric table and the V2 - V1 paired bootstrap on M2 for
  every model, then the verdict for qwen3-0.6b:
    V2 better by >= 2 points on M2 AND its range clear of 0 -> "consider V2 for long texts only"
    otherwise                                               -> "V1 stands"
  Steps 0, 1, 3, 4 and M4 are skipped (they belong to the full test). Small n: exploratory only, never a decision
  by itself. Without --min-words the script behaves exactly as v0.2.
"""
import argparse, json, random, sqlite3, sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

NEWS_DB, VEC_DB = Path("data") / "news.db", Path("data") / "vectors.db"
PAIRS_DIR, SHEET_DIR = Path("data") / "pairs", Path("review") / "pairs"
SIZE_ORDER = ["minilm", "bge-m3", "qwen3-0.6b"]        # protocol: smallest first
API = "voyage-4"
VARIANTS = ["V0", "V1", "V2", "V1i"]


def ro(path):
    if not path.exists():
        sys.exit(f"{path} not found. Run from ~/news-intel.")
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


# ---------- metrics ----------
def auc(pos, neg):
    """Mann-Whitney AUC; None if either side is empty."""
    pos, neg = np.asarray(pos, float), np.asarray(neg, float)
    if not len(pos) or not len(neg):
        return None
    allv = np.concatenate([pos, neg])
    order = allv.argsort(kind="mergesort")
    ranks = np.empty(len(allv))
    sv = allv[order]
    i = 0
    while i < len(sv):                                   # average ranks for ties
        j = i
        while j + 1 < len(sv) and sv[j + 1] == sv[i]:
            j += 1
        ranks[order[i:j + 1]] = (i + j) / 2 + 1
        i = j + 1
    rp = ranks[:len(pos)].sum()
    return (rp - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg))


def recall_at_precision(scores, is_pos, target=0.95):
    """Highest recall over lines t such that precision of {score >= t} >= target. Returns (recall, line)."""
    s, y = np.asarray(scores, float), np.asarray(is_pos, bool)
    if not y.any():
        return None, None
    best_r, best_t = 0.0, None
    for t in np.unique(s)[::-1]:
        above = s >= t
        prec = y[above].mean()
        if prec >= target:
            r = y[above].sum() / y.sum()
            if r >= best_r:
                best_r, best_t = r, t
    return best_r, best_t


def metrics(sc, lab):
    """sc: scores (array), lab: labels (array of str). Returns dict M1, M2, M3, M5 (+ lines)."""
    same = lab == "same"
    m1 = auc(sc[same], sc[~same])
    m2 = auc(sc[same], sc[lab == "subject"])
    m3, line = recall_at_precision(sc, same)
    m5 = auc(sc[lab == "follow"], sc[(lab == "subject") | (lab == "unrelated")])
    reject = float(np.quantile(sc[same], 0.02)) if same.any() else None
    return {"M1": m1, "M2": m2, "M3": m3, "M5": m5, "accept": line, "reject": reject}


def boot_diff(sa, sb, lab, metric, n, seed):
    """Paired bootstrap: same resampled pairs for A and B; returns (diff, lo, hi) in AUC points (x100)."""
    rng = np.random.default_rng(seed)
    base = metrics(sa, lab)[metric], metrics(sb, lab)[metric]
    if None in base:
        return None, None, None
    d = []
    for _ in range(n):
        idx = rng.integers(0, len(lab), len(lab))
        a, b = metrics(sa[idx], lab[idx])[metric], metrics(sb[idx], lab[idx])[metric]
        if a is not None and b is not None:
            d.append(a - b)
    d = np.array(d) * 100
    return (base[0] - base[1]) * 100, float(np.percentile(d, 2.5)), float(np.percentile(d, 97.5))


def tie(r):
    diff, lo, hi = r
    return lo <= 0 <= hi


def fmt(x, pct=True):
    return "  —  " if x is None else (f"{x * 100:5.1f}" if pct else f"{x:5.3f}")


def fr(r):
    d, lo, hi = r
    return "n/a" if d is None else f"{d:+5.1f} [{lo:+5.1f} … {hi:+5.1f}]" + ("  tie" if lo <= 0 <= hi else "")


# ---------- load ----------
def load_labels(sets):
    pairs, labels, audit_fail, notes = [], {}, [], []
    for s in sets:
        pf = PAIRS_DIR / f"pairs_{s}.jsonl"
        lf = SHEET_DIR / f"labels_{s}.json"
        if not pf.exists() or not lf.exists():
            sys.exit(f"missing {pf if not pf.exists() else lf}")
        lines = pf.read_text(encoding="utf-8").splitlines()
        ps = [json.loads(l) for l in lines[1:]]
        lab = json.loads(lf.read_text(encoding="utf-8"))["labels"]
        pairs += ps
        labels.update(lab)
        of = PAIRS_DIR / f"opus_{s}.json"
        rec = json.loads(of.read_text(encoding="utf-8")) if of.exists() else {}
        if "audit" in rec:  # v0.3.1: only a triage record (v1x) has an audit; opus_v1.json is labels only
            aud = rec["audit"]
            got = [q for q in aud if q in lab]
            hit = [q for q in got if lab[q] == "same"]
            notes.append(f"audit {s}: {len(got)}/{len(aud)} audit pairs labelled, Same event among them {len(hit)}; "
                         f"set aside unlabelled {len(rec['set_aside_unlabelled'])}")
            if hit or len(got) < len(aud):
                audit_fail.append(s)
            unl = [p["pair"] for p in ps if p["pair"] not in lab]
            notes.append(f"  {s}: {len(unl)} pairs without a human label (left out)")
        else:
            missing = [p["pair"] for p in ps if p["pair"] not in lab]
            if missing:
                sys.exit(f"{s}: {len(missing)} pairs unlabelled and no audit record; finish labelling first")
    return pairs, labels, audit_fail, notes


def load_vectors(vec, hashes):
    have = defaultdict(dict)
    hs = list(hashes)
    for m, v in vec.execute("SELECT DISTINCT model, variant FROM vectors"):
        rows = {}
        for i in range(0, len(hs), 500):
            chunk = hs[i:i + 500]
            q = f"SELECT text_hash, vec FROM vectors WHERE model=? AND variant=? AND text_hash IN ({','.join('?' * len(chunk))})"
            rows.update({h: np.frombuffer(b, dtype=np.float32) for h, b in vec.execute(q, (m, v, *chunk))})
        have[m][v] = rows
    return have


def related_links(news, run):
    """(teaser_hash, full_hash) from placements.extra related_id, same paper and date."""
    seat = {}
    rows = news.execute("SELECT date, publisher, art_id, text_hash, extra FROM placements WHERE run_id=?", (run,)).fetchall()
    for d, pub, aid, h, _ in rows:
        seat[(d, pub, aid)] = h
    out = []
    for d, pub, aid, h, ex in rows:
        try:
            rid = json.loads(ex or "{}").get("related_id")
        except ValueError:
            rid = None
        if rid and (d, pub, rid) in seat and seat[(d, pub, rid)] != h:
            out.append((h, seat[(d, pub, rid)]))
    return out


def body_words(news, hashes):
    """{text_hash: number of body words} from texts.body (whitespace split)."""
    hs, out = list(hashes), {}
    for i in range(0, len(hs), 500):
        chunk = hs[i:i + 500]
        q = f"SELECT text_hash, body FROM texts WHERE text_hash IN ({','.join('?' * len(chunk))})"
        out.update({h: len((b or "").split()) for h, b in news.execute(q, chunk)})
    return out


def subgroup_report(used, lab, S, cols, n_words, args):
    """Exploratory long-article check: metric table + V2 - V1 on M2 per model + rule verdict for Qwen."""
    cnt = Counter(lab)
    print(f"\n== LONG-ARTICLE SUBGROUP (exploratory) · pairs where at least one text has > {n_words} body words")
    print(f"  pairs {len(used)} · " + " · ".join(f"{k} {cnt[k]}" for k in ("same", "follow", "subject", "unrelated")))
    if cnt["same"] < 10 or cnt["subject"] < 10:
        print("  note  fewer than 10 Same-event or Same-subject pairs: ranges will be very wide")
    M = {c: metrics(S[c], lab) for c in cols}
    print(f"  {'model':11} {'var':4} {'M1':>6} {'M2':>6} {'M3':>6} {'M5':>6}")
    for c in cols:
        r = M[c]
        print(f"  {c[0]:11} {c[1]:4} {fmt(r['M1']):>6} {fmt(r['M2']):>6} {fmt(r['M3']):>6} {fmt(r['M5']):>6}")
    print("\n  V2 - V1 on M2 (paired bootstrap, points):")
    verdict = None
    for m in SIZE_ORDER + [API]:
        if (m, "V1") in S and (m, "V2") in S:
            r = boot_diff(S[(m, "V2")], S[(m, "V1")], lab, "M2", args.boot, args.seed)
            print(f"  {m:11} {fr(r)}")
            if m == "qwen3-0.6b":
                verdict = r
    print("\n  RULE (fixed 26 Oct, before any number): V2 for long texts only if >= 2 points better on M2 "
          "with range clear of 0")
    if verdict is None or verdict[0] is None:
        print("  qwen3-0.6b: cannot score (missing vectors or labels) -> V1 stands by default")
    elif verdict[0] >= 2 and verdict[1] > 0:
        print(f"  qwen3-0.6b: V2 - V1 {fr(verdict)} -> consider V2 for long texts only (exploratory; Kunaal decides)")
    else:
        print(f"  qwen3-0.6b: V2 - V1 {fr(verdict)} -> V1 stands (exploratory)")
    print("\nDone. Nothing was written. Log the result as exploratory (STATUS A5).")


def day_groups(news, run, keep):
    seat = {}
    for h, d, pub in news.execute("SELECT text_hash, date, publisher FROM placements WHERE run_id=? "
                                  "ORDER BY text_hash, date, publisher, art_id", (run,)):
        if h not in seat and h in keep:
            seat[h] = (d, pub)
    days = defaultdict(list)
    for h, (d, pub) in seat.items():
        days[d].append((h, pub))
    return days


def band_per_day(V, days, lo, hi):
    """Mean number of same-day cross-paper pairs with lo <= score < hi."""
    if lo is None or hi is None:
        return None
    counts = []
    for d, items in days.items():
        hs = [h for h, _ in items if h in V]
        pubs = np.array([p for h, p in items if h in V])
        if len(hs) < 2:
            continue
        M = np.stack([V[h] for h in hs])
        S = M @ M.T
        iu = np.triu_indices(len(hs), 1)
        cross = pubs[iu[0]] != pubs[iu[1]]
        s = S[iu][cross]
        counts.append(int(((s >= lo) & (s < hi)).sum()))
    return float(np.mean(counts)) if counts else None


# ---------- main ----------
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sets", default="v1,v1x")
    ap.add_argument("--run", default="exam-v1")
    ap.add_argument("--boot", type=int, default=1000)
    ap.add_argument("--seed", type=int, default=2025)
    ap.add_argument("--ignore-audit", action="store_true")
    ap.add_argument("--no-band", action="store_true", help="skip M4 (whole-archive similarity; slower)")
    ap.add_argument("--min-words", type=int, default=0,
                    help="v0.3: long-article subgroup check; keep pairs where a text has more than N body words")
    args = ap.parse_args()
    sets = args.sets.split(",")

    pairs, labels, audit_fail, notes = load_labels(sets)
    print(f"== 2.4 pair test scoring · sets {', '.join(sets)} · bootstrap {args.boot} · seed {args.seed}")
    for n in notes:
        print(n)
    if audit_fail and not args.ignore_audit:
        sys.exit(f"STOP (audit rule): {', '.join(audit_fail)} -- a set-aside sample held a Same-event pair or is not "
                 "fully labelled. Label the set-aside pairs first (rule fixed 25 Oct).")
    if audit_fail:
        print("!! --ignore-audit: audit rule overridden by Kunaal's decision (log it in 04_DECISIONS.md)")

    used = [p for p in pairs if p["pair"] in labels and labels[p["pair"]] != "cant"]
    lab = np.array([labels[p["pair"]] for p in used])
    cnt = Counter(lab)
    print(f"labelled pairs used: {len(used)} (Can't tell {sum(labels[p['pair']] == 'cant' for p in pairs if p['pair'] in labels)} left out) · "
          + " · ".join(f"{k} {cnt[k]}" for k in ("same", "follow", "subject", "unrelated")))

    vec, news = ro(VEC_DB), ro(NEWS_DB)
    hashes = {h for p in used for h in (p["a"], p["b"])}
    links = related_links(news, args.run)
    hashes |= {h for l in links for h in l}
    V = load_vectors(vec, hashes)
    cols = []                                            # (model, variant) with every pair covered
    for m in SIZE_ORDER + [API]:
        for v in VARIANTS:
            vv = V.get(m, {}).get(v, {})
            if vv and all(p["a"] in vv and p["b"] in vv for p in used):
                cols.append((m, v))
    if args.min_words:                                   # v0.3: subgroup check only, then stop
        bw = body_words(news, {h for p in used for h in (p["a"], p["b"])})
        keep = [i for i, p in enumerate(used) if max(bw.get(p["a"], 0), bw.get(p["b"], 0)) > args.min_words]
        used = [used[i] for i in keep]
        lab = lab[keep]
        S = {c: np.array([float(V[c[0]][c[1]][p["a"]] @ V[c[0]][c[1]][p["b"]]) for p in used]) for c in cols}
        subgroup_report(used, lab, S, cols, args.min_words, args)
        return
    S = {c: np.array([float(V[c[0]][c[1]][p["a"]] @ V[c[0]][c[1]][p["b"]]) for p in used]) for c in cols}
    skipped = [(m, v) for m in V for v in V[m] if (m, v) not in cols]
    print("columns: " + ", ".join(f"{m} {v}" for m, v in cols)
          + (f" · not covering every pair (skipped): {', '.join(f'{m} {v}' for m, v in skipped)}" if skipped else ""))

    # ---- step 0 ----
    print("\n-- step 0 · sanity")
    ok0 = True
    if cnt["same"] < 30:
        ok0 = False
        print(f"  FAIL  Same-event pairs {cnt['same']} < 30 (protocol: add P1/P2 pairs first)")
    else:
        print(f"  ok    Same-event pairs {cnt['same']} (>= 30)")
    p5 = [i for i, p in enumerate(used) if p["route"] == "P5"]
    if p5:
        un = sum(lab[i] == "unrelated" for i in p5)
        print(f"  {'ok  ' if un >= 0.8 * len(p5) else 'CHECK'}  random pairs (P5) labelled Unrelated: {un}/{len(p5)}")
        for c in cols:
            sc = S[c]
            pct = np.mean([(sc < sc[i]).mean() for i in p5]) * 100
            if pct > 50:
                print(f"  CHECK {c[0]} {c[1]}: random pairs sit at the {pct:.0f}th percentile of scores (expected low)")
    for c in cols:
        sd = S[c].std()
        if sd < 1e-3:
            ok0 = False
            print(f"  FAIL  {c[0]} {c[1]}: near-constant scores (sd {sd:.5f}); broken load?")
    if links:
        worst = []
        for c in cols:
            vv = V[c[0]][c[1]]
            ls = [float(vv[a] @ vv[b]) for a, b in links if a in vv and b in vv]
            if ls:
                pct = np.mean([(S[c] < x).mean() for x in ls]) * 100
                worst.append((pct, c, len(ls)))
        for pct, c, n in sorted(worst)[:3]:
            print(f"  {'ok  ' if pct >= 70 else 'CHECK'}  teaser<->full links ({n}) in {c[0]} {c[1]}: average at the "
                  f"{pct:.0f}th percentile of labelled-pair scores (lowest 3 models shown)")
    else:
        print("  note  no related_id links found in placements.extra")
    if not ok0:
        sys.exit("STOP at step 0: fix the failures above before reading any score.")

    # ---- step 1 ----
    print("\n-- step 1 · label reliability")
    rf = SHEET_DIR / "labels_v1_relabel.json"
    if rf.exists():
        re_ = json.loads(rf.read_text(encoding="utf-8"))["labels"]
        both = [q for q in re_ if q in labels]
        agree = sum((re_[q] == "same") == (labels[q] == "same") for q in both)
        five = sum(re_[q] == labels[q] for q in both)
        print(f"  Same event yes/no: {agree}/{len(both)} agree · all five labels: {five}/{len(both)}"
              + ("" if agree >= 12 else "   <-- below 12/15: review label definitions before choosing"))
    else:
        print("  OPEN  labels_v1_relabel.json not found (re-label not done yet); results below are provisional")

    # ---- metric table ----
    print("\n-- metrics (AUC x100; M3 = recall at 95% precision, %)")
    print(f"  {'model':11} {'var':4} {'M1':>6} {'M2':>6} {'M3':>6} {'M5':>6}")
    M = {c: metrics(S[c], lab) for c in cols}
    for c in cols:
        r = M[c]
        print(f"  {c[0]:11} {c[1]:4} {fmt(r['M1']):>6} {fmt(r['M2']):>6} {fmt(r['M3']):>6} {fmt(r['M5']):>6}")
    B = lambda a, b, met: boot_diff(S[a], S[b], lab, met, args.boot, args.seed)

    # ---- step 2 ----
    print("\n-- step 2 · input text per model (M2, paired bootstrap, A - B in points)")
    chosen = {}
    for m in SIZE_ORDER + [API]:
        if (m, "V1") not in S:
            continue
        pick = "V1"
        if (m, "V2") in S:
            r = B((m, "V2"), (m, "V1"), "M2")
            print(f"  {m:11} V2 - V1  {fr(r)}")
            if r[0] is not None and not tie(r) and r[0] > 0:
                pick = "V2"
        for extra in ("V0", "V1i"):
            if (m, extra) in S:
                others = [x for x in ("V1", "V2") if (m, x) in S]
                rs = [B((m, extra), (m, x), "M2") for x in others]
                for x, r in zip(others, rs):
                    print(f"  {m:11} {extra} - {x}  {fr(r)}   (reported)")
                if rs and all(r[0] is not None and not tie(r) and r[0] > 0 for r in rs):
                    pick = extra
                    print(f"  {m:11} {extra} beats both clearly -> chosen")
        chosen[m] = pick
        print(f"  {m:11} -> {pick}")

    # ---- step 3 ----
    print("\n-- step 3 · local pick (M2)")
    local = [m for m in SIZE_ORDER if m in chosen]
    if not local:
        sys.exit("no local model with vectors for every pair")
    best = max(local, key=lambda m: M[(m, chosen[m])]["M2"])
    print(f"  best on M2: {best} {chosen[best]} ({fmt(M[(best, chosen[best])]['M2'])})")
    tied = []
    for m in local:
        if m == best:
            tied.append(m)
            continue
        r = B((m, chosen[m]), (best, chosen[best]), "M2")
        t = tie(r) or abs(r[0]) < 1
        print(f"  {m:11} - {best}: {fr(r)}" + ("  -> tied with best" if t else ""))
        if t:
            tied.append(m)
    pick = min(tied, key=SIZE_ORDER.index)
    big = [m for m in tied if m in ("bge-m3", "qwen3-0.6b")]
    if pick != "minilm" and len(big) == 2:                # both large tied: higher M3, then BGE-M3
        a, b = (M[(x, chosen[x])]["M3"] or 0 for x in ("bge-m3", "qwen3-0.6b"))
        pick = "qwen3-0.6b" if b > a else "bge-m3"
    print(f"  LOCAL PICK (proposal): {pick} {chosen[pick]}"
          + ("   (MiniLM picked: the opening of an article identifies the event; note for E2/G1)" if pick == "minilm" else ""))

    # ---- step 4 ----
    print("\n-- step 4 · API check")
    if API in chosen:
        lp = (pick, chosen[pick]); vp = (API, chosen[API])
        r1, r2 = B(vp, lp, "M1"), B(vp, lp, "M2")
        r3 = B(vp, lp, "M3")
        win = any(r[0] is not None and r[0] >= 2 and not tie(r) for r in (r1, r2)) \
            or (r3[0] is not None and r3[0] >= 5 and not tie(r3))  # v0.2: M3 route needs range clear of 0
        print(f"  voyage - local  M1 {fr(r1)} · M2 {fr(r2)} · M3 {fr(r3)} -> "
              + ("VOYAGE WINS: go to step 5 (escalation)" if win else "local pick is final"))
    else:
        print("  OPEN  no voyage-4 vectors for every pair (Voyage parked 25 Oct). Scoring without it is a deviation:"
              " Kunaal decides, logged in 04_DECISIONS.md.")

    # ---- step 6 ----
    print("\n-- step 6 · report only")
    if not args.no_band:
        keep = {h for m in V for v in V[m] for h in V[m][v]}
        allh = {h for (h,) in vec.execute("SELECT DISTINCT text_hash FROM vectors")}
        days = day_groups(news, args.run, allh)
        for m in local:
            c = (m, chosen[m])
            full = {h: np.frombuffer(b, dtype=np.float32) for h, b in
                    vec.execute("SELECT text_hash, vec FROM vectors WHERE model=? AND variant=?", c)}
            lo, hi = M[c]["reject"], M[c]["accept"]
            b = band_per_day(full, days, lo, hi)
            print(f"  M4 {m:11} {c[1]}: reject line {fmt(lo, False)} · accept line {fmt(hi, False)} · "
                  f"pairs per day in between {'—' if b is None else f'{b:.0f}'} (a judge reads these)")
    print("  M5 follow-ups: see the metric table (" + str(cnt["follow"]) + " follow-up pairs; small, report only)")
    print("\nDone. Nothing was written. Record the decision (step 7) in 04_DECISIONS.md.")


if __name__ == "__main__":
    main()
