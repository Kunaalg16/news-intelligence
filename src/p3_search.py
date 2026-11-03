"""p3_search.py — Phase 3 · K3 · finding things in the archive (contract §6).
  v0.1 3 Nov 2026 (K3 B2-4b): `search()` and `facets()`. `s0.1`.

WHAT IT DOES
  Two functions the app calls through `p3_reader`'s import:
      search(query, filters, sort, limit)   one result list, stories with their best
                                            1-3 matching articles underneath
      facets()                              the values the Explore filters may offer
  A question goes to BOTH indexes in `data/search.db` -- the FTS5 word index and the
  Qwen passage vectors -- and the two rankings are fused. Passages find, articles are
  cited, stories are shown (Q10a, small-to-big retrieval).

WHY TWO INDEXES, AND WHY FUSE THEM BY RANK
  The two fail differently. The word index cannot find "the central bank" in a piece
  that only ever says "RBI"; the vector index cannot be trusted to find an exact figure
  or a name it has never seen. Running both and fusing covers each one's blind spot.
  The fusion is RECIPROCAL RANK FUSION: a passage's score is the sum of 1/(k + rank)
  over the lists it appears in, with k = 60. It uses only each list's ORDER, never its
  score, which is the point -- a bm25 score and a cosine are different units and cannot
  be added, while two orderings can always be compared. A passage both indexes like
  beats one that a single index loves.

THE UNIT QUESTION (why a result row is a story and not an article)
  The archive holds the same story many times over: one text per paper, often several
  passages per text. Ranking passages and showing passages would fill the screen with
  one story. So the roll-up is passage -> article (its best passage represents it) ->
  story (the THREAD ROOT, the same unit the digest shows), and one text printed in
  several seats appears once (Q11g).

WHAT A FILTER MEANS HERE (pre-filtering, not post-filtering)
  Filters are applied BEFORE ranking, by cutting the candidate set down to the allowed
  `text_hash`es first. Filtering afterwards would silently shorten the result list: ask
  for 20 stories from Mint and the top 50 passages might hold three of them. The label
  rules come from `p3_label`, so the digest and the search cannot disagree about what
  counts: ads are out, paid pieces are kept and carry their caveat, and NOTICES ARE
  SEARCHABLE even though the digest leaves them out (Q10d, schema-on-read -- what a
  question may see is decided at question time, not at indexing time).

WHEN THE EMBEDDING SERVICE IS DOWN
  This module runs in `.venv`, which has no embedding model, so a question is embedded
  by `p3_embedsvc.py` over HTTP (Q14 r). If that service is not up, `search()` does NOT
  fail: it runs the word index alone and says so in its result (`mode: "fts_only"`), so
  the page can tell the reader that meaning-search is off rather than quietly returning
  worse answers (graceful degradation).

DATA (one owner per file)
  reads   data/search.db · data/news.db · data/intel.db   ALL read-only (mode=ro)
  writes  nothing at all. No model call, no cost, no stored data.

HOW TO RUN (from ~/news-intel; the service is optional)
  .venv-embed/bin/python src/p3_embedsvc.py          first, in its own terminal
  .venv/bin/python src/p3_search.py "the EPFO wage ceiling"      look at a result
  .venv/bin/python src/p3_search.py --facets
  Its test (free, no model, no network):
  .venv/bin/python tests/test_p3_search.py
"""

import argparse
import json
import re
import sqlite3
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import p3_digest as D                                              # noqa: E402
import p3_label as L                                               # noqa: E402

SEARCH_DB = ROOT / "data" / "search.db"
NEWS_DB = ROOT / "data" / "news.db"
INTEL_DB = ROOT / "data" / "intel.db"

SEARCH_VERSION = "s0.1"

# ---------------------------------------------------------------- KNOBS

