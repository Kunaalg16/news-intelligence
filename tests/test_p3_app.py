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
        # B1 added one flag to v0's ten; B5 adds five more (§8.6). The count is checked
        # outright so a flag cannot be added without saying what it removes.
        check("all nineteen feature flags are sent to the page", len(f["flags"]), 19)
        check("the theme bar strip is off, its data not being in the JSON (R9)",
              f["flags"]["theme_bar_strip"], False)
        check("the cost header is on", f["flags"]["cost_display"], True)

        # --- B1 (brief 2 §8.1) ----------------------------------------------------
        check("[fixture] B1 · the printed see-also line is on",
              f["flags"]["see_also_line"], True)
        check("[fixture] B5/B6 · every v0.1 flag is on",
              [f["flags"][k] for k in ("assistant", "think_harder", "background_line",
                                       "background_on_request", "cap_override",
                                       "suggest_connections", "user_links",
                                       "explore")],
              [True] * 8)
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


JS_DOM = r"""
/* A DOM small enough to fit in a test and real enough to build the pane with.

   v0 left every DOM-building function to the eye, for want of a browser in the
   sandbox. These eighty lines are the alternative: enough of `document` for
   app.js's own `el()` to work, plus textOf / controlsOf to read back what was
   built. It is not a browser -- no layout, no events, no CSS -- so it says
   nothing about how the pane LOOKS. What it does say is that each of the nine
   states builds without throwing and puts the brief's exact words on the screen,
   which is acceptance check 3 and was a click list before. */
function Node(tag) {
  this.tagName = (tag || '').toUpperCase();
  this.children = [];
  this.attrs = {};
  this.dataset = {};
  this.style = { setProperty: function () {} };
  this.className = '';
  this.textContent = '';
  this.hidden = false;
  this.value = '';
  this.disabled = false;
}
Node.prototype.append = function () {
  for (var i = 0; i < arguments.length; i++) {
    var kid = arguments[i];
    if (kid === null || kid === undefined || kid === false) continue;
    this.children.push(kid);
  }
};
Node.prototype.appendChild = Node.prototype.append;
Node.prototype.setAttribute = function (k, v) { this.attrs[k] = String(v); };
Node.prototype.getAttribute = function (k) { return this.attrs[k]; };
Node.prototype.addEventListener = function () {};
Node.prototype.removeChild = function (kid) {
  this.children = this.children.filter(function (c) { return c !== kid; });
};
Object.defineProperty(Node.prototype, 'firstChild', {
  get: function () { return this.children.length ? this.children[0] : null; },
});
Node.prototype.querySelector = function () { return null; };
Node.prototype.querySelectorAll = function () { return []; };
Node.prototype.scrollIntoView = function () {};
Node.prototype.focus = function () {};
Node.prototype.remove = function () {};

function textOf(node) {
  if (node === null || node === undefined || node === false) return '';
  if (typeof node === 'string' || typeof node === 'number') return String(node);
  var out = node.textContent || '';
  for (var i = 0; i < (node.children || []).length; i++) out += textOf(node.children[i]);
  return out;
}

function classesOf(node, found) {
  found = found || [];
  if (node && node.className) found.push(node.className);
  for (var i = 0; i < ((node && node.children) || []).length; i++) {
    classesOf(node.children[i], found);
  }
  return found;
}

function controlsOf(node, found) {
  found = found || [];
  if (node && (node.tagName === 'BUTTON' || node.tagName === 'A'
               || node.tagName === 'INPUT')) {
    var text = textOf(node).trim();
    var aria = (node.attrs['aria-label'] || '').trim();
    var placeholder = (node.attrs.placeholder || '').trim();
    found.push({
      tag: node.tagName,
      /* What a reader or a screen reader is given, in that order of preference. A
         control with none of the three is the thing acceptance check 7 is looking for. */
      label: text || aria || placeholder,
      text: text, aria: aria, placeholder: placeholder,
      type: node.attrs.type || null,
      href: node.attrs.href || null,
      disabled: Boolean(node.attrs.disabled !== undefined || node.disabled),
    });
  }
  for (var i = 0; i < ((node && node.children) || []).length; i++) {
    controlsOf(node.children[i], found);
  }
  return found;
}

var document = {
  byId: {},
  createElement: function (tag) { return new Node(tag); },
  createElementNS: function (ns, tag) { var n = new Node(tag); n.ns = ns; return n; },
  createTextNode: function (t) { var n = new Node('#text'); n.textContent = t; return n; },
  getElementById: function (id) { return document.byId[id] || null; },
  querySelector: function () { return null; },
  querySelectorAll: function () { return []; },
  addEventListener: function () {},
  body: new Node('body'),
  documentElement: new Node('html'),
};
"""


