"""p3_index.py — Phase 3 · the search index (`chunk-1`). Builds `data/search.db`.
  v0.1 1 Nov 2026 (K3 B1-5): from K3 build brief 1 §B1-5 and the K3 note Q10.

WHAT IT DOES
  Cuts every stored article into passages and indexes them twice: a word index (FTS5)
  and a vector index (Qwen3-Embedding-0.6B). One command builds the file, one keeps it
  up to date, one checks it.

      .venv-embed/bin/python src/p3_index.py build            everything, from scratch
      .venv-embed/bin/python src/p3_index.py update           only texts not yet indexed
      .venv-embed/bin/python src/p3_index.py check            counts, smoke query, totals
  Options: --sample N (the first N texts, for a trial run) · --fake (a hash-based encoder,
  no model loaded, for rehearsal and tests) · --db PATH.
  `build` and `update` need `.venv-embed` because they load the embedding model; `check`
  and anything with --fake run in either environment.

WHY PASSAGES AND NOT ARTICLES
  Find by passage, cite the article, show the story (Q10, small-to-big retrieval). A
  1,000-word article averaged into one vector says little about any of its parts --
  representation dilution -- and M4 measured 25% of all body words sitting past word 400,
  so an article-level vector misses a quarter of the text. Passages fix that for finding;
  the citation and the display still happen at article and story level, decided at
  question time by `p3_label.py` (schema-on-read), so nothing has to be re-indexed when a
  labelling rule changes. Every kind is indexed, notices and paid features included
  (Q10d): what a question may see or cite is a read-time decision, not an index one.

WHY TWO INDEXES
  They fail differently (Q10c). The word index finds a name or a figure typed exactly as
  the paper printed it and cannot find a paraphrase; the vector index finds a paraphrase
  and is poor at rare proper nouns. Both are built in v1 and fused at question time.
  FTS5 is an EXTERNAL-CONTENT table: it stores the index but not a second copy of the
  text, and reads the words back from `passages` by rowid. The headline is its own column
  so a hit there can be weighted above a hit in the body.

THE RECIPE (Q10b, knobs below)
  - 300 words or fewer: one passage, uncut.
  - Longer: windows of about 250 words stepping 200, so neighbouring passages share about
    50 words. A window always ends at a sentence end, never mid-sentence.
  - A last window carrying fewer than 80 NEW words is merged into the one before it,
    rather than left as a scrap that matches nothing.
  - Each passage is stored with a header line "paper · date · headline", because a window
    cut from the middle of an article otherwise has no idea what it is about. The paper and
    date are those of the article's earliest printing.
  Sentence splitting uses the fixed-width look-behind form: Python requires every branch
  of a look-behind to be the same width, which is why this is written as two alternatives
  rather than one optional group (M4, a slip caught there).

DATA (one owner per file)
  reads   data/news.db    (read-only)   texts of run exam-v1
  writes  data/search.db                passages · passages_fts · passage_vectors · meta
  `search.db` is K3's, derived, and NOT backed up (Q10e): it can be rebuilt from
  `news.db` with one command, so it is grouped by how it changes, not by what it feeds.

KNOBS
  One block below with reasons. A recipe change bumps CHUNK_VERSION, which is stored in
  the file; `update` refuses to add passages cut by a different recipe to the same index.
"""

import argparse
import array
import hashlib
import re
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NEWS_DB = ROOT / "data" / "news.db"
SEARCH_DB = ROOT / "data" / "search.db"

CHUNK_VERSION = "chunk-1"
RUN_ID = "exam-v1"

# The same model and the same pinned commit as the article vectors (p2_embed MODELS),
# so passage and article vectors live in one space and can be compared if ever needed.
EMBED_NAME = "Qwen/Qwen3-Embedding-0.6B"
EMBED_REVISION = "97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3"
EMBED_SHORT = "qwen3-0.6b"
EMBED_VARIANT = "P1"          # P for passage; V0/V1/V2/V1i are the article variants

