"""p3_store.py — Phase 3 · K3's tables in `data/intel.db` (K3 schema 1).
  v0.1 1 Nov 2026 (K3 B1-2): the nine tables of K3 build brief 1 §B1-2, additive only.

WHAT IT DOES
  Owns the K3 side of `data/intel.db`: creates its tables if they are missing, hands
  back a connection, and says what it found. Nine tables in three groups (Q8):
      records       visit_records        every visit's digest exactly as it was shown
      caches        writeup_cache · story_so_far_cache        deletable, rebuildable
      Kunaal's own  read_state · boards · pins · feedback · cost_log · viewer_prefs
  K2's tables (`group_runs`, `groups`, `members`, `pairs`, `track_runs`, `links`) live
  in the same file and are never read or written here.

WHY
  The digest is built on open and must be reproducible afterwards: a visit record keeps
  the digest as shown together with the rule versions and knob values that produced it,
  so "why did it say that in October?" has an answer. The caches exist because a
  write-up costs money and the same article set must never be paid for twice; they are
  keyed by content, so a changed article set is a different key and no cache can go
  stale (cache keyed by content). Read state, pins and feedback are stored against
  ARTICLES, not stories, because stories are recomputed: a story is only as stable as
  the last clustering run, an article id is forever (Q8a, stable identity under
  re-clustering). Design of record: `_docs/2026-11-01_K3_digest_search.md` Q8 (a)-(c).

SCHEMA VERSIONS (two of them, on purpose)
  K2 keeps `meta.schema_version` and `p3_group.py` STOPS when that row is not exactly
  its own number (p3_group.py:234). So K3 must not touch it. K3 keeps its own row,
  `meta.k3_schema_version`, and leaves K2's alone; the two move independently. Changes
  here are additive only (§8c): new tables, or new columns on K3 tables, never a
  rewrite, so an older build keeps working against a newer file.

WHAT "ARTICLE ID" MEANS HERE
  `text_hash`, the identity of one distinct text in `news.db`. It is what K2's `members`
  table holds, so "was every article in this story seen?" is a set comparison against
  `members.text_hash` with no translation step. One wire story printed in two papers is
  one text and so one read, which is the behaviour a reader expects.

DATA (one owner per file)
  writes  data/intel.db   K3 tables only, listed in K3_TABLES below
  reads   nothing else. The digest and the index hand their rows in.

HOW TO RUN
  Normally imported (`open_intel()`). The two commands are for setting up and looking:
      .venv/bin/python src/p3_store.py create [--db PATH]    create what is missing
      .venv/bin/python src/p3_store.py check  [--db PATH]    tables, row counts, versions
  Its test:
      .venv/bin/python tests/test_p3_store.py                on a copy; never the real file
"""

import argparse
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INTEL_DB = ROOT / "data" / "intel.db"

# ---------------------------------------------------------------- KNOBS / versions

K3_SCHEMA_VERSION = 1              # own row, `meta.k3_schema_version`; additive bumps only
K3_SCHEMA_KEY = "k3_schema_version"

# K2's row and tables. Listed to be left alone: the test checks their row counts are
# unchanged, and nothing here ever writes to them.
K2_SCHEMA_KEY = "schema_version"
K2_TABLES = ("group_runs", "groups", "members", "pairs", "track_runs", "links")

# Deletable and rebuildable from news.db + the models (Q8b): they may be dropped to
# save space or after a prompt change, and nothing is lost but money.
CACHE_TABLES = ("writeup_cache", "story_so_far_cache")

# Kunaal's own data + the visit records: these join the backup routine (Q8b). A rebuild
# cannot recreate them, because they are what he did, not what the pipeline computed.
OWN_TABLES = ("visit_records", "read_state", "boards", "pins", "feedback", "cost_log",
              "viewer_prefs")

K3_TABLES = OWN_TABLES + CACHE_TABLES

