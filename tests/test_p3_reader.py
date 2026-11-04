"""
test_p3_reader.py -- TEST (free, no API key, no internet). Checks src/p3_reader.py.

WHAT IT DOES
    Never writes to data/intel.db and never opens news.db for writing. Two parts:
      1. mock   : a synthetic news.db + intel.db + digest JSON in a temporary directory,
                  built to make every behaviour rule testable at all -- a story read to
                  the last article, a held story, a three-day thread, a printed "see also"
                  link, a text printed in two papers with a deck in one of them
      2. real   : the same functions against data/news.db and a COPY of data/intel.db,
                  read-only on news.db, to prove the shapes survive real data

    The 15 behaviour rules of `_work/k3_b2_facts.md` §1b each have at least one check,
    named "rule N"; the contract's error cases each have one, named "error".

WHY IT EXISTS
    `p3_reader.py` replaces K1's double at integration, and eleven screens are built on
    the double's exact shapes. The behaviour rules are not decoration either: "read is on
    leave", "keep-unread beats mark-read" and "nothing is new until something is read"
    are each one line of code that is easy to write backwards, and each one would show up
    as the app lying to the reader about what he has seen.

PASS LINES (fixed before the run, K3 brief §B2-2)
    - Every function of facts §1a exists with the double's name and signature.
    - At least one check per behaviour rule 1-15 and per contract error case; all pass.
    - On real data: article() returns a real body and a real deck/byline from placements;
      a 2+ day thread has one entry per day, newest first, the first day typed `start`
      carrying the thread's own id, later days untyped; no invented link type appears.
    - The real data/intel.db is not written to by this test.
    A failed line is answered by error analysis, not by moving the line.

WHEN TO RUN
    After every change to src/p3_reader.py. Takes a few seconds.

USAGE (from ~/news-intel)
    .venv/bin/python tests/test_p3_reader.py            mock + real data
    .venv/bin/python tests/test_p3_reader.py --mock     mock only
"""

import datetime as dt
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

REAL_NEWS = ROOT / "data" / "news.db"
REAL_INTEL = ROOT / "data" / "intel.db"


def real_fingerprint():
    """The real intel.db's content, read-only: SHA-256 of its dump. Equal before and after
    the suite = the suite wrote nothing to it, whatever the app has stored there before."""
    con = sqlite3.connect(f"file:{REAL_INTEL}?mode=ro", uri=True)
    h = hashlib.sha256()
    for line in con.iterdump():
        h.update(line.encode()); h.update(b"\n")
    con.close()
    return h.hexdigest()[:16]

CHECKS = []


def check(name, got, want):
    CHECKS.append((name, got, want))


def raises(fn, kind):
    """True when calling fn() raises exactly that class of error."""
    try:
        fn()
    except kind:
        return True
    except BaseException as exc:                     # the wrong error is not a pass
        return f"{type(exc).__name__}: {exc}"
    return False


# ------------------------------------------------------------------ the mock archive
#
# Two papers, three dates. Story A runs on all three days (a thread); story B runs on one
# day only (no thread, rule 5). B's seat carries a printed `see_also_page` pointing at
# A's page, so `related` has something real to find.

MOCK_DATES = ("2026-10-01", "2026-10-02", "2026-10-03")
RUN_ID = "exam-v1"
TAG_MODEL, TAG_PROMPT = "claude-haiku-5-5", "v1.3"

# (text_hash, headline, words, [(date, publisher, page, art_id, deck, byline)], kind)
MOCK_TEXTS = [
    ("a1", "Water board meets on the canal", 600,
     [("2026-10-01", "Mint", 1, "1", "A deck for the canal story", "BY A REPORTER")], "news"),
    # a2 is printed twice on one day: buried on Mint's page 3 (which that day also has a
    # page-1 article, so page 3 is "inside") and on The Hindu's front. The deck and byline
    # the reader sees must come from the better seat, so the two have to differ in points.
    ("a2", "Canal plan goes to the state", 900,
     [("2026-10-02", "Mint", 3, "1", "", "New Delhi:"),
      ("2026-10-02", "The Hindu", 1, "1", "A deck from the front page", "")], "news"),
    ("a3", "Canal plan cleared", 400,
     [("2026-10-03", "Mint", 1, "1", "", "by someone else")], "news"),
    ("b1", "A one-day story about buses", 300,
     [("2026-10-01", "Mint", 4, "2", "", "New Delhi:")], "news"),
    ("c1", "An opinion piece", 500,
     [("2026-10-01", "The Hindu", 6, "1", "", "")], "opinion"),
    ("d1", "Mint's own page one that day", 200,
     [("2026-10-02", "Mint", 1, "9", "", "")], "news"),
]
# a1 prints "see also page 4", where b1 sits: a printed link OUT of this thread to another
# story, which is the direction `links_block` and `_related_items` both follow.
MOCK_EXTRA = {("2026-10-01", "Mint", "1"): {"paper": "2026-10-01_Mint", "see_also_page": 4}}

GROUPS = {                                           # group_id -> (date, [hashes])
    "2026-10-01:aaa": ("2026-10-01", ["a1"]),
    "2026-10-02:aaa": ("2026-10-02", ["a2"]),
    "2026-10-03:aaa": ("2026-10-03", ["a3"]),
    "2026-10-01:bbb": ("2026-10-01", ["b1"]),
    "2026-10-01:ccc": ("2026-10-01", ["c1"]),
    "2026-10-02:ddd": ("2026-10-02", ["d1"]),
}
PARENTS = [("2026-10-02:aaa", "2026-10-01:aaa"), ("2026-10-03:aaa", "2026-10-02:aaa")]
THREAD_ID = "2026-10-01:aaa"


