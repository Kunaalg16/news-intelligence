"""
test_p3_search.py -- TEST (free, no API key, no internet, no model). Checks src/p3_search.py.

WHAT IT DOES
  Builds a SYNTHETIC archive in a temporary directory -- its own `news.db`, `intel.db`
  and `search.db`, six texts, three papers, two days, a two-day thread, a notice and a
  sponsored piece -- and checks the seven rules of brief 2 §3 B2-4 against data whose
  right answer is known by construction. Then it asks the REAL archive a handful of
  questions read-only, because a rule that holds on six texts and fails on 6,655 has
  not been tested.
  Four parts:
    1. pieces   tokens, the FTS expression, RRF, snippets, the cosine
    2. mock     the seven rules, the filters, both error types, the fallback, facets
    3. real     facets against the current tag record, and three known questions
    4. safety   nothing was written anywhere

WHY THE SYNTHETIC ARCHIVE
  On the real archive almost nothing is decidable: "did the filter work?" needs a known
  population, and "is this the best match?" needs a known answer. The fixture's vectors
  are four-dimensional and deliberately NOT unit length (0.996-1.004, the spread the
  real `passage_vectors` have), so the cosine division is exercised rather than assumed.

PASS LINES (fixed before the run, brief 2 §3 B2-4)
  - Empty query is a browse: `verdict` None, `sort` "newest", really newest-first.
  - Every article in a story row is one of that story's own articles (K1 [behaviour]).
  - One text printed in several seats appears ONCE.
  - Filters are applied before ranking, and each one narrows: papers, kinds, topics,
    date_from/date_to, exclude_story_ids, story_ids, text_hashes.
  - Ads out, paid kept, notices searchable (p3_label's rules, not a second copy).
  - Short words are searched; a query of only short words is not a browse; a 1-2 letter
    word matches whole words only; the user's own quotes cannot become FTS syntax.
  - A missing id raises LookupError; a bad argument raises ValueError.
  - With the embedding service down, `search()` still answers and says `mode fts_only`.
  - `facets()` offers only values with stories behind them, under the CURRENT tag record.
  A failed line is answered by error analysis, not by moving the line.

WHEN TO RUN
  After every change to src/p3_search.py. About two seconds plus the real-archive part.

USAGE (from ~/news-intel)
  .venv/bin/python tests/test_p3_search.py            mock + the real archive
  .venv/bin/python tests/test_p3_search.py --mock      synthetic archive only
"""

import json
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import p3_search as S                                              # noqa: E402
import p3_label as L                                               # noqa: E402

CHECKS = []


def check(name, got, want):
    CHECKS.append((name, got, want))


def raises(fn, kind):
    try:
        fn()
    except kind:
        return True
    except Exception as e:                                    # noqa: BLE001
        return f"{type(e).__name__}: {e}"
    return False


# ================================================================= the fixture

DATE1, DATE2 = "2026-10-01", "2026-10-02"
RUN = "exam-v1"

# text_hash, headline, body, kind, topic, check
TEXTS = [
    ("h_repo", "Reserve Bank raises the repo rate",
     "The Reserve Bank raised the repo rate by 25 basis points on Wednesday. "
     "Borrowers on floating rate loans will pay more from next month. "
     "The decision was taken by a majority of the monetary policy committee.",
     "news", "Economy & Policy", "ok"),
    ("h_view", "Why the repo decision matters",
     "An editorial argues the repo decision was overdue and will slow inflation.",
     "opinion", "Economy & Policy", "ok"),
    ("h_banks", "Banks raise lending rates after the repo decision",
     "Three large banks raised lending rates on Thursday, a day after the repo move.",
     "news", "Economy & Policy", "ok"),
    ("h_rain", "Monsoon withdraws from Kerala",
     "The monsoon withdrew from Kerala on Wednesday, the weather office said.",
     "news", "Weather", "ok"),
    ("h_notice", "Public notice: tender for road works",
     "Sealed tenders are invited for road works in the eastern division.",
     "notice", None, "ok"),
    ("h_ad", "Invest with us and grow your savings",
     "A sponsored feature about a savings product from a mutual fund house.",
     "sponsored", "Companies & Business", "ok"),
]

