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

APPLYING THE VERDICT (added 15 Oct)
    p2_extract.py --referee "Business Line" uses make_page_referee() + articles_from_verdict() below: for each
    non-ad page of that paper, the referee's verdict replaces the rules' grouping and the page's articles are rebuilt
    from WHOLE blocks (no lines lost inside a block). Saved answers are re-used, so a page is never paid for twice.
    Articles built this way carry "method": "referee" in the JSON; rules articles have no "method" field.
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
import p2_extract_rules as R

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


def _as_list(x):
    """The schema says list; a model can still send a JSON string of one, or something else entirely."""
    if isinstance(x, str):
        try:
            x = json.loads(x)
        except ValueError:
            return None
    return x if isinstance(x, list) else None


def validate(answer, blocks):
    """Model answer -> {block_id: 'r1'.. | 'NOT'}; ids it left out keep the draft label (prefixed 'd:').
    Raises ValueError if the answer's shape is unusable (the caller then keeps the rules' output)."""
    ids = [b["id"] for b in blocks]
    known, label, problems = set(ids), {}, Counter()
    arts, nots = _as_list(answer.get("articles")), _as_list(answer.get("not_article", []))
    if arts is None and isinstance(answer.get("articles"), str):
        # Repair (D8, 15 Oct): the model sometimes writes its WHOLE answer inside the 'articles' field as text,
        # e.g. '[{...}, {...}], "not_article": ["b9", ...]'. Read that text as the full form and retry once.
        try:
            whole = json.loads('{"articles": ' + answer["articles"].strip().rstrip(",") + "}")
            arts, nots = _as_list(whole.get("articles")), _as_list(whole.get("not_article", nots or []))
        except ValueError:
            pass
    if arts is None or nots is None:
        raise ValueError(f"answer shape: articles={type(answer.get('articles')).__name__}")
    for k, art in enumerate(arts, 1):
        if not isinstance(art, dict):
            problems["bad article entry"] += 1
            continue
        for i in (_as_list(art.get("blocks")) or []):
            if not isinstance(i, str) or i not in known:
                problems["unknown id"] += 1
            elif i in label:
                problems["used twice"] += 1
            else:
                label[i] = f"r{k}"
    for i in nots:
        if not isinstance(i, str) or i not in known:
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
    try:
        labels, problems = validate(answer, blocks)
    except ValueError as e:                 # fail-closed: unusable shape -> keep the rules' output
        return {"ok": False, "error": str(e), "answer": answer, "labels": {b["id"]: "d:" + b["story"] for b in blocks},
                "tokens_in": tin, "tokens_out": tout, "cost_usd": round(cost, 5)}
    return {"ok": True, "labels": labels, "problems": problems, "answer": answer, "tokens_in": tin,
            "tokens_out": tout, "cost_usd": round(cost, 5), "seconds": round(time.time() - t0, 1),
            "stop_reason": getattr(resp, "stop_reason", None)}


# ----------------------------------------------------------------------------- apply the verdict (15 Oct)
def _blocks_with_spans(page):
    """Same blocks, same order, same ids as p2_extract.page_blocks, plus each block's text pieces (size, bold)."""
    blocks = []
    for b in page.get_text("dict")["blocks"]:
        if b["type"] != 0:
            continue
        lines = [l for l in b["lines"] if abs(l["dir"][1]) <= 0.1]
        text = " ".join(s["text"] for l in lines for s in l["spans"]).strip()
        if text:
            spans = [(s["text"], s["size"], bool(s["flags"] & 16) or "Bold" in s["font"] or "Black" in s["font"])
                     for l in lines for s in l["spans"] if s["text"].strip()]
            sizes = Counter()
            for t, sz, _ in spans:
                sizes[round(sz * 2) / 2] += len(t)
            bold_chars = sum(len(t) for t, _, bo in spans if bo)
            blocks.append({"bbox": pymupdf.Rect(b["bbox"]), "text": text,
                           "size": sizes.most_common(1)[0][0] if sizes else 0,
                           "bold": bold_chars > 0.5 * max(1, sum(len(t) for t, _, _ in spans))})
    blocks.sort(key=lambda b: (round(b["bbox"].x0 / 40), b["bbox"].y0))
    for i, b in enumerate(blocks, 1):
        b["id"] = f"b{i}"
    return blocks


def _reading_order(blocks):
    """Columns from where blocks start (left to right), then top to bottom inside each column."""
    cols, out = [], []
    for b in sorted(blocks, key=lambda b: b["bbox"].x0):
        if not cols or b["bbox"].x0 - cols[-1][0]["bbox"].x0 > R.COLUMN_MERGE:
            cols.append([b])
        else:
            cols[-1].append(b)
    for c in cols:
        out.append(sorted(c, key=lambda b: b["bbox"].y0))
    return out


