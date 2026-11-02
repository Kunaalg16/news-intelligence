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
import time
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

    # --- v0.1 the ask pane (§4.2-§4.4) --------------------------------------
    # Prices per million tokens, from K3's note §7 (2 Nov 2026). Here they are only the
    # arithmetic behind the fixture costs below; K3's real functions bill real tokens.
    "price_per_m": {"haiku": {"in": 0.10, "out": 0.50},
                    "sonnet": {"in": 2.00, "out": 10.00}},
    # What a fixture call "costs", so the cost lines and the cap work end to end. These
    # are ESTIMATES of the real thing (brief §3): an answer over ~8 passages on Haiku,
    # the same on Sonnet, a suggestion set, a background paragraph.
    "fixture_cost_usd": {"ask": 0.0005, "think_harder": 0.011,
                         "background": 0.0003, "suggest": 0.0005},
    # Milliseconds between streamed sentence events, so streaming is visible in the
    # browser. The tests set it to 0; nothing but the eye needs it.
    "fixture_stream_delay_ms": 60,
    # How many stories an answer searches over. The pane shows five (§8.3 state 1) and
    # offers "Show all N in Explore" for the rest.
    "ask_search_limit": 20,
    # False: one conversation runs until "New conversation", across restarts (§4.4).
    # True starts a fresh one on the first call of a new day.
    "conversation_new_each_day": False,
    # Suggestions are fetched only when the button is pressed (D4), never on open.
    "suggest_auto": False,
    # Decision D11 (2 Nov): the set is never padded. §4.4's old "3-5" is withdrawn --
    # a short set is the honest answer, so this is a ceiling with no floor under it.
    "suggest_max": 5,
    # D11 · the relevance bar one suggestion has to clear: it must share at least ONE
    # word with the story that is NOT in more than this fraction of the corpus's
    # stories. Below the bar there is nothing to say about the row, so it is not shown
    # and the set comes back short -- 0 to `suggest_max`. Measured 2 Nov over the 19
    # stories: "and" and "the" are in 16 of them and "for" in 14, all far above half,
    # while "gst", "crore" and "ending" are in 1-3. What would change it: a corpus big
    # enough that half of it is no longer a meaningful share.
    "common_word_share": 0.5,
    # How many search hits the bar is applied to. The bar is STRICTER than the search's
    # own "shares at least one word of the query", so filtering the top five would
    # return two suggestions on a story that has five qualifying ones further down.
    # Twenty is the whole corpus today (19 stories); what would change it is an archive
    # wider than one screen.
    "suggest_candidates": 20,
    # A word held by more than this fraction of the corpus is not worth NAMING as the
    # reason two stories go together, however rare it is among the rest. Measured on the
    # GST story, 2 Nov: "for" is in 14 of the 19 stories and "and" and "the" in 16, so
    # ranking by rarity alone still produced "shares the words 'for', 'and'" -- which
    # reads as a reason and is not one. Words in 1-3 stories ("gst", "crore", "ending")
    # are the ones that mean something. What would change it: a corpus big enough that
    # the fraction wants to be smaller.
    "suggest_why_max_spread": 0.4,
    # What one press of "Allow $1 more today" adds, and the ceiling on how often it can
    # be pressed in a day -- presses add up (D2), but not without end.
    "cap_override_usd": 1.00,
    "cap_override_max_per_day": 5,

    # --- v0.1 the answer's stored results (decision D12) --------------------
    # How many result rows an answer keeps for after a reload: the number the pane
    # shows (§8.3 state 1), plus every cited story on top of them however far down the
    # list it sat. The page has the same number as `PANE_RESULTS_SHOWN` in app.js and
    # the suite checks the two against each other, because a page that shows six rows
    # and an answer that remembers five would lose a row on reload and nothing else
    # would say so.
    "pane_results_shown": 5,

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
    # H4 / state 9: False is off; True means "model_busy"; or name the reason outright --
    # model_busy · no_network · key_rejected · timeout · checks_failed · other (§4.2).
    "assistant_error": False,
}


# ------------------------------------------------------------------ small helpers

def _now():
    return dt.datetime.now().isoformat(timespec="seconds")


