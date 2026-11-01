"""p3_digest.py — Phase 3 · Digest v1, part 1: ranking and the written item, no LLM.
  v0.1 1 Nov 2026 (K3 B1-3): from K3 build brief 1 §B1-3 and the K3 note Q3-Q7, Q5+.

WHAT IT DOES
  Builds the digest for a window of days: which stories mattered, in what order, each
  with a headline, a one-liner and the rest of its item — and writes nothing to the
  databases. Everything here is computed from stored data; no model is called. Part 2
  (B1-4) adds the written-up paragraph and the "story so far", both LLM.

  Reads  news.db (read-only)    seats, texts, tags
         intel.db (read-only)   K2's groups g0.1 and threads t0.2
         vectors.db (read-only) Qwen3-0.6B V1, for the medoid headline only
  Writes nothing. `build` prints JSON, or saves it under `_work/` with --out.

WHY, AND WHAT A "STORY" IS
  A story is one of K2's threads: a day-group, joined to the earlier day-groups it
  continues (t0.2 parent links). Its id is the thread root, computed on read — the same
  convention K2 uses, so nothing has to be stored twice. The digest ranks stories, not
  articles, because a story covered by four papers is one thing to read, and four
  papers choosing to cover it is the evidence that it mattered.

HOW THE RANKING WORKS (Q3)
  Papers vote with their layout. Each paper gets its own say about a story:
      placement   front page 3 · early 2 · inside 1       where the paper put it
      size        top 10% of that paper's day +2 · above that day's median +1
  A paper's points for a story = its best SINGLE day in the window (a paper running a
  story four days does not out-vote four papers running it once). The story's score is
  the sum over papers, so breadth of coverage drives the order — weighted voting, with
  each paper's own day as the yardstick (percentile normalisation, so a 900-word piece
  in a thin paper is not beaten by an average piece in a fat one).
  Only pieces that count as coverage vote (p3_label: no ads, paid features, notices or
  kind 'other'). A story with no coverage in the window scores zero and is never shown.
  Ties are broken by days covered → total words → earliest first print → story id, so
  the order is a total order and the same input always gives the same digest.
  The weights are placed guesses, to be set by the blind weight test on judged groups
  (K3 note §5); every one is a knob below.

THE JSON (the K3 <-> K1 data contract — K3 note Q16)
  build() returns, and `build` prints, one object:
    {"built_at", "window": {"kind", "from", "to", "days"},
     "versions": {"label", "grouping", "tracking", "tag_prompt", "digest"},
     "knobs": {...},                         what produced this digest
     "stories": [ {
        "story_id", "score", "rank", "dates", "developing",
        "headline": {"text", "source_text_hash", "fallbacks"},
        "one_liner": {"text", "source_text_hash", "fallback"},
        "theme": {"topic", "stories_21d", "trend"} | null,
        "coverage": {"articles", "papers", "rows": [ {paper, page, page_link,
                      words, kind, byline, headline, text_hash, points} ]},
        "views": [ {paper, page, kind, headline, page_link} ],
        "caveats": {paid_content, paid_only, single_source, partial_text},
        "links": [ {"kind": "see_also_page"|"related_id", "from", "to_story_id"} ],
        "writeup": null                      filled by part 2 (B1-4)
     } ],
     "dropped": {"zero_point": n, "below_top_n": n},
     "report": {...}                         the M3 numbers for the build report
    }
  A page_link is {"pdf", "pdf_page", "label"}. The label says "PDF p5", never a printed
  page number: the printed->PDF offset is NOT stored anywhere in this database (checked
  1 Nov: no meta key, no placements.extra key, no imports column), so claiming a printed
  page would be a guess. K1 owns how the link looks; this is what it has to work with.

HOW TO RUN (from ~/news-intel, pipeline environment)
  .venv/bin/python src/p3_digest.py build --date 2026-10-24 --no-llm
  .venv/bin/python src/p3_digest.py build --date 2026-10-24 --window week --no-llm
  .venv/bin/python src/p3_digest.py build --date D --no-llm --out _work/digest_D.json
  .venv/bin/python src/p3_digest.py build --date D --no-llm --brief     one line per story
  --no-llm is required: part 2 is not built, and a digest without it must say so.

KNOBS
  One block below, each with its reason and what would change it. A knob change bumps
  DIGEST_VERSION, which is stored on every visit record.
"""

import argparse
import array
import collections
import datetime as dt
import json
import re
import sqlite3
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import p3_label as L
import p3_store as ST

