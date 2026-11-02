"""
test_p3_store.py -- TEST (free, no API key, no internet). Checks src/p3_store.py (K3 schema 2).

WHAT IT DOES
    Never touches data/intel.db. Everything happens on a fresh file and on a COPY of the
    real one in a temporary directory, so a bug here cannot cost a day of K2's grouping.
    Five parts:
      1. fresh file : the seventeen K3 tables, the K3 schema row, a usable connection
      2. real copy  : K2's tables, their row counts, their DDL and K2's own schema row
                      are all identical before and after; the schema-1 file migrates to
                      schema 2; visit_records, both caches, cost_log and viewer_prefs
                      keep their DDL and every row
      3. no-op      : running it again adds nothing, changes no count, does not migrate
                      a second time, and does not touch rows already written
      3b. reshape   : the one-time schema 1 -> 2 reshape, BOTH outcomes -- it runs on a
                      file whose reader tables are empty, and is REFUSED on one where a
                      reshaped table holds a row (the row and the schema row survive)
      4. usable     : one sample row into each of the seventeen tables, read back, JSON
                      intact; the vote CHECK refuses +1; a pin's article_ids and payload
                      agree; the read-only connection refuses to write; fingerprint()
                      depends on the SET and not on the order it arrived in

WHY IT EXISTS
    `intel.db` holds two strands' tables in one file. K3 is allowed to add to it and
    forbidden to disturb K2, and `p3_group.py` STOPS when `meta.schema_version` is not
    its own number (p3_group.py:234) -- so "additive only" is not a style preference
    here, it is the difference between the daily run working and not working. This test
    is the proof, re-run after every change to p3_store.py.

    Schema 2 adds a second thing to prove. It reshapes four tables non-additively, once,
    because they were empty (brief 2 §B2-1); that is only safe while it CANNOT happen to
    a table with rows in it. Part 3b is that proof, and it is the reason the migration
    has a guard at all.

PASS LINES (fixed before the run, K3 brief §B2-1)
    - On a copy of the real file: every K2 table's row count equal before and after, the
      K2 table and index DDL byte-identical, and meta.schema_version still '2'.
    - cost_log keeps its 11 rows; visit_records and both caches keep their DDL and rows.
    - All seventeen K3 tables present after `create`; meta.k3_schema_version = '2'.
    - A second and third run add nothing, migrate nothing, and leave earlier rows alone.
    - Each of the seventeen tables accepts a representative row and gives it back
      unchanged.
    - The read-only connection raises on a write.
    - A schema-1 file with a row in a reshaped table is refused, by name, with its row
      and its schema row intact.
    A failed line is answered by error analysis, not by moving the line.

WHEN TO RUN
    After every change to src/p3_store.py. Takes about a second.

USAGE (from ~/news-intel)
    .venv/bin/python tests/test_p3_store.py              fresh file + a copy of the real one
    .venv/bin/python tests/test_p3_store.py --mock       fresh file only (no copy)
"""

import json
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import p3_store as S

REAL_DB = ROOT / "data" / "intel.db"

CHECKS = []


def check(name, got, want):
    CHECKS.append((name, got, want))


def k2_fingerprint(path):
    """Everything about K2's side of the file that must not move: DDL, counts, version."""
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    ddl = con.execute(
        "SELECT type, name, tbl_name, sql FROM sqlite_master WHERE tbl_name IN "
        f"({','.join('?' * len(S.K2_TABLES))}) ORDER BY type, name", S.K2_TABLES).fetchall()
    counts = S.counts(con, S.K2_TABLES)
    version = con.execute("SELECT value FROM meta WHERE key=?", (S.K2_SCHEMA_KEY,)).fetchone()
    con.close()
    return {"ddl": ddl, "counts": counts, "version": version[0] if version else None}


# ------------------------------------------------------------------ 1. a fresh file

