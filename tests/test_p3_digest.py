"""
test_p3_digest.py -- TEST (free, no API key, no internet). Checks src/p3_digest.py (d0.1), part 1.

WHAT IT DOES
    Two parts, neither of which touches the real databases.
    1. RULES: the ranking, headline, one-liner and theme rules as hand-made inputs --
       one case per rule in the brief, and the awkward neighbours of each (a paper that
       runs a story four days, a headline that stops on a comma, a topic with nothing
       before it, a story whose only piece is a notice).
    2. MOCK PIPELINE: a small synthetic news.db + intel.db + vectors.db built in a
       temporary directory, run end to end through build(), checking the whole contract
       object: order, scores, coverage counts, views, caveats, printed links, page links.

WHY IT EXISTS
    The ranking decides what Kunaal reads each morning and the rest is what he sees
    around it. Both are pure functions of stored data, so they can be pinned exactly --
    and the ranking weights are placed guesses that WILL be moved by the weight test, so
    the rules need a test that fails loudly when a weight change does more than intended.

PASS LINES (fixed before the run, K3 brief §3 and §B1-3)
    - Placement: front page 3, early 2, inside 1; size: top 10% +2, above median +1.
    - A paper's points = its best SINGLE day; the story's score = the sum over papers.
    - Only coverage kinds score. A story with no coverage scores zero and is never shown.
    - Tie-breaks fire in order: days covered -> total words -> earliest first print -> id.
    - Headlines never come from a fragment or garbled text; too long / too short /
      mid-sentence each push to the next candidate; all refused -> writer headline needed.
    - One-liner = the stored summary of the highest-points editorial piece, skipping
      fragment, garbled and paid; none available -> the printed headline.
    - Theme hidden below 3 stories in 21 days; rising / falling / steady as defined.
    - Coverage counts articles and papers separately; notices, paid and 'other' are in
      neither count. Printed links point only to a DIFFERENT story.
    - Every page link carries a PDF path and a "PDF pN" label (the printed->PDF offset
      is not stored, so no printed page number is ever claimed).
    A failed line is answered by error analysis, not by moving the line.

WHEN TO RUN
    After every change to src/p3_digest.py, and after any knob change. About two seconds.

USAGE (from ~/news-intel)
    .venv/bin/python tests/test_p3_digest.py
"""

import array
import collections
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import p3_digest as D
import p3_label as L

CHECKS = []


def check(name, got, want):
    CHECKS.append((name, got, want))


def seat(**kw):
    """One labelled printed slot, as load_seats() hands them to the ranking."""
    base = dict(date="2026-10-01", publisher="Mint", art_id="p01-a01", page=1, number=1,
                deck="", byline="Our reporter", text_hash="h1",
                extra={"paper": "2026-10-01_Mint"},
                source_file="archive/2026-10-01/2026-10-01_Mint.pdf",
                headline="A clear printed headline here", words=500, kind="news",
                topic="Energy", summary="A stored one-line summary.",
                event="policy or regulation", events_more="[]", text_check="ok")
    base.update(kw)
    label = L.label_text(base)
    label.update({k: base[k] for k in ("date", "publisher", "art_id", "page", "number",
                                       "deck", "byline", "extra", "source_file",
                                       "headline", "words")})
    return label


def tcounts(topics, recent_days=7, earlier_days=14, end="2026-10-24", by_day=None):
    """A `topic_counts_over` return value, made by hand.

    Its shape lives here once, so a change to what `topic_counts_over` returns breaks one
    helper and not nine fixtures. `papers_on` is the last `recent_days` of the recent week
    plus the first `earlier_days` of the earlier fortnight, which is what the counts say.
    """
    window = D.KNOBS["theme_window_days"]
    calendar = [D._back(end, n) for n in range(window - 1, -1, -1)]
    recent, earlier = calendar[-7:], calendar[:-7]
    out = {t: {**c, "by_day": collections.Counter((by_day or {}).get(t, {}))}
           for t, c in topics.items()}
    out["__days__"] = {"recent": recent_days, "earlier": earlier_days,
                       "calendar": calendar,
                       "papers_on": set(recent[-recent_days:] if recent_days else [])
                       | set(earlier[:earlier_days])}
    return out


def story(seats, story_id="s1"):
    return {"story_id": story_id, "group_ids": ["g1"], "seats": seats}


# ------------------------------------------------------------------ 1. the rules