# text_hash, date, publisher, art_id, page, number, deck, byline
SEATS = [
    # h_repo is printed TWICE in one paper -- one article, two seats (the roll-up case)
    ("h_repo", DATE1, "Mint", "p1-a01", 1, 1, "Borrowers to pay more", "BY A REPORTER"),
    ("h_repo", DATE1, "Mint", "p9-a04", 9, 4, "", ""),
    ("h_view", DATE1, "The Hindu", "p8-a02", 8, 2, "", ""),
    ("h_rain", DATE1, "Times of India", "p3-a01", 3, 1, "", "New Delhi:"),
    ("h_notice", DATE1, "Mint", "p14-a09", 14, 9, "", ""),
    ("h_ad", DATE1, "Times of India", "p20-a01", 20, 1, "", ""),
    ("h_banks", DATE2, "Mint", "p1-a02", 1, 2, "", ""),
]

# Four-dimensional, and deliberately NOT unit length: the real stored vectors run
# 0.998..1.004, so the cosine must divide rather than assume (measured 3 Nov).
VECTORS = {
    "h_repo": [1.0, 0.0, 0.02, 0.0],
    "h_view": [0.95, 0.0, 0.0, 0.1],
    "h_banks": [0.9, 0.0, 0.1, 0.0],
    "h_rain": [0.0, 1.004, 0.0, 0.0],
    "h_notice": [0.0, 0.0, 0.996, 0.0],
    "h_ad": [0.0, 0.0, 0.0, 1.002],
}
REPO_QUERY_VECTOR = [1.0, 0.0, 0.0, 0.0]        # closest to h_repo, then h_view, h_banks


