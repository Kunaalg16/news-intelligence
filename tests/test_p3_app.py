"""
test_p3_app.py -- TEST (free, no API key, no internet). Checks the K1 app v0:
src/p3_app_double.py (the §4 interface) and src/p3_app.py (the routes). The page itself
is checked by the markup and contrast parts that arrive with A7.

WHAT IT DOES
    Six parts, none of which touches a database or the reader's real state file.
    1. MOCK: a synthetic digest JSON + a synthetic archive of empty PDFs in a temporary
       directory, with the double pointed at them. Every function of §4 is exercised
       there -- read on leave, keep unread, new-since, boards, pins, follows, feedback,
       prefs, visits, cost, the invented thread and the invented article body.
    2. REAL DATA (read-only): the two digests in `_work/`, with the state file sent to a
       temporary path so the run cannot touch Kunaal's own read state. This is where the
       numbers that the screens will show are pinned.
    3. ROUTES: every route function of `p3_app.py` called DIRECTLY, on the real digests.
       Not through a client: the sandbox this was built in refuses to bind a local port
       (checked at A0), and `httpx` is not installed, so FastAPI's TestClient is out on
       two counts. The route functions are plain `def`s for exactly this reason.
    4. THE PAGE'S LOGIC: app.js run under JavaScriptCore, which macOS ships, so the
       words the header and the drawer put on screen are pinned exactly like the Python.
       The brief expected this to be impossible here; only the DOM-building functions
       and the look of the thing still need Kunaal's eye.
    5. OWNERSHIP: a grep over both modules for anything they must not import.

WHY IT EXISTS
    Every screen of the app reads through this one module, so a wrong answer here is a
    wrong answer everywhere, silently. It is also K1's request to K3 (brief §4, C1): the
    behaviour pinned below is what K3's real functions will have to match, so the checks
    are written as the rules they are, not as whatever the code happens to do.

PASS LINES (fixed before the run; brief §4 rules, §7 populations, §8.7 states)
    Digests
    - `list_digests()` finds both real digests, newest `built_at` first; a JSON file in
      `_work/` that is not a digest is never offered as one.
    - `get_digest("latest")` is the newest by `built_at`; an unknown ref is None, not a
      guess at another digest.
    - `run_status()` says "ready"; with the `processing` fixture on it says "processing".
    Read, and why it is on leave
    - A story is READ only when EVERY coverage hash has a read_at; one hash short is not
      READ.
    - With keep-unread on, `mark_read` marks nothing of that story; switching it off lets
      the next leave mark it; switching it ON on a READ story makes it unread again.
    - `new_since_read` on a story nothing has been read of is [] -- never the whole
      coverage. Read the story, add an article to it, and that one hash is the answer.
    Boards, pins, follows, feedback, prefs, visits
    - Boards come back most recently used first; pinning touches that board's last_used_at;
      `pin_count` counts that board's pins only; `unpin` removes exactly one pin.
    - A pin stores the story's text_hashes as they were at that moment.
    - `follow` / `list_follows` round-trip; unfollowing removes the story.
    - `feedback` stores kind, id, vote and note; `get_pref` of an unset key is None.
    - Two visits to the same digest inside the sitting knob are one visit; `last_visit
      (opened_only=True)` ignores a visit where no story was opened.
    Cost (§7, §A8, decision D1)
    - `cost_today()` = the sum of write-up and story-so-far costs of the LATEST digest.
    - The cap is the digest's own `daily_cost_cap_usd` knob, never a hard-coded number;
      a digest with no such knob reports cap None.
    - With the `cap_reached` fixture on, spent equals the cap (state H5).
    The invented thread (R2)
    - A day-1 story has NO thread (None), so the page renders no thread section (H3).
    - A thread's entries are one per day, newest first, each with the day before as its
      parent; the entry on the thread's root date carries the thread's own id, so a
      story-so-far chip (which cites that id) can resolve to an entry.
    - Across the fixture threads, every link type the drawing handles appears at least
      once: run_up, start, follow_up, untyped (None), continuing. In one thread the
      run-up is the oldest entry and "where it began" is the one above it, as the
      timeline mockup places them.
    - A thread of 4+ days gains the one run-up day, so `thread.days` may be one more than
      `story_so_far.days`: they count different things.
    - `thread.articles` is the sum of its entries' articles; `thread.papers` is the number
      of DISTINCT papers across the thread, not the sum.
    - EVERY page link in EVERY invented entry points at a PDF that really exists under
      `archive/`, on the entry's own date. No invented link is a dead end.
    - The thread carries `fixture: true`, and so does any entry whose pages were invented.
    The invented article (R3)
    - `article()` returns the REAL headline, paper, date and page link from the coverage
      row, an invented body, and `fixture: true`.
    - The body is at least the knob's minimum words, so the reading view always has
      enough text to need several paragraphs; an unknown hash is None.
    The routes (brief §5, acceptance checks 1 and 8)
    - Every route the brief lists exists, and each one answers with JSON that can be
      serialised, for BOTH digest files.
    - An unknown digest ref is 404, not a different digest; an unknown article is 404.
    - A day-1 story's thread is 404, which is how the page knows to render no thread (H3).
    - A posted body missing a required field is 400 and says which field; a feedback vote
      that is not up/down and a target_kind that is not story/thread_link are 400.
    - Flipping a fixture switch through the dev route changes what /api/status says, so
      the states of §8.7 can be seen in the browser and not only in this test.
    - `/api/flags` sends all ten flags, with `theme_bar_strip` off (R9).
    - Paragraphs: every paragraph but the last reaches the knob's word count, no sentence
      is split (the paragraphs joined back together are the body again, word for word),
      and every paragraph ends on sentence punctuation.
    - The PDF route serves a real archive PDF; `../` climbing out is 403; a name that does
      not end .pdf is 404; a date-shaped path that holds no such file is 404.
    - Against a planted file that really exists outside the archive, in a temporary
      archive: `../`, an absolute path, and a symlink inside the archive pointing out of
      it are all refused; a climb that lands back inside is still served.
    Ownership (brief §2.1, acceptance check 9)
    - The double imports no sqlite3, no p3_store, no p3_digest, and nothing that reaches
      the network. It writes exactly one file: `_work/k1_app_state.json`.
    - `p3_app.py` has exactly ONE data import, `p3_app_double`, so integration is the
      one-line swap the brief asks for, and it too imports no database.
    A failed line is answered by reading the failure, not by moving the line.

WHEN TO RUN
    After every change to src/p3_app_double.py. Under two seconds.

USAGE (from ~/news-intel)
    .venv/bin/python tests/test_p3_app.py
"""