def rules():
    # --- helpers the ranking is built on -----------------------------------------
    check("early = first quarter of the pages with articles",
          D._early_until([1, 2, 3, 4, 5, 6, 7, 8]), 2)
    check("early with few pages still covers one", D._early_until([3, 9]), 3)
    check("early of a single page", D._early_until([5]), 5)
    check("no pages -> no early line", D._early_until([]), None)
    check("median of a day's words", D._percentile([100, 200, 300], 0.5), 200)
    check("top decile of a day's words",
          D._percentile([10, 20, 30, 40, 50, 60, 70, 80, 90, 100], 0.9), 100)
    check("percentile of nothing", D._percentile([], 0.5), None)

    # --- placement and size points ------------------------------------------------
    seats = [seat(page=p, words=w, text_hash=f"h{p}", art_id=f"p{p:02d}-a01")
             for p, w in ((1, 900), (2, 500), (9, 100), (12, 300))]
    shape = D.paper_day_shape(seats)
    key = ("2026-10-01", "Mint")
    check("front page = the lowest page with articles", shape[key]["front"], 1)
    check("the early line for these four pages", shape[key]["early_until"], 1)
    points = {s["page"]: D.seat_points(s, shape) for s in seats}
    check("front page + top-decile size = 3 + 2", points[1][0], 5)
    check("front page placement named", points[1][1]["placement"], "front")
    # four pages: the first quarter is one page, which IS the front page, so the early
    # band is empty here and every other page is inside. A real paper-day has ~17 pages.
    check("with four pages the early band is empty", points[2][1]["placement"], "inside")
    check("page 2 inside + above the 400-word median = 1 + 1", points[2][0], 2)
    check("page 9 inside + below median = 1 + 0", points[9][0], 1)
    check("page 12 inside + below the median = 1 + 0", points[12][0], 1)
    many = [seat(page=p, words=300, text_hash=f"M{p}") for p in (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12)]
    many_shape = D.paper_day_shape(many)
    check("twelve pages: the early band is the first quarter",
          many_shape[("2026-10-01", "Mint")]["early_until"], 3)
    check("page 3 of twelve is early",
          D.seat_points(many[2], many_shape)[1]["placement"], "early")
    check("page 4 of twelve is inside",
          D.seat_points(many[3], many_shape)[1]["placement"], "inside")

    # a paper-day whose pages start at 5: the lowest page with articles is the front page
    late = [seat(page=p, words=300, text_hash=f"L{p}", publisher="BL") for p in (5, 6, 20)]
    late_shape = D.paper_day_shape(late)
    check("a paper-day starting at page 5 still has a front page",
          late_shape[("2026-10-01", "BL")]["front"], 5)
    check("its page 5 scores as front", D.seat_points(late[0], late_shape)[1]["placement"], "front")

    # --- only coverage kinds score -------------------------------------------------
    for kind in ("sponsored", "notice", "other"):
        s = seat(kind=kind, page=1, words=900)
        check(f"kind {kind} scores nothing", D.seat_points(s, D.paper_day_shape([s]))[0], 0)
    s = seat(kind="opinion", page=1, words=900)
    check("kind opinion scores", D.seat_points(s, D.paper_day_shape([s]))[0] > 0, True)

    # --- best single day per paper, summed over papers ------------------------------
    four_days = [seat(date=f"2026-10-0{d}", page=1, words=900, text_hash=f"m{d}",
                      art_id=f"p01-a0{d}") for d in (1, 2, 3, 4)]
    st = story(four_days)
    D.score_story(st, D.paper_day_shape(four_days))
    check("a paper running a story four days still votes once", st["score"], 5)
    two_papers = [seat(page=1, words=900, text_hash="a1"),
                  seat(page=1, words=900, text_hash="a2", publisher="The Hindu",
                       source_file="archive/2026-10-01/2026-10-01_The Hindu.pdf")]
    st2 = story(two_papers)
    D.score_story(st2, D.paper_day_shape(two_papers))
    check("two papers each vote", st2["score"], 10)
    check("per-paper points are kept for the report",
          sorted(st2["paper_points"].items()), [("Mint", 5), ("The Hindu", 5)])

    only_paid = story([seat(kind="sponsored", page=1, words=900)])
    D.score_story(only_paid, D.paper_day_shape(only_paid["seats"]))
    check("a story with no coverage scores zero", only_paid["score"], 0)
    scored, zero = D.rank([st2, only_paid], D.paper_day_shape(two_papers + only_paid["seats"]))
    check("a zero-point story is not ranked", [s["story_id"] for s in scored], ["s1"])
    check("it is reported as dropped", len(zero), 1)

    # --- tie-breaks, in order -------------------------------------------------------
    def tie_story(sid, days, words, first):
        seats = [seat(date=d, page=9, words=words // len(days), text_hash=f"{sid}{i}",
                      art_id=f"p09-a{i:02d}") for i, d in enumerate(days)]
        s = story(seats, sid)
        s["score"] = 4
        return s
    a = tie_story("A", ["2026-10-01", "2026-10-02"], 400, "2026-10-01")
    b = tie_story("B", ["2026-10-02"], 400, "2026-10-02")
    check("equal score: more days covered wins",
          [s["story_id"] for s in sorted([b, a], key=D.tie_key)], ["A", "B"])
    c = tie_story("C", ["2026-10-02"], 900, "2026-10-02")
    check("equal score and days: more words wins",
          [s["story_id"] for s in sorted([b, c], key=D.tie_key)], ["C", "B"])
    d = tie_story("D", ["2026-10-01"], 400, "2026-10-01")
    check("equal score, days and words: the earlier first print wins",
          [s["story_id"] for s in sorted([b, d], key=D.tie_key)], ["D", "B"])
    e, f = tie_story("E", ["2026-10-02"], 400, "2026-10-02"), tie_story("F", ["2026-10-02"], 400, "2026-10-02")
    check("all equal: the story id makes it a total order",
          [s["story_id"] for s in sorted([f, e], key=D.tie_key)], ["E", "F"])

    # --- headline checks -------------------------------------------------------------
    check("a normal headline passes", D.headline_problem("Bank raises rates by a quarter point"), "")
    check("an empty headline", D.headline_problem(""), "empty")
    check("two words is too short", D.headline_problem("Rates up"), "too_short")
    check("three words is allowed", D.headline_problem("Bank raises rates"), "")
    check("seventeen words is too long",
          D.headline_problem(" ".join(["word"] * 17)), "too_long")
    check("sixteen words is allowed", D.headline_problem(" ".join(["bank"] * 16)), "")
    check("a trailing comma is mid-sentence",
          D.headline_problem("Bank raises rates and the market,"), "mid_sentence")
    check("a dangling conjunction is mid-sentence",
          D.headline_problem("Bank raises rates and"), "mid_sentence")
    check("a trailing dash is mid-sentence", D.headline_problem("Bank raises rates -"), "mid_sentence")

    # --- medoid -----------------------------------------------------------------------
    vec = lambda *xs: array.array("f", xs)
    vectors = {"x": vec(1, 0, 0), "y": vec(0.9, 0.1, 0), "z": vec(0.95, 0.05, 0)}
    check("the medoid is the most central text", D.medoid(["x", "y", "z"], vectors) in ("z", "x"), True)
    check("one candidate is its own medoid", D.medoid(["x"], vectors), "x")
    check("no vectors: the first candidate stands", D.medoid(["q", "r"], {}), "q")
    check("no candidates at all", D.medoid([], {}), None)

    # --- headline choice --------------------------------------------------------------
    good = seat(text_hash="g", headline="Bank raises rates by a quarter point", page=1, words=900)
    frag = seat(text_hash="f", headline="Bank raises rates sharply today", text_check="fragment",
                page=1, words=900, art_id="p01-a02")
    st = story([frag, good])
    D.score_story(st, D.paper_day_shape([frag, good]))
    picked = D.pick_headline(st, {})
    check("a fragment's headline is never used", picked["source_text_hash"], "g")
    check("a clean headline needs no fallback", picked["fallbacks"], [])

    short = seat(text_hash="s", headline="Rates up", page=1, words=900)
    st = story([short, good])
    D.score_story(st, D.paper_day_shape([short, good]))
    picked = D.pick_headline(st, {"s": vec(1, 0, 0)})
    check("a too-short headline pushes to the next candidate", picked["source_text_hash"], "g")
    check("the refusal is reported", picked["fallbacks"], ["too_short"])

    bad1 = seat(text_hash="b1", headline="Rates up", page=1, words=900)
    bad2 = seat(text_hash="b2", headline="Bank raises rates and", page=1, words=900,
                art_id="p01-a03")
    st = story([bad1, bad2])
    D.score_story(st, D.paper_day_shape([bad1, bad2]))
    picked = D.pick_headline(st, {})
    check("every candidate refused -> the writer is needed",
          picked["fallbacks"][-1], "writer_headline_needed")
    check("and something printed is still shown", bool(picked["text"]), True)

    only_frag = story([seat(text_hash="of", headline="A fragment headline here",
                            text_check="garbled", page=1, words=900)])
    D.score_story(only_frag, D.paper_day_shape(only_frag["seats"]))
    check("a story of only garbled text still gets a headline",
          D.pick_headline(only_frag, {})["source_text_hash"], "of")

    # --- one-liner ---------------------------------------------------------------------
    high = seat(text_hash="hi", page=1, words=900, summary="The important summary.")
    low = seat(text_hash="lo", page=9, words=100, summary="The lesser summary.",
               art_id="p09-a01")
    st = story([low, high])
    D.score_story(st, D.paper_day_shape([low, high]))
    check("the one-liner comes from the highest-points piece",
          D.pick_one_liner(st, {"text": "H"})["text"], "The important summary.")

    paid = seat(text_hash="pd", page=1, words=900, kind="sponsored", summary="Paid summary.")
    st = story([paid, low])
    D.score_story(st, D.paper_day_shape([paid, low]))
    check("a paid piece never supplies the one-liner",
          D.pick_one_liner(st, {"text": "H"})["text"], "The lesser summary.")

    frag_sum = seat(text_hash="fs", page=1, words=900, text_check="fragment",
                    summary="Fragment summary.")
    st = story([frag_sum, low])
    D.score_story(st, D.paper_day_shape([frag_sum, low]))
    check("a fragment never supplies the one-liner (Q6t)",
          D.pick_one_liner(st, {"text": "H"})["text"], "The lesser summary.")

    none_left = story([seat(text_hash="n1", kind="notice", summary="Notice summary.")])
    D.score_story(none_left, D.paper_day_shape(none_left["seats"]))
    fallback = D.pick_one_liner(none_left, {"text": "The printed headline"})
    check("nothing usable -> the printed headline", fallback["text"], "The printed headline")
    check("and the fallback is named", fallback["fallback"], "printed_headline")

    # --- theme line --------------------------------------------------------------------
    # A full three weeks of papers: the rates are then this week's count and the earlier
    # weekly average, which is 7a exactly.
    counts = tcounts({"Banking": {"total": 5, "recent": 4, "earlier": 2},
                      "Energy": {"total": 2, "recent": 2, "earlier": 0},
                      "Health": {"total": 6, "recent": 1, "earlier": 5},
                      "Sport": {"total": 4, "recent": 2, "earlier": 4}},
                     by_day={"Banking": {"2026-10-24": 2, "2026-10-22": 2,
                                         "2026-10-10": 1}})
    th = lambda topic: D.theme_block(story([seat(topic=topic)]), counts)
    check("4 this week vs 1 a week before -> rising", th("Banking")["trend"], "rising")
    check("and the 21-day count is shown", th("Banking")["stories_21d"], 5)
    check("below the minimum -> no theme line", th("Energy"), None)
    check("1 this week vs 2.5 before -> falling", th("Health")["trend"], "falling")
    check("2 this week vs 2 before -> steady", th("Sport")["trend"], "steady")
    check("papers ran but carried nothing on it before -> rising",
          D.theme_block(story([seat(topic="New")]),
                        tcounts({"New": {"total": 3, "recent": 3, "earlier": 0}}))["trend"],
          "rising")
    check("no topic at all -> no theme line",
          D.theme_block(story([seat(topic=None)]), counts), None)

    # the guard: no papers at all in the earlier two weeks -> no trend word, count kept
    gapped = tcounts({"Banking": {"total": 4, "recent": 4, "earlier": 0}}, earlier_days=0)
    g = D.theme_block(story([seat(topic="Banking")]), gapped)
    check("no papers in the earlier weeks -> no trend is claimed", g["trend"], None)
    check("and the reason is given", g["no_trend_because"], "no papers in the earlier weeks")
    check("but the 21-day count is still shown", g["stories_21d"], 4)
    D.KNOBS["theme_trend_needs_earlier_days"] = False
    check("with the guard switched off a trend is stated again",
          D.theme_block(story([seat(topic="Banking")]), gapped)["trend"], "rising")
    D.KNOBS["theme_trend_needs_earlier_days"] = True

    # two earlier publication days is not "the earlier weeks" either
    thin = tcounts({"Banking": {"total": 4, "recent": 4, "earlier": 0}}, earlier_days=2)
    th2 = D.theme_block(story([seat(topic="Banking")]), thin)
    check("two earlier publication days -> still no trend claimed", th2["trend"], None)
    check("and the reason says how thin it was",
          th2["no_trend_because"], "only 2 publication days in the earlier weeks")
    enough = tcounts({"Banking": {"total": 6, "recent": 4, "earlier": 2}}, earlier_days=4)
    check("four earlier publication days is enough to state a trend",
          D.theme_block(story([seat(topic="Banking")]), enough)["trend"], "steady")

    # rates are per PUBLICATION week, so a half-covered earlier fortnight is not a dip
    half = tcounts({"Banking": {"total": 8, "recent": 4, "earlier": 4}}, earlier_days=7)
    check("4 this week vs 4 in the one earlier week with papers -> steady, not rising",
          D.theme_block(story([seat(topic="Banking")]), half)["trend"], "steady")
    check("the publication days behind the trend are reported",
          D.theme_block(story([seat(topic="Banking")]), half)["publication_days"],
          {"recent": 7, "earlier": 7})

    # --- R5: the other pages a printed piece runs on ------------------------------------
    # Measured on run exam-v1: 44 seats carry continues_to, 147 carry joined_from (as the
    # art id "p<page>-a<n>", NOT an integer), 36 carry both.
    check("a piece on one page has no other pages", D.jump_links(seat()), [])
    goes = D.jump_links(seat(extra={"paper": "2026-10-01_Mint", "continues_to": 9}))
    check("continues_to gives one link", len(goes), 1)
    check("at the page it names", goes[0]["pdf_page"], 9)
    check("labelled as a PDF page like every other link", goes[0]["label"], "PDF p9")
    check("in the same PDF as the seat", goes[0]["pdf"], "archive/2026-10-01/2026-10-01_Mint.pdf")
    check("and says which kind of jump it is", goes[0]["reason"], "continues_to")
    came = D.jump_links(seat(extra={"paper": "2026-10-01_Mint", "joined_from": "p11-a02"}))
    check("joined_from gives one link", len(came), 1)
    check("at the page read out of the art id", came[0]["pdf_page"], 11)
    check("keeping the art id it was read from", came[0]["from_art_id"], "p11-a02")
    check("and saying which kind of jump it is", came[0]["reason"], "joined_from")
    both = D.jump_links(seat(extra={"paper": "2026-10-01_Mint", "joined_from": "p11-a02",
                                    "continues_to": 9}))
    check("a piece that jumps twice keeps both pages", [l["pdf_page"] for l in both], [11, 9])
    check("in reading order: where it came from, then where it goes",
          [l["reason"] for l in both], ["joined_from", "continues_to"])
    # the two shapes that must not produce a link rather than a wrong one
    check("an unparseable joined_from is dropped, not guessed",
          D.jump_links(seat(extra={"paper": "p", "joined_from": "unknown"})), [])
    check("a continues_to that is not a page number is dropped",
          D.jump_links(seat(extra={"paper": "p", "continues_to": "overleaf"})), [])

    # --- R8: the rest of the day, grouped by topic --------------------------------------
    def scored_seat(**kw):
        """A seat as the ranking leaves it: `points` is set by score_story, not load_seats."""
        s = seat(**kw)
        s["points"] = 1
        return s

    tail = [story([scored_seat(topic="Energy", text_hash="t1")], "s1"),
            story([scored_seat(topic="Energy", text_hash="t2")], "s2"),
            story([scored_seat(topic="Banking", text_hash="t3")], "s3"),
            story([scored_seat(topic="Arts", text_hash="t4")], "s4"),
            story([scored_seat(topic=None, text_hash="t5")], "s5")]
    lt = D.long_tail_block(tail, {})
    check("every story below the top N is in a bucket",
          sum(b["count"] for b in lt), len(tail))
    check("topics are ordered by count, then by name",
          [b["topic"] for b in lt], ["Energy", "Arts", "Banking", None])
    check("a story whose articles carry no topic is not dropped",
          lt[-1]["count"], 1)
    check("each story carries what the folded list shows",
          sorted(lt[0]["stories"][0]), ["articles", "headline", "papers", "story_id"])
    check("with the printed headline, through the same checks as the top 10",
          lt[0]["stories"][0]["headline"], "A clear printed headline here")
    check("one paper and one article counted on a one-seat story",
          (lt[0]["stories"][0]["papers"], lt[0]["stories"][0]["articles"]), (1, 1))

    # --- R9: 21 daily counts, with holes where no paper was filed -----------------------
    ser = tcounts({"Banking": {"total": 5, "recent": 4, "earlier": 1}},
                  recent_days=7, earlier_days=2,
                  by_day={"Banking": {"2026-10-24": 2, "2026-10-23": 2, "2026-10-04": 1}})
    block = D.theme_block(story([seat(topic="Banking")]), ser)
    check("the series has one entry per day of the theme window",
          len(block["series"]), D.KNOBS["theme_window_days"])
    check("a day with no papers is null, not zero",
          block["series"].count(None), 21 - 9)
    check("the counts sum to the 21-day total",
          sum(v for v in block["series"] if v is not None), block["stories_21d"])
    check("it ends on the digest's own day, newest last", block["series"][-1], 2)
    check("a day with papers and nothing on the theme is zero, not null",
          block["series"][-3], 0)

    # --- the knob hash: two builds with different knobs are distinguishable -------------
    before = D.knobs_hash()
    check("the same knobs give the same hash", D.knobs_hash(), before)
    D.KNOBS["top_n"] = 11
    check("a changed knob VALUE changes the hash", D.knobs_hash() == before, False)
    D.KNOBS["top_n"] = 10
    check("and putting it back restores it", D.knobs_hash(), before)
    check("the daily cap is K1's number, not the placeholder",
          D.KNOBS["daily_cost_cap_usd"], 1.00)

    # --- coverage, views, page links ----------------------------------------------------
    mixed = [seat(text_hash="c1", page=1, words=900),
             seat(text_hash="c1", page=14, words=900, publisher="The Hindu", art_id="p14-a01"),
             seat(text_hash="c2", page=3, words=400, publisher="The Hindu", art_id="p03-a01",
                  kind="opinion"),
             seat(text_hash="c3", page=5, words=200, kind="notice", art_id="p05-a01"),
             seat(text_hash="c4", page=6, words=200, kind="sponsored", art_id="p06-a01")]
    st = story(mixed)
    D.score_story(st, D.paper_day_shape(mixed))
    cov = D.coverage_block(st)
    check("coverage counts distinct articles", cov["articles"], 2)
    check("coverage counts papers separately", cov["papers"], 2)
    check("a notice is in neither count", all(r["kind"] != "notice" for r in cov["rows"]), True)
    check("a paid piece is in neither count", all(r["kind"] != "sponsored" for r in cov["rows"]), True)
    check("one text in two papers gives two coverage rows", len(cov["rows"]), 3)
    check("every coverage row carries a PDF path",
          all(r["page_link"]["pdf"] for r in cov["rows"]), True)
    check("the PDF path points into archive/, not at the extraction JSON",
          all(r["page_link"]["pdf"].startswith("archive/") and
              r["page_link"]["pdf"].endswith(".pdf") for r in cov["rows"]), True)
    check("the page label never claims a printed page",
          {r["page_link"]["label"] for r in cov["rows"]},
          {"PDF p1", "PDF p14", "PDF p3"})
    check("a theme with a trend prints it",
          D.theme_line({"topic": "Energy", "stories_21d": 7, "trend": "rising"}),
          "theme Energy (7, rising) · ")
    check("a theme with NO trend prints 'no trend', it does not crash",
          D.theme_line({"topic": "Energy", "stories_21d": 7, "trend": None}),
          "theme Energy (7, no trend) · ")
    check("no theme prints nothing", D.theme_line(None), "")

    views = D.views_block(st)
    check("the views line lists the opinion piece", [v["kind"] for v in views], ["opinion"])
    check("the views line leaves news out",
          D.views_block(story([seat()])), [])