def _today():
    """Today's local date. The cap override and the fixture spend are both per-day, and
    the day that matters is the reader's own, not UTC's."""
    return dt.date.today().isoformat()


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
            "pins": [], "feedback": [], "prefs": {}, "visits": [],
            # --- v0.1 ---------------------------------------------------------
            "conversations": [],       # [{conversation_id, started_at, turns}]
            "current_conversation_id": None,
            "answers": {},             # answer_id -> the answer object
            "passage_sets": {},        # passage_set_id -> [the articles it was built on]
            "spend": [],               # [{at, date, kind, cost_usd}] -- fixture spend
            "cap_overrides": [],       # [{date, amount_usd, at}] -- one-day presses (D2)
            "user_links": []}          # R13: [{link_id, from_hashes, target_*, at}]


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


def _user_entries(story):
    """R13 · one thread entry per link the reader made out of this story (contract v2.1).

    `added_by` is "you" and the node is drawn differently (§8.4), because the one thing
    this entry must never do is look like something the pipeline found. Date, headline,
    pages and counts all come from the TARGET story's own coverage rows -- real data, so
    these entries are the only ones in the thread that are not fixtures."""
    links = list_user_links(_story_hashes(story))
    if not links:
        return []
    by_id = {s.get("story_id"): s for s in _search_corpus()}
    out = []
    for link in links:
        target = by_id.get(link["target_story_id"])
        if target is None:
            continue
        rows = target.get("coverage", {}).get("rows", [])
        pages, papers = [], []
        for row in rows:
            page = dict(row.get("page_link") or {})
            page["paper"] = row.get("paper")
            pages.append(page)
            papers.append(row.get("paper"))
        dates = sorted({r.get("date") for r in rows if r.get("date")})
        out.append({
            "entry_id": f"user:{link['link_id']}",
            "link_id": link["link_id"],
            "date": dates[-1] if dates else (target.get("dates") or [""])[-1],
            "link_type": "user",
            "added_by": "you",
            "parent_id": None,
            "story_id": target.get("story_id"),
            "headline": (target.get("headline") or {}).get("text") or "",
            "one_liner": (target.get("one_liner") or {}).get("text") or "",
            "articles": len(rows),
            "papers": len(set(papers)),
            "paper_names": sorted(set(p for p in papers if p)),
            "pages": pages,
            "fixture": False,
        })
    return out


