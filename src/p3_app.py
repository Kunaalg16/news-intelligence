"""p3_app.py — K1 app v0 · the local server behind the reading path.
  v0.1 2 Nov 2026 (K1 A2): from the K1 build brief `_docs/K1_app_v0_brief.md` §5, §8.8.

WHAT IT DOES
  Serves one page (`app/`) and the JSON it asks for, on 127.0.0.1 only. Every piece of
  data comes from ONE import — `p3_app_double` — so when K3's real functions land, that
  single line changes and no route and no screen moves (brief §2.1, C1). This file opens
  no database and writes no SQL; a test greps it to make sure it never starts.

  It also serves the archive PDFs, which is the only way to see a chart, a photograph or
  the printed page itself. That route is the one place a request names a file, so it is
  guarded twice: the name must end `.pdf`, and the resolved path must still be inside
  `archive/` — a request that climbs out with `../` is refused, not served.

WHAT IT ADDS TO WHAT THE DOUBLE RETURNS
  Two presentation jobs that are K1's, not K3's, so they live here rather than in the
  interface K3 will implement:
  - `paragraphs`: stored article bodies have no paragraph breaks at all (K3 measured 0 of
    1,816), so the reading view makes its own, grouping WHOLE sentences until a paragraph
    reaches the knob's word count. A sentence is never split.
  - `flags`: the feature flags of §8.8, sent to the page so it can hide a feature whose
    flag is off. K3 always computes everything; a flag only decides what is shown.

ROUTES (brief §5; all JSON unless noted)
  GET  /                       the page            GET  /app.css · /app.js
  GET  /api/flags              flags + knobs the page needs
  GET  /api/digest?ref=        one digest ("latest" by default)
  GET  /api/digests            what Past digests lists
  GET  /api/status             R6 · still processing, or ready
  GET  /api/article/{hash}     R3 · one article, with `paragraphs`
  GET  /api/thread/{id}        R2 · one thread; 404 when the story has none (H3)
  POST /api/read               R1 · read on LEAVE, never on open
  POST /api/keep_unread · /api/follow · /api/visit · /api/feedback · /api/pin
  GET  /api/follows · /api/cost · /api/boards · /api/boards/{id}/pins
  POST /api/boards             new board
  DELETE /api/pin/{id}
  GET|POST /api/pref/{key}
  GET|POST /api/dev/fixtures   K1's own: flips the §8.7 fixture switches, so the states
                               that no real data can produce can be SEEN, not just tested
  GET  /pdf/{date}/{name}      the archive PDF, guarded as above

  Every route function is a plain `def`, not `async def`, for a reason: the sandbox this
  was built in refuses to bind a local port, so the tests call these functions directly.
  Keeping them synchronous means the test needs no event loop, and a single-reader local
  app has nothing to gain from async anyway.

HOW TO RUN (from ~/news-intel)
  .venv/bin/python src/p3_app.py            then open http://127.0.0.1:8765

KNOBS AND FLAGS
  Two blocks below: the knobs, each with its reason, and the §8.8 feature flags.
"""

import itertools
import json
import os
import re
import sys
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse,
                               PlainTextResponse, StreamingResponse)

ROOT = Path(__file__).resolve().parent.parent
APP_DIR = ROOT / "app"
ARCHIVE = ROOT / "archive"

sys.path.insert(0, str(Path(__file__).resolve().parent))
import p3_app_double as data          # THE one data import (brief §2.1)

APP_VERSION = "k1-app-0.1"

# ------------------------------------------------------------------------- KNOBS

KNOBS = {
    # The port is a knob so a second copy can run beside the first; 127.0.0.1 is not a
    # knob, because the brief fixes it (§2.2) and this app has no business on a network.
    "port": 8765,
    "host": "127.0.0.1",

    # A paragraph in the reading view takes whole sentences until it reaches this many
    # words. 100 is the brief's number (§8.4); what would change it is Kunaal finding the
    # paragraphs too long to re-find his place in.
    "paragraph_words": 100,

}

