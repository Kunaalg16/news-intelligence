"""
p2_embed.py -- 2.4 Embed: one vector per article text, per model and input variant (v0.2, 24 Oct 2026: + Voyage pacing)

WHAT IT DOES
  Reads article texts from data/news.db (READ-ONLY; never changes it) and writes vectors to
  data/vectors.db, a separate file that can be deleted and rebuilt at any time.
  One row per text_hash + model + variant. Vectors are float32, full length, L2-normalised.

WHY
  The pair test (drafts/2026-10-24_2.4_pair_test_protocol.md) compares models and input texts.
  Every model x variant must score every pair, so every text gets every vector. The winner later
  becomes the daily 2.4 step; the losers' vectors can then be dropped.

MODELS (MODELS below)            VARIANTS (build_input below)
  minilm      all-MiniLM-L6-v2     V0   headline only (diagnostic)
  bge-m3      BAAI/bge-m3          V1   headline + deck + first 400 words of the body
  qwen3-0.6b  Qwen3-Embedding-0.6B V2   headline + deck + full body
  voyage-4    API (MongoDB Atlas)  V1i  Qwen3 only: V1 with a task instruction in front
  Deck: taken from the earliest seat of the text in the run (date, then paper, then seat), the
  same "earliest seat wins" rule as the store.

HOW TO RUN (always from ~/news-intel, with the EMBEDDING environment)
  .venv-embed/bin/python src/p2_embed.py plan                          what exists, what is missing, input sizes
  .venv-embed/bin/python src/p2_embed.py run --model minilm --variant V1 [--limit 50]
  .venv-embed/bin/python src/p2_embed.py check                         counts per model x variant, norms, NaNs
  Options: --run exam-v1 · --batch 32 · --limit N (only the first N missing texts; for trials)
  Voyage pacing (v0.2): --tpm T --rpm R  keeps every rolling minute under T tokens and R requests
    (measure T and R with src/p2_dev_voyage_limits.py); batches are packed to <= 90% of T.

SAFETY
  news.db is opened with SQLite's read-only mode (file:...?mode=ro): a write would raise an error.
  Idempotent: a vector that exists is never computed again; a re-run fills only what is missing.
  Saved every batch: Ctrl+C loses at most one batch.
  Local models load with trust_remote_code=False (no downloaded code runs).
  Voyage: the key is read from ~/.config/news-intel/.env by a small parser that never prints it;
    errors show only the HTTP status code. Voyage's free tier covers a full pass many times over.
"""
import argparse, datetime as dt, json, os, sqlite3, sys, time, urllib.error, urllib.request
from pathlib import Path

import numpy as np

NEWS_DB = Path("data") / "news.db"
VEC_DB = Path("data") / "vectors.db"
ENV_FILE = Path.home() / ".config" / "news-intel" / ".env"
VOYAGE_URL = os.environ.get("NI_VOYAGE_URL", "https://ai.mongodb.com/v1/embeddings")  # override only for tests
FAKE = os.environ.get("NI_EMBED_FAKE") == "1"          # rehearsal only: deterministic fake vectors, no torch

MODELS = {
    "minilm":     {"kind": "local", "name": "sentence-transformers/all-MiniLM-L6-v2", "revision": None},
    "bge-m3":     {"kind": "local", "name": "BAAI/bge-m3", "revision": None},
    "qwen3-0.6b": {"kind": "local", "name": "Qwen/Qwen3-Embedding-0.6B", "revision": None},
    "voyage-4":   {"kind": "api",   "name": "voyage-4", "revision": None},
}
VARIANTS = ["V0", "V1", "V2", "V1i"]
V1_WORDS = 400
QWEN_INSTRUCTION = ("Instruct: Given a newspaper article, represent the news event it reports "
                    "so that articles about the same event are close\nQuery: ")
SCHEMA = [
    "CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT)",
    "CREATE TABLE IF NOT EXISTS vectors (text_hash TEXT, model TEXT, variant TEXT, dim INTEGER, vec BLOB, "
    "input_words INTEGER, embedded_at TEXT, PRIMARY KEY (text_hash, model, variant))",
    "CREATE TABLE IF NOT EXISTS log (at TEXT, model TEXT, variant TEXT, run_id TEXT, done INTEGER, "
    "seconds REAL, api_tokens INTEGER, note TEXT)",
]