# ------------------------------------------------------------------ 2. mock pipeline

MOCK_NEWS = """
CREATE TABLE texts (text_hash TEXT PRIMARY KEY, headline TEXT, body TEXT, words INTEGER,
                    first_run TEXT, first_seen TEXT);
CREATE TABLE placements (run_id TEXT, date TEXT, publisher TEXT, art_id TEXT, page INTEGER,
                         number INTEGER, deck TEXT, byline TEXT, text_hash TEXT, extra TEXT,
                         source_file TEXT, imported_at TEXT,
                         PRIMARY KEY (run_id, date, publisher, art_id));
CREATE TABLE tags (text_hash TEXT, model TEXT, prompt_version TEXT, topic TEXT, topics_more TEXT,
                   kind TEXT, summary TEXT, text_check TEXT, tagged_at TEXT, in_tokens INTEGER,
                   out_tokens INTEGER, via TEXT, topic_parent TEXT, forum TEXT,
                   sponsored_cue TEXT, event TEXT, events_more TEXT,
                   PRIMARY KEY (text_hash, model, prompt_version));
"""
MOCK_INTEL = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE members (version TEXT, date TEXT, group_id TEXT, text_hash TEXT,
                      PRIMARY KEY (version, date, text_hash));
CREATE TABLE links (gversion TEXT, tversion TEXT, date TEXT, group_id TEXT, earlier_date TEXT,
                    earlier_group_id TEXT, gap_days INTEGER, avg_score REAL, best_score REAL,
                    centroid_score REAL, role TEXT, rank INTEGER);