# ------------------------------------------------------- FEATURE FLAGS (brief §8.8)
# A flag that is off removes its element completely — no empty gap where it was.

FLAGS = {
    "graph_view": True,            # the thread's Timeline | Graph switch
    "story_so_far": True,          # "The story so far" box
    "caveat_single_source": True,  # the four caveat tags, each on its own flag, so one
    "caveat_paid_content": True,   # that reads badly on real data can be dropped without
    "caveat_paid_only": True,      # taking the others with it
    "caveat_partial_text": True,
    "theme_line": True,            # "<topic> · N stories in 21 days"
    "theme_bar_strip": False,      # OFF: the daily series it needs is not in the JSON (R9)
    "cost_display": True,          # the cost header; review after one week of real use
    "agency_copy": False,          # OFF until the v0.1 assistant exists

    # --- v0.1, brief 2 §8.6 -------------------------------------------------
    # Each one off removes its element completely; `assistant` off puts v0's disabled
    # rail back, so the layout never has a hole where the pane was.
    "see_also_line": True,         # the "The paper also points to:" line above Coverage
    "assistant": True,             # the whole ask pane
    "think_harder": True,          # the Sonnet re-read button in an answer's actions
    "background_line": True,       # the dashed "NOT FROM YOUR PAPERS" block
    "background_on_request": True, # the Background button on an answer that has none
    "cap_override": True,          # "Allow $1 more today" in the cap band
    "suggest_connections": True,   # the Suggest connections button and its box
    "user_links": True,            # off hides the + icon AND the user thread entries
    "explore": True,               # the EXPLORE nav item and its screen
}

app = FastAPI(title="News Intelligence", version=APP_VERSION, docs_url=None,
              redoc_url=None, openapi_url=None)


# ------------------------------------------------------------------ small helpers

def _need(payload, *keys):
    """Pull required fields out of a posted body, or answer 400 saying which is missing."""
    payload = payload or {}
    missing = [k for k in keys if k not in payload]
    if missing:
        raise HTTPException(status_code=400,
                            detail=f"missing: {', '.join(sorted(missing))}")
    return [payload[k] for k in keys]


# Two fixed-width look-behinds rather than one with an optional quote: Python's re needs
# every look-behind to be a fixed width, and a sentence can end inside a quotation mark.
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+|(?<=[.!?][\"'”’])\s+")


def paragraphs(body, min_words=None):
    """Group whole sentences into paragraphs of at least `min_words` words.

    Stored article bodies are one unbroken run of text, so the reading view has to make
    the breaks itself. A sentence is never split: a paragraph simply stops once it has
    enough words, and the last paragraph keeps whatever is left, however short — joining
    it to the one before would make a paragraph of nearly twice the size.
    """
    min_words = min_words or KNOBS["paragraph_words"]
    text = (body or "").strip()
    if not text:
        return []
    out, current, words = [], [], 0
    for sentence in _SENTENCE_END.split(text):
        sentence = sentence.strip()
        if not sentence:
            continue
        current.append(sentence)
        words += len(sentence.split())
        if words >= min_words:
            out.append(" ".join(current))
            current, words = [], 0
    if current:
        out.append(" ".join(current))
    return out


# -------------------------------------------------------------------- the page

@app.get("/", response_class=HTMLResponse)
def page():
    index = APP_DIR / "index.html"
    if not index.is_file():
        return HTMLResponse("<h1>News Intelligence</h1><p>The page is not built yet "
                            "(step A3). The JSON routes work: try /api/digest.</p>",
                            status_code=200)
    return HTMLResponse(index.read_text())


@app.get("/app.css")
def stylesheet():
    path = APP_DIR / "app.css"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="not built yet")
    return PlainTextResponse(path.read_text(), media_type="text/css")


