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

import os
import re
import sys
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse

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

    # Today's "Earlier coverage" shows thread entries from this many days before the
    # story's latest date (§7, last row).
    "earlier_coverage_days": 3,
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
            "knobs": {"paragraph_words": KNOBS["paragraph_words"],
                      "earlier_coverage_days": KNOBS["earlier_coverage_days"]}}


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
    browser: still processing (H1), nothing new (H2), cap reached (H5)."""
    unknown = [k for k in (payload or {}) if k not in data.FIXTURES]
    if unknown:
        raise HTTPException(status_code=400, detail=f"unknown fixture: {unknown[0]}")
    for k, v in (payload or {}).items():
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