def now():
    return dt.datetime.now().isoformat(timespec="seconds")


# ---------- databases ----------
def open_news():
    if not NEWS_DB.exists():
        sys.exit(f"{NEWS_DB} not found. Run from ~/news-intel.")
    return sqlite3.connect(f"file:{NEWS_DB}?mode=ro", uri=True)


def open_vectors():
    VEC_DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(VEC_DB)
    for s in SCHEMA:
        con.execute(s)
    con.execute("INSERT OR IGNORE INTO meta VALUES ('schema_version', '1')")
    con.commit()
    return con


def load_texts(news, run_id):
    """Every distinct text in the run, with the deck of its earliest seat."""
    rows = news.execute(
        "SELECT t.text_hash, t.headline, t.body, p.deck FROM texts t JOIN placements p ON p.text_hash = t.text_hash "
        "WHERE p.run_id = ? ORDER BY t.text_hash, p.date, p.publisher, p.art_id", (run_id,)).fetchall()
    out = {}
    for h, head, body, deck in rows:
        if h not in out:                                   # first row per text = earliest seat
            out[h] = (head or "", body or "", deck or "")
    if not out:
        sys.exit(f"No texts in run '{run_id}'.")
    return out


# ---------- input variants ----------
def build_input(variant, head, body, deck):
    if variant == "V0":
        return head or body[:200]                          # a few texts have no headline
    lead = "\n".join(x for x in (head, deck) if x)
    if variant in ("V1", "V1i"):
        body = " ".join(body.split()[:V1_WORDS])
    text = (lead + "\n\n" + body).strip()
    return QWEN_INSTRUCTION + text if variant == "V1i" else text


# ---------- encoders ----------
def fake_encoder(dim=16):
    """Rehearsal only: a vector from a hash of the text; same text -> same vector."""
    import hashlib
    def enc(texts):
        out = []
        for t in texts:
            seed = int(hashlib.sha1(t.encode("utf-8")).hexdigest()[:8], 16)
            out.append(np.random.default_rng(seed).standard_normal(dim))
        return np.array(out, dtype=np.float32), 0
    return enc


def local_encoder(spec):
    import torch
    from sentence_transformers import SentenceTransformer
    device = "mps" if torch.backends.mps.is_available() else "cpu"
    m = SentenceTransformer(spec["name"], device=device, revision=spec["revision"], trust_remote_code=False)
    print(f"loaded {spec['name']} on {device}; reads up to {m.max_seq_length} tokens per text")
    def enc(texts):
        return m.encode(texts, batch_size=len(texts), normalize_embeddings=True,
                        convert_to_numpy=True).astype(np.float32), 0
    return enc


def read_key(name):
    """Read one NAME=value line from the key file. Never prints the value."""
    if not ENV_FILE.exists():
        sys.exit(f"Key file {ENV_FILE} not found.")
    for line in ENV_FILE.read_text().splitlines():
        k, sep, v = line.partition("=")
        if sep and k.strip() == name:
            return v.strip().strip('"').strip("'")
    sys.exit(f"{name} not found in the key file (variable names only are checked).")


def voyage_encoder(spec):
    key = read_key("VOYAGE_API_KEY")
    def enc(texts):
        body = json.dumps({"input": texts, "model": spec["name"], "input_type": "document"}).encode("utf-8")
        for attempt in range(6):
            req = urllib.request.Request(VOYAGE_URL, data=body, method="POST",
                                         headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=120) as r:
                    data = json.load(r)
                data["data"].sort(key=lambda d: d["index"])
                return (np.array([d["embedding"] for d in data["data"]], dtype=np.float32),
                        int(data.get("usage", {}).get("total_tokens", 0)))
            except urllib.error.HTTPError as e:            # status code only; never the request
                if e.code in (429, 500, 502, 503, 504) and attempt < 5:
                    wait = 2 ** attempt * 5 if not os.environ.get("NI_FAST_RETRY") else 0
                    print(f"voyage: HTTP {e.code}, retrying in {wait}s")
                    time.sleep(wait)
                    continue
                sys.exit(f"voyage: HTTP {e.code}; stopped. Vectors saved so far are kept.")
            except urllib.error.URLError as e:
                if attempt < 5:
                    time.sleep(2 ** attempt * 5)
                    continue
                sys.exit(f"voyage: network error ({type(e.reason).__name__}); stopped.")
    return enc


