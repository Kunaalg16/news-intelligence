"""
test_p3_embedsvc.py -- TEST (free, no API key, no internet, no model). Checks
src/p3_embedsvc.py.

WHAT IT DOES
    Starts the REAL HTTP server in a thread on an ephemeral port with the STUB encoder,
    and talks to it with `urllib.request` -- the same way `p3_search.py` will, because
    `httpx` is not installed in `.venv`. So what is tested is the service as a caller
    meets it: the parsing, the ordering, the instruction switch and every error reply.
    Four parts:
      1. good requests  : one vector per text, in order, unit norm, /health answers
      2. the switch     : the retrieval instruction is prepended for a query and for
                          nothing else -- checked against the stub's own output, since
                          the stub is a pure function of the text it was handed
      3. bad requests   : malformed JSON, no body, no `texts`, empty `texts`, wrong
                          types, no `is_query`, too many texts, too large, wrong path,
                          wrong method -- each a clean JSON error with no `vectors` key
      4. writes nothing : `data/` is byte-identical afterwards, and the module does not
                          import sqlite3 at all

WHY IT EXISTS
    The service is the one part of search that can be down. `search()` is allowed to
    cope with that (it falls back to the word index and says `mode: "fts_only"`), but it
    cannot cope with a service that answers something a caller mistakes for a vector --
    so every error path here is checked to carry `error` and never `vectors`.

    The instruction switch is the other reason. The index embedded all 10,912 passages
    plain; a query must carry Qwen's retrieval instruction and a passage must not. Get it
    backwards and nothing fails -- search just quietly gets worse -- so it is checked
    directly rather than trusted.

PASS LINES (fixed before the run, K3 brief §B2-4, line 1 and line 3)
    - A well-formed POST /embed returns one vector per text, in the order they were sent.
    - `is_query` true and false take different paths, and the instruction is applied only
      when true.
    - Malformed JSON, a missing `texts`, a wrong type and a non-/embed path each give a
      clean error, not a stack trace and not a hang.
    - Nothing is written to disk: no file created or changed under `data/`.
    - The service's model name and pinned revision are identical to `p3_index.py`'s, so a
      query vector and a passage vector are points in one space.
    A failed line is answered by error analysis, not by moving the line.

    NOT COVERED HERE: the real model. Loading it needs the Qwen cache, which lives
    outside this project and which the sandbox refuses to read. Brief §B2-4 line 2 is
    WRITTEN, NOT RUN; the report names the command Kunaal runs himself.

WHEN TO RUN
    After every change to src/p3_embedsvc.py. Takes about a second.

USAGE (from ~/news-intel)
    .venv/bin/python tests/test_p3_embedsvc.py
    .venv-embed/bin/python tests/test_p3_embedsvc.py      same checks, other environment
"""

import ast
import json
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import p3_embedsvc as E          # noqa: E402
import p3_index as I             # noqa: E402  (stdlib-only at import; read for its constants)

DATA = ROOT / "data"

CHECKS = []


def check(name, got, want):
    CHECKS.append((name, got, want))


# ------------------------------------------------------------------ the server

