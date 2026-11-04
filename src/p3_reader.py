"""p3_reader.py — Phase 3 · K3 · the data layer the app reads (R1-R4, R6, R10, R13).
  v0.1 3 Nov 2026 (K3 B2-2): every reader function of the integration contract §2, §3,
  against the real databases, with the names and shapes of K1's test double.

WHAT IT DOES
  This is the module `src/p3_app.py` imports as its data layer. It replaced K1's test
  double at integration (K1 `4be91dd`); the double is gone. Everything a screen shows
  and everything a reader does goes through here:

      R6   run_status · get_digest · list_digests      what there is to read
      R3   article                                     one article, with its real body
      R2   thread                                      a story's days, and its related items
      R4   new_since_read                              what joined since the reader left
      R1   mark_read · read_state · set_keep_unread · list_keep_unread · follow ·
           list_follows · list_boards · create_board · pin · unpin · list_pins ·
           feedback · list_feedback · get_pref · set_pref · record_visit · last_visit ·
           cost_today · raise_cap_today
      R13  add_user_link · remove_user_link · list_user_links

  Reads  data/news.db, data/vectors.db   READ-ONLY (`file:...?mode=ro`)
         data/intel.db                   K2's tables read-only, through p3_store
         data/digests/*.json             the digests the daily run builds (FP-7, 4 Nov)
         _work/*.json                    the two older digests, until they are moved
  Writes data/intel.db                   K3's own tables ONLY (p3_store, K3 schema 3)

WHY ITS SHAPES ARE NOT NEGOTIABLE
  K1 built eleven screens against the double's signatures before these functions
  existed, and the suite `tests/test_p3_app.py` is the executable statement of what the
  app expects (consumer-driven contract testing: the consumer writes the test, the
  provider passes it). So a name or a key that differs here is not a style choice, it is
  a broken screen. Where this module and the double disagree about a VALUE, the real
  data wins; where they disagree about a SHAPE, the double wins.

  The double returns `"fixture": true` on invented content — bodies and thread entries.
  Everything here is real, so `fixture` is False everywhere, and the page's "sample
  data" tag stops appearing by itself.

WHAT IS STILL THE DOUBLE'S AND NOT MINE
  The ask pane, search and the conversation (contract §6-§8) live in `src/p3_search.py`
  (B2-4 … B2-6). The app has ONE data import, so this module re-exports them when that
  file exists; until it does, the names are simply absent and the ask-pane routes are
  the only ones that cannot be served.

WHAT A STORY ID IS, AND WHY FOLLOWS SURVIVE A RE-CLUSTERING
  A story id is K2's thread root (`date:hash`), recomputed every time grouping runs; a
  `text_hash` is forever (Q8a). So every mark the reader makes is stored with the
  article set it was made against, and a mark whose story id has moved is re-attached by
  article overlap rather than lost. `list_follows` does that re-attachment on the way
  out: the follow belongs to whichever thread now holds most of its articles.

HOW TO RUN
  Normally imported. The two commands are for looking, and open nothing for writing:
      .venv/bin/python src/p3_reader.py status            run_status + the digests
      .venv/bin/python src/p3_reader.py article <hash>    one article, body word count
  Its test:
      .venv/bin/python tests/test_p3_reader.py            mock first, then the real data

  Three environment variables move the files it reads, so a test (or K1's suite) can run
  it against a COPY of intel.db without editing anything:
      P3_READER_INTEL_DB · P3_READER_NEWS_DB · P3_READER_WORK · P3_READER_DIGESTS
  P3_READER_WORK set and P3_READER_DIGESTS not = a test world: `data/digests/` is not
  read, so a mock never picks up a real digest.

KNOBS
  One block below, each with its reason and the measurement that would change it.
"""

import argparse
import collections
import datetime as dt
import json
import os
import sqlite3
import sys
import threading
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

# `WORK` and `ARCHIVE` are module globals, read at call time, for one reason: K1's suite
# aims the data module at its own mock world by REASSIGNING them (`tests/test_p3_app.py`
# `build_mock_world`), and that suite is not ours to edit. Keeping the same names means
# K1's tests can point this module at a temporary directory exactly as they point the
# double at one. The three P3_READER_* environment variables do the same job from
# outside the process, for a shell or another test.

import p3_digest as D          # noqa: E402  — ranking, seats, page links: read-only reuse
import p3_label as L           # noqa: E402  — what counts as coverage
import p3_store as S           # noqa: E402  — K3's tables and connections

WORK = ROOT / "_work"
ARCHIVE = ROOT / "archive"
STATE_FILE = None              # the double's state file; this module stores state in intel.db

READER_VERSION = "reader-1"

# ------------------------------------------------------------------------- KNOBS