def build_mock_news(path):
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE texts (text_hash TEXT PRIMARY KEY, headline TEXT, body TEXT,
            words INTEGER, first_run TEXT, first_seen TEXT);
        CREATE TABLE placements (run_id TEXT, date TEXT, publisher TEXT, art_id TEXT,
            page INTEGER, number INTEGER, deck TEXT, byline TEXT, text_hash TEXT,
            extra TEXT, source_file TEXT, imported_at TEXT,
            PRIMARY KEY (run_id, date, publisher, art_id));
        CREATE TABLE tags (text_hash TEXT, model TEXT, prompt_version TEXT, topic TEXT,
            topics_more TEXT, kind TEXT, summary TEXT, text_check TEXT, tagged_at TEXT,
            in_tokens INTEGER, out_tokens INTEGER, via TEXT, topic_parent TEXT,
            forum TEXT, sponsored_cue TEXT, event TEXT, events_more TEXT,
            PRIMARY KEY (text_hash, model, prompt_version));
    """)
    for text_hash, headline, words, seats, kind in MOCK_TEXTS:
        body = " ".join(f"word{i}" for i in range(words))
        con.execute("INSERT INTO texts VALUES (?,?,?,?,?,?)",
                    (text_hash, headline, body, words, RUN_ID, "2026-10-01T06:00:00"))
        con.execute("INSERT INTO tags (text_hash, model, prompt_version, topic, kind, "
                    "summary, text_check) VALUES (?,?,?,?,?,?,?)",
                    (text_hash, TAG_MODEL, TAG_PROMPT, "Water", kind,
                     f"One line about {headline.lower()}.", "ok"))
        for date, publisher, page, art_id, deck, byline in seats:
            extra = MOCK_EXTRA.get((date, publisher, art_id),
                                   {"paper": f"{date}_{publisher}"})
            con.execute("INSERT INTO placements VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                        (RUN_ID, date, publisher, art_id, page, 1, deck, byline,
                         text_hash, json.dumps(extra), "x.json", "2026-10-01T06:00:00"))
    con.commit()
    con.close()


def build_mock_intel(path):
    import p3_store as S
    con = S.open_intel(path, quiet=True)
    con.executescript("""
        CREATE TABLE IF NOT EXISTS groups (version TEXT, date TEXT, group_id TEXT,
            anchor_hash TEXT, size INTEGER, papers INTEGER, same_paper_only INTEGER,
            min_score REAL, chain_pairs INTEGER, PRIMARY KEY (version, date, group_id));
        CREATE TABLE IF NOT EXISTS members (version TEXT, date TEXT, group_id TEXT,
            text_hash TEXT, PRIMARY KEY (version, date, text_hash));
        CREATE TABLE IF NOT EXISTS links (gversion TEXT, tversion TEXT, date TEXT,
            group_id TEXT, earlier_date TEXT, earlier_group_id TEXT, gap_days INTEGER,
            avg_score REAL, best_score REAL, centroid_score REAL, role TEXT,
            rank INTEGER, PRIMARY KEY (gversion, tversion, date, group_id, earlier_group_id));
    """)
    for group_id, (date, hashes) in GROUPS.items():
        con.execute("INSERT INTO groups (version, date, group_id, anchor_hash, size) "
                    "VALUES ('g0.1', ?, ?, ?, ?)", (date, group_id, hashes[0], len(hashes)))
        for h in hashes:
            con.execute("INSERT INTO members VALUES ('g0.1', ?, ?, ?)", (date, group_id, h))
    for group_id, earlier in PARENTS:
        con.execute("INSERT INTO links (gversion, tversion, date, group_id, earlier_date, "
                    "earlier_group_id, gap_days, role, rank) "
                    "VALUES ('g0.1','t0.2',?,?,?,?,1,'parent',1)",
                    (GROUPS[group_id][0], group_id, GROUPS[earlier][0], earlier))
    con.commit()
    con.close()


MOCK_DIGEST = {
    "window": {"kind": "week", "from": "2026-10-01", "to": "2026-10-03"},
    "built_at": "2026-10-03T06:00:00",
    "knobs": {"daily_cost_cap_usd": 0.5},
    "versions": {"digest": "d0.1"},
    "stories": [
        {"story_id": THREAD_ID, "headline": {"text": "Canal plan cleared"},
         "coverage": {"articles": 3, "papers": 2, "rows": [
             {"text_hash": "a3", "paper": "Mint", "date": "2026-10-03"},
             {"text_hash": "a2", "paper": "Mint", "date": "2026-10-02"},
             {"text_hash": "a1", "paper": "Mint", "date": "2026-10-01"}]}},
        {"story_id": "2026-10-01:bbb", "headline": {"text": "Buses"},
         "coverage": {"articles": 1, "papers": 1, "rows": [
             {"text_hash": "b1", "paper": "Mint", "date": "2026-10-01"}]}},
    ],
}


def mock_world(tmp):
    """Build the whole mock archive and point p3_reader at it."""
    work = Path(tmp) / "work"
    work.mkdir()
    (work / "digest_mock.json").write_text(json.dumps(MOCK_DIGEST))
    (work / "not_a_digest.json").write_text(json.dumps({"hello": "world"}))
    news = Path(tmp) / "news.db"
    intel = Path(tmp) / "intel.db"
    build_mock_news(news)
    build_mock_intel(intel)
    os.environ["P3_READER_NEWS_DB"] = str(news)
    os.environ["P3_READER_INTEL_DB"] = str(intel)
    os.environ["P3_READER_WORK"] = str(work)
    return work, news, intel


# ------------------------------------------------------------------------- 1. mock

def on_mock(tmp):
    work, _news, _intel = mock_world(tmp)
    import p3_reader as R
    R.close_all()

    # --- the functions the app calls, all of them (facts §1a) -------------------
    wanted = ["get_digest", "list_digests", "run_status", "article", "thread",
              "new_since_read", "mark_read", "read_state", "set_keep_unread",
              "list_keep_unread", "follow", "list_follows", "list_boards",
              "create_board", "pin", "unpin", "list_pins", "feedback", "list_feedback",
              "get_pref", "set_pref", "record_visit", "last_visit", "cost_today",
              "raise_cap_today", "add_user_link", "remove_user_link", "list_user_links"]
    check("every function the app calls exists",
          [n for n in wanted if not callable(getattr(R, n, None))], [])
    check("KNOBS and FIXTURES exist (the dev route mutates FIXTURES in place)",
          isinstance(R.KNOBS, dict) and isinstance(R.FIXTURES, dict), True)

    # --- digests (rules 14, 15) -------------------------------------------------
    check("rule 14 · a stray JSON file is not a digest",
          [d["ref"] for d in R.list_digests()], ["digest_mock"])

    # Digests come from `ROOT / digest_dir`, NOT from `WORK` -- the double's own rule,
    # which K1's suite depends on: `real()` sets `WORK` to an empty temporary directory
    # to send the state file somewhere safe and still expects the two real digests to be
    # found. Reading digests from `WORK` found the empty directory instead, and K1's
    # suite is what caught it.
    kept_work, kept_root, kept_env = R.WORK, R.ROOT, os.environ.pop("P3_READER_WORK")
    try:
        R.ROOT = work.parent
        (work.parent / "_work").mkdir(exist_ok=True)
        shutil.copy2(work / "digest_mock.json", work.parent / "_work" / "digest_mock.json")
        R.WORK = work.parent / "nowhere"
        check("digests are read from ROOT/digest_dir, not from WORK",
              [d["ref"] for d in R.list_digests()], ["digest_mock"])
    finally:
        R.WORK, R.ROOT = kept_work, kept_root
        os.environ["P3_READER_WORK"] = kept_env
    check("rule 14 · and get_digest('latest') is the digest, not the stray file",
          R.get_digest("latest")["window"]["to"], "2026-10-03")
    check("get_digest by ref", R.get_digest("digest_mock")["built_at"], "2026-10-03T06:00:00")
    check("get_digest of an unknown ref is None", R.get_digest("nope"), None)
    (work / "digest_older.json").write_text(json.dumps(
        dict(MOCK_DIGEST, built_at="2026-10-02T06:00:00")))
    (work / "digest_undated.json").write_text(json.dumps(
        {"stories": [], "window": {}, "built_at": None}))
    check("rule 15 · digests sort by built_at descending, missing last",
          [d["ref"] for d in R.list_digests()],
          ["digest_mock", "digest_older", "digest_undated"])
    (work / "digest_older.json").unlink()
    (work / "digest_undated.json").unlink()

    # --- FP-7 · the daily run's folder, data/digests/, read with _work/ (TC-15, X10) ----
    daily = Path(tmp) / "digests"
    daily.mkdir()
    check("FP-7 · a test world (P3_READER_WORK set) never reads a data/digests folder",
          R._daily_dir(), None)
    os.environ["P3_READER_DIGESTS"] = str(daily)
    try:
        (daily / "2026-10-04_week.json").write_text(json.dumps(dict(
            MOCK_DIGEST, built_at="2026-10-05T06:00:00",
            window={"kind": "week", "from": "2026-09-28", "to": "2026-10-04"})))
        (daily / "2026-10-01_day.json").write_text(json.dumps(dict(
            MOCK_DIGEST, built_at="2026-10-01T06:00:00",
            window={"kind": "day", "from": "2026-10-01", "to": "2026-10-01"})))
        (daily / "2026-10-05_day.json.tmp").write_text('{"stories": [')
        got = R.list_digests()
        check("FP-7 · list_digests merges both folders, newest built_at first",
              [d["ref"] for d in got], ["2026-10-04_week", "digest_mock", "2026-10-01_day"])
        check("FP-7 · each row carries its kind",
              [d["kind"] for d in got], ["week", MOCK_DIGEST["window"]["kind"], "day"])
        check("FP-7 · a digest still being written (.tmp) is never listed",
              any("tmp" in d["ref"] for d in got), False)
        check("FP-7 · get_digest('latest') is the newest of both folders, shape unchanged",
              (R.get_digest("latest")["window"]["kind"],
               sorted(R.get_digest("latest")) == sorted(dict(MOCK_DIGEST, built_at="x",
                                                             window={}))), ("week", True))
        check("FP-7 · run_status follows the newest digest of both folders",
              R.run_status()["latest_ready"], "2026-10-04")
        (daily / "digest_mock.json").write_text(json.dumps(dict(
            MOCK_DIGEST, built_at="2026-09-01T06:00:00")))
        check("FP-7 · a stem in both folders is listed once, the daily folder's copy",
              [(d["ref"], d["built_at"]) for d in R.list_digests() if d["ref"] == "digest_mock"],
              [("digest_mock", "2026-09-01T06:00:00")])
    finally:
        os.environ.pop("P3_READER_DIGESTS", None)
    check("FP-7 · without P3_READER_DIGESTS the mock world is as before",
          [d["ref"] for d in R.list_digests()], ["digest_mock"])
    kept_env = os.environ.pop("P3_READER_WORK")
    kept_root = R.ROOT
    try:
        R.ROOT = Path(tmp)
        check("FP-7 · outside a test world the daily folder is ROOT/data/digests",
              R._daily_dir(), Path(tmp) / "data" / "digests")
    finally:
        R.ROOT = kept_root
        os.environ["P3_READER_WORK"] = kept_env
    check("F-2 · no file in src/ or app/ names the test double any more",
          [str(f) for d in ("src", "app") for f in (ROOT / d).rglob("*") if f.is_file() and f.suffix != ".pyc"
           if "p3_app_double" in f.read_text(encoding="utf-8", errors="ignore")], [])

    # --- run_status (R6) --------------------------------------------------------
    check("run_status is ready with no lock file", R.run_status()["state"], "ready")
    check("run_status carries the latest window and build time",
          (R.run_status()["latest_ready"], R.run_status()["updated_at"]),
          ("2026-10-03", "2026-10-03T06:00:00"))
    R.FIXTURES["processing"] = True
    check("the processing fixture still works after the swap (state H1)",
          R.run_status()["state"], "processing")
    R.FIXTURES["processing"] = False

    # R6 · the lock the daily wrapper writes, and the crashed run that left one behind.
    # The lock belongs to p3_daily, so the test moves p3_daily's own LOCK rather than
    # writing to the real `_work/daily.lock` while Kunaal may have a run going.
    import p3_daily
    kept_lock = p3_daily.LOCK
    p3_daily.LOCK = Path(tmp) / "daily.lock"
    try:
        p3_daily.LOCK.write_text(json.dumps({"pid": os.getpid(), "day": "2026-10-03",
                                             "step": "tag", "started_at": "x"}))
        check("R6 · a live lock means today's papers are still processing",
              R.run_status()["state"], "processing")
        p3_daily.LOCK.write_text(json.dumps({"pid": 999999999, "day": "2026-10-03",
                                             "step": "tag", "started_at": "x"}))
        check("R6 · a lock whose process is gone is a crashed run, not a running one",
              R.run_status()["state"], "ready")
        R.KNOBS["trust_a_stale_lock"] = True
        check("R6 · and the knob can say to honour it anyway",
              R.run_status()["state"], "processing")
        R.KNOBS["trust_a_stale_lock"] = False
        p3_daily.LOCK.unlink()
        check("R6 · no lock at all is ready", R.run_status()["state"], "ready")
    finally:
        p3_daily.LOCK = kept_lock

    # --- article (R3) -----------------------------------------------------------
    a1 = R.article("a1")
    check("article has every key the double returns",
          sorted(k for k in a1 if k != "dateline"),
          ["body", "byline", "date", "deck", "fixture", "headline", "kind", "page_link",
           "paper", "text_hash", "words"])
    check("article body is the real text, not a fixture",
          (len(a1["body"].split()), a1["fixture"]), (600, False))
    check("article deck comes from placements, not texts",
          a1["deck"], "A deck for the canal story")
    check("article byline is the printed value", a1["byline"], "BY A REPORTER")
    check("article page_link carries its paper (R2 D8)",
          (a1["page_link"]["label"], a1["page_link"]["paper"]), ("PDF p1", "Mint"))
    a2 = R.article("a2")
    b1 = R.article("b1")
    check("a dateline is not printed as a byline",
          (b1["byline"], b1["dateline"]), ("", "New Delhi:"))
    check("a real byline is passed through as printed",
          R.article("a3")["byline"], "by someone else")
    check("the best seat wins: a2's deck and paper come from the front-page seat, "
          "not the buried one",
          (a2["paper"], a2["page_link"]["pdf_page"], a2["deck"]),
          ("The Hindu", 1, "A deck from the front page"))
    check("article of an unknown hash is None (the route makes the 404)",
          R.article("no_such_hash"), None)
    check("error · article of a non-string is a ValueError",
          raises(lambda: R.article(None), ValueError), True)

    # --- thread (R2; rules 5, 7) ------------------------------------------------
    t = R.thread(THREAD_ID)
    check("thread has every key the double returns", sorted(t),
          ["articles", "days", "entries", "entries_added_by_you", "fixture", "papers",
           "related", "thread_id"])
    check("rule 5 · a one-day story has no thread", R.thread("2026-10-01:bbb"), None)
    check("an unknown thread id is None too", R.thread("2026-09-09:zzz"), None)
    check("one entry per day, newest first",
          [e["date"] for e in t["entries"]], ["2026-10-03", "2026-10-02", "2026-10-01"])
    check("rule 7 · the first day is typed `start` and later days are untyped",
          [e["link_type"] for e in t["entries"]], [None, None, "start"])
    check("X2 · no invented link type appears",
          [e["link_type"] for e in t["entries"] if e["link_type"] in
           ("run_up", "follow_up", "continuing")], [])
    check("rule 7 · the start entry carries the thread's own id",
          [e["entry_id"] for e in t["entries"] if e["link_type"] == "start"], [THREAD_ID])
    check("rule 7 · every other entry's id is date:hash",
          [e["entry_id"] for e in t["entries"] if e["link_type"] is None],
          ["2026-10-03:aaa", "2026-10-02:aaa"])
    check("rule 7 · each day's parent is the day before it",
          [(e["date"], e["parent_id"]) for e in t["entries"]],
          [("2026-10-03", "2026-10-02:aaa"), ("2026-10-02", "2026-10-01:aaa"),
           ("2026-10-01", None)])
    check("an entry's headline is that day's longest covering article",
          t["entries"][1]["headline"], "Canal plan goes to the state")
    check("an entry counts its articles and names its papers",
          (t["entries"][1]["articles"], t["entries"][1]["paper_names"]),
          (1, ["Mint", "The Hindu"]))
    check("every page of every entry carries its paper",
          all("paper" in p for e in t["entries"] for p in e["pages"]), True)
    check("the thread is real data, not a fixture", t["fixture"], False)
    check("7f · a printed see-also to another story becomes a related item",
          [(r["date"], r["headline"]) for r in t["related"]],
          [("2026-10-01", "A one-day story about buses")])
    check("error · thread of a non-string is a ValueError",
          raises(lambda: R.thread(None), ValueError), True)

    # --- read state (rules 1, 2, 3, 4) ------------------------------------------
    story = MOCK_DIGEST["stories"][0]
    hashes = [r["text_hash"] for r in story["coverage"]["rows"]]
    check("rule 4 · nothing is new until something is read", R.new_since_read(story), [])
    R.mark_read(["a1"])
    check("rule 1 · read is per article", R.read_state(["a1", "a2"])["a2"], None)
    check("rule 1 · and a read article has a time",
          bool(R.read_state(["a1"])["a1"]), True)
    check("rule 4 · now the unread ones are new",
          sorted(R.new_since_read(story)), ["a2", "a3"])
    R.mark_read(hashes)
    check("rule 1 · a story is READ only when every article is",
          R.new_since_read(story), [])
    first = R.read_state(["a1"])["a1"]
    R.mark_read(["a1"], at="2030-01-01T00:00:00")
    check("a second mark_read does not move the first read time",
          R.read_state(["a1"])["a1"], first)

    R.set_keep_unread(THREAD_ID, ["a1", "a2"], True)
    check("rule 2 · set_keep_unread clears the read times of the held hashes",
          [R.read_state(["a1"])["a1"], R.read_state(["a2"])["a2"]], [None, None])
    R.mark_read(["a1", "a2", "a3"])
    check("rule 2 · mark_read silently skips a held hash",
          [R.read_state(["a1"])["a1"], R.read_state(["a2"])["a2"]], [None, None])
    check("rule 2 · and still marks the ones that are not held",
          bool(R.read_state(["a3"])["a3"]), True)
    check("rule 3 · the held hashes are stored as they were",
          R.list_keep_unread(), [{"story_id": THREAD_ID, "text_hashes": ["a1", "a2"]}])
    R.set_keep_unread(THREAD_ID, ["a1", "a2"], False)
    check("keep-unread can be turned off", R.list_keep_unread(), [])
    R.mark_read(["a1", "a2"])
    check("and then the hashes can be read", bool(R.read_state(["a1"])["a1"]), True)

    R.FIXTURES["nothing_new"] = True
    check("the nothing_new fixture still works (state H2)",
          all(R.read_state(["zz1", "zz2"]).values()), True)
    R.FIXTURES["nothing_new"] = False

    # --- follows ----------------------------------------------------------------
    R.follow(THREAD_ID, ["a1", "a2", "a3"], True)
    check("follow stores the story with its articles",
          R.list_follows(), [{"story_id": THREAD_ID, "text_hashes": ["a1", "a2", "a3"]}])
    R.follow("2026-09-09:gone", ["a3"], True)
    check("a follow whose thread no longer exists re-attaches by article overlap",
          sorted(f["story_id"] for f in R.list_follows()),
          [THREAD_ID, THREAD_ID])
    R.follow("2026-09-09:gone", [], False)
    R.follow(THREAD_ID, [], False)
    check("unfollow removes it", R.list_follows(), [])

    # --- boards and pins --------------------------------------------------------
    board = R.create_board("Water")
    check("create_board returns a b_ id", board.startswith("b_"), True)
    check("a new board is listed with no pins",
          [(b["name"], b["pin_count"], b["created_by"]) for b in R.list_boards()],
          [("Water", 0, "app")])
    pin_id = R.pin(board, "story", {"text_hashes": ["a1", "a2"], "story_id": THREAD_ID},
                   {"paper": "Mint", "date": "2026-10-01"})
    check("pin returns a p_ id", pin_id.startswith("p_"), True)
    pins = R.list_pins(board)
    check("a pin comes back with its payload and source intact",
          (pins[0]["payload"]["text_hashes"], pins[0]["source"]["paper"]),
          (["a1", "a2"], "Mint"))
    check("rule 3 · the pin's article_ids are derived from its payload (one writer)",
          json.loads(sqlite3.connect(str(_intel)).execute(
              "SELECT article_ids FROM pins WHERE pin_id=?", (pin_id,)).fetchone()[0]),
          ["a1", "a2"])
    check("pinning bumps the board's pin count", R.list_boards()[0]["pin_count"], 1)
    second = R.create_board("Buses")
    R.pin(second, "article", {"text_hash": "b1"}, None)
    # `_now()` has one-second resolution (as K1's double has), and two boards made in the
    # same second cannot be ordered by it -- so the times are set apart here to test the
    # SORT rule rather than the clock.
    con = sqlite3.connect(str(_intel))
    con.execute("UPDATE boards SET last_used_at = '2026-10-01T06:00:00' WHERE board_id = ?",
                (board,))
    con.execute("UPDATE boards SET last_used_at = '2026-10-02T06:00:00' WHERE board_id = ?",
                (second,))
    con.commit()
    con.close()
    check("list_boards is most recently used first",
          [b["name"] for b in R.list_boards()], ["Buses", "Water"])
    R.touch_board(board)
    check("opening a board makes it the most recently used, without a pin",
          [b["name"] for b in R.list_boards()], ["Water", "Buses"])
    check("error · touching a board that does not exist is a LookupError",
          raises(lambda: R.touch_board("b_nope"), LookupError), True)
    check("error · a pin to a board that does not exist is a LookupError",
          raises(lambda: R.pin("b_nope", "story", {}, None), LookupError), True)
    check("error · an unknown item_type is a ValueError",
          raises(lambda: R.pin(board, "nonsense", {}, None), ValueError), True)
    check("error · an unknown created_by is a ValueError",
          raises(lambda: R.pin(board, "story", {}, None, created_by="hacker"),
                 ValueError), True)
    R.unpin(pin_id)
    check("unpin removes it", R.list_pins(board), [])
    check("error · unpinning twice is a LookupError",
          raises(lambda: R.unpin(pin_id), LookupError), True)

    # --- feedback (rule 12) -----------------------------------------------------
    R.feedback("story", THREAD_ID, ["a1"], "up")
    R.feedback("story", THREAD_ID, ["a1"], "down", note="changed my mind")
    check("rule 12 · the latest vote wins",
          R.list_feedback()[f"story:{THREAD_ID}"]["vote"], "down")
    check("rule 12 · and its note comes with it",
          R.list_feedback()[f"story:{THREAD_ID}"]["note"], "changed my mind")
    check("votes are words, not numbers",
          raises(lambda: R.feedback("story", "x", [], 1), ValueError), True)
    check("error · an unknown target_kind is a ValueError",
          raises(lambda: R.feedback("nonsense", "x", [], "up"), ValueError), True)

    # --- prefs ------------------------------------------------------------------
    R.set_pref("sort", "newest")
    R.set_pref("compact", False)
    check("a string pref round-trips", R.get_pref("sort"), "newest")
    check("a false pref comes back as False, not the string 'false'",
          R.get_pref("compact"), False)
    check("an unset pref is None", R.get_pref("never_set"), None)

    # --- visits (rules 10, 11) --------------------------------------------------
    check("rule 11 · last_visit before any visit is None", R.last_visit(), None)
    R.record_visit("digest_mock", MOCK_DIGEST["window"], opened_story=False)
    check("a visit with no story opened is not a 'since last visit' mark",
          R.last_visit(opened_only=True), None)
    check("but it is a visit", bool(R.last_visit(opened_only=False)), True)
    R.record_visit("digest_mock", MOCK_DIGEST["window"], opened_story=True)
    rows = sqlite3.connect(str(_intel)).execute("SELECT COUNT(*) FROM visits").fetchone()[0]
    check("rule 10 · the same digest inside the sitting updates one row", rows, 1)
    check("rule 10 · opened_story is sticky", bool(R.last_visit(opened_only=True)), True)
    R.record_visit("digest_other", {"kind": "day"}, opened_story=False)
    rows = sqlite3.connect(str(_intel)).execute("SELECT COUNT(*) FROM visits").fetchone()[0]
    check("rule 10 · a different digest is a new sitting", rows, 2)
    check("rule 10 · and the sticky mark of the first sitting is not lost",
          bool(R.last_visit(opened_only=True)), True)

    # --- cost (rule 13) ---------------------------------------------------------
    check("rule 13 · the cap is the digest's own knob", R.cost_today()["cap_usd"], 0.5)
    check("spend starts at zero", R.cost_today()["spent_usd"], 0.0)
    con = sqlite3.connect(str(_intel))
    for purpose, usd in (("writeup", 0.001), ("ask", 0.002), ("think_harder", 0.01),
                         ("ask_background", 0.0003), ("suggest", 0.0005),
                         ("story_so_far", 0.0002)):
        con.execute("INSERT INTO cost_log (at, purpose, model, usd) "
                    "VALUES (datetime('now','localtime'), ?, 'claude-haiku-5-5', ?)",
                    (purpose, usd))
    con.execute("INSERT INTO cost_log (at, purpose, model, usd) "
                "VALUES ('2020-01-01T06:00:00', 'ask', 'claude-haiku-5-5', 99.0)")
    con.commit()
    con.close()
    check("spend counts every paid purpose of today, and only today",
          R.cost_today()["spent_usd"], round(0.001 + 0.002 + 0.01 + 0.0003 + 0.0005 + 0.0002, 6))
    (work / "digest_mock.json").write_text(json.dumps(
        {k: v for k, v in MOCK_DIGEST.items() if k != "knobs"}))
    check("rule 13 · a digest with no cap knob has no cap to show",
          R.cost_today()["cap_usd"], None)
    (work / "digest_mock.json").write_text(json.dumps(MOCK_DIGEST))

    after = R.raise_cap_today()
    check("a cap press adds the knob's amount to today's cap", after["cap_usd"], 1.5)
    check("and is reported as an override", after["override_usd"], 1.0)
    R.raise_cap_today(0.25)
    check("presses add up", R.cost_today()["cap_usd"], 1.75)
    for _ in range(3):                               # five presses in all = the ceiling
        R.raise_cap_today()
    check("error · a press past cap_override_max_per_day is a ValueError",
          raises(R.raise_cap_today, ValueError), True)
    check("error · a press of nothing is a ValueError",
          raises(lambda: R.raise_cap_today(0), ValueError), True)
    today_key = f"cap_override:{dt.date.today().isoformat()}"
    stored = R.get_pref(today_key)
    check("the override is filed under today's date, so tomorrow it is gone",
          stored["total_usd"], round(1.00 + 0.25 + 3 * 1.00, 6))
    check("and every press is logged with its time and amount (contract v2.1 D2)",
          [sorted(p) for p in stored["presses"]], [["amount_usd", "at"]] * 5)

    # --- R13 user links ---------------------------------------------------------
    link_id = R.add_user_link(THREAD_ID, ["a1", "a2"], "2026-10-01:bbb", ["b1"])
    check("add_user_link returns an id", link_id.startswith("ul_"), True)
    check("list_user_links finds it by the hashes at this end",
          [(l["link_id"], l["target_story_id"], l["target_text_hashes"])
           for l in R.list_user_links(["a1"])],
          [(link_id, "2026-10-01:bbb", ["b1"])])
    check("a story that shares no hash has no link", R.list_user_links(["c1"]), [])
    check("error · a link with no hashes at one end is a ValueError",
          raises(lambda: R.add_user_link("x", [], "y", ["b1"]), ValueError), True)

    one_day = R.thread("2026-10-01:bbb")
    check("R13 · a one-day story with a user link pointing INTO it still has no thread",
          one_day, None)
    t2 = R.thread(THREAD_ID)
    user = [e for e in t2["entries"] if e["link_type"] == "user"]
    check("R13 · the user link is a thread entry", len(user), 1)
    check("R13 · it says who added it, and carries its link_id and target story",
          (user[0]["added_by"], user[0]["link_id"], user[0]["story_id"]),
          ("you", link_id, "2026-10-01:bbb"))
    check("R13 · its date is the target's latest date", user[0]["date"], "2026-10-01")
    check("R13 · the thread counts what you added", t2["entries_added_by_you"], 1)
    check("R13 · but `days` still counts days only (K1 D9)", t2["days"], 3)
    check("R13 · the user entry is real data, not a fixture", user[0]["fixture"], False)

    link2 = R.add_user_link("2026-10-01:ccc", ["c1"], THREAD_ID, ["a1"])
    alone = R.thread("2026-10-01:ccc")
    check("R13 · a one-day story GIVEN a link now has a thread",
          None if alone is None else (alone["days"], alone["entries_added_by_you"]),
          (1, 1))
    R.remove_user_link(link2)
    check("R13 · removing it takes the thread away again", R.thread("2026-10-01:ccc"), None)
    check("error · removing it twice is a LookupError",
          raises(lambda: R.remove_user_link(link2), LookupError), True)
    check("R13 · nothing was written into K2's links",
          sqlite3.connect(str(_intel)).execute(
              "SELECT COUNT(*) FROM links WHERE role = 'user'").fetchone()[0], 0)
    check("R13 · K2's link count is untouched",
          sqlite3.connect(str(_intel)).execute(
              "SELECT COUNT(*) FROM links").fetchone()[0], len(PARENTS))

    R.close_all()


# ------------------------------------------------------------------ FP-6 (4 Nov)
#
# get_story · the answer-pin snapshot (TC-14) · the passages() snapshot check.
#
# PASS LINES (K3 final pass brief §6 FP-6, fixed before the run):
#   - get_story on the real story 2026-10-30:9bbbe5763293 lists 6 articles, in points
#     order (best seat points, then paper, then date) -- read-only on the real data.
#   - on every story of one full real day, article_count = its members in corpus().
#   - news.db / search.db mtimes unchanged by get_story.
#   - pin snapshot equals the answer's fields (question, sentences with cites,
#     passage_set_id, model, model_id, verdict, not_covered, at) and survives a hide
#     and a replacement of the stored answer; unknown answer -> LookupError, no write.
#   - passages() check: an answer written on a copy (fake client), then its articles
#     changed in COPIES of news.db and search.db -> passages() rows identical.
#   - the real intel.db is never written (fingerprinted outside the suite as well).
# A failed line is answered by error analysis, not by moving the line.

import re                                            # noqa: E402

FP6_REAL_STORY = "2026-10-30:9bbbe5763293"
FP6_REAL_DAY = "2026-10-30"
FP6_ROW_KEYS = sorted(["text_hash", "headline", "paper", "date", "kind", "page_link",
                       "page_links_more", "snippet"])
FP6_STORY_KEYS = sorted(["story_id", "headline", "dates", "papers", "paper_names",
                         "article_count", "articles", "fixture"])


class FakeWriter:
    """The only "model" here: replies built from the prompt, no network, no cost. The
    writer cites the first and last article it was shown; sentences carry no number, so
    the number check has nothing to drop."""

    def __init__(self):
        self.calls = []

    def __call__(self, prompt, system, purpose, model, schema):
        if schema == "rewrite":
            question = prompt.rsplit("NEW QUESTION: ", 1)[-1].strip()
            return {"decision": "new_topic", "queries": [question]}, 1200, 40
        if schema == "text":
            return {"text": "Earlier: a question."}, 300, 40
        self.calls.append((purpose, model))
        ids = re.findall(r'<article id="([^"]+)"', prompt)
        return ({"sentences": [{"text": "The plan moved ahead.", "cites": [ids[0]]},
                               {"text": "Officials spoke about it.", "cites": [ids[-1]]}]},
                3000, 200)


def build_mock_search(path):
    """search.db for the mock: one passage per text, header + text, word index only (no
    vectors: the tests run with the embedding service 'down', so mode is fts_only)."""
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE passages (passage_id INTEGER PRIMARY KEY, text_hash TEXT, ord INTEGER,
                               start_word INTEGER, words INTEGER, headline TEXT, text TEXT);
        CREATE VIRTUAL TABLE passages_fts USING fts5(headline, text, content='passages',
                               content_rowid='passage_id', tokenize='porter unicode61');
        CREATE TABLE passage_vectors (passage_id INTEGER PRIMARY KEY, dim INTEGER,
                               vec BLOB, model TEXT, variant TEXT, embedded_at TEXT);
    """)
    for pid, (h, head, _w, _seats, _kind) in enumerate(MOCK_TEXTS, 1):
        text = f"{head}\n\n{head}. The canal plan and the water board, as reported."
        con.execute("INSERT INTO passages VALUES (?,?,0,0,?,?,?)",
                    (pid, h, len(text.split()), head, text))
        con.execute("INSERT INTO passages_fts (rowid, headline, text) VALUES (?,?,?)",
                    (pid, head, text))
    con.commit()
    con.close()