def fresh(tmp):
    path = Path(tmp) / "fresh.db"
    con = S.open_intel(path)
    present = S.tables(con)
    for name in S.K3_TABLES:
        check(f"fresh file has {name}", name in present, True)
    check("fresh file has all seventeen K3 tables", len(S.K3_TABLES), 17)
    check("fresh file has meta", "meta" in present, True)
    check("K3 schema row written",
          con.execute("SELECT value FROM meta WHERE key=?", (S.K3_SCHEMA_KEY,)).fetchone()[0], "2")
    check("K2's schema row NOT invented by K3",
          con.execute("SELECT value FROM meta WHERE key=?", (S.K2_SCHEMA_KEY,)).fetchone(), None)
    check("no K2 table created by K3", [t for t in S.K2_TABLES if t in present], [])
    d = S.describe(con)
    check("describe: nothing missing", d["missing_k3"], [])
    check("describe: all K3 tables empty", set(d["k3_counts"].values()), {0})
    con.close()

    # the three groups must partition every K3 table: a table that is in neither group
    # would be missed by the backup routine or deleted as a cache by mistake
    check("cache + own = every K3 table",
          sorted(S.CACHE_TABLES + S.OWN_TABLES), sorted(S.K3_TABLES))
    check("no table is both a cache and backed up",
          set(S.CACHE_TABLES) & set(S.OWN_TABLES), set())
    check("K3 and K2 table names do not overlap",
          set(S.K3_TABLES) & set(S.K2_TABLES), set())


# ------------------------------------------------- 2. and 3. a copy of the real file

# The tables schema 1 → 2 must NOT touch. `cost_log` is the one with real rows (11 on
# 3 Nov), so it is the check with teeth: a migration that reshaped the wrong table would
# lose Kunaal's measured spend, and nothing could recompute it.
KEEPSAFE = ("visit_records", "writeup_cache", "story_so_far_cache", "cost_log", "viewer_prefs")


def keepsafe_fingerprint(path):
    """Row counts AND DDL of the tables the migration promises not to touch."""
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    ddl = con.execute(
        "SELECT type, name, tbl_name, sql FROM sqlite_master WHERE tbl_name IN "
        f"({','.join('?' * len(KEEPSAFE))}) ORDER BY type, name", KEEPSAFE).fetchall()
    counts = S.counts(con, KEEPSAFE)
    con.close()
    return {"ddl": ddl, "counts": counts}