KNOBS = {
    # --- threads (R2) -------------------------------------------------------
    # Below this many days a story has no thread at all, and that is an answer, not an
    # error (state H3, K1's double): one day of coverage is a story, not a history.
    # What would change it: Kunaal asking to see a one-day "thread" with its related items.
    "min_thread_days": 2,
    # The real `links.role` vocabulary is `parent` / `side` / `below` (t0.2, 9,092 rows,
    # facts §4) -- there is no typed run-up or follow-up until K2's judge lands. So the
    # thread's first day is labelled `start` (the chip "where it began" resolves to it)
    # and every later day is untyped: "earlier coverage". Nothing here invents a type.
    # What would change it: K2's judge shipping typed links.
    "link_type_first_day": "start",
    "link_type_later_days": None,

    # --- articles (R3) ------------------------------------------------------
    # `placements.byline` holds datelines as well as bylines ('New Delhi:' -- 5,049 of
    # 12,240 rows are filled and the casing is inconsistent, facts §4). A value that ends
    # in a colon and is this short is a place, not a person, so it is returned as the
    # dateline and the byline is left empty rather than printing "BY New Delhi:".
    # What would change it: a real byline of three words or fewer ending in a colon.
    "dateline_max_words": 3,

    # --- visits (R1) --------------------------------------------------------
    # Re-opening the same digest within this many minutes is the same sitting, not a new
    # visit, so a reload does not reset "since last visit". Half an hour is a guess
    # (K1's double has the same number); what would change it is Kunaal noticing the
    # line reset mid-morning.
    "visit_same_sitting_minutes": 30,

    # --- cost (R1) ----------------------------------------------------------
    # The cap is the latest digest's own `daily_cost_cap_usd` knob, never hard-coded
    # (§A8). A digest built without that knob has no cap to show, and the header then
    # omits the cap clause (decision D1). What would change it: Kunaal deciding the app
    # should fall back to a number of its own.
    "cap_usd_when_knob_absent": None,
    # One press of "Allow $1 more today" and how many presses a day allows. Presses add
    # up (contract v2.1 D2) but not without end (v2.2). What would change the ceiling:
    # Kunaal hitting it on a normal day.
    "cap_override_usd": 1.00,
    "cap_override_max_per_day": 5,
    # Every paid purpose the day's spend counts. `cost_today()` must agree exactly with
    # the per-answer cost lines, so this list is the whole set of `cost_log.purpose`
    # values any K3 code writes (contract §7 pass line G4).
    "paid_purposes": ("writeup", "story_so_far", "ask", "think_harder",
                      "ask_background", "suggest"),

    # --- digests (R6) -------------------------------------------------------
    # Where the daily run writes its day and week digests (FP-7, TC-15). Read FIRST; a
    # stem found here hides the same stem in `digest_dir`. A rule, not a tuned number.
    "digest_dir_daily": "data/digests",
    # The two digests built before the daily run did it live here until Kunaal moves
    # them (X10). Kept, and read second, so "Past digests" loses nothing meanwhile; K1's
    # suite also aims its mock world at `ROOT / digest_dir`. What would change it: both
    # files moved -- then this folder holds no digest and costs one empty glob.
    "digest_dir": "_work",
    # A JSON file in `_work/` is a digest only with all three of these keys -- the guard
    # that keeps a stray JSON file out of "Past digests" (behaviour rule 14).
    "digest_required_keys": ("stories", "window", "built_at"),
    # A lock whose process is gone is a crashed run, not a running one. Without this the
    # page would say "today's papers still processing" for ever after a crash, and the
    # only way back would be deleting a file by hand. True honours a stale lock as a run
    # in progress; False (the default) reads the lock's own pid, exactly as the wrapper
    # does when it refuses to start twice. What would change it: a daily run whose steps
    # outlive the process that started them.
    "trust_a_stale_lock": False,
}

# Fixture switches. They produce the three states no real data can be made to show on
# demand (§8.7), and `p3_app.py`'s dev route flips them in place -- so the name has to
# exist here too, or the route breaks the moment the import is swapped. All off in
# normal use, and each one is honoured by the function it belongs to.
FIXTURES = {
    "processing": False,      # H1: today's papers still processing
    "cap_reached": False,     # H5: the daily cost cap is spent
    "nothing_new": False,     # H2: every story in the digest already read
    "assistant_error": False, # H4: the ask pane's error states (served by p3_search)
}

CREATED_BY = S.CREATED_BY
ITEM_TYPES = S.ITEM_TYPES
TARGET_KINDS = S.TARGET_KINDS
VOTES = S.VOTES


# ------------------------------------------------------------------ files and clocks

def _work_dir():
    """Where the digests are: `ROOT / digest_dir`, or the environment variable.

    NOT `WORK`, deliberately -- and this is the one place where copying the double
    exactly matters. The double reads its digests from `ROOT / KNOBS["digest_dir"]` and
    uses `WORK` only for the file it writes its state into, so K1's suite can send the
    state file to a temporary directory (`D.WORK = Path(tmp)`) while still reading the
    two real digests. Reading digests from `WORK` would have found that empty directory
    instead. (This module keeps its state in `intel.db`, so it has no state file at all;
    `WORK` is kept only because the suite assigns it.)
    """
    from_env = os.environ.get("P3_READER_WORK")
    return Path(from_env) if from_env else (Path(ROOT) / KNOBS["digest_dir"])


def _daily_dir():
    """Where the daily run's digests are, or None in a test world (see HOW TO RUN)."""
    from_env = os.environ.get("P3_READER_DIGESTS")
    if from_env:
        return Path(from_env)
    if os.environ.get("P3_READER_WORK"):
        return None
    return Path(ROOT) / KNOBS["digest_dir_daily"]


def _news_path():
    return Path(os.environ.get("P3_READER_NEWS_DB") or (ROOT / "data" / "news.db"))


def _intel_path():
    return Path(os.environ.get("P3_READER_INTEL_DB") or (ROOT / "data" / "intel.db"))


def _now():
    return dt.datetime.now().isoformat(timespec="seconds")


def _today():
    """Today's LOCAL date: the cap and the day's spend are the reader's day, not UTC's."""
    return dt.date.today().isoformat()


def _new_id(prefix):
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _json_or(text, default):
    if not text:
        return default
    try:
        return json.loads(text)
    except ValueError:
        return default


# ------------------------------------------------------------------ connections
#
# One read-only connection per database per process, opened on first use. Read-only is
# least privilege, not neatness: `news.db` is the extraction's output and Phase 3 has no
# business being able to write a byte of it (CLAUDE.md §2.4).

# Keyed by THREAD as well as file: SQLite binds a connection to the thread that opened
# it, and FastAPI runs sync routes in a thread pool, so one process-wide connection made
# every route served off the first thread fail (found by wave 5, 3 Nov: K1's asgi and
# v01_routes sections, and p3_mcp.py's tool threads).
_CONS = {}


def _news():
    path = _news_path()
    key = ("news", str(path), threading.get_ident())
    if key not in _CONS:
        if not path.exists():
            raise LookupError(f"missing {path}")
        _CONS[key] = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    return _CONS[key]


def _intel_ro():
    path = _intel_path()
    key = ("intel_ro", str(path), threading.get_ident())
    if key not in _CONS:
        _CONS[key] = S.open_intel_ro(path)
    return _CONS[key]


def _intel_w():
    """The one writable connection, and the only writer of K3's tables."""
    path = _intel_path()
    key = ("intel_w", str(path), threading.get_ident())
    if key not in _CONS:
        _CONS[key] = S.open_intel(path, quiet=True)
    return _CONS[key]