SCHEMA = """
-- `meta` is shared with K2; the DDL is identical to p3_group.py's, so this is a no-op
-- when K2 made the file first, and makes a file K3 reaches first usable by K2.
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);

-- ---------------------------------------------------------------- records (backed up)
-- One row per visit. `digest` is the digest EXACTLY as shown, as JSON; the version and
-- knob columns beside it are what makes that JSON explainable later.
CREATE TABLE IF NOT EXISTS visit_records (
    visit_id TEXT PRIMARY KEY,        -- time-ordered id, made by the digest builder
    at TEXT NOT NULL,                 -- ISO time the visit was recorded
    window_kind TEXT,                 -- 'day' | 'week' | 'since_last'
    window_from TEXT, window_to TEXT, -- the dates the window covered (inclusive)
    digest TEXT NOT NULL,             -- JSON: the digest as shown
    stories INTEGER,                  -- how many items it held (a cheap sanity column)
    label_version TEXT,               -- p3_label.LABEL_VERSION
    grouping_version TEXT,            -- p3_group GROUPING_VERSION (g0.1)
    tracking_version TEXT,            -- p3_group TRACKING_VERSION (t0.2)
    tag_prompt TEXT,                  -- the tag record the labels read (v1.3)
    writer_prompt TEXT,               -- write-up prompt version, NULL with --no-llm
    knobs TEXT                        -- JSON of the knob values used for this digest
);

-- ------------------------------------------------------------- caches (rebuildable)
-- Keyed by content: fingerprint of the article set + which model + which prompt. A
-- changed article set is a different key, so a cached write-up can never be stale.
CREATE TABLE IF NOT EXISTS writeup_cache (
    fingerprint TEXT NOT NULL,        -- fingerprint() over the article set fed to the writer
    model TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    sentences TEXT NOT NULL,          -- JSON: [{"text": ..., "cites": [text_hash, ...]}, ...]
    article_ids TEXT NOT NULL,        -- JSON: the text_hashes the fingerprint was made from
    headline TEXT,                    -- writer's headline, only when the printed ones failed
    created_at TEXT NOT NULL,
    PRIMARY KEY (fingerprint, model, prompt_version)
);

CREATE TABLE IF NOT EXISTS story_so_far_cache (
    thread_id TEXT NOT NULL,          -- K2's thread id (the root group id)
    fingerprint TEXT NOT NULL,        -- fingerprint() over the thread's day set
    model TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    sentences TEXT NOT NULL,          -- JSON: [{"text": ..., "cites": [...]}, ...]
    key_dates TEXT,                   -- JSON: [{"date": ..., "what": ...}, ...]
    day_ids TEXT NOT NULL,            -- JSON: the dates the fingerprint was made from
    created_at TEXT NOT NULL,
    PRIMARY KEY (thread_id, fingerprint, model, prompt_version)
);

-- ------------------------------------------------- Kunaal's own data (backed up)
-- Against text_hash, never a story id (Q8a).
CREATE TABLE IF NOT EXISTS read_state (
    text_hash TEXT PRIMARY KEY,
    seen_at TEXT NOT NULL,
    keep_unread INTEGER NOT NULL DEFAULT 0   -- 1 = "leave this marked unread"
);

CREATE TABLE IF NOT EXISTS boards (
    board_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    note TEXT
);

-- `article_ids` is the story's article set AT PIN TIME, so the pin re-attaches to
-- whichever story later holds those articles (Q8a).
CREATE TABLE IF NOT EXISTS pins (
    pin_id TEXT PRIMARY KEY,
    board_id TEXT,
    article_ids TEXT NOT NULL,        -- JSON: [text_hash, ...] at pin time
    story_id_at_pin TEXT,             -- the story id then, for the record only
    pinned_at TEXT NOT NULL,
    note TEXT
);
CREATE INDEX IF NOT EXISTS pins_board ON pins (board_id);

CREATE TABLE IF NOT EXISTS feedback (
    feedback_id TEXT PRIMARY KEY,
    target_type TEXT NOT NULL,        -- what was judged: 'story' | 'writeup' | 'headline' |
                                      -- 'one_liner' | 'story_so_far' | 'answer' | 'digest'
    article_ids TEXT NOT NULL,        -- JSON: [text_hash, ...] at the time
    vote INTEGER NOT NULL,            -- +1 or -1
    why TEXT,
    at TEXT NOT NULL,
    visit_id TEXT                     -- which visit it came from, when known
);
CREATE INDEX IF NOT EXISTS feedback_at ON feedback (at);

-- Every paid call writes one row, before the daily cap is checked again.
CREATE TABLE IF NOT EXISTS cost_log (
    at TEXT NOT NULL,
    purpose TEXT NOT NULL,            -- 'writeup' | 'story_so_far' | 'answer' | ...
    model TEXT NOT NULL,
    in_tokens INTEGER, out_tokens INTEGER,
    usd REAL,
    visit_id TEXT,
    note TEXT
);
CREATE INDEX IF NOT EXISTS cost_log_at ON cost_log (at);

CREATE TABLE IF NOT EXISTS viewer_prefs (
    key TEXT PRIMARY KEY,
    value TEXT,
    set_at TEXT
);
"""


# ------------------------------------------------------------------ content keys

def fingerprint(items):
    """A short, stable key for a SET of ids (article hashes, or a thread's dates).

    Sorted and de-duplicated first, so the key depends on the set and not on the order
    it arrived in; 16 hex characters of SHA-256 is far more than enough to keep a few
    thousand article sets apart. The cache keys are built only with this function, so a
    second caller can never disagree with the first about what the key is.
    """
    joined = "\n".join(sorted({str(i) for i in items}))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]