def _ro_rows(path, sql, args=()):
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return con.execute(sql, args).fetchall()
    finally:
        con.close()


def _points_order_ok(story, seat_of):
    keys = [(-(seat_of[a["text_hash"]]["points"] or 0), a["paper"] or "", a["date"] or "",
             a["text_hash"]) for a in story["articles"]]
    return keys == sorted(keys)


def _passages_snapshot_check(label, news, search_p, story_id, question):
    """Write an answer with the fake client on these COPIES, then change its articles in
    the news.db and search.db copies, and read passages() before and after."""
    import p3_reader as R
    import p3_search as SR
    SR._CLIENT = fake = FakeWriter()
    SR.new_conversation()
    events = list(SR.ask(question, scope={"story_id": story_id}))
    answer = events[-1].get("answer") or {}
    check(f"{label} · passages() · the fake writer wrote an answer on the copy",
          (events[-1]["type"], bool(answer.get("sentences")), len(fake.calls)),
          ("done", True, 1))
    before = R.passages(answer["passage_set_id"])
    hashes = sorted({p["text_hash"] for p in before})
    qs = ",".join("?" * len(hashes))
    con = sqlite3.connect(news)
    con.execute(f"UPDATE texts SET headline = 'CHANGED', body = 'CHANGED' "
                f"WHERE text_hash IN ({qs})", hashes)
    con.execute(f"UPDATE placements SET extra = '{{}}', page = 99 "
                f"WHERE text_hash IN ({qs})", hashes)
    con.commit()
    con.close()
    con = sqlite3.connect(search_p)
    con.execute(f"UPDATE passages SET text = 'CHANGED', headline = 'CHANGED' "
                f"WHERE text_hash IN ({qs})", hashes)
    con.commit()
    con.close()
    R.close_all()
    check(f"{label} · passages() · the change really landed in the copies",
          ({R.article(h)["headline"] for h in hashes},
           {t for (t,) in _ro_rows(search_p, f"SELECT DISTINCT text FROM passages "
                                             f"WHERE text_hash IN ({qs})", hashes)}),
          ({"CHANGED"}, {"CHANGED"}))
    check(f"{label} · passages() · rows identical before and after the change",
          R.passages(answer["passage_set_id"]), before)
    check(f"{label} · passages() · and not empty", bool(before), True)
    SR._CLIENT = None
    return answer