import json
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import p3_app_double as D

CHECKS = []


def check(name, got, want):
    CHECKS.append((name, got, want))


# ------------------------------------------------------------------ the mock world

MOCK_DATES = ["2026-09-28", "2026-09-29", "2026-09-30", "2026-10-01", "2026-10-02",
              "2026-10-03", "2026-10-05"]
MOCK_PAPERS = ["The Hindu", "Times of India", "Mint", "Business Line"]


def row(paper, date, page, text_hash, words=500, kind="news", byline="Our reporter"):
    return {"paper": paper, "date": date, "page": page,
            "page_link": {"pdf": f"archive/{date}/{date}_{paper}.pdf",
                          "pdf_page": page, "label": f"PDF p{page}"},
            "words": words, "kind": kind, "byline": byline,
            "headline": f"A printed headline on {paper} p{page}",
            "text_hash": text_hash, "points": 3}


def mock_story(story_id, dates, rows, developing, ssf_days=None, cost=0.001):
    story = {"story_id": story_id, "group_ids": ["g1"], "score": 10, "rank": 1,
             "paper_points": {r["paper"]: 3 for r in rows}, "dates": dates,
             "developing": developing,
             "headline": {"text": f"Headline for {story_id}", "source_text_hash":
                          rows[0]["text_hash"], "fallbacks": []},
             "one_liner": {"text": "A stored one-line summary.",
                           "source_text_hash": rows[0]["text_hash"], "fallback": False},
             "theme": {"topic": "Taxes & budget", "stories_21d": 8, "trend": None,
                       "no_trend_because": "only 2 publication days in the earlier weeks"},
             "coverage": {"articles": len(rows),
                          "papers": len({r["paper"] for r in rows}), "rows": rows},
             "views": [], "caveats": {"paid_content": False, "paid_only": False,
                                      "single_source": False, "partial_text": False},
             "links": [],
             "writeup": {"sentences": [{"text": "A written sentence.",
                                        "cites": [rows[0]["text_hash"]]}],
                         "cited_ids": [rows[0]["text_hash"]], "input_articles": 2,
                         "input_words": 900, "model": "claude-haiku-5-5",
                         "prompt_version": "w1.0", "attempts": 1, "drops": [],
                         "fallback": None, "cost_usd": cost, "used_text": "...",
                         "short": False}}
    if ssf_days:
        story["story_so_far"] = {
            "thread_id": story_id, "days": ssf_days,
            "sentences": [{"text": "The story so far.", "cites": [story_id]}],
            "key_dates": [{"date": "2026-10-02", "what": "Something happened."}],
            "model": "claude-haiku-5-5", "prompt_version": "s1.0", "cached": False,
            "drops": [], "cost_usd": 0.0006, "shown": ssf_days >= 5,
            "reason": f"thread has {ssf_days} days"}
    return story


def build_mock_world(tmp):
    """A synthetic archive of empty PDFs and one synthetic digest, and the double aimed
    at them. Returns the digest object."""
    tmp = Path(tmp)
    (tmp / "_work").mkdir()
    for date in MOCK_DATES:
        day = tmp / "archive" / date
        day.mkdir(parents=True)
        for paper in MOCK_PAPERS:
            (day / f"{date}_{paper}.pdf").write_bytes(b"%PDF-1.4 mock")

    # three stories: a day-1 story (no thread), a 3-day story, a 5-day story
    one_day = mock_story("2026-10-05:aaa000000001",
                         ["2026-10-05"],
                         [row("Mint", "2026-10-05", 3, "h-one-1"),
                          row("The Hindu", "2026-10-05", 1, "h-one-2", byline="")],
                         developing=False, ssf_days=1)
    three_day = mock_story("2026-10-01:bbb000000002",
                           ["2026-10-01", "2026-10-02", "2026-10-03"],
                           [row("The Hindu", "2026-10-01", 1, "h-three-1"),
                            row("Mint", "2026-10-02", 4, "h-three-2"),
                            row("Times of India", "2026-10-03", 7, "h-three-3")],
                           developing=True, ssf_days=3)
    five_day = mock_story("2026-09-29:ccc000000003",
                          ["2026-09-29", "2026-09-30", "2026-10-01", "2026-10-02",
                           "2026-10-03"],
                          [row("The Hindu", "2026-09-29", 1, "h-five-1"),
                           row("Mint", "2026-09-30", 2, "h-five-2"),
                           row("Business Line", "2026-10-01", 5, "h-five-3"),
                           row("Times of India", "2026-10-02", 9, "h-five-4"),
                           row("The Hindu", "2026-10-03", 1, "h-five-5")],
                          developing=True, ssf_days=5)
    digest = {"built_at": "2026-10-05T07:00:00",
              "window": {"kind": "week", "from": "2026-09-29", "to": "2026-10-05",
                         "days": MOCK_DATES},
              "versions": {"digest": "d0.1"},
              "knobs": {"top_n": 10, "daily_cost_cap_usd": 0.5,
                        "story_so_far_from_day": 5, "page_label": "pdf_page"},
              "stories": [one_day, three_day, five_day],
              "dropped": {"zero_point": 3, "below_top_n": 9}, "report": {}}
    (tmp / "_work" / "digest_mock.json").write_text(json.dumps(digest))
    # an older digest, and a JSON file that is NOT a digest
    older = json.loads(json.dumps(digest))
    older["built_at"] = "2026-10-04T07:00:00"
    older["window"] = {"kind": "day", "from": "2026-10-03", "to": "2026-10-03",
                       "days": ["2026-10-03"]}
    older["knobs"] = {"top_n": 10, "page_label": "pdf_page"}   # no cap knob (D1)
    (tmp / "_work" / "digest_mock_older.json").write_text(json.dumps(older))
    (tmp / "_work" / "not_a_digest.json").write_text(json.dumps({"hello": "world"}))

    D.ROOT = tmp
    D.WORK = tmp / "_work"
    D.ARCHIVE = tmp / "archive"
    D.STATE_FILE = tmp / "_work" / "k1_app_state.json"
    return digest