KNOBS = {
    # How many passages each index offers before fusion. 50 is the design's number
    # (Q11). Raising it is the first candidate if Q17 shows right answers at ranks 6-20.
    # Measured: the Q17 run's rank list -- if misses sit just outside the cut, raise it.
    "candidates_per_index": 50,

    # RRF smoothing. 60 is the published default and the design's (Q11): it keeps rank 1
    # from dominating so completely that a strong second opinion cannot matter.
    # Changing it changes only how steep the rank discount is.
    "rrf_k": 60,

    # How much more a word in the HEADLINE counts than a word in the body, in bm25.
    # A headline is the paper's own summary of the piece, so a question's words landing
    # there is better evidence than the same words in paragraph nine. Placed.
    # Measured: Q17 recall@5 with and without the weight.
    "fts_headline_weight": 3.0,

    # Articles shown under each story row (Q11g: 1-3). More would turn a result list
    # into a reading list; fewer would hide that two papers covered it.
    "articles_per_story": 3,

    # Snippet length, cut at a sentence end. ~40 words is about two lines on screen.
    "snippet_words": 40,

    # WHICH NUMBER THE VERDICT READS. Brief §3 rule 7 says the top story's FUSED score;
    # the Q17 run shows that number cannot carry the decision (see Q17_EVIDENCE
    # "why_not_fused"), so this reads the top passage's SIMILARITY instead.
    # CLOSED by Kunaal, 3 Nov 2026: keep the cosine. The brief's rule 7 and the design
    # note's Q11 are superseded on this point, and `_docs/` records the old rule until
    # the Project is updated -- so a later reader who finds them disagreeing with this
    # line should trust this line and the measurement behind it, not the documents.
    # `"fused_score"` is still implemented and selectable, for the record and in case a
    # near-miss question set ever changes the picture.
    "verdict_signal": "top_similarity",          # "top_similarity" | "fused_score"

    # Below this cosine the top passage is not about the question at all -> verdict
    # "empty", and the ask pane does not call a model (contract §7). Measured: the three
    # not-covered questions top out at 0.5151 and the weakest covered one is 0.6174, so
    # the line is the middle of that gap.
    "not_found_similarity": 0.5663,

    # Below this the answer is written but the pane says it is thin. Placed JUST BELOW
    # the weakest covered question (Q09, 0.6174), so every question the archive does
    # answer clears it -- putting the line exactly AT 0.6174 made Q09 flip to "thin" on
    # float noise alone.
    # PLACED, not measured: the Q17 set has no near-miss questions by construction (its
    # own "known limit" -- it tests "archive has nothing", never "nearby but not the
    # answer"), so NOTHING in the test set is thin and the band 0.5663-0.6150 is
    # unobserved. Measured by: a near-miss question set, the one the frozen set says it
    # does not have.
    "thin_similarity": 0.6150,

    # The same two lines for bm25, used only when the embedding service is down and
    # there is no cosine to read. Measured: the worst covered question scores -16.021
    # and the best not-covered one -13.844 (bm25 is negative; lower is better), so the
    # line is the middle of that gap. WEAKER than the cosine: the margin is a third as
    # wide and bm25 moves with query length, so a long question can look weak.
    "not_found_bm25": -14.93,
    "thin_bm25": -16.02,

    # Brief rule 7's own thresholds, kept for `verdict_signal = "fused_score"`. The Q17
    # run could not separate covered from not-covered on this signal at any value.
    "not_found_threshold": 0.0280,
    "thin_threshold": 0.0300,

    # A 1-2 letter word is dropped only when a longer word remains in the query, so
    # "AI" is searched while "of the police" drops "of" (contract v2.2).
    "short_word_len": 2,

    # The service that embeds a question. Local, free, started by hand (Q14 r).
    "embed_url": "http://127.0.0.1:8766",
    "embed_timeout_s": 30,
}

# The evidence behind the two thresholds, kept beside them so a later session can see
# what would change them rather than guessing. Filled in by `_work/k3_q17_score.py`.
Q17_EVIDENCE = {
    "measured": "2026-11-03, menu q17-menu-1, service up (mps), run exam-v1, s0.1",
    "recall_at_5": {"fused": 1.00, "fts_only": 0.95, "vector_only": 0.95},
    "recall_at_10": {"fused": 1.00, "fts_only": 1.00, "vector_only": 1.00},

    # THE SIGNAL THE VERDICT READS -- top passage cosine. Separated, with a wide gap.
    "covered_top_similarity": {"min": 0.6174, "median": 0.7601, "max": 0.8915},
    "not_covered_top_similarity": {"N6": 0.5151, "N8": 0.4526, "N9": 0.4523},
    "similarity_gap": "0.5151 (worst not-covered) .. 0.6174 (weakest covered)",

    # The fallback signal, for when the embedding service is down.
    "covered_top_bm25": {"best": -68.144, "median": -30.845, "worst": -16.021},
    "not_covered_top_bm25": {"N6": -13.844, "N8": -11.103, "N9": -11.014},

    # WHY BRIEF RULE 7'S SIGNAL WAS NOT USED. The fused score is built from RANKS, so
    # the top story's score is a function of the POSITION it reached in each list, not
    # of how well it matches. Rank 1 in both lists is 2/(60+1) = 0.03279 whatever the
    # query, and every query has a top-ranked story -- the not-covered ones still match
    # 64-83 stories, because an OR of a question's words hits incidental mentions. So
    # the two populations overlap and no threshold separates them:
    "fused_covered_top_scores": [0.02938, 0.03279],          # the observed range
    "fused_not_covered_top_scores": {"N6": 0.03150, "N8": 0.03033, "N9": 0.02662},
    "why_not_fused": "not separable at any value: not-covered reaches 0.03150 while "
                     "covered falls to 0.02938. The whole observed spread is the top "
                     "few RRF ranks (rank1+rank1 = 0.03279, rank2+rank2 = 0.03226), "
                     "so the number carries position, not match quality.",
}

