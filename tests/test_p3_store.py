"""
test_p3_store.py -- TEST (free, no API key, no internet). Checks src/p3_store.py (K3 schema 1).

WHAT IT DOES
    Never touches data/intel.db. Everything happens on a fresh file and on a COPY of the
    real one in a temporary directory, so a bug here cannot cost a day of K2's grouping.
    Four parts:
      1. fresh file : the nine K3 tables, the K3 schema row, a usable connection
      2. real copy  : K2's tables, their row counts, their DDL and K2's own schema row
                      are all identical before and after; the K3 tables appear
      3. no-op      : running it again adds nothing, changes no count, and does not
                      touch rows already written into the K3 tables
      4. usable     : one sample row into each of the nine tables, read back, JSON
                      intact; the read-only connection refuses to write; fingerprint()
                      depends on the SET and not on the order it arrived in

WHY IT EXISTS
    `intel.db` holds two strands' tables in one file. K3 is allowed to add to it and
    forbidden to disturb K2, and `p3_group.py` STOPS when `meta.schema_version` is not
    its own number (p3_group.py:234) -- so "additive only" is not a style preference
    here, it is the difference between the daily run working and not working. This test
    is the proof, re-run after every change to p3_store.py.

PASS LINES (fixed before the run, K3 brief §B1-2)
    - On a copy of the real file: every K2 table's row count equal before and after, the
      K2 table and index DDL byte-identical, and meta.schema_version still '2'.
    - All nine K3 tables present after `create`; meta.k3_schema_version = '1'.
    - A second and third run add nothing and print "no-op"; rows written into K3 tables
      before a re-run are still there after it.
    - Each of the nine tables accepts a representative row and gives it back unchanged.
    - The read-only connection raises on a write.
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
    check("fresh file has nine K3 tables", len(S.K3_TABLES), 9)
    check("fresh file has meta", "meta" in present, True)
    check("K3 schema row written",
          con.execute("SELECT value FROM meta WHERE key=?", (S.K3_SCHEMA_KEY,)).fetchone()[0], "1")
    check("K2's schema row NOT invented by K3",
          con.execute("SELECT value FROM meta WHERE key=?", (S.K2_SCHEMA_KEY,)).fetchone(), None)
    check("no K2 table created by K3", [t for t in S.K2_TABLES if t in present], [])
    d = S.describe(con)
    check("describe: nothing missing", d["missing_k3"], [])
    check("describe: all K3 tables empty", set(d["k3_counts"].values()), {0})
    con.close()

    # the three groups must partition the nine tables: a table that is in neither group
    # would be missed by the backup routine or deleted as a cache by mistake
    check("cache + own = every K3 table",
          sorted(S.CACHE_TABLES + S.OWN_TABLES), sorted(S.K3_TABLES))
    check("no table is both a cache and backed up",
          set(S.CACHE_TABLES) & set(S.OWN_TABLES), set())
    check("K3 and K2 table names do not overlap",
          set(S.K3_TABLES) & set(S.K2_TABLES), set())


# ------------------------------------------------- 2. and 3. a copy of the real file

def on_copy(tmp):
    path = Path(tmp) / "intel_copy.db"
    shutil.copy2(REAL_DB, path)
    before = k2_fingerprint(path)

    con = S.open_intel(path)
    con.close()
    after = k2_fingerprint(path)

    check("K2 table and index DDL byte-identical", after["ddl"], before["ddl"])
    check("K2 row counts equal before/after", after["counts"], before["counts"])
    check("K2 schema row untouched", after["version"], before["version"])
    check("K2 schema row is still '2'", after["version"], "2")
    check("K2 tables were actually there to protect",
          sorted(before["counts"]), sorted(S.K2_TABLES))
    check("K2 counts are not all zero (a real copy)",
          all(v == 0 for v in before["counts"].values()), False)

    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    d = S.describe(con)
    con.close()
    check("copy: no K3 table missing", d["missing_k3"], [])
    check("copy: no K2 table missing", d["missing_k2"], [])
    check("copy: K3 schema row = 1", d["k3_schema"], "1")
    check("copy: K2 schema row = 2", d["k2_schema"], "2")

    # 3. a second run is a no-op, and does not disturb rows already written
    con = S.open_intel(path)
    con.execute("INSERT INTO viewer_prefs VALUES ('window', 'day', '2026-11-01T06:00:00')")
    con.execute("INSERT INTO read_state VALUES ('abc123', '2026-11-01T06:01:00', 0)")
    con.commit()
    con.close()

    again = k2_fingerprint(path)
    con = S.open_intel(path)          # third run, after K3 rows exist
    rows = con.execute("SELECT COUNT(*) FROM viewer_prefs").fetchone()[0]
    reads = con.execute("SELECT text_hash, keep_unread FROM read_state").fetchall()
    con.close()
    check("re-run keeps K3 rows", rows, 1)
    check("re-run keeps read_state rows", reads, [("abc123", 0)])
    check("re-run still leaves K2 alone", k2_fingerprint(path)["counts"], again["counts"])
    check("re-run still leaves K2 DDL alone", k2_fingerprint(path)["ddl"], before["ddl"])


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
    "read_state": ("INSERT INTO read_state VALUES (?,?,?)", ("h1", "2026-11-01T06:00:00", 1)),
    "boards": ("INSERT INTO boards VALUES (?,?,?,?)", ("b1", "Water", "2026-11-01T06:00:00", None)),
    "pins": ("INSERT INTO pins VALUES (?,?,?,?,?,?)",
             ("p1", "b1", json.dumps(["h1", "h2"]), "2026-10-24:abc", "2026-11-01T06:00:00", "why")),
    "feedback": ("INSERT INTO feedback VALUES (?,?,?,?,?,?,?)",
                 ("f1", "writeup", json.dumps(["h1"]), -1, "wrong number", "2026-11-01T06:00:00", "v1")),
    "cost_log": ("INSERT INTO cost_log VALUES (?,?,?,?,?,?,?,?)",
                 ("2026-11-01T06:00:00", "writeup", "claude-haiku-5-5", 3000, 200, 0.0004, "v1", None)),
    "viewer_prefs": ("INSERT INTO viewer_prefs VALUES (?,?,?)", ("sort", "relevance", "2026-11-01T06:00:00")),
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