def build_archive(tmp):
    """A whole little archive: news.db, intel.db, search.db. Returns the paths."""
    news = Path(tmp) / "news.db"
    intel = Path(tmp) / "intel.db"
    search = Path(tmp) / "search.db"

    n = sqlite3.connect(news)
    n.executescript("""
        CREATE TABLE texts (text_hash TEXT PRIMARY KEY, headline TEXT, body TEXT,
                            words INTEGER, first_run TEXT, first_seen TEXT);
        CREATE TABLE placements (run_id TEXT, date TEXT, publisher TEXT, art_id TEXT,
                                 page INTEGER, number INTEGER, deck TEXT, byline TEXT,
                                 text_hash TEXT, extra TEXT, source_file TEXT,
                                 imported_at TEXT,
                                 PRIMARY KEY (run_id, date, publisher, art_id));
        CREATE TABLE tags (text_hash TEXT, model TEXT, prompt_version TEXT, topic TEXT,
                           topics_more TEXT, kind TEXT, summary TEXT, text_check TEXT,
                           tagged_at TEXT, in_tokens INTEGER, out_tokens INTEGER,
                           via TEXT, topic_parent TEXT, forum TEXT, sponsored_cue TEXT,
                           event TEXT, events_more TEXT,
                           PRIMARY KEY (text_hash, model, prompt_version));
    """)
    for h, head, body, kind, topic, chk in TEXTS:
        n.execute("INSERT INTO texts VALUES (?,?,?,?,?,?)",
                  (h, head, body, len(body.split()), RUN, DATE1))
        n.execute("INSERT INTO tags (text_hash, model, prompt_version, topic, kind, "
                  "summary, text_check) VALUES (?,?,?,?,?,?,?)",
                  (h, L.TAG_MODEL, L.TAG_PROMPT, topic, kind, head + ".", chk))
    for h, date, paper, art, page, number, deck, byline in SEATS:
        n.execute("INSERT INTO placements VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                  (RUN, date, paper, art, page, number, deck, byline, h,
                   json.dumps({"paper": f"{date}_{paper}"}), "x.json", date))
    n.commit()
    n.close()

    i = sqlite3.connect(intel)
    i.executescript("""
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE members (version TEXT, date TEXT, group_id TEXT, text_hash TEXT,
                              PRIMARY KEY (version, date, text_hash));
        CREATE TABLE links (gversion TEXT, tversion TEXT, date TEXT, group_id TEXT,
                            earlier_date TEXT, earlier_group_id TEXT, gap_days INTEGER,
                            avg_score REAL, best_score REAL, centroid_score REAL,
                            role TEXT, rank INTEGER);
    """)
    import p3_digest as D
    g = D.GROUPING_VERSION
    for date, gid, h in ((DATE1, "g1", "h_repo"), (DATE1, "g1", "h_view"),
                         (DATE1, "g2", "h_rain"), (DATE2, "g4", "h_banks")):
        i.execute("INSERT INTO members VALUES (?,?,?,?)", (g, date, gid, h))
    # g4 (day two) hangs off g1 (day one): one story over two days, root g1.
    i.execute("INSERT INTO links (gversion, tversion, date, group_id, earlier_date, "
              "earlier_group_id, role, rank) VALUES (?,?,?,?,?,?,?,?)",
              (g, D.TRACKING_VERSION, DATE2, "g4", DATE1, "g1", "parent", 1))
    i.commit()
    i.close()

    s = sqlite3.connect(search)
    s.executescript("""
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE passages (passage_id INTEGER PRIMARY KEY, text_hash TEXT, ord INTEGER,
                               start_word INTEGER, words INTEGER, headline TEXT, text TEXT);
        CREATE VIRTUAL TABLE passages_fts USING fts5(headline, text, content='passages',
                               content_rowid='passage_id', tokenize='porter unicode61');
        CREATE TABLE passage_vectors (passage_id INTEGER PRIMARY KEY, dim INTEGER,
                               vec BLOB, model TEXT, variant TEXT, embedded_at TEXT);
    """)
    import array
    pid = 0
    for h, head, body, kind, topic, chk in TEXTS:
        # h_repo gets two passages, so "an article is shown by its BEST passage" is real
        chunks = [body] if h != "h_repo" else [
            body.split(". ")[0] + ".",
            ". ".join(body.split(". ")[1:])]
        for ordinal, chunk in enumerate(chunks):
            pid += 1
            text = f"{'Mint'} · {DATE1} · {head}\n\n{chunk}"
            s.execute("INSERT INTO passages VALUES (?,?,?,?,?,?,?)",
                      (pid, h, ordinal, 0, len(chunk.split()), head, text))
            s.execute("INSERT INTO passages_fts (rowid, headline, text) VALUES (?,?,?)",
                      (pid, head, text))
            vec = array.array("f", VECTORS[h]).tobytes()
            s.execute("INSERT INTO passage_vectors VALUES (?,?,?,?,?,?)",
                      (pid, 4, vec, "qwen3-0.6b", "P1", DATE1))
    s.commit()
    s.close()
    return news, intel, search


class Archive:
    """Open connections on the synthetic archive, for passing into search()."""

    def __init__(self, tmp):
        self.news_p, self.intel_p, self.search_p = build_archive(tmp)

    def find(self, query, **kw):
        kw.setdefault("embed", lambda q: REPO_QUERY_VECTOR)
        return S.search(query, news=S.open_ro(self.news_p),
                        intel=S.open_ro(self.intel_p),
                        scon=S.open_ro(self.search_p), **kw)

    def facets(self):
        return S.facets(news=S.open_ro(self.news_p), intel=S.open_ro(self.intel_p))

    def corpus(self, filters=None):
        return S.corpus(filters or {}, news=S.open_ro(self.news_p),
                        intel=S.open_ro(self.intel_p))


# ================================================================= 1. the pieces