ROOT = Path(__file__).resolve().parent.parent
NEWS_DB = ROOT / "data" / "news.db"
VECTORS_DB = ROOT / "data" / "vectors.db"
INTEL_DB = ROOT / "data" / "intel.db"

DIGEST_VERSION = "d0.1"

# What K3 reads from the other strands. Bumped only when they bump.
RUN_ID = "exam-v1"
GROUPING_VERSION = "g0.1"
TRACKING_VERSION = "t0.2"
VEC_MODEL, VEC_VARIANT = "qwen3-0.6b", "V1"

# ------------------------------------------------------------------------- KNOBS

KNOBS = {
    # --- ranking (Q3) -------------------------------------------------------
    "top_n": 10,                       # stories written in full; placed guess
    "placement_points": {"front": 3, "early": 2, "inside": 1},
    "early_fraction": 0.25,            # "early" = first quarter of the pages with articles
    "size_top_points": 2,              # article in the top 10% of its paper's day
    "size_mid_points": 1,              # above that paper-day's median
    "size_top_percentile": 0.90,   # on a paper-day with fewer than 10 covering
                                   # articles this is simply its longest one; real
                                   # paper-days carry ~100, so it bites only in tests
                                   # and on a badly truncated PDF
    "catchup_cap_days": 7,             # longest "since last visit" window (S7)
    "window_week_days": 7,

    # OPEN DECISION (Kunaal, 1 Nov): what counts as the front page.
    #   "lowest_with_articles" (default) — the first page of that paper-day that has any
    #       article. Chosen because only 21 of 62 paper-days have an article on PDF page 1
    #       (lowest page is 3 on 16 days, 5 on 11, once 13): screen v0.2 drops pages with
    #       no text layer or mostly drawing, and some PDFs start late. Treating page 1 as
    #       the only front page would give 3 points to a third of paper-days and 0 to the
    #       rest — a bias in the ranking, not a measurement. It also matches the brief's
    #       own frame for "early", which is already "pages with articles".
    #   "pdf_page_1" — only PDF page 1 is the front page; paper-days without one have no
    #       front-page article. Honest about what we know, systematically unfair.
    #   Changes which stories reach the top 10, so it is Kunaal's call, not a mechanic.
    "front_page_rule": "lowest_with_articles",

    # --- headline (Q4) ------------------------------------------------------
    # Limits from the real spread of printed headlines (4,783 coverage texts with
    # text_check ok, 1 Nov): median 9 words, p95 13, p99 16, max 44; 157 have <= 2 words.
    # Set at p99 and just under p1 so the fallback fires on misreads, not on normal
    # headlines. Report their trigger share (brief §B1-3); move them if it is not small.
    "headline_min_words": 3,
    "headline_max_words": 16,

    # --- theme line (7a) ----------------------------------------------------
    "theme_window_days": 21,
    "theme_rising": 1.5,
    "theme_falling": 0.67,
    "theme_min_stories": 3,
    # A "story on this theme" must have at least this many covering articles (Kunaal,
    # 1 Nov). Counting every lone brief made the line meaningless: the biggest 21-day
    # count was 126 stories and 74 of 75 topics cleared the 3-story guard, so the guard
    # hid nothing. At 2 the biggest count is 23 and 34 of 53 topics clear it.
    "theme_min_articles_per_story": 2,
    # Say nothing about the trend when the earlier two weeks hold no publication days
    # (Kunaal, 1 Nov). The archive has gaps, so an empty earlier week is usually missing
    # papers, not a quiet fortnight. Set False to always state a trend.
    # NOTE: the rates below are per publication week, so with continuous papers
    # (7 recent days, 14 earlier days) this arithmetic IS 7a unchanged and the guard
    # never fires. It is a correction for gaps, not a new rule to undo later.
    "theme_trend_needs_earlier_days": True,

    # --- the rest -----------------------------------------------------------
    "views_kinds": ["opinion", "analysis", "interview"],
    "page_label": "pdf_page",          # the printed->PDF offset is not stored (Q5+ z)
}

# A headline that stops mid-sentence: a trailing comma or dash, or a last word that
# cannot end a sentence. Not a grammar check — a cheap guard against a cut headline.
DANGLING = {"and", "or", "but", "to", "of", "in", "on", "for", "with", "as", "at", "by",
            "from", "that", "the", "a", "an", "its", "his", "her", "their", "after",
            "before", "over", "under", "into", "amid", "says", "said"}