def on_copy(tmp):
    path = Path(tmp) / "intel_copy.db"
    shutil.copy2(REAL_DB, path)
    before = k2_fingerprint(path)
    kept_before = keepsafe_fingerprint(path)

    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    was = S.k3_version(con)
    con.close()
    check("the real file is at a K3 schema this code can open", was in (1, 2), True)

    con = S.open_intel(path, quiet=True)
    con.close()
    after = k2_fingerprint(path)
    kept_after = keepsafe_fingerprint(path)

    check("K2 table and index DDL byte-identical", after["ddl"], before["ddl"])
    check("K2 row counts equal before/after", after["counts"], before["counts"])
    check("K2 schema row untouched", after["version"], before["version"])
    check("K2 schema row is still '2'", after["version"], "2")
    check("K2 tables were actually there to protect",
          sorted(before["counts"]), sorted(S.K2_TABLES))
    check("K2 counts are not all zero (a real copy)",
          all(v == 0 for v in before["counts"].values()), False)

    # the migration's own promise: these five are not reshaped, additive only
    check("visit_records, both caches, cost_log and viewer_prefs keep their DDL",
          kept_after["ddl"], kept_before["ddl"])
    check("and keep every row", kept_after["counts"], kept_before["counts"])
    check("cost_log had rows to protect", kept_before["counts"]["cost_log"] > 0, True)

    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    d = S.describe(con)
    reshaped_cols = {t: [r[1] for r in con.execute(f"PRAGMA table_info({t})")]
                     for t in S.RESHAPED_AT_2}
    con.close()
    check("copy: no K3 table missing", d["missing_k3"], [])
    check("copy: no K2 table missing", d["missing_k2"], [])
    check("copy: K3 schema row = 2", d["k3_schema"], "2")
    check("copy: K2 schema row = 2", d["k2_schema"], "2")
    check("copy: read_state is now text_hash + read_at, with no keep_unread column",
          reshaped_cols["read_state"], ["text_hash", "read_at"])
    check("copy: boards gained created_by and last_used_at",
          reshaped_cols["boards"],
          ["board_id", "name", "created_by", "created_at", "last_used_at", "note"])
    check("copy: pins gained item_type, payload, source, created_by, position",
          reshaped_cols["pins"],
          ["pin_id", "board_id", "item_type", "payload", "source", "article_ids",
           "created_by", "created_at", "position", "note"])
    check("copy: feedback gained target_id and a word-valued vote",
          reshaped_cols["feedback"],
          ["feedback_id", "target_kind", "target_id", "article_ids", "vote", "note",
           "at", "visit_id"])

    # 3. a second run is a no-op, and does not disturb rows already written
    con = S.open_intel(path, quiet=True)
    con.execute("INSERT INTO viewer_prefs VALUES ('window', ?, '2026-11-01T06:00:00')",
                (json.dumps("day"),))
    con.execute("INSERT INTO read_state VALUES ('abc123', '2026-11-01T06:01:00')")
    con.commit()
    con.close()

    again = k2_fingerprint(path)
    con = S.open_intel(path, quiet=True)      # third run, after K3 rows exist
    prefs = con.execute("SELECT key, value FROM viewer_prefs").fetchall()
    reads = con.execute("SELECT text_hash, read_at FROM read_state").fetchall()
    version = S.k3_version(con)
    con.close()
    check("re-run keeps K3 rows", prefs, [("window", '"day"')])
    check("re-run keeps read_state rows", reads, [("abc123", "2026-11-01T06:01:00")])
    check("re-run leaves the schema row at 2", version, 2)
    check("re-run still leaves K2 alone", k2_fingerprint(path)["counts"], again["counts"])
    check("re-run still leaves K2 DDL alone", k2_fingerprint(path)["ddl"], before["ddl"])
    # a re-run must not migrate a second time: if it did, the reshaped tables would be
    # dropped again and the row written above would be gone, silently
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    reshaped_counts = S.counts(con, S.RESHAPED_AT_2)
    con.close()
    check("re-run does not re-migrate (the row written after the reshape is still there)",
          reshaped_counts, {"read_state": 1, "boards": 0, "pins": 0, "feedback": 0})
    check("re-run leaves cost_log's real rows alone",
          keepsafe_fingerprint(path)["counts"]["cost_log"], kept_before["counts"]["cost_log"])


# --------------------------------------------- 3b. the one-time reshape, both outcomes

