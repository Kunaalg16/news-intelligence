"""p3_app_double.py — K1 app v0 · the test double behind every screen. TEMPORARY.
  v0.1 2 Nov 2026 (K1 A1): from the K1 build brief `_docs/K1_app_v0_brief.md` §4.

WHAT IT DOES
  The app asks this module for everything it shows, and writes everything a reader does
  back through it. Nothing else. It is the only data import in `p3_app.py` (one line, so
  swapping it for K3's real functions is a one-line change), and it is DELETED at
  integration (brief §5, C1).

  Reads  `_work/*.json`              the digests K3 already builds, exactly as they are
  Writes `_work/k1_app_state.json`   read state, follows, boards, pins, feedback, prefs,
                                     visits — the reader's own state, and nowhere else
  Opens no database, runs no SQL, calls no model, touches no network (brief §2.1, §2.2).

WHY IT EXISTS
  Of the thirteen things the reading path needs, K3 has built one: the digest JSON.
  The other twelve (requests R1-R6) are specified but not written. Rather than wait, the
  screens are built against the signatures below — which ARE K1's request to K3 — so the
  app is finished and tested before K3's functions land, and lands on them without a
  screen changing. Each function's docstring says which request owns it.

WHAT IS REAL AND WHAT IS INVENTED (this matters; see §8.7 "sample data")
  REAL, read from the digest JSON: every story, headline, one-liner, write-up, story so
  far, theme, coverage row, view, caveat, page link, and every cost.
  REAL, kept in the state file: everything the reader does — read state, keep-unread,
  follows, boards, pins, feedback, prefs, visits.
  INVENTED: thread entries (R2), article bodies (R3). Both are returned with
  `"fixture": true`, and the page puts a muted "sample data" tag on them, so invented
  text is never mistaken for news. Invented page links always point at a PDF that really
  exists under `archive/` (checked on the way out), so no link in the app is a dead end.

HOW THE INVENTED THREAD IS BUILT (brief §4, and it follows the mockup exactly)
  One thread per story that ran more than a day (`developing`, or a story-so-far of 2+
  days). A day-1 story has NO thread, and the page then renders no thread section (H3).
  Entries run newest first, and the types are placed as `ThreadTimeline.dc.html` places
  them: the oldest entry is the RUN-UP (coverage before the story began), then the START
  ("where it began", which is the thread root and carries the thread's own id, so a
  story-so-far chip can resolve to it), then a FOLLOW-UP, then one UNTYPED day ("earlier
  coverage", what real data will look like until K2's judge lands), then CONTINUING.
  A thread of 4+ days gains that one extra run-up day, so `thread.days` can be one more
  than `story_so_far.days` — the brief's own example header ("6 days") against a 5-day
  story so far shows that is expected: they count different things.

READ, AND WHY IT IS ON LEAVE
  A story is READ when every `text_hash` in its coverage rows has a `read_at`. The page
  calls `mark_read` when the reader LEAVES a story, never when it opens — so the story
  in front of you never shows READ, and a glance at a headline does not consume it.
  `set_keep_unread` stores the story's hashes at that moment; `mark_read` then refuses
  them, so the rule holds even if a screen forgets to check.

HOW TO RUN
  Not run on its own. `p3_app.py` imports it; `tests/test_p3_app.py` tests it:
      .venv/bin/python tests/test_p3_app.py

KNOBS
  One block below, each with its reason and the measurement that would change it.
"""

import datetime as dt
import hashlib
import json
import os
import re
import tempfile
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORK = ROOT / "_work"
ARCHIVE = ROOT / "archive"
STATE_FILE = WORK / "k1_app_state.json"

DOUBLE_VERSION = "k1-double-0.1"

# ------------------------------------------------------------------------- KNOBS