def pieces():
    check("short words are kept when nothing longer is there",
          S.tokens("AI"), ["ai"])
    # "gst" is three letters and therefore "longer", so "eu" is dropped beside it. That
    # is the contract's rule (v2.2) and the double's own behaviour, and it has a sharp
    # edge worth knowing: a 2-letter word is dropped next to ANY 3-letter one, so
    # "UP election" searches only for "election". Reported, not worked around.
    check("a 2-letter word is dropped beside a 3-letter one",
          S.tokens("EU GST"), ["gst"])
    check("and 'UP election' loses the UP -- the sharp edge, pinned so it cannot "
          "change silently", S.tokens("UP election"), ["election"])
    check("while a query of only 2-letter words keeps them all",
          S.tokens("EU UP"), ["eu", "up"])
    check("short words are dropped when a longer one remains",
          S.tokens("of the police"), ["the", "police"])
    check("a question keeps its content words",
          S.tokens("Why is the EPFO ceiling raised?"),
          ["why", "the", "epfo", "ceiling", "raised"])
    check("punctuation is not a token", S.tokens("repo rate?!"), ["repo", "rate"])
    check("an empty query has no tokens (that is what makes it a browse)",
          S.tokens("   "), [])

    check("every token is quoted and joined with OR",
          S.fts_expression(["repo", "rate"]), '"repo" OR "rate"')
    check("a reader's own quote cannot become FTS syntax",
          S.fts_expression(['he"said']), '"he""said"')
    check("nor can a reader's OR", S.fts_expression(["or"]), '"or"')

    # RRF: rank 1 in both lists beats rank 1 in one, and the arithmetic is the design's
    fused = S.rrf([["a", "b"], ["b", "a"]], k=60)
    check("a passage both indexes rank first scores 1/61 + 1/62",
          round(fused["a"], 8), round(1 / 61 + 1 / 62, 8))
    check("and both of a pair appear", sorted(fused), ["a", "b"])
    one = S.rrf([["a"], []], k=60)
    check("one list only is half the evidence", round(one["a"], 8), round(1 / 61, 8))

    check("the passage header is dropped from a snippet",
          S.strip_passage_header("Mint · 2026-10-01 · A headline\n\nThe body."),
          "The body.")
    check("a passage with no header survives unchanged",
          S.strip_passage_header("Just a body."), "Just a body.")
    check("a short snippet is left whole",
          S.snippet("Mint · d · h\n\nOne short sentence."), "One short sentence.")
    long_body = "Mint · d · h\n\n" + " ".join(["word"] * 30) + ". " + \
                " ".join(["more"] * 30) + "."
    cut = S.snippet(long_body, words=40)
    check("a long snippet stops at a sentence end", cut.endswith("."), True)
    check("and stays near the budget", len(cut.split()) <= 41, True)
    check("a single sentence longer than the budget is cut with an ellipsis",
          S.snippet("h\n\n" + " ".join(["word"] * 80), words=10).endswith("…"), True)


# ================================================================= 2. the mock