JS_TEST = r"""
load(ROOT + "/app/app.js");
var fails = [], ran = 0, names = [];
function eq(name, got, want) {
  ran += 1;
  names.push(name);
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
// v0 bound nine plain keys and left "/" free for the assistant. §8.7 binds it now, so
// there are ten, and the cheat sheet lists eleven (the ten plus undo).
eq("exactly ten plain keys are bound", Object.keys(APP.KEYS).length, 10);
eq("the cheat sheet lists all eleven, the ten plus undo", APP.CHEAT_SHEET.length, 11);

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
eq("[fixture] slash now focuses the pane's composer (§8.7)",
   APP.keyAction("/", {}), "ask");
eq("[fixture] and does nothing while the reader is typing, composer included",
   APP.keyAction("/", {inField: true}), null);
eq("[fixture] a modifier does not turn slash into the shortcut",
   APP.keyAction("/", {meta: true}), null);
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

eq("[fixture] check 8 · see_also_line names the element it removes",
   APP.FLAG_ELEMENTS.see_also_line, ".see-also");

// Q3 B: "Earlier coverage" is gone, function and all. A leftover call would throw in
// the browser and not here, so the check is that the name itself is unknown to the page.
eq("[fixture] Q3 B · the page has no earlierCoverage function left",
   typeof APP.earlierCoverage, "undefined");


// === B5 · the ask pane, nine states (brief 2 §8.3) ================================
// Pass lines: every state below builds without throwing and carries the brief's own
// words; the money format of §7; the stream parser of acceptance check 4; the six
// error wordings of acceptance check 11; and "/" focuses the composer (check 5).

// §7's money: "0.05¢" under a cent, "1.1¢" from a cent, "$1.02" from a dollar.
// the plural rule, which the pane is the first thing to need
eq("[fixture] a consonant then y pluralises to -ies", APP.count(9, "story"),
   "9 stories");
eq("[fixture] one of them stays singular", APP.count(1, "story"), "1 story");
eq("[fixture] a vowel then y just takes an s", APP.count(3, "day"), "3 days");

eq("[fixture] half a twentieth of a cent", APP.moneyFine(0.0005), "0.05¢");
eq("[fixture] a background's three hundredths", APP.moneyFine(0.0003), "0.03¢");
eq("[fixture] Think harder's 1.1 cents", APP.moneyFine(0.011), "1.1¢");
eq("[fixture] a dollar and two cents", APP.moneyFine(1.02), "$1.02");
eq("[fixture] nothing spent is a flat 0¢", APP.moneyFine(0), "0¢");
eq("[fixture] and v0's own money() is untouched, still saying <1¢",
   APP.money(0.0004), "<1¢");

// Acceptance check 4 · a line split across two chunks is still one event
var first = APP.ndjsonSplit("", '{"type":"results"}\n{"type":"sen');
eq("[behaviour] the whole line is an event", first.events, [{type: "results"}]);
eq("[behaviour] and the half line is carried", first.rest, '{"type":"sen');
var second = APP.ndjsonSplit(first.rest, 'tence","text":"x"}\n');
eq("[behaviour] which the next chunk completes",
   second.events, [{type: "sentence", text: "x"}]);
eq("[behaviour] leaving nothing behind", second.rest, "");
eq("[fixture] a chunk with no newline at all yields nothing yet",
   APP.ndjsonSplit("", '{"a":1}').events, []);
eq("[fixture] blank lines are not events",
   APP.ndjsonSplit("", '\n\n{"a":1}\n').events, [{a: 1}]);
eq("[fixture] three events in one chunk all arrive",
   APP.ndjsonSplit("", '{"a":1}\n{"a":2}\n{"a":3}\n').events.length, 3);

// Acceptance check 11 · the six reasons, word for word
var REASONS = {
  model_busy: ["Couldn't get an answer · the model service is busy",
               "Usually clears within a minute.", true],
  no_network: ["Couldn't get an answer · no internet connection",
               "Check the connection, then retry.", true],
  key_rejected: ["Couldn't get an answer · the API key was rejected",
                 "Check the key file; retrying won't help.", false],
  timeout: ["Couldn't get an answer · the model took too long",
            "Retry usually works.", true],
  checks_failed: ["The answer didn't pass the source checks twice",
                  "See what was found below, or try Think harder.", false],
  other: ["Couldn't get an answer · unexpected error",
          "Retry; if it repeats, tell Claude Code.", true],
};
Object.keys(REASONS).forEach(function (reason) {
  var want = REASONS[reason];
  var got = APP.errorWording({reason: reason, retryable: want[2]});
  eq("[fixture] " + reason + " says its first line", got.first, want[0]);
  eq("[fixture] " + reason + " says its hint", got.hint, want[1]);
  eq("[fixture] " + reason + " offers Retry only when it is worth it",
     got.retry, want[2]);
});
eq("[fixture] a reason the page has never heard of still gets words, not a blank",
   APP.errorWording({reason: "sulking", retryable: true}).first,
   REASONS.other[0]);
eq("[fixture] and retryable, not the table, decides the button",
   APP.errorWording({reason: "timeout", retryable: false}).retry, false);

// --- the nine states, built ------------------------------------------------------
var PS = APP.S;
PS.flags = {assistant: true, think_harder: true, background_line: true,
            background_on_request: true, cap_override: true, see_also_line: true};
PS.cost = {spent_usd: 0.01, cap_usd: 0.5, override_usd: 0};
PS.estimates = {ask: 0.0005, think_harder: 0.011, background: 0.0003, suggest: 0.0005};
PS.digest = {stories: []};
PS.results = {};
PS.folded = {};
PS.sorts = {};
PS.scope = {story_id: null, text_hashes: [], filters: {}};

function answerFixture(over) {
  var base = {answer_id: "a1", conversation_id: "c1", parent_answer_id: null,
    question: "what happened in Delhi?", model: "haiku",
    model_id: "claude-haiku-5-5", verdict: "enough", found_count: 17,
    sentences: [{text: "Sample answer · one.", cites: ["h1"]},
                {text: "Sample answer, continued · two.", cites: ["h2"]}],
    background: null, cited: ["h1", "h2"], found: ["s1"], closest: [],
    passage_set_id: "ps_1", cost_usd: 0.0005, attempts: 1, drops: 0, fixture: true};
  Object.keys(over || {}).forEach(function (k) { base[k] = over[k]; });
  return base;
}

var resultsFixture = {query: "Delhi", filters: {}, sort: "relevance", verdict: "enough",
  searched_days: 7, total_stories: 9, total_articles: 23,
  stories: [1, 2, 3, 4, 5, 6].map(function (i) {
    return {story_id: "s" + i, headline: "Story number " + i,
            dates: ["2026-10-1" + (i % 5)], papers: 2, score: 2.0,
            articles: [{text_hash: i === 1 ? "h1" : "x" + i, headline: "An article",
                        paper: "Mint", date: "2026-10-1" + (i % 5), kind: "news",
                        page_link: {pdf: "archive/x/y.pdf", pdf_page: 3,
                                    label: "PDF p3", paper: "Mint"},
                        snippet: "An article"}]};
  })};

// State 2 · answer done
var state2 = APP.answerTurn(answerFixture(), {});
eq("[fixture] state 2 · the question is yours, in ochre",
   textOf(state2).indexOf("You · what happened in Delhi?"), 0);
eq("[fixture] state 2 · both sentences are there",
   textOf(state2).indexOf("Sample answer, continued · two.") > 0, true);
eq("[fixture] state 2 · the cost line names the model and the money",
   textOf(state2).indexOf("Haiku · 0.05¢") > 0, true);
eq("[fixture] state 2 · Think harder prints its own estimate",
   textOf(state2).indexOf("Think harder · ≈ 1.1¢") > 0, true);
eq("[fixture] state 2 · and every fixture answer says it is sample data",
   textOf(state2).indexOf("sample data") > 0, true);
eq("[fixture] state 2 · every control in it is a real button with a label",
   controlsOf(state2).filter(function (c) {
     return c.tag !== "BUTTON" || !c.label; }), []);

// State 1 · answering: the results list is there before the answer is
PS.results.a1 = resultsFixture;
var state1 = APP.answerTurn(answerFixture(), {});
eq("[fixture] state 1 · the heading counts the stories found",
   textOf(state1).indexOf("Found · 9 stories") > 0, true);
eq("[fixture] state 1 · five story rows, no more",
   classesOf(state1).filter(function (c) { return c === "result-row"; }).length, 5);
eq("[fixture] state 1 · and an offer to see the rest in Explore",
   textOf(state1).indexOf("Show all 9 in Explore") > 0, true);
eq("[fixture] state 1 · the cited story is marked CITED, the rest FOUND",
   [textOf(APP.resultRow(resultsFixture.stories[0], answerFixture())).indexOf("CITED") > 0,
    textOf(APP.resultRow(resultsFixture.stories[1], answerFixture())).indexOf("FOUND") > 0],
   [true, true]);
eq("[fixture] the mark is decided by the answer's own cites",
   [APP.markFor(resultsFixture.stories[0], answerFixture()).word,
    APP.markFor(resultsFixture.stories[1], answerFixture()).word],
   ["CITED", "FOUND"]);
eq("[fixture] a browse cannot be sorted by relevance, so that switch is off",
   controlsOf(APP.resultsList("a1", {query: "", filters: {}, sort: "newest",
                                     total_stories: 2, stories: resultsFixture.stories},
                              null))
     .filter(function (c) { return c.label === "Relevance"; }).length, 1);
var live = APP.liveTurn.call(null);

// State 3 · Think harder: the Haiku answer folds to one line
var folded = APP.answerTurn(answerFixture(), {folded: true});
eq("[fixture] state 3 · the Haiku answer folds to one line with its cost",
   textOf(folded).indexOf("▸ Haiku answer · 0.05¢") > 0, true);
eq("[fixture] state 3 · and its sentences are gone while folded",
   textOf(folded).indexOf("Sample answer · one.") > 0, false);
eq("[fixture] state 3 · the Sonnet label names the passage count",
   APP.sonnetLabel({parent_answer_id: "a1"}), "Sonnet · same 6 passages");
eq("[fixture] state 3 · with the results gone, it claims no number",
   APP.sonnetLabel({parent_answer_id: "nothing"}), "Sonnet · the same passages");

// State 4 · weak match
var weak = APP.answerTurn(answerFixture({verdict: "thin", found_count: 5}), {});
eq("[fixture] state 4 · a thin answer says so above itself",
   textOf(weak).indexOf("Weak match · 5 articles found") > 0, true);

// State 5 · not in your papers
var none = answerFixture({verdict: "empty", sentences: [], cited: [], cost_usd: 0,
  searched_days: 7,
  closest: [{story_id: "s9", headline: "The closest story", dates: ["2026-10-15"]},
            {story_id: "s8", headline: "Another one", dates: ["2026-10-14"]},
            {story_id: "s7", headline: "A third", dates: ["2026-10-13"]}]});
delete PS.results.a1;
var state5 = APP.answerTurn(none, {});
eq("[fixture] state 5 · it says what it did not find, and how far it looked",
   textOf(state5).indexOf("Not in your papers · searched 7 days") > 0, true);
eq("[fixture] state 5 · the three closest stories are offered",
   textOf(state5).indexOf("The closest story") > 0
   && textOf(state5).indexOf("A third") > 0, true);
eq("[fixture] state 5 · no model was asked, and the cost line says exactly that",
   textOf(state5).indexOf("no model call · 0¢") > 0, true);
eq("[fixture] state 5 · and the offer is background knowledge, not a cheap answer",
   textOf(state5).indexOf("Answer from background knowledge") > 0, true);
eq("[fixture] state 5 · Think harder is not offered on nothing",
   textOf(state5).indexOf("Think harder") > 0, false);

// State 6 · background on request
var withBack = APP.backgroundBlock(answerFixture({verdict: "empty",
  background: "Sample background paragraph."}));
eq("[fixture] state 6 · an empty answer's background says it is not in the archive",
   textOf(withBack).indexOf("Not in the archive · background"), 0);
var matched = APP.backgroundBlock(answerFixture({background: "Sample background."}));
eq("[fixture] state 2 · a matched answer's says it is not from your papers",
   textOf(matched).indexOf("Background · not from your papers"), 0);
eq("[fixture] background carries no chips, because it cites nothing",
   classesOf(matched).filter(function (c) { return c === "chips"; }), []);
eq("[fixture] check 13 · the Background button is on an answer with no line",
   controlsOf(APP.actionsRow(answerFixture()))
     .filter(function (c) { return c.label.indexOf("Background") === 0; }).length, 1);
eq("[fixture] check 13 · and not on one that already has one",
   controlsOf(APP.actionsRow(answerFixture({background: "x"})))
     .filter(function (c) { return c.label.indexOf("Background") === 0; }).length, 0);

// State 9 · the error, and the cap
PS.live = {question: "q", sentences: [], results: null,
           error: {reason: "key_rejected", retryable: false}};
var errored = APP.liveTurn();
eq("[fixture] state 9 · the reason is the first line",
   textOf(errored).indexOf("the API key was rejected") > 0, true);
eq("[fixture] state 9 · with no Retry, because retrying will not help",
   controlsOf(errored).filter(function (c) { return c.label === "Retry"; }), []);
PS.live.error = {reason: "timeout", retryable: true};
eq("[fixture] state 9 · a retryable one offers Retry",
   controlsOf(APP.liveTurn()).filter(function (c) { return c.label === "Retry"; })
     .length, 1);
PS.live = null;

eq("[fixture] under the cap there is room to ask", APP.atCap(), false);
eq("[fixture] and the cap band is not drawn", APP.capBand(), null);
PS.cost = {spent_usd: 0.5, cap_usd: 0.5, override_usd: 0};
eq("[fixture] check 10 · spent to the cap, there is no room", APP.atCap(), true);
var band = APP.capBand();
eq("[fixture] check 10 · the band says the cap and what was spent",
   textOf(band).indexOf("Daily cap $0.50 reached · spent $0.50 today"), 0);
eq("[fixture] check 10 · it offers a day's override, and says it ends at midnight",
   [textOf(band).indexOf("Allow $1 more today") > 0,
    textOf(band).indexOf("Ends at midnight · logged") > 0], [true, true]);
eq("[fixture] at the cap Think harder greys with its reason",
   APP.roomFor("think_harder").why, "· not enough left today");
PS.cost = {spent_usd: 0.5, cap_usd: 1.5, override_usd: 1.0};
eq("[fixture] check 10 · after the override there is room again",
   [APP.atCap(), APP.capBand()], [false, null]);
PS.cost = {spent_usd: 0.01, cap_usd: null, override_usd: 0};
eq("[fixture] a digest with no cap knob has no cap to hit (v0 D1)",
   APP.atCap(), false);
PS.cost = {spent_usd: 0.01, cap_usd: 0.5, override_usd: 0};

// The scope chips, and the widths
eq("[fixture] the open story is one chip, each filter another",
   APP.scopeChips({story_id: "s1", headline: "A headline",
                   filters: {papers: ["Mint"], date_from: "2026-10-09"}})
     .map(function (c) { return c.key + "=" + c.text; }),
   ["story=A headline", "papers=Mint", "date_from=from 2026-10-09"]);
eq("[fixture] a selection from Explore is one chip saying how many",
   APP.scopeChips({filters: {story_ids: ["a", "b", "c"]}})[0].text, "3 stories");
eq("[fixture] and a label given for it wins",
   APP.scopeChips({filters: {story_ids: ["a"]}}, "these 4")[0].text, "these 4");
eq("[fixture] the hashes of a scope are not a chip of their own",
   APP.scopeChips({filters: {text_hashes: ["h1"]}}), []);
eq("[fixture] no scope, no chips", APP.scopeChips({}), []);
eq("[fixture] the three pane widths",
   [APP.PANE_WIDTHS.open, APP.PANE_WIDTHS.wide, APP.PANE_WIDTHS.collapsed],
   ["360px", "520px", "var(--rail-width)"]);
eq("[fixture] five results shown in the pane, the rest in Explore",
   APP.PANE_RESULTS_SHOWN, 5);
eq("[fixture] a query string carries every filter, repeated for a list",
   APP.searchQueryString("police", {papers: ["Mint", "The Hindu"],
                                    date_from: "2026-10-09"}, "newest"),
   "q=police&sort=newest&papers=Mint&papers=The%20Hindu&date_from=2026-10-09");
eq("[fixture] and the hashes filter travels as 'hashes'",
   APP.searchQueryString("", {text_hashes: ["h1"]}), "q=&hashes=h1");

// --- the whole pane, and acceptance check 8 (a flag off removes its element) ------
// The pane is rendered into a node the shim hands back from getElementById, exactly as
// the browser would, so this is the pane itself and not a piece of it.

document.byId.assistant = document.createElement("aside");
PS.conversation = {conversation_id: "c1", started_at: "2026-11-02T09:30:00",
  turns: [{kind: "answer", answer: answerFixture()},
          {kind: "divider", story_id: "s2", headline: "ANOTHER STORY",
           at: "2026-11-02T09:40:00"},
          {kind: "answer", answer: answerFixture({answer_id: "a2",
            question: "and then?", verdict: "thin", found_count: 4})}]};
PS.reloadDivider = "Mon 9.30am";
PS.pane = "open";

function paneText() {
  document.byId.assistant.children = [];
  APP.renderAssistant();
  return textOf(document.byId.assistant);
}
function paneClasses() {
  document.byId.assistant.children = [];
  APP.renderAssistant();
  return classesOf(document.byId.assistant);
}

var whole = paneText();
eq("[fixture] the pane names itself", whole.indexOf("Assistant"), 0);
eq("[fixture] it offers a new conversation, widening and collapsing",
   ["New conversation", "Widen", "Collapse"].filter(function (w) {
     return whole.indexOf(w) < 0; }), []);
eq("[fixture] state 8 · a reloaded conversation says when it started",
   whole.indexOf("Conversation from Mon 9.30am") > 0, true);
eq("[fixture] state 7 · the divider names the story now being read",
   whole.indexOf("Now reading · ANOTHER STORY") > 0, true);
eq("[fixture] both answers are in it",
   whole.indexOf("what happened in Delhi?") > 0 && whole.indexOf("and then?") > 0, true);
eq("[fixture] the composer invites a question about your papers",
   controlsOf(document.byId.assistant).filter(function (c) {
     return c.placeholder === "Ask about your papers…"; }).length, 1);
PS.scope = {story_id: "s1", headline: "A headline", text_hashes: [], filters: {}};
paneText();
eq("[fixture] and about this story, with the key that focuses it, when one is open",
   controlsOf(document.byId.assistant).filter(function (c) {
     return c.placeholder === "Ask about this story… ( / )"; }).length, 1);
eq("[fixture] the open story is a scope chip you can take off",
   controlsOf(document.byId.assistant).filter(function (c) {
     return c.aria === "Remove A headline from the question"; }).length, 1);

eq("[fixture] check 7 · every control in the whole pane is a real labelled control",
   controlsOf(document.byId.assistant).filter(function (c) {
     return ["BUTTON", "INPUT", "A"].indexOf(c.tag) < 0 || !c.label; }), []);
eq("[fixture] check 7 · and every button says it is a button",
   controlsOf(document.byId.assistant).filter(function (c) {
     return c.tag === "BUTTON" && !c.type; }), []);

// collapsed and wide
PS.pane = "collapsed";
eq("[fixture] collapsed, the pane is a rail that opens it again",
   [paneText(), controlsOf(document.byId.assistant).length], ["Assistant", 1]);
eq("[fixture] and the collapsed rail is as wide as v0's", APP.paneWidth(),
   "var(--rail-width)");
PS.pane = "wide";
eq("[fixture] widened, it offers to narrow instead",
   [paneText().indexOf("Narrow") > 0, APP.paneWidth()], [true, "520px"]);
PS.pane = "open";

// Acceptance check 8 · each flag off removes its element, and the page still renders
var PANE_FLAG_ELEMENTS = {think_harder: "think-harder",
  background_line: "answer-background", background_on_request: "background-button",
  cap_override: "cap-allow"};
PS.results.a1 = resultsFixture;
PS.conversation.turns[0].answer.background = "Sample background.";
PS.cost = {spent_usd: 0.5, cap_usd: 0.5, override_usd: 0};
Object.keys(PANE_FLAG_ELEMENTS).forEach(function (flag) {
  var cls = PANE_FLAG_ELEMENTS[flag];
  var on = paneClasses().filter(function (c) { return (c || "").indexOf(cls) >= 0; });
  eq("[fixture] check 8 · " + flag + " on draws " + cls, on.length > 0, true);
  PS.flags[flag] = false;
  var off = paneClasses().filter(function (c) { return (c || "").indexOf(cls) >= 0; });
  eq("[fixture] check 8 · " + flag + " off removes it completely", off, []);
  eq("[fixture] check 8 · and the pane still renders without it",
     paneText().indexOf("Assistant"), 0);
  PS.flags[flag] = true;
});
PS.flags.assistant = false;
eq("[fixture] check 8 · assistant off puts v0's disabled rail back, with no gap",
   [paneClasses().filter(function (c) { return c === "pane"; }),
    paneClasses().filter(function (c) { return c === "pane-collapsed"; }).length],
   [[], 1]);
eq("[fixture] check 8 · and that rail is disabled, as it was in v0",
   [controlsOf(document.byId.assistant)[0].aria,
    controlsOf(document.byId.assistant)[0].disabled],
   ["Assistant, version 0.1, not available yet", true]);
PS.flags.assistant = true;
PS.cost = {spent_usd: 0.01, cap_usd: 0.5, override_usd: 0};
PS.conversation = null;
PS.reloadDivider = null;
PS.scope = {story_id: null, text_hashes: [], filters: {}};
PS.results = {};


// === B6 · Suggest connections and the reader's own links (brief 2 §8.4) ===========

PS.ref = "digest_live";
PS.openStoryId = "open1";
PS.flags.suggest_connections = true;
PS.flags.user_links = true;
PS.suggestions = {};
PS.userLinks = {};
PS.pinnedStories = new Set();
PS.pinIds = {};
PS.estimates.suggest = 0.0005;

var openStoryFixture = {story_id: "open1",
  headline: {text: "The open story", source_text_hash: "oh1"},
  coverage: {rows: [{text_hash: "oh1", paper: "Mint", date: "2026-10-11",
                     page_link: {pdf: "a/b/c.pdf", pdf_page: 1, label: "PDF p1"}}]}};
PS.digest = {stories: [openStoryFixture]};

var suggestionFixture = {story_id: "sug1", headline: "A suggested story",
  dates: ["2026-10-13"], papers: 2,
  why: "Sample reason · shares the words 'delhi', 'jantar'",
  articles: [{text_hash: "sh1", headline: "An article", paper: "The Hindu",
              date: "2026-10-13", kind: "news",
              page_link: {pdf: "a/b/d.pdf", pdf_page: 4, label: "PDF p4",
                          paper: "The Hindu"}}]};
var suggestSetFixture = {story_id: "open1", suggestions: [suggestionFixture],
  verdict: "enough", model: "haiku", cost_usd: 0.0005, fixture: true};

// before it is asked for: a button that prints its own price, and no box
var offer = APP.suggestSection(openStoryFixture);
eq("[fixture] §8.4 · on demand, the button says what it will cost",
   textOf(offer), "Suggest connections · ≈ 0.05¢");
eq("[fixture] D4 · and nothing has been fetched to draw a box with",
   classesOf(offer).filter(function (c) { return c === "suggest-box"; }), []);

// once asked for, the button is REPLACED by the box
PS.suggestions.open1 = suggestSetFixture;
var box = APP.suggestSection(openStoryFixture);
eq("[fixture] §8.4 · the box is headed by who suggested it and that it is unchecked",
   [textOf(box).indexOf("Suggested by assistant"), textOf(box).indexOf("Not checked") > 0],
   [0, true]);
eq("[fixture] §8.4 · the button is gone, not left beside the box",
   controlsOf(box).filter(function (c) {
     return c.label.indexOf("Suggest connections") === 0; }), []);
eq("[fixture] §8.4 · the row says which paper, which day and why",
   textOf(box).indexOf("TH · 13 Oct · Why: Sample reason · shares the words "
                       + "'delhi', 'jantar'") > 0, true);
eq("[fixture] §8.4 · a closing cost line, marked sample data",
   [textOf(box).indexOf("Suggestions · 0.05¢") > 0,
    textOf(box).indexOf("sample data") > 0], [true, true]);
eq("[fixture] §8.4 · the box has no Pin of its own, only the rows' icons",
   classesOf(box).filter(function (c) { return c === "suggest-box"; }).length, 1);

// the two icon buttons: light, labelled, 32x32 targets
var icons = controlsOf(box).filter(function (c) {
  return c.label === "Pin to board" || c.label === "Add to thread"; });
eq("[fixture] §8.4 · two icon buttons, each with its own label",
   icons.map(function (c) { return c.aria; }).sort(),
   ["Add to thread", "Pin to board"]);
eq("[fixture] §8.4 · and both are real buttons, not clickable spans",
   icons.filter(function (c) { return c.tag !== "BUTTON"; }), []);
eq("[fixture] §8.4 · neither is pressed before anything is done",
   APP.suggestionRow(openStoryFixture, suggestionFixture) &&
   classesOf(APP.suggestionRow(openStoryFixture, suggestionFixture))
     .filter(function (c) { return (c || "").indexOf("is-done") >= 0; }), []);

// dark once done, and the row says so
PS.userLinks.open1 = [{link_id: "ul1", target_story_id: "sug1",
                       target_text_hashes: ["sh1"], at: "2026-11-02T10:00:00"}];
PS.pinnedStories = new Set(["sug1"]);
PS.pinIds.sug1 = {pin_id: "pin1", board_id: "b1", name: "Delhi",
                  payload: {story_id: "sug1"}, source: {}};
var doneRow = APP.suggestionRow(openStoryFixture, suggestionFixture);
eq("[fixture] §8.4 · both icons are dark once both are done",
   classesOf(doneRow).filter(function (c) {
     return (c || "").indexOf("is-done") >= 0; }).length, 2);
eq("[fixture] §8.4 · and say so to a screen reader",
   controlsOf(doneRow).filter(function (c) {
     return c.tag === "BUTTON"; }).length >= 2, true);
eq("[fixture] §8.4 · the muted line gains 'Added to the thread'",
   textOf(doneRow).indexOf("· Added to the thread") > 0, true);
eq("[fixture] §8.4 · a dark pin offers to unpin, not to pin again",
   controlsOf(doneRow).filter(function (c) {
     return (c.aria || "").indexOf("click to unpin") > 0; }).length, 1);
eq("[fixture] §8.4 · and a dark + offers to take it out of the thread",
   controlsOf(doneRow).filter(function (c) {
     return c.aria === "Remove from the thread"; }).length, 1);
eq("[fixture] the link is found by the story it points at",
   APP.userLinkTo("sug1").link_id, "ul1");
eq("[fixture] and a story with no link has none", APP.userLinkTo("nothing"), null);
eq("[fixture] a pinned story is known as pinned", APP.suggestionPinned("sug1"), true);

// check 8 · user_links off hides the + icon
PS.flags.user_links = false;
eq("[fixture] check 8 · user_links off removes the + icon completely",
   controlsOf(APP.suggestionRow(openStoryFixture, suggestionFixture))
     .filter(function (c) { return (c.aria || "").indexOf("thread") > 0; }), []);
PS.flags.user_links = true;
PS.flags.suggest_connections = false;
eq("[fixture] check 8 · suggest_connections off removes the whole thing",
   APP.suggestSection(openStoryFixture), null);
PS.flags.suggest_connections = true;

// --- R13 · the user entry in BOTH views (acceptance check 12) --------------------
var userEntry = {entry_id: "user:ul1", link_id: "ul1", date: "2026-10-13",
  link_type: "user", added_by: "you", parent_id: null, story_id: "sug1",
  headline: "A suggested story", one_liner: "Its one-liner.", articles: 3, papers: 2,
  paper_names: ["Mint", "The Hindu"],
  pages: [{pdf: "a/b/d.pdf", pdf_page: 4, label: "PDF p4", paper: "The Hindu"}],
  fixture: false};
var dayEntry = {entry_id: "2026-10-11:x", date: "2026-10-11", link_type: null,
  parent_id: null, headline: "The open story's day", one_liner: "A one-liner.",
  articles: 1, papers: 1, paper_names: ["Mint"],
  pages: [{pdf: "a/b/c.pdf", pdf_page: 1, label: "PDF p1", paper: "Mint"}],
  fixture: true};
var threadFixture = {thread_id: "open1", days: 1, entries_added_by_you: 1,
  articles: 4, papers: 2, entries: [userEntry, dayEntry], related: [], fixture: true};

eq("[behaviour] check 12 · a user entry is a user node, not 'today', whatever its date",
   APP.nodeKind(userEntry, 0), "user");
eq("[behaviour] check 12 · and a day entry at the top still is today",
   APP.nodeKind(dayEntry, 0), "today");
eq("[behaviour] check 12 · both views draw the same entries, the user one included",
   [APP.viewEntryIds(threadFixture, "timeline").sort(),
    APP.viewEntryIds(threadFixture, "graph").sort()],
   [["2026-10-11:x", "user:ul1"], ["2026-10-11:x", "user:ul1"]]);
eq("[behaviour] check 12 · the graph gives it its own kind, so its box is ochre",
   APP.graphPlan(threadFixture).filter(function (p) {
     return p.entry_id === "user:ul1"; })[0].kind, "user");

PS.threads = {open1: threadFixture};
PS.prefs = {thread_view: "timeline"};
var timeline = APP.timelineView(threadFixture);
eq("[fixture] R13 · the entry is tagged as yours beside its headline",
   textOf(timeline).indexOf("Added by you") > 0, true);
eq("[fixture] R13 · 'Wrong link?' is replaced by 'Remove' on an entry you added",
   controlsOf(timeline).filter(function (c) { return c.text === "Remove"; }).length, 1);
eq("[fixture] R13 · and the pipeline's own entry still offers Wrong link?",
   controlsOf(timeline).filter(function (c) {
     return c.text === "Wrong link?"; }).length, 1);
var head = APP.threadSection(openStoryFixture);
eq("[fixture] R13 · the thread header counts what you added",
   textOf(head).indexOf("1 added by you") > 0, true);
eq("[fixture] R13 · the ? sheet explains the new node with the others",
   APP.CHEAT_MARKS.map(function (m) { return m[0]; }),
   ["today", "follow_up", "start", "run_up", "user"]);

PS.threads = {};
PS.prefs = {};
PS.suggestions = {};
PS.userLinks = {};
PS.pinnedStories = new Set();
PS.pinIds = {};
PS.openStoryId = null;
PS.digest = {stories: []};


// === B7 · the Explore screen (brief 2 §8.5) ======================================

PS.flags.explore = true;
PS.facets = {papers: ["Mint", "The Hindu"], topics: ["AI", "Protests & movements"],
             kinds: ["news", "opinion"], date_min: "2026-10-09", date_max: "2026-10-15"};
PS.explore = null;
var ex = APP.exploreState();
eq("[fixture] §8.5 · it opens as a browse, newest first, nothing chosen",
   [ex.query, ex.sort, ex.rows, ex.selected.size, Object.keys(ex.filters).length],
   ["", "newest", "stories", 0, 0]);

eq("[fixture] §8.5 · the count line counts both populations",
   APP.exploreCounts({total_stories: 24, total_articles: 61}),
   "24 stories · 61 articles");
eq("[fixture] §8.5 · and one of each reads in the singular",
   APP.exploreCounts({total_stories: 1, total_articles: 1}), "1 story · 1 article");
eq("[fixture] §8.5 · nothing found counts to nothing",
   APP.exploreCounts(null), "0 stories · 0 articles");

// the chips say what is narrowing the screen
eq("[fixture] §8.5 · the keyword is a chip, in quotes",
   APP.exploreChips({query: "police", filters: {}}),
   [{key: "query", text: "“police”"}]);
eq("[fixture] §8.5 · each filter is its own chip, dates read from and to",
   APP.exploreChips({query: "", filters: {papers: ["Mint", "The Hindu"],
                                          date_from: "2026-10-11",
                                          kinds: ["opinion"]}})
     .map(function (c) { return c.key + "=" + c.text; }),
   ["papers=Mint, The Hindu", "date_from=from 2026-10-11", "kinds=opinion"]);
eq("[fixture] §8.5 · a selection carried in from the pane says how many",
   APP.exploreChips({query: "", filters: {story_ids: ["a", "b"]}})[0].text,
   "2 stories chosen");
eq("[fixture] §8.5 · and the machinery of a scope is not a chip",
   APP.exploreChips({query: "", filters: {text_hashes: ["h"],
                                          exclude_story_ids: ["s"]}}), []);

// Stories | Articles changes what a row IS
var exResult = {query: "police", filters: {}, sort: "relevance", verdict: "enough",
  searched_days: 7, total_stories: 2, total_articles: 5,
  stories: [
    {story_id: "e1", headline: "The first story", dates: ["2026-10-11"], papers: 2,
     score: 2.0, articles: [
       {text_hash: "a1", headline: "First article", paper: "Mint", date: "2026-10-11",
        kind: "news", snippet: "First article",
        page_link: {pdf: "x/y/z.pdf", pdf_page: 3, label: "PDF p3", paper: "Mint"}},
       {text_hash: "a2", headline: "Second article", paper: "The Hindu",
        date: "2026-10-11", kind: "news", snippet: "Second article",
        page_link: {pdf: "x/y/w.pdf", pdf_page: 9, label: "PDF p9",
                    paper: "The Hindu"}}]},
    {story_id: "e2", headline: "The second story", dates: ["2026-10-09"], papers: 1,
     score: 1.0, articles: [
       {text_hash: "a3", headline: "Third article", paper: "Mint", date: "2026-10-09",
        kind: "opinion", snippet: "Third article",
        page_link: {pdf: "x/y/v.pdf", pdf_page: 1, label: "PDF p1", paper: "Mint"}}]}]};

eq("[fixture] §8.5 · Articles flattens each story into its own articles",
   APP.exploreArticleRows(exResult).map(function (r) {
     return r.story.story_id + "/" + r.article.text_hash; }),
   ["e1/a1", "e1/a2", "e2/a3"]);
eq("[fixture] §8.5 · and Stories keeps one row per story",
   exResult.stories.length, 2);

var storyRow = APP.exploreRow(exResult.stories[0], null);
eq("[fixture] §8.5 · a row names its best article's page and snippet",
   textOf(storyRow).indexOf("Mint PDF p3 · First article") > 0, true);
eq("[fixture] §8.5 · and the day, the papers and how many matched",
   textOf(storyRow).indexOf("11 Oct · 2 papers · 2 matching articles") > 0, true);
eq("[fixture] §8.5 · with a checkbox that says what it chooses",
   controlsOf(storyRow).filter(function (c) {
     return c.tag === "INPUT" && c.aria === "Choose The first story"; }).length, 1);
eq("[fixture] §8.5 · every control in a row is real and labelled",
   controlsOf(storyRow).filter(function (c) { return !c.label; }), []);
var articleRow = APP.exploreRow(exResult.stories[0], exResult.stories[0].articles[1]);
eq("[fixture] §8.5 · an article row is headed by the article, not the story",
   textOf(articleRow).indexOf("Second article"), 0);

// the whole screen, including H6
document.byId.reading = document.createElement("section");
function exploreText(result, over) {
  PS.explore = Object.assign({query: "", filters: {}, sort: "newest", rows: "stories",
                              result: result, selected: new Set(), openMenu: null},
                             over || {});
  var pane = document.createElement("section");
  document.byId["explore-actions"] = null;
  APP.exploreScreen(pane);
  return {text: textOf(pane), controls: controlsOf(pane), node: pane};
}
var screen = exploreText(exResult, {query: "police", sort: "relevance"});
eq("[fixture] §8.5 · the screen names itself", screen.text.indexOf("Explore"), 0);
eq("[fixture] §8.5 · both switches are there",
   ["Relevance", "Newest", "Stories", "Articles"].filter(function (w) {
     return screen.controls.filter(function (c) {
       return c.text === w; }).length !== 1; }), []);
eq("[fixture] §8.5 · the filter menus come from the facets",
   screen.controls.filter(function (c) {
     return c.text.indexOf("Papers ▾") === 0 || c.text.indexOf("Topics ▾") === 0
            || c.text.indexOf("Kinds ▾") === 0; }).length, 3);
eq("[fixture] §8.5 · the date fields are bounded by the archive's own range",
   [APP.FILTER_MENUS.length, PS.facets.date_min], [3, "2026-10-09"]);

var browsing = exploreText(exResult, {query: ""});
eq("[fixture] §8.5 · with no keyword, Relevance cannot be chosen",
   browsing.controls.filter(function (c) {
     return c.text === "Relevance" && c.disabled; }).length, 1);
eq("[fixture] §8.5 · and Newest is the one that is pressed",
   browsing.controls.filter(function (c) {
     return c.text === "Newest" && !c.disabled; }).length, 1);

var chosen = exploreText(exResult, {query: "police", selected: new Set(["e1", "e2"])});
eq("[fixture] §8.5 · the actions say how many are chosen, and cost nothing",
   [chosen.controls.filter(function (c) {
      return c.text === "Ask about these 2"; }).length,
    chosen.text.indexOf("no model call · 0¢") > 0,
    chosen.controls.filter(function (c) { return c.text === "Pin selected"; }).length],
   [1, true, 1]);
var none = exploreText(exResult, {});
eq("[fixture] §8.5 · with nothing chosen both actions are off",
   none.controls.filter(function (c) {
     return c.text.indexOf("Ask about these") === 0 && c.disabled; }).length, 1);

// H6 · zero results
var empty = exploreText({query: "Norway", filters: {papers: ["Mint"]}, sort: "relevance",
                         verdict: "empty", total_stories: 0, total_articles: 0,
                         searched_days: 7, stories: []},
                        {query: "Norway", filters: {papers: ["Mint"]}});
eq("[fixture] H6 · it says nothing matches", empty.text.indexOf("No stories match") > 0,
   true);
eq("[fixture] H6 · the chips that caused it are still on screen",
   [empty.text.indexOf("“Norway”") > 0, empty.text.indexOf("Mint") > 0], [true, true]);
eq("[fixture] H6 · and Clear all is the way out",
   empty.controls.filter(function (c) { return c.text === "Clear all"; }).length, 1);

// check 8 · explore off takes the nav item away
eq("[fixture] check 8 · the flag names the nav item it removes",
   APP.FLAG_ELEMENTS.explore, '.nav-item[data-screen="explore"]');

PS.explore = null;
PS.facets = null;

// every feature flag has to say what it takes off the page, or a flag can be added
// that silently hides nothing (acceptance check 7)
eq("every flag names the element it removes",
   Object.keys(APP.FLAG_ELEMENTS).sort(), FLAGS_FROM_SERVER.sort());

/* The names of the passing checks travel back too, not only the failures: the report
   counts how many checks are `behaviour` (K3 must pass them) and how many are
   `fixture`, and a tally that cannot see the names cannot say. */
print(JSON.stringify({ran: ran, fails: fails, names: names}));
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
        script.write_text('var ROOT = "%s";\nvar FLAGS_FROM_SERVER = %s;\n%s%s'
                          % (ROOT, flags, JS_DOM, JS_TEST))
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
        failed = {}
        for f in result["fails"]:
            name, _, detail = f.partition(" :: ")
            failed[name] = detail
        # Every assertion the page's own checks made is registered under its own name,
        # not only the failures: the total this test prints is the number of things
        # actually checked, and the names carry the behaviour/fixture tags the report
        # counts (brief §1 C1).
        for name in result.get("names") or []:
            if name in failed:
                check("page logic: " + name, failed.pop(name), "")
            else:
                CHECKS.append(("page logic: " + name, True, True))
        for name, detail in failed.items():
            check("page logic: " + name, detail, "")


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
    # v0.1, acceptance check 6: the pane's own marks and the borders in it
    ("--mark", "--assistant-ground", "the pane's control borders and disabled text"),
    ("--mark", "--chip-ground", "a disabled control inside the cap band"),
    ("--ochre", "--assistant-ground", "the CITED mark's ground and Think harder"),
    ("--page", "--ochre", "the CITED mark's own text on its ochre fill"),
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


def drain(response):
    """Every line a streamed route really sends, as parsed events.

    The brief (§5) asks for the response body iterator itself rather than the generator
    behind it, so this is what the browser would read: Starlette's async iterator, run
    to the end. A chunk is NOT assumed to be a line -- the chunks are joined and then
    split, which is the same thing the page's own parser has to do."""
    import asyncio

    async def collect():
        chunks = []
        async for chunk in response.body_iterator:
            chunks.append(chunk if isinstance(chunk, str) else chunk.decode())
        return "".join(chunks)

    body = asyncio.run(collect())
    return [json.loads(line) for line in body.splitlines() if line.strip()]