@app.get("/app.js")
def script():
    path = APP_DIR / "app.js"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="not built yet")
    return PlainTextResponse(path.read_text(), media_type="text/javascript")


# ------------------------------------------------------------------ flags, status

@app.get("/api/flags")
def flags():
    """What the page needs to know about itself: which features are shown, and the two
    knobs it also has to honour."""
    return {"flags": dict(FLAGS), "version": APP_VERSION,
            "knobs": {"paragraph_words": KNOBS["paragraph_words"]}}


@app.get("/api/status")
def status():
    return data.run_status()


@app.get("/api/cost")
def cost():
    return data.cost_today()


# ----------------------------------------------------------------------- digests

@app.get("/api/digest")
def digest(ref: str = "latest"):
    got = data.get_digest(ref)
    if got is None:
        raise HTTPException(status_code=404, detail=f"no digest {ref!r}")
    return got


@app.get("/api/digests")
def digests():
    return {"digests": data.list_digests()}


# ------------------------------------------------------------- articles, threads

@app.get("/api/article/{text_hash}")
def article(text_hash: str):
    got = data.article(text_hash)
    if got is None:
        raise HTTPException(status_code=404, detail="no such article")
    got = dict(got)
    got["paragraphs"] = paragraphs(got.get("body"))
    return got


@app.get("/api/new_since/{story_id}")
def new_since(story_id: str, ref: str = "latest"):
    """The story's articles that arrived after the reader last read it (R4).

    Not in the brief's route list, because §4 gives the double a `new_since_read(story)`
    and no route was named for it. The page could work the rule out for itself -- it
    already knows the read state -- but then the rule would live in two places and K3's
    version would stop being the one that decides. So the server looks the story up and
    asks the double.
    """
    digest = data.get_digest(ref)
    if digest is None:
        raise HTTPException(status_code=404, detail=f"no digest {ref!r}")
    for story in digest.get("stories", []):
        if story.get("story_id") == story_id:
            return {"story_id": story_id, "new_since_read": data.new_since_read(story)}
    raise HTTPException(status_code=404, detail="no such story in that digest")


def _story_exists(story_id):
    """Whether any digest holds this story, by its own id or by its thread's."""
    for entry in data.list_digests():
        digest = data.get_digest(entry["ref"]) or {}
        for story in digest.get("stories", []):
            if story.get("story_id") == story_id:
                return True
            if (story.get("story_so_far") or {}).get("thread_id") == story_id:
                return True
    return False


@app.get("/api/thread/{thread_id}")
def thread(thread_id: str):
    """One story's thread, or a plain "there isn't one".

    Most stories ran for a single day and have no thread, and the page asks for one on
    every story it opens, so answering 404 would print a red error in the browser's
    console seven times out of ten on a perfectly healthy morning. "This story has no
    thread" is an answer (state H3), not a missing resource. A thread_id no digest has
    ever heard of still is one.
    """
    got = data.thread(thread_id)
    if got is not None:
        return got
    if _story_exists(thread_id):
        return {"thread_id": thread_id, "thread": None}
    raise HTTPException(status_code=404, detail="no such story")


# -------------------------------------------------------------- reader state (R1)

@app.post("/api/read")
def read(payload: dict = Body(...)):
    """Called when the reader LEAVES a story, never when it opens."""
    (hashes,) = _need(payload, "text_hashes")
    data.mark_read(hashes, payload.get("at"))
    return {"read": data.read_state(hashes)}


@app.post("/api/read_state")
def read_state(payload: dict = Body(...)):
    """Asking is a POST because a digest's worth of hashes does not belong in a URL."""
    (hashes,) = _need(payload, "text_hashes")
    return {"read": data.read_state(hashes)}


@app.post("/api/keep_unread")
def keep_unread(payload: dict = Body(...)):
    story_id, hashes, on = _need(payload, "story_id", "text_hashes", "on")
    data.set_keep_unread(story_id, hashes, bool(on))
    return {"story_id": story_id, "on": bool(on), "read": data.read_state(hashes)}


