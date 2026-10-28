"""p3_group.py — Phase 3 · Story grouping v0: stage 1 (same-day groups) + stage 2 (cross-day threads).
  v0.1 26 Oct 2026 (B·2) · v0.2 26 Oct: paid predicate for `check` set from real tag values
  v0.3 28 Oct (B·3): stage 2 `track` (t0.1) + `threads`; `check` reports stage 2; intel.db schema 2

WHAT IT DOES
  For each date, finds which articles (across all papers) report the same story.
  It scores every pair of that day's texts by vector similarity (Qwen3-0.6B, V1),
  links pairs at or above the LINK line, never links pairs below the NEVER line,
  and stores the pairs in between ("middle band") for a later Haiku judge.
  Groups = connected components over the links (single linkage, union-find).
  Every text ends up in exactly one group; a lone article is a group of one.

WHY
  The digest and search need "stories", not loose articles. Stage 1 (detection,
  same day) is the base; stage 2 (tracking) links each day-group to its best
  earlier day-group within WINDOW days, so stories become threads across days.
  Design of record: drafts/2026-10-26_B1_grouping_design.md (B·1) §2.1–2.3, as kept
  by drafts/2026-10-28_S6_closeout.md (D2 backbone, D3 single linkage, D4 stage-2
  settings provisional until cross-day labels exist).

STAGE 2 (track)
  For each built date D, every group of D is scored against every group of the
  built dates 1..WINDOW calendar days earlier. Three group-to-group scores are
  stored for each candidate: average (mean of all article-pair scores = dot of the
  raw centroids), best (highest single article pair), centroid (dot of the
  re-normalised centroids). The DECIDING score (knob "linkage") ≥ link line →
  the best candidate becomes the PARENT link (tie → more recent date, then group id);
  other candidates above the line are SIDE links ("also related", not threading);
  the best few under the line are stored as BELOW (for the below-the-line skim).
  Threads = trees over parent links; thread id = the root group, computed on read.
  Links are untyped ("earlier coverage") until the judge types them.
  A date is rebuilt when it is new, when the set of built dates in its window
  changed (a late date arrived), or when a date in its window was rebuilt in stage 1.

DATA (one writer per file)
  reads   data/news.db     (read-only)  texts of run exam-v1, tags + entities v1.3
  reads   data/vectors.db  (read-only)  model qwen3-0.6b, variant V1 (L2-normalised float32)
  writes  data/intel.db                 stage 1: group_runs · groups · members · pairs
                                        stage 2: track_runs · links      (+ meta)
  A re-run of a date replaces that date's rows for this version, in one
  transaction (idempotent). Group IDs are content-derived: <date>:<first 12 chars
  of the smallest member text_hash>; the full hash is stored as anchor_hash.

HOW TO RUN (from ~/news-intel, pipeline environment)
  .venv/bin/python src/p3_group.py run                 build dates not yet built
  .venv/bin/python src/p3_group.py run --all           rebuild every stored date
  .venv/bin/python src/p3_group.py run --date 2026-10-24 [--date ...]
  .venv/bin/python src/p3_group.py run --dry           compute and print, write nothing
  .venv/bin/python src/p3_group.py track               stage 2: dates new or stale since the last track
  .venv/bin/python src/p3_group.py track --all | --date D [--date ...] | --dry
  .venv/bin/python src/p3_group.py check [--date D]    stage 1 counts + flags; stage 2 summary
  .venv/bin/python src/p3_group.py show 2026-10-24 [--min-size 2] [--limit 30]
  .venv/bin/python src/p3_group.py threads [--min-span 15] [--limit 20]   long threads, one headline per day
  Daily: `run`, then `track`.

KNOBS
  Every setting chosen without strong evidence is in the KNOBS / TRACK_KNOBS blocks
  below, with its reason and the measurement that would change it (mirrored in
  03_ARCHITECTURE.md). Stage-1 knob change → bump GROUPING_VERSION; stage-2 knob
  change → bump TRACKING_VERSION only (stage 1 is not rebuilt).
"""

import argparse
import json
import sqlite3
import sys
from datetime import date as Date, datetime, timezone
from pathlib import Path

import numpy as np

# ─────────────────────────────── KNOBS ────────────────────────────────
# Change a knob → bump GROUPING_VERSION, so old and new groupings can sit side by side.
GROUPING_VERSION = "g0.1"

KNOBS = {
    # Pair score ≥ this → link. From 2.4 M4 (Kunaal's pair labels, 25 Oct).
    # Changed by: group skim; chain flags.
    "link_threshold": 0.810,
    # Pair score < this → never linked. From 2.4 M4. Changed by: chain flags.
    "never_threshold": 0.496,
    # Links between two articles of the same paper allowed (teaser + story, sidebars,
    # Mint features split at subheadings). Changed by: same-paper-only groups with chain flags.
    "same_paper_links": True,
}
# PLANNED knob, not in g0.1: "paid items may not bridge two editorial articles".
# Today paid items bridge freely; added only if `check` shows paid bridges (B·1 §2.1 item 6).

# Inputs (fixed by decisions, not knobs).
RUN_ID = "exam-v1"
VEC_MODEL, VEC_VARIANT = "qwen3-0.6b", "V1"
TAG_MODEL, TAG_PROMPT = "claude-haiku-5-5", "v1.3"