# ------------------------------------------------------------------------- KNOBS

KNOBS = {
    # An article this short says one thing; cutting it only splits the thing in two.
    # M4: median text is 224 words, so most articles are a single passage.
    "single_passage_limit": 300,
    # Window and step. M4 projected 11,933 passages at 250/50 counting overlap and
    # 11,284 counting new words only, about 1.7 per text -- the brief's expectation.
    # Tuned only on the Q17 question set (recall@k); kept unless a recipe clearly wins.
    "window_words": 250,
    "step_words": 200,
    # A scrap at the end matches nothing on its own and dilutes the article's coverage.
    "tail_min_new_words": 80,
    # FTS5 bm25 weights at query time: a hit in the headline counts for more than a hit
    # in the body. Placed guess, to be set on the Q17 question set.
    "headline_weight": 10.0,
    "body_weight": 1.0,
    # No passage-side instruction (Q10c): the 2.4 choice embedded documents plain and put
    # the instruction on the query side only. Changing this would need a re-embed.
    "passage_instruction": None,
    "embed_batch": 32,
}

# Fixed-width look-behind: ". " and ".\" " are handled as two branches of the same width
# each, because Python rejects a variable-width look-behind (M4).
SENTENCE_RE = re.compile(r'(?:(?<=[.!?])|(?<=[.!?]["\'”’]))\s+(?=[A-Z"\'“‘(])')

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS passages (
    passage_id INTEGER PRIMARY KEY,   -- FTS5 rowid for this passage
    text_hash TEXT NOT NULL,          -- the article it was cut from (cite this)
    ord INTEGER NOT NULL,             -- 0, 1, 2 ... within that article
    start_word INTEGER NOT NULL,      -- where it starts in the body, in words
    words INTEGER NOT NULL,           -- how many body words it carries
    headline TEXT,                    -- indexed as its own weighted FTS column
    text TEXT NOT NULL,               -- the header line + the body words
    UNIQUE (text_hash, ord)
);
CREATE INDEX IF NOT EXISTS passages_hash ON passages (text_hash);

-- External content: the index lives here, the words stay in `passages`.
CREATE VIRTUAL TABLE IF NOT EXISTS passages_fts USING fts5(
    headline, text,
    content='passages', content_rowid='passage_id',
    tokenize='porter unicode61'
);