STOP_AT_SENTENCE = re.compile(r"(?:(?<=[.!?])|(?<=[.!?][\"'”’]))\s+")
WORD = re.compile(r"[0-9a-zÀ-ɏ]+", re.I)


# ---------------------------------------------------------------- connections

def open_ro(path):
    """Read-only connection. Least privilege: this module must not be able to write."""
    path = Path(path)
    if not path.exists():
        raise LookupError(f"missing {path} (run from ~/news-intel)")
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


# ---------------------------------------------------------------- the query

def tokens(query):
    """The words actually searched for, after the short-word rule (contract v2.2).

    Short words ARE searched -- "UP", "EU", "GST" and "AI" are real queries here, and a
    minimum length would make them unanswerable. They are dropped only when a longer
    word remains, because next to "police" the word "of" adds nothing but noise. A query
    of nothing but short words is still a query, never a browse.
    """
    found = [w.lower() for w in WORD.findall(query or "")]
    longer = [w for w in found if len(w) > KNOBS["short_word_len"]]
    return longer if longer else found


def fts_expression(words):
    """An FTS5 MATCH expression from the user's words, with every token QUOTED.

    Quoting is the whole job. Unquoted, a reader's apostrophe, asterisk or the word
    "OR" becomes FTS5 syntax: `search('"`)` would be a syntax error and `search("the
    OR")` would mean something the reader never asked. Quoted, each token is a literal
    (an inner `"` is doubled, FTS5's own escape), and the tokens are joined with OR so
    that a long question still matches -- no passage contains every word of "why do
    employers welcome the EPFO wage ceiling being raised", and an AND would answer
    nothing. bm25 then does the ranking, where a rare word counts for much more than a
    common one, so OR costs precision much less than it looks.
    """
    quoted = ['"' + w.replace('"', '""') + '"' for w in words]
    return " OR ".join(quoted)


# ---------------------------------------------------------------- the corpus

def _dates_in_window(news, filters):
    """The stored paper-days the filters allow, newest last."""
    dates = D.stored_dates(news)
    lo, hi = filters.get("date_from"), filters.get("date_to")
    for name, value in (("date_from", lo), ("date_to", hi)):
        if value is not None and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(value)):
            raise ValueError(f"{name} must be YYYY-MM-DD, got {value!r}")
    if lo:
        dates = [d for d in dates if d >= lo]
    if hi:
        dates = [d for d in dates if d <= hi]
    return dates


def _seat_allowed(seat, filters):
    """The label and filter rules, per printed slot, BEFORE anything is ranked.

    `p3_label` owns the first three: an ad is never searchable, a paid piece is kept and
    carries its caveat, and a notice IS searchable (the digest leaves notices out; a
    question may still find one -- Q10d). The rest are the reader's own filters.
    """
    if seat["layer"] == "ad" or not seat["searchable"]:
        return False
    if filters.get("papers") and seat["publisher"] not in set(filters["papers"]):
        return False
    if filters.get("kinds") and seat["kind"] not in set(filters["kinds"]):
        return False
    if filters.get("text_hashes") and seat["text_hash"] not in set(filters["text_hashes"]):
        return False
    return True


