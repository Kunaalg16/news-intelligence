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
    - `/api/flags` sends all eleven flags, with `theme_bar_strip` off (R9) and B1's
      `see_also_line` on. (v0 said ten; B1 adds one, brief 2 §8.1.)
    - B1 pass lines, written before the run: the one 11 Oct story with a non-empty
      `links` still has its eight `see_also_page` items after B1 (B1 changes the page,
      never the data), all ten live-digest stories still have none, and the knobs the
      page is sent no longer carry `earlier_coverage_days` -- Q3 B removed the only
      thing that read it.
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
        check("all eleven feature flags are sent to the page", len(f["flags"]), 11)
        check("the theme bar strip is off, its data not being in the JSON (R9)",
              f["flags"]["theme_bar_strip"], False)
        check("the cost header is on", f["flags"]["cost_display"], True)

        # --- B1 (brief 2 §8.1) ----------------------------------------------------
        check("[fixture] B1 · the printed see-also line is on",
              f["flags"]["see_also_line"], True)
        check("[fixture] B1 · Q3 B took the earlier-coverage knob off the page with it",
              sorted(f["knobs"]), ["paragraph_words"])

        live = A.digest(ref="digest_live")
        day = A.digest(ref="k3_b1_3_digest_2026-10-11")
        with_links = [st for st in day["stories"] if st.get("links")]
        check("[fixture] B1 · one 11 Oct story carries printed see-also pointers",
              len(with_links), 1)
        check("[fixture] B1 · and it carries eight of them",
              len(with_links[0]["links"]), 8)
        check("[fixture] B1 · every one is a see_also_page with a from page and a headline",
              sorted({(it["kind"], bool(it["from"]["pdf"]), bool(it["to_headline"]))
                      for it in with_links[0]["links"]}),
              [("see_also_page", True, True)])
        check("[fixture] B1 · all eight point at the same printed page, so the line groups",
              len({(it["from"]["pdf"], it["from"]["pdf_page"])
                   for it in with_links[0]["links"]}), 1)
        check("[fixture] B1 · and none of their targets is a story in the digest, so no "
              "headline here could be a link (hand-off F1)",
              [it["to_headline"] for it in with_links[0]["links"]
               if it["to_story_id"] in {st["story_id"] for st in day["stories"]}], [])
        check("[fixture] B1 · the live digest's ten stories have no printed pointers",
              [st["story_id"] for st in live["stories"] if st.get("links")], [])

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
        check("a day-1 story is told plainly that it has no thread (H3), not 404 -- most "
              "stories have none, and the console should not fill with red on a healthy "
              "morning", A.thread(day1["story_id"])["thread"], None)
        check("but a story no digest has heard of is still 404",
              status_of(A.thread, "2026-01-01:nosuchthing"), 404)

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
        # §8.6: one article can be pinned on its own, from the article view
        art_pin = A.pin(payload={"board_id": board_id, "item_type": "article",
                                 "payload": {"text_hash": hashes[0]},
                                 "source": {"digest_ref": "digest_live"}})["pin_id"]
        kinds = sorted(p["item_type"] for p in A.board_pins(board_id)["pins"])
        check("a board holds a story and a single article side by side",
              kinds, ["article", "story"])
        A.remove_pin(art_pin)

        A.remove_pin(pin_id)
        check("unpinning empties it", A.board_pins(board_id)["pins"], [])

        check("a feedback vote that is not up or down is 400",
              status_of(A.feedback, payload={"target_kind": "story", "target_id": "x",
                                             "vote": "sideways"}), 400)
        check("a feedback target_kind outside story/thread_link is 400",
              status_of(A.feedback, payload={"target_kind": "paper", "target_id": "x",
                                             "vote": "up"}), 400)
        check("nothing has been voted on yet", A.feedback_list()["votes"], {})
        check("a 👎 with a note is stored",
              A.feedback(payload={"target_kind": "story", "target_id": shown["story_id"],
                                  "vote": "down", "note": "not what I expected",
                                  "text_hashes": hashes})["stored"], True)

        votes = A.feedback_list()["votes"]
        check("the vote can be read back, so the page can show it was given",
              votes["story:" + shown["story_id"]]["vote"], "down")
        A.feedback(payload={"target_kind": "story", "target_id": shown["story_id"],
                            "vote": "up"})
        check("and changing your mind replaces it",
              A.feedback_list()["votes"]["story:" + shown["story_id"]]["vote"], "up")

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

// knowing whether the digest on screen is today's: the TODAY tab and the drawer's way
// back both hang off this, and "latest" is a request, not a name
APP.S.digest = {built_at: "2026-11-01T16:38:51"};
APP.S.digests = [{ref: "digest_live", built_at: "2026-11-01T16:38:51"},
                 {ref: "older", built_at: "2026-11-01T16:01:49"}];
APP.markIfPast();
eq("the newest digest is not a past one", APP.S.isPast, false);
APP.S.digest = {built_at: "2026-11-01T16:01:49"};
APP.markIfPast();
eq("an older digest is", APP.S.isPast, true);
APP.S.digests = [];
APP.markIfPast();
eq("with nothing to compare against, nothing is called past", APP.S.isPast, false);

// the nine keys of §8.9, and the rule that none of them fires while typing
var NINE = {j: "next", k: "previous", o: "open", Escape: "back", u: "keep_unread",
            f: "follow", p: "pin", t: "thread_view", "?": "help"};
Object.keys(NINE).forEach(function (key) {
  eq("the key " + key + " means " + NINE[key], APP.keyAction(key, {}), NINE[key]);
  eq("and " + key + " does nothing while typing",
     APP.keyAction(key, {inField: true}), null);
});
eq("exactly nine plain keys are bound", Object.keys(APP.KEYS).length, 9);
eq("the cheat sheet lists all ten, the nine plus undo", APP.CHEAT_SHEET.length, 10);

// the tenth key: undo, the only one with a modifier
eq("cmd-Z undoes", APP.keyAction("z", {meta: true}), "undo");
eq("ctrl-Z too, for a keyboard without cmd", APP.keyAction("z", {ctrl: true}), "undo");
eq("shift does not stop it", APP.keyAction("Z", {meta: true}), "undo");
eq("a bare z is still free", APP.keyAction("z", {}), null);
eq("and inside a text field cmd-Z stays the browser's own undo",
   APP.keyAction("z", {meta: true, inField: true}), null);