@app.get("/api/keep_unread")
def keep_unread_list():
    """Which stories are being held unread, so the button is right after a reload."""
    return {"keep_unread": data.list_keep_unread()}


@app.post("/api/follow")
def follow(payload: dict = Body(...)):
    story_id, hashes, on = _need(payload, "story_id", "text_hashes", "on")
    data.follow(story_id, hashes, bool(on))
    return {"follows": data.list_follows()}


@app.get("/api/follows")
def follows():
    return {"follows": data.list_follows()}


# --------------------------------------------------------------- boards and pins

@app.get("/api/boards")
def boards():
    return {"boards": data.list_boards()}


@app.post("/api/boards")
def new_board(payload: dict = Body(...)):
    (name,) = _need(payload, "name")
    name = (name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="a board needs a name")
    board_id = data.create_board(name, payload.get("created_by", "app"))
    return {"board_id": board_id, "boards": data.list_boards()}


@app.post("/api/pin")
def pin(payload: dict = Body(...)):
    board_id, item_type, pin_payload = _need(payload, "board_id", "item_type", "payload")
    pin_id = data.pin(board_id, item_type, pin_payload, payload.get("source") or {},
                      payload.get("created_by", "app"))
    return {"pin_id": pin_id}


@app.delete("/api/pin/{pin_id}")
def remove_pin(pin_id: str):
    data.unpin(pin_id)
    return {"pin_id": pin_id, "unpinned": True}


@app.get("/api/boards/{board_id}/pins")
def board_pins(board_id: str):
    return {"board_id": board_id, "pins": data.list_pins(board_id)}


# ------------------------------------------------------------- feedback and prefs

@app.post("/api/feedback")
def feedback(payload: dict = Body(...)):
    kind, target_id, vote = _need(payload, "target_kind", "target_id", "vote")
    if kind not in ("story", "thread_link"):
        raise HTTPException(status_code=400, detail="target_kind: story or thread_link")
    if vote not in ("up", "down"):
        raise HTTPException(status_code=400, detail="vote: up or down")
    data.feedback(kind, target_id, payload.get("text_hashes") or [], vote,
                  payload.get("note"))
    return {"stored": True}


@app.get("/api/feedback")
def feedback_list():
    """What has already been voted on, so the page can show it."""
    return {"votes": data.list_feedback()}


@app.get("/api/pref/{key}")
def pref(key: str):
    return {"key": key, "value": data.get_pref(key)}


@app.post("/api/pref/{key}")
def set_pref(key: str, payload: dict = Body(...)):
    (value,) = _need(payload, "value")
    data.set_pref(key, value)
    return {"key": key, "value": data.get_pref(key)}


@app.post("/api/visit")
def visit(payload: dict = Body(...)):
    """The page reads /api/visit's `last` BEFORE recording this visit, because once this
    visit is stored it is itself the last one (double, `last_visit`)."""
    (ref,) = _need(payload, "digest_ref")
    last = data.last_visit(opened_only=True)
    data.record_visit(ref, payload.get("window"), bool(payload.get("opened_story")))
    return {"last_visit": last}


# ---------------------------------------------------- the §8.7 fixture switches

@app.get("/api/dev/fixtures")
def fixtures():
    return {"fixtures": dict(data.FIXTURES)}