# Provisional paid predicate for `check` only (schema-on-read; never stored).
# The final rule lives in the digest's read-time labelling function; when it exists,
# `check` should import it instead (single source of truth).
# Values from a read-only query of tags v1.3 (26 Oct, B·2): kind 'sponsored' = 104 texts;
# sponsored_cue values: in association with 11 · marketing initiative 5 · advertorial 2 ·
# marketing feature 1 · sponsored content 1. Chat 13 dropped "in association with" from the cue rule.
PAID_KINDS = ("sponsored",)
DROPPED_CUES = ("in association with",)

# ──────────────────────────── STAGE 2 KNOBS ───────────────────────────
# Provisional (S6 D4): set from B·1, replaced by a rule learned from cross-day labels.
# Change a knob → bump TRACKING_VERSION (stage-1 groups are untouched).
TRACKING_VERSION = "t0.1"

TRACK_KNOBS = {
    # Look back this many calendar days for an earlier day-group = the longest GAP
    # between two consecutive appearances (a thread itself can run longer). B·1 / EMM.
    # Changed by: gap distribution (accepted links piling up at 6–7 days → try 10).
    "window_days": 7,
    # Deciding score ≥ this → link. B·1 start 0.810 (same scale as article pairs).
    # S6 D4: story-sized groups mix events, so averages run low → expect under-linking.
    # Changed by: blind sheet (≥ 8 of 20 below-line pairs story/follow-up → too strict).
    "link_threshold": 0.810,
    # Which stored score decides: "average" | "best" | "centroid" (all three stored).
    # Changed by: blind sheet + stored scores (no re-scoring needed to compare).
    "linkage": "average",
    # Below-the-line candidates kept per group (for the skim), best first, only those
    # at or above the floor. Changed by: the skim needing more or fewer.
    "below_keep": 3,
    "below_floor": 0.496,
}
# Time decay: OFF in t0.1 (B·1). Changed by: links at 5–7 days much worse than at 1–2 days.
# Pass lines (written 28 Oct, before any stage-2 result; Kunaal agreed):
#   precision  ≥ 18 of 20 accepted parent links = "same story continuing" or "follow-up"
#   too strict if ≥ 8 of 20 just-below-line candidates = story or follow-up
#   S6 D2 trigger: > 10% of linked groups have a side link into a different thread
CONVERGENCE_TRIGGER = 0.10

NEWS_DB = Path("data/news.db")
VECTORS_DB = Path("data/vectors.db")
INTEL_DB = Path("data/intel.db")
INTEL_SCHEMA_VERSION = 2          # 1 → 2 (v0.3): + track_runs, links (additive)

# ───────────────────────────── database ───────────────────────────────

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);

-- one row per date × grouping version: what went in, which knobs, what came out
CREATE TABLE IF NOT EXISTS group_runs (
    version TEXT NOT NULL, date TEXT NOT NULL,
    run_id TEXT, vec_model TEXT, vec_variant TEXT, tag_model TEXT, tag_prompt TEXT,
    knobs TEXT,                       -- JSON of KNOBS at build time
    texts INTEGER, missing_vectors INTEGER, groups INTEGER, multi_groups INTEGER,
    link_pairs INTEGER, middle_pairs INTEGER, built_at TEXT,
    PRIMARY KEY (version, date)
);

-- one row per group, singletons included
CREATE TABLE IF NOT EXISTS groups (
    version TEXT NOT NULL, date TEXT NOT NULL, group_id TEXT NOT NULL,
    anchor_hash TEXT NOT NULL,        -- full text_hash the ID comes from (stable pointer into news.db)
    size INTEGER, papers INTEGER,     -- members; distinct publishers
    same_paper_only INTEGER,          -- 1 = size ≥ 2 and every member from one paper
    min_score REAL,                   -- lowest member-pair score inside the group (NULL if size 1)
    chain_pairs INTEGER,              -- member pairs scoring < never_threshold (chaining flag)
    PRIMARY KEY (version, date, group_id)
);

CREATE TABLE IF NOT EXISTS members (
    version TEXT NOT NULL, date TEXT NOT NULL, group_id TEXT NOT NULL, text_hash TEXT NOT NULL,
    PRIMARY KEY (version, date, text_hash)
);
CREATE INDEX IF NOT EXISTS members_group ON members (version, date, group_id);
CREATE INDEX IF NOT EXISTS members_hash ON members (text_hash);

-- link and middle-band pairs only (below never_threshold is not stored); a < b
CREATE TABLE IF NOT EXISTS pairs (
    version TEXT NOT NULL, date TEXT NOT NULL, a TEXT NOT NULL, b TEXT NOT NULL,
    score REAL, band TEXT,            -- 'link' | 'middle'
    shared_entities INTEGER,          -- distinct lower-case canonical names in both (v1.3)
    same_paper INTEGER,               -- 1 = the two texts share a publisher that day
    used INTEGER,                     -- 1 = this pair joined the union (link, not blocked by a knob)
    PRIMARY KEY (version, date, a, b)
);

-- stage 2: one row per date × grouping version × tracking version
CREATE TABLE IF NOT EXISTS track_runs (
    gversion TEXT NOT NULL, tversion TEXT NOT NULL, date TEXT NOT NULL,
    knobs TEXT,                       -- JSON of TRACK_KNOBS at build time
    window_dates TEXT,                -- JSON list of the built dates it looked back over
    groups INTEGER, parents INTEGER, sides INTEGER, below INTEGER, built_at TEXT,
    PRIMARY KEY (gversion, tversion, date)
);

