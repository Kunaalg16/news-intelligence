"""p3_embedsvc.py — Phase 3 · the local embedding service (K3 schema-free, no database).
  v0.1 3 Nov 2026 (K3 B2-4, first half): from K3 build brief 2 §B2-4 and the note Q14 (r).

WHAT IT DOES
  Holds Qwen3-Embedding-0.6B in memory and turns text into vectors over HTTP, on
  127.0.0.1 only. Two endpoints:

      POST /embed   {"texts": ["..."], "is_query": true}   -> {"vectors": [[...]], ...}
      GET  /health                                         -> {"ok": true, ...}

  Run it in `.venv-embed`, which is the environment that has the model:

      .venv-embed/bin/python src/p3_embedsvc.py                    127.0.0.1:8766
      .venv-embed/bin/python src/p3_embedsvc.py --stub             no model, for tests
      .venv-embed/bin/python src/p3_embedsvc.py --port 8767        another port

WHY IT EXISTS AT ALL (Q14 r)
  The app (`p3_app.py`) and the search layer (`p3_search.py`) run in `.venv`, which has
  fastapi, numpy and the anthropic client but NO embedding model -- and `p3_index.py`
  needs `.venv-embed`, which has sentence-transformers and torch but no fastapi. So the
  process that receives a question cannot embed it in-process. Three ways out were on the
  table: move the app into `.venv-embed`, merge the two environments, or put the model
  behind a tiny local service. The service was chosen (Q14 r) because it keeps one model
  in memory across questions -- loading Qwen takes tens of seconds, which no reader will
  wait for per search -- and because it leaves both environments as they are, so the
  index can keep being built by the code that already works.

  It is built on the standard library's `http.server`. That is not laziness: a new package
  needs Kunaal's yes and his hands (CLAUDE.md §4), `httpx` is not installed in `.venv`
  either, and a service with one endpoint on the loopback interface is exactly the size
  `http.server` is adequate for.

THE ONE THING THAT MUST NOT BE GOT WRONG
  The query side gets Qwen's retrieval instruction; the passage side gets none. That is
  not a preference, it is what matches the index: `p3_index.py` embedded all 10,912
  passages PLAIN (`KNOBS["passage_instruction"] = None`, with its own note that changing
  it would need a re-embed). Qwen's instruction format is designed for exactly this
  asymmetry -- an instructed query against plain documents. Putting the instruction on
  both sides, or on neither, still returns vectors and still returns results, so nothing
  fails loudly; search just quietly gets worse. That is why `is_query` is REQUIRED in
  every request rather than defaulting to anything: a caller that forgets it gets a 400,
  not a silently worse answer.

  The same reasoning covers the model: the same name AND the same pinned revision as
  `p3_index.py:77-78`, so a query vector and a passage vector are points in one space.
  `/health` reports the model string the index stores in `search.db` (`qwen3-0.6b/P1@
  97b0c61`), so a caller can compare the two and refuse to search on a mismatch instead
  of ranking by nonsense.

DATA (one owner per file)
  reads   the Qwen model cache (outside this project, read-only, offline)
  writes  NOTHING. No database, no file, no log on disk. This module does not import
          sqlite3 at all, which its test checks -- the cheapest proof that a service
          reached over HTTP cannot touch `news.db`, `search.db` or `intel.db`.

WHEN IT IS DOWN
  `search()` falls back to the word index alone and says so in its result
  (`mode: "fts_only"`, brief §B2-4). So the service is allowed to be down; what it is not
  allowed to do is answer slowly, wrongly, or in a way a caller mistakes for a vector.
  Every error reply carries an `"error"` key and never a `"vectors"` key.

ONE REQUEST AT A TIME, ON PURPOSE
  `HTTPServer` is single-threaded, so requests queue. That is the right shape here: there
  is one model on one GPU and one reader asking questions, and `model.encode` is not
  promised to be thread-safe. Queueing is slower under a burst and cannot corrupt a
  vector; threading would be the other way round.
"""