def fp6_mock(tmp):
    work, news, intel = mock_world(tmp)
    search_p = Path(tmp) / "search.db"
    build_mock_search(search_p)
    # a3 runs on: a page link to its continuation (R5), so page_links_more has an entry
    con = sqlite3.connect(news)
    con.execute("UPDATE placements SET extra = ? WHERE text_hash = 'a3'",
                (json.dumps({"paper": "2026-10-03_Mint", "continues_to": 5}),))
    con.commit()
    con.close()
    import p3_reader as R
    import p3_search as SR
    saved = (SR.NEWS_DB, SR.INTEL_DB, SR.SEARCH_DB, SR.embed_query, SR._CLIENT)
    SR.NEWS_DB, SR.INTEL_DB, SR.SEARCH_DB = news, intel, search_p
    SR.embed_query = lambda q, **kw: None            # the service "down": fts_only
    R.close_all()
    try:
        stamps = {p.name: p.stat().st_mtime_ns for p in (news, search_p)}
        world = SR.corpus(news=R._news(), intel=R._intel_ro())
        st = R.get_story(THREAD_ID)
        check("FP-6 mock · get_story has exactly the contract's keys", sorted(st), FP6_STORY_KEYS)
        check("FP-6 mock · every article row has exactly the contract's keys",
              {tuple(sorted(a)) for a in st["articles"]}, {tuple(FP6_ROW_KEYS)})
        check("FP-6 mock · every member article, once",
              (st["article_count"], sorted(a["text_hash"] for a in st["articles"])),
              (3, ["a1", "a2", "a3"]))
        check("FP-6 mock · membership = corpus()['story_of_hash']",
              sorted(a["text_hash"] for a in st["articles"]),
              sorted(h for h, s in world["story_of_hash"].items() if s == THREAD_ID))
        check("FP-6 mock · best-placed first (points, then paper, then date)",
              _points_order_ok(st, world["stories"][THREAD_ID]["seat_of_hash"]), True)
        a2 = next(a for a in st["articles"] if a["text_hash"] == "a2")
        check("FP-6 mock · a text printed twice is shown at its best seat (front page)",
              (a2["paper"], a2["page_link"]["pdf_page"], a2["page_link"]["paper"]),
              ("The Hindu", 1, "The Hindu"))
        check("FP-6 mock · papers counts every seat; paper_names names them",
              (st["papers"], st["paper_names"]), (2, ["Mint", "The Hindu"]))
        check("FP-6 mock · the headline is corpus' (TC-20 rule, one copy)",
              st["headline"], world["stories"][THREAD_ID]["headline"])
        check("FP-6 mock · dates are the story's days", st["dates"], list(MOCK_DATES))
        a3 = next(a for a in st["articles"] if a["text_hash"] == "a3")
        check("FP-6 mock · page_links_more carries the jump, with its paper",
              [(l["pdf_page"], l["reason"], l["paper"]) for l in a3["page_links_more"]],
              [(5, "continues_to", "Mint")])
        check("FP-6 mock · the others have no extra page",
              [a["page_links_more"] for a in st["articles"] if a["text_hash"] != "a3"],
              [[], []])
        check("FP-6 mock · the snippet is the lead passage, header stripped",
              a3["snippet"].startswith("Canal plan cleared. The canal plan"), True)
        check("FP-6 mock · a one-day story has its one article",
              R.get_story("2026-10-01:bbb")["article_count"], 1)
        check("FP-6 mock · error · an unknown story is a LookupError",
              raises(lambda: R.get_story("2026-09-09:zzz"), LookupError), True)
        check("FP-6 mock · error · a non-day-group (later day) id is not a story",
              raises(lambda: R.get_story("2026-10-02:aaa"), LookupError), True)
        check("FP-6 mock · error · a non-string id is a ValueError",
              raises(lambda: R.get_story(None), ValueError), True)
        check("FP-6 mock · get_story writes nothing to news.db / search.db",
              {p.name: p.stat().st_mtime_ns for p in (news, search_p)}, stamps)

        # --- the passages() snapshot check, then TC-14 on the same answer -------------
        answer = _passages_snapshot_check("FP-6 mock", news, search_p, THREAD_ID,
                                          "What happened to the canal plan?")
        check("FP-6 mock · ITEM_TYPES has 'answer'", "answer" in R.ITEM_TYPES, True)
        board = R.create_board("answers")
        n_pins = lambda: _ro_rows(intel, "SELECT COUNT(*) FROM pins")[0][0]
        before_bad = n_pins()
        check("FP-6 mock · TC-14 · an unknown answer is a LookupError",
              raises(lambda: R.pin(board, "answer", {"answer_id": "ans_nope"}, {}),
                     LookupError), True)
        check("FP-6 mock · TC-14 · an answer pin with no answer_id is a ValueError",
              raises(lambda: R.pin(board, "answer", {"question": "x"}, {}), ValueError), True)
        check("FP-6 mock · TC-14 · and neither wrote a pin", n_pins(), before_bad)
        sent = {"answer_id": answer["answer_id"], "question": "page's copy",
                "snapshot": {"question": "forged"}}
        R.pin(board, "answer", sent, {"conversation_id": answer["conversation_id"]})
        want = {k: answer[k] for k in R.ANSWER_SNAPSHOT_KEYS}
        snap = R.list_pins(board)[0]["payload"]["snapshot"]
        check("FP-6 mock · TC-14 · the snapshot equals the answer's fields", snap, want)
        check("FP-6 mock · TC-14 · sentences keep their cites",
              [s["cites"] for s in snap["sentences"]],
              [s["cites"] for s in answer["sentences"]])
        check("FP-6 mock · TC-14 · the page's own payload keys are kept beside it",
              R.list_pins(board)[0]["payload"]["question"], "page's copy")
        con = sqlite3.connect(intel)
        con.execute("UPDATE turns SET hidden_at = '2026-11-04T10:00:00', hidden_by = 'hide', "
                    "hide_action_id = 'h_test' WHERE json_extract(payload, '$.answer_id') = ?",
                    (answer["answer_id"],))
        con.execute("UPDATE turns SET payload = json_set(payload, '$.sentences', json('[]'), "
                    "'$.verdict', 'empty') WHERE json_extract(payload, '$.answer_id') = ?",
                    (answer["answer_id"],))
        con.commit()
        con.close()
        R.close_all()
        check("FP-6 mock · TC-14 · the stored answer really was hidden and changed",
              (SR.get_answer(answer["answer_id"])["sentences"],
               _ro_rows(intel, "SELECT hidden_by FROM turns WHERE "
                               "json_extract(payload, '$.answer_id') = ?",
                        (answer["answer_id"],))[0][0]), ([], "hide"))
        check("FP-6 mock · TC-14 · the snapshot survives the hide and the replacement",
              R.list_pins(board)[0]["payload"]["snapshot"], want)
        check("FP-6 mock · TC-14 · a hidden answer can still be pinned (get_answer finds it)",
              R.pin(board, "answer", {"answer_id": answer["answer_id"]}, {})[:2], "p_")
    finally:
        SR.NEWS_DB, SR.INTEL_DB, SR.SEARCH_DB, SR.embed_query, SR._CLIENT = saved
        R.close_all()
        os.environ.pop("P3_READER_INTEL_DB", None)
        os.environ.pop("P3_READER_NEWS_DB", None)