def get_encoder(model):
    spec = MODELS[model]
    if FAKE:
        print("NI_EMBED_FAKE=1: fake vectors (rehearsal only)")
        return fake_encoder()
    return voyage_encoder(spec) if spec["kind"] == "api" else local_encoder(spec)


# ---------- pacing (API only) ----------
def est_tokens(text):
    """Cautious token estimate for planning (Voyage measured ~1 token per word on our text; 1.3x + 8 margin)."""
    return int(len(text.split()) * 1.3) + 8


def make_batches(todo, inputs, size, tpm):
    """Consecutive batches of at most `size` texts; with --tpm, also at most 90% of the per-minute token budget."""
    out, cur, cur_tok = [], [], 0
    cap = int(tpm * 0.9) if tpm else None
    for h in todo:
        t = est_tokens(inputs[h])
        if cap and t > cap:
            sys.exit(f"one text needs about {t} tokens, more than 90% of --tpm {tpm}; raise --tpm or use a shorter variant")
        if cur and (len(cur) >= size or (cap and cur_tok + t > cap)):
            out.append(cur)
            cur, cur_tok = [], 0
        cur.append(h)
        cur_tok += t
    if cur:
        out.append(cur)
    return out


class Throttle:
    """Sliding 60-second window: before each request, wait until it fits under both limits.
    Concept: client-side rate limiting (a leaky/token bucket kept by the caller)."""
    def __init__(self, tpm, rpm, window=None):
        self.tpm, self.rpm, self.window = tpm, rpm, window or float(os.environ.get("NI_WINDOW", 61))
        self.sent = []                                     # (time, tokens) of requests in the window

    def wait(self, tokens):
        while True:
            cutoff = time.time() - self.window
            self.sent = [(t, k) for t, k in self.sent if t > cutoff]
            used = sum(k for _, k in self.sent)
            ok_t = not self.tpm or used + tokens <= self.tpm
            ok_r = not self.rpm or len(self.sent) < self.rpm
            if ok_t and ok_r:
                return
            time.sleep(max(0.2, self.sent[0][0] - cutoff + 0.05) if self.sent else 0.2)

    def record(self, tokens):
        self.sent.append((time.time(), tokens))


# ---------- commands ----------
def missing(vec, texts, model, variant):
    have = {h for (h,) in vec.execute("SELECT text_hash FROM vectors WHERE model=? AND variant=?", (model, variant))}
    return [h for h in texts if h not in have]


def cmd_plan(args):
    texts = load_texts(open_news(), args.run)
    vec = open_vectors()
    print(f"run {args.run}: {len(texts)} texts")
    for v in VARIANTS:
        words = [len(build_input(v, *texts[h]).split()) for h in texts]
        print(f"  input {v:4}: average {np.mean(words):.0f} words, longest {max(words)}, "
              f"over 190 words {sum(w > 190 for w in words)}")
    print("vectors missing (model x variant):")
    for m in MODELS:
        vs = [v for v in VARIANTS if v != "V1i" or m == "qwen3-0.6b"]
        print("  " + m.ljust(11) + "  ".join(f"{v} {len(missing(vec, texts, m, v))}" for v in vs))


