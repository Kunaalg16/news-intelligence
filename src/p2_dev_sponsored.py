"""
p2_dev_sponsored.py -- 2.3 Tag · targeted sponsored check (read-only, free, 23 Oct 2026)

WHAT IT DOES
  Two independent judges decide whether a text is paid content:
    the model   tags.kind = 'sponsored'
    the rule    tags.sponsored_cue != ''   (a printed cue phrase found by a free local search)
  This script counts how often they agree (2x2 table + Cohen's kappa) and writes every text that
  EITHER judge flags into one review file, so Kunaal judges only those (disagreement sampling).
  Texts that neither judge flags are not reviewed: the check estimates what the two judges miss
  together only through the targeted review, not through a random sample.

HOW TO RUN (from ~/news-intel)
  .venv/bin/python src/p2_dev_sponsored.py                      run exam-v1, Haiku 5.5, prompt v1.3
  Options: --run exam-v1 · --model claude-haiku-5-5 · --prompt v1.3
  Writes review/tag_audit/sponsored_check_<prompt>.jsonl (upload it to the chat).

SAFETY
  Opens data/news.db READ-ONLY (SQLite mode=ro): it cannot change the database. No API calls.
"""
import argparse, json, sqlite3, sys
from pathlib import Path

DB = Path("data") / "news.db"


def kappa(a, b, c, d):
    """Cohen's kappa for a 2x2 table: a = both yes, b = model only, c = rule only, d = both no."""
    n = a + b + c + d
    if n == 0:
        return None
    po = (a + d) / n
    pe = ((a + b) / n) * ((a + c) / n) + ((c + d) / n) * ((b + d) / n)
    return None if pe == 1 else (po - pe) / (1 - pe)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", default="exam-v1")
    ap.add_argument("--model", default="claude-haiku-5-5")
    ap.add_argument("--prompt", default="v1.3")
    args = ap.parse_args()
    if not DB.exists():
        sys.exit(f"{DB} not found. Run from ~/news-intel.")
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)

    # one row per distinct text in the run (earliest seat gives paper, date, page)
    rows = con.execute(
        "SELECT t.text_hash, t.kind, COALESCE(t.sponsored_cue, ''), t.topic, t.event, t.summary, "
        "       x.headline, x.body, x.words, "
        "       (SELECT p.date || '|' || p.publisher || '|' || p.art_id FROM placements p "
        "         WHERE p.text_hash = t.text_hash AND p.run_id = ? ORDER BY p.date, p.publisher, p.art_id LIMIT 1) "
        "FROM tags t JOIN texts x ON x.text_hash = t.text_hash "
        "WHERE t.model = ? AND t.prompt_version = ? "
        "  AND EXISTS (SELECT 1 FROM placements p WHERE p.text_hash = t.text_hash AND p.run_id = ?)",
        (args.run, args.model, args.prompt, args.run)).fetchall()
    if not rows:
        sys.exit(f"No tags for run {args.run}, model {args.model}, prompt {args.prompt}.")

    a = b = c = d = 0
    review = []
    for h, kind, cue, topic, event, summary, head, body, words, key in rows:
        m, r = kind == "sponsored", cue != ""
        if m and r:
            a += 1; group = "both"
        elif m:
            b += 1; group = "model only"
        elif r:
            c += 1; group = "rule only"
        else:
            d += 1
            continue
        review.append({"group": group, "key": key, "text_hash": h, "words": words, "headline": head,
                       "body": (body or "")[:1500], "model_kind": kind, "cue": cue, "topic": topic,
                       "event": event, "summary": summary})

    n = a + b + c + d
    k = kappa(a, b, c, d)
    print(f"run {args.run} · {args.model} {args.prompt}: {n} distinct texts")
    print(f"                    rule: cue found   rule: no cue")
    print(f"model: sponsored    {a:>14}   {b:>12}")
    print(f"model: not          {c:>14}   {d:>12}")
    print(f"flagged by either judge: {a + b + c} · agree on sponsored: {a} · disagree: {b + c}")
    print(f"Cohen's kappa: {'n/a' if k is None else f'{k:.2f}'}  (1 = perfect agreement beyond chance, 0 = chance)")

    out = Path(f"review/tag_audit/sponsored_check_{args.prompt}.jsonl")
    out.parent.mkdir(parents=True, exist_ok=True)
    order = {"rule only": 0, "model only": 1, "both": 2}
    with open(out, "w", encoding="utf-8") as f:
        for row in sorted(review, key=lambda x: (order[x["group"]], x["key"] or "")):
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"review file: {out} ({len(review)} texts; upload it to the chat)")


if __name__ == "__main__":
    main()