# ───────────────────────────── reading ────────────────────────────────

def open_ro(path):
    if not Path(path).exists():
        sys.exit(f"missing {path} (run from ~/news-intel)")
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def stored_dates(news):
    return [r[0] for r in news.execute(
        "SELECT DISTINCT date FROM placements WHERE run_id=? ORDER BY date", (RUN_ID,))]


def window_dates(news, date, kind, last_visit=None):
    """The dates this digest covers, and what they are called (Q3p: one rule per window)."""
    every = stored_dates(news)
    if date not in every:
        sys.exit(f"no stored papers for {date} (stored: {every[0]}..{every[-1]})")
    if kind == "day":
        days = [date]
    elif kind == "week":
        days = [d for d in every if _back(date, KNOBS["window_week_days"] - 1) <= d <= date]
    elif kind == "since_last":
        cap = _back(date, KNOBS["catchup_cap_days"] - 1)
        start = max(last_visit or cap, cap)
        days = [d for d in every if start <= d <= date]
    else:
        sys.exit(f"unknown window {kind}")
    return days


def _back(date, days):
    return (dt.date.fromisoformat(date) - dt.timedelta(days=days)).isoformat()


def load_seats(news, dates):
    """Every printed slot on those dates, with its text, tag record and label.

    One row per seat (a text printed twice gives two), labelled through p3_label so the
    digest and the search window cannot disagree about what counts.
    """
    if not dates:
        return []
    qs = ",".join("?" * len(dates))
    rows = news.execute(
        f"SELECT p.date, p.publisher, p.art_id, p.page, p.number, p.deck, p.byline, "
        f"       p.text_hash, p.extra, p.source_file, x.headline, x.words, "
        f"       t.kind, t.topic, t.summary, t.event, t.events_more, t.text_check "
        f"FROM placements p "
        f"JOIN texts x ON x.text_hash = p.text_hash "
        f"LEFT JOIN tags t ON t.text_hash = p.text_hash AND t.model = ? AND t.prompt_version = ? "
        f"WHERE p.run_id = ? AND p.date IN ({qs}) "
        f"ORDER BY p.date, p.publisher, p.page, p.art_id",
        (L.TAG_MODEL, L.TAG_PROMPT, RUN_ID, *dates)).fetchall()
    cols = ("date", "publisher", "art_id", "page", "number", "deck", "byline", "text_hash",
            "extra", "source_file", "headline", "words", "kind", "topic", "summary",
            "event", "events_more", "text_check")
    seats = []
    for r in rows:
        seat = dict(zip(cols, r))
        seat["extra"] = _json(seat["extra"], {})
        label = L.label_text(seat)
        label.update({k: seat[k] for k in ("date", "publisher", "art_id", "page", "number",
                                           "deck", "byline", "extra", "source_file",
                                           "headline", "words")})
        label["page_note_ad"] = None
        seats.append(label)
    return seats


def _json(text, default):
    if not text:
        return default
    try:
        return json.loads(text)
    except ValueError:
        return default


def load_stories(intel, seats):
    """Group the seats into stories: K2's day-groups, joined into threads (t0.2).

    A seat whose text K2 never grouped (no member row) becomes a story of its own, so
    nothing printed can fall out of the digest silently.
    """
    hashes = {s["text_hash"] for s in seats}
    group_of, dates_of = {}, {}
    if hashes:
        qs = ",".join("?" * len(hashes))
        for h, gid, gdate in intel.execute(
                f"SELECT text_hash, group_id, date FROM members "
                f"WHERE version = ? AND text_hash IN ({qs})",
                (GROUPING_VERSION, *hashes)):
            group_of[h] = gid
            dates_of[gid] = gdate
    root = thread_roots(intel)

    stories = collections.defaultdict(lambda: {"group_ids": set(), "seats": []})
    for s in seats:
        gid = group_of.get(s["text_hash"])
        sid = root.get(gid, gid) if gid else f"ungrouped:{s['text_hash'][:12]}"
        stories[sid]["group_ids"].add(gid or sid)
        stories[sid]["seats"].append(s)
    for sid, st in stories.items():
        st["story_id"] = sid
        st["group_ids"] = sorted(g for g in st["group_ids"] if g)
    return list(stories.values())


