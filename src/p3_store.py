"""p3_store.py — Phase 3 · K3's tables in `data/intel.db` (K3 schema 3).
  v0.1 1 Nov 2026 (K3 B1-2): the nine tables of K3 build brief 1 §B1-2, additive only.
  v0.2 3 Nov 2026 (K3 B2-1): K3 schema 2 — the reader tables reshaped, eight tables added.
  v0.3 4 Nov 2026 (K3 FP-1): K3 schema 3 — additive: `cost_log` gains the timing of each
       paid call (TC-5), `turns` gains the hide columns FP-5 needs (one bump, not two).

THE ONE NON-ADDITIVE CHANGE (schema 1 → 2, and why it was allowed exactly once)
  Schema 1's reader tables were never written to; reshaped once (brief 2 of 2 Nov 2026,
  run 3 Nov); **additive-only applies from schema 2.** Four tables — `read_state`,
  `boards`, `pins`, `feedback` — were built before K1's app said what it needed, and
  held 0 rows on 3 Nov with no reader anywhere in `src/` outside K1's test double
  (facts §2a). Reshaping an empty table with no readers costs nothing; reshaping one
  with a single real row costs Kunaal his own data. So the window was used now and is
  closed: `migrate_1_to_2` REFUSES to drop a table that holds rows, and says which one.
  From schema 2 on, changes are additive only (§8c) — new tables, new columns, never a
  rewrite — so an older build keeps working against a newer file.

SCHEMA 2 → 3 (additive, 4 Nov 2026)
  Nine columns, `ADDED_AT_3`, added with `ALTER TABLE … ADD COLUMN` in ONE transaction,
  so a failure part-way leaves the file at schema 2 with none of them. Existing rows keep
  NULL in every new column; no row is rewritten. When the file is the real
  `data/intel.db`, a safety copy is written first to `_work/intel_before_schema3_<date>.db`
  (SQLite's online backup, so a reader mid-transaction is copied consistently).
  NOTE for a running app: this module's `open_intel` refuses a file NEWER than its own
  schema, so an app process still running the schema-2 code fails on its next new
  connection once another process has migrated the file. Restart the app after pulling.

WHAT IT DOES
  Owns the K3 side of `data/intel.db`: creates its tables if they are missing, hands
  back a connection, and says what it found. Seventeen tables in three groups (Q8):
      records       visit_records   every visit's digest exactly as it was shown
                    visits          one row per sitting, lightweight (schema 2)
      caches        writeup_cache · story_so_far_cache · suggest_cache
                    deletable, rebuildable — nothing is lost but money
      Kunaal's own  read_state · keep_unread · follows · boards · pins · feedback ·
                    cost_log · viewer_prefs · user_links · conversations · turns ·
                    passage_sets
  K2's tables (`group_runs`, `groups`, `members`, `pairs`, `track_runs`, `links`) live
  in the same file and are never read or written here. Kunaal's own thread links go in
  K3's `user_links`, NEVER in K2's `links` (contract R13).

  Two records tables, on purpose. `visit_records` is one row per digest AS BUILT, with
  the versions and knobs that explain it — written by the digest builder, for
  reproducibility. `visits` is one row per SITTING — written on every page load, updated
  in place inside the same sitting (30 min) so a reload does not reset "since last
  visit". One row per digest and one row per sitting are different counts of different
  things; the question "what did it say in October?" is answered by `visit_records`,
  "when was I last here?" by `visits`.

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
  `text_hash`, the identity of one distinct text in `news.db`: SHA-1 of headline + body,
  EXACT, with no normalisation (`p2_store.py:121`). It is what K2's `members` table holds,
  so "was every article in this story seen?" is a set comparison against `members.text_hash`
  with no translation step -- that is the reason for this choice.
  Measured 1 Nov on run exam-v1, so nobody has to guess at the behaviour: 6,655 distinct
  texts in 6,662 seats; texts printed under more than one publisher = **0**; texts printed
  on more than one date = 6. So "the same wire copy in two papers is one read" is true of
  the mechanism but describes a case that does not occur: an exact hash cannot merge two
  texts that differ by even one word. The error this choice CANNOT make is marking an
  unread article read; the error it can make is the harmless opposite, two near-identical
  pieces counting as two reads. Two papers' versions of one story are tied together by
  K2's grouping (378 multi-paper groups), not by the hash.

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

K3_SCHEMA_VERSION = 3              # own row, `meta.k3_schema_version`; additive from 2 on
K3_SCHEMA_KEY = "k3_schema_version"

# K2's row and tables. Listed to be left alone: the test checks their row counts are
# unchanged, and nothing here ever writes to them.
K2_SCHEMA_KEY = "schema_version"
K2_TABLES = ("group_runs", "groups", "members", "pairs", "track_runs", "links")

# Deletable and rebuildable from news.db + the models (Q8b): they may be dropped to
# save space or after a prompt change, and nothing is lost but money.
CACHE_TABLES = ("writeup_cache", "story_so_far_cache", "suggest_cache")

# Kunaal's own data + the visit records: these join the backup routine (Q8b). A rebuild
# cannot recreate them, because they are what he did, not what the pipeline computed.
# `passage_sets` is here and not in CACHE_TABLES on purpose: a stored answer's citation
# chips are drawn by reading its passage set back (contract v2.3), so deleting it would
# turn every cited sentence of every old answer into an uncited one.
OWN_TABLES = ("visit_records", "visits", "read_state", "keep_unread", "follows",
              "boards", "pins", "feedback", "cost_log", "viewer_prefs", "user_links",
              "conversations", "turns", "passage_sets")

K3_TABLES = OWN_TABLES + CACHE_TABLES

# The four tables reshaped once at schema 2 (see the header). `migrate_1_to_2` drops and
# recreates these and NOTHING else: `visit_records`, both schema-1 caches, `cost_log`
# (11 rows of real spend) and `viewer_prefs` (already the right shape) are never touched.
RESHAPED_AT_2 = ("read_state", "boards", "pins", "feedback")

# Schema 3's columns, (table, column, type). Added to an existing schema-2 file by
# `migrate_2_to_3`; a new file gets them from SCHEMA directly. Kept as one list so the
# migration and the test name the same nine.
ADDED_AT_3 = (
    ("cost_log", "duration_ms", "INTEGER"),     # the whole client call, attempts + waits
    ("cost_log", "first_token_ms", "INTEGER"),  # NULL while no purpose is streamed
    ("cost_log", "attempts", "INTEGER"),        # HTTP attempts the SDK made for this row
    ("cost_log", "retry_reasons", "TEXT"),      # JSON list, one entry per failed attempt
    ("cost_log", "request_id", "TEXT"),         # the API's id of the attempt that answered
    ("cost_log", "target_id", "TEXT"),          # what the call was paid for (see p3_search)
    ("turns", "hidden_at", "TEXT"),             # FP-5: soft delete
    ("turns", "hidden_by", "TEXT"),             # 'hide' | 'reask'
    ("turns", "hide_action_id", "TEXT"),        # one id per hide, so Undo reverses one
)
SAFETY_COPY_DIR = ROOT / "_work"

# Closed sets, repeated here so a caller can check without parsing the DDL comments.
CREATED_BY = ("app", "assistant", "claude_mcp")
ITEM_TYPES = ("story", "article", "thread", "answer", "passage", "own_line")
TARGET_KINDS = ("story", "thread_link", "writeup", "answer", "digest")
VOTES = ("up", "down")

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

-- One row per SITTING (schema 2). Re-opening the same digest inside
-- `visit_same_sitting_minutes` (30) UPDATES this row instead of adding one, so a reload
-- does not reset "since last visit"; `opened_story` is sticky (ORed in), because a
-- sitting in which a story was opened stays one even if the next load opens none.
CREATE TABLE IF NOT EXISTS visits (
    visit_id TEXT PRIMARY KEY,        -- 'v_' + 12 hex
    digest_ref TEXT,                  -- which digest was on screen ('latest' or a stem)
    window TEXT,                      -- JSON: the digest's own window object
    opened_story INTEGER NOT NULL DEFAULT 0,   -- 0 | 1, sticky within the sitting
    at TEXT NOT NULL                  -- ISO time, updated within the same sitting
);
CREATE INDEX IF NOT EXISTS visits_at ON visits (at);

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

-- ---------------------------------------------- caches, part 2 (rebuildable)
-- Suggest connections costs a Haiku call per press; keyed by content like the write-up
-- cache, so pressing the button twice on an unchanged story is free.
CREATE TABLE IF NOT EXISTS suggest_cache (
    fingerprint TEXT NOT NULL,        -- fingerprint() over the story's text_hashes
    model TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    suggestions TEXT NOT NULL,        -- JSON: the suggestions list as returned
    verdict TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (fingerprint, model, prompt_version)
);

-- ------------------------------------------------- Kunaal's own data (backed up)
-- Against text_hash, never a story id (Q8a): a story is only as stable as the last
-- clustering run, a text_hash is forever. Where a story id is stored it is marked
-- "at that moment" and is for the record only -- never the key anything is found by.
--
-- Closed sets (also in CREATED_BY / ITEM_TYPES / TARGET_KINDS / VOTES above):
--   created_by  app | assistant | claude_mcp     who made the row; `claude_mcp` is the
--                                               MCP server, so a pin Claude made is
--                                               tellable from one Kunaal made
--   item_type   story | article | thread | answer | passage | own_line
--   target_kind story | thread_link | writeup | answer | digest
--   vote        up | down          -- words, not +/-1: the app sends words (p3_app.py:362)

-- Read is per ARTICLE and has one column: the time it was read. Schema 1's
-- `keep_unread` flag lived here and is gone -- it was per article while the mark is per
-- story, which is the `keep_unread` table below.
CREATE TABLE IF NOT EXISTS read_state (
    text_hash TEXT PRIMARY KEY,
    read_at TEXT NOT NULL
);

-- "Leave these unread." Per story, with the article set AS IT WAS when the mark was
-- made, because that is exactly what the mark means: THESE articles, leave them unread.
CREATE TABLE IF NOT EXISTS keep_unread (
    story_id TEXT PRIMARY KEY,        -- the story id at that moment
    article_ids TEXT NOT NULL,        -- JSON: [text_hash, ...] held unread
    set_at TEXT NOT NULL
);

-- "Tell me when this moves on." Keyed on the THREAD root, because following is about
-- the future of a thread, not about today's article set -- but the article set is stored
-- too, and is what re-attaches the follow after a re-clustering run: the thread holding
-- most of `article_ids` is the same follow (contract R1; facts D1a).
CREATE TABLE IF NOT EXISTS follows (
    thread_id TEXT PRIMARY KEY,       -- K2's thread root id
    story_id_at_follow TEXT,          -- the story id then, for the record only
    article_ids TEXT NOT NULL,        -- JSON: [text_hash, ...] at follow time
    followed_at TEXT NOT NULL
);

-- `last_used_at` is STORED, not derived from the pins: a board you opened and read but
-- did not pin to has still been used, and deriving it from MAX(pins.created_at) would
-- quietly change what the Boards screen's order means (facts D3).
CREATE TABLE IF NOT EXISTS boards (
    board_id TEXT PRIMARY KEY,        -- 'b_' + 12 hex
    name TEXT NOT NULL,
    created_by TEXT NOT NULL,         -- CREATED_BY
    created_at TEXT NOT NULL,
    last_used_at TEXT,                -- bumped by pin() and by opening the board
    note TEXT
);

-- `payload` is the whole pinned thing as JSON (the double's shape); `article_ids` is the
-- indexed path to the same hashes. They CAN disagree in principle, so they have one
-- writer: `pin()` derives `article_ids` from `payload` in the same function, and nothing
-- else writes either column (brief B2-1).
CREATE TABLE IF NOT EXISTS pins (
    pin_id TEXT PRIMARY KEY,          -- 'p_' + 12 hex
    board_id TEXT,
    item_type TEXT NOT NULL,          -- ITEM_TYPES
    payload TEXT NOT NULL,            -- JSON: the pinned thing (text or ids)
    source TEXT,                      -- JSON: where it was pinned from (paper, date, page, ids)
    article_ids TEXT NOT NULL,        -- JSON: [text_hash, ...], derived from payload
    created_by TEXT NOT NULL,         -- CREATED_BY
    created_at TEXT NOT NULL,
    position INTEGER,                 -- order within the board
    note TEXT
);
CREATE INDEX IF NOT EXISTS pins_board ON pins (board_id);

-- `vote` is TEXT and checked by the database, because the app sends 'up' / 'down'
-- (p3_app.py:362) and schema 1 stored +1 / -1: one side had to convert, and a mapping
-- that exists in code can be got wrong in one direction only. Now there is no mapping.
CREATE TABLE IF NOT EXISTS feedback (
    feedback_id TEXT PRIMARY KEY,     -- 'f_' + 12 hex
    target_kind TEXT NOT NULL,        -- TARGET_KINDS
    target_id TEXT NOT NULL,          -- the id within that kind; list_feedback keys '<kind>:<id>'
    article_ids TEXT NOT NULL,        -- JSON: [text_hash, ...] at the time
    vote TEXT NOT NULL CHECK (vote IN ('up','down')),
    note TEXT,                        -- schema 1 called this `why`
    at TEXT NOT NULL,
    visit_id TEXT                     -- which visit it came from, when known
);
CREATE INDEX IF NOT EXISTS feedback_at ON feedback (at);

-- Every paid call writes one row, before the daily cap is checked again.
CREATE TABLE IF NOT EXISTS cost_log (
    at TEXT NOT NULL,
    purpose TEXT NOT NULL,            -- 'writeup' | 'story_so_far' | 'ask' | 'think_harder' |
                                      -- 'ask_background' | 'suggest'
    model TEXT NOT NULL,
    in_tokens INTEGER, out_tokens INTEGER,
    usd REAL,
    visit_id TEXT,
    note TEXT,
    -- schema 3 (TC-5): where the call's time went. NULL on rows written before it.
    duration_ms INTEGER, first_token_ms INTEGER, attempts INTEGER,
    retry_reasons TEXT,               -- JSON list: one reason per failed attempt
    request_id TEXT,
    target_id TEXT                    -- answer id, or story id for 'suggest'
);
CREATE INDEX IF NOT EXISTS cost_log_at ON cost_log (at);

-- `value` is a JSON-encoded TEXT, always: get_pref/set_pref round-trip any JSON type,
-- so the reader layer encodes on write and decodes on read (facts D7a). A plain string
-- is stored as '"x"', not x -- otherwise a stored `false` comes back as the string
-- "false", which is true. Today's cap override lives here under a dated key, with its
-- presses (time and amount) as the log the contract asks for (v2.1 D2).
CREATE TABLE IF NOT EXISTS viewer_prefs (
    key TEXT PRIMARY KEY,
    value TEXT,                       -- JSON-encoded
    set_at TEXT
);

-- Kunaal's OWN thread links (contract R13). Stored against text_hashes on BOTH ends,
-- like pins, so a re-clustering run cannot orphan them. Never written into K2's `links`:
-- K2's links are the judge's output, these are Kunaal's, and mixing them would make the
-- judge's own evaluation unreadable.
CREATE TABLE IF NOT EXISTS user_links (
    link_id TEXT PRIMARY KEY,         -- 'ul_' + 12 hex
    story_id TEXT,                    -- the open story then, for the record only
    story_text_hashes TEXT NOT NULL,  -- JSON: [text_hash, ...] -- the "from" end
    target_story_id TEXT,             -- the suggested story then, for the record only
    target_text_hashes TEXT NOT NULL, -- JSON: [text_hash, ...] -- the "to" end
    at TEXT NOT NULL
);

-- The ask pane's conversation, kept across app restarts until new_conversation().
-- `summary` is the Haiku summary of the exchanges older than the last three (Q13).
CREATE TABLE IF NOT EXISTS conversations (
    conversation_id TEXT PRIMARY KEY, -- 'c_' + 12 hex
    started_at TEXT NOT NULL,
    summary TEXT,                     -- Haiku summary of older exchanges, or NULL
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS turns (
    turn_id TEXT PRIMARY KEY,         -- 't_' + 12 hex
    conversation_id TEXT NOT NULL,
    kind TEXT NOT NULL,               -- 'answer' | 'divider' | 'background'
    payload TEXT NOT NULL,            -- JSON: the answer object, or the divider fields
    at TEXT NOT NULL,
    -- schema 3 (FP-5): a hidden turn is marked, never deleted, so Undo can restore it
    hidden_at TEXT, hidden_by TEXT,   -- hidden_by: 'hide' | 'reask'
    hide_action_id TEXT
);
CREATE INDEX IF NOT EXISTS turns_conversation ON turns (conversation_id, at);

-- The exact passages one answer's writer was allowed to read. Kept, not cached: a
-- stored answer's citation chips are drawn by reading this set back after a restart
-- (contract v2.3), and a cite that cannot be resolved is drawn as nothing, which makes
-- a cited sentence look uncited. Think harder reuses its parent's set, so two answers
-- can share one row.
CREATE TABLE IF NOT EXISTS passage_sets (
    passage_set_id TEXT PRIMARY KEY,  -- 'ps_' + 12 hex
    text_hashes TEXT NOT NULL,        -- JSON: [text_hash, ...], one entry per article
    passages TEXT NOT NULL,           -- JSON: the passages as fed to the writer
    created_at TEXT NOT NULL
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

def k3_version(con):
    """The file's K3 schema number, or None when it has none yet.

    Read BEFORE any DDL runs, because `CREATE TABLE IF NOT EXISTS` is silent about
    whether it created anything: once the schema-2 statements have run, a schema-1 file
    is indistinguishable from a new one by its table list alone. The version row is the
    only thing that still knows.
    """
    if "meta" not in tables(con):
        return None
    row = con.execute("SELECT value FROM meta WHERE key=?", (K3_SCHEMA_KEY,)).fetchone()
    return None if row is None else int(row[0])


def migrate_1_to_2(con, quiet=False):
    """The one non-additive step: drop and rebuild the four empty reader tables.

    THE GUARD IS THE POINT. The reshape is free only while the tables are empty, so this
    counts the rows first and REFUSES if any are there, naming the table. A migration
    that silently drops a row it did not expect is worse than one that stops: Kunaal's
    read state, boards, pins and votes cannot be recomputed from anything.

    Nothing else is touched. `visit_records`, `writeup_cache`, `story_so_far_cache`,
    `cost_log` (real spend) and `viewer_prefs` (already the right shape) are left exactly
    as they are, and K2's six tables and K2's own schema row are never named here at all.
    """
    present = tables(con)
    held = {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            for t in RESHAPED_AT_2 if t in present}
    nonempty = {t: n for t, n in held.items() if n}
    if nonempty:
        rows = " · ".join(f"{t} {n} rows" for t, n in sorted(nonempty.items()))
        sys.exit(f"REFUSED: K3 schema 1 → 2 reshapes {', '.join(RESHAPED_AT_2)}, and "
                 f"{rows}. The free window closed the day the first real row was "
                 f"written. Stop and ask Kunaal: the rows have to be migrated by hand, "
                 f"or the reshape dropped.")
    for t in RESHAPED_AT_2:
        con.execute(f"DROP TABLE IF EXISTS {t}")
    con.executescript(SCHEMA)
    # The tables kept from schema 1 (cost_log among them) are not rebuilt by SCHEMA, so
    # they still lack schema 3's columns: this step ends at 2 and 2 → 3 follows.
    con.execute("UPDATE meta SET value='2' WHERE key=?", (K3_SCHEMA_KEY,))
    con.commit()
    if not quiet:
        print(f"intel.db: K3 schema 1 → 2 · reshaped "
              f"{', '.join(RESHAPED_AT_2)} (all empty) · added the schema-2 tables · "
              f"K2 tables and K2's schema row untouched")
    return [t for t in K3_TABLES if t not in present]


def missing_at_3(con):
    """The schema-3 columns this file does not have yet, as (table, column, type)."""
    have = {t: {r[1] for r in con.execute(f"PRAGMA table_info({t})")}
            for t in {t for t, _c, _ty in ADDED_AT_3}}
    return [(t, c, ty) for t, c, ty in ADDED_AT_3 if c not in have[t]]


def migrate_2_to_3(con, safety_copy=None, quiet=False):
    """Schema 2 → 3: add the nine columns, all in one transaction, then the version row.

    `safety_copy`, a path, gets a full copy of the file first (the real file only; see
    `_safety_path`). A column already present is skipped, so a file some earlier build
    half-migrated still ends complete. K2's tables and K2's schema row are never named.
    """
    if safety_copy is not None and not Path(safety_copy).exists():
        Path(safety_copy).parent.mkdir(parents=True, exist_ok=True)
        dest = sqlite3.connect(safety_copy)
        con.backup(dest)
        dest.close()
    con.commit()
    # IMMEDIATE takes the write lock BEFORE the columns are read: two processes opening
    # the file at once (the app and a script) then migrate one after the other, and the
    # second finds nothing left to add instead of failing on a duplicate column.
    con.execute("BEGIN IMMEDIATE")
    try:
        todo = missing_at_3(con)
        for t, c, ty in todo:
            con.execute(f"ALTER TABLE {t} ADD COLUMN {c} {ty}")
        con.execute("UPDATE meta SET value=? WHERE key=?", ("3", K3_SCHEMA_KEY))
        con.commit()
    except Exception:
        con.rollback()
        raise
    if not quiet:
        print(f"intel.db: K3 schema 2 → 3 · added {len(todo)} columns "
              f"({', '.join(f'{t}.{c}' for t, c, _ty in todo) or 'none missing'})"
              + (f" · safety copy {safety_copy}" if safety_copy else ""))
    return todo


def _safety_path(path):
    """Where the real file's pre-migration copy goes; None for any other file."""
    if Path(path).resolve() != INTEL_DB.resolve():
        return None
    import datetime as dt
    return SAFETY_COPY_DIR / f"intel_before_schema3_{dt.date.today().isoformat()}.db"


def open_intel(path=INTEL_DB, quiet=False):
    """Open intel.db, bring the K3 tables up to date, and return the connection.

    Idempotent: a second call creates nothing and changes nothing. K2's schema row is
    read for the report but never written, and a K2 version this code does not know is
    not an error here — K2 polices its own tables.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    found = k3_version(con)        # read before any DDL; see k3_version()
    if found is not None and found > K3_SCHEMA_VERSION:
        sys.exit(f"intel.db K3 schema {found} is newer than this code "
                 f"({K3_SCHEMA_VERSION}); update the code, do not downgrade the file")
    if found == 1:
        migrate_1_to_2(con, quiet=quiet)
        found = 2
    con.executescript(SCHEMA)       # additive from schema 2 on (§8c)
    if found is None:
        con.execute("INSERT INTO meta VALUES (?, ?)", (K3_SCHEMA_KEY, str(K3_SCHEMA_VERSION)))
    con.commit()
    if found == 2:
        migrate_2_to_3(con, safety_copy=_safety_path(path), quiet=quiet)
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