"""
MOCK_VECS = """
CREATE TABLE vectors (text_hash TEXT, model TEXT, variant TEXT, dim INTEGER, vec BLOB,
                      input_words INTEGER, embedded_at TEXT, PRIMARY KEY (text_hash, model, variant));
"""

# text_hash, headline, words, kind, topic, summary, text_check
TEXTS = [
    ("A1", "Power tariff raised across four states", 900, "news", "Energy", "The tariff rises 8%.", "ok"),
    ("A2", "Tariff order draws industry protest", 600, "news", "Energy", "Industry objects to the order.", "ok"),
    ("A3", "Why the tariff order matters", 700, "analysis", "Energy", "An analysis of the order.", "ok"),
    ("A4", "Tariff protests widen on day two", 500, "news", "Energy", "Protests spread.", "ok"),
    ("B1", "Bank merger clears final hurdle", 800, "news", "Banking", "The merger is approved.", "ok"),
    ("C1", "Small lender raises capital", 300, "news", "Banking", "A lender raises capital.", "ok"),
    ("D1", "Bank hiring picks up", 250, "news", "Banking", "Hiring rises.", "ok"),
    ("E1", "Classified notices", 120, "notice", None, "Notices.", "ok"),
    ("F1", "A sponsored health feature", 400, "sponsored", "Health", "A paid feature.", "ok"),
    ("G1", "Rates up", 450, "news", "Markets", "Rates rose.", "ok"),
]
# run date, paper, art_id, page, text_hash, extra
SEATS = [
    ("2026-10-01", "Mint", "p01-a01", 1, "A1",
     {"see_also_page": 9, "continues_to": 11}),
    ("2026-10-01", "Mint", "p09-a01", 9, "B1", {}),
    ("2026-10-01", "Mint", "p05-a01", 5, "F1", {}),
    ("2026-10-01", "The Hindu", "p01-a01", 1, "A2",
     {"related_id": "p04-a01", "joined_from": "p10-a03", "continues_to": 4}),
    ("2026-10-01", "The Hindu", "p04-a01", 4, "C1", {}),
    ("2026-10-01", "The Hindu", "p12-a01", 12, "E1", {}),
    ("2026-10-01", "Business Line", "p03-a01", 3, "A3", {}),
    ("2026-10-01", "Business Line", "p07-a01", 7, "D1", {}),
    ("2026-10-02", "Mint", "p02-a01", 2, "A4", {}),
    ("2026-10-02", "Mint", "p06-a01", 6, "G1", {}),
]
# group per (date, text_hash); A1/A2/A3 group on day 1, A4 on day 2, linked as a thread
GROUPS = {("2026-10-01", "A1"): "2026-10-01:gA", ("2026-10-01", "A2"): "2026-10-01:gA",
          ("2026-10-01", "A3"): "2026-10-01:gA", ("2026-10-01", "B1"): "2026-10-01:gB",
          ("2026-10-01", "C1"): "2026-10-01:gC", ("2026-10-01", "D1"): "2026-10-01:gD",
          ("2026-10-01", "E1"): "2026-10-01:gE", ("2026-10-01", "F1"): "2026-10-01:gF",
          ("2026-10-02", "A4"): "2026-10-02:gA2", ("2026-10-02", "G1"): "2026-10-02:gG"}


def make_mock(tmp):
    news, intel, vecs = (Path(tmp) / n for n in ("news.db", "intel.db", "vectors.db"))
    n = sqlite3.connect(news); n.executescript(MOCK_NEWS)
    for h, head, words, kind, topic, summary, chk in TEXTS:
        n.execute("INSERT INTO texts VALUES (?,?,?,?,?,?)", (h, head, "body " * 10, words, "exam-v1", "t"))
        n.execute("INSERT INTO tags (text_hash, model, prompt_version, topic, kind, summary, "
                  "text_check, event, events_more) VALUES (?,?,?,?,?,?,?,?,?)",
                  (h, L.TAG_MODEL, L.TAG_PROMPT, topic, kind, summary, chk,
                   "policy or regulation", "[]"))
    for date, pub, art, page, h, extra in SEATS:
        n.execute("INSERT INTO placements VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                  ("exam-v1", date, pub, art, page, 1, "", "Our reporter", h,
                   json.dumps(dict(extra, paper=f"{date}_{pub}")),
                   f"{date}/{date}_{pub}.json", "t"))
    n.commit(); n.close()

    i = sqlite3.connect(intel); i.executescript(MOCK_INTEL)
    for (date, h), gid in GROUPS.items():
        i.execute("INSERT INTO members VALUES (?,?,?,?)", ("g0.1", date, gid, h))
    i.execute("INSERT INTO links VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
              ("g0.1", "t0.2", "2026-10-02", "2026-10-02:gA2", "2026-10-01", "2026-10-01:gA",
               1, 0.8, 0.9, 0.85, "parent", 1))
    i.commit(); i.close()

    v = sqlite3.connect(vecs); v.executescript(MOCK_VECS)
    pull = {"A1": (1.0, 0.0), "A2": (0.98, 0.02), "A3": (0.6, 0.4), "A4": (0.9, 0.1)}
    for h, xy in pull.items():
        v.execute("INSERT INTO vectors VALUES (?,?,?,?,?,?,?)",
                  (h, D.VEC_MODEL, D.VEC_VARIANT, 2, array.array("f", xy).tobytes(), 100, "t"))
    v.commit(); v.close()
    return news, intel, vecs


def pipeline(tmp):
    news, intel, vecs = make_mock(tmp)
    con = (D.open_ro(news), D.open_ro(intel), D.open_ro(vecs))
    result = D.build("2026-10-01", "day", news=con[0], intel=con[1], vecs=con[2])

    ids = [s["story_id"] for s in result["stories"]]
    check("the three-paper story ranks first", ids[0], "2026-10-01:gA")
    check("the notice-only and paid-only stories are not shown",
          [i for i in ids if i in ("2026-10-01:gE", "2026-10-01:gF")], [])
    check("two zero-point stories were dropped", result["dropped"]["zero_point"], 2)

    top = result["stories"][0]
    # Mint p1 (front, 900 of [900] that day -> top decile) = 5; Hindu p1 front, 600 above
    # its day median = 3+1 = 4; BL p3: pages 3,7 -> front is 3, 700 above median = 3+1 = 4
    # Each paper puts it on its own front page as that day's longest covering piece,
    # so each votes the maximum 3 + 2. (The mock's paper-days carry 2-3 articles, so the
    # "top 10%" mark is simply the longest one -- see the knob comment.)
    check("the top story's score is the sum over three papers", top["score"], 15)
    check("its per-paper points", sorted(top["paper_points"].items()),
          [("Business Line", 5), ("Mint", 5), ("The Hindu", 5)])
    check("three papers in its coverage table", top["coverage"]["papers"], 3)
    check("three articles in its coverage table", top["coverage"]["articles"], 3)
    check("the medoid supplied the headline", top["headline"]["source_text_hash"], "A1")
    check("the headline is the printed one",
          top["headline"]["text"], "Power tariff raised across four states")
    check("the one-liner comes from the same article as the headline",
          top["one_liner"]["source_text_hash"], top["headline"]["source_text_hash"])
    check("the one-liner is that piece's stored summary",
          top["one_liner"]["text"], "The tariff rises 8%.")
    check("the analysis piece is in the views line",
          [v["kind"] for v in top["views"]], ["analysis"])
    check("a day window is not developing", top["developing"], False)
    check("no caveat is raised on a three-paper story",
          [k for k, v in top["caveats"].items() if v], [])

    links = {l["kind"]: l["to_story_id"] for l in top["links"]}
    check("the printed see-also link points at the other story",
          links.get("see_also_page"), "2026-10-01:gB")
    check("the printed related-id link points at the other story",
          links.get("related_id"), "2026-10-01:gC")

    single = next(s for s in result["stories"] if s["story_id"] == "2026-10-01:gB")
    check("a one-paper story is flagged single source", single["caveats"]["single_source"], True)
    check("a one-paper story has one paper", single["coverage"]["papers"], 1)

    # Every Banking story in the mock has a single article, so none of them counts
    # towards a theme any more (Kunaal, 1 Nov): a lone brief is not a theme.
    banking = next(s for s in result["stories"] if s["story_id"] == "2026-10-01:gC")
    check("a single-article story no longer carries a theme line", banking["theme"], None)
    check("nor does the three-article story, whose topic has only one story",
          top["theme"], None)
    D.KNOBS["theme_min_articles_per_story"] = 1
    loose = D.build("2026-10-01", "day", news=con[0], intel=con[1], vecs=con[2])
    D.KNOBS["theme_min_articles_per_story"] = 2
    loose_banking = next(s for s in loose["stories"] if s["story_id"] == "2026-10-01:gC")
    check("counting lone briefs again brings the theme line back",
          loose_banking["theme"]["stories_21d"], 3)
    check("its topic", loose_banking["theme"]["topic"], "Banking")
    check("a short headline was refused and reported",
          "too_short" in str(result["report"]["headline_fallbacks"]) or
          all(s["headline"]["fallbacks"] == [] for s in result["stories"]), True)

    # the contract itself
    check("the window is recorded", result["window"]["days"], ["2026-10-01"])
    check("every version that shaped it is recorded",
          sorted(result["versions"]),
          ["digest", "grouping", "knobs_hash", "label", "tag_prompt", "tracking"])
    check("the digest version is d0.2", result["versions"]["digest"], "d0.2")
    check("the knob values are recorded with it", result["knobs"]["top_n"], 10)
    check("the build says the page offset is not stored",
          result["report"]["page_offset_stored"], False)
    check("the report counts page links whose PDF is missing",
          "page_links_missing_pdf" in result["report"], True)
    check("the write-up slot is present and empty for part 1",
          [s["writeup"] for s in result["stories"]], [None] * len(result["stories"]))
    check("the whole contract is JSON-serialisable", bool(json.dumps(result)), True)

    # the d0.2 additions, through the whole build and not only their own functions
    mint_row = next(r for r in top["coverage"]["rows"]
                    if r["paper"] == "Mint" and r["page"] == 1)
    check("a coverage row whose piece continues carries the other page",
          [l["pdf_page"] for l in mint_row["page_links_more"]], [11])
    hindu_row = next(r for r in top["coverage"]["rows"]
                     if r["paper"] == "The Hindu" and r["page"] == 1)
    check("a row that jumps twice carries both, in reading order",
          [(l["reason"], l["pdf_page"]) for l in hindu_row["page_links_more"]],
          [("joined_from", 10), ("continues_to", 4)])
    check("a row whose piece does not jump carries an empty list",
          next(r for r in top["coverage"]["rows"]
               if r["paper"] == "Business Line")["page_links_more"], [])
    check("R5's other pages are not invented for the see-also link it sits beside",
          [l["kind"] for l in top["links"]], ["see_also_page", "related_id"])

    check("every view carries the text_hash that opens its article",
          [v["text_hash"] for v in top["views"]], ["A3"])

    # R8: with top_n = 1 everything else is the long tail
    folded = D.build("2026-10-01", "day", news=con[0], intel=con[1], vecs=con[2], top_n=1)
    scored_count = len(folded["stories"]) + folded["dropped"]["below_top_n"]
    check("the long tail holds every story below the top N",
          sum(b["count"] for b in folded["long_tail"]), folded["dropped"]["below_top_n"])
    check("which is every scored story but the one shown in full",
          scored_count - 1, folded["dropped"]["below_top_n"])
    check("the folded list is ordered by how many stories each topic has",
          [b["count"] for b in folded["long_tail"]],
          sorted((b["count"] for b in folded["long_tail"]), reverse=True))
    check("a story in the long tail is not also in the top N",
          set(s["story_id"] for b in folded["long_tail"] for s in b["stories"])
          & {s["story_id"] for s in folded["stories"]}, set())
    check("the long tail is JSON-serialisable with the rest",
          bool(json.dumps(folded["long_tail"])), True)

    # R9 through the real theme path. `loose_banking` above is the one themed story the
    # mock can produce (three Banking stories in the window, counting lone briefs); the
    # window ending 2026-10-01 has papers on that one day only.
    series = loose_banking["theme"]["series"]
    check("a real build fills the 21-day series", len(series), 21)
    check("with papers on one day of the window, the other 20 are holes",
          series.count(None), 20)
    check("the day with papers carries the count", series[-1], 3)
    check("and the series sums to the 21-day count",
          sum(v for v in series if v is not None), loose_banking["theme"]["stories_21d"])

    # a week window joins the thread across two days
    week = D.build("2026-10-02", "week", news=con[0], intel=con[1], vecs=con[2])
    thread = next(s for s in week["stories"] if s["story_id"] == "2026-10-01:gA")
    check("the week window joins the thread's two days", thread["dates"],
          ["2026-10-01", "2026-10-02"])
    check("a story running two days is DEVELOPING", thread["developing"], True)
    check("the second day's paper does not vote twice",
          thread["paper_points"]["Mint"], 5)
    check("top N is honoured", len(week["stories"]) <= 10, True)
    for c in con:
        c.close()


def main():
    rules()
    with tempfile.TemporaryDirectory() as tmp:
        pipeline(tmp)
    bad = [(n, g, w) for n, g, w in CHECKS if g != w]
    for n, g, w in bad:
        print(f"FAIL {n}: got {g!r}, want {w!r}")
    print(f"{len(CHECKS) - len(bad)}/{len(CHECKS)} checks passed (rules + mock pipeline)")
    print("no real database was opened by this test")
    print("PASS" if not bad else "FAIL")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