# ------------------------------------------------------------------------ 1. mock

def mock(tmp):
    digest = build_mock_world(tmp)
    one_day, three_day, five_day = digest["stories"]

    # --- digests ------------------------------------------------------------------
    refs = [d["ref"] for d in D.list_digests()]
    check("both mock digests are listed, newest built_at first", refs,
          ["digest_mock", "digest_mock_older"])
    check("a JSON file that is not a digest is not offered as one",
          "not_a_digest" in refs, False)
    check("latest is the newest by built_at",
          D.get_digest("latest")["built_at"], "2026-10-05T07:00:00")
    check("a named ref loads that file",
          D.get_digest("digest_mock_older")["window"]["kind"], "day")
    check("an unknown ref is None, not another digest", D.get_digest("nope"), None)
    check("run_status is ready", D.run_status()["state"], "ready")
    D.FIXTURES["processing"] = True
    check("the processing fixture gives state H1", D.run_status()["state"], "processing")
    D.FIXTURES["processing"] = False

    # --- read on leave ------------------------------------------------------------
    hashes = D._story_hashes(three_day)
    check("a story nothing is read of is not READ",
          all(D.read_state(hashes).values()), False)
    D.mark_read(hashes[:2])
    check("one hash short of the coverage is not READ",
          all(D.read_state(hashes).values()), False)
    D.mark_read(hashes)
    check("every coverage hash read means READ", all(D.read_state(hashes).values()), True)

    # --- keep unread --------------------------------------------------------------
    five = D._story_hashes(five_day)
    D.set_keep_unread(five_day["story_id"], five, True)
    D.mark_read(five)
    check("keep unread on: leaving marks nothing",
          any(D.read_state(five).values()), False)
    D.set_keep_unread(five_day["story_id"], five, False)
    D.mark_read(five)
    check("keep unread off: the next leave marks it READ",
          all(D.read_state(five).values()), True)
    D.set_keep_unread(five_day["story_id"], five, True)
    check("keep unread on a READ story makes it unread again",
          any(D.read_state(five).values()), False)
    D.set_keep_unread(five_day["story_id"], five, False)

    # --- new since you read it ----------------------------------------------------
    check("nothing read yet means nothing is 'new since'",
          D.new_since_read(one_day), [])
    extra = row("Business Line", "2026-10-03", 6, "h-three-4")
    grown = json.loads(json.dumps(three_day))
    grown["coverage"]["rows"].append(extra)
    check("an article added after the read shows as the only new one",
          D.new_since_read(grown), ["h-three-4"])
    check("the grown story is no longer READ",
          all(D.read_state(D._story_hashes(grown)).values()), False)

    # --- boards and pins ----------------------------------------------------------
    b1 = D.create_board("Taxes")
    b2 = D.create_board("Protests")
    p1 = D.pin(b1, "story", {"story_id": three_day["story_id"], "text_hashes": hashes},
               source={"digest_ref": "digest_mock"})
    check("pinning makes that board the most recently used",
          [b["name"] for b in D.list_boards()][0], "Taxes")
    check("pin_count counts that board's pins only",
          {b["name"]: b["pin_count"] for b in D.list_boards()}, {"Taxes": 1, "Protests": 0})
    check("a pin stores the story's hashes as they were",
          D.list_pins(b1)[0]["payload"]["text_hashes"], hashes)
    p2 = D.pin(b1, "article", {"text_hash": "h-three-1"}, source={})
    D.unpin(p1)
    check("unpin removes exactly one pin",
          [p["pin_id"] for p in D.list_pins(b1)], [p2])
    check("the other board is untouched", D.list_pins(b2), [])

    # --- follows, feedback, prefs -------------------------------------------------
    D.follow(three_day["story_id"], hashes, True)
    check("a followed story is listed",
          [f["story_id"] for f in D.list_follows()], [three_day["story_id"]])
    D.follow(three_day["story_id"], hashes, False)
    check("unfollowing removes it", D.list_follows(), [])
    D.feedback("thread_link", "2026-10-01:bbb000000002", [], "down", note="not the same story")
    fb = D._load_state()["feedback"][-1]
    check("feedback stores kind, vote and note",
          [fb["target_kind"], fb["vote"], fb["note"]],
          ["thread_link", "down", "not the same story"])
    check("an unset pref is None", D.get_pref("thread_view"), None)
    D.set_pref("thread_view", "graph")
    check("a pref round-trips", D.get_pref("thread_view"), "graph")

    # --- visits -------------------------------------------------------------------
    check("no visit yet means no last visit", D.last_visit(), None)
    D.record_visit("digest_mock", digest["window"], opened_story=False)
    check("a visit where no story was opened does not count as a visit",
          D.last_visit(opened_only=True), None)
    check("but it is there when opened_only is off", bool(D.last_visit(opened_only=False)), True)
    D.record_visit("digest_mock", digest["window"], opened_story=True)
    check("two visits to the same digest in one sitting are one visit",
          len(D._load_state()["visits"]), 1)
    check("opening a story in that visit makes it count",
          bool(D.last_visit(opened_only=True)), True)

    # --- cost ---------------------------------------------------------------------
    cost = D.cost_today()
    check("spend is the write-up and story-so-far costs of the latest digest",
          round(cost["spent_usd"], 6), round(0.001 * 3 + 0.0006 * 3, 6))
    check("the cap is the digest's own knob", cost["cap_usd"], 0.5)
    D.FIXTURES["cap_reached"] = True
    check("the cap_reached fixture spends exactly the cap (H5)",
          D.cost_today()["spent_usd"], 0.5)
    D.FIXTURES["cap_reached"] = False
    D.FIXTURES["nothing_new"] = True
    check("the nothing_new fixture reports every story read (H2)",
          all(D.read_state(D._story_hashes(one_day)).values()), True)
    D.FIXTURES["nothing_new"] = False

    # --- the invented thread ------------------------------------------------------
    check("a day-1 story has no thread (H3)", D.thread(one_day["story_id"]), None)
    t3 = D.thread(three_day["story_id"])
    t5 = D.thread(five_day["story_id"])
    check("a 3-day thread has 3 entries", t3["days"], 3)
    check("a 5-day thread gains one run-up day", t5["days"], 6)
    check("entries are newest first",
          [e["date"] for e in t5["entries"]] ==
          sorted([e["date"] for e in t5["entries"]], reverse=True), True)
    check("the oldest entry of the long thread is the run-up",
          t5["entries"][-1]["link_type"], "run_up")
    check("'where it began' sits above the run-up",
          t5["entries"][-2]["link_type"], "start")
    check("one day is a follow-up", t5["entries"][-3]["link_type"], "follow_up")
    check("one day is untyped earlier coverage", t5["entries"][-4]["link_type"], None)
    check("the newest day is continuing", t5["entries"][0]["link_type"], "continuing")
    types = {e["link_type"] for t in (t3, t5) for e in t["entries"]}
    check("every link type the drawing handles appears across the fixture threads",
          types, {"run_up", "start", "follow_up", None, "continuing"})
    root_entries = [e for e in t5["entries"] if e["entry_id"] == five_day["story_id"]]
    check("the entry on the thread's root date carries the thread's own id, so a "
          "story-so-far chip resolves", len(root_entries), 1)
    check("that entry is the one 'where it began'",
          root_entries[0]["link_type"], "start")
    parents = [(e["entry_id"], e["parent_id"]) for e in t5["entries"]]
    check("each day's parent is the day before it",
          all(parents[i][1] == parents[i + 1][0] for i in range(len(parents) - 1)), True)
    check("thread.articles is the sum of its entries",
          t5["articles"], sum(e["articles"] for e in t5["entries"]))
    papers_seen = {p for e in t5["entries"] for p in e["paper_names"]}
    check("thread.papers counts distinct papers, not the sum",
          t5["papers"], len(papers_seen))
    check("the thread is flagged as fixture", t5["fixture"], True)
    check("one related item, attached to a real entry",
          [r["attaches_to"] in {e["entry_id"] for e in t5["entries"]} for r in t5["related"]],
          [True])
    missing = [pg["pdf"] for t in (t3, t5) for e in t["entries"] for pg in e["pages"]
               if not (D.ROOT / pg["pdf"]).exists()]
    check("every page link in every invented entry points at a PDF that exists",
          missing, [])
    wrong_date = [pg["pdf"] for t in (t3, t5) for e in t["entries"] for pg in e["pages"]
                  if e["date"] not in pg["pdf"]]
    check("and at a PDF of that entry's own date", wrong_date, [])
    check("every page link names its paper (K1's addition to R2)",
          all(pg.get("paper") for t in (t3, t5) for e in t["entries"] for pg in e["pages"]),
          True)

    # --- the invented article -----------------------------------------------------
    art = D.article("h-five-3")
    check("the article's headline is the real one from the coverage row",
          art["headline"], "A printed headline on Business Line p5")
    check("its paper, date and page are real",
          [art["paper"], art["date"], art["page_link"]["pdf_page"]],
          ["Business Line", "2026-10-01", 5])
    check("it is flagged as fixture", art["fixture"], True)
    check("the body is long enough to need several paragraphs",
          len(art["body"].split()) >= D.KNOBS["body_words_min"], True)
    check("an unknown text_hash is None", D.article("nope"), None)

    # --- what it wrote ------------------------------------------------------------
    written = sorted(p.name for p in (D.ROOT / "_work").iterdir()
                     if not p.name.startswith("digest") and p.name != "not_a_digest.json")
    check("the double wrote exactly one file", written, ["k1_app_state.json"])