def close_all():
    """Drop every cached connection. The tests call it between temporary databases."""
    for con in _CONS.values():
        try:
            con.close()
        except sqlite3.Error:
            pass
    _CONS.clear()


# ------------------------------------------------------------------------- digests

def _digest_files():
    """Every digest JSON in `data/digests/` and the work directory, newest `built_at` first.

    Same guard and same order as the double: a file is a digest only with `stories`,
    `window` and `built_at` (rule 14), and a missing `built_at` sorts last (rule 15).
    The daily run's folder is read first and wins a stem both folders hold. Its
    temporary files end in `.tmp`, so a digest still being written is never listed.
    """
    out, seen = [], set()
    for folder in (_daily_dir(), _work_dir()):
        if folder is None or not folder.is_dir():
            continue
        for path in sorted(folder.glob("*.json")):
            if path.stem in seen:
                continue
            try:
                obj = json.loads(path.read_text())
            except (json.JSONDecodeError, OSError):
                continue
            if not isinstance(obj, dict):
                continue
            if not all(k in obj for k in KNOBS["digest_required_keys"]):
                continue
            seen.add(path.stem)
            out.append((path.stem, path, obj))
    out.sort(key=lambda t: t[2].get("built_at") or "", reverse=True)
    return out


def get_digest(ref):
    """The digest JSON as it is. `ref` = "latest"/None/"" or a filename stem (R6)."""
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
    """[{ref, window, built_at, kind}], newest first (R6). `kind` = the window's kind:
    `day` · `week` · `since_last` (contract v2.5 line 13), None if the file has none."""
    return [{"ref": stem, "window": obj.get("window"), "built_at": obj.get("built_at"),
             "kind": (obj.get("window") or {}).get("kind")}
            for stem, _path, obj in _digest_files()]


def _run_in_progress():
    """Is `p3_daily.py` running? Asked of its own lock, not of a path spelled out here.

    The lock file and the liveness test both belong to the wrapper (`p3_daily.LOCK`,
    `p3_daily.alive`), so they are imported rather than re-derived: two spellings of one
    path is how a reader and a writer come to disagree about whether a run is on.
    """
    try:
        import p3_daily
    except ImportError:                              # the wrapper is not built yet
        return False
    lock = p3_daily.read_lock()
    if lock is None:
        return False
    if KNOBS["trust_a_stale_lock"]:
        return True
    return p3_daily.alive(lock.get("pid")) if lock.get("pid") else True


def run_status():
    """{state, latest_ready, updated_at, fixture} — is a run in progress? (R6)

    `processing` while the daily run holds its lock: the page then opens the latest
    finished digest with a note rather than an empty screen, which is the whole point of
    Q9b's stale-while-processing rule — the reader is never blocked by a batch.
    """
    files = _digest_files()
    latest = files[0][2] if files else {}
    window = latest.get("window") or {}
    running = _run_in_progress() or bool(FIXTURES["processing"])
    return {"state": "processing" if running else "ready",
            "latest_ready": window.get("to") or window.get("from"),
            "updated_at": latest.get("built_at"),
            "fixture": bool(FIXTURES["processing"])}


def _latest_digest():
    files = _digest_files()
    return files[0][2] if files else {}


# ------------------------------------------------------------------------ articles

def _seats_on(dates):
    """Every labelled seat on those dates (p3_digest's own loader, read-only).

    Reused rather than re-queried so that "which seat is the best one" cannot disagree
    with the digest's own answer: one owner for the ranking rules, in `p3_digest.py`.
    """
    return D.load_seats(_news(), sorted(set(d for d in dates if d)))


def _best_seat(text_hash):
    """The highest-points printed slot of one text, and the shape it was scored in.

    A text can sit in several papers and on several pages; its deck and byline belong to
    a SLOT, not to the text (facts §4: `texts` has no deck at all). The deck the reader
    should see is the one from the most prominent appearance, which is exactly what the
    digest's points measure.
    """
    dates = [d for (d,) in _news().execute(
        "SELECT DISTINCT date FROM placements WHERE run_id = ? AND text_hash = ?",
        (D.RUN_ID, text_hash))]
    if not dates:
        return None, []
    seats = _seats_on(dates)
    shape = D.paper_day_shape(seats)
    mine = [s for s in seats if s["text_hash"] == text_hash]
    for s in mine:
        s["points"], s["points_why"] = D.seat_points(s, shape)
    mine.sort(key=lambda s: (-s["points"], s["date"], s["publisher"], s["page"] or 0))
    return (mine[0] if mine else None), mine


def _page_link_with_paper(seat):
    """R2 D8: the page link plus the paper it belongs to.

    The paper cannot be read off the PDF path safely -- "The Hindu_International" is one
    paper with an edition in its stem, not two words to split -- so it is carried beside
    the link instead of parsed out of it.
    """
    link = dict(D.page_link(seat))
    link["paper"] = seat["publisher"]
    return link


def _split_byline(value):
    """A printed byline value -> (byline, dateline).

    `placements.byline` carries both: 'BY SHOUVIK DAS' is a byline and 'New Delhi:' is a
    dateline, and nothing in the database says which. A value ending in a colon and no
    longer than `dateline_max_words` is a place; anything else is a name.
    """
    value = (value or "").strip()
    if not value:
        return "", None
    if value.endswith(":") and len(value.split()) <= KNOBS["dateline_max_words"]:
        return "", value
    return value, None