import argparse
import array
import hashlib
import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

# The model, byte-for-byte as `src/p3_index.py:77-80` names it. These are not knobs: they
# are what the 10,912 passage vectors in `data/search.db` were made with, and a query
# embedded by a different model or a different revision is not comparable with them.
EMBED_NAME = "Qwen/Qwen3-Embedding-0.6B"
EMBED_REVISION = "97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3"
EMBED_SHORT = "qwen3-0.6b"
EMBED_VARIANT = "P1"          # the passage variant the index was built with

# The string `p3_index.py` writes into `search.db` meta as `embed_model`. Reported by
# /health so a caller can check the service against the index it is about to search.
INDEX_MODEL = f"{EMBED_SHORT}/{EMBED_VARIANT}@{EMBED_REVISION[:7]}"

# ------------------------------------------------------------------------- KNOBS

KNOBS = {
    # Loopback only. This service answers anything that can reach it, so it must not be
    # reachable from the network; there is no authentication and there should not be one.
    "host": "127.0.0.1",
    # 8766. 8765 is the app's own port (`p3_app.py` KNOBS["port"]), already listening.
    "port": 8766,
    # Same as `p3_index.py` KNOBS["embed_batch"], so a batch here costs what a batch
    # there costs and the memory high-water mark is one already seen on this machine.
    "embed_batch": 32,
    # Qwen's retrieval instruction, in the project's own existing format
    # (`p2_embed.py:65` uses "Instruct: <task>\nQuery: " for the V1i article variant).
    # QUERY SIDE ONLY. Changing this text does NOT need a re-embed -- the index holds no
    # instruction -- so it is the one genuinely tunable thing here, on the Q17 set.
    "query_instruction": ("Instruct: Given a question about news coverage, retrieve "
                          "newspaper passages that answer it\nQuery: "),
    # A question is one short text; the only legitimate batch is a handful of them
    # (query-by-document in `suggest_connections` sends a headline, a one-liner and a
    # lead). A cap turns a caller's loop bug into one clear 400 instead of a stalled GPU.
    "max_texts": 64,
    # 1 MiB. Far more than any question, far less than anything that could exhaust memory
    # before it is even parsed.
    "max_request_bytes": 1 << 20,
}

STUB_DIM = 16                 # the stub encoder's width; the real model decides its own


# ----------------------------------------------------------------------- encoders

def real_encoder():
    """Qwen3-Embedding-0.6B, pinned, offline — the same loading as `p3_index.py:262-280`.

    Deliberately a copy of that function rather than an import: `p3_index.py` runs in
    `.venv-embed` as a command and is the frozen owner of the index recipe. What matters
    is that the four things which decide the vector space are identical -- the name, the
    revision, `trust_remote_code=False`, and `normalize_embeddings=True` with a float32
    cast. If `p3_index.py` ever re-embeds with different ones, this function has to move
    with it, which is why they are read from the constants above and not spelled twice.
    """
    import os
    os.environ.setdefault("HF_HUB_OFFLINE", "1")      # use the cached files, fetch nothing
    import numpy as np
    import torch
    from sentence_transformers import SentenceTransformer
    device = "mps" if torch.backends.mps.is_available() else "cpu"
    model = SentenceTransformer(EMBED_NAME, device=device, revision=EMBED_REVISION,
                                trust_remote_code=False)

    def enc(texts):
        vecs = model.encode(texts, batch_size=min(len(texts), KNOBS["embed_batch"]),
                            normalize_embeddings=True, convert_to_numpy=True)
        vecs = vecs.astype(np.float32)
        return [[float(x) for x in v] for v in vecs]

    return enc, f"{EMBED_NAME}@{EMBED_REVISION[:7]} on {device}", model