# -------------------------------------------------------------------- 2. real data

def real():
    """Read-only on the two digests in _work/, with the state file sent somewhere safe."""
    D.ROOT = ROOT
    D.WORK = ROOT / "_work"
    D.ARCHIVE = ROOT / "archive"
    with tempfile.TemporaryDirectory() as tmp:
        D.STATE_FILE = Path(tmp) / "state.json"

        refs = [d["ref"] for d in D.list_digests()]
        check("both real digests are found", sorted(refs),
              ["digest_live", "k3_b1_3_digest_2026-10-11"])
        check("the live digest is the latest", refs[0], "digest_live")
        live = D.get_digest("latest")
        check("the live digest has ten stories", len(live["stories"]), 10)

        cost = D.cost_today()
        check("today's spend on the live digest", round(cost["spent_usd"], 6), 0.010941)
        check("the cap comes from the live digest's own knob", cost["cap_usd"], 0.5)

        day = D.get_digest("k3_b1_3_digest_2026-10-11")
        check("the day digest has no cap knob", "daily_cost_cap_usd" in day["knobs"], False)
        check("a story in the day digest has no story_so_far key at all",
              "story_so_far" in day["stories"][0], False)
        check("the day digest's window is a single day", day["window"]["kind"], "day")

        # the story whose story-so-far is shown, and its thread
        shown = [s for s in live["stories"] if (s.get("story_so_far") or {}).get("shown")]
        check("one live story shows its story so far", len(shown), 1)
        story = shown[0]
        tid = story["story_so_far"]["thread_id"]
        t = D.thread(tid)
        check("its thread is 5 story-so-far days plus the run-up day", t["days"], 6)
        cited = {c for s in story["story_so_far"]["sentences"] for c in s["cites"]}
        ids = {e["entry_id"] for e in t["entries"]}
        check("every day a story-so-far sentence cites is an entry in the thread",
              sorted(cited - ids), [])
        missing = [pg["pdf"] for e in t["entries"] for pg in e["pages"]
                   if not (ROOT / pg["pdf"]).exists()]
        check("every page link in the real fixture thread exists", missing, [])

        day1 = [s for s in live["stories"]
                if not s["developing"] and (s.get("story_so_far") or {}).get("days") == 1]
        check("live day-1 stories get no thread (H3)",
              [D.thread(s["story_id"]) is None for s in day1], [True] * len(day1))
        check("and there are day-1 stories to check", len(day1) > 0, True)

        first_hash = live["stories"][0]["coverage"]["rows"][0]["text_hash"]
        art = D.article(first_hash)
        check("a real coverage hash resolves to an article",
              art["headline"], live["stories"][0]["coverage"]["rows"][0]["headline"])
        check("its page link is the row's own",
              art["page_link"]["pdf"],
              live["stories"][0]["coverage"]["rows"][0]["page_link"]["pdf"])
        check("no real state file was written", D.STATE_FILE.exists(), False)