def thread_roots(intel):
    """group_id -> thread root, over t0.2 parent links (same convention as p3_group)."""
    parent = dict(intel.execute(
        "SELECT group_id, earlier_group_id FROM links "
        "WHERE gversion = ? AND tversion = ? AND role = 'parent'",
        (GROUPING_VERSION, TRACKING_VERSION)).fetchall())
    root = {}

    def find(x):
        path = []
        while x not in root and x in parent:
            path.append(x)
            x = parent[x]
        r = root.get(x, x)
        for p in path:
            root[p] = r
        return r

    for g in list(parent):
        find(g)
    return root


# ───────────────────────────── ranking (Q3) ───────────────────────────

def paper_day_shape(seats):
    """Per (date, paper): the pages that have articles, and the word marks for size points."""
    pages = collections.defaultdict(set)
    words = collections.defaultdict(list)
    for s in seats:
        key = (s["date"], s["publisher"])
        pages[key].add(s["page"])
        if s["counts_as_coverage"]:
            words[key].append(s["words"] or 0)
    shape = {}
    for key, page_set in pages.items():
        ordered = sorted(p for p in page_set if p is not None)
        w = sorted(words.get(key, []))
        shape[key] = {
            "pages": ordered,
            "front": ordered[0] if ordered else None,
            "early_until": _early_until(ordered),
            "median_words": statistics.median(w) if w else None,
            "top_words": _percentile(w, KNOBS["size_top_percentile"]),
        }
    return shape


def _early_until(ordered):
    """The last page still counted as 'early': the first quarter of the pages with articles."""
    if not ordered:
        return None
    cut = max(1, round(len(ordered) * KNOBS["early_fraction"]))
    return ordered[min(cut, len(ordered)) - 1]


def _percentile(sorted_values, p):
    if not sorted_values:
        return None
    i = min(int(len(sorted_values) * p), len(sorted_values) - 1)
    return sorted_values[i]


def seat_points(seat, shape):
    """Placement + size points for one printed slot (0 if it does not count as coverage)."""
    if not seat["counts_as_coverage"]:
        return 0, {"placement": None, "size": 0}
    s = shape.get((seat["date"], seat["publisher"]))
    if not s or seat["page"] is None:
        return 0, {"placement": None, "size": 0}

    if KNOBS["front_page_rule"] == "pdf_page_1":
        front = 1
    else:
        front = s["front"]
    if seat["page"] == front:
        where = "front"
    elif s["early_until"] is not None and seat["page"] <= s["early_until"]:
        where = "early"
    else:
        where = "inside"
    placement = KNOBS["placement_points"][where]

    words = seat["words"] or 0
    size = 0
    if s["top_words"] is not None and words >= s["top_words"]:
        size = KNOBS["size_top_points"]
    elif s["median_words"] is not None and words > s["median_words"]:
        size = KNOBS["size_mid_points"]
    return placement + size, {"placement": where, "size": size}


def score_story(story, shape):
    """Story score = sum over papers of that paper's BEST SINGLE DAY (Q3o)."""
    per_paper_day = collections.defaultdict(int)
    for seat in story["seats"]:
        points, why = seat_points(seat, shape)
        seat["points"], seat["points_why"] = points, why
        if points:
            key = (seat["publisher"], seat["date"])
            per_paper_day[key] = max(per_paper_day[key], points)
    best = collections.defaultdict(int)
    for (paper, _day), points in per_paper_day.items():
        best[paper] = max(best[paper], points)
    story["paper_points"] = dict(best)
    story["score"] = sum(best.values())
    return story["score"]


def tie_key(story):
    """Total order among equal scores (Q3q), then the story id so it is never arbitrary."""
    cover = [s for s in story["seats"] if s["counts_as_coverage"]]
    days = len({s["date"] for s in cover})
    words = sum(s["words"] or 0 for s in cover)
    first = min((s["date"] for s in cover), default="9999-99-99")
    return (-story["score"], -days, -words, first, story["story_id"])


def rank(stories, shape):
    for st in stories:
        score_story(st, shape)
    zero = [st for st in stories if st["score"] <= 0]
    scored = sorted((st for st in stories if st["score"] > 0), key=tie_key)
    for i, st in enumerate(scored, 1):
        st["rank"] = i
    return scored, zero