def mock(tmp):
    a = Archive(tmp)

    # --- rule 6: an empty query is a browse ------------------------------------
    browse = a.find("")
    check("[rule 6] a browse has no verdict to give", browse["verdict"], None)
    check("[rule 6] a browse is sorted newest", browse["sort"], "newest")
    latest = [max(s["dates"]) for s in browse["stories"]]
    check("[rule 6] and really is in newest-first order",
          latest, sorted(latest, reverse=True))
    check("[rule 6] a browse scores nothing",
          {s["score"] for s in browse["stories"]}, {None})
    check("[rule 6] every story once",
          len({s["story_id"] for s in browse["stories"]}), len(browse["stories"]))
    check("a browse row still shows a snippet (its article's first passage)",
          all(art["snippet"] for s in browse["stories"] for art in s["articles"]), True)

    # The fixture: 4 stories -- the two-day repo thread, the monsoon, the notice and
    # the sponsored piece. The notice is HERE on purpose (notices are searchable).
    check("the corpus is four stories", browse["total_stories"], 4)
    check("and six articles, the twice-printed text counted once",
          browse["total_articles"], 6)

    # --- rule 4: the roll-up ---------------------------------------------------
    repo = [s for s in browse["stories"] if s["story_id"] == "g1"][0]
    check("[rule 4] the thread is one story over two days",
          repo["dates"], [DATE1, DATE2])
    check("[rule 4] a text printed twice is one article",
          [art["text_hash"] for art in repo["articles"]].count("h_repo"), 1)
    hashes = {art["text_hash"] for art in repo["articles"]}
    check("[rule 4] and the story holds exactly its own three texts",
          sorted(hashes), ["h_banks", "h_repo", "h_view"])

    # K1's [behaviour] rule: every article in a row belongs to that story
    world = a.corpus()
    own = {sid: set(row["seat_of_hash"]) for sid, row in world["stories"].items()}
    stray = sorted({art["text_hash"] for s in browse["stories"] for art in s["articles"]
                    if art["text_hash"] not in own[s["story_id"]]})
    check("[K1 behaviour] every article in a story row is one of that story's own",
          stray, [])

    # --- rules 2, 3, 7: a real ranked query ------------------------------------
    hit = a.find("repo rate")
    check("[rule 2/3] the repo story is first", hit["stories"][0]["story_id"], "g1")
    check("[rule 3] and carries a fused score",
          hit["stories"][0]["score"] > 0, True)
    check("[rule 7] a good match is enough or thin, never empty",
          hit["verdict"] in ("enough", "thin"), True)
    check("the matched story's articles are ordered best-first",
          hit["stories"][0]["articles"][0]["text_hash"], "h_repo")
    check("an article row carries the contract's seven keys",
          sorted(hit["stories"][0]["articles"][0]),
          ["date", "headline", "kind", "page_link", "paper", "snippet", "text_hash"])
    check("and its page_link carries the paper (K3's own rows do not)",
          "paper" in hit["stories"][0]["articles"][0]["page_link"], True)
    check("the page link points at the archived PDF",
          hit["stories"][0]["articles"][0]["page_link"]["pdf"],
          f"archive/{DATE1}/{DATE1}_Mint.pdf")

    # the cosine, on vectors that are NOT unit length
    check("the closest vector wins despite non-unit norms",
          [s["story_id"] for s in a.find("repo rate")["stories"]][0], "g1")
    check("similarity travels with the story",
          hit["stories"][0]["similarity"] is not None, True)

    # --- rule 5: sort ----------------------------------------------------------
    newest = a.find("repo rate", sort="newest")
    lat = [max(s["dates"]) for s in newest["stories"]]
    check("[rule 5] newest sort really is newest-first", lat, sorted(lat, reverse=True))
    check("[rule 5] and still reports the sort it used", newest["sort"], "newest")
    check("[rule 5] relevance and newest can disagree -- that is the point",
          [s["story_id"] for s in a.find("monsoon Kerala", sort="relevance")["stories"]][0],
          "g2")

    # --- rule 1: the filters, applied before ranking ---------------------------
    check("[rule 1] papers narrows",
          {art["paper"] for s in a.find("", filters={"papers": ["Mint"]})["stories"]
           for art in s["articles"]}, {"Mint"})
    check("[rule 1] kinds narrows",
          {art["kind"] for s in a.find("", filters={"kinds": ["opinion"]})["stories"]
           for art in s["articles"]}, {"opinion"})
    check("[rule 1] topics reads the story's theme topic",
          [s["story_id"] for s in
           a.find("", filters={"topics": ["Weather"]})["stories"]], ["g2"])
    # The window narrows to day two, where only `h_banks` (group g4) was printed -- but
    # g4 hangs off g1, so the story is still the THREAD ROOT g1. A date filter changes
    # which articles are in the window, never a story's identity; that is what makes a
    # result row the same object as a digest item.
    check("[rule 1] date_from narrows the window, and the story keeps its root id",
          [s["story_id"] for s in a.find("", filters={"date_from": DATE2})["stories"]],
          ["g1"])
    check("[rule 1] and only day two's article is in it",
          sorted({art["text_hash"] for s in
                  a.find("", filters={"date_from": DATE2})["stories"]
                  for art in s["articles"]}), ["h_banks"])
    check("[rule 1] date_to narrows to the earlier day",
          DATE2 not in {d for s in a.find("", filters={"date_to": DATE1})["stories"]
                        for d in s["dates"]}, True)
    check("[rule 1] exclude_story_ids removes exactly that story",
          [s["story_id"] for s in
           a.find("", filters={"exclude_story_ids": ["g1"]})["stories"]],
          [s["story_id"] for s in browse["stories"] if s["story_id"] != "g1"])
    check("[rule 1 · v2.2] story_ids restricts to those stories",
          [s["story_id"] for s in a.find("", filters={"story_ids": ["g2"]})["stories"]],
          ["g2"])
    check("[rule 1 · v2.2] text_hashes restricts to those articles",
          sorted({art["text_hash"] for s in
                  a.find("", filters={"text_hashes": ["h_rain"]})["stories"]
                  for art in s["articles"]}), ["h_rain"])

    # the label rules, taken from p3_label and not copied
    kinds_found = {art["kind"] for s in browse["stories"] for art in s["articles"]}
    check("notices are searchable (the digest leaves them out)",
          "notice" in kinds_found, True)
    check("a paid piece is searchable too, with its caveat kept by p3_label",
          "sponsored" in kinds_found, True)
    check("and p3_label still calls it paid",
          L.label_text({"kind": "sponsored"})["caveats"] != [], True)

    # searched_days
    check("searched_days counts the window, not the matches",
          a.find("repo rate")["searched_days"], 2)
    check("searched_days follows a date filter",
          a.find("", filters={"date_to": DATE1})["searched_days"], 1)
    check("searched_days is NOT changed by exclude_story_ids",
          a.find("", filters={"exclude_story_ids": ["g1"]})["searched_days"], 2)

    # limit and the totals
    small = a.find("", limit=2)
    check("limit caps the stories returned", len(small["stories"]), 2)
    check("and leaves the totals alone", small["total_stories"], 4)
    check("limit 0 returns no stories but still counts them",
          [len(a.find("", limit=0)["stories"]), a.find("", limit=0)["total_stories"]],
          [0, 4])
    check("a story shows at most articles_per_story articles",
          max(len(s["articles"]) for s in browse["stories"])
          <= S.KNOBS["articles_per_story"], True)

    # --- the errors ------------------------------------------------------------
    check("an unknown sort is a ValueError",
          raises(lambda: a.find("x", sort="sideways"), ValueError), True)
    check("a negative limit is a ValueError",
          raises(lambda: a.find("x", limit=-1), ValueError), True)
    check("a limit that is not a number is a ValueError",
          raises(lambda: a.find("x", limit="lots"), ValueError), True)
    check("a malformed date_from is a ValueError",
          raises(lambda: a.find("x", filters={"date_from": "last Tuesday"}),
                 ValueError), True)
    check("a missing database is a LookupError",
          raises(lambda: S.open_ro(Path(tmp) / "nope.db"), LookupError), True)

    # --- the service-down fallback --------------------------------------------
    down = a.find("repo rate", embed=lambda q: None)
    check("with the service down search still answers",
          down["stories"][0]["story_id"], "g1")
    check("and says which mode it used", down.get("mode"), "fts_only")
    check("a both-indexes result does not claim a mode",
          "mode" in a.find("repo rate"), False)
    check("with the service down there is no similarity to report",
          down["stories"][0]["similarity"], None)
    check("but a verdict is still given, from bm25",
          down["verdict"] in ("empty", "thin", "enough"), True)
    check("vector_only is a mode too (the Q17 comparison runs on it)",
          a.find("repo rate", indexes=("vector",)).get("mode"), "vector_only")

    # --- facets ----------------------------------------------------------------
    f = a.facets()
    check("facets offers only the papers in the archive",
          f["papers"], ["Mint", "The Hindu", "Times of India"])
    check("facets offers only the kinds present",
          f["kinds"], ["news", "notice", "opinion", "sponsored"])
    # The sponsored piece is tagged "Companies & Business", and that topic is NOT
    # offered -- a theme topic is counted over COVERING articles (7a) and a sponsored
    # piece is not coverage, so no story's theme is it. That is the property facets
    # exists for: a filter it offers can never come back empty.
    check("facets offers the stories' theme topics, not every tag in the archive",
          f["topics"], ["Economy & Policy", "Weather"])
    check("the sponsored piece's own tag is not offered as a topic",
          "Companies & Business" in f["topics"], False)
    check("though the sponsored article is still searchable",
          "sponsored" in {art["kind"] for s in a.find("")["stories"]
                          for art in s["articles"]}, True)
    check("and the real date range", [f["date_min"], f["date_max"]], [DATE1, DATE2])
    check("every topic facets offers has a story behind it",
          [t for t in f["topics"]
           if not a.find("", filters={"topics": [t]})["total_stories"]], [])
    check("every paper facets offers has a story behind it",
          [p for p in f["papers"]
           if not a.find("", filters={"papers": [p]})["total_stories"]], [])