-- stage 2: candidate links from a group of `date` to a group of an earlier date
CREATE TABLE IF NOT EXISTS links (
    gversion TEXT NOT NULL, tversion TEXT NOT NULL, date TEXT NOT NULL, group_id TEXT NOT NULL,
    earlier_date TEXT NOT NULL, earlier_group_id TEXT NOT NULL,
    gap_days INTEGER,
    avg_score REAL, best_score REAL, centroid_score REAL,
    role TEXT,                        -- 'parent' (threads) | 'side' (also related) | 'below' (skim only)
    rank INTEGER,                     -- 1 = best candidate of this group
    PRIMARY KEY (gversion, tversion, date, group_id, earlier_group_id)
);
CREATE INDEX IF NOT EXISTS links_earlier ON links (gversion, tversion, earlier_group_id);
"""


def open_ro(path):
    """Open a database read-only (the file cannot be written through this connection)."""
    if not path.exists():
        sys.exit(f"missing {path} (run from ~/news-intel)")
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def open_intel(path=INTEL_DB):
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    row = con.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    if row is None:
        con.execute("INSERT INTO meta VALUES ('schema_version', ?)", (str(INTEL_SCHEMA_VERSION),))
        con.commit()
    elif int(row[0]) == 1 and INTEL_SCHEMA_VERSION == 2:
        # additive upgrade: executescript above already created the two new tables
        con.execute("UPDATE meta SET value='2' WHERE key='schema_version'")
        con.commit()
        print("intel.db: schema 1 → 2 (added track_runs, links; stage-1 tables untouched)")
    elif int(row[0]) != INTEL_SCHEMA_VERSION:
        sys.exit(f"intel.db schema {row[0]} ≠ code {INTEL_SCHEMA_VERSION}; stop")
    return con


# ───────────────────────────── loading ────────────────────────────────

def stored_dates(news):
    return [r[0] for r in news.execute(
        "SELECT DISTINCT date FROM placements WHERE run_id=? ORDER BY date", (RUN_ID,))]


def load_day(news, vecs, date):
    """Texts of one date with publishers, vectors and entity sets.

    Returns (hashes, publishers{hash: set}, matrix[n×d] for hashes with vectors,
    vec_hashes, entities{hash: set}). Hashes are sorted, so order is deterministic.
    """
    pubs = {}
    for h, p in news.execute(
            "SELECT text_hash, publisher FROM placements WHERE run_id=? AND date=?", (RUN_ID, date)):
        pubs.setdefault(h, set()).add(p)
    hashes = sorted(pubs)

    vec = {}
    for i in range(0, len(hashes), 500):                       # stay under SQLite's variable limit
        chunk = hashes[i:i + 500]
        q = ",".join("?" * len(chunk))
        for h, b in vecs.execute(
                f"SELECT text_hash, vec FROM vectors WHERE model=? AND variant=? AND text_hash IN ({q})",
                (VEC_MODEL, VEC_VARIANT, *chunk)):
            vec[h] = np.frombuffer(b, dtype=np.float32)
    vec_hashes = [h for h in hashes if h in vec]
    matrix = np.vstack([vec[h] for h in vec_hashes]) if vec_hashes else np.zeros((0, 0), np.float32)

    ents = {h: set() for h in hashes}
    for i in range(0, len(hashes), 500):
        chunk = hashes[i:i + 500]
        q = ",".join("?" * len(chunk))
        for h, c in news.execute(
                f"SELECT text_hash, lower(trim(COALESCE(canonical, name))) FROM entities "
                f"WHERE model=? AND prompt_version=? AND text_hash IN ({q})",
                (TAG_MODEL, TAG_PROMPT, *chunk)):
            if c:
                ents[h].add(c)
    return hashes, pubs, matrix, vec_hashes, ents


# ───────────────────────────── grouping ───────────────────────────────

class UnionFind:
    """Disjoint sets: find(x) gives a set's representative; union(a, b) merges two sets."""

    def __init__(self, items):
        self.parent = {x: x for x in items}

    def find(self, x):
        root = x
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[x] != root:                          # path compression
            self.parent[x], x = root, self.parent[x]
        return root

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[max(ra, rb)] = min(ra, rb)             # deterministic root


def group_day(date, hashes, pubs, matrix, vec_hashes, ents, knobs=KNOBS):
    """Pure function: one day's inputs → groups and stored pairs (no database access)."""
    link_t, never_t = knobs["link_threshold"], knobs["never_threshold"]
    n = len(vec_hashes)
    scores = matrix @ matrix.T if n else np.zeros((0, 0), np.float32)   # unit vectors: dot = cosine

    uf = UnionFind(hashes)
    pairs = []
    if n:
        iu, ju = np.triu_indices(n, k=1)
        s = scores[iu, ju]
        keep = s >= never_t
        for i, j, sc in zip(iu[keep].tolist(), ju[keep].tolist(), s[keep].tolist()):
            a, b = vec_hashes[i], vec_hashes[j]                # vec_hashes sorted → a < b
            band = "link" if sc >= link_t else "middle"
            same = 1 if pubs[a] & pubs[b] else 0
            used = 1 if band == "link" and (knobs["same_paper_links"] or not same) else 0
            if used:
                uf.union(a, b)
            pairs.append((a, b, round(float(sc), 6), band, len(ents[a] & ents[b]), same, used))

    comps = {}
    for h in hashes:
        comps.setdefault(uf.find(h), []).append(h)

    index = {h: k for k, h in enumerate(vec_hashes)}
    groups = []
    for members in comps.values():
        members.sort()
        anchor = members[0]
        papers = set().union(*(pubs[m] for m in members))
        idx = [index[m] for m in members if m in index]
        if len(idx) >= 2:
            sub = scores[np.ix_(idx, idx)][np.triu_indices(len(idx), k=1)]
            min_score, chain = round(float(sub.min()), 6), int((sub < never_t).sum())
        else:
            min_score, chain = None, 0
        groups.append({
            "group_id": f"{date}:{anchor[:12]}", "anchor_hash": anchor, "members": members,
            "size": len(members), "papers": len(papers),
            "same_paper_only": int(len(members) >= 2 and len(papers) == 1),
            "min_score": min_score, "chain_pairs": chain,
        })
    groups.sort(key=lambda g: (-g["size"], g["group_id"]))
    return groups, pairs