def tie_report(scored):
    """M3: score spread, the share of ties at the top_n boundary, which tie-break decided."""
    n = KNOBS["top_n"]
    scores = [st["score"] for st in scored]
    out = {
        "stories_scored": len(scored),
        "score_max": max(scores, default=0),
        "score_min": min(scores, default=0),
        "score_at_top_n": scores[n - 1] if len(scores) >= n else None,
        "boundary_tie": False,
        "boundary_decided_by": None,
    }
    if len(scores) > n and scores[n - 1] == scores[n]:
        out["boundary_tie"] = True
        a, b = tie_key(scored[n - 1]), tie_key(scored[n])
        for name, i in (("days_covered", 1), ("total_words", 2), ("first_print", 3),
                        ("story_id", 4)):
            if a[i] != b[i]:
                out["boundary_decided_by"] = name
                break
    out["tied_groups"] = sum(1 for _, c in collections.Counter(scores).items() if c > 1)
    return out


# ───────────────────────────── headline (Q4) ──────────────────────────

def headline_problem(text):
    """'' when the printed headline can be used, else which check refused it."""
    words = (text or "").split()
    if not words:
        return "empty"
    if len(words) < KNOBS["headline_min_words"]:
        return "too_short"
    if len(words) > KNOBS["headline_max_words"]:
        return "too_long"
    last = words[-1].strip(".,;:!?\"')").lower()
    if text.rstrip().endswith((",", ";", "-", "–", "—")) or last in DANGLING:
        return "mid_sentence"
    return ""


def load_vectors(vecs, hashes):
    out = {}
    if not hashes:
        return out
    qs = ",".join("?" * len(hashes))
    for h, blob in vecs.execute(
            f"SELECT text_hash, vec FROM vectors WHERE model = ? AND variant = ? "
            f"AND text_hash IN ({qs})", (VEC_MODEL, VEC_VARIANT, *hashes)):
        out[h] = array.array("f", blob)
    return out


def medoid(hashes, vectors):
    """The most central text of the story: highest mean similarity to the others.

    The vectors are L2-normalised, so a dot product IS the cosine similarity. With one
    candidate, or none embedded, the first hash wins — the caller has already put them
    in a deterministic order.
    """
    have = [h for h in hashes if h in vectors]
    if len(have) < 2:
        return have[0] if have else (hashes[0] if hashes else None)
    best, best_mean = None, None
    for h in have:
        a = vectors[h]
        total = 0.0
        for g in have:
            if g == h:
                continue
            b = vectors[g]
            total += sum(x * y for x, y in zip(a, b))
        mean = total / (len(have) - 1)
        if best_mean is None or mean > best_mean:
            best, best_mean = h, mean
    return best


def pick_headline(story, vectors):
    """Medoid printed headline, never from a fragment or garbled text (Q4, Q1g).

    Candidates are tried in order: the medoid first, then the rest by points, and each
    must pass the code checks. Every refusal is reported, so the build report can show
    how often the fallback chain is used (brief §B1-3).
    """
    usable = [s for s in story["seats"]
              if s["counts_as_coverage"] and not s["fragment_like"] and s["headline"]]
    pool = usable or [s for s in story["seats"] if s["headline"]]
    if not pool:
        return {"text": None, "source_text_hash": None, "fallbacks": ["no_headline"]}

    by_hash = {}
    for s in sorted(pool, key=lambda s: (-s["points"], s["date"], s["publisher"], s["art_id"])):
        by_hash.setdefault(s["text_hash"], s)
    ordered = list(by_hash.values())
    first = medoid([s["text_hash"] for s in ordered], vectors)
    ordered.sort(key=lambda s: (s["text_hash"] != first,))

    fallbacks = []
    for s in ordered:
        problem = headline_problem(s["headline"])
        if not problem:
            return {"text": s["headline"].strip(), "source_text_hash": s["text_hash"],
                    "fallbacks": fallbacks}
        fallbacks.append(problem)
    # Every candidate was refused: part 2 asks the writer for a headline (Q4s). Until
    # then the least-bad printed one is shown rather than nothing.
    best = ordered[0]
    return {"text": best["headline"].strip(), "source_text_hash": best["text_hash"],
            "fallbacks": fallbacks + ["writer_headline_needed"]}


# ───────────────────────────── one-liner (Q6) ─────────────────────────

def pick_one_liner(story, headline):
    """Stored summary of the highest-points editorial article, skipping fragment/garbled (Q6t)."""
    pool = [s for s in story["seats"]
            if s["counts_as_coverage"] and not s["paid"] and not s["fragment_like"]
            and s["summary"]]
    if not pool:
        return {"text": headline.get("text"), "source_text_hash": None,
                "fallback": "printed_headline"}
    # Tie-break (Claude's call, 1 Nov): among equal-points pieces prefer the one the
    # headline was taken from, so headline and one-liner describe the same article. The
    # brief leaves ties open, and the alternative (alphabetical by paper) is arbitrary.
    chosen = headline.get("source_text_hash")
    best = min(pool, key=lambda s: (-s["points"], s["text_hash"] != chosen, s["date"],
                                    s["publisher"], s["art_id"]))
    return {"text": best["summary"].strip(), "source_text_hash": best["text_hash"],
            "fallback": None}


