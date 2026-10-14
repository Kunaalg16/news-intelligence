"""
p2_extract_referee.py -- Phase 2.1 method add-on: an AI "referee" that corrects the rules' story grouping on a page.

How it works (model cascade: free rules first, a cheap model only where they fall short):
  1. The rules (p2_extract_rules.py) split the page and every text block gets a draft label (s1, s2, grey, other).
  2. Haiku sees one line per block: id, position, text size, bold, word count, the rules' draft label, and the
     first ~160 characters of text. No image: text + layout only, so it stays cheap.
  3. It must answer through a fixed schema (forced tool use): a list of articles (each = a list of block ids)
     and a list of blocks that are not article text.
  4. The answer is checked before use: unknown ids are dropped, a block used twice keeps its first use, and any
     block the model left out keeps the rules' draft label. A failed call or an unusable answer = the page keeps
     the rules' output (fail-closed). Every call is logged with its token counts so cost is measured, not guessed.

USAGE (from ~/news-intel) -- referee the gold-set pages of one paper and save the answers for scoring:
    .venv/bin/python src/p2_extract_referee.py "archive/2026-10-13/2026-10-13_Business Line.pdf" --pages 1 2 5
    writes review/<stem>/referee_pNN.json  (block -> story, the model's raw answer, tokens, cost)
    and appends one line per call to logs/referee.log
"""

import argparse
import json
import re
import sys
import time
from collections import Counter
from pathlib import Path

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parent))
from p2_extract import page_blocks, assign_blocks          # same block ids as the answer keys
from p2_extract_rules import segment_page

MODEL = "claude-haiku-4-5-20251001"
PRICE_IN, PRICE_OUT = 1.0, 5.0         # US$ per million tokens (Haiku), for the cost log
MAX_TOKENS = 4000
TEXT_CHARS = 160                       # characters of each block shown to the model
ENV_FILE = Path.home() / ".config" / "news-intel" / ".env"

TOOL = {
    "name": "record_page_articles",
    "description": "Record which text blocks of this newspaper page form each article, and which blocks are not article text.",
    "input_schema": {
        "type": "object",
        "properties": {
            "articles": {
                "type": "array",
                "description": "One entry per article on the page, in any order.",
                "items": {
                    "type": "object",
                    "properties": {
                        "headline": {"type": "string", "description": "The article's headline, copied from its block."},
                        "blocks": {"type": "array", "items": {"type": "string"},
                                   "description": "Every block id of this article: headline, sub-headline, byline, body, "
                                                  "and any chart or table that belongs to it."},
                    },
                    "required": ["headline", "blocks"],
                },
            },
            "not_article": {"type": "array", "items": {"type": "string"},
                            "description": "Block ids that are not article text."},
        },
        "required": ["articles", "not_article"],
    },
}

INSTRUCTIONS = """You are checking how a newspaper page was split into articles. The page's text comes as numbered blocks
(b1, b2, ...) with their position in points (x0,y0 = top-left; x1,y1 = bottom-right; y grows downwards), text size,
bold flag, word count, and a DRAFT label from a layout-rule program (s1, s2, ... = draft article; grey = body text it
could not place; other = not placed). The draft is usually right; your job is to fix its mistakes.

Rules:
- An article = its headline, sub-headline, byline, body text, and its own charts/tables. Body text often continues in
  the next column, sometimes under another article's headline area; follow the meaning of the text.
- Each brief or short item with its own headline or bold lead-in is a SEPARATE article, even when several sit under
  one shared section heading.
- NOT article text: page headers and footers, section labels, crosswords, puzzles and their solutions, quizzes, ads and
  promos, photo captions and credits, pull quotes (a sentence repeated large from the article), reporter email
  addresses, stock or data tables not attached to an article, printer marks.
- Use every block id exactly once, either in one article or in not_article. Use only ids from the list."""


def block_lines(page, blocks):
    """One compact line per block for the prompt: id | box | size | bold | words | draft | text."""
    spans = []
    for b in page.get_text("dict")["blocks"]:
        for l in b.get("lines", []):
            for s in l["spans"]:
                if s["text"].strip():
                    spans.append((pymupdf.Rect(s["bbox"]), s["size"], bool(s["flags"] & 16) or "Bold" in s["font"],
                                  len(s["text"])))
    out = []
    for b in blocks:
        inside = [(sz, bold, n) for r, sz, bold, n in spans if b["bbox"].contains(r.tl + (0.5, 0.5))]
        sizes = Counter()
        for sz, _, n in inside:
            sizes[round(sz, 1)] += n
        size = sizes.most_common(1)[0][0] if sizes else 0
        bold = sum(n for _, bo, n in inside if bo) > 0.5 * max(1, sum(n for *_, n in inside))
        x0, y0, x1, y1 = (round(v) for v in b["bbox"])
        text = re.sub(r"\s+", " ", b["text"])[:TEXT_CHARS]
        out.append(f'{b["id"]} | {x0},{y0},{x1},{y1} | {size}pt | {"bold" if bold else "-"} | '
                   f'{len(b["text"].split())}w | {b.get("story", "other")} | {text}')
    return out