def v01_routes():
    """B4 · the routes of brief 2 §5, called directly (v0 D5: no port to bind).

    Pass lines, written before the run:
    - Every one of the fourteen new routes the brief lists exists on the app.
    - `/api/search` searches and browses, and each filter arrives through the query
      string; `Query(None)`'s placeholder object is read as "no filter", not as a crash.
    - `/api/facets` answers the five keys.
    - [behaviour] `/api/ask` is `application/x-ndjson`, one JSON event per line, and the
      `results` event really is the first line out -- read off the response body
      iterator, not the generator behind it (acceptance check 4).
    - `/api/think_harder` streams without a `results` event, and 404s on an answer id
      that does not exist.
    - `/api/background` 400s on an answer that already has a background line and on one
      that does not exist.
    - `/api/estimate` answers the four kinds and 400s on a fifth.
    - The conversation routes: read it, add a divider, start a new one.
    - `/api/suggest` answers, and 404s on a story that does not exist.
    - `/api/cap/raise` raises today's cap and returns the new cost object.
    - `/api/user_link` creates, `/api/user_links` lists, DELETE removes, and deleting a
      link that is not there is a 404.
    - A posted body missing a required field is a 400 on every new POST route.
    - The `assistant_error` switch takes a reason by name through the dev route, keeps
      it as a string (it is not a yes/no), and refuses a reason the double never heard
      of rather than letting the pane show the wrong wording.
    """
    import p3_app as A
    import p3_app_double as D

    with tempfile.TemporaryDirectory() as tmp:
        D.ROOT, D.ARCHIVE = ROOT, ROOT / "archive"
        D.WORK = Path(tmp)
        D.STATE_FILE = Path(tmp) / "state.json"
        D.KNOBS["fixture_stream_delay_ms"] = 0
        D.FIXTURES["assistant_error"] = False
        D.FIXTURES["cap_reached"] = False

        paths = {r.path for r in A.app.routes if hasattr(r, "path")}
        wanted = {"/api/search", "/api/facets", "/api/ask", "/api/think_harder",
                  "/api/background", "/api/estimate", "/api/conversation",
                  "/api/conversation/new", "/api/conversation/divider", "/api/suggest",
                  "/api/cap/raise", "/api/user_link", "/api/user_link/{link_id}",
                  "/api/user_links"}
        check("[fixture] every route brief 2 §5 lists exists",
              sorted(wanted - paths), [])

        # --- search and facets --------------------------------------------------
        found = A.search(q="police")
        check("[fixture] /api/search searches",
              [found["verdict"], found["total_stories"]], ["enough", 4])
        browse = A.search(q="")
        check("[fixture] and browses when the query is empty",
              [browse["verdict"], browse["total_stories"]], [None, 19])
        check("[fixture] a filter arrives through the query string",
              sorted({a["paper"] for s in A.search(q="", papers=["Mint"])["stories"]
                      for a in s["articles"]}), ["Mint"])
        check("[fixture] the Query placeholder is read as 'no filter', not a crash",
              A.search(q="")["total_stories"], 19)
        check("[fixture] the limit is capped so one request cannot ask for everything",
              len(A.search(q="", limit=10_000)["stories"]), 19)
        check("[fixture] /api/facets answers its five keys", sorted(A.facets()),
              ["date_max", "date_min", "kinds", "papers", "topics"])

        # --- the streamed answer ------------------------------------------------
        response = A.ask(payload={"question": "police"})
        check("[behaviour] /api/ask is NDJSON", response.media_type,
              "application/x-ndjson")
        events = drain(response)
        check("[behaviour] and the results event is the first line out",
              [e["type"] for e in events][0], "results")
        check("[behaviour] with sentences between it and done",
              [e["type"] for e in events][-1], "done")
        check("[fixture] one JSON object per line, newline-terminated",
              list(A.ndjson([{"a": 1}, {"b": 2}])), ['{"a": 1}\n', '{"b": 2}\n'])
        answer = events[-1]["answer"]

        harder = A.think_harder(payload={"answer_id": answer["answer_id"]})
        check("[fixture] /api/think_harder streams without a results event",
              [e["type"] for e in drain(harder) if e["type"] == "results"], [])
        check("[fixture] and 404s on an answer that does not exist",
              status_of(A.think_harder, payload={"answer_id": "ans_nope"}), 404)
        check("[fixture] a missing answer_id is a 400",
              status_of(A.think_harder, payload={}), 400)

        # --- background and estimates -------------------------------------------
        empty = drain(A.ask(payload={"question": "Norway"}))[-1]["answer"]
        got = A.background(payload={"answer_id": empty["answer_id"]})
        check("[fixture] /api/background answers with the paragraph and its cost",
              [bool(got["text"]), got["cost_usd"]], [True, 0.0003])
        check("[fixture] and 400s when the answer already has one",
              status_of(A.background, payload={"answer_id": empty["answer_id"]}), 400)
        check("[fixture] and on an answer that does not exist",
              status_of(A.background, payload={"answer_id": "ans_nope"}), 400)
        check("[fixture] /api/estimate answers the four kinds",
              [A.estimate(kind=k)["cost_usd"]
               for k in ("ask", "think_harder", "background", "suggest")],
              [0.0005, 0.011, 0.0003, 0.0005])
        check("[fixture] and 400s on a kind it does not know",
              status_of(A.estimate, kind="nonsense"), 400)

        # --- the conversation ---------------------------------------------------
        conv = A.conversation()
        check("[fixture] /api/conversation answers the running one",
              sorted(conv), ["conversation_id", "started_at", "turns"])
        divider = A.conversation_divider(payload={"story_id": "sid_1",
                                                  "headline": "A HEADLINE"})
        check("[fixture] a divider is added as its own turn",
              [divider["kind"], A.conversation()["turns"][-1]["kind"]],
              ["divider", "divider"])
        check("[fixture] a divider without a headline is a 400",
              status_of(A.conversation_divider, payload={"story_id": "x"}), 400)
        fresh = A.conversation_new()["conversation_id"]
        check("[fixture] /api/conversation/new starts an empty one",
              [fresh != conv["conversation_id"], A.conversation()["turns"]], [True, []])

        # --- suggestions and the reader's links ---------------------------------
        story_id = "2026-10-11:56eaf7aa4d5b"
        story = D._find_story(lambda s: s.get("story_id") == story_id)
        sugg = A.suggest(payload={"story_id": story_id,
                                  "text_hashes": D._story_hashes(story)})
        check("[fixture] /api/suggest answers with three to five suggestions",
              D.KNOBS["suggest_min"] <= len(sugg["suggestions"])
              <= D.KNOBS["suggest_max"], True)
        check("[fixture] and 404s on a story that does not exist",
              status_of(A.suggest, payload={"story_id": "nope"}), 404)
        check("[fixture] a missing story_id is a 400",
              status_of(A.suggest, payload={}), 400)

        target_id = sugg["suggestions"][0]["story_id"]
        target = D._find_story(lambda s: s.get("story_id") == target_id)
        made = A.user_link(payload={"story_id": story_id,
                                    "text_hashes": D._story_hashes(story),
                                    "target_story_id": target_id,
                                    "target_text_hashes": D._story_hashes(target)})
        check("[fixture] /api/user_link answers with the new link's id",
              bool(made["link_id"]), True)
        listed = A.user_links(hashes=D._story_hashes(story))["user_links"]
        check("[fixture] /api/user_links lists it, so the + icon is dark after a reload",
              [len(listed), listed[0]["link_id"]], [1, made["link_id"]])
        check("[fixture] DELETE removes it",
              A.user_link_remove(link_id=made["link_id"])["removed"], made["link_id"])
        check("[fixture] and removing it twice is a 404",
              status_of(A.user_link_remove, link_id=made["link_id"]), 404)
        check("[fixture] a user link missing an end is a 400",
              status_of(A.user_link, payload={"story_id": story_id}), 400)

        # --- the cap ------------------------------------------------------------
        was = A.cost()["cap_usd"]
        raised = A.cap_raise(payload={"amount_usd": 1.00})
        check("[fixture] /api/cap/raise answers with the new cost object",
              [sorted(raised), raised["override_usd"]],
              [["cap_usd", "fixture", "override_usd", "spent_usd"], 1.0])
        check("[fixture] and the cap really went up by the dollar",
              raised["cap_usd"], round(was + 1.00, 6))
        check("[fixture] an empty body uses the knob's own amount",
              A.cap_raise(payload=None)["override_usd"], 2.0)

        # --- the error switch takes a reason by name ----------------------------
        state = A.set_fixtures(payload={"assistant_error": "timeout"})
        check("[fixture] the error switch keeps its reason as a string, not a bool",
              state["fixtures"]["assistant_error"], "timeout")
        check("[fixture] and the pane gets that reason",
              drain(A.ask(payload={"question": "police"}))[-1]["reason"], "timeout")
        check("[fixture] a reason the double never heard of is refused here",
              status_of(A.set_fixtures, payload={"assistant_error": "sulking"}), 400)
        A.set_fixtures(payload={"assistant_error": False})
        check("[fixture] and switching it off still works",
              D.FIXTURES["assistant_error"], False)


