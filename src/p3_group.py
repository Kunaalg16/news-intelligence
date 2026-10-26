"""p3_group.py — Phase 3 · Story grouping v0, stage 1 (same-day groups).
  v0.1 26 Oct 2026 (B·2) · v0.2 26 Oct: paid predicate for `check` set from real tag values

WHAT IT DOES
  For each date, finds which articles (across all papers) report the same story.
  It scores every pair of that day's texts by vector similarity (Qwen3-0.6B, V1),
  links pairs at or above the LINK line, never links pairs below the NEVER line,
  and stores the pairs in between ("middle band") for a later Haiku judge.
  Groups = connected components over the links (single linkage, union-find).
  Every text ends up in exactly one group; a lone article is a group of one.

WHY
  The digest and search need "stories", not loose articles. Stage 1 (detection,
  same day) is the base; stage 2 (tracking, cross-day threads) comes later.
  Design of record: drafts/2026-10-26_B1_grouping_design.md (B·1), §2.1 and §2.3.

DATA (one writer per file)
  reads   data/news.db     (read-only)  texts of run exam-v1, tags + entities v1.3
  reads   data/vectors.db  (read-only)  model qwen3-0.6b, variant V1 (L2-normalised float32)
  writes  data/intel.db                 group_runs · groups · members · pairs (+ meta)
  A re-run of a date replaces that date's rows for this GROUPING_VERSION, in one
  transaction (idempotent). Group IDs are content-derived: <date>:<first 12 chars
  of the smallest member text_hash>; the full hash is stored as anchor_hash.

HOW TO RUN (from ~/news-intel, pipeline environment)
  .venv/bin/python src/p3_group.py run                 build dates not yet built
  .venv/bin/python src/p3_group.py run --all           rebuild every stored date
  .venv/bin/python src/p3_group.py run --date 2026-10-24 [--date ...]
  .venv/bin/python src/p3_group.py run --dry           compute and print, write nothing
  .venv/bin/python src/p3_group.py check [--date D]    counts + flags (chains, same-paper-only, paid bridges)
  .venv/bin/python src/p3_group.py show 2026-10-24 [--min-size 2] [--limit 30]

KNOBS
  Every setting chosen without strong evidence is in the KNOBS block below, with
  its reason and the measurement that would change it (mirrored in 03_ARCHITECTURE.md).
"""

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
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

NEWS_DB = Path("data/news.db")
VECTORS_DB = Path("data/vectors.db")
INTEL_DB = Path("data/intel.db")
INTEL_SCHEMA_VERSION = 1

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
    args = ap.parse_args()
    {"run": cmd_run, "check": cmd_check, "show": cmd_show}[args.cmd](args)


if __name__ == "__main__":
    main()