def stub_encoder(dim=STUB_DIM):
    """A vector from a hash of the text, L2-normalised — `p3_index.py`'s `fake_encoder`.

    Same shape and the same unit norm as the real one, so every part of this service but
    the model's quality can be tested without loading it: the request parsing, the
    ordering, the instruction switch, the error replies. It is a pure function of the
    text, which is what makes "did the instruction get prepended?" a checkable question.
    """
    def enc(texts):
        out = []
        for t in texts:
            seed = hashlib.sha1(t.encode("utf-8")).digest()
            raw = [((seed[i % len(seed)] / 255.0) - 0.5) for i in range(dim)]
            norm = sum(x * x for x in raw) ** 0.5 or 1.0
            out.append([float(x) for x in array.array("f", [x / norm for x in raw])])
        return out

    return enc, "stub (hash-based, no model loaded)", None


# ------------------------------------------------------------------------ the one job

def embed_texts(encode, texts, is_query):
    """Embed `texts`, putting the retrieval instruction in front ONLY for a query.

    The whole asymmetry of the design lives in these two lines, which is why they are a
    function of their own with a test on them rather than two lines inside the handler.
    """
    if is_query:
        prepared = [KNOBS["query_instruction"] + t for t in texts]
    else:
        prepared = list(texts)                 # passage side: plain, as the index is
    return encode(prepared)