def validate(answer, blocks):
    """Model answer -> {block_id: 'r1'.. | 'NOT'}; ids it left out keep the draft label (prefixed 'd:')."""
    ids = [b["id"] for b in blocks]
    known, label, problems = set(ids), {}, Counter()
    for k, art in enumerate(answer.get("articles", []), 1):
        for i in art.get("blocks", []):
            if i not in known:
                problems["unknown id"] += 1
            elif i in label:
                problems["used twice"] += 1
            else:
                label[i] = f"r{k}"
    for i in answer.get("not_article", []):
        if i not in known:
            problems["unknown id"] += 1
        elif i in label:
            problems["used twice"] += 1
        else:
            label[i] = "NOT"
    for b in blocks:
        if b["id"] not in label:
            problems["left out"] += 1
            label[b["id"]] = "d:" + b.get("story", "other")
    return label, dict(problems)


def make_client():
    import anthropic                        # only needed for real calls (tests pass their own client)
    from dotenv import load_dotenv
    load_dotenv(ENV_FILE)
    return anthropic.Anthropic()


def referee_page(page, client, rules_res=None):
    """-> dict with 'labels' (block -> story or NOT), raw answer, tokens, cost, problems, ok flag."""
    blocks = page_blocks(page)
    res = rules_res or segment_page(page)
    for k, a in enumerate(res["articles"], 1):
        a.setdefault("number", k)
    assign_blocks(blocks, res)
    prompt = INSTRUCTIONS + "\n\nBLOCKS (id | x0,y0,x1,y1 | size | bold | words | draft | text):\n" + \
        "\n".join(block_lines(page, blocks))
    t0 = time.time()
    try:
        resp = client.messages.create(
            model=MODEL, max_tokens=MAX_TOKENS, tools=[TOOL],
            tool_choice={"type": "tool", "name": TOOL["name"]},
            extra_body={"temperature": 0},
            messages=[{"role": "user", "content": prompt}])
    except Exception as e:                  # fail-closed: keep the rules' output
        return {"ok": False, "error": str(e)[:300], "labels": {b["id"]: "d:" + b["story"] for b in blocks}}
    answer = next((c.input for c in resp.content if getattr(c, "type", "") == "tool_use"), None)
    tin, tout = resp.usage.input_tokens, resp.usage.output_tokens
    cost = (tin * PRICE_IN + tout * PRICE_OUT) / 1e6
    if not answer:
        return {"ok": False, "error": "no tool answer", "labels": {b["id"]: "d:" + b["story"] for b in blocks},
                "tokens_in": tin, "tokens_out": tout, "cost_usd": cost}
    labels, problems = validate(answer, blocks)
    return {"ok": True, "labels": labels, "problems": problems, "answer": answer, "tokens_in": tin,
            "tokens_out": tout, "cost_usd": round(cost, 5), "seconds": round(time.time() - t0, 1),
            "stop_reason": getattr(resp, "stop_reason", None)}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pdf")
    ap.add_argument("--pages", nargs="+", type=int, required=True)
    ap.add_argument("--root", default=str(Path(__file__).resolve().parent.parent))
    args = ap.parse_args()
    pdf, root = Path(args.pdf), Path(args.root)
    rev = root / "review" / pdf.stem
    rev.mkdir(parents=True, exist_ok=True)
    (root / "logs").mkdir(exist_ok=True)
    client = make_client()
    doc = pymupdf.open(pdf)
    total = 0.0
    for pn in args.pages:
        out = referee_page(doc[pn - 1], client)
        (rev / f"referee_p{pn:02d}.json").write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
        total += out.get("cost_usd", 0)
        line = (f"{time.strftime('%Y-%m-%d %H:%M:%S')} {pdf.stem} p{pn}: ok={out['ok']} "
                f"in={out.get('tokens_in')} out={out.get('tokens_out')} ${out.get('cost_usd', 0):.4f} "
                f"problems={out.get('problems', out.get('error'))} stop={out.get('stop_reason')}")
        print(line)
        with open(root / "logs" / "referee.log", "a", encoding="utf-8") as f:
            f.write(line + "\n")
    print(f"total ${total:.4f} for {len(args.pages)} pages")


if __name__ == "__main__":
    main()
