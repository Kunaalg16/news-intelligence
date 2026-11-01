"""
test_p3_app.py -- TEST (free, no API key, no internet). Checks the K1 app v0, part 1:
src/p3_app_double.py (the §4 interface). Part 2 (the routes and the page) arrives with A2.

WHAT IT DOES
    Two parts, neither of which touches a database or the reader's real state file.
    1. MOCK: a synthetic digest JSON + a synthetic archive of empty PDFs in a temporary
       directory, with the double pointed at them. Every function of §4 is exercised
       there -- read on leave, keep unread, new-since, boards, pins, follows, feedback,
       prefs, visits, cost, the invented thread and the invented article body.
    2. REAL DATA (read-only): the two digests in `_work/`, with the state file sent to a
       temporary path so the run cannot touch Kunaal's own read state. This is where the
       numbers that the screens will show are pinned.

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
    Ownership (brief §2.1, acceptance check 9)
    - The double imports no sqlite3, no p3_store, no p3_digest, and nothing that reaches
      the network. It writes exactly one file: `_work/k1_app_state.json`.
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


# ------------------------------------------------------------------- 3. ownership

def ownership():
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
    ownership()
    bad = [(n, g, w) for n, g, w in CHECKS if g != w]
    for n, g, w in bad:
        print(f"FAIL {n}: got {g!r}, want {w!r}")
    print(f"{len(CHECKS) - len(bad)}/{len(CHECKS)} checks passed "
          f"(mock world + the two real digests + ownership)")
    print("no database was opened and no real read state was touched by this test")
    print("PASS" if not bad else "FAIL")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