# ---------------------------------------------------------------------- 3. routes

def status_of(fn, *args, **kwargs):
    """The HTTP status a route function answers with, 200 when it simply returns."""
    from fastapi import HTTPException
    try:
        fn(*args, **kwargs)
    except HTTPException as e:
        return e.status_code
    return 200


def routes():
    """Every route called directly: no client, no server, no bound port (see A0)."""
    import p3_app as A

    D.ROOT = ROOT
    D.WORK = ROOT / "_work"
    D.ARCHIVE = ROOT / "archive"
    with tempfile.TemporaryDirectory() as tmp:
        D.STATE_FILE = Path(tmp) / "state.json"

        paths = {r.path for r in A.app.routes if hasattr(r, "path")}
        wanted = {"/", "/api/digest", "/api/digests", "/api/status",
                  "/api/article/{text_hash}", "/api/thread/{thread_id}", "/api/read",
                  "/api/keep_unread", "/api/follow", "/api/follows", "/api/boards",
                  "/api/pin", "/api/pin/{pin_id}", "/api/boards/{board_id}/pins",
                  "/api/feedback", "/api/pref/{key}", "/api/visit", "/api/cost",
                  "/pdf/{date}/{name:path}"}
        check("every route the brief lists exists", sorted(wanted - paths), [])

        # --- flags ----------------------------------------------------------------
        f = A.flags()
        check("all ten feature flags are sent to the page", len(f["flags"]), 10)
        check("the theme bar strip is off, its data not being in the JSON (R9)",
              f["flags"]["theme_bar_strip"], False)
        check("the cost header is on", f["flags"]["cost_display"], True)

        # --- the digests, both of them --------------------------------------------
        for ref in ("digest_live", "k3_b1_3_digest_2026-10-11"):
            got = A.digest(ref=ref)
            check(f"/api/digest serialises for {ref}", bool(json.dumps(got)), True)
            check(f"/api/digest returns ten stories for {ref}", len(got["stories"]), 10)
        check("/api/digest with no ref is the latest",
              A.digest()["built_at"], "2026-11-01T16:38:51")
        check("an unknown digest ref is 404, not another digest",
              status_of(A.digest, ref="nope"), 404)
        check("/api/digests lists both", len(A.digests()["digests"]), 2)
        check("/api/status is ready", A.status()["state"], "ready")
        check("/api/cost is the live digest's spend",
              round(A.cost()["spent_usd"], 6), 0.010941)

        # --- the fixture switches, which are how the states are seen ---------------
        A.set_fixtures(payload={"processing": True})
        check("flipping a fixture changes what /api/status says",
              A.status()["state"], "processing")
        A.set_fixtures(payload={"processing": False})
        check("an unknown fixture name is 400",
              status_of(A.set_fixtures, payload={"nope": True}), 400)

        # --- one article, and its paragraphs --------------------------------------
        live = A.digest(ref="digest_live")
        row = live["stories"][0]["coverage"]["rows"][0]
        art = A.article(row["text_hash"])
        check("the article's headline is the row's own", art["headline"], row["headline"])
        check("an unknown article is 404", status_of(A.article, "nope"), 404)
        paras = art["paragraphs"]
        check("the body is broken into more than one paragraph", len(paras) > 1, True)
        check("no sentence is split: the paragraphs joined back up are the body again",
              " ".join(paras).split(), art["body"].split())
        check("every paragraph but the last reaches the knob's word count",
              [len(p.split()) >= A.KNOBS["paragraph_words"] for p in paras[:-1]],
              [True] * len(paras[:-1]))
        check("every paragraph ends on sentence punctuation",
              sorted({p.strip()[-1] for p in paras}), ["."])

        # --- threads --------------------------------------------------------------
        shown = [s for s in live["stories"] if (s.get("story_so_far") or {}).get("shown")][0]
        t = A.thread(shown["story_so_far"]["thread_id"])
        check("/api/thread serialises", bool(json.dumps(t)), True)
        day1 = next(s for s in live["stories"]
                    if not s["developing"] and (s.get("story_so_far") or {}).get("days") == 1)
        check("a day-1 story's thread is 404, which is how the page renders none (H3)",
              status_of(A.thread, day1["story_id"]), 404)

        # --- the posted routes ----------------------------------------------------
        hashes = [r["text_hash"] for r in shown["coverage"]["rows"]]
        check("a body missing a required field is 400",
              status_of(A.read, payload={}), 400)
        check("/api/read marks them read", all(A.read(payload={"text_hashes": hashes})
                                               ["read"].values()), True)
        check("/api/keep_unread needs all three fields",
              status_of(A.keep_unread, payload={"story_id": "x"}), 400)
        kept = A.keep_unread(payload={"story_id": shown["story_id"],
                                      "text_hashes": hashes, "on": True})
        check("keep unread through the route makes a READ story unread again",
              any(kept["read"].values()), False)
        A.keep_unread(payload={"story_id": shown["story_id"], "text_hashes": hashes,
                               "on": False})
        check("/api/follow adds the story",
              len(A.follow(payload={"story_id": shown["story_id"],
                                    "text_hashes": hashes, "on": True})["follows"]), 1)
        check("/api/follows agrees", len(A.follows()["follows"]), 1)

        # keep-unread must survive a reload, or the button lies about its own state
        A.keep_unread(payload={"story_id": shown["story_id"], "text_hashes": hashes,
                               "on": True})
        check("the held stories can be asked for",
              [k["story_id"] for k in A.keep_unread_list()["keep_unread"]],
              [shown["story_id"]])
        A.read(payload={"text_hashes": hashes})
        check("and leaving a held story marks nothing",
              any(A.read(payload={"text_hashes": hashes})["read"].values()), False)
        A.keep_unread(payload={"story_id": shown["story_id"], "text_hashes": hashes,
                               "on": False})
        check("letting go empties the list", A.keep_unread_list()["keep_unread"], [])

        # new-since, which the page draws its red box from
        check("a story nothing has been read of has nothing new since",
              A.new_since(shown["story_id"])["new_since_read"], [])
        check("new_since on a story that is not in the digest is 404",
              status_of(A.new_since, "2026-01-01:nope"), 404)

        board_id = A.new_board(payload={"name": "Protests"})["board_id"]
        check("a board with a blank name is 400",
              status_of(A.new_board, payload={"name": "   "}), 400)
        pin_id = A.pin(payload={"board_id": board_id, "item_type": "story",
                                "payload": {"story_id": shown["story_id"],
                                            "text_hashes": hashes},
                                "source": {"digest_ref": "digest_live"}})["pin_id"]
        check("the board now holds one pin",
              len(A.board_pins(board_id)["pins"]), 1)
        check("the Boards screen can count pins without a call each",
              A.boards()["boards"][0]["pin_count"], 1)
        A.remove_pin(pin_id)
        check("unpinning empties it", A.board_pins(board_id)["pins"], [])

        check("a feedback vote that is not up or down is 400",
              status_of(A.feedback, payload={"target_kind": "story", "target_id": "x",
                                             "vote": "sideways"}), 400)
        check("a feedback target_kind outside story/thread_link is 400",
              status_of(A.feedback, payload={"target_kind": "paper", "target_id": "x",
                                             "vote": "up"}), 400)
        check("a 👎 with a note is stored",
              A.feedback(payload={"target_kind": "story", "target_id": shown["story_id"],
                                  "vote": "down", "note": "not what I expected",
                                  "text_hashes": hashes})["stored"], True)

        check("an unset pref reads as None", A.pref("thread_view")["value"], None)
        check("a pref posts and reads back",
              A.set_pref("thread_view", payload={"value": "graph"})["value"], "graph")
        check("/api/visit answers with the visit BEFORE this one",
              A.visit(payload={"digest_ref": "digest_live",
                               "window": live["window"]})["last_visit"], None)
        A.visit(payload={"digest_ref": "digest_live", "window": live["window"],
                         "opened_story": True})
        check("and then that visit is the last one",
              bool(A.visit(payload={"digest_ref": "digest_live",
                                    "window": live["window"]})["last_visit"]), True)

        # --- the PDF route, which is the one route that names a file ---------------
        pdf_rel = row["page_link"]["pdf"]
        date, name = pdf_rel.split("/")[1], pdf_rel.split("/")[2]
        served = A.pdf(date, name)
        check("a real archive PDF is served",
              str(served.path), str((ROOT / pdf_rel).resolve()))
        check("climbing out of the archive is refused",
              status_of(A.pdf, date, "../../secrets.pdf"), 403)
        check("and so is a deeper climb",
              status_of(A.pdf, date, "../../../../etc/passwd.pdf"), 403)
        check("a name that is not a PDF is 404",
              status_of(A.pdf, date, "../../CLAUDE.md"), 404)
        check("a path that is not a date is 404",
              status_of(A.pdf, "not-a-date", name), 404)
        check("a PDF that is not there is 404",
              status_of(A.pdf, date, "2026-10-09_The Daily Invention.pdf"), 404)
        check("the page route answers even before the page is built",
              A.page().status_code, 200)


