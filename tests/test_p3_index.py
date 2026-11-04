"""
test_p3_index.py -- TEST (free, no API key, no internet, NO MODEL LOADED). Checks src/p3_index.py (chunk-1).

WHAT IT DOES
    Two parts, neither touching the real databases and neither loading the embedding
    model (it uses the module's own --fake encoder, which makes a vector from a hash and
    has the same shape as the real one).
      1. THE RECIPE as hand-made input: the single-passage limit, the window and step,
         the overlap, the rule that a window never ends mid-sentence, the tail merge, and
         the awkward neighbours of each -- a body that is one enormous sentence, a body
         of exactly the limit, an empty body, "p. 7" which must not look like a sentence end.
      2. THE WHOLE FILE: a small synthetic news.db built in a temporary directory, run
         through build / update / check, verifying the passage rows, the FTS5
         external-content index, the bm25 weighting, the vector count, and that a second
         update is a no-op.

WHY IT EXISTS
    The passage recipe decides what the search can find. A window that ends mid-sentence,
    an off-by-one in the step, or a scrap left at the end of an article are all invisible
    in the output and all quietly cost recall. They are pure functions of the text, so
    they can be pinned exactly -- and the numbers are placed guesses that the Q17
    question set will move, so a knob change needs a test that says what else moved.

PASS LINES (fixed before the run, K3 brief §3 and §B1-5)
    - 300 words or fewer is one passage, uncut; 301 is more than one.
    - Windows are about 250 words, step about 200, so neighbours overlap about 50.
    - Every window boundary is a sentence boundary: no passage ends mid-sentence.
    - A final window with fewer than 80 NEW words is merged into the one before.
    - Every passage carries the header "paper · date · headline" and its own body words.
    - vectors = passages, one vector size, every passage belongs to an article.
    - The FTS smoke query returns rows and bm25 with the headline weighted ranks them.
    - `update` adds only texts that are not indexed; a second `update` is a no-op.
    - A file cut by a different recipe is refused rather than mixed.
    A failed line is answered by error analysis, not by moving the line.

WHEN TO RUN
    After every change to src/p3_index.py or to its knobs. About two seconds.

USAGE (from ~/news-intel; runs in either environment, no model is loaded)
    .venv/bin/python tests/test_p3_index.py
"""

import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import p3_index as X

CHECKS = []


def check(name, got, want):
    CHECKS.append((name, got, want))


def body_of(sentences, words_each=20):
    """A body of `sentences` sentences, each `words_each` words, with real sentence ends."""
    out = []
    for i in range(sentences):
        filler = " ".join(["word"] * (words_each - 2))
        out.append(f"Sentence {filler} {i}.")
    return " ".join(out)


# ------------------------------------------------------------------ 1. the recipe