eq("a modifier does not turn another key into a shortcut",
   APP.keyAction("f", {meta: true}), null);
eq("nor does alt", APP.keyAction("j", {alt: true}), null);
eq("slash is left unbound for the assistant", APP.keyAction("/", {}), null);
eq("an ordinary letter does nothing", APP.keyAction("z", {}), null);
eq("and neither does a key that only looks like one",
   APP.keyAction("constructor", {}), null);

// --- B1 · Q1 B, the printed "see also" line (brief 2 §8.1) ------------------------
// Tagged [fixture]: K1's own page logic, not a rule K3 has to pass.

eq("[fixture] the paper's name comes out of the archive file name",
   APP.paperFromPdf("archive/2026-10-11/2026-10-11_The Hindu.pdf"), "The Hindu");
eq("[fixture] a file name with no date prefix is taken whole",
   APP.paperFromPdf("Mint.pdf"), "Mint");
eq("[fixture] no pdf, no paper", APP.paperFromPdf(null), "");

eq("[fixture] a story with no links has no see-also line",
   APP.seeAlsoPlan({links: []}), null);
eq("[fixture] a story with no links field at all has none either",
   APP.seeAlsoPlan({}), null);
eq("[fixture] an item with no from page is dropped rather than drawn empty",
   APP.seeAlsoPlan({links: [{kind: "see_also_page", to_headline: "x"}]}), null);

var manyOnOnePage = {links: [1, 2, 3].map(function (i) {
  return {kind: "see_also_page", to_story_id: "s" + i, to_headline: "Headline " + i,
          from: {pdf: "archive/2026-10-11/2026-10-11_The Hindu.pdf", pdf_page: 1,
                 label: "PDF p1"}};
})};
eq("[fixture] pointers to the same page are one pointer, not three",
   APP.seeAlsoPlan(manyOnOnePage).pages.map(function (p) { return p.text; }),
   ["TH PDF p1"]);
eq("[fixture] the line says how many stories that pointer is worth",
   APP.seeAlsoPlan(manyOnOnePage).tail, "3 stories on the same page");
eq("[fixture] the headlines ride along as the pointer's tooltip",
   APP.seeAlsoPlan(manyOnOnePage).pages[0].headlines,
   ["Headline 1", "Headline 2", "Headline 3"]);

eq("[fixture] one pointer to one story reads in the singular",
   APP.seeAlsoPlan({links: [{to_headline: "One",
     from: {pdf: "archive/2026-10-11/2026-10-11_Mint.pdf", pdf_page: 4}}]}).tail,
   "1 story on the same page");
eq("[fixture] two pages are two pointers and 'pages'",
   APP.seeAlsoPlan({links: [
     {to_headline: "a", from: {pdf: "archive/2026-10-11/2026-10-11_The Hindu.pdf", pdf_page: 1}},
     {to_headline: "b", from: {pdf: "archive/2026-10-11/2026-10-11_Business Line.pdf", pdf_page: 7}}]}),
   {pages: [{key: "archive/2026-10-11/2026-10-11_The Hindu.pdf#1",
             link: {pdf: "archive/2026-10-11/2026-10-11_The Hindu.pdf", pdf_page: 1},
             headlines: ["a"], text: "TH PDF p1"},
            {key: "archive/2026-10-11/2026-10-11_Business Line.pdf#7",
             link: {pdf: "archive/2026-10-11/2026-10-11_Business Line.pdf", pdf_page: 7},
             headlines: ["b"], text: "BL PDF p7"}],
    total: 2, tail: "2 stories on the same pages"});

// Q3 B: "Earlier coverage" is gone, function and all. A leftover call would throw in
// the browser and not here, so the check is that the name itself is unknown to the page.
eq("[fixture] Q3 B · the page has no earlierCoverage function left",
   typeof APP.earlierCoverage, "undefined");

// every feature flag has to say what it takes off the page, or a flag can be added
// that silently hides nothing (acceptance check 7)
eq("every flag names the element it removes",
   Object.keys(APP.FLAG_ELEMENTS).sort(), FLAGS_FROM_SERVER.sort());

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
        import p3_app as A
        flags = json.dumps(sorted(A.FLAGS))
        script.write_text('var ROOT = "%s";\nvar FLAGS_FROM_SERVER = %s;\n%s'
                          % (ROOT, flags, JS_TEST))
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


def _css_without_comments():
    return re.sub(r"/\*.*?\*/", "", (ROOT / "app" / "app.css").read_text(), flags=re.S)


def _tokens(css):
    root = re.search(r":root\s*\{(.*?)\n\}", css, re.S).group(1)
    return dict(re.findall(r"(--[\w-]+):\s*(#[0-9A-Fa-f]{6})", root))