def article(text_hash):
    """One article to read in the pane, with its real body (R3).

    Headline and body come from `texts`; deck and byline from the best `placements` seat,
    because a deck belongs to the printed slot. Returns None for an unknown hash, which
    the route turns into a 404 (`p3_app.py:256-259`) -- the double does the same, so the
    shape of "no such article" is not changed here.
    """
    if not isinstance(text_hash, str) or not text_hash:
        raise ValueError("text_hash must be a non-empty string")
    row = _news().execute(
        "SELECT headline, body, words FROM texts WHERE text_hash = ?", (text_hash,)).fetchone()
    if row is None:
        return None
    headline, body, words = row
    seat, _all = _best_seat(text_hash)
    if seat is None:
        # A text with no printed slot cannot happen in run exam-v1 (every text came from
        # a placement), but a half-imported day could produce one, and a reader opening
        # it should get the text rather than an exception.
        return {"text_hash": text_hash, "headline": headline, "deck": "", "byline": "",
                "dateline": None, "paper": None, "date": None, "page_link": None,
                "words": words, "kind": None, "body": body or "", "fixture": False}
    byline, dateline = _split_byline(seat.get("byline"))
    return {"text_hash": text_hash,
            "headline": headline,
            "deck": (seat.get("deck") or "").strip(),
            "byline": byline,
            "dateline": dateline,
            "paper": seat["publisher"],
            "date": seat["date"],
            "page_link": _page_link_with_paper(seat),
            "words": words,
            "kind": seat.get("kind"),
            "body": body or "",
            "fixture": False}


# ------------------------------------------------------------------------- threads

def _thread_group_ids(thread_id):
    """Every day-group of a thread, including the root itself (t0.2 parent links)."""
    intel = _intel_ro()
    root = D.thread_roots(intel)
    ids = {g for g in root if root.get(g) == thread_id}
    ids.add(thread_id)
    return sorted(ids)


def _thread_days(thread_id):
    """{date: [text_hash, ...]} for the thread's own days, and the group id of each day."""
    group_ids = _thread_group_ids(thread_id)
    qs = ",".join("?" * len(group_ids))
    rows = _intel_ro().execute(
        f"SELECT date, group_id, text_hash FROM members "
        f"WHERE version = ? AND group_id IN ({qs}) ORDER BY date",
        (D.GROUPING_VERSION, *group_ids)).fetchall()
    by_date, group_of_date = collections.defaultdict(list), {}
    for date, group_id, text_hash in rows:
        by_date[date].append(text_hash)
        group_of_date[date] = group_id
    return dict(by_date), group_of_date


def _story_id_of_hash(text_hash):
    """Which story a text belongs to today: its day-group's thread root."""
    row = _intel_ro().execute(
        "SELECT group_id FROM members WHERE version = ? AND text_hash = ?",
        (D.GROUPING_VERSION, text_hash)).fetchone()
    if row is None:
        return None
    root = D.thread_roots(_intel_ro())
    return root.get(row[0], row[0])


def _day_entry(date, hashes, group_id, seats_by_date):
    """One thread entry: that day's headline, one-liner, counts and page links.

    The day's headline is its LONGEST covering article, the same rule the story-so-far
    box uses (`p3_digest.thread_day_rows`): inside one story on one day, the fullest
    account is the longest one.
    """
    day_seats = [s for s in seats_by_date.get(date, []) if s["text_hash"] in set(hashes)]
    covering = [s for s in day_seats if s["counts_as_coverage"]]
    use = covering or day_seats
    if not use:
        return None
    top = max(use, key=lambda s: (s["words"] or 0))
    pages = [_page_link_with_paper(s) for s in
             sorted(use, key=lambda s: (s["publisher"], s["page"] or 0))]
    papers = sorted({s["publisher"] for s in use})
    return {"entry_id": group_id,
            "date": date,
            "link_type": None,                      # set by the caller, oldest day first
            "parent_id": None,
            "headline": top["headline"],
            "one_liner": top.get("summary"),
            "articles": len({s["text_hash"] for s in use}),
            "papers": len(papers),
            "paper_names": papers,
            "pages": pages,
            "fixture": False}


def _related_items(thread_id, entries, seats_by_date):
    """7f. Printed links only -- `see_also_page` and `related_id` -- to another story.

    An inferred link is K2's judge's job; a printed "see page 4" is a fact the paper
    stated, which is why v1 shows only these two.
    """
    entry_of_date = {e["date"]: e["entry_id"] for e in entries}
    hashes_of_date = {e["date"]: e["_hashes"] for e in entries}
    out, seen = [], set()
    for date, seats in seats_by_date.items():
        if date not in entry_of_date:
            continue
        index = D.build_seat_index(seats)
        for s in seats:
            if s["text_hash"] not in hashes_of_date[date]:
                continue                             # only this thread's own seats link out
            extra = s.get("extra") or {}
            for kind in ("related_id", "see_also_page"):
                value = extra.get(kind)
                if value is None:
                    continue
                if kind == "related_id":
                    targets = [index["by_art"].get((s["date"], s["publisher"], str(value)))]
                else:
                    targets = index["by_page"].get((s["date"], s["publisher"], value), [])
                for target in targets:
                    if not target:
                        continue
                    other = _story_id_of_hash(target["text_hash"])
                    if not other or other == thread_id:
                        continue
                    key = (kind, other)
                    if key in seen:
                        continue
                    seen.add(key)
                    out.append({"date": date,
                                "headline": target["headline"],
                                "attaches_to": entry_of_date[date]})
    return out