def write_day(intel, date, n_texts, n_missing, groups, pairs, version=GROUPING_VERSION, knobs=KNOBS):
    """Replace one date × version in a single transaction (all or nothing)."""
    with intel:
        for t in ("pairs", "members", "groups", "group_runs"):
            intel.execute(f"DELETE FROM {t} WHERE version=? AND date=?", (version, date))
        intel.executemany(
            "INSERT INTO groups VALUES (?,?,?,?,?,?,?,?,?)",
            [(version, date, g["group_id"], g["anchor_hash"], g["size"], g["papers"],
              g["same_paper_only"], g["min_score"], g["chain_pairs"]) for g in groups])
        intel.executemany(
            "INSERT INTO members VALUES (?,?,?,?)",
            [(version, date, g["group_id"], m) for g in groups for m in g["members"]])
        intel.executemany(
            "INSERT INTO pairs VALUES (?,?,?,?,?,?,?,?,?)",
            [(version, date, *p) for p in pairs])
        intel.execute(
            "INSERT INTO group_runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (version, date, RUN_ID, VEC_MODEL, VEC_VARIANT, TAG_MODEL, TAG_PROMPT,
             json.dumps(knobs, sort_keys=True), n_texts, n_missing, len(groups),
             sum(1 for g in groups if g["size"] >= 2),
             sum(1 for p in pairs if p[3] == "link"), sum(1 for p in pairs if p[3] == "middle"),
             datetime.now(timezone.utc).isoformat(timespec="seconds")))


# ───────────────────────────── stage 2 ────────────────────────────────

def gap_days(earlier, later):
    return (Date.fromisoformat(later) - Date.fromisoformat(earlier)).days


def window_of(date, built_dates, window):
    """Built dates 1..window calendar days before `date`, oldest first."""
    return [e for e in built_dates if 1 <= gap_days(e, date) <= window]


def load_groups(intel, vecs, date, version=GROUPING_VERSION):
    """One date's stage-1 groups as [(group_id, matrix k×d)], groups with ≥ 1 vector only."""
    members = {}
    for gid, h in intel.execute(
            "SELECT group_id, text_hash FROM members WHERE version=? AND date=? ORDER BY group_id, text_hash",
            (version, date)):
        members.setdefault(gid, []).append(h)
    hashes = sorted({h for ms in members.values() for h in ms})
    vec = {}
    for i in range(0, len(hashes), 500):
        chunk = hashes[i:i + 500]
        q = ",".join("?" * len(chunk))
        for h, b in vecs.execute(
                f"SELECT text_hash, vec FROM vectors WHERE model=? AND variant=? AND text_hash IN ({q})",
                (VEC_MODEL, VEC_VARIANT, *chunk)):
            vec[h] = np.frombuffer(b, dtype=np.float32)
    out = []
    for gid in sorted(members):
        rows = [vec[h] for h in members[gid] if h in vec]
        if rows:
            out.append((gid, np.vstack(rows)))
    return out


def track_day(date, today, earlier, knobs=TRACK_KNOBS):
    """Pure function: one date's groups vs earlier groups → candidate links (no database access).

    today:   [(group_id, matrix)]            groups of `date`
    earlier: [(earlier_date, group_id, matrix)]  groups of the window dates
    Returns [(group_id, earlier_date, earlier_group_id, gap, avg, best, centroid, role, rank)].
    """
    if not today or not earlier:
        return []
    link_t, floor, keep = knobs["link_threshold"], knobs["below_floor"], knobs["below_keep"]
    T = np.vstack([m for _, m in today])
    E = np.vstack([m for _, _, m in earlier])
    nt = np.array([len(m) for _, m in today])
    ne = np.array([len(m) for _, _, m in earlier])
    st = np.concatenate(([0], np.cumsum(nt)[:-1]))
    se = np.concatenate(([0], np.cumsum(ne)[:-1]))

    S = (T @ E.T).astype(np.float64)                              # article × article cosine
    sums = np.add.reduceat(np.add.reduceat(S, st, axis=0), se, axis=1)
    avg = sums / np.outer(nt, ne)                                  # average linkage
    best = np.maximum.reduceat(np.maximum.reduceat(S, st, axis=0), se, axis=1)
    ct = np.add.reduceat(T.astype(np.float64), st, axis=0)
    ce = np.add.reduceat(E.astype(np.float64), se, axis=0)
    ct /= np.linalg.norm(ct, axis=1, keepdims=True)
    ce /= np.linalg.norm(ce, axis=1, keepdims=True)
    cen = ct @ ce.T                                                # centroid linkage
    decide = {"average": avg, "best": best, "centroid": cen}[knobs["linkage"]]

    gaps = [gap_days(ed, date) for ed, _, _ in earlier]
    rows = []
    for i, (gid, _) in enumerate(today):
        d = decide[i]
        cand = np.flatnonzero(d >= min(floor, link_t)).tolist()   # only these can become any link
        # order: deciding score desc, then the more recent date (smaller gap), then group id
        order = sorted(cand, key=lambda j: (-round(float(d[j]), 6), gaps[j], earlier[j][1]))
        rank, below = 0, 0
        for j in order:
            sc = float(d[j])
            if sc >= link_t:
                role = "parent" if rank == 0 else "side"
            elif sc >= floor and below < keep:
                role, below = "below", below + 1
            else:
                break                                              # sorted: nothing further qualifies
            rank += 1
            ed, egid, _ = earlier[j]
            rows.append((gid, ed, egid, gaps[j], round(float(avg[i, j]), 6), round(float(best[i, j]), 6),
                         round(float(cen[i, j]), 6), role, rank))
    return rows