def fp6_real(tmp):
    """Read-only get_story checks on the REAL data, then the passages() check on COPIES.
    Runs before `on_real`, so FP-8's known crash there (digests moved, X10) cannot hide
    it."""
    import p3_reader as R
    import p3_search as SR
    real_search = ROOT / "data" / "search.db"
    stamps = {p.name: p.stat().st_mtime_ns for p in (REAL_NEWS, real_search)}
    copy = Path(tmp) / "intel_fp6_copy.db"
    src = sqlite3.connect(f"file:{REAL_INTEL}?mode=ro", uri=True)
    dst = sqlite3.connect(copy)
    src.backup(dst)
    src.close()
    dst.close()
    work = Path(tmp) / "work"
    work.mkdir()
    os.environ["P3_READER_NEWS_DB"] = str(REAL_NEWS)
    os.environ["P3_READER_INTEL_DB"] = str(copy)
    os.environ["P3_READER_WORK"] = str(work)
    saved = (SR.NEWS_DB, SR.INTEL_DB, SR.SEARCH_DB, SR.embed_query, SR._CLIENT)
    R.close_all()
    try:
        world = SR.corpus(news=R._news(), intel=R._intel_ro())
        members = {}
        for h, sid in world["story_of_hash"].items():
            members.setdefault(sid, set()).add(h)
        st = R.get_story(FP6_REAL_STORY)
        seat_of = world["stories"][FP6_REAL_STORY]["seat_of_hash"]
        check("FP-6 real · 2026-10-30:9bbbe5763293 lists 6 articles (K1 saw '3 of 6')",
              (st["article_count"], len(st["articles"])), (6, 6))
        check("FP-6 real · in points order (points, then paper, then date)",
              _points_order_ok(st, seat_of), True)
        check("FP-6 real · its headline is never blank", bool(st["headline"].strip()), True)
        day = sorted(sid for sid, row in world["stories"].items()
                     if FP6_REAL_DAY in row["dates"])
        got = {sid: R.get_story(sid) for sid in day}
        check(f"FP-6 real · every story of {FP6_REAL_DAY} ({len(day)}): article_count = "
              f"its members in corpus()",
              [sid for sid in day if got[sid]["article_count"] != len(members[sid]) or
               {a["text_hash"] for a in got[sid]["articles"]} != members[sid]], [])
        check("FP-6 real · and every one is in points order",
              [sid for sid in day if not _points_order_ok(
                  got[sid], world["stories"][sid]["seat_of_hash"])], [])
        check("FP-6 real · papers = len(paper_names) on every one",
              [sid for sid in day if got[sid]["papers"] != len(got[sid]["paper_names"])], [])
        check("FP-6 real · no blank headline that day",
              [sid for sid in day if not (got[sid]["headline"] or "").strip()], [])
        check("FP-6 real · every page link points at a PDF that exists",
              [a["page_link"]["pdf"] for s in got.values() for a in s["articles"]
               if not (ROOT / a["page_link"]["pdf"]).exists()], [])
        check("FP-6 real · news.db / search.db mtimes unchanged by get_story",
              {p.name: p.stat().st_mtime_ns for p in (REAL_NEWS, real_search)}, stamps)
        CHECKS.append(("FP-6 real · (count) stories checked on the day", len(day), len(day)))

        # passages() snapshot check on COPIES of news.db and search.db
        news_c, search_c = Path(tmp) / "news_copy.db", Path(tmp) / "search_copy.db"
        for a, b in ((REAL_NEWS, news_c), (real_search, search_c)):
            s_, d_ = sqlite3.connect(f"file:{a}?mode=ro", uri=True), sqlite3.connect(b)
            s_.backup(d_)
            s_.close()
            d_.close()
        os.environ["P3_READER_NEWS_DB"] = str(news_c)
        SR.NEWS_DB, SR.INTEL_DB, SR.SEARCH_DB = news_c, copy, search_c
        SR.embed_query = lambda q, **kw: None        # never the live service (8766)
        R.close_all()
        _passages_snapshot_check("FP-6 real copies", news_c, search_c, FP6_REAL_STORY,
                                 "Where is the cyclone expected to make landfall?")
        check("FP-6 real · the real news.db / search.db were not touched by the copies",
              {p.name: p.stat().st_mtime_ns for p in (REAL_NEWS, real_search)}, stamps)
    finally:
        SR.NEWS_DB, SR.INTEL_DB, SR.SEARCH_DB, SR.embed_query, SR._CLIENT = saved
        R.close_all()