def _user_entries(story_hashes):
    """R13 · one entry per link the reader made out of this story (contract v2.1).

    `added_by` is "you" and `link_type` is "user", because the one thing this entry must
    never do is look like something the pipeline found. Date, headline and counts come
    from the TARGET story's own coverage, so the content is as real as any other entry.
    """
    links = list_user_links(story_hashes)
    if not links:
        return []
    out = []
    for link in links:
        target_hashes = link["target_text_hashes"]
        if not target_hashes:
            continue
        dates = [d for (d,) in _news().execute(
            f"SELECT DISTINCT date FROM placements WHERE run_id = ? AND text_hash IN "
            f"({','.join('?' * len(target_hashes))})",
            (D.RUN_ID, *target_hashes))]
        seats = [s for s in _seats_on(dates) if s["text_hash"] in set(target_hashes)]
        if not seats:
            continue
        latest = max(s["date"] for s in seats)
        on_latest = [s for s in seats if s["date"] == latest]
        top = max(on_latest, key=lambda s: (s["words"] or 0))
        # The headline is the TARGET STORY's own, by the digest's rule -- the one the
        # reader saw when choosing it -- not the longest piece of its last day, which
        # can differ (found by wave 5, K1 rule 14). The longest piece still gives the
        # one-liner, as a thread day's does.
        shape = D.paper_day_shape(seats)
        for s in seats:
            s["points"], s["points_why"] = D.seat_points(s, shape)
        own = D.load_stories(_intel_ro(), seats)
        headline = (D.pick_headline(own[0], {}).get("text") if len(own) == 1
                    else None) or top["headline"]
        papers = sorted({s["publisher"] for s in seats})
        out.append({"entry_id": f"user:{link['link_id']}",
                    "link_id": link["link_id"],
                    "date": latest,
                    "link_type": "user",
                    "added_by": "you",
                    "parent_id": None,
                    "story_id": link["target_story_id"],
                    "headline": headline,
                    "one_liner": top.get("summary"),
                    "articles": len({s["text_hash"] for s in seats}),
                    "papers": len(papers),
                    "paper_names": papers,
                    "pages": [_page_link_with_paper(s) for s in
                              sorted(seats, key=lambda s: (s["date"], s["publisher"]))],
                    "fixture": False})
    out.sort(key=lambda e: e["date"], reverse=True)
    return out


def thread(thread_id):
    """A story's thread: one entry per day, newest first, plus the reader's own (R2).

    None when the story ran for a single day and the reader has added nothing -- that is
    state H3, an answer and not an error, and the route prints `{"thread": null}` for it
    (`p3_app.py:294-311`). None is also what an unknown id gets: the route tells the two
    apart by looking for the story itself, which keeps this function a plain lookup.

    R13: a story with no thread of its own that has been given a link now HAS a thread --
    its own day, plus what the reader added. That is the point of the feature.
    """
    if not isinstance(thread_id, str) or not thread_id:
        raise ValueError("thread_id must be a non-empty string")
    by_date, group_of_date = _thread_days(thread_id)
    story_hashes = sorted({h for hs in by_date.values() for h in hs})
    mine = _user_entries(story_hashes) if story_hashes else []
    if not by_date:
        return None
    if len(by_date) < KNOBS["min_thread_days"] and not mine:
        return None

    seats = _seats_on(by_date.keys())
    seats_by_date = collections.defaultdict(list)
    for s in seats:
        seats_by_date[s["date"]].append(s)

    entries = []
    for date in sorted(by_date):                     # oldest first, to type and parent them
        entry = _day_entry(date, by_date[date], group_of_date[date], seats_by_date)
        if entry is None:
            continue
        entry["_hashes"] = set(by_date[date])
        entries.append(entry)
    if not entries:
        return None
    entries[0]["link_type"] = KNOBS["link_type_first_day"]
    for e in entries[1:]:
        e["link_type"] = KNOBS["link_type_later_days"]
    for i in range(1, len(entries)):                 # each day's parent is the day before
        entries[i]["parent_id"] = entries[i - 1]["entry_id"]

    related = _related_items(thread_id, entries, seats_by_date)
    for e in entries:
        e.pop("_hashes", None)

    day_count = len(entries)
    entries.sort(key=lambda e: e["date"], reverse=True)
    everything = sorted(entries + mine, key=lambda e: e["date"], reverse=True)
    all_papers = sorted({p for e in everything for p in e["paper_names"]})
    return {"thread_id": thread_id,
            "days": day_count,                       # days only: a link is not a day (K1 D9)
            "entries_added_by_you": len(mine),
            "articles": sum(e["articles"] for e in everything),
            "papers": len(all_papers),
            "entries": everything,
            "related": related,
            "fixture": False}


# -------------------------------------------------------------- reader state (R1)

def _story_hashes(story):
    return [r["text_hash"] for r in (story or {}).get("coverage", {}).get("rows", [])]


def new_since_read(story):
    """The story's articles that arrived after the reader last read it (R4).

    Nothing counts as new until something has been read: a story never opened is simply
    unread, not all-new, so this is [] rather than its whole coverage (rule 4).
    """
    if not isinstance(story, dict):
        raise ValueError("new_since_read takes the story object, not an id")
    hashes = _story_hashes(story)
    if not hashes:
        return []
    read = read_state(hashes)
    if not any(read.get(h) for h in hashes):
        return []
    return [h for h in hashes if not read.get(h)]


def _held_hashes(con=None):
    """Every text_hash held unread by an active keep-unread."""
    con = con or _intel_ro()
    out = set()
    for (ids,) in con.execute("SELECT article_ids FROM keep_unread"):
        out.update(_json_or(ids, []))
    return out


def mark_read(text_hashes, at=None):
    """Mark these articles read. Called when the reader LEAVES a story (rule 1).

    A hash held by an active keep-unread is silently SKIPPED (rule 2), so the rule holds
    even if a screen forgets to check -- the one place that can enforce it is the only
    place that writes.
    """
    at = at or _now()
    con = _intel_w()
    held = _held_hashes(con)
    for h in text_hashes or []:
        if h in held:
            continue
        con.execute("INSERT OR IGNORE INTO read_state (text_hash, read_at) VALUES (?, ?)",
                    (h, at))
    con.commit()


def read_state(text_hashes):
    """{text_hash: read_at or None} for the hashes asked about."""
    hashes = list(text_hashes or [])
    if FIXTURES["nothing_new"]:
        at = _now()
        return {h: at for h in hashes}               # H2: everything already read
    if not hashes:
        return {}
    qs = ",".join("?" * len(hashes))
    got = dict(_intel_ro().execute(
        f"SELECT text_hash, read_at FROM read_state WHERE text_hash IN ({qs})",
        tuple(hashes)))
    return {h: got.get(h) for h in hashes}


def set_keep_unread(story_id, text_hashes, on):
    """Hold a story unread, with the hashes AS THEY ARE NOW (rule 3).

    Turning it ON also CLEARS any existing `read_at` for those hashes -- that is what
    "Mark unread" on a story you have already read has to mean (rule 2).
    """
    con = _intel_w()
    hashes = list(text_hashes or [])
    if on:
        con.execute("INSERT OR REPLACE INTO keep_unread (story_id, article_ids, set_at) "
                    "VALUES (?, ?, ?)", (story_id, json.dumps(hashes), _now()))
        if hashes:
            qs = ",".join("?" * len(hashes))
            con.execute(f"DELETE FROM read_state WHERE text_hash IN ({qs})", tuple(hashes))
    else:
        con.execute("DELETE FROM keep_unread WHERE story_id = ?", (story_id,))
    con.commit()