def write_track(intel, date, window, n_groups, rows):
    """Replace one date × (grouping, tracking) version in a single transaction."""
    g, t = GROUPING_VERSION, TRACKING_VERSION
    with intel:
        intel.execute("DELETE FROM links WHERE gversion=? AND tversion=? AND date=?", (g, t, date))
        intel.execute("DELETE FROM track_runs WHERE gversion=? AND tversion=? AND date=?", (g, t, date))
        intel.executemany("INSERT INTO links VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                          [(g, t, date, *r) for r in rows])
        count = lambda role: sum(1 for r in rows if r[7] == role)
        intel.execute(
            "INSERT INTO track_runs VALUES (?,?,?,?,?,?,?,?,?,?)",
            (g, t, date, json.dumps(TRACK_KNOBS, sort_keys=True), json.dumps(window), n_groups,
             count("parent"), count("side"), count("below"),
             datetime.now(timezone.utc).isoformat(timespec="microseconds")))


def stale_dates(intel, built, window):
    """Dates whose stage-2 rows are missing or out of date (make-style dependency check)."""
    done = {d: (json.loads(w), b) for d, w, b in intel.execute(
        "SELECT date, window_dates, built_at FROM track_runs WHERE gversion=? AND tversion=?",
        (GROUPING_VERSION, TRACKING_VERSION))}
    dates = sorted(built)
    todo = []
    for d in dates:
        win = window_of(d, dates, window)
        if d not in done:
            todo.append(d)
        elif done[d][0] != win:                                    # a late date arrived (or left)
            todo.append(d)
        elif max(built[x] for x in win + [d]) > done[d][1]:         # stage 1 rebuilt since
            todo.append(d)
    return todo


def thread_roots(intel):
    """parent map and root of every group that has a parent link (threads = trees)."""
    parent = dict(intel.execute(
        "SELECT group_id, earlier_group_id FROM links WHERE gversion=? AND tversion=? AND role='parent'",
        (GROUPING_VERSION, TRACKING_VERSION)).fetchall())
    root = {}

    def find(x):
        path = []
        while x not in root and x in parent:
            path.append(x)
            x = parent[x]
        r = root.get(x, x)
        for p in path:
            root[p] = r
        return r

    for g in list(parent):
        find(g)
    return parent, root


# ───────────────────────────── commands ───────────────────────────────

def cmd_run(args):
    news, vecs = open_ro(NEWS_DB), open_ro(VECTORS_DB)
    intel = None if args.dry else open_intel()
    dates = stored_dates(news)
    if args.date:
        bad = [d for d in args.date if d not in dates]
        if bad:
            sys.exit(f"not stored in run {RUN_ID}: {', '.join(bad)}")
        todo = args.date
    elif args.all or args.dry:
        todo = dates
    else:
        built = {r[0] for r in intel.execute(
            "SELECT date FROM group_runs WHERE version=?", (GROUPING_VERSION,))}
        todo = [d for d in dates if d not in built]
    if not todo:
        print(f"{GROUPING_VERSION}: every stored date is built ({len(dates)} dates). Nothing to do.")
        return

    print(f"{GROUPING_VERSION} | {'DRY, nothing written' if args.dry else 'writing data/intel.db'} | "
          f"{len(todo)} date(s) | link ≥ {KNOBS['link_threshold']} · never < {KNOBS['never_threshold']}")
    tot = dict(texts=0, missing=0, groups=0, multi=0, link=0, middle=0, chain=0)
    for d in todo:
        hashes, pubs, matrix, vh, ents = load_day(news, vecs, d)
        groups, pairs = group_day(d, hashes, pubs, matrix, vh, ents)
        missing = len(hashes) - len(vh)
        if not args.dry:
            write_day(intel, d, len(hashes), missing, groups, pairs)
        multi = [g for g in groups if g["size"] >= 2]
        link = sum(1 for p in pairs if p[3] == "link")
        middle = len(pairs) - link
        chain = sum(1 for g in groups if g["chain_pairs"])
        print(f"  {d}  texts {len(hashes):4d}  groups {len(groups):4d}  multi {len(multi):3d}  "
              f"largest {groups[0]['size'] if groups else 0:3d}  link {link:4d}  middle {middle:4d}"
              + (f"  NO-VECTOR {missing}" if missing else "") + (f"  CHAIN {chain}" if chain else ""))
        for k, v in zip(tot, (len(hashes), missing, len(groups), len(multi), link, middle, chain)):
            tot[k] += v
    print(f"  total     texts {tot['texts']}  groups {tot['groups']}  multi {tot['multi']}  "
          f"link {tot['link']}  middle {tot['middle']} (≈ {tot['middle'] / len(todo):.0f}/day)  "
          f"no-vector {tot['missing']}  chain-flag groups {tot['chain']}")


def cmd_track(args):
    vecs, intel = open_ro(VECTORS_DB), (None if args.dry else open_intel())
    con = intel or open_intel_ro()
    built = dict(con.execute("SELECT date, built_at FROM group_runs WHERE version=?", (GROUPING_VERSION,)))
    if not built:
        sys.exit(f"no stage-1 groups for {GROUPING_VERSION}; run `p3_group.py run` first")
    dates = sorted(built)
    W = TRACK_KNOBS["window_days"]
    if args.date:
        bad = [d for d in args.date if d not in built]
        if bad:
            sys.exit(f"no stage-1 groups for: {', '.join(bad)}")
        todo = sorted(args.date)
    elif args.all or args.dry:
        todo = dates
    else:
        todo = stale_dates(con, built, W)
    if not todo:
        print(f"{GROUPING_VERSION}/{TRACKING_VERSION}: every built date is tracked and current "
              f"({len(dates)} dates). Nothing to do.")
        return
    k = TRACK_KNOBS
    print(f"{GROUPING_VERSION}/{TRACKING_VERSION} | {'DRY, nothing written' if args.dry else 'writing data/intel.db'} | "
          f"{len(todo)} date(s) | window {W} d · {k['linkage']} ≥ {k['link_threshold']} · decay off")
    cache = {}

    def groups_of(d):
        if d not in cache:
            cache[d] = load_groups(con, vecs, d)
        return cache[d]

    tot = dict(groups=0, parents=0, sides=0, below=0)
    for d in todo:
        win = window_of(d, dates, W)
        today = groups_of(d)
        earlier = [(e, gid, m) for e in win for gid, m in groups_of(e)]
        rows = track_day(d, today, earlier)
        if not args.dry:
            write_track(intel, d, win, len(today), rows)
        n = {r: sum(1 for x in rows if x[7] == r) for r in ("parent", "side", "below")}
        print(f"  {d}  groups {len(today):4d}  window {len(win)} date(s)  "
              f"parents {n['parent']:4d} ({(n['parent'] / len(today) if today else 0):5.1%})  "
              f"sides {n['side']:3d}  below {n['below']:4d}" + ("" if win else "  (nothing earlier in window)"))
        for key, v in zip(tot, (len(today), n["parent"], n["side"], n["below"])):
            tot[key] += v
        for e in list(cache):                                     # keep memory flat: drop dates no longer needed
            if gap_days(e, d) > W:
                del cache[e]
    print(f"  total     groups {tot['groups']}  parents {tot['parents']} "
          f"({tot['parents'] / max(tot['groups'], 1):.1%} of groups)  sides {tot['sides']}  below {tot['below']}")


def open_intel_ro():
    if not INTEL_DB.exists():
        sys.exit(f"missing {INTEL_DB}; run `p3_group.py run` first")
    return open_ro(INTEL_DB)


def paid_hashes(news, hashes):
    """Provisional paid predicate (check only): kind in PAID_KINDS OR a cue not in DROPPED_CUES."""
    paid = set()
    for i in range(0, len(hashes), 500):
        chunk = hashes[i:i + 500]
        q = ",".join("?" * len(chunk))
        for h, kind, cue in news.execute(
                f"SELECT text_hash, kind, sponsored_cue FROM tags "
                f"WHERE model=? AND prompt_version=? AND text_hash IN ({q})",
                (TAG_MODEL, TAG_PROMPT, *chunk)):
            if kind in PAID_KINDS or (cue and cue.strip().lower() not in DROPPED_CUES):
                paid.add(h)
    return paid


def paid_bridges(group_members, links, paid):
    """Groups whose editorial members fall apart when the paid members are removed."""
    editorial = [m for m in group_members if m not in paid]
    if len(editorial) < 2 or len(editorial) == len(group_members):
        return False
    uf = UnionFind(editorial)
    for a, b in links:
        if a in uf.parent and b in uf.parent:
            uf.union(a, b)
    return len({uf.find(m) for m in editorial}) > 1


def cmd_check(args):
    news, intel = open_ro(NEWS_DB), open_intel()
    dates = [r[0] for r in intel.execute(
        "SELECT date FROM group_runs WHERE version=? ORDER BY date", (GROUPING_VERSION,))]
    if args.date:
        dates = [d for d in dates if d in args.date]
    if not dates:
        sys.exit(f"nothing built for {GROUPING_VERSION}" + (f" on {args.date}" if args.date else ""))
    stored = set(stored_dates(news))
    print(f"{GROUPING_VERSION} | {len(dates)} built date(s); stored but not built: "
          f"{len(stored - set(dates)) if not args.date else '-'}")
    print(f"  paid (provisional, check only): kind in {list(PAID_KINDS)} or a cue other than {list(DROPPED_CUES)}")

    sizes, flags = {}, dict(chain=0, same_only=0, bridges=0, paid=0)
    worst = []
    for d in dates:
        groups = intel.execute(
            "SELECT group_id, size, same_paper_only, chain_pairs, min_score FROM groups "
            "WHERE version=? AND date=?", (GROUPING_VERSION, d)).fetchall()
        members = {}
        for gid, h in intel.execute(
                "SELECT group_id, text_hash FROM members WHERE version=? AND date=?", (GROUPING_VERSION, d)):
            members.setdefault(gid, []).append(h)
        links = {}
        for a, b in intel.execute(
                "SELECT a, b FROM pairs WHERE version=? AND date=? AND used=1", (GROUPING_VERSION, d)):
            links.setdefault(a, []).append((a, b))
        paid = paid_hashes(news, [h for ms in members.values() for h in ms])
        flags["paid"] += len(paid)
        for gid, size, same_only, chain, min_score in groups:
            bucket = "1" if size == 1 else "2" if size == 2 else "3-4" if size <= 4 else "5-9" if size <= 9 else "10+"
            sizes[bucket] = sizes.get(bucket, 0) + 1
            flags["chain"] += bool(chain)
            flags["same_only"] += same_only
            if size >= 2:
                group_links = [p for m in members[gid] for p in links.get(m, [])]
                if paid_bridges(members[gid], group_links, paid):
                    flags["bridges"] += 1
                worst.append((min_score, size, gid))
    order = ["1", "2", "3-4", "5-9", "10+"]
    print("  group sizes   " + "  ".join(f"{k}: {sizes.get(k, 0)}" for k in order))
    print(f"  chain-flag groups {flags['chain']}  ·  same-paper-only groups {flags['same_only']}  ·  "
          f"paid texts {flags['paid']}  ·  paid bridges {flags['bridges']}")
    mid = intel.execute(
        "SELECT COUNT(*), AVG(shared_entities) FROM pairs WHERE version=? AND band='middle' AND date IN "
        f"({','.join('?' * len(dates))})", (GROUPING_VERSION, *dates)).fetchone()
    print(f"  middle band {mid[0]} pairs (≈ {mid[0] / len(dates):.0f}/day), "
          f"avg shared entities {mid[1] or 0:.2f}  (stored for the judge, not grouped)")
    worst.sort()
    if worst:
        print("  loosest multi-article groups (lowest inside-pair score): " +
              ", ".join(f"{g} ({s} texts, {m:.3f})" for m, s, g in worst[:5]))
    check_stage2(intel)


def check_stage2(intel):
    """Stage-2 summary over every tracked date (threads cross dates, so no --date filter)."""
    g, t = GROUPING_VERSION, TRACKING_VERSION
    runs = intel.execute("SELECT COUNT(*), SUM(groups), SUM(parents), SUM(sides), SUM(below) FROM track_runs "
                         "WHERE gversion=? AND tversion=?", (g, t)).fetchone()
    if not runs[0]:
        print(f"\n{g}/{t} | stage 2 not built yet (`p3_group.py track`)")
        return
    built = dict(intel.execute("SELECT date, built_at FROM group_runs WHERE version=?", (g,)))
    stale = stale_dates(intel, built, TRACK_KNOBS["window_days"])
    n_dates, n_groups, n_par, n_side, n_below = runs
    print(f"\n{g}/{t} | stage 2: {n_dates} tracked date(s); stale or missing: {len(stale)}"
          + (f" ({', '.join(stale[:5])}{' …' if len(stale) > 5 else ''})" if stale else ""))
    print(f"  links  parents {n_par} ({n_par / max(n_groups, 1):.1%} of {n_groups} groups)  ·  "
          f"sides {n_side}  ·  below-line kept {n_below}")
    gap = dict(intel.execute("SELECT gap_days, COUNT(*) FROM links WHERE gversion=? AND tversion=? "
                             "AND role='parent' GROUP BY gap_days", (g, t)))
    print("  gap of parent links (days)   " + "  ".join(f"{d}: {gap.get(d, 0)}"
                                                         for d in range(1, TRACK_KNOBS["window_days"] + 1)))
    # convergence (S6 D2 trigger): a linked group with a side link into a different thread
    parent, root = thread_roots(intel)
    conv = 0
    sides = {}
    for gid, egid in intel.execute("SELECT group_id, earlier_group_id FROM links WHERE gversion=? "
                                   "AND tversion=? AND role='side'", (g, t)):
        sides.setdefault(gid, []).append(egid)
    for gid, es in sides.items():
        mine = root.get(parent[gid], parent[gid])
        if any(root.get(e, e) != mine for e in es):
            conv += 1
    share = conv / max(len(parent), 1)
    print(f"  convergence  {conv} of {len(parent)} linked groups ({share:.1%}) have a side link into another "
          f"thread  →  S6 D2 trigger > {CONVERGENCE_TRIGGER:.0%}: {'REACHED' if share > CONVERGENCE_TRIGGER else 'not reached'}")
    # threads: size (day-groups) and span (first → last date)
    date_of = dict(intel.execute("SELECT group_id, date FROM groups WHERE version=?", (g,)))
    spans = {}
    for gid in parent:
        r = root[gid]
        lo, hi, n = spans.get(r, (date_of[r], date_of[r], 1))
        d = date_of[gid]
        spans[r] = (min(lo, d), max(hi, d), n + 1)
    long = [(gap_days(lo, hi), n, r) for r, (lo, hi, n) in spans.items()]
    long.sort(reverse=True)
    over14 = sum(1 for s, _, _ in long if s > 14)
    print(f"  threads  {len(spans)} with ≥ 2 day-groups  ·  longest span {long[0][0] if long else 0} days  ·  "
          f"spanning > 14 days: {over14}  (list: `threads`)")


def cmd_show(args):
    news, intel = open_ro(NEWS_DB), open_intel()
    rows = intel.execute(
        "SELECT group_id, size, papers, same_paper_only, chain_pairs, min_score FROM groups "
        "WHERE version=? AND date=? AND size>=? ORDER BY size DESC, group_id LIMIT ?",
        (GROUPING_VERSION, args.date, args.min_size, args.limit)).fetchall()
    if not rows:
        sys.exit(f"no groups of size ≥ {args.min_size} for {args.date} ({GROUPING_VERSION})")
    for gid, size, papers, same_only, chain, min_score in rows:
        tag = (" SAME-PAPER" if same_only else "") + (f" CHAIN×{chain}" if chain else "")
        print(f"\n{gid}  {size} texts · {papers} papers" +
              (f" · min {min_score:.3f}" if min_score is not None else "") + tag)
        for (h,) in intel.execute(
                "SELECT text_hash FROM members WHERE version=? AND date=? AND group_id=? ORDER BY text_hash",
                (GROUPING_VERSION, args.date, gid)):
            head = news.execute("SELECT headline FROM texts WHERE text_hash=?", (h,)).fetchone()
            pub = news.execute(
                "SELECT group_concat(DISTINCT publisher) FROM placements WHERE run_id=? AND date=? AND text_hash=?",
                (RUN_ID, args.date, h)).fetchone()
            print(f"   [{pub[0]}] {((head and head[0]) or '(no headline)')[:100]}")


def cmd_threads(args):
    news, intel = open_ro(NEWS_DB), open_intel_ro()
    g = GROUPING_VERSION
    parent, root = thread_roots(intel)
    if not parent:
        sys.exit(f"no stage-2 parent links for {g}/{TRACKING_VERSION}; run `p3_group.py track`")
    date_of = dict(intel.execute("SELECT group_id, date FROM groups WHERE version=?", (g,)))
    threads = {}
    for gid in parent:
        threads.setdefault(root[gid], {root[gid]}).add(gid)
    rows = []
    for r, gs in threads.items():
        ds = sorted(date_of[x] for x in gs)
        rows.append((gap_days(ds[0], ds[-1]), len(gs), r, gs))
    rows = sorted((x for x in rows if x[0] >= args.min_span), key=lambda x: (-x[0], -x[1], x[2]))
    print(f"{g}/{TRACKING_VERSION} | threads spanning ≥ {args.min_span} days: {len(rows)}"
          + (f" (showing {args.limit})" if len(rows) > args.limit else ""))
    for span, n, r, gs in rows[:args.limit]:
        print(f"\nthread {r}  ·  {n} day-groups  ·  {span} days")
        for gid in sorted(gs, key=lambda x: (date_of[x], x)):
            # headline of the longest member (most words) stands in for the group
            h = news_headline_of_group(news, intel, gid)
            gp = parent.get(gid)
            gap = f"+{gap_days(date_of[gp], date_of[gid])}d" if gp else "root"
            print(f"   {date_of[gid]}  {gap:>5}  {h[:100]}")


def news_headline_of_group(news, intel, gid):
    best = None
    for (h,) in intel.execute("SELECT text_hash FROM members WHERE version=? AND group_id=?", (GROUPING_VERSION, gid)):
        row = news.execute("SELECT headline, words FROM texts WHERE text_hash=?", (h,)).fetchone()
        if row and (best is None or (row[1] or 0) > best[1]):
            best = (row[0] or "(no headline)", row[1] or 0)
    return best[0] if best else "(no text)"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="build groups (default: dates not yet built)")
    r.add_argument("--date", action="append", help="rebuild this date (repeatable)")
    r.add_argument("--all", action="store_true", help="rebuild every stored date")
    r.add_argument("--dry", action="store_true", help="compute and print only; write nothing")
    c = sub.add_parser("check", help="counts and flags over built dates")
    c.add_argument("--date", action="append")
    s = sub.add_parser("show", help="print one date's groups with headlines")
    s.add_argument("date")
    s.add_argument("--min-size", type=int, default=2)
    s.add_argument("--limit", type=int, default=30)
    t = sub.add_parser("track", help="stage 2: link day-groups to earlier day-groups (default: new or stale dates)")
    t.add_argument("--date", action="append", help="rebuild this date (repeatable)")
    t.add_argument("--all", action="store_true", help="rebuild every built date")
    t.add_argument("--dry", action="store_true", help="compute and print only; write nothing")
    th = sub.add_parser("threads", help="list long threads with one headline per day")
    th.add_argument("--min-span", type=int, default=15, help="first-to-last days (default 15 = more than 14)")
    th.add_argument("--limit", type=int, default=20)
    args = ap.parse_args()
    {"run": cmd_run, "check": cmd_check, "show": cmd_show,
     "track": cmd_track, "threads": cmd_threads}[args.cmd](args)


if __name__ == "__main__":
    main()