def cmd_run(args):
    if args.variant == "V1i" and args.model != "qwen3-0.6b":
        sys.exit("V1i (instruction) is for qwen3-0.6b only.")
    texts = load_texts(open_news(), args.run)
    vec = open_vectors()
    todo = missing(vec, texts, args.model, args.variant)
    if args.limit:
        todo = todo[:args.limit]
    print(f"{args.model} {args.variant}: {len(texts)} texts in {args.run}, {len(todo)} to embed")
    if not todo:
        return
    inputs = {h: build_input(args.variant, *texts[h]) for h in todo}
    todo.sort(key=lambda h: len(inputs[h]))                # similar lengths together: less padding, faster
    batches = make_batches(todo, inputs, args.batch, args.tpm)
    pace = Throttle(args.tpm, args.rpm) if (args.tpm or args.rpm) else None
    if pace:
        est = sum(est_tokens(inputs[h]) for h in todo)
        mins = max(est / args.tpm if args.tpm else 0, len(batches) / args.rpm if args.rpm else 0)
        print(f"paced: {len(batches)} requests, about {est} tokens -> at least {mins:.0f} min "
              f"(limits {args.tpm or '-'} tokens/min, {args.rpm or '-'} requests/min)")
    enc = get_encoder(args.model)
    t0, done, tokens = time.time(), 0, 0
    try:
        for i, chunk in enumerate(batches):
            if pace:
                pace.wait(sum(est_tokens(inputs[h]) for h in chunk))
            arr, tok = enc([inputs[h] for h in chunk])
            if pace:
                pace.record(tok or sum(est_tokens(inputs[h]) for h in chunk))
            if arr.shape[0] != len(chunk) or not np.isfinite(arr).all():
                sys.exit(f"bad output for batch {i}: shape {arr.shape}; stopped, nothing of it saved")
            arr = arr / np.linalg.norm(arr, axis=1, keepdims=True)
            t = now()
            vec.executemany("INSERT OR REPLACE INTO vectors VALUES (?,?,?,?,?,?,?)",
                            [(h, args.model, args.variant, arr.shape[1], arr[j].astype(np.float32).tobytes(),
                              len(inputs[h].split()), t) for j, h in enumerate(chunk)])
            vec.commit()
            done += len(chunk)
            tokens += tok
            if (i + 1) % (5 if pace else 20) == 0 or done == len(todo):
                print(f"  {done}/{len(todo)}  {time.time() - t0:.0f}s")
    finally:
        vec.execute("INSERT INTO log VALUES (?,?,?,?,?,?,?,?)",
                    (now(), args.model, args.variant, args.run, done, round(time.time() - t0, 1), tokens,
                     "fake" if FAKE else ""))
        vec.commit()
    print(f"done: {done} vectors in {time.time() - t0:.0f}s" + (f", {tokens} API tokens" if tokens else ""))


def cmd_check(args):
    texts = load_texts(open_news(), args.run)
    vec = open_vectors()
    print(f"{VEC_DB}: run {args.run} has {len(texts)} texts")
    for m, v, n, dim in vec.execute("SELECT model, variant, COUNT(*), MIN(dim) FROM vectors GROUP BY 1, 2 ORDER BY 1, 2"):
        blobs = [np.frombuffer(b, dtype=np.float32) for (b,) in
                 vec.execute("SELECT vec FROM vectors WHERE model=? AND variant=?", (m, v))]
        norms = np.array([np.linalg.norm(b) for b in blobs])
        bad = sum(not np.isfinite(b).all() for b in blobs)
        dims = {len(b) for b in blobs}
        stray = n - len([1 for (h,) in vec.execute("SELECT text_hash FROM vectors WHERE model=? AND variant=?", (m, v))
                         if h in texts])
        print(f"  {m:11} {v:4} {n:>6} vectors · dim {sorted(dims)} · norm {norms.min():.4f}-{norms.max():.4f} · "
              f"NaN/inf {bad} · not in this run {stray}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["plan", "run", "check"])
    ap.add_argument("--model", choices=list(MODELS))
    ap.add_argument("--variant", choices=VARIANTS)
    ap.add_argument("--run", default="exam-v1")
    ap.add_argument("--batch", type=int, default=32)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--tpm", type=int, default=0, help="API pacing: tokens per minute (from p2_dev_voyage_limits.py)")
    ap.add_argument("--rpm", type=int, default=0, help="API pacing: requests per minute")
    args = ap.parse_args()
    if args.command == "run" and not (args.model and args.variant):
        ap.error("run needs --model and --variant")
    {"plan": cmd_plan, "run": cmd_run, "check": cmd_check}[args.command](args)


if __name__ == "__main__":
    main()