def list_keep_unread():
    """The stories being held unread, so the button is not a liar after a reload."""
    return [{"story_id": sid, "text_hashes": _json_or(ids, [])}
            for sid, ids in _intel_ro().execute(
                "SELECT story_id, article_ids FROM keep_unread ORDER BY story_id")]


def follow(story_id, text_hashes, on):
    """Follow or unfollow a story. Keyed on the thread, stored with its articles.

    Following is about the FUTURE of a thread, so the key is the thread id; the article
    set is stored beside it because that is the only thing that can find the thread again
    after a re-clustering run (`list_follows` does the re-attaching).
    """
    con = _intel_w()
    if on:
        con.execute("INSERT OR REPLACE INTO follows "
                    "(thread_id, story_id_at_follow, article_ids, followed_at) "
                    "VALUES (?, ?, ?, ?)",
                    (story_id, story_id, json.dumps(list(text_hashes or [])), _now()))
    else:
        con.execute("DELETE FROM follows WHERE thread_id = ?", (story_id,))
    con.commit()


def list_follows():
    """[{story_id, text_hashes}], sorted. A follow whose thread moved is re-attached.

    Story ids are recomputed by every grouping run, so a stored id can name a thread that
    no longer exists. The articles cannot move, so the follow belongs to whichever thread
    holds most of them -- which is what the reader meant by following it.
    """
    rows = _intel_ro().execute(
        "SELECT thread_id, article_ids FROM follows ORDER BY thread_id").fetchall()
    out = []
    for thread_id, ids in rows:
        hashes = _json_or(ids, [])
        out.append({"story_id": _reattach(thread_id, hashes), "text_hashes": hashes})
    out.sort(key=lambda f: f["story_id"] or "")
    return out


def _reattach(stored_id, hashes):
    """The thread that holds most of these articles today, or the stored id."""
    if not hashes:
        return stored_id
    if _thread_days(stored_id)[0]:
        return stored_id
    counts = collections.Counter()
    for h in hashes:
        sid = _story_id_of_hash(h)
        if sid:
            counts[sid] += 1
    return counts.most_common(1)[0][0] if counts else stored_id


# -------------------------------------------------------------- boards and pins (R1)

def list_boards():
    """Boards, most recently used first, with their pin count (R1 + K1's D9).

    `last_used_at` is stored and bumped by `pin()`, not derived from the pins: a board
    you opened and read but did not pin to has still been used.
    """
    counts = dict(_intel_ro().execute(
        "SELECT board_id, COUNT(*) FROM pins GROUP BY board_id"))
    out = [{"board_id": bid, "name": name, "last_used_at": last, "created_by": by,
            "pin_count": counts.get(bid, 0)}
           for bid, name, last, by in _intel_ro().execute(
               "SELECT board_id, name, last_used_at, created_by FROM boards")]
    out.sort(key=lambda b: b["last_used_at"] or "", reverse=True)
    return out


def create_board(name, created_by="app"):
    if not name or not str(name).strip():
        raise ValueError("a board needs a name")
    if created_by not in CREATED_BY:
        raise ValueError(f"created_by must be one of {', '.join(CREATED_BY)}")
    board_id, now = _new_id("b"), _now()
    con = _intel_w()
    con.execute("INSERT INTO boards (board_id, name, created_by, created_at, "
                "last_used_at, note) VALUES (?, ?, ?, ?, ?, NULL)",
                (board_id, str(name).strip(), created_by, now, now))
    con.commit()
    return board_id


def touch_board(board_id):
    """Bump a board's `last_used_at` -- called by `pin()` and when a board is opened."""
    con = _intel_w()
    cur = con.execute("UPDATE boards SET last_used_at = ? WHERE board_id = ?",
                      (_now(), board_id))
    con.commit()
    if cur.rowcount == 0:
        raise LookupError(f"no such board: {board_id!r}")


def _hashes_in_payload(payload):
    """The article ids a pinned thing is made of, taken from the payload itself.

    `pins.article_ids` and `pins.payload` hold the same hashes, so they have ONE writer:
    this function, called from `pin()`. Two writers could disagree and nothing would say
    which was right.
    """
    if isinstance(payload, dict):
        for key in ("text_hashes", "article_ids"):
            value = payload.get(key)
            if isinstance(value, (list, tuple)):
                return [str(v) for v in value]
        one = payload.get("text_hash")
        if one:
            return [str(one)]
        rows = payload.get("coverage", {}).get("rows") if isinstance(
            payload.get("coverage"), dict) else None
        if rows:
            return [r["text_hash"] for r in rows if r.get("text_hash")]
    return []


def pin(board_id, item_type, payload, source, created_by="app"):
    """Pin something to a board (R1). `payload` carries the hashes as they are now."""
    if item_type not in ITEM_TYPES:
        raise ValueError(f"item_type must be one of {', '.join(ITEM_TYPES)}")
    if created_by not in CREATED_BY:
        raise ValueError(f"created_by must be one of {', '.join(CREATED_BY)}")
    con = _intel_w()
    if con.execute("SELECT 1 FROM boards WHERE board_id = ?", (board_id,)).fetchone() is None:
        raise LookupError(f"no such board: {board_id!r}")
    pin_id, now = _new_id("p"), _now()
    position = (con.execute("SELECT COALESCE(MAX(position), 0) + 1 FROM pins "
                            "WHERE board_id = ?", (board_id,)).fetchone()[0])
    con.execute("INSERT INTO pins (pin_id, board_id, item_type, payload, source, "
                "article_ids, created_by, created_at, position, note) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)",
                (pin_id, board_id, item_type, json.dumps(payload),
                 json.dumps(source) if source is not None else None,
                 json.dumps(_hashes_in_payload(payload)), created_by, now, position))
    con.execute("UPDATE boards SET last_used_at = ? WHERE board_id = ?", (now, board_id))
    con.commit()
    return pin_id