def v01_states():
    """B8 · acceptance check 3: every state of §8.3 can be PRODUCED, not just drawn.

    The page checks under jsc prove each state renders with the brief's words. This is
    the other half: that the data behind it comes out of the double from a real query
    or a fixture switch, so Kunaal can reach every state in the browser without a
    special build.

    Pass lines, written before the run: each of the nine states below is reachable, and
    the input that reaches it is named here so the report's manual list can repeat it.
    """
    import p3_app_double as D

    with tempfile.TemporaryDirectory() as tmp:
        D.ROOT, D.ARCHIVE = ROOT, ROOT / "archive"
        D.WORK = Path(tmp)
        D.STATE_FILE = Path(tmp) / "state.json"
        D.KNOBS["fixture_stream_delay_ms"] = 0
        D.FIXTURES["assistant_error"] = False
        D.FIXTURES["cap_reached"] = False

        # 1 and 2 · answering, then an answer, from the query "police"
        events = list(D.ask("police", None))
        answer = events[-1]["answer"]
        check("[fixture] state 1 · 'police' gives a results event before any sentence",
              [events[0]["type"], events[0]["search"]["verdict"]],
              ["results", "enough"])
        check("[fixture] state 2 · and an answer with sentences and cites",
              [len(answer["sentences"]) >= 2, len(answer["cited"]) >= 1], [True, True])

        # 3 · think harder
        harder = list(D.think_harder(answer["answer_id"]))[-1]["answer"]
        check("[fixture] state 3 · Think harder on it gives a Sonnet answer",
              harder["model"], "sonnet")

        # 4 · weak match, from the query "GST"
        thin = list(D.ask("GST", None))[-1]["answer"]
        check("[fixture] state 4 · 'GST' gives a thin answer with a count to show",
              [thin["verdict"], thin["found_count"] > 0], ["thin", True])

        # 5 and 6 · nothing found, then background on request
        empty = list(D.ask("Norway", None))[-1]["answer"]
        check("[fixture] state 5 · 'Norway' gives an empty verdict and three closest",
              [empty["verdict"], len(empty["closest"])], ["empty", 3])
        check("[fixture] state 6 · and background on request answers for it",
              bool(D.ask_background(empty["answer_id"])["text"]), True)

        # 7 · a divider, from opening another story
        conv = D.current_conversation()
        D.add_divider(conv["conversation_id"], "sid", "ANOTHER STORY")
        check("[fixture] state 7 · opening another story adds a divider turn",
              D.current_conversation()["turns"][-1]["kind"], "divider")

        # 8 · the conversation kept across a restart
        check("[fixture] state 8 · and the conversation survives a restart",
              _fresh_double(D.STATE_FILE).current_conversation()["conversation_id"],
              conv["conversation_id"])

        # 9 · the error reasons and the cap, both from fixture switches
        reached = {}
        for reason in ("model_busy", "no_network", "key_rejected", "timeout",
                       "checks_failed", "other"):
            D.FIXTURES["assistant_error"] = reason
            reached[reason] = list(D.ask("police", None))[-1]["reason"]
        check("[fixture] state 9 · every error reason is reachable by its switch",
              sorted(k for k, v in reached.items() if k == v), sorted(reached))
        D.FIXTURES["assistant_error"] = False
        D.FIXTURES["cap_reached"] = True
        check("[fixture] state 9 · and the cap band by the cap_reached switch",
              list(D.ask("police", None))[-1]["code"], "cap_reached")
        D.FIXTURES["cap_reached"] = False

        # 10 (D3) and 11 · suggestions and Explore, both from real data
        story_id = "2026-10-11:56eaf7aa4d5b"
        story = D._find_story(lambda s: s.get("story_id") == story_id)
        sugg = D.suggest_connections(story_id, D._story_hashes(story))
        check("[fixture] state 10 · the Delhi story gives three to five suggestions",
              D.KNOBS["suggest_min"] <= len(sugg["suggestions"])
              <= D.KNOBS["suggest_max"], True)
        bare = [s["story_id"] for s in D._search_corpus()
                if D.thread(s["story_id"]) is None][0]
        bare_story = D._find_story(lambda s: s.get("story_id") == bare)
        target = D._find_story(
            lambda s: s.get("story_id") == sugg["suggestions"][0]["story_id"])
        link_id = D.add_user_link(bare, D._story_hashes(bare_story),
                                  target["story_id"], D._story_hashes(target))
        check("[fixture] state 10 · and + on one gives a thread where there was none",
              [e["link_type"] for e in D.thread(bare)["entries"]
               if e["link_type"] == "user"], ["user"])
        D.remove_user_link(link_id)
        check("[fixture] state 11 · Explore's filters all have something behind them",
              [v for key in ("papers", "topics", "kinds")
               for v in D.facets()[key]
               if not D.search("", filters={key: [v]})["total_stories"]], [])
        check("[fixture] state 11 · and H6 is reachable with a real filter pair",
              D.search("Norway", filters={"papers": ["Mint"]})["total_stories"], 0)


def main():
    with tempfile.TemporaryDirectory() as tmp:
        mock(tmp)
    real()
    v01_search()
    v01_ask()
    routes()
    v01_routes()
    v01_states()
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