@app.post("/api/dev/fixtures")
def set_fixtures(payload: dict = Body(...)):
    """Flip a fixture switch so a state that no real data can produce can be seen in the
    browser: still processing (H1), nothing new (H2), cap reached (H5), the assistant
    failing (H4 / state 9).

    `assistant_error` is the one switch that is not a yes/no: its value picks WHICH
    failure (§4.2), so a string is kept as it is and only the others are coerced. A
    reason the double does not know is refused here rather than silently becoming
    "other" in the pane."""
    unknown = [k for k in (payload or {}) if k not in data.FIXTURES]
    if unknown:
        raise HTTPException(status_code=400, detail=f"unknown fixture: {unknown[0]}")
    for k, v in (payload or {}).items():
        if k == "assistant_error" and isinstance(v, str):
            if v not in data.ERROR_RETRYABLE:
                raise HTTPException(
                    status_code=400,
                    detail=f"unknown error reason: {v} "
                           f"(try {', '.join(sorted(data.ERROR_RETRYABLE))})")
            data.FIXTURES[k] = v
        else:
            data.FIXTURES[k] = bool(v)
    return {"fixtures": dict(data.FIXTURES)}


# ------------------------------------------------------------------- the PDF route

@app.get("/pdf/{date}/{name:path}")
def pdf(date: str, name: str):
    """Serve one archive PDF, so the printed page is always one click away.

    This is the only route that takes a file name, so it is guarded twice: the name must
    end `.pdf`, and the resolved path must still be inside `archive/`. A request that
    climbs out of the archive is refused (403) rather than answered.
    """
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date or ""):
        raise HTTPException(status_code=404, detail="not a date")
    if not (name or "").lower().endswith(".pdf"):
        raise HTTPException(status_code=404, detail="not a PDF")
    archive = ARCHIVE.resolve()
    target = (archive / date / name).resolve()
    if target == archive or not target.is_relative_to(archive):
        raise HTTPException(status_code=403, detail="outside the archive")
    if not target.is_file():
        raise HTTPException(status_code=404, detail="no such PDF")
    return FileResponse(target, media_type="application/pdf",
                        filename=target.name, content_disposition_type="inline")


def main():
    import uvicorn
    print(f"News Intelligence {APP_VERSION} — http://{KNOBS['host']}:{KNOBS['port']}")
    uvicorn.run(app, host=KNOBS["host"], port=KNOBS["port"], log_level="warning")


if __name__ == "__main__":
    main()


# ============================================ v0.1 · search, Explore, the ask pane
#
# Brief 2 §5. Every one of these calls the double and nothing else: no database, no
# model, no network. The two answer routes stream NDJSON -- one JSON event per line --
# so the results list is on the screen before the first sentence is written.

def _as_list(value):
    """A repeated query parameter as a plain list.

    `Query(None)` is a placeholder object that FastAPI replaces with the real value
    when a request comes in. The tests call these route functions DIRECTLY (v0 D5: the
    sandbox cannot bind a port), so they see the placeholder itself -- and anything that
    is not a list means "this filter was not asked for"."""
    return list(value) if isinstance(value, (list, tuple)) else None


def _filters_from_query(date_from, date_to, papers, topics, kinds, story_ids,
                        exclude_story_ids, hashes):
    """Build the double's filter object out of query parameters, leaving out what was
    not asked for. An empty list and a missing parameter mean the same thing here: no
    filter on that field, rather than "match nothing"."""
    wanted = {"date_from": date_from, "date_to": date_to,
              "papers": _as_list(papers), "topics": _as_list(topics),
              "kinds": _as_list(kinds), "story_ids": _as_list(story_ids),
              "exclude_story_ids": _as_list(exclude_story_ids),
              "text_hashes": _as_list(hashes)}
    return {k: v for k, v in wanted.items() if v}


@app.get("/api/search")
def search(q: str = "", sort: str = "relevance", limit: int = 20,
           date_from: str = None, date_to: str = None,
           papers: list[str] = Query(None), topics: list[str] = Query(None),
           kinds: list[str] = Query(None), story_ids: list[str] = Query(None),
           exclude_story_ids: list[str] = Query(None),
           hashes: list[str] = Query(None)):
    """Search, or browse when `q` is empty. Lists arrive as repeated parameters
    (`?papers=Mint&papers=The+Hindu`) rather than one comma-joined string, because a
    paper's name is not ours to promise has no comma in it."""
    filters = _filters_from_query(date_from, date_to, papers, topics, kinds, story_ids,
                                  exclude_story_ids, hashes)
    return data.search(q, filters=filters, sort=sort, limit=max(0, min(limit, 200)))