def unpin(pin_id):
    """A pin that is not there is a LookupError, not a quiet no-op (contract v2.2)."""
    con = _intel_w()
    cur = con.execute("DELETE FROM pins WHERE pin_id = ?", (pin_id,))
    con.commit()
    if cur.rowcount == 0:
        raise LookupError(f"no such pin: {pin_id!r}")


def list_pins(board_id):
    rows = _intel_ro().execute(
        "SELECT pin_id, board_id, item_type, payload, source, created_by, created_at "
        "FROM pins WHERE board_id = ? ORDER BY position, created_at", (board_id,))
    return [{"pin_id": pid, "board_id": bid, "item_type": kind,
             "payload": _json_or(payload, None), "source": _json_or(source, None),
             "created_by": by, "created_at": at}
            for pid, bid, kind, payload, source, by, at in rows]


# ------------------------------------------------------------- feedback, prefs (R1)

def feedback(target_kind, target_id, text_hashes, vote, note=None):
    """A vote on a story, or "Wrong link?" on a thread link. Stored, never acted on."""
    if target_kind not in TARGET_KINDS:
        raise ValueError(f"target_kind must be one of {', '.join(TARGET_KINDS)}")
    if vote not in VOTES:
        raise ValueError(f"vote must be one of {', '.join(VOTES)}")
    con = _intel_w()
    con.execute("INSERT INTO feedback (feedback_id, target_kind, target_id, article_ids, "
                "vote, note, at, visit_id) VALUES (?, ?, ?, ?, ?, ?, ?, NULL)",
                (_new_id("f"), target_kind, str(target_id),
                 json.dumps(list(text_hashes or [])), vote, note, _now()))
    con.commit()


def list_feedback():
    """{"<kind>:<id>": {vote, note, at}} — the LATEST vote on each thing (rule 12)."""
    latest = {}
    for kind, tid, vote, note, at in _intel_ro().execute(
            "SELECT target_kind, target_id, vote, note, at FROM feedback ORDER BY at"):
        latest[f"{kind}:{tid}"] = {"vote": vote, "note": note, "at": at}
    return latest


def get_pref(key):
    """The stored value, decoded. `viewer_prefs.value` is JSON, always -- a stored
    `false` read back as the string "false" would be true."""
    row = _intel_ro().execute(
        "SELECT value FROM viewer_prefs WHERE key = ?", (key,)).fetchone()
    return None if row is None else _json_or(row[0], None)


def set_pref(key, value):
    con = _intel_w()
    con.execute("INSERT OR REPLACE INTO viewer_prefs (key, value, set_at) VALUES (?, ?, ?)",
                (key, json.dumps(value), _now()))
    con.commit()


# ---------------------------------------------------------------- visits, cost (R1)

def record_visit(digest_ref, window, opened_story=False):
    """One row per sitting. Re-opening the same digest inside the knob's minutes UPDATES
    the last row instead of adding one, so a reload does not reset "since last visit"
    (rule 10); `opened_story` is sticky, because a sitting in which a story was opened
    stays one."""
    con = _intel_w()
    now = _now()
    row = con.execute("SELECT visit_id, digest_ref, opened_story, at FROM visits "
                      "ORDER BY at DESC, rowid DESC LIMIT 1").fetchone()
    same = False
    if row and row[1] == digest_ref:
        gap = (dt.datetime.fromisoformat(now) - dt.datetime.fromisoformat(row[3])).total_seconds()
        same = gap <= KNOBS["visit_same_sitting_minutes"] * 60
    if same:
        con.execute("UPDATE visits SET opened_story = ?, at = ? WHERE visit_id = ?",
                    (1 if (row[2] or opened_story) else 0, now, row[0]))
    else:
        con.execute("INSERT INTO visits (visit_id, digest_ref, window, opened_story, at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (_new_id("v"), digest_ref,
                     json.dumps(window) if window is not None else None,
                     1 if opened_story else 0, now))
    con.commit()


def last_visit(opened_only=True):
    """When the reader was last here. A plain maximum: the page reads this BEFORE
    recording the new visit (rule 11, `p3_app.py:393-394`), so there is no "current"
    visit to exclude here."""
    sql = "SELECT MAX(at) FROM visits"
    if opened_only:
        sql += " WHERE opened_story = 1"
    row = _intel_ro().execute(sql).fetchone()
    return row[0] if row and row[0] else None


def _override_key(day=None):
    return f"cap_override:{day or _today()}"


def _override_today():
    """{total_usd, presses:[{at, amount_usd}]} for today, from `viewer_prefs`."""
    got = get_pref(_override_key()) or {}
    presses = got.get("presses") or []
    return {"total_usd": float(got.get("total_usd") or 0.0), "presses": presses}


def cost_today():
    """{spent_usd, cap_usd, override_usd, fixture} — the day's spend against the cap.

    Spend is every paid call of the day from `cost_log`, over EVERY purpose (write-ups,
    story so far, answers, Think harder, background, suggestions), by the reader's local
    date -- so the header and each answer's own cost line are the same arithmetic and
    cannot drift apart (contract §7, pass line G4).

    The cap is the latest digest's own knob plus today's override, never a number in this
    file (§A8): a digest built without that knob has no cap to show (D1).
    """
    purposes = KNOBS["paid_purposes"]
    qs = ",".join("?" * len(purposes))
    row = _intel_ro().execute(
        f"SELECT COALESCE(SUM(usd), 0) FROM cost_log "
        f"WHERE purpose IN ({qs}) AND date(at) = ?", (*purposes, _today())).fetchone()
    spent = float(row[0] or 0.0)
    base_cap = (_latest_digest().get("knobs") or {}).get("daily_cost_cap_usd")
    if base_cap is None:
        base_cap = KNOBS["cap_usd_when_knob_absent"]
    override = _override_today()["total_usd"]
    if FIXTURES["cap_reached"]:
        # H5 pushes spend to the KNOB cap, not the overridden one: otherwise pressing
        # "Allow $1 more today" would raise the cap and the spend with it, and the
        # composer would never come back.
        base_cap = base_cap if base_cap is not None else 1.0
        spent = float(base_cap)
    cap = None if base_cap is None else round(float(base_cap) + override, 6)
    return {"spent_usd": round(spent, 6), "cap_usd": cap,
            "override_usd": round(override, 6),
            "fixture": bool(FIXTURES["cap_reached"])}