KNOBS = {
    # --- what counts as a digest file ---------------------------------------
    "digest_dir": "_work",             # the brief's data location; read-only
    # A JSON file in there is a digest only if it has all three of these keys. Measured
    # 2 Nov: the two real digests have them; nothing else in _work/ is JSON at all. This
    # guard is what stops a stray JSON file from appearing in "Past digests".
    "digest_required_keys": ("stories", "window", "built_at"),

    # --- the cost header when a digest carries no cap knob (decision D1) ----
    # The live digest has `daily_cost_cap_usd` = 0.5; the day digest has no such knob.
    # §A8 forbids hard-coding the cap, so with no knob there is no cap to show: the
    # header says what was spent and omits the cap clause. Set this to 1.00 (the decided
    # value) to fall back to a cap instead. Kunaal's call; None until he says otherwise.
    "cap_usd_when_knob_absent": None,

    # --- the invented thread (R2) -------------------------------------------
    "min_thread_days": 2,              # below this a story has no thread at all (H3)
    "thread_runup_from_days": 4,       # a thread this long gains one earlier run-up day
    "related_per_thread": 1,           # brief §4: "plus one related item"
    "entry_max_papers": 3,             # papers per invented day; the mockup shows 1-3

    # --- the invented article body (R3) -------------------------------------
    # Real bodies have no paragraph breaks at all (K3 measured 0 of 1,816), so the
    # reading view makes its own. The body is generated to the row's own word count,
    # held inside these bounds so every fixture article is long enough to need several
    # paragraphs and short enough to stay a test fixture.
    "body_words_min": 240,
    "body_words_max": 700,

    # --- v0.1 search (§4.1) -------------------------------------------------
    # A query word shorter than this matches inside too many headlines to tell stories
    # apart ("to", "of"); three letters is the brief's own rule. What would change it is
    # a real query whose only useful word is two letters.
    "search_word_min_letters": 3,
    # How many articles a story shows in a result list. The brief says 1-3 (§4.1); the
    # pane and Explore both show the best few, not the whole story.
    "search_articles_shown": 3,
    # The fixture verdict line: this many matching stories or more is "enough", 1-2 is
    # "thin", none is "empty". K3 sets the real thresholds from its Q17 question set
    # (register A3); this is the number the brief fixes for the double.
    "verdict_enough_min": 3,

    # --- visits -------------------------------------------------------------
    # Re-opening the same digest within this many minutes is the same sitting, not a new
    # visit, so a reload does not reset "since last visit". Half an hour is a guess;
    # what would change it is Kunaal noticing the line reset mid-morning.
    "visit_same_sitting_minutes": 30,
}

# Fixture switches — the states of §8.7 that no real data can produce. One dict, flipped
# by the tests and by `p3_app.py`'s dev routes; all off in normal use.
FIXTURES = {
    "processing": False,      # H1: today's papers still processing
    "cap_reached": False,     # H5: the daily cost cap is spent
    "nothing_new": False,     # H2: every story in the digest already read
    "assistant_error": False, # H4 (v0.1): kept so the switch set is complete
}


# ------------------------------------------------------------------ small helpers

def _now():
    return dt.datetime.now().isoformat(timespec="seconds")


def _new_id(prefix):
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _fixture_hash(*parts):
    """A stable 12-hex id for invented things, so ids do not move between runs."""
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:12]


def _archive_dates():
    """Every date the archive actually holds papers for, ascending. The calendar has
    gaps (no 2026-10-16, nothing between 09-22 and 10-07), so invented thread days are
    taken from here and never from arithmetic on dates."""
    if not ARCHIVE.is_dir():
        return []
    return sorted(p.name for p in ARCHIVE.iterdir()
                  if p.is_dir() and re.fullmatch(r"\d{4}-\d{2}-\d{2}", p.name))


def _archive_papers(date):
    """The papers whose PDF really exists on that date, as (paper, relative path)."""
    day = ARCHIVE / date
    if not day.is_dir():
        return []
    out = []
    for p in sorted(day.glob(f"{date}_*.pdf")):
        out.append((p.name[len(date) + 1:-4], f"archive/{date}/{p.name}"))
    return out


def _page_link(pdf, page, paper):
    """A page link in K3's shape {pdf, pdf_page, label} plus the paper it belongs to.
    The paper is K1's addition to R2: §7 prints an entry as "3 articles · TOI p5 · TH p9"
    and the paper cannot be read off the path safely ("The Hindu_International")."""
    return {"pdf": pdf, "pdf_page": page, "label": f"PDF p{page}", "paper": paper}


# ------------------------------------------------------------------- the state file

def _blank_state():
    return {"version": 1, "read": {}, "keep_unread": {}, "follows": {}, "boards": [],
            "pins": [], "feedback": [], "prefs": {}, "visits": []}