def articles_from_verdict(page, out, rules_res):
    """Rebuild one page's articles from the referee's verdict (block -> story), in the same shape the rules produce,
    so the runner (jump joining, block map, summary, JSON) treats them exactly like rules articles.
      - blocks labelled rN form article N; blocks the referee left out ('d:sK') form a group of their own;
        NOT / d:other blocks are dropped; d:grey blocks stay grey (unplaced body text)
      - headline = the group's largest-text blocks if at least HEADLINE_RATIO x body size; else the headline the
        referee wrote for that article; deck = mid-size blocks just under the headline; byline = a short bold block
        right after; body = ALL text of the remaining blocks, in reading order (whole blocks: no lines lost)"""
    body_size = rules_res.get("body_size") or 0
    blocks = _blocks_with_spans(page)
    groups, grey = {}, []
    for b in blocks:
        lab = out["labels"].get(b["id"], "NOT")
        if lab in ("NOT", "d:other"):
            continue
        if lab == "d:grey":
            grey.append({"bbox": b["bbox"], "text": b["text"]})
            continue
        groups.setdefault(lab, []).append(b)
    ref_heads = {}
    for k, art in enumerate((out.get("answer") or {}).get("articles") or [], 1):
        if isinstance(art, dict) and isinstance(art.get("headline"), str):
            ref_heads[f"r{k}"] = art["headline"]
    articles, labels = [], []
    for lab, bl in groups.items():
        letters = [b for b in bl if len(b["text"].strip()) <= 2]           # drop caps: never a headline
        big = max((b["size"] for b in bl if b not in letters), default=0)
        heads = [b for b in bl if b not in letters and body_size and b["size"] >= R.HEADLINE_RATIO * body_size
                 and abs(b["size"] - big) < 1.5]
        heads.sort(key=lambda b: (b["bbox"].y0, b["bbox"].x0))
        rest = [b for b in bl if b not in heads]
        hbox = pymupdf.Rect(heads[0]["bbox"]) if heads else pymupdf.Rect(min(bl, key=lambda b: b["bbox"].y0)["bbox"])
        for h in heads[1:]:
            hbox |= h["bbox"]
        deck = [b for b in rest if heads and body_size + R.BODY_SIZE_TOL <= b["size"] < big
                and 0 <= b["bbox"].y0 - hbox.y1 < R.DECK_GAP_MAX + 20]
        rest = [b for b in rest if b not in deck]
        cols = _reading_order(rest)
        ordered = [b for c in cols for b in c]
        brief_head = []
        if not heads and ordered and ordered[0]["bold"] and len(ordered[0]["text"].split()) <= 14:
            brief_head = [ordered.pop(0)]       # a brief: its headline is bold text at body size
            hbox = pymupdf.Rect(brief_head[0]["bbox"])
        byline = []
        if ordered and ordered[0]["bold"] and len(ordered[0]["text"].split()) <= 6:
            byline = [ordered.pop(0)]
        parts = []
        for b in ordered:                       # a drop cap joins the text after it: "T" + "he abbreviation" = "The ..."
            if parts and parts[-1][1] and len(parts[-1][0]) <= 2:
                parts[-1] = (parts[-1][0] + b["text"].lstrip(), False)
            else:
                parts.append((b["text"], b in letters))
        info = R._clean(" ".join(t for t, _ in parts))
        headline = " ".join(h["text"] for h in heads + brief_head) or ref_heads.get(lab, "")
        entry = {"headline": re.sub(r"\s+", " ", headline).strip(), "deck": " ".join(b["text"] for b in deck),
                 "byline": " ".join(b["text"] for b in byline), "body": info["text"],
                 "words": len(info["text"].split()), "continues_to": info["continues_to"],
                 "see_also_page": info["see_also_page"], "method": "referee",
                 "_hbox": hbox, "_boxes": [b["bbox"] for b in rest + deck + byline],
                 "_pieces": [" ".join(b["text"] for b in c) for c in cols]}
        (articles if entry["words"] >= R.MIN_ARTICLE_WORDS else labels).append(entry)
    articles.sort(key=lambda a: (a["_hbox"].y0, a["_hbox"].x0))
    return {"body_size": rules_res.get("body_size"), "articles": articles, "grey": grey, "labels": labels}


def make_page_referee(root, stem, fresh=False):
    """-> function(page, page_number, rules_res) -> (res, info) used by p2_extract.py --referee.
    Re-uses a saved answer (review/<stem>/referee_pNN.json) when one exists, so a page is never paid for twice;
    otherwise calls Haiku once and saves the answer. Any failure -> the rules' result (fail-closed)."""
    rev = Path(root) / "review" / stem
    rev.mkdir(parents=True, exist_ok=True)
    (Path(root) / "logs").mkdir(exist_ok=True)
    state = {"client": None}

    def run(page, pn, rules_res, zones=()):
        f = rev / f"referee_p{pn:02d}.json"
        if f.exists() and not fresh:
            out, paid = json.loads(f.read_text(encoding="utf-8")), 0.0
            if not out.get("ok") and isinstance(out.get("answer"), dict):     # saved before a repair existed
                try:
                    lab, prob = validate(out["answer"], page_blocks(page))
                    out.update(ok=True, labels=lab, problems=prob)
                except ValueError:
                    pass
        else:
            try:
                if state["client"] is None:
                    state["client"] = make_client()
                out = referee_page(page, state["client"], rules_res)
            except Exception as e:              # no key / SDK problem: fail-closed, don't save, don't stop the run
                return rules_res, {"ok": False, "paid": 0.0, "error": str(e)[:200]}
            paid = out.get("cost_usd", 0) or 0
            f.write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
            with open(Path(root) / "logs" / "referee.log", "a", encoding="utf-8") as fh:
                fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {stem} p{pn}: ok={out['ok']} "
                         f"in={out.get('tokens_in')} out={out.get('tokens_out')} ${paid:.4f} "
                         f"problems={out.get('problems', out.get('error'))}\n")
        if not out.get("ok"):
            return rules_res, {"ok": False, "paid": paid}
        try:
            if zones:                           # 18 Oct: Screen's zones win over the verdict (a block inside one = NOT)
                labels = dict(out["labels"])
                for b in page_blocks(page):
                    c = (b["bbox"].tl + b["bbox"].br) / 2
                    if any(z.contains(c) for z in zones):
                        labels[b["id"]] = "NOT"
                out = dict(out, labels=labels)
            return articles_from_verdict(page, out, rules_res), {"ok": True, "paid": paid}
        except Exception as e:                  # fail-closed: never lose a page to a rebuild bug
            return rules_res, {"ok": False, "paid": paid, "error": str(e)[:200]}
    return run


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