CREATE TABLE IF NOT EXISTS passage_vectors (
    passage_id INTEGER PRIMARY KEY,
    dim INTEGER NOT NULL,
    vec BLOB NOT NULL,                -- float32, L2-normalised, as in vectors.db
    model TEXT NOT NULL, variant TEXT NOT NULL, embedded_at TEXT
);
"""


# ───────────────────────────── the recipe ─────────────────────────────

def sentences(text):
    """The text split at sentence ends. One element when nothing looks like a break."""
    return [s for s in SENTENCE_RE.split(text or "") if s.strip()]


def chunk_spans(body):
    """Word spans [(start, end)] for one article body, cut at sentence ends.

    Returns [] for an empty body and [(0, n)] for anything at or under the
    single-passage limit. Spans overlap by about (window - step) words.
    """
    words = (body or "").split()
    n = len(words)
    if n == 0:
        return []
    if n <= KNOBS["single_passage_limit"]:
        return [(0, n)]

    sents = sentences(body)
    starts, at = [], 0
    for s in sents:
        starts.append(at)
        at += len(s.split())
    starts.append(n)                      # sentinel: where the last sentence ends

    spans, i = [], 0
    while i < len(sents):
        begin = starts[i]
        j = i
        # take whole sentences until the window reaches its size; the sentence that
        # crosses the line is included, so a window never ends mid-sentence
        while j < len(sents) and starts[j + 1] - begin < KNOBS["window_words"]:
            j += 1
        end = starts[min(j + 1, len(sents))]
        spans.append((begin, end))
        if end >= n:
            break
        target = begin + KNOBS["step_words"]
        nxt = i + 1
        while nxt < len(sents) and starts[nxt] < target:
            nxt += 1
        i = max(nxt, i + 1)

    # a final scrap merges back into the window before it (Q10b)
    if len(spans) >= 2:
        (prev_begin, prev_end), (_, last_end) = spans[-2], spans[-1]
        if last_end - prev_end < KNOBS["tail_min_new_words"]:
            spans[-2] = (prev_begin, last_end)
            spans.pop()
    return spans


def header(paper, date, headline):
    """The context line every passage carries (Q10b)."""
    return " · ".join(x for x in (paper, date, headline) if x)


def passages_for(text_hash, headline, body, paper, date):
    """Every passage of one article, ready to store."""
    words = (body or "").split()
    head_line = header(paper, date, headline)
    out = []
    for ord_, (begin, end) in enumerate(chunk_spans(body)):
        piece = " ".join(words[begin:end])
        out.append({"text_hash": text_hash, "ord": ord_, "start_word": begin,
                    "words": end - begin, "headline": headline or "",
                    "text": f"{head_line}\n\n{piece}" if head_line else piece})
    return out


# ───────────────────────────── reading news.db ────────────────────────

def open_ro(path):
    if not Path(path).exists():
        sys.exit(f"missing {path} (run from ~/news-intel)")
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def load_texts(news, sample=None, only=None):
    """Every text of the run, with the paper and date of its EARLIEST printing.

    The earliest seat is the same convention `p2_tag` and the M1 measurement use, so the
    header on a passage says the same thing those reports would.
    """
    seat = {}
    for h, date, pub in news.execute(
            "SELECT text_hash, date, publisher FROM placements WHERE run_id = ? "
            "ORDER BY date, publisher, art_id", (RUN_ID,)):
        seat.setdefault(h, (date, pub))
    rows = []
    for h, head, body in news.execute(
            "SELECT text_hash, headline, body FROM texts ORDER BY text_hash"):
        if h not in seat:
            continue                      # not printed in this run
        if only is not None and h not in only:
            continue
        date, pub = seat[h]
        rows.append({"text_hash": h, "headline": head or "", "body": body or "",
                     "paper": pub, "date": date})
        if sample and len(rows) >= sample:
            break
    return rows


# ───────────────────────────── encoders ───────────────────────────────

def fake_encoder(dim=16):
    """Rehearsal only: a vector from a hash of the text, L2-normalised. Same shape as the
    real one, so every step but the model's quality can be tested without loading it."""
    def enc(texts):
        out = []
        for t in texts:
            seed = hashlib.sha1(t.encode("utf-8")).digest()
            raw = [((seed[i % len(seed)] / 255.0) - 0.5) for i in range(dim)]
            norm = sum(x * x for x in raw) ** 0.5 or 1.0
            out.append(array.array("f", [x / norm for x in raw]).tobytes())
        return out, dim
    return enc


def real_encoder():
    """Qwen3-Embedding-0.6B, pinned, offline, no downloaded code (same as p2_embed)."""
    import os
    os.environ.setdefault("HF_HUB_OFFLINE", "1")      # use the cached files, fetch nothing
    import numpy as np
    import torch
    from sentence_transformers import SentenceTransformer
    device = "mps" if torch.backends.mps.is_available() else "cpu"
    model = SentenceTransformer(EMBED_NAME, device=device, revision=EMBED_REVISION,
                                trust_remote_code=False)
    print(f"loaded {EMBED_NAME} @ {EMBED_REVISION[:7]} on {device}; "
          f"reads up to {model.max_seq_length} tokens per passage")

    def enc(texts):
        vecs = model.encode(texts, batch_size=min(len(texts), KNOBS["embed_batch"]),
                            normalize_embeddings=True, convert_to_numpy=True)
        vecs = vecs.astype(np.float32)
        return [v.tobytes() for v in vecs], int(vecs.shape[1])
    return enc