JS_TEST = r"""
load(ROOT + "/app/app.js");
var fails = [], ran = 0;
function eq(name, got, want) {
  ran += 1;
  if (JSON.stringify(got) !== JSON.stringify(want)) {
    fails.push(name + " :: got " + JSON.stringify(got) + ", want " + JSON.stringify(want));
  }
}

// the window as words (decision D4: one digest's window is a day, not a week)
eq("a week inside one month",
   APP.windowText({kind: "week", from: "2026-10-09", to: "2026-10-15"}),
   "Week 9–15 Oct");
eq("a week across two months",
   APP.windowText({kind: "week", from: "2026-09-28", to: "2026-10-03"}),
   "Week 28 Sep–3 Oct");
eq("a single-day window names its day",
   APP.windowText({kind: "day", from: "2026-10-11", to: "2026-10-11"}),
   "Sun 11 Oct");
eq("no window, no words", APP.windowText(null), "");

// paper codes for the chips
eq("a named paper", APP.paperCode("The Hindu"), "TH");
eq("another named paper", APP.paperCode("Business Line"), "BL");
eq("Mint keeps its name", APP.paperCode("Mint"), "Mint");
eq("a paper the list has never seen takes its initials",
   APP.paperCode("Economic Times"), "ET");
eq("and skips the small words", APP.paperCode("The Daily Star of India"), "DSI");

// money, which is read at a glance and must never say "0.01¢"
eq("a penny", APP.money(0.010941), "1¢");
eq("less than a penny", APP.money(0.0004), "<1¢");
eq("nothing spent", APP.money(0), "0¢");
eq("more than a dollar", APP.money(1.5), "$1.50");

eq("one article from one paper", APP.count(1, "article"), "1 article");
eq("and more than one takes the s", APP.count(5, "paper"), "5 papers");
eq("none is still plural", APP.count(0, "article"), "0 articles");

eq("an afternoon build time", APP.timeOfDay("2026-11-01T16:38:51"), "4.38pm");
eq("midnight is 12am, not 0am", APP.timeOfDay("2026-10-11T00:05:00"), "12.05am");
eq("noon is 12pm", APP.timeOfDay("2026-10-11T12:00:00"), "12.00pm");

// the stamp, and READ overriding the other two
var story = {story_id: "s1", developing: true,
             coverage: {rows: [{text_hash: "a"}, {text_hash: "b"}]}};
APP.S.read = {};
eq("a developing story is DEVELOPING", APP.stamp(story).kind, "developing");
APP.S.read = {a: "2026-11-02T09:00:00"};
eq("half read is still DEVELOPING", APP.stamp(story).kind, "developing");
APP.S.read = {a: "2026-11-02T09:00:00", b: "2026-11-02T09:00:00"};
eq("every article read means READ, whatever else it is", APP.stamp(story).kind, "read");
eq("and a story with no coverage is never READ",
   APP.isRead({coverage: {rows: []}}), false);
APP.S.read = {};
eq("a story that is not developing is NEW", APP.stamp({story_id: "s2", developing: false,
   coverage: {rows: [{text_hash: "c"}]}}).kind, "new");

// "N unread" counts the digest's stories plus followed ones, without double counting
APP.S.digest = {stories: [story, {story_id: "s2", developing: false,
                coverage: {rows: [{text_hash: "c"}]}}]};
APP.S.follows = [];
eq("both stories unread", APP.unreadCount(), 2);
APP.S.read = {a: "t", b: "t"};
eq("one read leaves one", APP.unreadCount(), 1);
APP.S.follows = [{story_id: "s1", text_hashes: ["a", "b"]}];
eq("a followed story already in the digest is not counted twice", APP.unreadCount(), 1);
APP.S.follows = [{story_id: "elsewhere", text_hashes: ["z"]}];
eq("a followed story outside the digest is counted", APP.unreadCount(), 2);

// the thread: both views draw the same entries (acceptance check 6), and the node
// shapes say what kind of link each one is
var thread = {
  thread_id: "2026-10-08:aaa", days: 6, articles: 12, papers: 5,
  entries: [
    {entry_id: "d13", date: "2026-10-13", link_type: "continuing", parent_id: "d12"},
    {entry_id: "d12", date: "2026-10-12", link_type: null, parent_id: "d10"},
    {entry_id: "d10", date: "2026-10-10", link_type: "continuing", parent_id: "d09"},
    {entry_id: "d09", date: "2026-10-09", link_type: "follow_up", parent_id: "d08"},
    {entry_id: "d08", date: "2026-10-08", link_type: "start", parent_id: "d07"},
    {entry_id: "d07", date: "2026-10-07", link_type: "run_up", parent_id: null}
  ],
  related: [{date: "2026-10-12", headline: "beside it", attaches_to: "d12"}]
};

eq("the timeline and the graph draw the same entries",
   APP.viewEntryIds(thread, "timeline").slice().sort(),
   APP.viewEntryIds(thread, "graph").slice().sort());
eq("and neither drops one", APP.viewEntryIds(thread, "graph").length, 6);

var plan = APP.graphPlan(thread);
function placed(id) { return plan.filter(function (p) { return p.entry_id === id; })[0]; }
eq("the newest day is today, whatever its link type", placed("d13").kind, "today");
eq("where it began is drawn as the start", placed("d08").kind, "start");
eq("a follow-up hangs off the spine", placed("d09").isFollow, true);
eq("and sits in the column of the day it follows",
   placed("d09").column, placed("d08").column);
eq("the run-up is the leftmost column", placed("d07").column, 0);
eq("the days march rightwards in time",
   [placed("d07").column, placed("d08").column, placed("d10").column,
    placed("d12").column, placed("d13").column], [0, 1, 2, 3, 4]);
eq("an untyped day is drawn like a continuing one", placed("d12").kind, "spine");
eq("no column is negative",
   plan.filter(function (p) { return p.column < 0; }).length, 0);

// the node shapes in the timeline
eq("the newest entry is the today node", APP.nodeKind(thread.entries[0], 0), "today");
eq("an untyped entry is named untyped, not left blank",
   APP.nodeKind(thread.entries[1], 1), "untyped");
eq("a follow-up keeps its own shape", APP.nodeKind(thread.entries[3], 3), "follow_up");
eq("only three kinds say a word beside the node",
   Object.keys(APP.KIND_WORDS).sort(), ["follow_up", "run_up", "start"]);
eq("and continuing is not one of them", APP.KIND_WORDS.continuing, undefined);

print(JSON.stringify({ran: ran, fails: fails}));
"""

