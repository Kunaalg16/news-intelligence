"""
test_p2_referee.py -- TEST (free, no API key, no internet). Checks the referee's plumbing and safety, not its quality.

WHAT IT DOES
    Runs src/p2_extract_referee.py on the gold pages with a FAKE model instead of Haiku, and checks four things:
      1. oracle  : fake model answers with your answer key   -> must score 100/100/100 (proves the pipeline from
                   answer to score loses nothing; block level is not a hidden limit on these pages)
      2. echo    : fake model repeats the rules' draft       -> must score exactly like the rules at block level
      3. garbage : fake model sends invented ids, duplicates -> page must fall back to the rules (same as echo)
      4. crash   : the API call fails                        -> page must fall back to the rules (same as echo)
    plus 5 malformed answer shapes (the 15 Oct crash: 'articles' sent as a string) -> handled or fail-closed.
    Prints PASS / FAIL per check and exits with an error if any check fails.

WHY IT EXISTS
    The referee must never make the pipeline crash or produce worse-than-rules output because of a bad answer
    ("fail-closed", decided 14 Oct). This is the regression test for that promise: run it after ANY referee change.
    Quality (does Haiku group stories well?) is measured separately with p2_check.py --referee on real answers.

WHEN TO RUN
    After every change to p2_extract_referee.py, p2_extract.py or p2_extract_rules.py. Takes about 1 minute.

USAGE (from ~/news-intel)
    .venv/bin/python tests/test_p2_referee.py
"""

import glob
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import pymupdf
import p2_extract_referee as RF
from p2_check import read_key, page_words_referee, score
from p2_extract import page_blocks, assign_blocks
from p2_extract_rules import segment_page

PAPERS = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "archive"


class FakeClient:
    """Stands in for anthropic.Anthropic(): .messages.create(...) returns a canned tool answer or raises."""
    def __init__(self, make):
        self.make, self.messages = make, self

    def create(self, **kw):
        ans = self.make()
        if isinstance(ans, Exception):
            raise ans
        return types.SimpleNamespace(content=[types.SimpleNamespace(type="tool_use", input=ans)],
                                     usage=types.SimpleNamespace(input_tokens=1000, output_tokens=100),
                                     stop_reason="tool_use")


def gold_pages():
    for kp in sorted(glob.glob(str(ROOT / "eval" / "*.answer_key.txt"))):
        stem = Path(kp).name.replace(".answer_key.txt", "")
        pdfs = glob.glob(str(PAPERS / "*" / f"{stem}.pdf"))
        if pdfs:
            doc = pymupdf.open(pdfs[0])
            for pn, info in read_key(kp).items():
                yield stem, pn, doc[pn - 1], info["labels"]


def fakes(page, labels):
    blocks = page_blocks(page)
    res = segment_page(page)
    for k, a in enumerate(res["articles"], 1):
        a["number"] = k
    assign_blocks(blocks, res)

    def oracle():
        g = {}
        for b in blocks:
            g.setdefault(labels.get(b["id"], "NOT"), []).append(b["id"])
        return {"articles": [{"headline": t, "blocks": v} for t, v in g.items() if t != "NOT"],
                "not_article": g.get("NOT", [])}

    def echo():
        g = {}
        for b in blocks:
            g.setdefault(b["story"], []).append(b["id"])
        return {"articles": [{"headline": t, "blocks": v} for t, v in g.items() if t.startswith("s")],
                "not_article": [i for t, v in g.items() if not t.startswith("s") for i in v]}

    def garbage():
        return {"articles": [{"headline": "x", "blocks": ["b9999", blocks[0]["id"], blocks[0]["id"]]}],
                "not_article": ["zz"]}

    return {"oracle": oracle, "echo": echo, "garbage": garbage,
            "crash": lambda: RuntimeError("HTTP 529 overloaded")}


def main():
    failures = 0
    pages = list(gold_pages())
    if not pages:
        sys.exit(f"No gold-page PDFs found under {PAPERS}")
    results = {m: [] for m in ("oracle", "echo", "garbage", "crash")}
    for stem, pn, page, labels in pages:
        for mode, make in fakes(page, labels).items():
            out = RF.referee_page(page, FakeClient(make))
            q = [(f"{stem}p{pn}:{t}", f"{stem}p{pn}:{p}" if p else None)
                 for t, p in page_words_referee(page, labels, out["labels"])]
            results[mode] += q
    s = {m: score(q) for m, q in results.items()}
    key = lambda d: (round(d["coverage"], 4), round(d["precision"], 4), round(d["recall"], 4))
    checks = [
        ("oracle answer scores 100/100/100", key(s["oracle"]) == (1.0, 1.0, 1.0)),
        ("crash falls back to the rules (= echo)", key(s["crash"]) == key(s["echo"])),
        ("garbage falls back to the rules (within 1 point of echo)",
         all(abs(a - b) <= 0.01 for a, b in zip(key(s["garbage"]), key(s["echo"])))),
    ]
    blocks = [{"id": "b1", "story": "s1"}, {"id": "b2", "story": "s1"}, {"id": "b3", "story": "other"}]
    shapes = {
        "articles sent as a string": {"articles": "S1: b1 b2", "not_article": ["b3"]},
        "articles sent as JSON text": {"articles": '[{"headline":"x","blocks":["b1","b2"]}]', "not_article": ["b3"]},
        "article entry is a string": {"articles": ["b1 b2", {"headline": "x", "blocks": ["b1"]}], "not_article": ["b3"]},
        "ids not strings": {"articles": [{"headline": "x", "blocks": [1, ["b2"], "b1"]}], "not_article": [None, "b3"]},
        "no articles key": {"not_article": ["b3"]},
    }
    for name, ans in shapes.items():
        try:
            labels, _ = RF.validate(ans, blocks)
            ok = set(labels) == {"b1", "b2", "b3"}          # every block labelled exactly once
        except ValueError:
            ok = True                                         # refused -> caller keeps the rules (fail-closed)
        except Exception as e:                                # anything else = crash = FAIL
            ok = False
        checks.append((f"malformed answer handled: {name}", ok))
    print(f"{len(pages)} gold pages · scores (cov/prec/rec): " +
          " · ".join(f"{m} {key(v)[0]:.1%}/{key(v)[1]:.1%}/{key(v)[2]:.1%}" for m, v in s.items()))
    for name, ok in checks:
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
        failures += not ok
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