# ───────────────────────────── writing search.db ──────────────────────

def open_search(path=SEARCH_DB, fresh=False):
    path = Path(path)
    if fresh and path.exists():
        path.unlink()
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    row = con.execute("SELECT value FROM meta WHERE key = 'chunk_version'").fetchone()
    if row is None:
        con.execute("INSERT INTO meta VALUES ('chunk_version', ?)", (CHUNK_VERSION,))
        con.execute("INSERT OR REPLACE INTO meta VALUES ('embed_model', ?)",
                    (f"{EMBED_SHORT}/{EMBED_VARIANT}@{EMBED_REVISION[:7]}",))
        con.commit()
    elif row[0] != CHUNK_VERSION:
        sys.exit(f"{path} was cut by recipe {row[0]}, this code is {CHUNK_VERSION}; "
                 f"run `build` to rebuild it rather than mixing recipes")
    return con


def store(con, rows, encode):
    """Cut, index and embed one batch of articles. Idempotent per article."""
    made = 0
    for row in rows:
        pieces = passages_for(row["text_hash"], row["headline"], row["body"],
                              row["paper"], row["date"])
        if not pieces:
            continue
        con.execute("DELETE FROM passages WHERE text_hash = ?", (row["text_hash"],))
        ids = []
        for p in pieces:
            cur = con.execute(
                "INSERT INTO passages (text_hash, ord, start_word, words, headline, text) "
                "VALUES (?,?,?,?,?,?)",
                (p["text_hash"], p["ord"], p["start_word"], p["words"], p["headline"],
                 p["text"]))
            ids.append(cur.lastrowid)
        # external content: the index has to be told, it does not read inserts itself
        for pid, p in zip(ids, pieces):
            con.execute("INSERT INTO passages_fts (rowid, headline, text) VALUES (?,?,?)",
                        (pid, p["headline"], p["text"]))
        blobs, dim = encode([p["text"] for p in pieces])
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        con.executemany(
            "INSERT OR REPLACE INTO passage_vectors (passage_id, dim, vec, model, variant, "
            "embedded_at) VALUES (?,?,?,?,?,?)",
            [(pid, dim, blob, EMBED_SHORT, EMBED_VARIANT, now)
             for pid, blob in zip(ids, blobs)])
        made += len(pieces)
    con.commit()
    return made


def indexed_hashes(con):
    return {r[0] for r in con.execute("SELECT DISTINCT text_hash FROM passages")}


# ───────────────────────────── commands ───────────────────────────────

def _encoder(args):
    return fake_encoder() if args.fake else real_encoder()


def cmd_build(args):
    news = open_ro(NEWS_DB)
    con = open_search(args.db, fresh=True)
    rows = load_texts(news, sample=args.sample)
    encode = _encoder(args)
    t0, made = time.time(), 0
    for i in range(0, len(rows), 50):
        made += store(con, rows[i:i + 50], encode)
        if not args.quiet:
            print(f"  {min(i + 50, len(rows))}/{len(rows)} texts · {made} passages", end="\r")
    print(f"\nbuilt {args.db}: {len(rows)} texts → {made} passages "
          f"({made / max(len(rows), 1):.2f} per text) in {time.time() - t0:.0f}s"
          f"{' [FAKE vectors]' if args.fake else ''}")
    report(con, news)
    return 0


def cmd_update(args):
    news = open_ro(NEWS_DB)
    con = open_search(args.db)
    have = indexed_hashes(con)
    rows = [r for r in load_texts(news, sample=args.sample) if r["text_hash"] not in have]
    if not rows:
        print(f"{args.db}: nothing new ({len(have)} texts already indexed) — no-op")
        return 0
    encode = _encoder(args)
    made = 0
    for i in range(0, len(rows), 50):
        made += store(con, rows[i:i + 50], encode)
    print(f"{args.db}: added {len(rows)} texts → {made} passages "
          f"({len(have) + len(rows)} texts indexed in all)"
          f"{' [FAKE vectors]' if args.fake else ''}")
    return 0