# ------------------------------------------------------------------------ 2. real data

def on_real(tmp):
    copy = Path(tmp) / "intel_real_copy.db"
    shutil.copy2(REAL_INTEL, copy)
    os.environ["P3_READER_NEWS_DB"] = str(REAL_NEWS)
    os.environ["P3_READER_INTEL_DB"] = str(copy)
    os.environ["P3_READER_WORK"] = str(ROOT / "_work")
    # X10 moved the old digests into data/digests/, where the daily run writes the new
    # ones: read both folders, as the app does.
    os.environ["P3_READER_DIGESTS"] = str(ROOT / "data" / "digests")
    import p3_reader as R
    R.close_all()

    digest = R.get_digest("latest")
    check("real · the latest digest loads", bool(digest and digest.get("stories")), True)
    check("real · the old digests are listed from data/digests/ (X10)",
          {"digest_live", "k3_b1_3_digest_2026-10-11"} <=
          {d["ref"] for d in R.list_digests()}, True)
    check("real · run_status is ready", R.run_status()["state"], "ready")

    rows = [r for s in digest["stories"] for r in s["coverage"]["rows"]]
    arts = [R.article(r["text_hash"]) for r in rows[:25]]
    check("real · every article has a real body",
          all(a and len(a["body"].split()) > 20 for a in arts), True)
    check("real · nothing is flagged as a fixture", [a["fixture"] for a in arts[:3]],
          [False, False, False])
    check("real · every page link points at a PDF that exists",
          [a["page_link"]["pdf"] for a in arts
           if a["page_link"] and not (ROOT / a["page_link"]["pdf"]).exists()], [])
    decked = [a for a in arts if a["deck"]]
    check("real · some articles have a deck from placements", bool(decked), True)
    datelined = [a for a in arts if a["dateline"]]
    check("real · a dateline is never also a byline",
          [a["byline"] for a in datelined], [""] * len(datelined))

    threads = [(s["story_id"], R.thread(s["story_id"])) for s in digest["stories"]]
    live = [(sid, t) for sid, t in threads if t]
    check("real · at least one story has a thread", bool(live), True)
    sid, t = live[0]
    check("real · entries are newest first",
          [e["date"] for e in t["entries"]] ==
          sorted([e["date"] for e in t["entries"]], reverse=True), True)
    check("real · the thread has as many days as it has day entries",
          t["days"], len([e for e in t["entries"] if e["link_type"] != "user"]))
    check("real · exactly one entry is typed `start`",
          [e["link_type"] for e in t["entries"]].count("start"), 1)
    check("real · the start entry carries the thread's own id",
          [e["entry_id"] for e in t["entries"] if e["link_type"] == "start"], [sid])
    check("real · no invented link type appears anywhere",
          sorted({e["link_type"] for _s, th in live for e in th["entries"]} -
                 {"start", None, "user"}), [])
    check("real · every entry's pages carry their paper",
          all("paper" in p for _s, th in live for e in th["entries"] for p in e["pages"]),
          True)

    check("real · a story with one day has no thread",
          [sid for sid, t in threads if t is None and
           len({r["date"] for r in next(s for s in digest["stories"]
                                        if s["story_id"] == sid)["coverage"]["rows"]}) > 1],
          [])

    check("real · cost_today reads the cap off the digest knob",
          R.cost_today()["cap_usd"], (digest.get("knobs") or {}).get("daily_cost_cap_usd"))
    check("real · today's spend on the copy is a number",
          isinstance(R.cost_today()["spent_usd"], float), True)

    # writes land on the COPY, and K2's tables are untouched by them
    before = dict(sqlite3.connect(f"file:{copy}?mode=ro", uri=True).execute(
        "SELECT name, (SELECT COUNT(*) FROM members) FROM sqlite_master "
        "WHERE name='members'"))
    R.mark_read([rows[0]["text_hash"]])
    board = R.create_board("real check")
    R.pin(board, "story", {"text_hashes": [rows[0]["text_hash"]]}, {"paper": "x"})
    after = dict(sqlite3.connect(f"file:{copy}?mode=ro", uri=True).execute(
        "SELECT name, (SELECT COUNT(*) FROM members) FROM sqlite_master "
        "WHERE name='members'"))
    check("real · K2's members table is untouched by K3's writes", after, before)
    check("real · the write landed on the copy",
          bool(R.read_state([rows[0]["text_hash"]])[rows[0]["text_hash"]]), True)

    R.close_all()
    # "the real intel.db was not written to" is checked in main(), by the file's content
    # before and after the whole suite (it once counted read_state rows here, which the
    # app's own use fills).