# ───────────────────────────── rest of the item (Q7) ──────────────────

def page_link(seat):
    """Q5+ (z). The archived PDF and the page to open it at.

    The path is built from `placements.extra["paper"]`, which is the archived file's own
    stem and already carries the edition where there is one ("2026-10-19_Business
    Line_Hyderabad"). NOT from `placements.source_file`: that is the extraction JSON, not
    the PDF (a wrong turn caught by the §3 pass line on 1 Nov). Checked on all 6,662
    seats of run exam-v1: archive/<date>/<paper>.pdf exists for every one.
    The label says the PDF page, never a printed page: the printed->PDF offset is not
    stored anywhere in this database, so a printed number would be a guess (Q5+ z).
    """
    paper = (seat.get("extra") or {}).get("paper")
    pdf = f"archive/{seat['date']}/{paper}.pdf" if paper else None
    return {"pdf": pdf, "pdf_page": seat["page"],
            "label": f"PDF p{seat['page']}" if seat["page"] is not None else None}


def coverage_block(story):
    """7b. One row per covering slot; articles and papers counted separately (K1 B3)."""
    rows = []
    for s in sorted(story["seats"], key=lambda s: (-s["points"], s["date"], s["publisher"],
                                                   s["page"] or 0)):
        if not s["counts_as_coverage"]:
            continue
        rows.append({"paper": s["publisher"], "date": s["date"], "page": s["page"],
                     "page_link": page_link(s), "words": s["words"], "kind": s["kind"],
                     "byline": s["byline"] or None, "headline": s["headline"],
                     "text_hash": s["text_hash"], "points": s["points"]})
    return {"articles": len({r["text_hash"] for r in rows}),
            "papers": len({r["paper"] for r in rows}), "rows": rows}


def views_block(story):
    """7c. Opinion, analysis and interview pieces. Empty list = the line is hidden."""
    kinds = set(KNOBS["views_kinds"])
    out = []
    for s in sorted(story["seats"], key=lambda s: (s["date"], s["publisher"], s["page"] or 0)):
        if s["kind"] in kinds and s["counts_as_coverage"]:
            out.append({"paper": s["publisher"], "date": s["date"], "page": s["page"],
                        "kind": s["kind"], "headline": s["headline"],
                        "page_link": page_link(s)})
    return out


def theme_block(story, topic_counts):
    """7a. The story's topic, how many stories carried it in 21 days, and the trend.

    Rates are per publication WEEK on both sides, so a gap in the archive cannot read as
    a quiet fortnight. With papers on every day the two rates are simply "this week's
    count" and "the earlier weekly average", which is 7a exactly. When the earlier two
    weeks hold no papers at all there is nothing to compare with, and `trend` comes back
    None with the reason — the count is still shown (Kunaal, 1 Nov).
    """
    topics = collections.Counter(s["topic"] for s in story["seats"]
                                 if s["counts_as_coverage"] and s["topic"])
    if not topics:
        return None
    topic = topics.most_common(1)[0][0]
    counts = topic_counts.get(topic)
    if not counts or counts["total"] < KNOBS["theme_min_stories"]:
        return None

    recent_days = topic_counts["__days__"]["recent"]
    earlier_days = topic_counts["__days__"]["earlier"]
    recent_rate = counts["recent"] * 7 / recent_days if recent_days else 0
    earlier_rate = counts["earlier"] * 7 / earlier_days if earlier_days else 0

    trend, no_trend = None, None
    if earlier_days == 0 and KNOBS["theme_trend_needs_earlier_days"]:
        no_trend = "no papers in the earlier weeks"
    elif earlier_rate == 0:
        # Papers were published and carried nothing on this topic: a genuine rise.
        trend = "rising" if counts["recent"] else "steady"
    elif recent_rate >= KNOBS["theme_rising"] * earlier_rate:
        trend = "rising"
    elif recent_rate <= KNOBS["theme_falling"] * earlier_rate:
        trend = "falling"
    else:
        trend = "steady"
    return {"topic": topic, "stories_21d": counts["total"], "trend": trend,
            "no_trend_because": no_trend, "recent_7d": counts["recent"],
            "recent_per_week": round(recent_rate, 2),
            "earlier_per_week": round(earlier_rate, 2),
            "publication_days": {"recent": recent_days, "earlier": earlier_days}}