JSC = ("/System/Library/Frameworks/JavaScriptCore.framework/Versions/A/Helpers/jsc")


def js_logic():
    """Run the page's own pure functions, with no browser.

    The brief expected this to be impossible here -- no headless browser in the sandbox,
    so the page's logic would go unchecked and become a click list for Kunaal. But macOS
    ships JavaScriptCore, which runs app.js directly: everything in its EXPORTS takes
    data and returns a value, so the words the header and the drawer put on screen can
    be pinned exactly like the Python. The DOM-building functions still need the eye.
    If jsc is ever missing, this part says so rather than passing quietly.
    """
    import subprocess
    if not Path(JSC).exists():
        check("JavaScriptCore is available to test the page's logic", False, True)
        return
    with tempfile.TemporaryDirectory() as tmp:
        script = Path(tmp) / "run.js"
        script.write_text('var ROOT = "%s";\n%s' % (ROOT, JS_TEST))
        done = subprocess.run([JSC, str(script)], capture_output=True, text=True,
                              timeout=60)
        out = (done.stdout or "").strip().splitlines()
        if done.returncode != 0 or not out:
            check("the page's script loads and runs",
                  (done.stderr or done.stdout or "no output").strip()[:200], "")
            return
        try:
            result = json.loads(out[-1])
        except json.JSONDecodeError:
            check("the page's checks report cleanly", out[-1][:200], "{}")
            return
        for f in result["fails"]:
            name, _, detail = f.partition(" :: ")
            check("page logic: " + name, detail, "")
        # Count every assertion the page's own checks made, not just the failures, so
        # the total this test prints is the number of things actually checked.
        passed = result["ran"] - len(result["fails"])
        for i in range(passed):
            CHECKS.append((f"page logic check {i + 1}", True, True))