def corpus(filters=None, news=None, intel=None):
    """Everything searchable under these filters, grouped into stories.

    Returns the pieces every later step needs: which hashes may be ranked, which story
    each hash belongs to, and each story's own row. Built from `p3_digest`'s own
    functions -- `load_seats`, `paper_day_shape`, `seat_points`, `load_stories`,
    `pick_headline`, `page_link` -- so a search result and a digest item can never
    disagree about what a story is, what it is called, or where its pages are.
    """
    filters = dict(filters or {})
    close_news = news is None
    close_intel = intel is None
    news = news or open_ro(NEWS_DB)
    intel = intel or open_ro(INTEL_DB)
    try:
        dates = _dates_in_window(news, filters)
        seats = [s for s in D.load_seats(news, dates) if _seat_allowed(s, filters)]
        shape = D.paper_day_shape(seats)
        for s in seats:
            # seat_points returns (points, why) and the digest unpacks it the same way
            # in score_story; `points` is what pick_headline and the row order read.
            s["points"], s["points_why"] = D.seat_points(s, shape)
        stories = D.load_stories(intel, seats)
    finally:
        if close_news:
            news.close()
        if close_intel:
            intel.close()

    topics = set(filters["topics"]) if filters.get("topics") else None
    only = set(filters["story_ids"]) if filters.get("story_ids") else None
    exclude = set(filters.get("exclude_story_ids") or [])

    rows, story_of_hash = {}, {}
    # Every day with something searchable on it counts as SEARCHED, even if its story is
    # excluded or does not match: "searched N days" describes the window the question
    # was asked of, not what came back (K1's double, same rule).
    searched_days = {s["date"] for s in seats if s.get("date")}

    for story in stories:
        head = D.pick_headline(story, {})
        topic = _story_topic(story)
        sid = story["story_id"]
        if topics is not None and topic not in topics:
            continue
        if only is not None and sid not in only:
            continue
        if sid in exclude:
            continue
        seen = {}
        for seat in story["seats"]:
            # One text printed in several seats is ONE article (Q11g). The seat kept is
            # the best-placed one, so the page link opens the fullest showing of it.
            best = seen.get(seat["text_hash"])
            if best is None or seat["points"] > best["points"]:
                seen[seat["text_hash"]] = seat
        for h in seen:
            story_of_hash[h] = sid
        rows[sid] = {
            "story_id": sid,
            "headline": head.get("text") or "",
            "topic": topic,
            "dates": sorted({s["date"] for s in story["seats"] if s["date"]}),
            "papers": len({s["publisher"] for s in story["seats"]}),
            "seat_of_hash": seen,
        }
    return {"stories": rows, "story_of_hash": story_of_hash,
            "searched_days": len(searched_days), "allowed": set(story_of_hash)}


def _story_topic(story):
    """The story's theme topic: the most common topic among its covering articles (7a).

    The topics filter reads this (contract v2.2) -- a story carries no `topics` field of
    its own, and the per-article tag is not the story's subject.
    """
    counts = {}
    for seat in story["seats"]:
        if seat["counts_as_coverage"] and seat.get("topic"):
            counts[seat["topic"]] = counts.get(seat["topic"], 0) + 1
    if not counts:
        return None
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]


# ---------------------------------------------------------------- the two indexes

def fts_rank(scon, words, allowed, limit):
    """Top passages by bm25, headline weighted.

    Returns [(passage_id, text_hash, bm25), ...], best first. bm25 is NEGATIVE in
    SQLite and lower is better, which is why the ordering has no `DESC` and why the
    fallback verdict compares with `<`.

    The `allowed` cut is taken after the index returns but before the limit, and the
    index is asked for more rows than the limit, because the filters can remove most of
    what the top 50 holds (pre-filtering by hash set, see the module docstring).
    """
    if not words:
        return []
    expr = fts_expression(words)
    sql = ("SELECT p.passage_id, p.text_hash, bm25(passages_fts, ?, 1.0) AS s "
           "FROM passages_fts f JOIN passages p ON p.passage_id = f.rowid "
           "WHERE passages_fts MATCH ? ORDER BY s LIMIT ?")
    try:
        rows = scon.execute(sql, (KNOBS["fts_headline_weight"], expr,
                                  max(limit * 8, limit))).fetchall()
    except sqlite3.OperationalError as e:
        # A MATCH expression this build produced is a bug here, not the reader's fault.
        raise ValueError(f"the word index refused the query: {e}") from e
    out = [(pid, h, s) for pid, h, s in rows if h in allowed]
    return out[:limit]