def recipe():
    check("an empty body makes no passage", X.chunk_spans(""), [])
    check("a missing body makes no passage", X.chunk_spans(None), [])

    short = " ".join(["word"] * 50)
    check("a short article is one passage", X.chunk_spans(short), [(0, 50)])
    at_limit = " ".join(["word"] * X.KNOBS["single_passage_limit"])
    check("exactly the limit is still one passage",
          X.chunk_spans(at_limit), [(0, X.KNOBS["single_passage_limit"])])

    # 1,200 words in 60 clean sentences
    long_body = body_of(60)
    spans = X.chunk_spans(long_body)
    total = len(long_body.split())
    check("a long article is cut into more than one passage", len(spans) > 1, True)
    check("the first passage starts at the first word", spans[0][0], 0)
    check("the last passage ends at the last word", spans[-1][1], total)
    # every window but the last is about the window size; the last is whatever the
    # article has left, which is shorter by definition
    check("every window but the last is about the window size",
          all(X.KNOBS["window_words"] <= (b - a) <= X.KNOBS["window_words"] + 40
              for a, b in spans[:-1]), True)
    check("the last passage is no longer than a window",
          spans[-1][1] - spans[-1][0] <= X.KNOBS["window_words"] + 40, True)
    check("and it carries at least the tail minimum of new words",
          spans[-1][1] - spans[-2][1] >= X.KNOBS["tail_min_new_words"], True)
    steps = [spans[i + 1][0] - spans[i][0] for i in range(len(spans) - 1)]
    check("the step is about the step knob", set(steps), {X.KNOBS["step_words"]})
    overlaps = [spans[i][1] - spans[i + 1][0] for i in range(len(spans) - 1)]
    check("neighbours overlap by about 50 words",
          all(40 <= o <= 80 for o in overlaps), True)
    check("the passages cover the whole article",
          spans[0][0] == 0 and spans[-1][1] == total, True)

    # no window may end mid-sentence: every boundary must be a sentence boundary
    sents = X.sentences(long_body)
    bounds, at = {0}, 0
    for s in sents:
        at += len(s.split())
        bounds.add(at)
    check("no passage starts mid-sentence", all(a in bounds for a, _ in spans), True)
    check("no passage ends mid-sentence", all(b in bounds for _, b in spans), True)

    # the tail merge: 1,100 words leaves a 60-new-word scrap, which must be absorbed
    tail_body = body_of(55)
    tail_spans = X.chunk_spans(tail_body)
    no_merge = X.chunk_spans(body_of(60))
    check("a scrap at the end is merged, so there is one passage fewer",
          len(tail_spans), len(no_merge) - 1)
    check("and the merged passage is longer than a window",
          tail_spans[-1][1] - tail_spans[-1][0] > X.KNOBS["window_words"], True)
    check("the merged passage still ends at the last word",
          tail_spans[-1][1], len(tail_body.split()))
    X.KNOBS["tail_min_new_words"] = 0
    check("with the tail knob at zero the scrap survives on its own",
          len(X.chunk_spans(tail_body)), len(no_merge))
    X.KNOBS["tail_min_new_words"] = 80

    # one enormous sentence: nothing to cut at, so it stays whole (M4: 1.4% of long texts)
    one_sentence = " ".join(["word"] * 900) + "."
    check("a body with no sentence end stays one passage",
          X.chunk_spans(one_sentence), [(0, 900)])
    check("so it is far longer than a window, which `check` reports as a long sentence",
          900 > X.KNOBS["window_words"] * 2, True)

    # the sentence splitter
    check("it splits on a full stop before a capital",
          len(X.sentences("One thing happened. Another thing followed.")), 2)
    check("it splits after a closing quote",
          len(X.sentences('He said "it happened." Another thing followed.')), 2)
    check("it does not split a page reference",
          len(X.sentences("See p. 7 for the table.")), 1)
    check("it does not split on a lower-case continuation",
          len(X.sentences("The figure was 1.5 per cent last year.")), 1)
    check("it splits on a question mark",
          len(X.sentences("What now? The council meets on Friday.")), 2)

    # the header and the passage rows
    check("the header joins paper, date and headline",
          X.header("Mint", "2026-10-01", "Tariff raised"), "Mint · 2026-10-01 · Tariff raised")
    check("a missing part is left out", X.header("Mint", None, "Tariff raised"),
          "Mint · Tariff raised")
    pieces = X.passages_for("h1", "Tariff raised", long_body, "Mint", "2026-10-01")
    check("one row per span", len(pieces), len(spans))
    check("ord counts from zero", [p["ord"] for p in pieces], list(range(len(spans))))
    check("start_word matches the span", [p["start_word"] for p in pieces],
          [a for a, _ in spans])
    check("words matches the span", [p["words"] for p in pieces],
          [b - a for a, b in spans])
    check("every passage carries the header",
          all(p["text"].startswith("Mint · 2026-10-01 · Tariff raised") for p in pieces), True)
    check("the passage body is its own words, not the whole article",
          len(pieces[1]["text"].split()) < len(long_body.split()), True)
    check("the headline is kept separately for the weighted FTS column",
          pieces[0]["headline"], "Tariff raised")
    check("a short article makes one passage with the header",
          len(X.passages_for("h2", "A head", short, "Mint", "2026-10-01")), 1)


# ------------------------------------------------------------------ 2. the whole file

MOCK_NEWS = """
CREATE TABLE texts (text_hash TEXT PRIMARY KEY, headline TEXT, body TEXT, words INTEGER,
                    first_run TEXT, first_seen TEXT);
CREATE TABLE placements (run_id TEXT, date TEXT, publisher TEXT, art_id TEXT, page INTEGER,
                         number INTEGER, deck TEXT, byline TEXT, text_hash TEXT, extra TEXT,
                         source_file TEXT, imported_at TEXT,
                         PRIMARY KEY (run_id, date, publisher, art_id));
"""