def topic_counts_over(news, intel, end_date):
    """Stories per topic over the theme window, split into the last 7 days and before.

    Counted in STORIES, not articles (7a): a story is a thread, so a topic running for a
    week is one story, not seven.
    """
    start = _back(end_date, KNOBS["theme_window_days"] - 1)
    recent_start = _back(end_date, 6)
    days = [d for d in stored_dates(news) if start <= d <= end_date]
    seats = load_seats(news, days)
    stories = load_stories(intel, seats)
    counts = collections.defaultdict(lambda: {"total": 0, "recent": 0, "earlier": 0})
    for st in stories:
        cover = [s for s in st["seats"] if s["counts_as_coverage"] and s["topic"]]
        # A lone brief is not a theme: a story needs this many covering articles to count.
        if len({s["text_hash"] for s in cover}) < KNOBS["theme_min_articles_per_story"]:
            continue
        topic = collections.Counter(s["topic"] for s in cover).most_common(1)[0][0]
        counts[topic]["total"] += 1
        if max(s["date"] for s in cover) >= recent_start:
            counts[topic]["recent"] += 1
        else:
            counts[topic]["earlier"] += 1
    # How many days actually carried papers on each side, so the trend compares rates
    # per publication week rather than per calendar week (the archive has gaps).
    counts["__days__"] = {"recent": sum(1 for d in days if d >= recent_start),
                          "earlier": sum(1 for d in days if d < recent_start)}
    return counts


def links_block(story, seat_index, story_of_hash):
    """7f. Printed links only: `see_also_page` and `related_id`, to a DIFFERENT story."""
    out, seen = [], set()
    for s in story["seats"]:
        extra = s["extra"] or {}
        for kind in ("related_id", "see_also_page"):
            value = extra.get(kind)
            if value is None:
                continue
            if kind == "related_id":
                targets = [seat_index["by_art"].get((s["date"], s["publisher"], str(value)))]
            else:
                targets = seat_index["by_page"].get((s["date"], s["publisher"], value), [])
            for target in targets:
                if not target:
                    continue
                other = story_of_hash.get(target["text_hash"])
                if not other or other == story["story_id"]:
                    continue
                key = (kind, other)
                if key in seen:
                    continue
                seen.add(key)
                out.append({"kind": kind, "from": page_link(s), "to_story_id": other,
                            "to_headline": target["headline"]})
    return out


def build_seat_index(seats):
    by_art, by_page = {}, collections.defaultdict(list)
    for s in seats:
        by_art[(s["date"], s["publisher"], str(s["art_id"]))] = s
        by_page[(s["date"], s["publisher"], s["page"])].append(s)
    return {"by_art": by_art, "by_page": by_page}


# ───────────────────────────── building ───────────────────────────────