# Schema 1's four reader tables, exactly as `p3_store.py` v0.1 built them. Written out
# here rather than read from git so the test stands on its own: it has to be able to
# make a schema-1 file to migrate.
SCHEMA_1_READER = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS read_state (
    text_hash TEXT PRIMARY KEY, seen_at TEXT NOT NULL,
    keep_unread INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS boards (
    board_id TEXT PRIMARY KEY, name TEXT NOT NULL, created_at TEXT NOT NULL, note TEXT);
CREATE TABLE IF NOT EXISTS pins (
    pin_id TEXT PRIMARY KEY, board_id TEXT, article_ids TEXT NOT NULL,
    story_id_at_pin TEXT, pinned_at TEXT NOT NULL, note TEXT);
CREATE INDEX IF NOT EXISTS pins_board ON pins (board_id);
CREATE TABLE IF NOT EXISTS feedback (
    feedback_id TEXT PRIMARY KEY, target_type TEXT NOT NULL, article_ids TEXT NOT NULL,
    vote INTEGER NOT NULL, why TEXT, at TEXT NOT NULL, visit_id TEXT);
CREATE TABLE IF NOT EXISTS cost_log (
    at TEXT NOT NULL, purpose TEXT NOT NULL, model TEXT NOT NULL,
    in_tokens INTEGER, out_tokens INTEGER, usd REAL, visit_id TEXT, note TEXT);
"""


def schema_1_file(path, with_a_row=False):
    con = sqlite3.connect(path)
    con.executescript(SCHEMA_1_READER)
    con.execute("INSERT INTO meta VALUES (?, '1')", (S.K3_SCHEMA_KEY,))
    con.execute("INSERT INTO cost_log VALUES "
                "('2026-10-30T06:00:00','writeup','claude-haiku-5-5',3000,200,0.0004,'v1',NULL)")
    if with_a_row:
        con.execute("INSERT INTO read_state VALUES ('h1','2026-10-30T06:00:00',0)")
    con.commit()
    con.close()


def reshape(tmp):
    # (a) the empty case: the reshape happens, and the rows that exist survive it
    path = Path(tmp) / "schema1_empty.db"
    schema_1_file(path)
    con = S.open_intel(path, quiet=True)
    cols = [r[1] for r in con.execute("PRAGMA table_info(read_state)")]
    spend = con.execute("SELECT COUNT(*) FROM cost_log").fetchone()[0]
    present = S.tables(con)
    version = S.k3_version(con)
    con.close()
    check("schema 1 → 2: the reshape ran", cols, ["text_hash", "read_at"])
    check("schema 1 → 2: the schema row moved to 2", version, 2)
    check("schema 1 → 2: cost_log's row survived", spend, 1)
    check("schema 1 → 2: the new tables are there",
          [t for t in S.K3_TABLES if t not in present], [])

    # (b) the held case: one row in a reshaped table and the migration REFUSES.
    # This is the check that decides whether the one-time window is safe: without it,
    # running this code against a file somebody had already used would delete his read
    # state, boards, pins and votes, and no backup inside the file could bring them back.
    path = Path(tmp) / "schema1_held.db"
    schema_1_file(path, with_a_row=True)
    try:
        con = S.open_intel(path, quiet=True)
        con.close()
        refused, message = False, ""
    except SystemExit as e:
        refused, message = True, str(e)
    check("a schema-1 file with a row in a reshaped table is REFUSED", refused, True)
    check("and the refusal names the table and the count",
          "read_state 1 rows" in message, True)
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    kept = con.execute("SELECT text_hash, seen_at FROM read_state").fetchall()
    still_1 = S.k3_version(con)
    con.close()
    check("the refused file still holds its row", kept, [("h1", "2026-10-30T06:00:00")])
    check("and is still at schema 1", still_1, 1)


# ------------------------------------------------------------------ 4. usable schema

SAMPLES = {
    "visit_records": (
        "INSERT INTO visit_records VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("v1", "2026-11-01T06:00:00", "day", "2026-10-24", "2026-10-24",
         json.dumps({"stories": [{"headline": "A headline", "points": 7}]}), 1,
         "label-1", "g0.1", "t0.2", "v1.3", None, json.dumps({"top_n": 10}))),
    "writeup_cache": (
        "INSERT INTO writeup_cache VALUES (?,?,?,?,?,?,?)",
        ("fp01", "claude-haiku-5-5", "w1.0",
         json.dumps([{"text": "A sentence.", "cites": ["h1"]}]), json.dumps(["h1", "h2"]),
         None, "2026-11-01T06:00:00")),
    "story_so_far_cache": (
        "INSERT INTO story_so_far_cache VALUES (?,?,?,?,?,?,?,?)",
        ("2026-10-20:abc", "fp02", "claude-haiku-5-5", "s1.0",
         json.dumps([{"text": "It began.", "cites": ["h1"]}]),
         json.dumps([{"date": "2026-10-20", "what": "It began."}]),
         json.dumps(["2026-10-20", "2026-10-21"]), "2026-11-01T06:00:00")),
    "suggest_cache": (
        "INSERT INTO suggest_cache VALUES (?,?,?,?,?,?)",
        ("fp03", "claude-haiku-5-5", "g1.0",
         json.dumps([{"story_id": "2026-10-24:abc", "why": "Same ruling."}]),
         "enough", "2026-11-01T06:00:00")),
    "read_state": ("INSERT INTO read_state VALUES (?,?)", ("h1", "2026-11-01T06:00:00")),
    "keep_unread": ("INSERT INTO keep_unread VALUES (?,?,?)",
                    ("2026-10-24:abc", json.dumps(["h1", "h2"]), "2026-11-01T06:00:00")),
    "follows": ("INSERT INTO follows VALUES (?,?,?,?)",
                ("2026-10-20:abc", "2026-10-24:abc", json.dumps(["h1", "h2"]),
                 "2026-11-01T06:00:00")),
    "boards": ("INSERT INTO boards VALUES (?,?,?,?,?,?)",
               ("b1", "Water", "app", "2026-11-01T06:00:00", "2026-11-01T06:30:00", None)),
    "pins": ("INSERT INTO pins VALUES (?,?,?,?,?,?,?,?,?,?)",
             ("p1", "b1", "story", json.dumps({"text_hashes": ["h1", "h2"]}),
              json.dumps({"paper": "Mint", "date": "2026-10-24", "page": 5}),
              json.dumps(["h1", "h2"]), "app", "2026-11-01T06:00:00", 1, "why")),
    "feedback": ("INSERT INTO feedback VALUES (?,?,?,?,?,?,?,?)",
                 ("f1", "writeup", "2026-10-24:abc", json.dumps(["h1"]), "down",
                  "wrong number", "2026-11-01T06:00:00", "v1")),
    "cost_log": ("INSERT INTO cost_log VALUES (?,?,?,?,?,?,?,?)",
                 ("2026-11-01T06:00:00", "writeup", "claude-haiku-5-5", 3000, 200, 0.0004, "v1", None)),
    "viewer_prefs": ("INSERT INTO viewer_prefs VALUES (?,?,?)",
                     ("sort", json.dumps("relevance"), "2026-11-01T06:00:00")),
    "visits": ("INSERT INTO visits VALUES (?,?,?,?,?)",
               ("v_0001", "latest", json.dumps({"kind": "day", "to": "2026-10-24"}), 1,
                "2026-11-01T06:00:00")),
    "user_links": ("INSERT INTO user_links VALUES (?,?,?,?,?,?)",
                   ("ul_0001", "2026-10-24:abc", json.dumps(["h1"]), "2026-10-20:def",
                    json.dumps(["h3"]), "2026-11-01T06:00:00")),
    "conversations": ("INSERT INTO conversations VALUES (?,?,?,?)",
                      ("c_0001", "2026-11-01T06:00:00", None, "2026-11-01T06:10:00")),
    "turns": ("INSERT INTO turns VALUES (?,?,?,?,?)",
              ("t_0001", "c_0001", "answer",
               json.dumps({"answer_id": "a_1", "sentences": [{"text": "A.", "cites": ["h1"]}]}),
               "2026-11-01T06:05:00")),
    "passage_sets": ("INSERT INTO passage_sets VALUES (?,?,?,?)",
                     ("ps_0001", json.dumps(["h1", "h2"]),
                      json.dumps([{"text_hash": "h1", "text": "A passage."}]),
                      "2026-11-01T06:05:00")),
}


def usable(tmp):
    path = Path(tmp) / "usable.db"
    con = S.open_intel(path)
    check("a sample row is written for every K3 table", sorted(SAMPLES), sorted(S.K3_TABLES))
    for name, (sql, values) in SAMPLES.items():
        try:
            con.execute(sql, values)
            check(f"{name} accepts a representative row", True, True)
        except sqlite3.Error as e:
            check(f"{name} accepts a representative row", f"{type(e).__name__}: {e}", True)
    con.commit()

    back = con.execute("SELECT digest, knobs FROM visit_records WHERE visit_id='v1'").fetchone()
    check("the digest JSON comes back unchanged",
          json.loads(back[0])["stories"][0]["headline"], "A headline")
    check("the knob values come back unchanged", json.loads(back[1])["top_n"], 10)
    cites = con.execute("SELECT sentences FROM writeup_cache").fetchone()[0]
    check("cited article ids come back unchanged", json.loads(cites)[0]["cites"], ["h1"])
    check("a cache key is the primary key (a second write of the same key is refused)",
          refuses(con, *SAMPLES["writeup_cache"]), True)
    check("one read per article (a second read_state row for one hash is refused)",
          refuses(con, *SAMPLES["read_state"]), True)

    # schema 2's vote column: the database refuses anything but the two words, so a
    # caller that still thinks in +1/-1 (schema 1) fails loudly instead of storing a
    # vote nothing can read back
    check("a +1 vote is refused by the vote CHECK",
          refuses(con, "INSERT INTO feedback VALUES (?,?,?,?,?,?,?,?)",
                  ("f2", "story", "s1", json.dumps(["h1"]), 1, None,
                   "2026-11-01T06:00:00", None)), True)
    check("an 'up' vote is accepted",
          refuses(con, "INSERT INTO feedback VALUES (?,?,?,?,?,?,?,?)",
                  ("f3", "story", "s1", json.dumps(["h1"]), "up", None,
                   "2026-11-01T06:00:00", None)), False)
    ids, payload = con.execute(
        "SELECT article_ids, payload FROM pins WHERE pin_id='p1'").fetchone()
    check("a pin's article_ids and payload hold the same hashes (one writer)",
          sorted(json.loads(ids)), sorted(json.loads(payload)["text_hashes"]))
    check("a JSON-encoded pref decodes to its own type, not a string",
          json.loads(con.execute("SELECT value FROM viewer_prefs WHERE key='sort'").fetchone()[0]),
          "relevance")
    check("one follow per thread (a second row for one thread is refused)",
          refuses(con, *SAMPLES["follows"]), True)
    check("a turn's answer object comes back unchanged",
          json.loads(con.execute("SELECT payload FROM turns WHERE turn_id='t_0001'"
                                 ).fetchone()[0])["sentences"][0]["cites"], ["h1"])
    con.close()

    ro = S.open_intel_ro(path)
    check("the read-only connection refuses to write",
          refuses(ro, "INSERT INTO viewer_prefs VALUES ('x','y','z')", ()), True)
    check("the read-only connection still reads",
          ro.execute("SELECT COUNT(*) FROM viewer_prefs").fetchone()[0], 1)
    ro.close()

    # fingerprint: a key for a SET
    check("fingerprint ignores order", S.fingerprint(["b", "a"]), S.fingerprint(["a", "b"]))
    check("fingerprint ignores duplicates", S.fingerprint(["a", "a", "b"]), S.fingerprint(["a", "b"]))
    check("a different set is a different key",
          S.fingerprint(["a", "b"]) == S.fingerprint(["a", "c"]), False)
    check("an added article is a different key (no stale cache)",
          S.fingerprint(["a", "b"]) == S.fingerprint(["a", "b", "c"]), False)
    check("fingerprint length", len(S.fingerprint(["a"])), 16)
    check("fingerprint of nothing still works", len(S.fingerprint([])), 16)


def refuses(con, sql, values):
    try:
        con.execute(sql, values)
        con.commit()
        return False
    except sqlite3.Error:
        return True


def main():
    mock_only = "--mock" in sys.argv
    with tempfile.TemporaryDirectory() as tmp:
        fresh(tmp)
        usable(tmp)
        reshape(tmp)
        if mock_only:
            print("copy of the real intel.db: SKIPPED (--mock)")
        elif not REAL_DB.exists():
            print(f"copy of the real intel.db: SKIPPED ({REAL_DB} not found)")
        else:
            on_copy(tmp)

    bad = [(n, g, w) for n, g, w in CHECKS if g != w]
    for n, g, w in bad:
        print(f"FAIL {n}: got {g!r}, want {w!r}")
    print(f"{len(CHECKS) - len(bad)}/{len(CHECKS)} checks passed "
          f"({'fresh file only' if mock_only else 'fresh file + copy of the real intel.db'})")
    print("the real data/intel.db was not opened for writing by this test")
    print("PASS" if not bad else "FAIL")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