# ------------------------------------------------------------------ connections

def open_intel(path=INTEL_DB):
    """Open intel.db, create any missing K3 table, and return the connection.

    Idempotent: a second call creates nothing and changes nothing. K2's schema row is
    read for the report but never written, and a K2 version this code does not know is
    not an error here — K2 polices its own tables.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    row = con.execute("SELECT value FROM meta WHERE key=?", (K3_SCHEMA_KEY,)).fetchone()
    if row is None:
        con.execute("INSERT INTO meta VALUES (?, ?)", (K3_SCHEMA_KEY, str(K3_SCHEMA_VERSION)))
        con.commit()
    elif int(row[0]) > K3_SCHEMA_VERSION:
        sys.exit(f"intel.db K3 schema {row[0]} is newer than this code "
                 f"({K3_SCHEMA_VERSION}); update the code, do not downgrade the file")
    elif int(row[0]) < K3_SCHEMA_VERSION:
        # Additive only (§8c): the executescript above already added what was missing.
        con.execute("UPDATE meta SET value=? WHERE key=?",
                    (str(K3_SCHEMA_VERSION), K3_SCHEMA_KEY))
        con.commit()
        print(f"intel.db: K3 schema {row[0]} → {K3_SCHEMA_VERSION} (tables added; "
              f"K2 tables and K2's schema row untouched)")
    return con


def open_intel_ro(path=INTEL_DB):
    """Read-only connection, for readers that must not be able to write (least privilege)."""
    path = Path(path)
    if not path.exists():
        sys.exit(f"missing {path} (run from ~/news-intel)")
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


# ------------------------------------------------------------------ looking

def tables(con):
    return {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def counts(con, names):
    out = {}
    present = tables(con)
    for name in names:
        out[name] = con.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0] if name in present else None
    return out


def describe(con):
    """What is in this file, as a dict (used by `check` and by the test)."""
    versions = dict(con.execute("SELECT key, value FROM meta"))
    return {
        "k3_schema": versions.get(K3_SCHEMA_KEY),
        "k2_schema": versions.get(K2_SCHEMA_KEY),
        "missing_k3": [t for t in K3_TABLES if t not in tables(con)],
        "missing_k2": [t for t in K2_TABLES if t not in tables(con)],
        "k3_counts": counts(con, K3_TABLES),
        "k2_counts": counts(con, K2_TABLES),
    }


def cmd_check(args):
    con = open_intel_ro(args.db)
    d = describe(con)
    print(f"{args.db}: K3 schema {d['k3_schema']} · K2 schema {d['k2_schema']}")
    print(f"K3 tables ({len(K3_TABLES)}): " +
          " · ".join(f"{t} {d['k3_counts'][t]}" for t in K3_TABLES))
    print(f"K2 tables, not touched by K3: " +
          " · ".join(f"{t} {d['k2_counts'][t]}" for t in K2_TABLES))
    if d["missing_k3"]:
        print(f"MISSING K3 tables: {', '.join(d['missing_k3'])} (run `create`)")
    if d["missing_k2"]:
        print(f"note: K2 tables not in this file: {', '.join(d['missing_k2'])}")
    print(f"caches (deletable, rebuildable): {', '.join(CACHE_TABLES)}")
    print(f"backed up (Kunaal's own + records): {', '.join(OWN_TABLES)}")
    return 0


def cmd_create(args):
    before = None
    if Path(args.db).exists():
        con = open_intel_ro(args.db)
        before = describe(con)
        con.close()
    con = open_intel(args.db)
    after = describe(con)
    con.close()
    if before is None:
        print(f"{args.db}: created, K3 schema {after['k3_schema']}, {len(K3_TABLES)} K3 tables")
    else:
        added = [t for t in before["missing_k3"] if t not in after["missing_k3"]]
        print(f"{args.db}: K3 schema {after['k3_schema']} · "
              f"{'added ' + ', '.join(added) if added else 'nothing to add (no-op)'}")
        kept = all(before["k2_counts"][t] == after["k2_counts"][t] for t in K2_TABLES)
        print(f"K2 tables unchanged: {'yes' if kept else 'NO — STOP'} · "
              f"K2 schema row {before['k2_schema']} → {after['k2_schema']}")
    return 0


def main():
    p = argparse.ArgumentParser(description="K3's tables in intel.db")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name, fn in (("create", cmd_create), ("check", cmd_check)):
        s = sub.add_parser(name)
        s.add_argument("--db", default=str(INTEL_DB))
        s.set_defaults(fn=fn)
    args = p.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