def raise_cap_today(amount_usd=None):
    """Allow more spending for today only, until local midnight (contract v2.1 D2).

    Presses add up and each one is logged with its time and amount under a DATED key, so
    tomorrow the cap is the knob again with nothing carried over -- the override expires
    by being filed under a date that is no longer today, not by anything having to run at
    midnight. Past the knob's daily ceiling a press is a ValueError (v2.2), which the
    route turns into a 400.
    """
    amount = KNOBS["cap_override_usd"] if amount_usd is None else float(amount_usd)
    if amount <= 0:
        raise ValueError("a cap override must be a positive amount")
    state = _override_today()
    if len(state["presses"]) >= KNOBS["cap_override_max_per_day"]:
        raise ValueError("today's cap has been raised as often as the knob allows")
    state["presses"].append({"at": _now(), "amount_usd": amount})
    state["total_usd"] = round(state["total_usd"] + amount, 6)
    set_pref(_override_key(), state)
    return cost_today()


# ------------------------------------------------------- R13 · your own thread links

def add_user_link(story_id, story_text_hashes, target_story_id, target_text_hashes):
    """The reader's own thread link (contract v2.1 §8). NEVER in K2's `links`.

    Stored against the `text_hash`es on BOTH ends, like pins, so it survives a digest
    being rebuilt under it. K2's links are the judge's output and these are Kunaal's;
    mixing them would make the judge's own evaluation unreadable.
    """
    here = sorted({str(h) for h in (story_text_hashes or [])})
    there = sorted({str(h) for h in (target_text_hashes or [])})
    if not here or not there:
        raise ValueError("a user link needs the text_hashes at both ends")
    link_id = _new_id("ul")
    con = _intel_w()
    con.execute("INSERT INTO user_links (link_id, story_id, story_text_hashes, "
                "target_story_id, target_text_hashes, at) VALUES (?, ?, ?, ?, ?, ?)",
                (link_id, story_id, json.dumps(here), target_story_id,
                 json.dumps(there), _now()))
    con.commit()
    return link_id


def remove_user_link(link_id):
    """A link that is not there is a LookupError (contract v2.2)."""
    con = _intel_w()
    cur = con.execute("DELETE FROM user_links WHERE link_id = ?", (link_id,))
    con.commit()
    if cur.rowcount == 0:
        raise LookupError(f"no such link: {link_id!r}")
    return True


def list_user_links(story_text_hashes):
    """The reader's own links out of this story, matched on the hashes at this end.

    Matched by overlap, not equality: the story may have grown an article since the link
    was made, and the link still belongs to it.
    """
    wanted = {str(h) for h in (story_text_hashes or [])}
    if not wanted:
        return []
    out = []
    for link_id, here, target_story_id, there, at in _intel_ro().execute(
            "SELECT link_id, story_text_hashes, target_story_id, target_text_hashes, at "
            "FROM user_links ORDER BY at"):
        if wanted & set(_json_or(here, [])):
            out.append({"link_id": link_id,
                        "target_story_id": target_story_id,
                        "target_text_hashes": _json_or(there, []),
                        "at": at})
    return out


# ------------------------------------------------- the ask pane, search, conversation
#
# Contract §6-§8 live in `src/p3_search.py` (B2-4 … B2-6). The app has ONE data import,
# so those names are re-exported here when that module exists. Until it does they are
# absent, and only the ask-pane and Explore routes cannot be served -- a MISSING
# p3_search does not stop the reader from loading.
#
# Only ImportError is caught, deliberately. A p3_search that exists but is broken (a
# syntax error, something raising at import) must take this import down with it: the
# alternative is an app that starts, serves the digest, and has silently lost search
# with nothing in the log to say so.

try:                                                  # pragma: no cover - wave 3/4
    from p3_search import (search, facets, ask, think_harder, ask_background,  # noqa: F401
                           estimate_cost, suggest_connections, passages,       # noqa: F401
                           current_conversation, new_conversation, add_divider,  # noqa: F401
                           ERROR_RETRYABLE)                                    # noqa: F401
except ImportError:
    pass


# ------------------------------------------------------------------------ looking

def cmd_status(args):
    status = run_status()
    print(f"state {status['state']} · latest ready {status['latest_ready']} · "
          f"built {status['updated_at']}")
    for d in list_digests():
        window = d["window"] or {}
        print(f"  {d['ref']}: {window.get('kind')} "
              f"{window.get('from')}..{window.get('to')} · built {d['built_at']}")
    cost = cost_today()
    cap = "none" if cost["cap_usd"] is None else f"${cost['cap_usd']:.2f}"
    print(f"today: spent ${cost['spent_usd']:.4f} · cap {cap} · "
          f"override ${cost['override_usd']:.2f}")
    return 0


def cmd_article(args):
    got = article(args.text_hash)
    if got is None:
        print(f"no such article: {args.text_hash}")
        return 1
    print(f"{got['paper']} {got['date']} · {got['kind']} · {got['words']} words · "
          f"{(got['page_link'] or {}).get('label')}")
    print(f"headline: {got['headline']}")
    print(f"deck: {'(none)' if not got['deck'] else '(' + str(len(got['deck'].split())) + ' words)'} · "
          f"byline: {got['byline'] or '(none)'} · dateline: {got['dateline'] or '(none)'}")
    print(f"body: {len(got['body'].split())} words (not printed)")
    return 0


def main():
    p = argparse.ArgumentParser(description="K3's reader layer (looking only)")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("status")
    s.set_defaults(fn=cmd_status)
    s = sub.add_parser("article")
    s.add_argument("text_hash")
    s.set_defaults(fn=cmd_article)
    args = p.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