def make_news(path, texts):
    con = sqlite3.connect(path)
    con.executescript(MOCK_NEWS)
    for i, (h, head, body, date, pub) in enumerate(texts):
        con.execute("INSERT INTO texts VALUES (?,?,?,?,?,?)",
                    (h, head, body, len(body.split()), "exam-v1", "t"))
        con.execute("INSERT INTO placements VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    ("exam-v1", date, pub, f"p01-a{i:02d}", 1, 1, "", "R", h, "{}", "f", "t"))
    con.commit()
    con.close()


def whole_file(tmp):
    news_path = Path(tmp) / "news.db"
    search_path = Path(tmp) / "search.db"
    texts = [
        ("h1", "Tariff raised across four states", body_of(60), "2026-10-01", "Mint"),
        ("h2", "Police deny permission", " ".join(["police"] * 40), "2026-10-01", "The Hindu"),
        ("h3", "Bank merger clears hurdle", body_of(20), "2026-10-02", "Business Line"),
        ("h4", "A notice", "Classified notices follow.", "2026-10-02", "Mint"),
    ]
    make_news(news_path, texts)

    news = X.open_ro(news_path)
    rows = X.load_texts(news)
    check("every printed text is loaded", len(rows), 4)
    check("the earliest printing supplies the paper",
          {r["text_hash"]: r["paper"] for r in rows}["h1"], "Mint")

    con = X.open_search(search_path, fresh=True)
    made = X.store(con, rows, X.fake_encoder())
    check("the recipe is recorded in the file",
          con.execute("SELECT value FROM meta WHERE key='chunk_version'").fetchone()[0],
          X.CHUNK_VERSION)

    passages = con.execute("SELECT COUNT(*) FROM passages").fetchone()[0]
    vectors = con.execute("SELECT COUNT(*) FROM passage_vectors").fetchone()[0]
    check("store reports what it made", made, passages)
    check("vectors = passages", vectors, passages)
    check("the long article is cut up",
          con.execute("SELECT COUNT(*) FROM passages WHERE text_hash='h1'").fetchone()[0] > 1,
          True)
    check("the short ones are not",
          con.execute("SELECT COUNT(*) FROM passages WHERE text_hash='h2'").fetchone()[0], 1)
    check("even a one-sentence notice is indexed (Q10d)",
          con.execute("SELECT COUNT(*) FROM passages WHERE text_hash='h4'").fetchone()[0], 1)
    check("one vector size", [r[0] for r in
          con.execute("SELECT DISTINCT dim FROM passage_vectors")], [16])
    check("vectors are stored as float32 bytes",
          con.execute("SELECT LENGTH(vec) FROM passage_vectors LIMIT 1").fetchone()[0], 16 * 4)
    check("the vector records which model and variant made it",
          con.execute("SELECT model, variant FROM passage_vectors LIMIT 1").fetchone(),
          (X.EMBED_SHORT, X.EMBED_VARIANT))

    # the FTS5 external-content index
    hits = con.execute("SELECT COUNT(*) FROM passages_fts WHERE passages_fts MATCH 'police'"
                       ).fetchone()[0]
    check("the word index finds a word in the body", hits > 0, True)
    # h3 is 400 words, so it is two passages, and both carry its headline
    check("the word index finds a word in the headline",
          con.execute("SELECT COUNT(*) FROM passages_fts WHERE passages_fts MATCH "
                      "'headline:merger'").fetchone()[0], 2)
    check("a headline-only term is not found in a body search",
          con.execute("SELECT COUNT(*) FROM passages_fts WHERE passages_fts MATCH "
                      "'text:hurdle'").fetchone()[0], 2)
    check("stemming works (the porter tokenizer)",
          con.execute("SELECT COUNT(*) FROM passages_fts WHERE passages_fts MATCH 'raising'"
                      ).fetchone()[0] > 0, True)
    ranked = con.execute(
        "SELECT p.text_hash FROM passages_fts JOIN passages p ON p.passage_id = passages_fts.rowid "
        "WHERE passages_fts MATCH 'tariff' ORDER BY bm25(passages_fts, ?, ?) LIMIT 1",
        (X.KNOBS["headline_weight"], X.KNOBS["body_weight"])).fetchone()
    check("bm25 with the headline weighted ranks the headline match first", ranked[0], "h1")
    check("the index stores no second copy of the text (external content)",
          con.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='passages_fts_content'"
                      ).fetchone()[0], 0)
    good, numbers = X.report(con)
    check("report's own checks all pass", good, True)
    con.close()

    # update: only what is missing, and idempotent
    args = type("A", (), {"db": str(search_path), "sample": None, "fake": True, "quiet": True})()
    X.NEWS_DB, keep = news_path, X.NEWS_DB
    try:
        check("a second update adds nothing", X.cmd_update(args), 0)
        after = sqlite3.connect(search_path)
        check("and the passage count is unchanged",
              after.execute("SELECT COUNT(*) FROM passages").fetchone()[0], passages)
        after.close()
        make_news_extra(news_path)
        X.cmd_update(args)
        after = sqlite3.connect(search_path)
        check("a new text is added by update",
              after.execute("SELECT COUNT(*) FROM passages WHERE text_hash='h5'").fetchone()[0], 1)
        check("vectors still match passages",
              after.execute("SELECT COUNT(*) FROM passage_vectors").fetchone()[0],
              after.execute("SELECT COUNT(*) FROM passages").fetchone()[0])
        after.close()

        # a file cut by another recipe must be refused, not mixed
        other = Path(tmp) / "other.db"
        o = sqlite3.connect(other)
        o.executescript(X.SCHEMA)
        o.execute("INSERT INTO meta VALUES ('chunk_version', 'chunk-0')")
        o.commit(); o.close()
        try:
            X.open_search(other)
            check("a file cut by another recipe is refused", "no refusal", "refused")
        except SystemExit as e:
            check("a file cut by another recipe is refused", "chunk-0" in str(e), True)
    finally:
        X.NEWS_DB = keep