def build(date, window="day", last_visit=None, news=None, intel=None, vecs=None,
          top_n=None):
    """The digest for one window. Pure read; returns the contract object described above."""
    close = news is None
    news = news or open_ro(NEWS_DB)
    intel = intel or ST.open_intel_ro(INTEL_DB)   # p3_store owns intel.db access
    vecs = vecs or open_ro(VECTORS_DB)
    top_n = top_n or KNOBS["top_n"]

    days = window_dates(news, date, window, last_visit)
    seats = load_seats(news, days)
    stories = load_stories(intel, seats)
    shape = paper_day_shape(seats)
    scored, zero = rank(stories, shape)

    chosen = scored[:top_n]
    vectors = load_vectors(vecs, {s["text_hash"] for st in chosen for s in st["seats"]})
    topic_counts = topic_counts_over(news, intel, date)
    seat_index = build_seat_index(seats)
    story_of_hash = {s["text_hash"]: st["story_id"] for st in stories for s in st["seats"]}

    out_stories, fallback_counts = [], collections.Counter()
    for st in chosen:
        headline = pick_headline(st, vectors)
        one_liner = pick_one_liner(st, headline)
        fallback_counts.update(headline["fallbacks"] or ["none"])
        if one_liner["fallback"]:
            fallback_counts["one_liner_" + one_liner["fallback"]] += 1
        cover_dates = sorted({s["date"] for s in st["seats"] if s["counts_as_coverage"]})
        out_stories.append({
            "story_id": st["story_id"],
            "group_ids": st["group_ids"],
            "score": st["score"],
            "rank": st["rank"],
            "paper_points": st["paper_points"],
            "dates": cover_dates,
            "developing": len(cover_dates) > 1,
            "headline": headline,
            "one_liner": one_liner,
            "theme": theme_block(st, topic_counts),
            "coverage": coverage_block(st),
            "views": views_block(st),
            "caveats": L.story_flags(st["seats"]),
            "links": links_block(st, seat_index, story_of_hash),
            "writeup": None,
        })

    report = tie_report(scored)
    report.update({
        "seats": len(seats), "stories": len(stories),
        "zero_point_stories": len(zero),
        "headline_fallbacks": dict(fallback_counts),
        "headline_fallback_share": round(
            sum(1 for s in out_stories if s["headline"]["fallbacks"]) / max(len(chosen), 1), 3),
        "page_links": sum(len(s["coverage"]["rows"]) for s in out_stories),
        "page_links_missing_pdf": sum(
            1 for s in out_stories for r in s["coverage"]["rows"]
            if not r["page_link"]["pdf"] or not (ROOT / r["page_link"]["pdf"]).exists()),
        "page_offset_stored": False,
    })

    result = {
        "built_at": dt.datetime.now().isoformat(timespec="seconds"),
        "window": {"kind": window, "from": days[0] if days else None,
                   "to": days[-1] if days else None, "days": days},
        "versions": {"label": L.LABEL_VERSION, "grouping": GROUPING_VERSION,
                     "tracking": TRACKING_VERSION, "tag_prompt": L.TAG_PROMPT,
                     "digest": DIGEST_VERSION},
        "knobs": KNOBS,
        "stories": out_stories,
        "dropped": {"zero_point": len(zero), "below_top_n": max(len(scored) - top_n, 0)},
        "report": report,
    }
    if close:
        news.close(); intel.close(); vecs.close()
    return result


# ───────────────────────────── commands ───────────────────────────────

def cmd_build(args):
    if not args.no_llm:
        sys.exit("part 2 (the write-up) is not built yet: pass --no-llm")
    result = build(args.date, args.window, args.last_visit, top_n=args.top_n)
    if args.out:
        Path(args.out).write_text(json.dumps(result, indent=1), encoding="utf-8")
        print(f"wrote {args.out}")
    if args.brief or args.out:
        r = result["report"]
        print(f"{result['window']['kind']} {result['window']['from']}..{result['window']['to']} "
              f"· {len(result['stories'])} stories of {r['stories']} "
              f"({r['zero_point_stories']} zero-point, {result['dropped']['below_top_n']} below top N)")
        print(f"score {r['score_min']}..{r['score_max']} · at top N {r['score_at_top_n']} "
              f"· boundary tie {r['boundary_tie']}"
              + (f" decided by {r['boundary_decided_by']}" if r["boundary_tie"] else ""))
        print(f"headline fallbacks {r['headline_fallbacks']} "
              f"(share {r['headline_fallback_share']}) · page links {r['page_links']}")
        for s in result["stories"]:
            cov = s["coverage"]
            flags = [k for k, v in s["caveats"].items() if v]
            print(f"  {s['rank']:2d}. [{s['score']:2d}] {s['headline']['text']}")
            print(f"      {cov['articles']} articles · {cov['papers']} papers · "
                  f"{'DEVELOPING · ' if s['developing'] else ''}"
                  f"{'theme ' + s['theme']['topic'] + ' (' + s['theme']['trend'] + ') · ' if s['theme'] else ''}"
                  f"{len(s['views'])} views · {len(s['links'])} printed links"
                  + (f" · {', '.join(flags)}" if flags else ""))
    elif not args.out:
        print(json.dumps(result, indent=1))
    return 0


def main():
    p = argparse.ArgumentParser(description="Digest v1 part 1 (no LLM)")
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--date", required=True)
    b.add_argument("--window", default="day", choices=("day", "week", "since_last"))
    b.add_argument("--last-visit", dest="last_visit", default=None)
    b.add_argument("--top-n", dest="top_n", type=int, default=None)
    b.add_argument("--out", default=None)
    b.add_argument("--brief", action="store_true", help="one line per story instead of JSON")
    b.add_argument("--no-llm", dest="no_llm", action="store_true", required=False)
    b.set_defaults(fn=cmd_build)
    args = p.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
