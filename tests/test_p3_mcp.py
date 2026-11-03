"""
test_p3_mcp.py -- TEST (free, no API key, no model, no MCP client). Checks src/p3_mcp.py.

WHAT IT DOES
  Calls the server's tools IN PROCESS (the SDK's own list_tools / call_tool), against a
  COPY of the real intel.db, so the real file is never written.

PASS LINES (brief 2 §3 B2-7, fixed before the run)
  - The tool list equals exactly: search facets get_story get_thread get_article
    get_digest list_boards get_board create_board add_pin.
  - A create_board + add_pin round trip changes ONLY `boards` and `pins` (every other
    table's contents hashed before and after), and both rows say created_by claude_mcp.
  - news.db and search.db mtimes are unchanged.

USAGE (from ~/news-intel)
  .venv/bin/python tests/test_p3_mcp.py
"""

import asyncio
import hashlib
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
DATA = ROOT / "data"

CHECKS = []


def check(name, got, want):
    CHECKS.append((name, got, want))


def table_hashes(path):
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    out = {}
    for (t,) in con.execute("SELECT name FROM sqlite_master WHERE type='table' "
                            "AND name NOT LIKE 'sqlite_%'"):
        h = hashlib.sha256()
        for row in con.execute(f'SELECT * FROM "{t}" ORDER BY 1'):
            h.update(repr(row).encode())
        out[t] = h.hexdigest()
    con.close()
    return out


def main():
    stamps = {p: p.stat().st_mtime_ns for p in (DATA / "news.db", DATA / "search.db")}
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / "intel.db"
        src = sqlite3.connect(f"file:{DATA / 'intel.db'}?mode=ro", uri=True)
        dst = sqlite3.connect(copy)
        src.backup(dst)
        src.close()
        dst.close()
        os.environ["P3_READER_INTEL_DB"] = str(copy)

        import p3_mcp as M
        import p3_reader as R
        R.close_all()
        R._intel_w()                       # opening may add nothing; snapshot after it
        before = table_hashes(copy)

        tools = asyncio.run(M.server.list_tools())
        names = sorted(t.name for t in tools)
        check("the tool list is exactly the ten", names, sorted([
            "search", "facets", "get_story", "get_thread", "get_article", "get_digest",
            "list_boards", "get_board", "create_board", "add_pin"]))
        check("no paid tool is exposed (ask, think_harder, background, suggest)",
              [n for n in names if n in ("ask", "think_harder", "ask_background",
                                         "suggest_connections")], [])

        asyncio.run(M.server.call_tool("create_board", {"name": "mcp test board"}))
        board = next(b for b in R.list_boards() if b["name"] == "mcp test board")
        payload = {"text_hash": "test_hash_for_mcp"}
        asyncio.run(M.server.call_tool("add_pin", {"board_id": board["board_id"],
                                                   "item_type": "article",
                                                   "payload": payload,
                                                   "source": {"via": "test"}}))
        pins = R.list_pins(board["board_id"])
        check("the pin landed on the board", len(pins), 1)
        who = R._intel_ro().execute(
            "SELECT (SELECT created_by FROM boards WHERE board_id=?), "
            "(SELECT created_by FROM pins WHERE board_id=?)",
            (board["board_id"], board["board_id"])).fetchone()
        check("both rows say created_by claude_mcp", who, ("claude_mcp", "claude_mcp"))
        R.close_all()
        after = table_hashes(copy)
        check("ONLY boards and pins changed",
              sorted(t for t in after if after[t] != before.get(t)), ["boards", "pins"])

        got = asyncio.run(M.server.call_tool("facets", {}))
        check("a read tool answers (facets)", got is not None, True)
        os.environ.pop("P3_READER_INTEL_DB", None)
        R.close_all()

    check("news.db and search.db mtimes unchanged",
          {p.name: p.stat().st_mtime_ns == s for p, s in stamps.items()},
          {"news.db": True, "search.db": True})

    bad = [(n, g, w) for n, g, w in CHECKS if g != w]
    for n, g, w in bad:
        print(f"FAIL {n}: got {g!r}, want {w!r}")
    print(f"{len(CHECKS) - len(bad)}/{len(CHECKS)} checks passed "
          f"(in-process tools, copy of intel.db)")
    print("no model was called, the real intel.db was not opened for writing")
    print("PASS" if not bad else "FAIL")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