def post(base, payload, raw=None, headers=None):
    """POST to /embed and return (status, parsed body). Never raises on a 4xx/5xx."""
    body = raw if raw is not None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(f"{base}/embed", data=body, method="POST",
                                 headers=headers or {"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


def get(base, path):
    req = urllib.request.Request(f"{base}{path}", method="GET")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


def data_fingerprint():
    """Every file under data/, with its size and mtime -- the proof it was not touched."""
    if not DATA.exists():
        return {}
    return {p.name: (p.stat().st_size, p.stat().st_mtime_ns)
            for p in sorted(DATA.iterdir()) if p.is_file()}


def main():
    encode, label, _ = E.stub_encoder()
    server = E.make_server(encode, label, host="127.0.0.1", port=0)
    server.dim_holder["dim"] = len(encode(["warm-up"])[0])
    host, port = server.server_address[0], server.server_address[1]
    base = f"http://{host}:{port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    before = data_fingerprint()

    try:
        # ------------------------------------------------- 1. good requests
        texts = ["the budget session opened", "a cricket result", "a court ruling"]
        status, body = post(base, {"texts": texts, "is_query": False})
        check("a well-formed POST /embed answers 200", status, 200)
        check("one vector per text", len(body.get("vectors", [])), 3)
        check("the reply says how many", body.get("count"), 3)
        check("the reply says which side it embedded", body.get("is_query"), False)
        check("every vector has the same width",
              {len(v) for v in body["vectors"]}, {E.STUB_DIM})
        check("the reply names the index model, so a caller can check the space matches",
              body.get("index_model"), E.INDEX_MODEL)
        # L2-normalised, with a tolerance: the values go through float32 on the way out
        # (as the index's do), so an exact 1.0 is not something to assert.
        norms = [sum(x * x for x in v) ** 0.5 for v in body["vectors"]]
        check("every vector is unit length (to 1e-5)",
              all(abs(n - 1.0) < 1e-5 for n in norms), True)

        # ORDER. The stub is a pure function of the text it is handed, so embedding the
        # three texts one at a time and lining them up is an exact test of the order the
        # batch came back in -- not a plausibility check.
        one_at_a_time = [post(base, {"texts": [t], "is_query": False})[1]["vectors"][0]
                         for t in texts]
        check("the vectors come back in the order the texts were sent",
              body["vectors"], one_at_a_time)
        check("and a different text really does give a different vector (the check above "
              "could pass on identical vectors otherwise)",
              len({tuple(v) for v in body["vectors"]}), 3)

        status, health = get(base, "/health")
        check("GET /health answers 200", status, 200)
        check("/health says it is up", health.get("ok"), True)
        check("/health reports the dim", health.get("dim"), E.STUB_DIM)
        check("/health reports the pinned revision", health.get("revision"),
              E.EMBED_REVISION)
        check("/health reports the query instruction it will use",
              health.get("query_instruction"), E.KNOBS["query_instruction"])

        # ------------------------------------------------- 2. the instruction switch
        q_status, q = post(base, {"texts": ["who runs the EPFO"], "is_query": True})
        p_status, pp = post(base, {"texts": ["who runs the EPFO"], "is_query": False})
        check("a query answers 200", q_status, 200)
        check("a query and a passage of the SAME text give different vectors",
              q["vectors"][0] == pp["vectors"][0], False)
        want_query = encode([E.KNOBS["query_instruction"] + "who runs the EPFO"])[0]
        want_passage = encode(["who runs the EPFO"])[0]
        check("the query vector is the text WITH the retrieval instruction in front",
              q["vectors"][0], want_query)
        check("the passage vector is the text PLAIN, as the index was embedded",
              pp["vectors"][0], want_passage)
        check("the reply records is_query true", q.get("is_query"), True)
        # the same thing again at the function, so a failure says which layer broke
        check("embed_texts prepends for a query",
              E.embed_texts(encode, ["x"], True), encode([E.KNOBS["query_instruction"] + "x"]))
        check("embed_texts prepends nothing for a passage",
              E.embed_texts(encode, ["x"], False), encode(["x"]))

        # ------------------------------------------------- 3. bad requests
        bad = {
            "malformed JSON": (dict(raw=b"{not json"), 400, "bad_json"),
            "a JSON array instead of an object": (dict(raw=b'["a"]'), 400, "bad_json"),
            "no body at all": (dict(raw=b""), 400, "no_body"),
            "no `texts`": (dict(payload={"is_query": True}), 400, "missing_texts"),
            "`texts` a string, not a list":
                (dict(payload={"texts": "a question", "is_query": True}), 400, "bad_texts"),
            "`texts` a list of numbers":
                (dict(payload={"texts": [1, 2], "is_query": True}), 400, "bad_texts"),
            "`texts` empty": (dict(payload={"texts": [], "is_query": True}), 400, "bad_texts"),
            "a text that is only whitespace":
                (dict(payload={"texts": ["  "], "is_query": True}), 400, "bad_texts"),
            "no `is_query` (there is no safe default)":
                (dict(payload={"texts": ["a question"]}), 400, "missing_is_query"),
            "`is_query` a string":
                (dict(payload={"texts": ["a"], "is_query": "yes"}), 400, "bad_is_query"),
            "too many texts":
                (dict(payload={"texts": ["a"] * (E.KNOBS["max_texts"] + 1),
                                "is_query": True}), 400, "too_many_texts"),
        }
        for name, (kwargs, want_status, want_code) in bad.items():
            status, body = post(base, kwargs.get("payload"), raw=kwargs.get("raw"))
            check(f"{name} -> {want_status} {want_code}", (status, body.get("error")),
                  (want_status, want_code))
            check(f"{name} -> no `vectors` key in the error", "vectors" in body, False)

        status, body = post(base, {"texts": ["a"], "is_query": True},
                            headers={"Content-Type": "application/json",
                                     "Content-Length": str(E.KNOBS["max_request_bytes"] + 1)})
        check("a body over the size limit -> 413", (status, body.get("error")),
              (413, "too_large"))

        status, body = get(base, "/nope")
        check("an unknown path -> 404", (status, body.get("error")), (404, "no_such_path"))
        check("the 404 says what the service does have",
              "/embed" in body.get("message", ""), True)
        status, body = get(base, "/embed")
        check("GET /embed -> 405", (status, body.get("error")), (405, "method_not_allowed"))

        # a 500 from the model's side is still JSON, and still not mistakable for a vector
        def broken(_texts):
            raise RuntimeError("the model fell over")

        broken_server = E.make_server(broken, "broken", host="127.0.0.1", port=0)
        bt = threading.Thread(target=broken_server.serve_forever, daemon=True)
        bt.start()
        bbase = f"http://127.0.0.1:{broken_server.server_address[1]}"
        status, body = post(bbase, {"texts": ["a"], "is_query": True})
        check("an encoder that throws -> 500 encode_failed, as JSON",
              (status, body.get("error")), (500, "encode_failed"))
        check("and still no `vectors` key", "vectors" in body, False)
        check("and the message names the failure",
              "the model fell over" in body.get("message", ""), True)
        broken_server.shutdown()
        broken_server.server_close()

        # ------------------------------------------------- 4. writes nothing
        check("data/ is unchanged after every request above", data_fingerprint(), before)
        source = (ROOT / "src" / "p3_embedsvc.py").read_text()
        tree = ast.parse(source)
        imported = {n.module.split(".")[0] for n in ast.walk(tree)
                    if isinstance(n, ast.ImportFrom) and n.module}
        imported |= {a.name.split(".")[0] for n in ast.walk(tree)
                     if isinstance(n, ast.Import) for a in n.names}
        check("the module imports no database driver", "sqlite3" in imported, False)
        # The CODE, not the prose: the docstring names the three databases on purpose, to
        # say it never touches them, so a check on the raw text would fail BECAUSE the
        # reason was written down. A docstring is an expression statement holding one
        # string, so those are skipped and what is left is the literals a path would
        # actually have to appear in.
        docstring_nodes = {id(n.value) for n in ast.walk(tree)
                           if isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant)
                           and isinstance(n.value.value, str)}
        code_literals = {n.value for n in ast.walk(tree)
                         if isinstance(n, ast.Constant) and isinstance(n.value, str)
                         and id(n) not in docstring_nodes}
        check("no database path appears anywhere in the code",
              sorted(s for s in code_literals if ".db" in s), [])

        # ------------------------------------------------- 5. one space as the index
        # Not a request: the constants. A query embedded by a different model or a
        # different revision is not comparable with the 10,912 passage vectors already
        # in search.db, and nothing at query time could notice.
        check("the model name is the index's", E.EMBED_NAME, I.EMBED_NAME)
        check("the pinned revision is the index's", E.EMBED_REVISION, I.EMBED_REVISION)
        check("the batch size is the index's",
              E.KNOBS["embed_batch"], I.KNOBS["embed_batch"])
        check("the index embedded passages with no instruction (so the query side owns it)",
              I.KNOBS["passage_instruction"], None)
        check("/health's index_model is the string search.db stores",
              E.INDEX_MODEL, f"{I.EMBED_SHORT}/{I.EMBED_VARIANT}@{I.EMBED_REVISION[:7]}")
    finally:
        server.shutdown()
        server.server_close()

    bad_checks = [(n, g, w) for n, g, w in CHECKS if g != w]
    for n, g, w in bad_checks:
        print(f"FAIL {n}: got {g!r}, want {w!r}")
    print(f"{len(CHECKS) - len(bad_checks)}/{len(CHECKS)} checks passed "
          f"(stub encoder; the real model is not loaded here)")
    print("no database was opened and nothing under data/ was written")
    print("PASS" if not bad_checks else "FAIL")
    return 1 if bad_checks else 0


if __name__ == "__main__":
    sys.exit(main())