def main():
    mock_only = "--mock" in sys.argv
    kept = {k: os.environ.get(k) for k in
            ("P3_READER_NEWS_DB", "P3_READER_INTEL_DB", "P3_READER_WORK",
             "P3_READER_DIGESTS")}
    before = real_fingerprint() if REAL_INTEL.exists() else None
    try:
        with tempfile.TemporaryDirectory() as tmp:
            on_mock(tmp)
        with tempfile.TemporaryDirectory() as tmp:
            fp6_mock(tmp)
        if mock_only:
            print("real data: SKIPPED (--mock)")
        elif not (REAL_NEWS.exists() and REAL_INTEL.exists()):
            print("real data: SKIPPED (databases not found)")
        else:
            # FP-6's real checks first, so a crash in on_real can never hide them.
            with tempfile.TemporaryDirectory() as tmp:
                fp6_real(tmp)
            with tempfile.TemporaryDirectory() as tmp:
                try:
                    on_real(tmp)
                except Exception as exc:                     # noqa: BLE001
                    # Reported as a FAILED check, not swallowed: a crash still fails
                    # the suite, and the checks above print.
                    import traceback
                    where = traceback.extract_tb(exc.__traceback__)[-1]
                    check("real · on_real ran to the end",
                          f"{type(exc).__name__} at line {where.lineno}: {exc}", "no crash")
    finally:
        for k, v in kept.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    if before is not None:
        check("the real intel.db was not written to (content same before and after)",
              real_fingerprint(), before)

    bad = [(n, g, w) for n, g, w in CHECKS if g != w]
    for n, g, w in bad:
        print(f"FAIL {n}: got {g!r}, want {w!r}")
    print(f"{len(CHECKS) - len(bad)}/{len(CHECKS)} checks passed "
          f"({'mock only' if mock_only else 'mock + real data'})")
    print("the real data/intel.db was not opened for writing by this test")
    print("PASS" if not bad else "FAIL")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