def thread(thread_id):
    """A story's thread: its days, and one entry per day. K3 implements this (request R2).
    INVENTED and flagged: K2's typed links need the judge (B1), so until then the entries
    here exercise every link type the drawing has to handle. Page links are real.

    R13: the reader's own links are entries too, and a story with NO thread of its own
    that has been given one now HAS a thread -- its own day, plus what the reader added.
    That is the whole point of the feature: the reader can make a thread the pipeline
    did not see."""
    story = _thread_story(thread_id)
    if story is None:
        return None
    mine = _user_entries(story)
    days, runup = _thread_days(story)
    if not days:
        if not mine:
            return None                              # a day-1 story has no thread (H3)
        days, runup = 1, False                       # its own day, plus the reader's
    dates = _thread_dates(story, days, runup)
    if len(dates) < KNOBS["min_thread_days"] and not mine:
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

    # The reader's entries take their place by date among the days. The header counts
    # include them (R13): a thread you added to is a longer thread.
    day_count = len(entries)
    entries = sorted(entries + mine, key=lambda e: e["date"], reverse=True)
    all_papers = sorted({p for e in entries for p in e["paper_names"]})
    return {"thread_id": thread_id,
            "days": day_count,
            "entries_added_by_you": len(mine),
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
    """Contract v2.2: a pin that is not there is a LookupError, not a quiet no-op. The
    route turns that into a 404 by the exception's class."""
    state = _load_state()
    kept = [p for p in state["pins"] if p["pin_id"] != pin_id]
    if len(kept) == len(state["pins"]):
        raise LookupError(f"no such pin: {pin_id!r}")
    state["pins"] = kept
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
    """{spent_usd, cap_usd, override_usd}. Added to request R1 by this build (C2).

    Spend = the write-up and story-so-far costs of the latest digest (v0) PLUS what
    today's fixture calls have "cost" (§4.3), so the cap states work end to end. The cap
    is the digest's own knob, never hard-coded (§A8), plus today's override (§4.3 /
    decision D2 of the brief); a digest without that knob has no cap to show (v0 D1).

    With the `cap_reached` fixture on, spend is pushed up to the knob cap and NOT to the
    overridden one -- otherwise pressing "Allow $1 more today" would raise the cap and
    the spend with it, and the composer would never come back (acceptance check 10)."""
    files = _digest_files()
    latest = files[0][2] if files else {}
    spent = 0.0
    for story in latest.get("stories", []):
        spent += (story.get("writeup") or {}).get("cost_usd") or 0.0
        spent += (story.get("story_so_far") or {}).get("cost_usd") or 0.0
    base_cap = (latest.get("knobs") or {}).get("daily_cost_cap_usd")
    if base_cap is None:
        base_cap = KNOBS["cap_usd_when_knob_absent"]
    state = _load_state()
    today = _today()
    override = sum(o["amount_usd"] for o in state["cap_overrides"]
                   if o.get("date") == today)
    if FIXTURES["cap_reached"]:
        base_cap = base_cap if base_cap is not None else 1.0
        spent = base_cap                             # H5: the knob cap is spent
    else:
        spent += sum(e["cost_usd"] for e in state["spend"] if e.get("date") == today)
    cap = None if base_cap is None else round(base_cap + override, 6)
    return {"spent_usd": round(spent, 6), "cap_usd": cap,
            "override_usd": round(override, 6),
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
    # `story_ids` and `text_hashes` are K1's additions to the filter vocabulary (§4.1
    # names only the other five). The pane's scope needs them: an open story is one
    # story_id, and Explore's "Ask about these N" is N of them. Logged for K3 as C2.
    if filters.get("story_ids") and story.get("story_id") not in filters["story_ids"]:
        return False
    if filters.get("text_hashes") and row.get("text_hash") not in filters["text_hashes"]:
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


# ================================================= v0.1 · THE ASK PANE (§4.2-§4.4)
#
# K3 implements all of this (contract v2 §7-§9) with real passages and real model calls.
# Nothing here calls a model or reaches a network: an "answer" is built out of the
# headlines the fixture search found, so every cite is a real text_hash and every state
# of the pane can be produced from a real query. Each answer carries "fixture": True and
# the page prints its "sample data" tag.

MODEL_IDS = {"haiku": "claude-haiku-5-5", "sonnet": "claude-sonnet-5-5"}

# Which reasons the reader can retry. key_rejected and checks_failed cannot be fixed by
# pressing the button again, so state 9 shows no Retry on them (§4.5 rule 12).
ERROR_RETRYABLE = {"model_busy": True, "no_network": True, "timeout": True,
                   "other": True, "key_rejected": False, "checks_failed": False}

ERROR_MESSAGES = {
    "model_busy": "the model service is busy",
    "no_network": "no internet connection",
    "key_rejected": "the API key was rejected",
    "timeout": "the model took too long",
    "checks_failed": "the answer didn't pass the source checks twice",
    "other": "unexpected error",
}


def estimate_cost(kind, answer_id=None):
    """What a call would cost, in USD. K3 implements this (contract v2 §7).
    `answer_id` is accepted because K3's Think-harder estimate depends on how many
    passages the parent answer used; the fixture cost does not vary."""
    costs = KNOBS["fixture_cost_usd"]
    if kind not in costs:
        raise ValueError(f"unknown cost kind: {kind!r}")
    return float(costs[kind])


def _record_spend(state, kind, cost_usd):
    """Add a fixture call's cost to today's spend, so `cost_today()` moves by exactly
    that much (§4.5 rule 11) and the cap is reachable by asking questions."""
    if cost_usd:
        state["spend"].append({"at": _now(), "date": _today(), "kind": kind,
                               "cost_usd": round(float(cost_usd), 6)})


def raise_cap_today(amount_usd=None):
    """Allow more spending for today only, until local midnight (§4.3, brief D2).
    Presses add up; every one is logged in the state file with its date, so tomorrow the
    cap is the knob again with nothing carried over."""
    amount = KNOBS["cap_override_usd"] if amount_usd is None else float(amount_usd)
    state = _load_state()
    today = _today()
    pressed = [o for o in state["cap_overrides"] if o.get("date") == today]
    if len(pressed) >= KNOBS["cap_override_max_per_day"]:
        raise ValueError("today's cap has been raised as often as the knob allows")
    state["cap_overrides"].append({"date": today, "amount_usd": amount, "at": _now()})
    _save_state(state)
    return cost_today()


# ------------------------------------------------------------- the conversation (R12)

def _conversation(state):
    """The running conversation inside an already-loaded state, creating it if there is
    none. Kept across restarts because it lives in the state file and nothing but
    `new_conversation()` replaces it (§4.4; §4.5 rule 8)."""
    cid = state.get("current_conversation_id")
    conv = next((c for c in state["conversations"]
                 if c["conversation_id"] == cid), None)
    stale = (conv is not None and KNOBS["conversation_new_each_day"]
             and (conv.get("started_at") or "")[:10] != _today())
    if conv is None or stale:
        conv = {"conversation_id": _new_id("conv"), "started_at": _now(), "turns": []}
        state["conversations"].append(conv)
        state["current_conversation_id"] = conv["conversation_id"]
    return conv


def current_conversation():
    """{conversation_id, started_at, turns}. K3 implements this (contract v2 §9, R12)."""
    state = _load_state()
    before = state.get("current_conversation_id")
    conv = _conversation(state)
    if before != state.get("current_conversation_id"):
        _save_state(state)
    return conv


def new_conversation():
    """Start a fresh conversation; the old one stays in the state file (no list in v1)."""
    state = _load_state()
    conv = {"conversation_id": _new_id("conv"), "started_at": _now(), "turns": []}
    state["conversations"].append(conv)
    state["current_conversation_id"] = conv["conversation_id"]
    _save_state(state)
    return conv["conversation_id"]


def add_divider(conversation_id, story_id, headline):
    """"NOW READING · <HEADLINE>" in the conversation (§8.3 state 7). The page calls this
    when the reader opens a different story while the conversation has turns."""
    state = _load_state()
    conv = _conversation(state)
    if conversation_id and conversation_id != conv["conversation_id"]:
        conv = next((c for c in state["conversations"]
                     if c["conversation_id"] == conversation_id), conv)
    turn = {"kind": "divider", "story_id": story_id, "headline": headline, "at": _now()}
    conv["turns"].append(turn)
    _save_state(state)
    return turn


# ------------------------------------------------------------------ the answer itself

def _scope_filters(scope):
    """A scope becomes search pre-filters (§4.2). The open story is one story_id; a
    selection from Explore is several, carried in `filters.story_ids`."""
    scope = dict(scope or {})
    filters = dict(scope.get("filters") or {})
    if scope.get("story_id") and not filters.get("story_ids"):
        filters["story_ids"] = [scope["story_id"]]
    if scope.get("text_hashes") and not filters.get("text_hashes"):
        filters["text_hashes"] = list(scope["text_hashes"])
    return filters


def _passage_set(result):
    """Every article the answer was allowed to cite, and a stable id for that set.
    Think harder must reuse it unchanged (§4.5 rule 4), which is the whole point of
    naming it: the second answer is a better reading of the SAME passages."""
    arts = [dict(a, story_id=s["story_id"])
            for s in result["stories"] for a in s["articles"]]
    ids = sorted(a["text_hash"] for a in arts)
    return arts, "ps_" + _fixture_hash(*ids) if ids else "ps_empty"


def _compact_results(stories, cited, limit):
    """Decision D12 · the result rows an answer keeps, so a reloaded answer draws the
    same "FOUND · N STORIES" list it drew when it streamed.

    Compact on purpose: story_id, headline, dates, papers and the mark, and no article
    rows -- the whole search result is tens of kilobytes and lands in the state file
    once per question. The rows kept are the ones the pane shows (`limit`) plus every
    story the answer actually cited, however far down the list it sat: a CITED row that
    the reader can see before a reload and not after would be the same lost row the
    decision is about.

    `mark` is computed here rather than on the page because the page no longer has the
    article hashes after a reload. "cited" means one of this story's shown articles is
    in the answer's `cited` list; everything else is "found"."""
    cited = set(cited or [])
    out = []
    for i, story in enumerate(stories):
        hashes = {a.get("text_hash") for a in story.get("articles") or []}
        mark = "cited" if hashes & cited else "found"
        if i >= limit and mark != "cited":
            continue
        out.append({"story_id": story.get("story_id"),
                    "headline": story.get("headline"),
                    "dates": list(story.get("dates") or []),
                    "papers": story.get("papers"),
                    "mark": mark})
    return out


def _fixture_sentences(result, lead="Sample answer"):
    """Two or three sentences built out of the headlines the search found, each citing
    the real text_hashes behind it (§4.2). Never invented prose about the news: the
    sentence says what it is, and the citation is the part that has to be right."""
    stories = result["stories"][:3]
    picks = [(s, s["articles"][:2]) for s in stories]
    if len(picks) == 1:
        # A thin answer is one story, and one sentence is not an answer. Its own
        # articles carry the second sentence.
        story = stories[0]
        arts = story["articles"]
        picks = [(story, arts[:1]), (story, arts[1:2] or arts[:1])]
    out = []
    for i, (story, arts) in enumerate(picks):
        papers = ", ".join(sorted({a["paper"] for a in arts if a.get("paper")}))
        when = max(story["dates"]) if story["dates"] else ""
        opener = lead if i == 0 else f"{lead}, continued"
        out.append({"text": f"{opener} · {story['headline']} ({papers}, {when}).",
                    "cites": [a["text_hash"] for a in arts]})
    return out


def _closest(exclude_ids=()):
    """The three stories shown when nothing matched (§8.3 state 5). The fixture rule is
    the top three of a browse, which is newest first."""
    browse = search("", filters={"exclude_story_ids": list(exclude_ids)}, limit=3)
    return browse["stories"]


def _error_reason():
    """The `assistant_error` switch's value picks the reason; True means model_busy."""
    switch = FIXTURES["assistant_error"]
    if switch is True:
        return "model_busy"
    return switch if switch in ERROR_RETRYABLE else "other"


def _error_event(reason):
    return {"type": "error", "code": "model_error", "reason": reason,
            "retryable": ERROR_RETRYABLE[reason],
            "message": ERROR_MESSAGES[reason], "fixture": True}


def _cap_blocks(kind):
    """Is there room for one more call of this kind? Returns the cost_today object when
    there is not, so the caller can say what the cap and the spend are."""
    cost = cost_today()
    if cost["cap_usd"] is None:
        return None                                  # no cap in the data, no cap to hit
    if cost["spent_usd"] + estimate_cost(kind) > cost["cap_usd"]:
        return cost
    return None


def _wants_background(state):
    """§4.2: an "enough" answer carries a fixture background line on every second one,
    so both renderings — with the dashed block, and with the Background button (D1) —
    are on the screen without a fixture switch."""
    made = sum(1 for a in state["answers"].values()
               if a.get("verdict") == "enough" and not a.get("parent_answer_id"))
    return made % 2 == 0


def _sleep_between_sentences():
    delay = KNOBS["fixture_stream_delay_ms"]
    if delay:
        time.sleep(delay / 1000.0)


def ask(question, scope=None, conversation_id=None):
    """Answer a question from the archive. K3 implements this (contract v2 §7).

    A generator of events: `results` always first, then zero or more `sentence`s, then
    `done` -- or `error` instead of `done`. The page draws the results list the moment
    the first event arrives, which is why the order is a behaviour rule (§4.5 rule 1)
    rather than an implementation detail."""
    filters = _scope_filters(scope)
    result = search(question, filters=filters, limit=KNOBS["ask_search_limit"])
    yield {"type": "results", "search": result}

    blocked = _cap_blocks("ask")
    if blocked:
        yield {"type": "error", "code": "cap_reached",
               "message": (f"Daily cap ${blocked['cap_usd']:.2f} reached · spent "
                           f"${blocked['spent_usd']:.2f} today"),
               "retryable": False, "fixture": True}
        return

    if FIXTURES["assistant_error"]:
        yield _error_event(_error_reason())
        return

    state = _load_state()
    conv = _conversation(state)
    if conversation_id and conversation_id != conv["conversation_id"]:
        conv = next((c for c in state["conversations"]
                     if c["conversation_id"] == conversation_id), conv)

    arts, passage_set_id = _passage_set(result)
    empty = result["verdict"] == "empty"
    sentences = [] if empty else _fixture_sentences(result)
    background = None
    if not empty and result["verdict"] == "enough" and _wants_background(state):
        background = ("Sample background · this paragraph is general knowledge standing "
                      "in for K3's own, and is not from your papers.")
    cost = 0.0 if empty else estimate_cost("ask")

    answer = {
        "answer_id": _new_id("ans"),
        "conversation_id": conv["conversation_id"],
        "parent_answer_id": None,
        "question": question,
        "scope": dict(scope or {}),
        "model": "haiku",
        "model_id": MODEL_IDS["haiku"],
        "verdict": result["verdict"],
        "found_count": result["total_articles"],
        # How wide the search actually was. §4.2's answer shape does not name it, and
        # only the `results` event carried it -- so after a restart, when the results
        # are gone (decision D12), state 5 fell back to zero and said "searched 0 days"
        # about a search that covered one. A wrong number is worse than no number, and
        # this is the one screen whose job is to say how wide the search was. Logged for
        # K3 as a contract addition.
        "searched_days": result["searched_days"],
        "sentences": sentences,
        "background": background,
        "cited": sorted({h for s in sentences for h in s["cites"]}),
        "found": [s["story_id"] for s in result["stories"]],
        "closest": _closest() if empty else [],
        # Decision D12. The `results` event is gone after a reload and the pane's list
        # went with it, so the answer carries its own compact copy of the rows, the
        # total they were drawn out of, and how many passages it was allowed to cite
        # ("Sonnet · same N passages"). Logged for K3 as contract additions.
        "results": [],                                      # filled just below
        "results_total": result["total_stories"],
        "passage_count": len(arts),
        "passage_set_id": passage_set_id,
        "cost_usd": round(cost, 6),
        "attempts": 1,             # K3's referee loop; one try, nothing dropped
        "drops": 0,
        "at": _now(),
        "fixture": True,
    }
    answer["results"] = _compact_results(result["stories"], answer["cited"],
                                         KNOBS["pane_results_shown"])
    state["answers"][answer["answer_id"]] = answer
    state["passage_sets"][passage_set_id] = arts
    conv["turns"].append({"kind": "answer", "answer": answer})
    _record_spend(state, "ask", cost)
    _save_state(state)

    for sentence in sentences:
        _sleep_between_sentences()
        yield {"type": "sentence", "text": sentence["text"], "cites": sentence["cites"]}
    yield {"type": "done", "answer": answer}


def think_harder(answer_id):
    """The same passages, read again by the bigger model. K3 implements this (§7).

    No `results` event: the reader is already looking at the list, and re-searching
    could quietly change it under them. The passage set id is the parent's, unchanged,
    and the cites can only come from it (§4.5 rule 4). One-shot: the next `ask()` is
    Haiku again, because nothing here is remembered as a preference."""
    state = _load_state()
    parent = state["answers"].get(answer_id)
    if parent is None:
        raise LookupError(f"no such answer: {answer_id!r}")

    blocked = _cap_blocks("think_harder")
    if blocked:
        yield {"type": "error", "code": "cap_reached",
               "message": (f"Daily cap ${blocked['cap_usd']:.2f} reached · spent "
                           f"${blocked['spent_usd']:.2f} today"),
               "retryable": False, "fixture": True}
        return
    if FIXTURES["assistant_error"]:
        yield _error_event(_error_reason())
        return

    arts = state["passage_sets"].get(parent["passage_set_id"]) or []
    by_story = {}
    for art in arts:
        by_story.setdefault(art["story_id"], []).append(art)
    stories = [{"story_id": sid, "headline": _headline_of(sid),
                "dates": sorted({a["date"] for a in rows if a.get("date")}),
                "papers": len({a["paper"] for a in rows}), "score": None,
                "articles": rows}
               for sid, rows in by_story.items()]
    sentences = _fixture_sentences({"stories": stories}, lead="Sample answer, read again")

    cost = estimate_cost("think_harder")
    cited = sorted({h for s in sentences for h in s["cites"]})
    answer = dict(parent,
                  answer_id=_new_id("ans"),
                  parent_answer_id=parent["answer_id"],
                  model="sonnet",
                  model_id=MODEL_IDS["sonnet"],
                  sentences=sentences,
                  background=None,
                  cited=cited,
                  # D12 · the same search, so the same rows in the same order (the
                  # passage set was built in the parent's result order and kept that
                  # way), but this answer's own cites, so the marks are recomputed
                  # rather than inherited. `passage_count` is the parent's by
                  # construction: these are the same passages, which is the entire
                  # claim Think harder makes.
                  results=_compact_results(stories, cited,
                                           KNOBS["pane_results_shown"]),
                  results_total=parent.get("results_total"),
                  passage_count=len(arts),
                  cost_usd=round(cost, 6),
                  at=_now())
    conv = _conversation(state)
    if parent.get("conversation_id"):
        conv = next((c for c in state["conversations"]
                     if c["conversation_id"] == parent["conversation_id"]), conv)
    state["answers"][answer["answer_id"]] = answer
    conv["turns"].append({"kind": "answer", "answer": answer})
    _record_spend(state, "think_harder", cost)
    _save_state(state)

    for sentence in sentences:
        _sleep_between_sentences()
        yield {"type": "sentence", "text": sentence["text"], "cites": sentence["cites"]}
    yield {"type": "done", "answer": answer}


def _headline_of(story_id):
    story = _find_story(lambda s: s.get("story_id") == story_id)
    return (story.get("headline") or {}).get("text") or "" if story else ""


def ask_background(answer_id):
    """A paragraph from the model's own knowledge, on request (§4.3; brief D1).

    For ANY answer whose `background` is null -- an empty one, a thin one, an "enough"
    one that did not come with a line. It errors when the answer already has one, which
    is what keeps the button from appearing twice (§4.5 rule 6)."""
    state = _load_state()
    answer = state["answers"].get(answer_id)
    if answer is None:
        raise LookupError(f"no such answer: {answer_id!r}")
    if answer.get("background"):
        raise ValueError("that answer already has a background line")

    text = ("Sample background · this paragraph is general knowledge standing in for "
            "K3's own. It is not from your papers and carries no citations.")
    cost = estimate_cost("background")
    answer["background"] = text
    out = {"answer_id": answer_id, "text": text, "model": "haiku",
           "model_id": MODEL_IDS["haiku"], "cost_usd": round(cost, 6), "fixture": True}
    conv = next((c for c in state["conversations"]
                 if c["conversation_id"] == answer.get("conversation_id")), None)
    if conv is None:
        conv = _conversation(state)
    conv["turns"].append({"kind": "background", "answer_id": answer_id, "text": text,
                          "cost_usd": out["cost_usd"], "at": _now()})
    # The stored answer is the one the conversation replays on restart, so the turn that
    # holds it has to be updated too, not only the answers table.
    for turn in conv["turns"]:
        if turn.get("kind") == "answer" and turn["answer"]["answer_id"] == answer_id:
            turn["answer"]["background"] = text
    _record_spend(state, "background", cost)
    _save_state(state)
    return out


# ------------------------------------------------- suggest connections (R11) and R13

def _thread_story_ids(story):
    """The stories this one's thread already covers, so a suggestion cannot offer one of
    them (§4.5 rule 9). The fixture thread's entries are invented days of the SAME story,
    so in this corpus that is the story itself plus anything sharing a group id -- which
    is nothing today (measured: no group_id is shared by two stories), and the check is
    here for K2's real threads, where it will be."""
    ids = {story.get("story_id")}
    groups = set(story.get("group_ids") or [])
    if groups:
        for other in _search_corpus():
            if groups & set(other.get("group_ids") or []):
                ids.add(other.get("story_id"))
    thread_obj = thread(story.get("story_id"))
    for entry in (thread_obj or {}).get("entries", []):
        ids.add(entry.get("entry_id"))
    return ids


def suggest_connections(story_id, text_hashes=None):
    """Other stories that may belong with this one. K3 implements this (contract v2 §8).

    The query is the story's own headline and one-liner; its own thread is excluded. It
    WRITES NOTHING but the cost: a suggestion is a reading, and only the reader's + turns
    it into a thread link (R13)."""
    story = _find_story(lambda s: s.get("story_id") == story_id)
    if story is None:
        raise LookupError(f"no such story: {story_id!r}")

    query = " ".join([(story.get("headline") or {}).get("text") or "",
                      (story.get("one_liner") or {}).get("text") or ""])
    exclude = _thread_story_ids(story)
    # The bar below is stricter than the search's own "shares a word", so the candidates
    # are the whole corpus's worth of hits, not the top five. Filtering five would hand
    # back two on a story whose fifth qualifying suggestion sat sixth in the search.
    found = search(query, filters={"exclude_story_ids": sorted(exclude)},
                   limit=KNOBS["suggest_candidates"])
    rows = list(found["stories"])

    words = _query_words(query)
    by_id = {s.get("story_id"): s for s in _search_corpus()}
    # Two bars over the same measurement, `spread` -- how many of the corpus's stories
    # hold a word.
    #
    # `common_word_share` (0.5) decides whether a row is SHOWN AT ALL (decision D11). A
    # story whose only overlap is words half the archive uses is not a connection; it is
    # a coincidence of English, and the old code padded the set with exactly those rows
    # under a `why` that admitted as much in so many words. That path and that wording
    # are both gone -- the suite greps for the words, so they cannot come back as a
    # one-line fallback -- and the set is 0 to `suggest_max`, short when the archive is.
    #
    # `suggest_why_max_spread` (0.4) is tighter and decides only what gets NAMED, which
    # is the older measurement: ranking by rarity alone still produced "shares the words
    # 'for', 'and'". When a row clears the first bar but nothing clears the second, the
    # named word is the rarest one that got it in -- never nothing, because a row that is
    # here at all has a real shared word to point at.
    texts = {sid: _story_text(st) for sid, st in by_id.items()}
    spread = {w: sum(1 for t in texts.values() if _word_hits([w], t)) for w in words}
    not_common = max(1, int(len(texts) * KNOBS["common_word_share"]))
    nameable = max(1, int(len(texts) * KNOBS["suggest_why_max_spread"]))
    suggestions = []
    for row in rows:
        if len(suggestions) >= KNOBS["suggest_max"]:
            break
        text = texts.get(row["story_id"], "")
        by_rarity = sorted((w for w in words if _word_hits([w], text)),
                           key=lambda w: (spread[w], w))
        clears = [w for w in by_rarity if spread[w] <= not_common]
        if not clears:
            continue                      # D11: below the bar, so not shown at all
        shared = [w for w in clears if spread[w] <= nameable][:2] or clears[:1]
        why = ("Sample reason · shares the "
               + ("words " if len(shared) > 1 else "word ")
               + ", ".join(f"'{w}'" for w in shared))
        suggestions.append({"story_id": row["story_id"], "headline": row["headline"],
                            "dates": row["dates"], "papers": row["papers"],
                            "why": why, "articles": row["articles"]})

    cost = estimate_cost("suggest")
    state = _load_state()
    _record_spend(state, "suggest", cost)
    _save_state(state)
    return {"story_id": story_id, "suggestions": suggestions,
            "verdict": found["verdict"], "model": "haiku",
            "model_id": MODEL_IDS["haiku"], "cost_usd": round(cost, 6), "fixture": True}


def add_user_link(story_id, story_text_hashes, target_story_id, target_text_hashes):
    """R13 · the reader's own thread link. K3 implements this (contract v2.1 §8).
    Stored against the text_hashes on both ends, as every other piece of reader state
    is, so it survives a digest being rebuilt under it."""
    state = _load_state()
    link = {"link_id": _new_id("ulink"),
            "story_id": story_id,
            "from_hashes": sorted(story_text_hashes or []),
            "target_story_id": target_story_id,
            "target_text_hashes": sorted(target_text_hashes or []),
            "at": _now()}
    state["user_links"].append(link)
    _save_state(state)
    return link["link_id"]


def remove_user_link(link_id):
    """Contract v2.2: a link that is not there is a LookupError. v0.1 returned False and
    left the route to decide what that meant; the class says it instead."""
    state = _load_state()
    kept = [l for l in state["user_links"] if l["link_id"] != link_id]
    if len(kept) == len(state["user_links"]):
        raise LookupError(f"no such link: {link_id!r}")
    state["user_links"] = kept
    _save_state(state)
    return True


def list_user_links(story_text_hashes):
    """The reader's own links out of this story, matched on the hashes at this end."""
    wanted = set(story_text_hashes or [])
    out = []
    for link in _load_state()["user_links"]:
        if wanted & set(link["from_hashes"]):
            out.append({"link_id": link["link_id"],
                        "target_story_id": link["target_story_id"],
                        "target_text_hashes": link["target_text_hashes"],
                        "at": link["at"]})
    return out