def _ratio(a, b):
    """WCAG contrast between two #rrggbb colours."""
    def lum(hexcolour):
        parts = [int(hexcolour[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        parts = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
                 for c in parts]
        return 0.2126 * parts[0] + 0.7152 * parts[1] + 0.0722 * parts[2]
    first, second = lum(a) + 0.05, lum(b) + 0.05
    return round(max(first, second) / min(first, second), 2)


# Every pair the design actually puts on screen. Written out rather than guessed at by
# a parser, because what matters is which ink lands on which ground -- and saying so
# here means a new pair has to be added deliberately.
TEXT_PAIRS = [
    ("--ink", "--page", "body text"),
    ("--ink", "--drawer-ground", "drawer headlines"),
    ("--ink", "--chip-ground", "a chip's own border text"),
    ("--text-secondary", "--page", "entry one-liners, coverage rows"),
    ("--text-secondary", "--drawer-ground", "key dates in the story so far"),
    ("--text-secondary", "--chip-ground", "the keys in the cheat sheet"),
    ("--text-muted", "--page", "meta lines, dates, the cost header"),
    ("--text-muted", "--drawer-ground", "drawer headings, the past-digest note"),
    ("--text-muted", "--red-ground", "muted text in the new-since box"),
    ("--red", "--page", "the DEVELOPING label, upcoming dates"),
    ("--red", "--red-ground", "the new-since heading"),
    ("--page", "--red", "white on the DEVELOPING stamp"),
    ("--page", "--ink", "the toast, the graph's start box"),
    ("--page", "--olive", "white on the READ stamp"),
    ("--ochre", "--page", "Follow and Pin"),
    ("--ochre", "--chip-ground", "chip text"),
    ("--ochre", "--assistant-ground", "the assistant rail"),
    ("--text-muted", "--assistant-ground", "the rail's label"),
]

# Marks and borders that carry MEANING rather than text: >= 3:1 is the bar for those.
# `--rule` and `--rule-light` are deliberately NOT in this list. §8.1 lists "rules" apart
# from "grey marks and borders #878276 (>= 3:1)" and puts the ratio only on the second,
# because a line that separates two sections carries no information: remove it and the
# page still says everything it said. The check below is the one that matters for them --
# that they are never used where a border DOES carry meaning, which is what would make
# 1.63:1 a real failure rather than a quiet divider.
MARK_PAIRS = [
    ("--mark", "--page", "caveat tags, the Wrong link? border"),
    ("--mark", "--drawer-ground", "the pin menu's rules"),
    ("--ink", "--page", "the thread's spine and its nodes"),
    ("--red", "--page", "the today node and the follow-up line"),
    ("--olive", "--page", "the READ stamp's ground"),
    ("--ochre", "--page", "a pressed action's border"),
]


def contrast():
    """Acceptance check 4: every text pair >= 4.5:1, every meaningful mark >= 3:1."""
    css = _css_without_comments()
    tokens = _tokens(css)
    worst_text, worst_mark = None, None
    for ink, ground, where in TEXT_PAIRS:
        r = _ratio(tokens[ink], tokens[ground])
        check(f"contrast >= 4.5:1 for {where} ({ink} on {ground}, {r}:1)", r >= 4.5, True)
        if worst_text is None or r < worst_text[0]:
            worst_text = (r, where)
    for ink, ground, where in MARK_PAIRS:
        r = _ratio(tokens[ink], tokens[ground])
        check(f"contrast >= 3:1 for {where} ({ink} on {ground}, {r}:1)", r >= 3.0, True)
        if worst_mark is None or r < worst_mark[0]:
            worst_mark = (r, where)
    print(f"lowest text pair {worst_text[0]}:1 ({worst_text[1]}); "
          f"lowest mark {worst_mark[0]}:1 ({worst_mark[1]})")

    # a decorative rule must stay decorative: anything that bounds a control or marks a
    # distinction uses --mark, --ink or --red, never --rule
    control_rules = []
    for block in re.findall(r"([^{}]+)\{([^}]*)\}", css):
        selector, body = block[0].strip(), block[1]
        if not re.search(r"border[^;]*var\(--rule", body):
            continue
        if re.search(r"\.(action|caveat|chip|entry-wrong|view-switch|pin-menu)"
                     r"|button|input|textarea", selector):
            control_rules.append(selector.split("\n")[-1].strip()[:40])
    check("no control or meaningful mark is drawn with a decorative rule",
          sorted(set(control_rules)), [])

    # the token system is only real if nothing paints with a raw colour
    root = re.search(r":root\s*\{.*?\n\}", css, re.S).group(0)
    stray = sorted(set(re.findall(r"#[0-9A-Fa-f]{3,8}", css.replace(root, ""))))
    check("every colour on the page comes from a token", stray, [])


def markup():
    """Acceptance check 3: real controls, no `all: unset`, a focus ring on everything."""
    css = _css_without_comments()
    html = (ROOT / "app" / "index.html").read_text()
    js = (ROOT / "app" / "app.js").read_text()

    check("no `all: unset` anywhere in the stylesheet",
          bool(re.search(r"all\s*:\s*unset", css)), False)
    check("the focus-visible rule is 2px of ink, offset",
          bool(re.search(r":focus-visible[^{]*\{[^}]*outline:\s*2px solid var\(--ink\)",
                         css, re.S)), True)
    for kind in ("button", "a", "input", "textarea", "[tabindex]"):
        check(f"{kind} has a focus ring",
              bool(re.search(re.escape(kind) + r":focus-visible", css)), True)

    # nothing in the shell is a clickable div
    check("the shell has no clickable div or span",
          re.findall(r"<(div|span|li|p)[^>]*onclick", html), [])

    # and nothing the page builds is either: every el() given an onclick is a real control
    bad = []
    for match in re.finditer(r"el\(\s*'([a-z]+)'\s*,\s*\{", js):
        tag, start = match.group(1), match.end() - 1
        depth, i = 0, start
        while i < len(js):                      # the props object, braces balanced
            if js[i] == "{":
                depth += 1
            elif js[i] == "}":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        props = js[start:i]
        if "onclick" in props and tag not in ("button", "a", "input", "textarea"):
            bad.append(tag)
    check("every clickable thing the page builds is a real control", sorted(set(bad)), [])

    # a chip is drawn small; its hit area must not be
    check("the small controls are given a >= 24px hit area",
          bool(re.search(r"\.chip::after[^}]*height:\s*24px", css, re.S)), True)
    # The thread's line and its nodes must stay on one axis. The nodes are deliberately
    # different sizes -- that is what says which kind of link each is -- so any rule that
    # places one by its left edge puts its centre off the line, which is what made the
    # column look ragged the first time it was used.
    check("the nodes are centred on the column, not placed by their left edge",
          bool(re.search(r"\.node\s*\{[^}]*left:\s*50%[^}]*"
                         r"transform:\s*translate\(-50%", css, re.S)), True)
    check("and the line is centred on the same axis",
          bool(re.search(r"\.entry-node::before\s*\{[^}]*left:\s*50%", css, re.S)), True)
    per_kind_offsets = re.findall(r"\.node\[data-kind=\"[a-z_]+\"\][^{]*\{([^}]*)\}", css)
    check("no single node kind moves itself off that axis",
          [b.strip()[:30] for b in per_kind_offsets
           if re.search(r"(^|;)\s*(left|top)\s*:", b)], [])

    # §8.6 asks for the same pin menu in the article view; it was missed once already
    check("the article view offers the same pin menu",
          bool(re.search(r"article-actions[^;]*?articlePinButton", js, re.S)), True)
    check("and it pins an article, not the story it came from",
          bool(re.search(r"function articlePinButton[^}]*?pinControl\('article'", js, re.S)),
          True)

    check("reduced motion turns every transition off",
          bool(re.search(r"prefers-reduced-motion[^}]*\{[^}]*transition:\s*none",
                         css, re.S)), True)


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


def v01_search():
    """B2 · the double's search() and facets() (brief 2 §4.1), on the real digests.

    Pass lines, written before the run:
    - [behaviour] `search("")` is a browse: `verdict` None, `sort` "newest", and the
      stories really are in newest-first order by their latest date.
    - [behaviour] Every `cited`-able hash a result offers belongs to a story in that
      result (the half of §4.5 rule 10 that search alone can answer: every article in a
      story row is one of that story's own coverage rows).
    - [fixture] Browse returns every story in the corpus once -- 19, not 20: one
      story_id is in both digest files and must not come back twice -- and counts all
      81 distinct coverage rows.
    - [fixture] The three verdicts come from real queries: "police" -> enough (4
      stories), "GST" -> thin (1), "Norway" -> empty (0).
    - [fixture] The verdict line is the knob, not a hard-coded 3: raising
      `verdict_enough_min` to 5 turns "police" from enough into thin, and putting it
      back restores it.
    - [fixture] `score` is the count of query words found, so a two-word query that
      matches both scores 2.0 and one that matches one scores 1.0.
    - [fixture] A query of only short words still searches: "AI" is a real topic here,
      so it must not fall through to a browse; and a short word matches whole words
      only, so "ai" does not score on "said".
    - [fixture] A story shows at most `search_articles_shown` (3) articles while
      `total_articles` counts every matching row, so "showing 3 of 7" is possible.
    - [fixture] Every article carries the contract's seven keys, with a `page_link`
      that has a `paper` in it (K3's rows do not).
    - [fixture] Each filter narrows: `papers`, `kinds`, `topics`, `date_from`/`date_to`,
      and `exclude_story_ids` removes exactly the excluded story.
    - [fixture] `searched_days` follows the date filters (7 days unfiltered, 1 for a
      single-day filter) and is not changed by `exclude_story_ids`.
    - [fixture] Relevance order puts the higher score first; "newest" order ignores
      score entirely.
    - [fixture] `limit` caps the stories returned and leaves the totals alone.
    - [fixture] `facets()` comes from the corpus: 5 papers, 5 kinds, 14 topics, the
      archive's own 9-15 Oct range -- and every value it offers really has stories
      behind it, so no filter can be chosen that returns nothing.
    """
    import p3_app_double as D

    # --- browse ----------------------------------------------------------------
    browse = D.search("")
    check("[behaviour] a browse has no verdict to give", browse["verdict"], None)
    check("[behaviour] a browse is sorted newest", browse["sort"], "newest")
    latest = [max(s["dates"]) for s in browse["stories"]]
    check("[behaviour] and really is in newest-first order",
          latest, sorted(latest, reverse=True))
    check("[behaviour] every article in a story row is one of that story's own rows",
          sorted({a["text_hash"] for s in browse["stories"] for a in s["articles"]}
                 - {h for s in D._search_corpus() for h in D._story_hashes(s)}), [])

    check("[fixture] the corpus is 19 stories, the one in both files counted once",
          browse["total_stories"], 19)
    check("[fixture] and 81 distinct coverage rows", browse["total_articles"], 81)
    check("[fixture] no story_id comes back twice",
          len({s["story_id"] for s in browse["stories"]}), len(browse["stories"]))
    check("[fixture] the story that is in both digests is in the corpus once",
          [s["story_id"] for s in browse["stories"]].count("2026-10-11:56eaf7aa4d5b"), 1)

    # --- the three verdicts, from real queries ---------------------------------
    enough, thin, empty = D.search("police"), D.search("GST"), D.search("Norway")
    check("[fixture] 'police' is enough", [enough["verdict"], enough["total_stories"]],
          ["enough", 4])
    check("[fixture] 'GST' is thin", [thin["verdict"], thin["total_stories"]],
          ["thin", 1])
    check("[fixture] 'Norway' is empty", [empty["verdict"], empty["total_stories"]],
          ["empty", 0])
    check("[fixture] an empty verdict brings back no stories", empty["stories"], [])

    D.KNOBS["verdict_enough_min"] = 5
    check("[fixture] the verdict line is the knob, not a 3 in the code",
          D.search("police")["verdict"], "thin")
    D.KNOBS["verdict_enough_min"] = 3
    check("[fixture] and putting the knob back restores it",
          D.search("police")["verdict"], "enough")

    # --- scoring ---------------------------------------------------------------
    two = D.search("Delhi police")
    check("[fixture] a story matching both query words scores 2",
          max(s["score"] for s in two["stories"]), 2.0)
    check("[fixture] and every score is a count of words, never above the word count",
          [s for s in two["stories"] if s["score"] > 2.0], [])
    check("[fixture] a story that matches nothing is not in the result",
          [s for s in two["stories"] if s["score"] == 0.0], [])

    short = D.search("AI")
    check("[fixture] a query of only short words still searches, not browses",
          [short["verdict"], short["total_stories"]], ["thin", 2])
    check("[fixture] a short word matches whole words only",
          D._word_hits(["ai"], "She said again it was an aid"), 0)
    check("[fixture] a long word matches inside a longer one, so plurals are found",
          D._word_hits(["protest"], "Protesters gathered"), 1)
    check("[fixture] short words are dropped when a longer one is there",
          D._query_words("of the police"), ["the", "police"])

    # --- the article rows ------------------------------------------------------
    gst = thin["stories"][0]
    check("[fixture] a story shows at most three articles", len(gst["articles"]), 3)
    check("[fixture] while the total counts every matching row",
          thin["total_articles"], 5)
    art = gst["articles"][0]
    check("[fixture] an article carries the contract's keys", sorted(art),
          ["date", "headline", "kind", "page_link", "paper", "snippet", "text_hash"])
    check("[fixture] its page_link carries the paper, which K3's rows do not",
          sorted(art["page_link"]), ["label", "paper", "pdf", "pdf_page"])
    check("[fixture] and the paper in it is the article's own paper",
          art["page_link"]["paper"], art["paper"])
    check("[fixture] the snippet is the row headline (fixture, recorded as such)",
          art["snippet"], art["headline"])
    check("[fixture] a story row counts its papers, not lists them",
          isinstance(gst["papers"], int), True)

    # --- filters ---------------------------------------------------------------
    one_paper = D.search("", filters={"papers": ["Mint"]})
    check("[fixture] a paper filter leaves only that paper",
          sorted({a["paper"] for s in one_paper["stories"] for a in s["articles"]}),
          ["Mint"])
    one_kind = D.search("", filters={"kinds": ["opinion"]})
    check("[fixture] a kind filter leaves only that kind",
          sorted({a["kind"] for s in one_kind["stories"] for a in s["articles"]}),
          ["opinion"])
    one_topic = D.search("", filters={"topics": ["AI"]})
    check("[fixture] a topic filter narrows to that theme's stories",
          0 < one_topic["total_stories"] < browse["total_stories"], True)
    one_day = D.search("", filters={"date_from": "2026-10-11", "date_to": "2026-10-11"})
    check("[fixture] a one-day filter leaves only that day's rows",
          sorted({a["date"] for s in one_day["stories"] for a in s["articles"]}),
          ["2026-10-11"])
    check("[fixture] and searched_days says one day was searched",
          one_day["searched_days"], 1)
    check("[fixture] where the unfiltered search says seven",
          browse["searched_days"], 7)

    drop = enough["stories"][0]["story_id"]
    minus = D.search("police", filters={"exclude_story_ids": [drop]})
    check("[fixture] exclude_story_ids removes exactly that story",
          [minus["total_stories"], drop in [s["story_id"] for s in minus["stories"]]],
          [enough["total_stories"] - 1, False])
    check("[fixture] excluding a story does not change the days searched",
          minus["searched_days"], enough["searched_days"])

    # --- order and limit -------------------------------------------------------
    scores = [s["score"] for s in two["stories"]]
    check("[fixture] relevance order puts the best score first",
          scores, sorted(scores, reverse=True))
    newest = D.search("Delhi police", sort="newest")
    check("[fixture] newest order ignores the score",
          [max(s["dates"]) for s in newest["stories"]],
          sorted([max(s["dates"]) for s in newest["stories"]], reverse=True))
    check("[fixture] and keeps the same stories",
          sorted(s["story_id"] for s in newest["stories"]),
          sorted(s["story_id"] for s in two["stories"]))
    small = D.search("", limit=4)
    check("[fixture] limit caps what comes back", len(small["stories"]), 4)
    check("[fixture] and leaves the totals alone",
          [small["total_stories"], small["total_articles"]],
          [browse["total_stories"], browse["total_articles"]])
    check("[fixture] limit 0 brings back nothing but still counts",
          [len(D.search("", limit=0)["stories"]), D.search("", limit=0)["total_stories"]],
          [0, 19])

    # --- facets ----------------------------------------------------------------
    f = D.facets()
    check("[fixture] facets name the five papers in the archive",
          f["papers"], ["Business Line", "Business Standard", "Mint", "The Hindu",
                        "Times of India"])
    check("[fixture] and the five kinds, 'brief' among them (v0 D3)",
          f["kinds"], ["analysis", "brief", "data", "news", "opinion"])
    check("[fixture] and fourteen theme topics", len(f["topics"]), 14)
    check("[fixture] and the archive's own date range",
          [f["date_min"], f["date_max"]], ["2026-10-09", "2026-10-15"])
    empty_facets = []
    for paper in f["papers"]:
        if not D.search("", filters={"papers": [paper]})["total_stories"]:
            empty_facets.append(paper)
    for kind in f["kinds"]:
        if not D.search("", filters={"kinds": [kind]})["total_stories"]:
            empty_facets.append(kind)
    for topic in f["topics"]:
        if not D.search("", filters={"topics": [topic]})["total_stories"]:
            empty_facets.append(topic)
    check("[fixture] every value a filter offers has stories behind it",
          empty_facets, [])
    check("[fixture] every search result says it is a fixture",
          [browse["fixture"], thin["fixture"], empty["fixture"]], [True, True, True])


def _fresh_double(state_file):
    """A second, independent copy of the double reading the same state file -- what a
    restart of the app really is. Loaded under its own name so nothing is shared with
    the module the rest of this file uses, which is the point: if the conversation came
    back from an in-memory variable rather than the file, this would not see it."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "p3_app_double_restarted", ROOT / "src" / "p3_app_double.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.ROOT, mod.ARCHIVE = ROOT, ROOT / "archive"
    mod.WORK = state_file.parent
    mod.STATE_FILE = state_file
    mod.KNOBS["fixture_stream_delay_ms"] = 0
    return mod


def v01_ask():
    """B3 · ask(), think_harder(), background, cost, conversation, suggestions, R13.

    The fourteen behaviour rules of §4.5 are each one [behaviour] check, called through
    the public functions only, so they run unchanged against K3's modules.

    Pass lines, written before the run:
    1  [behaviour] `ask()` yields `results` first, every time, before any sentence.
    2  [behaviour] Every cite in an answer is a text_hash in that answer's own results.
    3  [behaviour] An empty verdict has no sentences, costs 0, and offers exactly 3
       closest stories.
    4  [behaviour] `think_harder` keeps the parent's passage_set_id, sets
       parent_answer_id, is Sonnet -- and the next `ask()` is Haiku again (one-shot).
    5  [behaviour] A background line carries no cites and is never one of `sentences`.
    6  [behaviour] `ask_background` works on any answer whose background is null --
       empty, thin or enough -- and raises once there is one.
    7  [behaviour] At the cap, `ask` ends in `cap_reached` and adds no cost at all.
    8  [behaviour] `current_conversation()` gives the same id after a restart (a second
       copy of the module, reading the same file) until `new_conversation()`.
    9  [behaviour] `suggest_connections` never returns the story itself or a story its
       thread already holds.
    10 [behaviour] Every hash in `cited` belongs to a story in `found`.
    11 [behaviour] `cost_today()` spent rises by exactly each call's own cost_usd.
    12 [behaviour] Every model_error carries a reason and retryable, false for
       key_rejected and checks_failed and true for the other four.
    13 [behaviour] After `raise_cap_today(1.00)` the cap is the knob + 1.00 today and
       the knob alone tomorrow (the date is simulated), and `ask` answers again.
    14 [behaviour] `add_user_link` puts the target in `thread()` as link_type "user";
       `remove_user_link` takes it out; `list_user_links` shows both states; and
       nothing outside the reader's own state file is touched.

    And the fixture side:
    - [fixture] An answer carries all twenty contract keys, Haiku, attempts 1, drops 0.
    - [fixture] An "enough" answer carries a background line on every second one, so the
      dashed block and the Background button are both reachable without a switch.
    - [fixture] A thin answer still gets two sentences; an enough answer up to three.
    - [fixture] `estimate_cost` answers for the four kinds and raises on a fifth.
    - [fixture] `think_harder` sends no `results` event.
    - [fixture] The six error reasons each have their wording, and an unknown switch
      value falls back to "other" rather than crashing the pane.
    - [fixture] Cap presses add up, are refused past the knob's limit, and are logged
      with their date.
    - [fixture] A divider is a turn of its own; `ask` and `ask_background` add theirs.
    - [fixture] Suggestions are 3 to 5, name their two rarest shared words, and cost.
    - [fixture] A story with NO thread that is given a user link now has one.
    """
    import p3_app_double as D

    with tempfile.TemporaryDirectory() as tmp:
        D.ROOT, D.ARCHIVE = ROOT, ROOT / "archive"
        D.WORK = Path(tmp)
        D.STATE_FILE = Path(tmp) / "state.json"
        D.KNOBS["fixture_stream_delay_ms"] = 0
        D.FIXTURES["assistant_error"] = False
        D.FIXTURES["cap_reached"] = False

        # --- rule 1, 2, 10 and the answer's shape ------------------------------
        events = list(D.ask("police", {"story_id": None}))
        kinds = [e["type"] for e in events]
        check("[behaviour] 1 · results comes first, before any sentence",
              kinds[0], "results")
        check("[behaviour] 1 · and done comes last", kinds[-1], "done")
        check("[behaviour] 1 · nothing but results precedes the first sentence",
              set(kinds[:kinds.index("sentence")]), {"results"})
        result = events[0]["search"]
        answer = events[-1]["answer"]
        in_results = {a["text_hash"] for s in result["stories"] for a in s["articles"]}
        check("[behaviour] 2 · every cite is a hash in that answer's own results",
              sorted(set(answer["cited"]) - in_results), [])
        check("[behaviour] 2 · and every streamed sentence's cites are too",
              sorted({h for e in events if e["type"] == "sentence"
                      for h in e["cites"]} - in_results), [])
        found_hashes = {a["text_hash"] for s in result["stories"]
                        if s["story_id"] in answer["found"] for a in s["articles"]}
        check("[behaviour] 10 · every cited hash belongs to a story in found",
              sorted(set(answer["cited"]) - found_hashes), [])

        check("[fixture] an answer carries the contract's keys", sorted(answer),
              ["answer_id", "at", "attempts", "background", "cited", "closest",
               "conversation_id", "cost_usd", "drops", "fixture", "found",
               "found_count", "model", "model_id", "parent_answer_id",
               "passage_set_id", "question", "scope", "sentences", "verdict"])
        check("[fixture] it is Haiku, named by its model id",
              [answer["model"], answer["model_id"]], ["haiku", "claude-haiku-5-5"])
        check("[fixture] one attempt, nothing dropped",
              [answer["attempts"], answer["drops"]], [1, 0])
        check("[fixture] found_count is the articles the search found, not the stories",
              answer["found_count"], result["total_articles"])
        check("[fixture] an enough answer has two or three sentences",
              2 <= len(answer["sentences"]) <= 3, True)
        check("[fixture] and says it is a fixture", answer["fixture"], True)

        # --- rule 11 · the cost moves by exactly the call's own cost -----------
        before = D.cost_today()["spent_usd"]
        events2 = list(D.ask("GST", None))
        thin = events2[-1]["answer"]
        check("[behaviour] 11 · spend rises by exactly that answer's cost",
              round(D.cost_today()["spent_usd"] - before, 6), thin["cost_usd"])
        check("[fixture] a thin answer still gets two sentences",
              [thin["verdict"], len(thin["sentences"])], ["thin", 2])

        # --- rule 3 · the empty verdict ----------------------------------------
        before = D.cost_today()["spent_usd"]
        empty = list(D.ask("Norway", None))[-1]["answer"]
        check("[behaviour] 3 · an empty answer has no sentences", empty["sentences"], [])
        check("[behaviour] 3 · and costs nothing", empty["cost_usd"], 0)
        check("[behaviour] 3 · and offers exactly three closest stories",
              len(empty["closest"]), 3)
        check("[behaviour] 3 · and the spend did not move",
              D.cost_today()["spent_usd"], before)
        check("[fixture] the closest stories are newest first",
              [max(s["dates"]) for s in empty["closest"]],
              sorted([max(s["dates"]) for s in empty["closest"]], reverse=True))

        # --- rule 4 · think harder ---------------------------------------------
        harder_events = list(D.think_harder(answer["answer_id"]))
        harder = harder_events[-1]["answer"]
        check("[fixture] think_harder sends no results event",
              [e["type"] for e in harder_events if e["type"] == "results"], [])
        check("[behaviour] 4 · it keeps the parent's passage set",
              harder["passage_set_id"], answer["passage_set_id"])
        check("[behaviour] 4 · and names its parent",
              harder["parent_answer_id"], answer["answer_id"])
        check("[behaviour] 4 · and is Sonnet",
              [harder["model"], harder["model_id"]], ["sonnet", "claude-sonnet-5-5"])
        check("[behaviour] 4 · it cites only from the parent's passages",
              sorted(set(harder["cited"]) - in_results), [])
        check("[behaviour] 4 · and the next ask is Haiku again, so it is one-shot",
              list(D.ask("police", None))[-1]["answer"]["model"], "haiku")

        # --- rules 5 and 6 · background ----------------------------------------
        got = D.ask_background(empty["answer_id"])
        check("[fixture] background on an empty answer is answered",
              [got["model"], got["cost_usd"]], ["haiku", 0.0003])
        reloaded = D.current_conversation()
        stored = [t["answer"] for t in reloaded["turns"] if t.get("kind") == "answer"
                  and t["answer"]["answer_id"] == empty["answer_id"]][0]
        check("[behaviour] 5 · a background line is a plain string, not a cited sentence",
              isinstance(stored["background"], str), True)
        check("[behaviour] 5 · it names no text_hash at all",
              [h for h in in_results if h in stored["background"]], [])
        check("[behaviour] 5 · and is never one of the sentences",
              [s for s in stored["sentences"] if s["text"] == stored["background"]], [])
        check("[behaviour] 6 · and it is now on the stored answer",
              stored["background"], got["text"])
        try:
            D.ask_background(empty["answer_id"])
            twice = "no error"
        except ValueError as exc:
            twice = "error"
        check("[behaviour] 6 · asking twice is an error", twice, "error")
        try:
            D.ask_background("ans_nope")
            unknown = "no error"
        except ValueError:
            unknown = "error"
        check("[fixture] background on an answer that does not exist is an error",
              unknown, "error")

        # the alternating background line on enough answers
        marks = [bool(list(D.ask("police", None))[-1]["answer"]["background"])
                 for _ in range(4)]
        check("[fixture] an enough answer carries a background line every second time",
              marks, [marks[0], not marks[0], marks[0], not marks[0]])
        with_line = list(D.ask("police", None))[-1]["answer"]
        without = list(D.ask("police", None))[-1]["answer"]
        if with_line["background"] is None:                  # whichever way it fell
            with_line, without = without, with_line
        check("[behaviour] 6 · background works on an enough answer that came without one",
              isinstance(D.ask_background(without["answer_id"])["text"], str), True)
        try:
            D.ask_background(with_line["answer_id"])
            already = "no error"
        except ValueError:
            already = "error"
        check("[behaviour] 6 · and is refused on the one that came with one",
              already, "error")

        # --- rule 12 · the error reasons ---------------------------------------
        seen = {}
        for switch in ("model_busy", "no_network", "key_rejected", "timeout",
                       "checks_failed", "other"):
            D.FIXTURES["assistant_error"] = switch
            event = list(D.ask("police", None))[-1]
            seen[switch] = (event["code"], event["reason"], event["retryable"],
                            bool(event["message"]))
        check("[behaviour] 12 · every reason comes back as itself, with a message",
              sorted(k for k, v in seen.items()
                     if v[0] == "model_error" and v[1] == k and v[3]),
              ["checks_failed", "key_rejected", "model_busy", "no_network", "other",
               "timeout"])
        check("[behaviour] 12 · retryable is false for exactly these two",
              sorted(k for k, v in seen.items() if v[2] is False),
              ["checks_failed", "key_rejected"])
        check("[behaviour] 12 · and true for the other four",
              sorted(k for k, v in seen.items() if v[2] is True),
              ["model_busy", "no_network", "other", "timeout"])
        D.FIXTURES["assistant_error"] = True
        check("[fixture] the switch set to True means model_busy",
              list(D.ask("police", None))[-1]["reason"], "model_busy")
        D.FIXTURES["assistant_error"] = "nonsense"
        check("[fixture] an unknown reason falls back to 'other', not a crash",
              list(D.ask("police", None))[-1]["reason"], "other")
        D.FIXTURES["assistant_error"] = "timeout"
        failed = list(D.ask("police", None))
        check("[fixture] an error answer still sends its results first, so the reader "
              "sees what was found",
              [failed[0]["type"], failed[0]["search"]["verdict"], len(failed)],
              ["results", "enough", 2])
        D.FIXTURES["assistant_error"] = False

        # --- rule 7 and 13 · the cap ------------------------------------------
        D.FIXTURES["cap_reached"] = True
        before = D.cost_today()["spent_usd"]
        capped = list(D.ask("police", None))
        check("[behaviour] 7 · at the cap the answer is cap_reached",
              [capped[0]["type"], capped[-1]["type"], capped[-1]["code"]],
              ["results", "error", "cap_reached"])
        check("[behaviour] 7 · with no sentence in between",
              [e for e in capped if e["type"] == "sentence"], [])
        check("[behaviour] 7 · and it adds no cost",
              D.cost_today()["spent_usd"], before)
        check("[fixture] think harder is capped too",
              list(D.think_harder(answer["answer_id"]))[-1]["code"], "cap_reached")

        knob_cap = D.cost_today()["cap_usd"]
        raised = D.raise_cap_today(1.00)
        check("[behaviour] 13 · the cap is the knob plus the dollar",
              raised["cap_usd"], round(knob_cap + 1.00, 6))
        check("[behaviour] 13 · and says how much of it is an override",
              raised["override_usd"], 1.0)
        check("[behaviour] 13 · ask answers again while the spend is under the new cap",
              list(D.ask("police", None))[-1]["type"], "done")
        real_today = D._today
        D._today = lambda: "2099-01-01"
        try:
            check("[behaviour] 13 · and tomorrow the cap is the knob alone",
                  D.cost_today()["cap_usd"], knob_cap)
            check("[behaviour] 13 · with nothing carried over",
                  D.cost_today()["override_usd"], 0)
        finally:
            D._today = real_today
        check("[fixture] presses add up",
              D.raise_cap_today()["cap_usd"], round(knob_cap + 2.00, 6))
        pressed = 2
        while pressed < D.KNOBS["cap_override_max_per_day"]:
            D.raise_cap_today()
            pressed += 1
        try:
            D.raise_cap_today()
            past = "no error"
        except ValueError:
            past = "error"
        check("[fixture] and are refused past the knob's limit for one day",
              past, "error")
        D.FIXTURES["cap_reached"] = False

        # --- rule 8 · the conversation survives a restart ----------------------
        conv = D.current_conversation()
        check("[fixture] the conversation holds the answers as turns",
              len([t for t in conv["turns"] if t["kind"] == "answer"]) > 0, True)
        bg_turns = [t for t in conv["turns"] if t["kind"] == "background"]
        check("[fixture] and each background as a turn of its own",
              [len(bg_turns) > 0,
               sorted({t["answer_id"] for t in bg_turns}
                      - set(D._load_state()["answers"]))],
              [True, []])
        check("[fixture] every background turn carries the text that went on its answer",
              [t for t in bg_turns
               if D._load_state()["answers"][t["answer_id"]]["background"] != t["text"]],
              [])
        D.add_divider(conv["conversation_id"], "sid_1", "A STORY'S HEADLINE")
        divider = D.current_conversation()["turns"][-1]
        check("[fixture] a divider is a turn with the story it points at",
              [divider["kind"], divider["story_id"], divider["headline"]],
              ["divider", "sid_1", "A STORY'S HEADLINE"])

        restarted = _fresh_double(D.STATE_FILE)
        check("[behaviour] 8 · after a restart the conversation is the same one",
              restarted.current_conversation()["conversation_id"],
              conv["conversation_id"])
        check("[behaviour] 8 · with its turns still in it",
              len(restarted.current_conversation()["turns"]),
              len(D.current_conversation()["turns"]))
        fresh_id = D.new_conversation()
        check("[behaviour] 8 · and only new_conversation() changes it",
              [fresh_id != conv["conversation_id"],
               D.current_conversation()["turns"]], [True, []])
        check("[fixture] the old conversation is still in the state file",
              conv["conversation_id"] in
              [c["conversation_id"] for c in D._load_state()["conversations"]], True)

        # --- rule 9 · suggestions ----------------------------------------------
        story_id = "2026-10-11:56eaf7aa4d5b"
        story = D._find_story(lambda s: s.get("story_id") == story_id)
        before = D.cost_today()["spent_usd"]
        sugg = D.suggest_connections(story_id, D._story_hashes(story))
        ids = [s["story_id"] for s in sugg["suggestions"]]
        check("[behaviour] 9 · a suggestion is never the story itself",
              story_id in ids, False)
        check("[behaviour] 9 · nor anything its own thread already holds",
              sorted(set(ids) & D._thread_story_ids(story)), [])
        check("[fixture] three to five suggestions",
              D.KNOBS["suggest_min"] <= len(ids) <= D.KNOBS["suggest_max"], True)
        check("[fixture] each says why, naming shared words",
              [s for s in sugg["suggestions"] if not s["why"].startswith("Sample reason")],
              [])
        check("[fixture] each carries one to three articles",
              sorted({len(s["articles"]) for s in sugg["suggestions"]} - {1, 2, 3}), [])
        check("[fixture] and the set costs what the estimate said",
              [sugg["cost_usd"], round(D.cost_today()["spent_usd"] - before, 6)],
              [D.estimate_cost("suggest")] * 2)
        try:
            D.suggest_connections("no_such_story")
            missing = "no error"
        except ValueError:
            missing = "error"
        check("[fixture] suggestions for a story that does not exist are an error",
              missing, "error")

        # --- rule 14 · the reader's own links ----------------------------------
        bare = [s["story_id"] for s in D._search_corpus()
                if D.thread(s["story_id"]) is None][0]
        bare_story = D._find_story(lambda s: s.get("story_id") == bare)
        target_id = ids[0]
        target = D._find_story(lambda s: s.get("story_id") == target_id)
        link_id = D.add_user_link(bare, D._story_hashes(bare_story), target_id,
                                  D._story_hashes(target))
        made = D.thread(bare)
        user_entries = [e for e in made["entries"] if e["link_type"] == "user"]
        check("[behaviour] 14 · a story with no thread has one once you add a link",
              bool(made), True)
        check("[behaviour] 14 · the target is in it as a user entry",
              [len(user_entries), user_entries[0]["story_id"],
               user_entries[0]["added_by"]], [1, target_id, "you"])
        check("[behaviour] 14 · its headline and counts come from the target's own rows",
              [user_entries[0]["headline"],
               user_entries[0]["articles"]],
              [(target.get("headline") or {}).get("text"),
               len(target["coverage"]["rows"])])
        check("[behaviour] 14 · the header counts say how many you added",
              made["entries_added_by_you"], 1)
        listed = D.list_user_links(D._story_hashes(bare_story))
        check("[behaviour] 14 · list_user_links shows it",
              [len(listed), listed[0]["link_id"], listed[0]["target_story_id"]],
              [1, link_id, target_id])
        state_keys_before = sorted(D._load_state())
        check("[behaviour] 14 · remove_user_link takes it out",
              [D.remove_user_link(link_id), D.thread(bare)], [True, None])
        check("[behaviour] 14 · and list_user_links agrees",
              D.list_user_links(D._story_hashes(bare_story)), [])
        check("[behaviour] 14 · nothing outside the reader's own state was touched",
              sorted(D._load_state()), state_keys_before)
        check("[fixture] removing a link that is not there says so",
              D.remove_user_link("ulink_nope"), False)

        # a user link on a story that HAS a thread joins the days it already had
        has_thread = [s["story_id"] for s in D._search_corpus()
                      if D.thread(s["story_id"])][0]
        ht_story = D._find_story(lambda s: s.get("story_id") == has_thread)
        was = len(D.thread(has_thread)["entries"])
        link2 = D.add_user_link(has_thread, D._story_hashes(ht_story), target_id,
                                D._story_hashes(target))
        now = D.thread(has_thread)
        check("[fixture] a user link adds one entry to a thread that already had days",
              len(now["entries"]), was + 1)
        check("[fixture] and the days count still counts only the days",
              now["days"], D.thread(has_thread)["days"])
        check("[fixture] the entry ids are unique, user entries and days alike",
              len({e["entry_id"] for e in now["entries"]}), len(now["entries"]))
        D.remove_user_link(link2)

        # --- estimates ----------------------------------------------------------
        check("[fixture] every kind has an estimate",
              [D.estimate_cost(k) for k in ("ask", "think_harder", "background",
                                            "suggest")],
              [0.0005, 0.011, 0.0003, 0.0005])
        try:
            D.estimate_cost("nonsense")
            bad_kind = "no error"
        except ValueError:
            bad_kind = "error"
        check("[fixture] and an unknown kind is an error, not a zero", bad_kind, "error")
        check("[fixture] the state file is the only thing this wrote",
              D.STATE_FILE.exists(), True)


def main():
    with tempfile.TemporaryDirectory() as tmp:
        mock(tmp)
    real()
    v01_search()
    v01_ask()
    routes()
    pdf_guard()
    js_logic()
    contrast()
    markup()
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