@app.get("/api/facets")
def facets():
    """The values the Explore filters may offer. Every one has stories behind it."""
    return data.facets()


def ndjson(events):
    """One JSON object per line. The page reads it with `fetch` and a stream reader, so
    a line may arrive split across two chunks -- which is the reader's problem, and is
    why the page has its own tested parser (acceptance check 4) rather than assuming a
    chunk is a line."""
    for event in events:
        yield json.dumps(event, ensure_ascii=False) + "\n"


def _stream(events):
    return StreamingResponse(ndjson(events), media_type="application/x-ndjson")


@app.post("/api/ask")
def ask(payload: dict = Body(...)):
    """Ask a question. Streams `results`, then sentences, then `done` -- or an error."""
    (question,) = _need(payload, "question")
    return _stream(data.ask(question, payload.get("scope"),
                            payload.get("conversation_id")))


@app.post("/api/think_harder")
def think_harder(payload: dict = Body(...)):
    """Read the same passages again with the bigger model. No `results` event: the list
    on the screen is the one the answer was built from, and must not change under it."""
    (answer_id,) = _need(payload, "answer_id")
    try:
        events = data.think_harder(answer_id)
        first = next(events)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return _stream(itertools.chain([first], events))


@app.post("/api/background")
def background(payload: dict = Body(...)):
    """A paragraph from the model's own knowledge, for an answer that has none."""
    (answer_id,) = _need(payload, "answer_id")
    try:
        return data.ask_background(answer_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/estimate")
def estimate(kind: str, answer_id: str = None):
    """What a call would cost, for the buttons that print their own price."""
    try:
        return {"kind": kind, "answer_id": answer_id,
                "cost_usd": data.estimate_cost(kind, answer_id)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/conversation")
def conversation():
    """The running conversation, which the pane redraws on load (§8.3 state 8)."""
    return data.current_conversation()


@app.post("/api/conversation/new")
def conversation_new():
    return {"conversation_id": data.new_conversation()}


@app.post("/api/conversation/divider")
def conversation_divider(payload: dict = Body(...)):
    """"NOW READING · <HEADLINE>" when the reader opens a different story."""
    story_id, headline = _need(payload, "story_id", "headline")
    return data.add_divider(payload.get("conversation_id"), story_id, headline)


@app.post("/api/suggest")
def suggest(payload: dict = Body(...)):
    """Other stories that may belong with this one. On request only (D4)."""
    (story_id,) = _need(payload, "story_id")
    try:
        return data.suggest_connections(story_id, payload.get("text_hashes"))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.post("/api/cap/raise")
def cap_raise(payload: dict = Body(None)):
    """Allow more spending for today only. The page confirms in the page first; this
    route is the Yes, and the amount is the double's knob unless one is sent."""
    try:
        return data.raise_cap_today((payload or {}).get("amount_usd"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/user_link")
def user_link(payload: dict = Body(...)):
    """R13 · the reader's own thread link, from one story to another."""
    story_id, hashes, target_id, target_hashes = _need(
        payload, "story_id", "text_hashes", "target_story_id", "target_text_hashes")
    link_id = data.add_user_link(story_id, hashes, target_id, target_hashes)
    return {"link_id": link_id, "story_id": story_id, "target_story_id": target_id}


@app.delete("/api/user_link/{link_id}")
def user_link_remove(link_id: str):
    removed = data.remove_user_link(link_id)
    if not removed:
        raise HTTPException(status_code=404, detail="no such link")
    return {"removed": link_id}


@app.get("/api/user_links")
def user_links(hashes: list[str] = Query(None)):
    """The reader's links out of the story these hashes belong to, so the + icons are
    dark again after a reload (§8.4)."""
    return {"user_links": data.list_user_links(_as_list(hashes) or [])}