def embed_query(text, url=None, timeout=None):
    """One query vector from the local embedding service, or None when it is not up.

    Returns None rather than raising, because "the service is down" is a normal state
    the caller degrades through, not an error for the reader (see the module docstring).
    """
    url = (url or KNOBS["embed_url"]).rstrip("/")
    timeout = timeout or KNOBS["embed_timeout_s"]
    body = json.dumps({"texts": [text], "is_query": True}).encode("utf-8")
    req = urllib.request.Request(f"{url}/embed", data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            got = json.load(r)
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return None
    vectors = got.get("vectors") if isinstance(got, dict) else None
    if not vectors or not isinstance(vectors[0], list):
        return None
    return vectors[0]


_VECTOR_CACHE = {}


def _db_file(con):
    """The file a connection is actually reading, asked of the connection itself.

    The cache below is keyed on this rather than on a path argument, because a path
    argument can disagree with the connection: a test pointing at a synthetic
    `search.db` would otherwise share a cache entry with the real one and silently
    search the wrong archive.
    """
    for _seq, name, file in con.execute("PRAGMA database_list"):
        if name == "main":
            return file or ""
    return ""


def load_passage_vectors(scon, path=None):
    """Every passage vector as one float32 matrix, with its row order and its norms.

    10,912 x 1024 floats is 45 MB and the same every call, so it is read once per
    process and kept, keyed by the file's size and mtime -- a rebuilt `search.db` is a
    different key and is reloaded. Brute force over 10,912 rows is well inside the
    design's trigger for an approximate index (~200k passages or ~200 ms a query).
    """
    import numpy as np
    path = path or _db_file(scon)
    stat = Path(path).stat()
    key = (str(path), stat.st_size, stat.st_mtime_ns)
    hit = _VECTOR_CACHE.get(key)
    if hit is not None:
        return hit
    ids, blobs = [], []
    for pid, h, dim, vec in scon.execute(
            "SELECT v.passage_id, p.text_hash, v.dim, v.vec FROM passage_vectors v "
            "JOIN passages p ON p.passage_id = v.passage_id ORDER BY v.passage_id"):
        ids.append((pid, h))
        blobs.append(np.frombuffer(vec, dtype=np.float32, count=dim))
    matrix = np.vstack(blobs) if blobs else np.zeros((0, 1), dtype=np.float32)
    norms = np.linalg.norm(matrix, axis=1)
    norms[norms == 0] = 1.0
    _VECTOR_CACHE.clear()                     # one matrix per process, not one per file
    _VECTOR_CACHE[key] = (ids, matrix, norms)
    return _VECTOR_CACHE[key]


def vector_rank(scon, qvec, allowed, limit, path=None):
    """Top passages by cosine. Returns [(passage_id, text_hash), ...].

    THE COSINE IS A REAL COSINE, not a bare dot product. The stored vectors are meant to
    be L2-normalised and very nearly are, but measured on 3 Nov they are not exactly:
    400 sampled `passage_vectors` have norms from 0.998053 to 1.003724. That is what
    Qwen3-0.6B's own normalisation lands on in float32 on this machine, so dividing by
    the norms costs one pass over 10,912 numbers and removes the question. It changes
    only the ORDER of near-ties -- the fused score is built from RANKS, so a 0.4% drift
    in a similarity cannot move a verdict threshold, which is why this is cheap
    insurance rather than a correctness fix.
    """
    import numpy as np
    if qvec is None:
        return []
    ids, matrix, norms = load_passage_vectors(scon, path)
    if not ids:
        return []
    q = np.asarray(qvec, dtype=np.float32)
    qn = float(np.linalg.norm(q)) or 1.0
    sims = (matrix @ q) / (norms * qn)
    keep = [i for i, (_pid, h) in enumerate(ids) if h in allowed]
    if not keep:
        return []
    keep = np.asarray(keep)
    order = keep[np.argsort(-sims[keep], kind="stable")][:limit]
    # The cosine travels with the candidate, because the verdict is read off it: the
    # fused score cannot say how good a match is, only where it placed (Q17_EVIDENCE).
    return [(ids[i][0], ids[i][1], float(sims[i])) for i in order]


def rrf(lists, k=None):
    """Reciprocal rank fusion: score = sum of 1/(k + rank) over the lists it is in.

    Rank is 1-based. Only the order of each list is used, never its score, because a
    bm25 figure and a cosine are different units that cannot be added (Q11).
    """
    k = KNOBS["rrf_k"] if k is None else k
    fused = {}
    for ranked in lists:
        for i, key in enumerate(ranked, start=1):
            fused[key] = fused.get(key, 0.0) + 1.0 / (k + i)
    return fused


# ---------------------------------------------------------------- snippets

def strip_passage_header(text):
    """Drop the "paper · date · headline" line the index puts on every passage.

    `p3_index.py` prepends it (`p3_index.py:194-202`) so that a passage cut out of the
    middle of an article still carries its context INTO THE EMBEDDING -- it is written
    for the model, not for the reader. A result row already shows the paper, the date
    and the headline above the snippet, so leaving it in would print all three twice and
    spend a third of the snippet's words saying nothing new.
    """
    body = (text or "").split("\n\n", 1)
    return body[1] if len(body) == 2 else body[0]


def snippet(text, words=None):
    """The passage cut to about `snippet_words` words, ending at a sentence end.

    Cut mid-sentence a snippet reads as a mistake, so the cut lands on the last sentence
    boundary that fits; if the first sentence is already longer than the budget (1.4% of
    long texts have a sentence over 120 words, M4) the words are taken and an ellipsis
    added, because showing nothing is worse than showing a long line.
    """
    budget = words or KNOBS["snippet_words"]
    text = " ".join(strip_passage_header(text).split())
    if not text:
        return ""
    if len(text.split()) <= budget:
        return text
    sentences = STOP_AT_SENTENCE.split(text)
    if len(sentences[0].split()) > budget:
        # The first sentence alone overruns the budget, so there is no sentence end to
        # stop at (1.4% of long texts have a sentence over 120 words, M4). Cut to the
        # budget and say so with an ellipsis, rather than returning the whole wall of
        # text -- a snippet that ignores its budget is not a snippet.
        return " ".join(text.split()[:budget]) + "…"
    out, used = [], 0
    for sentence in sentences:
        n = len(sentence.split())
        if out and used + n > budget:
            break
        out.append(sentence)
        used += n
        if used >= budget:
            break
    return " ".join(out).strip()


def _article_row(seat, passage_text):
    """The seven keys the contract gives an article row, plus its snippet."""
    link = D.page_link(seat)
    link["paper"] = seat["publisher"]
    return {"text_hash": seat["text_hash"], "headline": seat["headline"],
            "paper": seat["publisher"], "date": seat["date"], "kind": seat["kind"],
            "page_link": link, "snippet": snippet(passage_text)}


# ---------------------------------------------------------------- search

def _desc(value):
    """Sort key that puts the newest first while keeping everything else ascending."""
    return tuple(-ord(c) for c in (value or ""))


def search(query, filters=None, sort="relevance", limit=20, news=None, intel=None,
           scon=None, embed=None, indexes=("fts", "vector")):
    """R10 · one result list: stories, each with its 1-3 best-matching articles.

    An empty `query` is a BROWSE (Explore): everything the filters allow, newest first,
    no score and no verdict -- a browse has nothing to be thin about. With a query, both
    indexes run, their rankings are fused, and the passages roll up to stories.

    The app passes only the first four arguments. The rest are seams: `news`/`intel`/
    `scon` let a test point this at a synthetic archive, `embed` injects a stub encoder,
    and `indexes` runs one index alone -- which is how the Q17 report compares
    word-search against meaning-search, and how the service-down path is exercised
    without stopping the service.
    """
    if sort not in ("relevance", "newest"):
        raise ValueError(f"sort must be 'relevance' or 'newest', got {sort!r}")
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        raise ValueError(f"limit must be a whole number, got {limit!r}") from None
    if limit < 0:
        raise ValueError(f"limit cannot be negative, got {limit}")

    filters = dict(filters or {})
    words = tokens(query)
    browse = not words

    close_scon = scon is None
    scon = scon or open_ro(SEARCH_DB)
    try:
        world = corpus(filters, news=news, intel=intel)
        allowed = world["allowed"]
        mode, scores, best_passage = "both", {}, {}
        sim_of, bm25_of = {}, {}

        if not browse and allowed:
            per = KNOBS["candidates_per_index"]
            fts = fts_rank(scon, words, allowed, per) if "fts" in indexes else []
            vec = []
            if "vector" in indexes:
                qvec = (embed or embed_query)(query)
                if qvec is None:
                    # The service is not up. Say so in the result rather than failing or
                    # quietly returning word-only answers as if nothing were missing.
                    mode = "fts_only"
                else:
                    vec = vector_rank(scon, qvec, allowed, per)
            elif "fts" in indexes:
                mode = "fts_only"
            if "fts" not in indexes:
                mode = "vector_only"
            fused = rrf([[pid for pid, _h, _s in fts], [pid for pid, _h, _s in vec]])
            hash_of = {pid: h for pid, h, _s in list(fts) + list(vec)}
            for pid, h, sim in vec:
                sim_of[h] = max(sim, sim_of.get(h, -1.0))
            for pid, h, score in fts:
                bm25_of[h] = min(score, bm25_of.get(h, 0.0))
            texts = _passage_texts(scon, list(fused))
            for pid, score in fused.items():
                h = hash_of[pid]
                # An article is represented by its BEST passage, and a story by the best
                # of its articles -- the roll-up of small-to-big retrieval (Q10a).
                if score > scores.get(h, -1.0):
                    scores[h] = score
                    best_passage[h] = texts.get(pid, "")

        hits = []
        for sid, row in world["stories"].items():
            seats = row["seat_of_hash"]
            if browse:
                matched = sorted(seats, key=lambda h: (-(seats[h]["points"] or 0),
                                                       _desc(seats[h]["date"])))
                story_score = None
            else:
                matched = [h for h in seats if h in scores]
                if not matched:
                    continue
                matched.sort(key=lambda h: (-scores[h], _desc(seats[h]["date"])))
                story_score = scores[matched[0]]
            story_sim = max((sim_of[h] for h in matched if h in sim_of), default=None)
            story_bm25 = min((bm25_of[h] for h in matched if h in bm25_of), default=None)
            hits.append({"story_id": sid, "headline": row["headline"],
                         "dates": row["dates"], "papers": row["papers"],
                         "score": story_score, "similarity": story_sim,
                         "articles": [],
                         "_bm25": story_bm25,
                         "_shown": matched[:KNOBS["articles_per_story"]],
                         "_seats": seats,
                         "_total": len(matched),
                         "_latest": row["dates"][-1] if row["dates"] else ""})

        if browse or sort == "newest":
            hits.sort(key=lambda h: (_desc(h["_latest"]), h["headline"]))
        else:
            hits.sort(key=lambda h: (-(h["score"] or 0.0), _desc(h["_latest"]),
                                     h["headline"]))

        total_articles = sum(h["_total"] for h in hits)

        # Snippets only for the rows actually returned. A browse over the whole archive
        # is 5,668 stories; building a snippet for each one's articles would read
        # thousands of passages to show twenty, so the lead passages are fetched AFTER
        # `limit` has been applied.
        shown_hits = hits[:limit]
        need_lead = [h for hit in shown_hits for h in hit["_shown"]
                     if not best_passage.get(h)]
        leads = _lead_passages(scon, need_lead)
        for hit in shown_hits:
            hit["articles"] = [
                _article_row(hit["_seats"][h],
                             best_passage.get(h) or leads.get(h, ""))
                for h in hit["_shown"]]
        # The best bm25 anywhere in the result, for the same reason as the cosine above.
        top_bm25 = min((h["_bm25"] for h in hits if h["_bm25"] is not None), default=None)
        for h in hits:
            del h["_total"]
            del h["_latest"]
            del h["_shown"]
            del h["_seats"]
            del h["_bm25"]

        out = {"query": query, "filters": filters,
               "sort": "newest" if browse else sort,
               "verdict": None if browse else verdict_of(hits, top_bm25),
               "searched_days": world["searched_days"],
               "total_stories": len(hits),
               "total_articles": total_articles,
               "stories": shown_hits,
               "fixture": False}
        if mode != "both":
            out["mode"] = mode
        return out
    finally:
        if close_scon:
            scon.close()


def verdict_of(hits, top_bm25=None):
    """`empty` / `thin` / `enough` for the top story — how good the best match is.

    Not from how many stories came back: one exactly-right story is a good answer and
    forty weak ones are not. The not-covered questions each still match 64-83 stories,
    so a count would call them all `enough`.

    WHICH NUMBER IT READS IS A DEVIATION FROM BRIEF RULE 7, and `KNOBS["verdict_signal"]`
    is all of it. Rule 7 says the top story's fused score; measured on the Q17 set, that
    number cannot carry the decision, because RRF is built from RANKS -- rank 1 in both
    lists is 2/(60+1) whatever was asked, and every query has a top-ranked story. The
    two populations overlap (`Q17_EVIDENCE["why_not_fused"]`). The top passage's COSINE
    does separate them, 0.5151 against 0.6174, so that is the default.

    With the embedding service down there is no cosine, and bm25 is the fallback: it
    separates too but by a third of the margin and it moves with query length, so a
    long question can look weak. The page already says meaning-search is off.
    """
    if not hits:
        return "empty"
    # THE BEST MATCH IN THE RESULT, not the first row. The first row is whatever RRF
    # ranked highest, and RRF ranks on position in two lists; the story holding the
    # closest passage is often not it. Measured 3 Nov: reading row one called Q18 empty
    # at 0.5479 while its own best passage scores 0.6527 and the question is answered in
    # the archive -- a false "not in your papers", the one wrong answer this verdict
    # must never give, because the ask pane then refuses to look.
    best_sim = max((h["similarity"] for h in hits if h.get("similarity") is not None),
                   default=None)
    if KNOBS["verdict_signal"] == "fused_score":
        value, low, high = hits[0].get("score"), "not_found_threshold", "thin_threshold"
    elif best_sim is not None:
        value, low, high = best_sim, "not_found_similarity", "thin_similarity"
    elif top_bm25 is not None:
        # bm25 runs the other way: MORE NEGATIVE is a better match. So a score above a
        # line is worse than it, and the two comparisons are the mirror of the ones
        # below. Measured: worst covered -16.021, best not-covered -13.844.
        if top_bm25 > KNOBS["not_found_bm25"]:
            return "empty"
        return "thin" if top_bm25 > KNOBS["thin_bm25"] else "enough"
    else:
        return None                   # no signal at all (browse, or nothing ranked)
    if value is None or KNOBS[low] is None or KNOBS[high] is None:
        return None
    if value < KNOBS[low]:
        return "empty"
    if value < KNOBS[high]:
        return "thin"
    return "enough"


def _lead_passages(scon, hashes):
    """Each article's FIRST passage (`ord` 0) — the snippet for a row nothing matched.

    A browse has no matching passage to quote, and an article shown because a SIBLING
    article matched has none of its own either. The opening words are what a reader
    scanning a list wants, and they are the one part of an article that is always worth
    reading first (the inverted pyramid: papers put the news in the lead).
    """
    want = sorted(set(h for h in hashes if h))
    if not want:
        return {}
    out = {}
    for chunk in [want[i:i + 400] for i in range(0, len(want), 400)]:
        qs = ",".join("?" * len(chunk))
        for h, text in scon.execute(
                f"SELECT text_hash, text FROM passages "
                f"WHERE text_hash IN ({qs}) AND ord = 0", tuple(chunk)):
            out[h] = text
    return out


def _passage_texts(scon, passage_ids):
    if not passage_ids:
        return {}
    out = {}
    for chunk in [passage_ids[i:i + 400] for i in range(0, len(passage_ids), 400)]:
        qs = ",".join("?" * len(chunk))
        out.update(dict(scon.execute(
            f"SELECT passage_id, text FROM passages WHERE passage_id IN ({qs})",
            tuple(chunk)).fetchall()))
    return out


# ---------------------------------------------------------------- facets

def facets(news=None, intel=None):
    """The values the Explore filters may offer — and nothing else.

    Taken from the archive itself, under the CURRENT tag record only, so a filter can
    never offer a value with nothing behind it (the 91 topics across all prompt versions
    include 16 from v0.1 that no current story carries; only v1.3's are real today).
    """
    world = corpus({}, news=news, intel=intel)
    papers, kinds, topics, dates = set(), set(), set(), set()
    for row in world["stories"].values():
        if row["topic"]:
            topics.add(row["topic"])
        for seat in row["seat_of_hash"].values():
            if seat["publisher"]:
                papers.add(seat["publisher"])
            if seat["kind"]:
                kinds.add(seat["kind"])
            if seat["date"]:
                dates.add(seat["date"])
    return {"papers": sorted(papers), "topics": sorted(topics), "kinds": sorted(kinds),
            "date_min": min(dates) if dates else None,
            "date_max": max(dates) if dates else None}


# ---------------------------------------------------------------- looking

def main():
    p = argparse.ArgumentParser(description="K3 search over the archive (contract §6)")
    p.add_argument("query", nargs="?", default="", help="the question; empty = browse")
    p.add_argument("--facets", action="store_true", help="print the filter values instead")
    p.add_argument("--sort", default="relevance", choices=("relevance", "newest"))
    p.add_argument("--limit", type=int, default=5)
    args = p.parse_args()
    if args.facets:
        f = facets()
        print(f"papers {len(f['papers'])}: {', '.join(f['papers'])}")
        print(f"kinds {len(f['kinds'])}: {', '.join(f['kinds'])}")
        print(f"topics {len(f['topics'])}")
        print(f"dates {f['date_min']} .. {f['date_max']}")
        return 0
    r = search(args.query, sort=args.sort, limit=args.limit)
    mode = r.get("mode", "both")
    print(f"{r['total_stories']} stories · {r['total_articles']} articles · "
          f"{r['searched_days']} days searched · verdict {r['verdict']} · mode {mode}")
    for i, s in enumerate(r["stories"], start=1):
        score = "browse" if s["score"] is None else f"{s['score']:.5f}"
        print(f"{i:2}. [{score}] {s['headline']}  ({s['papers']} papers, "
              f"{s['dates'][0]}..{s['dates'][-1]})")
        for a in s["articles"]:
            print(f"      {a['paper']} {a['date']} {a['page_link']['label']}: "
                  f"{a['snippet'][:90]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