def pdf_guard():
    """The path guard against files that really exist outside the archive.

    The route checks are above; these are the hostile ones, and they need a planted file
    to be worth anything — so they run against a TEMPORARY archive with a real PDF
    outside it and a symlink pointing at it. The real `archive/` is never written to.
    """
    import p3_app as A
    real_archive = A.ARCHIVE
    try:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            outside = tmp / "outside" / "real_secret.pdf"
            outside.parent.mkdir(parents=True)
            outside.write_bytes(b"%PDF-1.4 a real PDF that is not in the archive")
            day = tmp / "archive" / "2026-10-09"
            day.mkdir(parents=True)
            (day / "2026-10-09_Mint.pdf").write_bytes(b"%PDF-1.4 legitimate")
            (day / "2026-10-09_Sneaky.pdf").symlink_to(outside)
            A.ARCHIVE = tmp / "archive"

            check("the legitimate PDF is served",
                  status_of(A.pdf, "2026-10-09", "2026-10-09_Mint.pdf"), 200)
            check("a PDF that really exists outside, reached with ../, is refused",
                  status_of(A.pdf, "2026-10-09", "../../outside/real_secret.pdf"), 403)
            check("an absolute path to it is refused",
                  status_of(A.pdf, "2026-10-09", str(outside)), 403)
            check("a symlink inside the archive pointing outside it is refused",
                  status_of(A.pdf, "2026-10-09", "2026-10-09_Sneaky.pdf"), 403)
            check("a climb that lands back inside the archive is still served",
                  status_of(A.pdf, "2026-10-09", "../2026-10-09/2026-10-09_Mint.pdf"), 200)
            check("the archive root itself is not a PDF",
                  status_of(A.pdf, "2026-10-09", "../"), 404)
    finally:
        A.ARCHIVE = real_archive


# ------------------------------------------------------------------- 5. ownership

def ownership():
    app_src = (ROOT / "src" / "p3_app.py").read_text()
    app_imports = re.findall(r"^\s*(?:import|from)\s+([\w.]+)", app_src, re.M)
    check("p3_app has exactly one data import, so integration is a one-line swap",
          [i for i in app_imports if i.startswith("p3_")], ["p3_app_double"])
    check("p3_app opens no database either",
          sorted({"sqlite3", "p3_store", "p3_digest"} & set(app_imports)), [])

    src = (ROOT / "src" / "p3_app_double.py").read_text()
    imports = re.findall(r"^\s*(?:import|from)\s+([\w.]+)", src, re.M)
    forbidden = {"sqlite3", "p3_store", "p3_digest", "p3_index", "p3_group", "p3_label",
                 "urllib", "socket", "http", "httpx", "httpx2", "requests", "anthropic"}
    check("the double imports no database and nothing that reaches the network",
          sorted(set(imports) & forbidden), [])
    check("it writes only the one state file",
          sorted(set(re.findall(r"_work/[\w.]+", src))), ["_work/k1_app_state.json"])


def main():
    with tempfile.TemporaryDirectory() as tmp:
        mock(tmp)
    real()
    routes()
    pdf_guard()
    js_logic()
    ownership()
    bad = [(n, g, w) for n, g, w in CHECKS if g != w]
    for n, g, w in bad:
        print(f"FAIL {n}: got {g!r}, want {w!r}")
    print(f"{len(CHECKS) - len(bad)}/{len(CHECKS)} checks passed "
          f"(mock world + the two real digests + the routes + the page's logic "
          f"+ ownership)")
    print("no database was opened and no real read state was touched by this test")
    print("PASS" if not bad else "FAIL")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
