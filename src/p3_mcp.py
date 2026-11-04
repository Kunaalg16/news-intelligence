"""p3_mcp.py — Phase 3 · K3 · the archive as MCP tools for the Claude desktop app (B2-7).
  v0.1 3 Nov 2026.

WHAT IT DOES
  Serves the reader layer over MCP (Model Context Protocol, the standard the Claude
  desktop app uses to call local tools) on STDIO: the app starts this file as a
  subprocess and talks JSON-RPC over its stdin/stdout. No network port is opened.

  Eight READ tools:  search · facets · get_story · get_thread · get_article ·
                     get_digest · list_boards · get_board
  Exactly TWO WRITE tools: create_board(name) and add_pin(board_id, item_type, payload,
  source), both stamped created_by = "claude_mcp" so a pin Claude made can always be
  told from one Kunaal made. No tool writes anything else, and no tool calls a model --
  so nothing here costs money (the ask pane is deliberately NOT exposed: it is paid).

DATA (one owner per file)
  reads   through p3_reader / p3_search (news.db, search.db read-only; intel.db)
  writes  intel.db `boards` and `pins` only, through p3_reader's own functions

HOW TO RUN (from ~/news-intel)
  The Claude desktop app runs it; the config block is in _work/k3_b2_handoff.md.
  .venv/bin/python src/p3_mcp.py            stdio server (waits for a client)
  .venv/bin/python tests/test_p3_mcp.py     its test (copy of intel.db, no client)
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from mcp.server.mcpserver import MCPServer                         # noqa: E402

import p3_reader as R                                              # noqa: E402

CREATED_BY = "claude_mcp"

# Every tool is `async` on purpose: the SDK runs a plain function in a worker thread,
# and p3_reader keeps one SQLite connection per process, which SQLite binds to the
# thread that opened it. Async tools all run on the server's one loop thread. (Found by
# the test: create_board failed with "SQLite objects created in a thread...".)
server = MCPServer(name="news-intel",
                   instructions="Kunaal's newspaper archive: search it, read stories, "
                                "threads, articles and digests, and keep boards of pins.")


@server.tool()
async def search(query: str, filters: dict | None = None, sort: str = "relevance",
           limit: int = 20) -> dict:
    """Search the archive. Empty query = browse, newest first. Returns stories, each
    with its 1-3 best-matching articles, and a verdict (empty/thin/enough)."""
    return R.search(query, filters=filters, sort=sort, limit=limit)


@server.tool()
async def facets() -> dict:
    """The papers, topics, kinds and date range the search filters may use."""
    return R.facets()


@server.tool()
async def get_story(story_id: str, digest_ref: str | None = None) -> dict:
    """Any story, with every one of its articles (best-placed first). A story in the
    digest (the latest unless `digest_ref` is given) keeps its digest fields too."""
    # FP-6: found through p3_reader.get_story, so a story that is in NO digest is found
    # as well (LookupError only when the archive has no such story). The digest's fields
    # win where both have a key (its `headline` is the digest's object, `dates` the
    # digest's); get_story's fill in the rest (`articles`, `article_count`,
    # `paper_names`, `papers`).
    # A digest story whose id the archive no longer knows (grouping re-ran since that
    # digest was built) is still returned as the digest has it, exactly as before FP-6.
    digest = R.get_digest(digest_ref) if digest_ref else R._latest_digest()
    for story in (digest or {}).get("stories") or []:
        if story.get("story_id") == story_id:
            try:
                full = R.get_story(story_id)
            except LookupError:
                return story
            merged = dict(story)
            for key, value in full.items():
                merged.setdefault(key, value)
            return merged
    return R.get_story(story_id)


@server.tool()
async def get_thread(thread_id: str) -> dict | None:
    """A story's thread, one entry per day. None = the story ran for one day only."""
    return R.thread(thread_id)


@server.tool()
async def get_article(text_hash: str) -> dict:
    """One article in full: headline, paper, date, kind, body, page link."""
    got = R.article(text_hash)
    if got is None:
        raise LookupError(f"no such article: {text_hash!r}")
    return got


@server.tool()
async def get_digest(ref: str | None = None) -> dict:
    """A digest by ref; no ref = the list of digests available."""
    return R.get_digest(ref) if ref else {"digests": R.list_digests()}


@server.tool()
async def list_boards() -> list:
    """Every board, most recently used first."""
    return R.list_boards()


@server.tool()
async def get_board(board_id: str) -> dict:
    """One board and its pins."""
    board = next((b for b in R.list_boards() if b.get("board_id") == board_id), None)
    if board is None:
        raise LookupError(f"no such board: {board_id!r}")
    return dict(board, pins=R.list_pins(board_id))


@server.tool()
async def create_board(name: str) -> dict:
    """WRITE: a new board, created_by claude_mcp."""
    return {"board_id": R.create_board(name, created_by=CREATED_BY)}


@server.tool()
async def add_pin(board_id: str, item_type: str, payload: dict, source: dict | None = None) -> dict:
    """WRITE: pin a story/article/thread/answer/passage/own_line to a board,
    created_by claude_mcp."""
    return {"pin_id": R.pin(board_id, item_type, payload, source, created_by=CREATED_BY)}


if __name__ == "__main__":
    server.run("stdio")