# ================================================================= 3. the real archive

def real():
    f = S.facets()
    check("real: five papers", len(f["papers"]), 5)
    check("real: 75 topics, the current tag record's own (not 91 across versions)",
          len(f["topics"]), 75)
    check("real: the archive's 23 stored paper-days, end to end",
          [f["date_min"], f["date_max"]], ["2026-09-16", "2026-10-24"])

    # Three questions with known answers, from the frozen Q17 set and its N-list.
    epfo = S.search("why do employers welcome the move of the EPFO wage ceiling "
                    "being raised?", limit=5)
    heads = [s["headline"] for s in epfo["stories"]]
    check("real: the EPFO question finds the EPFO story first",
          "EPFO" in heads[0], True)
    check("real: and calls it enough", epfo["verdict"], "enough")

    none = S.search("Who won the Melbourne Cup?", limit=5)
    check("real: a question the archive cannot answer is empty", none["verdict"], "empty")
    check("real: and it still returns the closest stories to show for it",
          none["total_stories"] > 0, True)

    cancer = S.search("how are cancer drug prices likely to fall due to the capping "
                      "of trade margins?", limit=5)
    check("real: the cancer-drug question finds a capping story first",
          "cap" in cancer["stories"][0]["headline"].lower(), True)

    browse = S.search("", limit=5)
    check("real: a browse is newest-first", browse["sort"], "newest")
    check("real: and counts every article once (6,655 texts in exam-v1)",
          browse["total_articles"], 6655)
    check("real: searched_days is the 23 stored paper-days", browse["searched_days"], 23)


# ================================================================= 4. nothing written

def untouched(before):
    after = {p: (p.stat().st_size, p.stat().st_mtime_ns)
             for p in before}
    check("no file under data/ was written by this test",
          [p.name for p in before if before[p] != after[p]], [])


def main():
    mock_only = "--mock" in sys.argv
    data = ROOT / "data"
    before = {p: (p.stat().st_size, p.stat().st_mtime_ns)
              for p in sorted(data.glob("*.db"))} if data.exists() else {}

    pieces()
    with tempfile.TemporaryDirectory() as tmp:
        mock(tmp)
    if mock_only:
        print("the real archive: SKIPPED (--mock)")
    elif not (data / "search.db").exists():
        print("the real archive: SKIPPED (data/search.db not found)")
    else:
        real()
    if before:
        untouched(before)

    bad = [(n, g, w) for n, g, w in CHECKS if g != w]
    for n, g, w in bad:
        print(f"FAIL {n}: got {g!r}, want {w!r}")
    print(f"{len(CHECKS) - len(bad)}/{len(CHECKS)} checks passed "
          f"({'synthetic archive only' if mock_only else 'synthetic + the real archive'})")
    print("no model was loaded, no API call was made, nothing was written")
    print("PASS" if not bad else "FAIL")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