SMOKE_QUERIES = ["tariff", "police", "bank"]


def report(con, news=None):
    """Counts, the smoke query and the totals that have to agree."""
    passages = con.execute("SELECT COUNT(*) FROM passages").fetchone()[0]
    texts = con.execute("SELECT COUNT(DISTINCT text_hash) FROM passages").fetchone()[0]
    vectors = con.execute("SELECT COUNT(*) FROM passage_vectors").fetchone()[0]
    dims = [r[0] for r in con.execute("SELECT DISTINCT dim FROM passage_vectors")]
    per_text = passages / texts if texts else 0
    sizes = [r[0] for r in con.execute("SELECT words FROM passages ORDER BY words")]
    multi = con.execute("SELECT COUNT(*) FROM (SELECT text_hash FROM passages "
                        "GROUP BY 1 HAVING COUNT(*) > 1)").fetchone()[0]
    over = con.execute("SELECT COUNT(*) FROM passages WHERE words > ?",
                       (KNOBS["window_words"] * 2,)).fetchone()[0]
    print(f"{CHUNK_VERSION} · texts {texts} · passages {passages} "
          f"({per_text:.2f} per text) · vectors {vectors} · dims {dims}")
    if sizes:
        print(f"  passage words: min {sizes[0]} · median {sizes[len(sizes) // 2]} · "
              f"max {sizes[-1]} · cut into more than one passage: {multi} texts · "
              f"longer than twice the window (one long sentence): {over}")
    hits = {}
    for q in SMOKE_QUERIES:
        hits[q] = con.execute(
            "SELECT COUNT(*) FROM passages_fts WHERE passages_fts MATCH ?", (q,)).fetchone()[0]
    print(f"  FTS smoke query {hits}")
    ranked = con.execute(
        "SELECT p.text_hash, bm25(passages_fts, ?, ?) AS score FROM passages_fts "
        "JOIN passages p ON p.passage_id = passages_fts.rowid "
        "WHERE passages_fts MATCH ? ORDER BY score LIMIT 3",
        (KNOBS["headline_weight"], KNOBS["body_weight"], SMOKE_QUERIES[0])).fetchall()
    print(f"  bm25 with the headline weighted returns {len(ranked)} rows for "
          f"'{SMOKE_QUERIES[0]}'")
    ok = {
        "vectors = passages": vectors == passages,
        "one vector size": len(dims) <= 1,
        "every passage has an article": con.execute(
            "SELECT COUNT(*) FROM passages WHERE text_hash IS NULL OR text_hash = ''"
        ).fetchone()[0] == 0,
        "FTS finds rows": any(v > 0 for v in hits.values()),
        "bm25 ranks": len(ranked) > 0,
    }
    for name, good in ok.items():
        print(f"  {'ok  ' if good else 'FAIL'} {name}")
    return all(ok.values()), {"texts": texts, "passages": passages, "per_text": per_text,
                              "vectors": vectors}


def cmd_check(args):
    con = open_search(args.db)
    good, _ = report(con)
    print("PASS" if good else "FAIL")
    return 0 if good else 1


def main():
    p = argparse.ArgumentParser(description="the search index (passages + FTS5 + vectors)")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name, fn in (("build", cmd_build), ("update", cmd_update), ("check", cmd_check)):
        s = sub.add_parser(name)
        s.add_argument("--db", default=str(SEARCH_DB))
        s.add_argument("--sample", type=int, default=None,
                       help="only the first N texts (a trial run)")
        s.add_argument("--fake", action="store_true",
                       help="hash-based vectors, no model loaded (rehearsal and tests)")
        s.add_argument("--quiet", action="store_true")
        s.set_defaults(fn=fn)
    args = p.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