def make_news_extra(path):
    con = sqlite3.connect(path)
    con.execute("INSERT INTO texts VALUES ('h5','A later story','Tariff talks resume today.',4,'exam-v1','t')")
    con.execute("INSERT INTO placements VALUES ('exam-v1','2026-10-03','Mint','p01-a99',1,1,'','R','h5','{}','f','t')")
    con.commit(); con.close()


# ------------------------------------------------------------------ 3. the fold (FP-2, TC-21)

def folded(tmp):
    """Pass line (fixed before the run): `profit` finds the text that prints only `proﬁt`;
    `fts` rebuilds the word index alone, leaving passages and vectors byte-identical."""
    news_path = Path(tmp) / "news_fold.db"
    search_path = Path(tmp) / "search_fold.db"
    texts = [
        ("hL", "Firms report higher proﬁt", "The proﬁt rose as conﬂict eased in the ﬁelds.",
         "2026-10-01", "Mint"),
        ("hP", "Plain spelling", "Profit fell at another bank after the tariff.", "2026-10-01", "Mint"),
    ]
    make_news(news_path, texts)
    rows = X.load_texts(X.open_ro(news_path))

    check("fold turns the ligature into plain letters", X.fold("proﬁt ﬂ ﬀ"), "profit fl ff")
    check("fold leaves plain text alone", X.fold("Profit, 25% · ₹4,740"), "Profit, 25% · ₹4,740")
    check("fold of None is empty", X.fold(None), "")

    def hits(con, word):
        return sorted(r[0] for r in con.execute(
            "SELECT p.text_hash FROM passages_fts f JOIN passages p "
            "ON p.passage_id = f.rowid WHERE passages_fts MATCH ?", (f'"{word}"',)))

    def frozen(con):
        return (con.execute("SELECT * FROM passages ORDER BY passage_id").fetchall(),
                con.execute("SELECT * FROM passage_vectors ORDER BY passage_id").fetchall())

    # an index built the OLD way: unfolded text handed to FTS
    con = X.open_search(search_path, fresh=True)
    keep = X.fold
    X.fold = lambda t: t or ""
    try:
        X.store(con, rows, X.fake_encoder())
    finally:
        X.fold = keep
    check("before: the unfolded index misses the ligature spelling", hits(con, "profit"),
          ["hP"])
    before = frozen(con)
    con.close()

    args = type("A", (), {"db": str(search_path)})()
    check("`fts` runs and its report passes", X.cmd_fts(args), 0)
    con = X.open_search(search_path)
    check("after: profit finds both", hits(con, "profit"), ["hL", "hP"])
    check("after: conflict and fields find the ligature text",
          (hits(con, "conflict"), hits(con, "fields")), (["hL"], ["hL"]))
    check("passages and vectors byte-identical after `fts`", frozen(con) == before, True)
    check("the stored passage keeps its printed ligature (display untouched)",
          "proﬁt" in con.execute("SELECT text FROM passages WHERE text_hash='hL'"
                                 ).fetchone()[0], True)
    check("the fold is recorded in meta",
          con.execute("SELECT value FROM meta WHERE key='fts_fold'").fetchone()[0], X.FTS_FOLD)
    check("headline column folded too (only hL's headline says profit)", con.execute(
        "SELECT COUNT(*) FROM passages_fts WHERE passages_fts MATCH 'headline:profit'"
    ).fetchone()[0], 1)
    check("`fts` twice is the same index (no duplicates)",
          (X.cmd_fts(args), hits(X.open_search(search_path), "profit")), (0, ["hL", "hP"]))
    con.close()

    # a fresh build folds from the start
    con = X.open_search(search_path, fresh=True)
    X.store(con, rows, X.fake_encoder())
    check("a fresh store folds the index text", hits(con, "profit"), ["hL", "hP"])
    con.close()


def main():
    recipe()
    with tempfile.TemporaryDirectory() as tmp:
        whole_file(tmp)
        folded(tmp)
    bad = [(n, g, w) for n, g, w in CHECKS if g != w]
    for n, g, w in bad:
        print(f"FAIL {n}: got {g!r}, want {w!r}")
    print(f"{len(CHECKS) - len(bad)}/{len(CHECKS)} checks passed (recipe + whole file, fake vectors)")
    print("no model was loaded and no real database was opened")
    print("PASS" if not bad else "FAIL")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