def _load_state():
    if not STATE_FILE.exists():
        return _blank_state()
    try:
        state = json.loads(STATE_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        # A half-written state file must not take the app down: the reader's own state is
        # recoverable by reading again, the digest is not in it.
        return _blank_state()
    base = _blank_state()
    base.update(state)
    return base


def _save_state(state):
    WORK.mkdir(exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(WORK), prefix=".k1_state_", suffix=".json")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(state, fh, indent=1, sort_keys=True)
        os.replace(tmp, STATE_FILE)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


# ------------------------------------------------------------------------- digests

def _digest_files():
    """Every digest JSON in `_work/`, newest built_at first, as (ref, path, object)."""
    out = []
    for path in sorted((ROOT / KNOBS["digest_dir"]).glob("*.json")):
        try:
            obj = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(obj, dict):
            continue
        if not all(k in obj for k in KNOBS["digest_required_keys"]):
            continue
        out.append((path.stem, path, obj))
    out.sort(key=lambda t: t[2].get("built_at") or "", reverse=True)
    return out


def get_digest(ref):
    """The digest JSON as it is. `ref` = "latest" or a file id (the filename stem).
    REAL DATA, K3's own output — this is the one function K3 has already built
    (`p3_digest.py build`); at integration it reads from wherever K3 keeps them."""
    files = _digest_files()
    if not files:
        return None
    if ref in (None, "", "latest"):
        return files[0][2]
    for stem, _path, obj in files:
        if stem == ref:
            return obj
    return None


def list_digests():
    """[{ref, window, built_at}], newest first. K3 implements this (request R1/R6 area).
    `window` is the digest's own window object; the page makes the words from it, because
    a window is a week in one file and a single day in the other."""
    return [{"ref": stem, "window": obj.get("window"), "built_at": obj.get("built_at")}
            for stem, _path, obj in _digest_files()]


def run_status():
    """{state, latest_ready, updated_at}. K3 implements this (request R6).
    Real K3 will know whether this morning's papers are still being read; the double
    says "ready" unless the `processing` fixture is on (that is state H1)."""
    files = _digest_files()
    latest = files[0][2] if files else {}
    window = latest.get("window") or {}
    return {"state": "processing" if FIXTURES["processing"] else "ready",
            "latest_ready": window.get("to") or window.get("from"),
            "updated_at": latest.get("built_at"),
            "fixture": bool(FIXTURES["processing"])}


# --------------------------------------------------------------- rows and lookups

def _all_rows():
    """Every coverage row of every digest, with its story and digest ref beside it."""
    for stem, _path, obj in _digest_files():
        for story in obj.get("stories", []):
            for row in story.get("coverage", {}).get("rows", []):
                yield stem, story, row


def _story_hashes(story):
    return [r["text_hash"] for r in story.get("coverage", {}).get("rows", [])]


def _find_story(predicate):
    for _stem, _path, obj in _digest_files():
        for story in obj.get("stories", []):
            if predicate(story):
                return story
    return None


# ------------------------------------------------------------------------ articles

_BODY_SENTENCES = [
    "This is sample body text standing in for the article K3's article() will return "
    "(request R3); it is not newspaper text and no sentence here reports anything.",
    "The real body is already stored in news.db, so this placeholder exists only to give "
    "the reading view the right shape to lay out: one long run of sentences with no "
    "paragraph breaks of its own, which is what every stored article looks like.",
    "A fixture paragraph is grouped by the app, not by the source.",
    "Sample sentences vary in length on purpose, so that grouping whole sentences up to "
    "a hundred words has something uneven to work on and never has to split one.",
    "The page link beside this body opens the real printed page, which is the only place "
    "the actual article can be read until this function is real.",
    "Charts and photographs on the page are not read by the pipeline at all.",
    "This sentence, like the rest, carries the sample-data tag the reading view shows at "
    "the top of any fixture content, so invented text is never mistaken for news.",
    "Nothing in this body was written by a model and nothing in it was read from a paper.",
]


def _fixture_body(row):
    """A body of about the row's own word count, built from fixed sample sentences."""
    target = max(KNOBS["body_words_min"],
                 min(KNOBS["body_words_max"], int(row.get("words") or 0)))
    out, words, i = [], 0, 0
    while words < target:
        s = _BODY_SENTENCES[i % len(_BODY_SENTENCES)]
        out.append(s)
        words += len(s.split())
        i += 1
    return " ".join(out)


def article(text_hash):
    """One article to read in the pane. K3 implements this (request R3).
    Headline, paper, date and page link are REAL (from the digest's coverage row); the
    body is INVENTED and flagged, because no function returns stored body text yet."""
    for _stem, _story, row in _all_rows():
        if row.get("text_hash") == text_hash:
            link = dict(row.get("page_link") or {})
            link["paper"] = row.get("paper")
            return {"text_hash": text_hash,
                    "headline": row.get("headline"),
                    "deck": "",                      # no deck is stored in the digest row
                    "byline": row.get("byline") or "",
                    "paper": row.get("paper"),
                    "date": row.get("date"),
                    "page_link": link,
                    "words": row.get("words"),
                    "kind": row.get("kind"),
                    "body": _fixture_body(row),
                    "fixture": True}
    return None


# ------------------------------------------------------------------------- threads

def _thread_story(thread_id):
    """The story a thread belongs to: its own story_so_far says so, else its id matches."""
    story = _find_story(lambda s: ((s.get("story_so_far") or {}).get("thread_id")
                                   == thread_id))
    return story or _find_story(lambda s: s.get("story_id") == thread_id)


def _thread_days(story):
    """How many days this story's thread runs, and whether it gets a run-up day."""
    ssf = story.get("story_so_far") or {}
    days = ssf.get("days") or len(story.get("dates") or [])
    if days < KNOBS["min_thread_days"] and not story.get("developing"):
        return 0, False
    if days < KNOBS["min_thread_days"]:
        days = KNOBS["min_thread_days"]
    return days, days >= KNOBS["thread_runup_from_days"]


def _thread_dates(story, days, runup):
    """The dates the thread's entries sit on, ascending. Real dates only: the story's own
    days first, then other archive dates inside the thread's span, then — for the run-up —
    the archive date before the thread root."""
    ssf = story.get("story_so_far") or {}
    thread_id = ssf.get("thread_id") or story["story_id"]
    root = thread_id.split(":")[0]
    story_dates = sorted(set(story.get("dates") or []))
    cal = _archive_dates()
    chosen = sorted({root} | set(story_dates))
    span = [d for d in cal if root <= d <= (story_dates[-1] if story_dates else root)]
    for d in span:
        if len(chosen) >= days:
            break
        if d not in chosen:
            chosen = sorted(set(chosen) | {d})
    chosen = chosen[:days] if len(chosen) > days else chosen
    if runup:
        earlier = [d for d in cal if d < root]
        if earlier:
            chosen = [earlier[-1]] + chosen
    return chosen


def _entry_pages(story, date):
    """An entry's pages: the story's REAL coverage rows for that date if it has any,
    otherwise invented pages on papers whose PDF really exists on that date."""
    rows = [r for r in story.get("coverage", {}).get("rows", []) if r.get("date") == date]
    if rows:
        pages, papers = [], []
        for r in rows:
            link = dict(r.get("page_link") or {})
            link["paper"] = r.get("paper")
            pages.append(link)
            papers.append(r.get("paper"))
        return pages, sorted(set(papers)), len(rows), False
    available = _archive_papers(date)[:KNOBS["entry_max_papers"]]
    pages, papers = [], []
    for paper, pdf in available:
        page = 1 + int(_fixture_hash(story["story_id"], date, paper), 16) % 18
        pages.append(_page_link(pdf, page, paper))
        papers.append(paper)
    return pages, sorted(set(papers)), len(pages), True


def _entry_types(n, runup):
    """Link types newest-first, placed as ThreadTimeline.dc.html places them:
    run-up oldest, then where-it-began, then a follow-up, then one untyped day."""
    types = [None] * n
    oldest = n - 1
    if runup and n >= 2:
        types[oldest] = "run_up"
        types[oldest - 1] = "start"
        rest = list(range(oldest - 2, -1, -1))     # older to newer among what is left
    else:
        types[oldest] = "start"
        rest = list(range(oldest - 1, -1, -1))
    for pos, idx in enumerate(rest):
        if pos == 0:
            types[idx] = "follow_up"
        elif pos == 1:
            types[idx] = None                       # untyped "earlier coverage"
        else:
            types[idx] = "continuing"
    return types


def thread(thread_id):
    """A story's thread: its days, and one entry per day. K3 implements this (request R2).
    INVENTED and flagged: K2's typed links need the judge (B1), so until then the entries
    here exercise every link type the drawing has to handle. Page links are real."""
    story = _thread_story(thread_id)
    if story is None:
        return None
    days, runup = _thread_days(story)
    if not days:
        return None                                  # a day-1 story has no thread (H3)
    dates = _thread_dates(story, days, runup)
    if len(dates) < KNOBS["min_thread_days"]:
        return None
    ssf = story.get("story_so_far") or {}
    root = (ssf.get("thread_id") or story["story_id"]).split(":")[0]
    topic = ((story.get("theme") or {}).get("topic") or "this story")

    newest_first = sorted(dates, reverse=True)
    types = _entry_types(len(newest_first), runup and newest_first[-1] < root)
    entries, parent = [], None
    for i, date in enumerate(newest_first):
        pages, papers, articles, invented = _entry_pages(story, date)
        entry_id = (thread_id if date == root
                    else f"{date}:{_fixture_hash(thread_id, date)}")
        entries.append({
            "entry_id": entry_id,
            "date": date,
            "link_type": types[i],
            "parent_id": None,                       # filled below, oldest to newest
            "headline": f"Sample thread entry · {topic} as it stood on {date}",
            "one_liner": ("Sample one-liner standing in for K3's thread entry (request "
                          "R2); the real line comes from that day's own articles."),
            "articles": articles,
            "papers": len(papers),
            "paper_names": papers,
            "pages": pages,
            "fixture": True if invented else False,
        })
    for i in range(len(entries) - 1, 0, -1):         # each day's parent is the day before
        entries[i - 1]["parent_id"] = entries[i]["entry_id"]

    related = []
    if entries and KNOBS["related_per_thread"]:
        attach = entries[min(1, len(entries) - 1)]
        for k in range(KNOBS["related_per_thread"]):
            related.append({
                "date": attach["date"],
                "headline": (f"Sample related item · same subject as {topic}, "
                             "not part of this story"),
                "attaches_to": attach["entry_id"],
            })

    all_papers = sorted({p for e in entries for p in e["paper_names"]})
    return {"thread_id": thread_id,
            "days": len(entries),
            "articles": sum(e["articles"] for e in entries),
            "papers": len(all_papers),
            "entries": entries,
            "related": related,
            "fixture": True}


def new_since_read(story):
    """The story's articles that arrived after the reader last read it. K3: request R4.
    Nothing counts as new until something has been read: a story you have never opened is
    simply unread, so this returns [] rather than its whole coverage."""
    hashes = _story_hashes(story)
    state = _load_state()
    read = state["read"]
    if not any(read.get(h) for h in hashes):
        return []
    return [h for h in hashes if not read.get(h)]


# -------------------------------------------------------------- reader state (R1)

def mark_read(text_hashes, at=None):
    """Mark these articles read. Called when the reader LEAVES a story, never on open.
    Hashes held by an active keep-unread are refused here, so the rule holds even if a
    screen forgets to check it."""
    at = at or _now()
    state = _load_state()
    kept = {h for hs in state["keep_unread"].values() for h in hs}
    for h in text_hashes:
        if h in kept:
            continue
        state["read"].setdefault(h, at)
    _save_state(state)


def read_state(text_hashes):
    """{text_hash: read_at or None} for the hashes asked about."""
    if FIXTURES["nothing_new"]:
        at = _now()
        return {h: at for h in text_hashes}          # H2: everything already read
    read = _load_state()["read"]
    return {h: read.get(h) for h in text_hashes}


def set_keep_unread(story_id, text_hashes, on):
    """Hold a story unread. Stores the hashes AS THEY ARE NOW, because story ids change
    when groups are rebuilt and article ids do not."""
    state = _load_state()
    if on:
        state["keep_unread"][story_id] = list(text_hashes)
        for h in text_hashes:
            state["read"].pop(h, None)               # "Mark unread" on a READ story
    else:
        state["keep_unread"].pop(story_id, None)
    _save_state(state)


def list_keep_unread():
    """The stories being held unread. Added to request R1 by this build (register C2).

    §4 gives `set_keep_unread` and no way to ask. The rule still holds without one --
    `mark_read` refuses a held hash -- but after a reload the page could not tell which
    stories to show "Keep unread" as already on, so the button would lie. The brief
    added `follow` / `list_follows` to R1 for the same reason.
    """
    return [{"story_id": sid, "text_hashes": hs}
            for sid, hs in sorted(_load_state()["keep_unread"].items())]


def follow(story_id, text_hashes, on):
    """Follow or unfollow a story (added to request R1 by this build; register C2)."""
    state = _load_state()
    if on:
        state["follows"][story_id] = list(text_hashes)
    else:
        state["follows"].pop(story_id, None)
    _save_state(state)


def list_follows():
    return [{"story_id": sid, "text_hashes": hs}
            for sid, hs in sorted(_load_state()["follows"].items())]


# -------------------------------------------------------------- boards and pins (R1)

def list_boards():
    """Boards, most recently used first. `pin_count` is K1's addition to R1: the Boards
    screen of §8.6 shows it, and counting it here saves a call per board."""
    state = _load_state()
    counts = {}
    for p in state["pins"]:
        counts[p["board_id"]] = counts.get(p["board_id"], 0) + 1
    boards = [{"board_id": b["board_id"], "name": b["name"],
               "last_used_at": b.get("last_used_at"), "created_by": b.get("created_by"),
               "pin_count": counts.get(b["board_id"], 0)}
              for b in state["boards"]]
    boards.sort(key=lambda b: b["last_used_at"] or "", reverse=True)
    return boards


def create_board(name, created_by="app"):
    state = _load_state()
    board_id = _new_id("b")
    now = _now()
    state["boards"].append({"board_id": board_id, "name": name, "created_by": created_by,
                            "created_at": now, "last_used_at": now})
    _save_state(state)
    return board_id


def pin(board_id, item_type, payload, source, created_by="app"):
    """Pin something to a board. `payload` holds the story's own `text_hashes` as they are
    now, for the same reason keep-unread does."""
    state = _load_state()
    pin_id = _new_id("p")
    now = _now()
    state["pins"].append({"pin_id": pin_id, "board_id": board_id, "item_type": item_type,
                          "payload": payload, "source": source, "created_by": created_by,
                          "created_at": now})
    for b in state["boards"]:
        if b["board_id"] == board_id:
            b["last_used_at"] = now
    _save_state(state)
    return pin_id


def unpin(pin_id):
    state = _load_state()
    state["pins"] = [p for p in state["pins"] if p["pin_id"] != pin_id]
    _save_state(state)


def list_pins(board_id):
    return [p for p in _load_state()["pins"] if p["board_id"] == board_id]


# ------------------------------------------------------------- feedback, prefs (R1)

def feedback(target_kind, target_id, text_hashes, vote, note=None):
    """A 👍/👎 on a story, or "Wrong link?" on a thread link. Stored, never acted on."""
    state = _load_state()
    state["feedback"].append({"target_kind": target_kind, "target_id": target_id,
                              "text_hashes": list(text_hashes or []), "vote": vote,
                              "note": note, "at": _now()})
    _save_state(state)


def list_feedback():
    """The latest vote on each thing voted on. Added to request R1 by this build.

    §4 gives `feedback` and no way to ask what was already said. Without it the page
    cannot show that a story has been rated, so a reader has no way to tell a vote
    landed and votes the same story again and again -- which Kunaal did, and said so.
    Keyed "<kind>:<id>", latest wins, because changing your mind is allowed.
    """
    latest = {}
    for row in _load_state()["feedback"]:
        latest[f'{row["target_kind"]}:{row["target_id"]}'] = {
            "vote": row["vote"], "note": row.get("note"), "at": row["at"]}
    return latest


def get_pref(key):
    return _load_state()["prefs"].get(key)


def set_pref(key, value):
    state = _load_state()
    state["prefs"][key] = value
    _save_state(state)


# ---------------------------------------------------------------- visits, cost (R1)

def record_visit(digest_ref, window, opened_story=False):
    """Called when a digest is SHOWN; called again with opened_story=True once the reader
    opens any story, so an accidental open does not reset "since last visit". Re-opening
    the same digest within the knob's minutes is the same sitting, not a new visit."""
    state = _load_state()
    now = _now()
    last = state["visits"][-1] if state["visits"] else None
    same = False
    if last and last.get("digest_ref") == digest_ref:
        then = dt.datetime.fromisoformat(last["at"])
        same = (dt.datetime.fromisoformat(now) - then
                ).total_seconds() <= KNOBS["visit_same_sitting_minutes"] * 60
    if same:
        last["opened_story"] = bool(last.get("opened_story") or opened_story)
        last["at"] = now
    else:
        state["visits"].append({"digest_ref": digest_ref, "window": window,
                                "opened_story": bool(opened_story), "at": now})
    _save_state(state)


def last_visit(opened_only=True):
    """When the reader was last here. THE PAGE READS THIS BEFORE record_visit() on load,
    because once this visit is recorded it is itself the last one — keeping that ordering
    in the page leaves this function a plain maximum, with no notion of "current"."""
    visits = _load_state()["visits"]
    times = [v["at"] for v in visits if v.get("opened_story") or not opened_only]
    return max(times) if times else None


def cost_today():
    """{spent_usd, cap_usd}. Added to request R1 by this build (register C2).
    Spend = the write-up and story-so-far costs of the latest digest, which is the only
    record of what the model was asked for. The cap is the digest's own knob and is never
    hard-coded (§A8); a digest without that knob has no cap to show (decision D1)."""
    files = _digest_files()
    latest = files[0][2] if files else {}
    spent = 0.0
    for story in latest.get("stories", []):
        spent += (story.get("writeup") or {}).get("cost_usd") or 0.0
        spent += (story.get("story_so_far") or {}).get("cost_usd") or 0.0
    cap = (latest.get("knobs") or {}).get("daily_cost_cap_usd")
    if cap is None:
        cap = KNOBS["cap_usd_when_knob_absent"]
    if FIXTURES["cap_reached"]:
        cap = cap if cap is not None else 1.0
        spent = cap                                  # H5: the cap is spent
    return {"spent_usd": round(spent, 6), "cap_usd": cap,
            "fixture": bool(FIXTURES["cap_reached"])}


# =========================================================== v0.1 · SEARCH (§4.1)
#
# K3 implements this (contract v2 §6) over `data/search.db`, 10,912 passages. Here the
# corpus is every story and coverage row of the two digests -- 19 stories, 87 rows --
# which is small on purpose: it is enough to produce every verdict and every screen
# state, and far too small to be mistaken for the real index.

def _search_corpus():
    """Every story of every digest, newest digest first, one entry per story_id.

    One story_id really is in both files (`2026-10-11:56eaf7aa4d5b`, the 11 Oct day
    digest and the 9-15 Oct week overlap), so without the de-duplication it would be
    two results that open the same story. The newest digest's copy wins, because that
    is the one `get_digest("latest")` would hand the page."""
    seen = set()
    out = []
    for _stem, _path, obj in _digest_files():
        for story in obj.get("stories", []):
            sid = story.get("story_id")
            if sid in seen:
                continue
            seen.add(sid)
            out.append(story)
    return out


def _story_text(story):
    """The words a query is matched against: headline + one-liner + its rows' headlines.
    Fixture rule (§4.1). The real search matches passage text, which is not in the JSON."""
    bits = [(story.get("headline") or {}).get("text") or "",
            (story.get("one_liner") or {}).get("text") or ""]
    bits += [r.get("headline") or "" for r in story.get("coverage", {}).get("rows", [])]
    return " ".join(bits)


def _query_words(query):
    """Lower-cased words of at least the knob's length -- unless NONE of them reaches it.

    The brief's rule is words of three letters or more, and the reason is that "to" and
    "of" match inside every headline. But "AI" is a real topic in this archive (it is in
    `facets()["topics"]`), so dropping every short word would turn the query "AI" into
    an empty one, and the page would quietly show a browse instead of an answer. So the
    short words are dropped only when there is a longer one to search with."""
    words = re.findall(r"[a-z0-9]+", str(query or "").lower())
    long_enough = [w for w in words if len(w) >= KNOBS["search_word_min_letters"]]
    return long_enough or words


def _word_hits(words, text):
    """How many of the query's words appear in the text. Counted once per word, not per
    occurrence: a headline that says "poll" three times is not three times the answer.

    A word of the knob's length or more matches inside a longer one, so "protest" finds
    "protests" and "protesters" -- worth having, and the real search stems anyway. A
    shorter word has to be a whole word, or "ai" would match "said", "again" and "aid"
    and the score would mean nothing."""
    low = text.lower()
    hits = 0
    for w in words:
        if len(w) >= KNOBS["search_word_min_letters"]:
            hits += 1 if w in low else 0
        else:
            hits += 1 if re.search(r"\b" + re.escape(w) + r"\b", low) else 0
    return hits


def _desc(text):
    """Sort a string descending inside an otherwise ascending key. Dates are ISO, so
    inverting each character's code point is enough and keeps the key a plain tuple."""
    return tuple(-ord(c) for c in text)


def _row_ok(row, story, filters):
    """Does one coverage row pass the filters? Dates, papers and kinds are the row's own;
    `topics` is the story's theme topic, which every row of that story shares."""
    if filters.get("date_from") and (row.get("date") or "") < filters["date_from"]:
        return False
    if filters.get("date_to") and (row.get("date") or "") > filters["date_to"]:
        return False
    if filters.get("papers") and row.get("paper") not in filters["papers"]:
        return False
    if filters.get("kinds") and row.get("kind") not in filters["kinds"]:
        return False
    if filters.get("topics"):
        if (story.get("theme") or {}).get("topic") not in filters["topics"]:
            return False
    return True


def _article_row(row):
    """One article in a search result, in the contract's shape. `snippet` is the row's
    headline: the real one is the matching passage, which this corpus does not hold, so
    it is marked as the fixture it is in the hand-off rather than invented prose."""
    link = row.get("page_link") or {}
    return {"text_hash": row.get("text_hash"),
            "headline": row.get("headline"),
            "paper": row.get("paper"),
            "date": row.get("date"),
            "kind": row.get("kind"),
            # K3's rows carry no `paper` inside page_link; §4.1 asks for one, and the
            # page needs it to print "<code> PDF p<N>" (same addition as _page_link).
            "page_link": _page_link(link.get("pdf"), link.get("pdf_page"),
                                    row.get("paper")),
            "snippet": row.get("headline")}


def search(query, filters=None, sort="relevance", limit=20):
    """Search the archive. K3 implements this (contract v2 §6).

    Empty query is BROWSE: everything the filters allow, newest first, no score, and
    `verdict` None -- a browse has nothing to be thin about. With a query, the fixture
    score is how many of its words are in a story's headline, one-liner and row
    headlines, and the verdict comes from how many stories matched at all."""
    filters = dict(filters or {})
    sort = "newest" if sort == "newest" else "relevance"
    words = _query_words(query)
    browse = not words
    exclude = set(filters.get("exclude_story_ids") or [])

    searched_dates = set()
    hits = []
    for story in _search_corpus():
        rows = [r for r in story.get("coverage", {}).get("rows", [])
                if _row_ok(r, story, filters)]
        # Every day with something in it to search counts as searched, even if this
        # story is excluded or does not match: "searched N days" is about the window
        # the question was asked of, not about what came back.
        searched_dates.update(r.get("date") for r in rows if r.get("date"))
        if not rows or story.get("story_id") in exclude:
            continue
        score = None if browse else float(_word_hits(words, _story_text(story)))
        if not browse and not score:
            continue
        # Articles are ranked by the same word count on their own headline, then by
        # date (newest first) so the order is stable when nothing matches -- which is
        # the browse case, where every row scores zero.
        ranked = sorted(rows, key=lambda r: (-_word_hits(words, r.get("headline") or ""),
                                             _desc(r.get("date") or ""),
                                             r.get("paper") or ""))
        shown = [_article_row(r) for r in ranked[:KNOBS["search_articles_shown"]]]
        dates = sorted({r.get("date") for r in rows if r.get("date")})
        hits.append({"story_id": story.get("story_id"),
                     "headline": (story.get("headline") or {}).get("text") or "",
                     "dates": dates,
                     "papers": len({r.get("paper") for r in rows}),
                     "score": score,
                     "articles": shown,
                     "_articles_total": len(rows),
                     "_latest": dates[-1] if dates else ""})

    if sort == "newest" or browse:
        hits.sort(key=lambda h: (_desc(h["_latest"]), h["headline"]))
    else:
        hits.sort(key=lambda h: (-(h["score"] or 0.0), _desc(h["_latest"]), h["headline"]))

    total_articles = sum(h["_articles_total"] for h in hits)
    for h in hits:
        del h["_articles_total"]
        del h["_latest"]

    if browse:
        verdict = None
    elif len(hits) >= KNOBS["verdict_enough_min"]:
        verdict = "enough"
    elif hits:
        verdict = "thin"
    else:
        verdict = "empty"

    return {"query": query, "filters": filters,
            "sort": "newest" if browse else sort,
            "verdict": verdict,
            "searched_days": len(searched_dates),
            "total_stories": len(hits),
            "total_articles": total_articles,
            "stories": hits[:max(0, int(limit))],
            "fixture": True}


def facets():
    """The values the Explore filters offer. K3 implements this (contract v2 §6).
    Taken from the corpus itself, so a filter can never offer a value with nothing
    behind it. `topics` is the stories' theme topic -- K3's stories carry no `topics`
    field of their own (measured 2 Nov: absent on all 20)."""
    papers, kinds, topics, dates = set(), set(), set(), set()
    for story in _search_corpus():
        topic = (story.get("theme") or {}).get("topic")
        if topic:
            topics.add(topic)
        for row in story.get("coverage", {}).get("rows", []):
            if row.get("paper"):
                papers.add(row["paper"])
            if row.get("kind"):
                kinds.add(row["kind"])
            if row.get("date"):
                dates.add(row["date"])
    return {"papers": sorted(papers), "topics": sorted(topics), "kinds": sorted(kinds),
            "date_min": min(dates) if dates else None,
            "date_max": max(dates) if dates else None}