class BadRequest(Exception):
    """A caller's mistake, carrying the HTTP status it should become."""

    def __init__(self, code, message, status=400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def parse_embed_request(raw):
    """Turn a request body into `(texts, is_query)`, or raise BadRequest saying why.

    Separate from the HTTP plumbing so the rules can be tested directly, and so every
    rejection is one sentence a caller can print. `is_query` is required: see the module
    docstring -- a default would be the one error this service cannot detect later.
    """
    try:
        body = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise BadRequest("bad_json", f"the body is not UTF-8 JSON: {e}")
    if not isinstance(body, dict):
        raise BadRequest("bad_json", "the body must be a JSON object")

    if "texts" not in body:
        raise BadRequest("missing_texts", "no `texts` in the request")
    texts = body["texts"]
    if not isinstance(texts, list):
        raise BadRequest("bad_texts", "`texts` must be a list of strings")
    if not texts:
        raise BadRequest("bad_texts", "`texts` is empty: nothing to embed")
    if len(texts) > KNOBS["max_texts"]:
        raise BadRequest("too_many_texts",
                         f"{len(texts)} texts, the limit is {KNOBS['max_texts']}")
    if any(not isinstance(t, str) for t in texts):
        raise BadRequest("bad_texts", "`texts` must be a list of strings")
    if any(not t.strip() for t in texts):
        raise BadRequest("bad_texts", "a text is empty or only whitespace")

    if "is_query" not in body:
        raise BadRequest("missing_is_query",
                         "no `is_query` in the request: say true for a question, false "
                         "for a passage. It decides whether the retrieval instruction "
                         "goes in front, and the index holds none, so there is no safe "
                         "default")
    is_query = body["is_query"]
    if not isinstance(is_query, bool):
        raise BadRequest("bad_is_query", "`is_query` must be true or false")

    return texts, is_query


# --------------------------------------------------------------------------- HTTP

def build_handler(encode, label, dim_holder):
    """The request handler, closed over whichever encoder it was given.

    A factory rather than module-level state so the test can run the real HTTP server
    with the stub encoder, in-process, on an ephemeral port -- the only way to check the
    error replies and the ordering as a caller actually sees them.
    """

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"          # so a caller may keep the connection
        server_version = "p3_embedsvc/0.1"

        # --- replies -----------------------------------------------------------
        def _json(self, status, payload):
            raw = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def _error(self, status, code, message):
            # Never a `vectors` key: an error must not be mistakable for an answer.
            self._json(status, {"error": code, "message": message})

        # --- routes ------------------------------------------------------------
        def do_GET(self):
            path = self.path.split("?", 1)[0].rstrip("/") or "/"
            if path == "/health":
                self._json(200, {"ok": True, "model": EMBED_NAME,
                                 "revision": EMBED_REVISION,
                                 "index_model": INDEX_MODEL,
                                 "dim": dim_holder.get("dim"),
                                 "encoder": label,
                                 "query_instruction": KNOBS["query_instruction"]})
            elif path == "/embed":
                self._error(405, "method_not_allowed", "POST to /embed, do not GET it")
            else:
                self._error(404, "no_such_path",
                            f"no {path} here; this service has /embed and /health")

        def do_POST(self):
            path = self.path.split("?", 1)[0].rstrip("/") or "/"
            if path != "/embed":
                self._error(404, "no_such_path",
                            f"no {path} here; this service has /embed and /health")
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                self._error(400, "bad_length", "Content-Length is not a number")
                return
            if length <= 0:
                self._error(400, "no_body", "the request has no body")
                return
            if length > KNOBS["max_request_bytes"]:
                self._error(413, "too_large",
                            f"{length} bytes, the limit is {KNOBS['max_request_bytes']}")
                return
            raw = self.rfile.read(length)
            try:
                texts, is_query = parse_embed_request(raw)
            except BadRequest as e:
                self._error(e.status, e.code, e.message)
                return
            try:
                vectors = embed_texts(encode, texts, is_query)
            except Exception as e:                  # the model's failure, not a caller's
                self._error(500, "encode_failed", f"{type(e).__name__}: {e}")
                return
            dim_holder["dim"] = len(vectors[0]) if vectors else dim_holder.get("dim")
            self._json(200, {"vectors": vectors, "dim": dim_holder["dim"],
                             "count": len(vectors), "is_query": is_query,
                             "index_model": INDEX_MODEL})

        # --- quiet by default --------------------------------------------------
        def log_message(self, fmt, *args):
            if getattr(self.server, "verbose", False):
                sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    return Handler


def make_server(encode, label, host=None, port=None, verbose=False):
    """An unstarted HTTPServer. `port=0` takes any free port (what the test uses)."""
    host = KNOBS["host"] if host is None else host
    port = KNOBS["port"] if port is None else port
    dim_holder = {"dim": None}
    server = HTTPServer((host, port), build_handler(encode, label, dim_holder))
    server.verbose = verbose
    server.dim_holder = dim_holder
    return server


def serve(stub=False, host=None, port=None, verbose=False):
    """Load the model, bind the port, and say so in one line before serving.

    The warm-up embed is there to make the start-up line honest: it proves the model can
    actually produce a vector, and it is what fills in `dim` for `/health`, so a caller
    checking health before the first question gets a number rather than a null.
    """
    encode, label, _model = stub_encoder() if stub else real_encoder()
    warm = encode(["warm-up"])
    dim = len(warm[0])
    server = make_server(encode, label, host=host, port=port, verbose=verbose)
    server.dim_holder["dim"] = dim
    bound_host, bound_port = server.server_address[0], server.server_address[1]
    print(f"p3_embedsvc: {label} · dim {dim} · index model {INDEX_MODEL}")
    print(f"p3_embedsvc: listening on http://{bound_host}:{bound_port} "
          f"· POST /embed · GET /health · writes nothing")
    print("p3_embedsvc: ready", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\np3_embedsvc: stopped")
    finally:
        server.server_close()
    return 0


def main():
    p = argparse.ArgumentParser(description="K3's local embedding service (no database)")
    p.add_argument("--stub", action="store_true",
                   help="hash-based encoder, no model loaded (tests and rehearsal)")
    p.add_argument("--host", default=KNOBS["host"])
    p.add_argument("--port", type=int, default=KNOBS["port"])
    p.add_argument("--verbose", action="store_true", help="log every request")
    args = p.parse_args()
    return serve(stub=args.stub, host=args.host, port=args.port, verbose=args.verbose)


if __name__ == "__main__":
    sys.exit(main())
