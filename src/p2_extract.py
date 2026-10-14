"""
p2_extract.py (was run_part1.py) -- Phase 2.1 Extract: archived PDFs in, articles out.
No AI calls, no cost. The HOW lives in p2_extract_rules.py (strategy pattern).

USAGE (from ~/news-intel)
    .venv/bin/python src/p2_extract.py archive/2026-10-13            # every main edition that day
    .venv/bin/python src/p2_extract.py "archive/2026-10-13/2026-10-13_Mint.pdf"
    options:  --root DIR       project root (default: the folder above src/)
              --all-editions   also process _International / _School etc.
              --no-pictures    skip the overlay pictures (faster)

WRITES (names trace back to the PDF)
    data/articles/<date>/<date>_<Publisher>.json   list of articles
    review/<date>_<Publisher>/page_NN.png           overlay: each story a colour + big number,
                                                    GREY = body text in no story, small bNN = block ids
    review/<date>_<Publisher>/blocks.json           which story every block went to (gold-set raw material)
    review/<date>_<Publisher>/summary.txt           one line per page + free-check flags

FREE CHECKS (no model, no labels; they show where to look). Ad pages are not checked.
    grey    > 15% of a page's body text in no article
    long    an article of 1,500+ words (probably two merged)
    short   an article under 40 words (probably a fragment)
    order   a column break inside an article where the text doesn't flow on

PAGE JUMPS
    Markers are read from ANY text size: "Continued on Page N", "Turn to Page N", "» PAGE N", "...text. P 10"
    at the cut; "Continued from P1", "From Page 1" where the story resumes. Markers use PRINTED page numbers;
    an ad wrap makes PDF page = printed page + offset. A cut on PDF page s pointing to printed page T pairs with
    a resume-marker on PDF page t saying "from printed page S" only if t - T == s - S (same offset).
    Without resume-markers (The Hindu), the repeated headline on the target page decides (>= 50% key words).
"""

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

import numpy as np
import pymupdf

from p2_extract_rules import segment_page

COLORS = [(0.90, 0.20, 0.20), (0.10, 0.50, 0.90), (0.10, 0.70, 0.30), (0.95, 0.60, 0.00),
          (0.60, 0.20, 0.80), (0.00, 0.70, 0.70), (0.85, 0.30, 0.60), (0.45, 0.45, 0.10),
          (0.20, 0.30, 0.65), (0.70, 0.40, 0.20), (0.30, 0.60, 0.55), (0.50, 0.50, 0.95)]
STOP = {"the", "a", "an", "of", "to", "in", "on", "for", "and", "at", "by", "from", "with", "as", "is", "be",
        "said", "that", "this", "was", "were", "has", "have", "are", "its", "his", "her", "their", "will", "not"}
GREY_FLAG, LONG_FLAG, SHORT_FLAG = 0.15, 1500, 40
AD_IMAGE_FRAC = 0.45     # page with images over >= 45% of its area and < 250 body words = ad page
MAX_OFFSET = 4           # fallback (no resume marker): try PDF page = printed page + 0..4
KNOWN = {"The Hindu", "Business Line", "Mint", "Economic Times", "Times of India", "Business Standard"}

# --- jump-marker patterns (case-insensitive, matched on one line or a line + the next) ---
P_CUT = re.compile(r"(?:continued\s+on|turn\s+to|contd\.?\s+on)\s*[»>]?\s*(?:page|p)\.?\s*(\d{1,2})\b", re.I)
P_FROM = re.compile(r"(?:continued\s+from|contd\.?\s+from|\bfrom)\s*[»>]?\s*(?:page|p)\.?\s*(\d{1,2})\b", re.I)
P_PTR = [re.compile(r"[»>]\s*(?:pages?|p)\.?\s*(\d{1,2})\b", re.I),           # » PAGE 10, >P5
         re.compile(r"\bsee\s+page\s+(\d{1,2})\b", re.I),
         re.compile(r"[.,;:|’”'\")\]]\s*\|?\s*p\s?(\d{1,2})\s*[»>]?\s*$", re.I)]  # "...Bhubaneswar. P 10"
INLINE_PTR = re.compile(r"(?<=[.,;:’”'\")\]])\s*\|?\s*\bP\s?\d{1,2}\b\s*[»>]?(?=\s|$)")


def parse_name(stem: str):
    """'2026-10-13_The Hindu' -> (date, publisher, edition or None)."""
    date, _, rest = stem.partition("_")
    if rest in KNOWN:
        return date, rest, None
    pub, _, edition = rest.rpartition("_")
    return (date, pub, edition) if pub in KNOWN else (date, rest, None)


def _key_words(s):
    return {w for w in re.findall(r"[a-z0-9]+", s.lower()) if w not in STOP and len(w) > 2}


def _headline_match(a, b):
    ka, kb = _key_words(a), _key_words(b)
    return len(ka & kb) / max(1, min(len(ka), len(kb)))


def _text_sim(a, b):
    """Overlap of key words in the first ~150 words of two articles (Jaccard)."""
    ka = _key_words(a["headline"] + " " + " ".join(a["body"].split()[-150:]))
    kb = _key_words(b["headline"] + " " + " ".join(b["body"].split()[:150]))
    return len(ka & kb) / max(1, len(ka | kb))


# ----------------------------------------------------------------------------- blocks
def page_blocks(page):
    """Native PyMuPDF text blocks, numbered by column band (40 pt) then top-to-bottom: b1, b2, ..."""
    blocks = []
    for b in page.get_text("dict")["blocks"]:
        if b["type"] != 0:
            continue
        lines = [l for l in b["lines"] if abs(l["dir"][1]) <= 0.1]
        text = " ".join(s["text"] for l in lines for s in l["spans"]).strip()
        if text:
            blocks.append({"bbox": pymupdf.Rect(b["bbox"]), "text": text})
    blocks.sort(key=lambda b: (round(b["bbox"].x0 / 40), b["bbox"].y0))
    for i, b in enumerate(blocks, 1):
        b["id"] = f"b{i}"
    return blocks


def _overlap(A, B):
    """Intersection areas between every rect in A (n,4) and every rect in B (m,4) -> (n,m)."""
    w = np.clip(np.minimum(A[:, None, 2], B[None, :, 2]) - np.maximum(A[:, None, 0], B[None, :, 0]), 0, None)
    h = np.clip(np.minimum(A[:, None, 3], B[None, :, 3]) - np.maximum(A[:, None, 1], B[None, :, 1]), 0, None)
    return w * h


def assign_blocks(blocks, res):
    """Each block -> the story covering most of it ('sN'), else 'grey' (unassigned body text) or 'other'."""
    if not blocks:
        return
    A = np.array([tuple(b["bbox"]) for b in blocks])
    area = np.maximum((A[:, 2] - A[:, 0]) * (A[:, 3] - A[:, 1]), 1)
    cols, labels = [], []
    for a in res["articles"]:
        R = np.array([tuple(r) for r in a["_boxes"]] or [(0, 0, 0, 0)])
        cov = _overlap(A, R).sum(axis=1) / area
        head = _overlap(A, np.array([tuple(a["_hbox"])]))[:, 0] / area
        cols.append(np.maximum(cov, np.where(head > 0.5, 1.0, 0)))
        labels.append(f"s{a['number']}")
    G = np.array([tuple(s["bbox"]) for s in res["grey"]] or [(0, 0, 0, 0)])
    grey = _overlap(A, G).sum(axis=1) / area
    M = np.stack(cols, axis=1) if cols else np.zeros((len(blocks), 0))
    for i, b in enumerate(blocks):
        if M.shape[1] and M[i].max() > 0.15:
            b["story"] = labels[int(M[i].argmax())]
        else:
            b["story"] = "grey" if grey[i] > 0.15 else "other"


# ----------------------------------------------------------------------------- free checks
def check_order(a):
    """Column joins inside an article where the text doesn't flow on (ends a sentence, next starts lower-case;
    or ends mid-sentence, next starts a capitalised word)."""
    bad = 0
    pieces = a.get("_pieces", [])
    for prev, nxt in zip(pieces, pieces[1:]):
        p, n = prev.rstrip(), nxt.lstrip()
        if not p or not n:
            continue
        if p[-1] in ".?!\"'”’" and n[0].islower():
            bad += 1
        elif (p[-1].isalpha() or p[-1] == ",") and n[0].isupper() and not p.split()[-1][0].isupper():
            bad += 1
    return bad


def is_ad_page(page, res):
    area = page.rect.width * page.rect.height
    img = sum(pymupdf.Rect(i["bbox"]).intersect(page.rect).get_area() for i in page.get_image_info())
    words = sum(a["words"] for a in res["articles"]) + sum(len(s["text"].split()) for s in res["grey"])
    return img / area >= AD_IMAGE_FRAC and words < 250


# ----------------------------------------------------------------------------- jump markers
def find_markers(page):
    """All jump markers on a page, any text size: [{'kind': cut|ptr|from, 'page': printed N, 'rect': Rect}]."""
    lines = []
    for b in page.get_text("dict")["blocks"]:
        for l in b.get("lines", []):
            t = "".join(s["text"] for s in l["spans"]).replace("\x01", " ").replace("\x02", " ").strip()
            if t:
                lines.append((t, pymupdf.Rect(l["bbox"])))
    out = []
    for i, (t, r) in enumerate(lines):
        nxt = lines[i + 1] if i + 1 < len(lines) and -6 <= lines[i + 1][1].y0 - r.y1 < 14 and abs(lines[i + 1][1].x0 - r.x0) < 60 else None
        joined, jr = (t + " " + nxt[0], r | nxt[1]) if nxt else (t, r)
        if len(t) > 120:
            continue
        m = P_FROM.search(t)
        if m:
            out.append({"kind": "from", "page": int(m.group(1)), "rect": r})
            continue
        m = P_CUT.search(t) or P_CUT.search(joined)
        if m:
            out.append({"kind": "cut", "page": int(m.group(1)), "rect": jr if not P_CUT.search(t) else r})
            continue
        for pat in P_PTR:
            m = pat.search(t)
            if m:
                out.append({"kind": "ptr", "page": int(m.group(1)), "rect": r})
                break
    # a "CONTINUED ON" line followed by a "» PAGE 10" line gives one cut, not cut + ptr
    cuts = [m for m in out if m["kind"] == "cut"]
    return [m for m in out if not (m["kind"] == "ptr" and any(c["rect"].contains(m["rect"]) for c in cuts))]


def _attach(marker, articles, max_dist=45):
    """The article a marker belongs to. A resume marker ('from page 1') belongs to the story that STARTS at or
    just under it (box containing it, or top edge within 30 pt below, same column); a cut marker to the
    story whose text sits closest."""
    mr = marker["rect"]
    if marker["kind"] == "from":
        best, best_gap = None, 30
        for a in articles:
            for r in a["_boxes"] + [a["_hbox"]]:
                if r.x0 > mr.x1 or r.x1 < mr.x0:
                    continue
                gap = 0 if r.contains(mr.tl + (1, 1)) else r.y0 - mr.y0
                if -2 <= gap < best_gap:
                    best, best_gap = a, gap
        return best
    best, best_d = None, max_dist
    for a in articles:
        for r in a["_boxes"] + [a["_hbox"]]:
            dx = max(r.x0 - mr.x1, mr.x0 - r.x1, 0)
            dy = max(r.y0 - mr.y1, mr.y0 - r.y1, 0)
            d = (dx * dx + dy * dy) ** 0.5
            if d < best_d:
                best, best_d = a, d
    return best


def join_jumps(pages, by_page):
    """Pair cut/pointer markers with resume markers (offset-consistent), else fall back to headline match."""
    sources, resumes = [], []
    for p in pages:
        for m in p["markers"]:
            a = _attach(m, p["res"]["articles"])
            if not a:
                continue
            (resumes if m["kind"] == "from" else sources).append((m, a, p["page"]))
    # 1) offset-consistent pairs, best text similarity first
    cands = []
    for m, a, s in sources:
        for f, b, t in resumes:
            if b is a or t <= s:
                continue
            o1, o2 = t - m["page"], s - f["page"]
            if o1 == o2 and o1 >= 0:           # same offset at both ends (sections may number separately)
                cands.append((_text_sim(a, b), m, a, b, o1))
    cands.sort(key=lambda c: -c[0])
    used_a, used_b, offsets, joined, linked = set(), set(), Counter(), 0, 0
    for sim, m, a, b, o in cands:
        if id(a) in used_a or id(b) in used_b or "joined_into" in a:
            continue
        _glue(a, b)
        used_a.add(id(a)); used_b.add(id(b)); offsets[o] += 1; joined += 1
    # 2) no resume marker: repeated headline on the target page (The Hindu style)
    order = [offsets.most_common(1)[0][0]] if offsets else list(range(0, MAX_OFFSET + 1))
    for m, a, s in sources:
        if id(a) in used_a or "joined_into" in a or len(_key_words(a["headline"])) < 3:
            continue                           # 1-2 word labels ("QUICKLY.") match anything
        best, best_score = None, 0.5
        for o in order:
            for c in by_page.get(m["page"] + o, []):
                if c is a or id(c) in used_b or c["page"] <= s:
                    continue
                sc = _headline_match(a["headline"], c["headline"])
                if sc >= best_score:
                    best, best_score = c, sc
        if not best:
            continue
        if m["kind"] == "cut":
            _glue(a, best); used_b.add(id(best)); joined += 1
        else:
            a["related_id"] = best["id"]; linked += 1
        used_a.add(id(a))
    return joined, linked, (offsets.most_common(1)[0][0] if offsets else None)


def _glue(a, b):
    a["body"] = (a["body"] + " " + b["body"]).strip()
    a["words"] = len(a["body"].split())
    a["joined_from"] = b["id"]
    b["joined_into"] = a["id"]


# ----------------------------------------------------------------------------- one paper
def process_pdf(pdf: Path, root: Path, pictures=True) -> dict:
    date, publisher, edition = parse_name(pdf.stem)
    doc = pymupdf.open(pdf)
    rev = root / "review" / pdf.stem
    rev.mkdir(parents=True, exist_ok=True)
    (root / "data" / "articles" / date).mkdir(parents=True, exist_ok=True)

    pages, all_articles, block_map = [], [], {}
    for pn, page in enumerate(doc, start=1):
        try:
            res, error = segment_page(page), None
        except Exception as e:
            res, error = {"body_size": None, "articles": [], "grey": [], "labels": []}, str(e)
        for k, a in enumerate(res["articles"], start=1):
            a["id"], a["page"], a["number"] = f"p{pn:02d}-a{k:02d}", pn, k
            a["body"] = re.sub(r"\s+", " ", INLINE_PTR.sub("", a["body"])).strip()   # drop "... P 10" pointers
            a["words"] = len(a["body"].split())
            all_articles.append(a)
        blocks = page_blocks(page)
        assign_blocks(blocks, res)
        block_map[pn] = blocks
        ad = is_ad_page(page, res)
        assigned = sum(a["words"] for a in res["articles"])
        grey = sum(len(s["text"].split()) for s in res["grey"])
        flags = []
        if error:
            flags.append(f"page failed: {error}")
        elif not ad:
            if assigned + grey > 150 and grey / (assigned + grey) > GREY_FLAG:
                flags.append(f"grey {grey / (assigned + grey):.0%}")
            if grey > 300 and not res["articles"]:
                flags.append("text but no articles")
            flags += [f"long s{a['number']} ({a['words']}w)" for a in res["articles"] if a["words"] >= LONG_FLAG]
            flags += [f"short s{a['number']} ({a['words']}w)" for a in res["articles"] if a["words"] < SHORT_FLAG]
            flags += [f"order s{a['number']} ({c})" for a in res["articles"] if (c := check_order(a))]
        pages.append({"page": pn, "res": res, "grey_words": 0 if ad else grey, "assigned_words": assigned,
                      "flags": flags, "ad": ad, "markers": find_markers(page)})

    by_page = {}
    for a in all_articles:
        by_page.setdefault(a["page"], []).append(a)
    joined, linked, offset = join_jumps(pages, by_page)

    # ---- articles JSON (each article stands alone: paper, date, publisher on every row) ----
    final = []
    for a in all_articles:
        if "joined_into" in a:
            continue
        row = {"paper": pdf.stem, "date": date, "publisher": publisher}
        if edition:
            row["edition"] = edition
        row.update({k: v for k, v in a.items() if not k.startswith("_") and v not in (None, "")})
        final.append(row)
    (root / "data" / "articles" / date / f"{pdf.stem}.json").write_text(
        json.dumps(final, indent=2, ensure_ascii=False), encoding="utf-8")

    (rev / "blocks.json").write_text(json.dumps(
        {str(pn): [{"id": b["id"], "story": b["story"], "bbox": [round(x, 1) for x in b["bbox"]],
                    "text": b["text"][:120]} for b in bl] for pn, bl in block_map.items()},
        indent=1, ensure_ascii=False), encoding="utf-8")

    if pictures:
        for p in pages:
            draw_overlay(doc[p["page"] - 1], p, block_map[p["page"]], rev)

    total_a = sum(p["assigned_words"] for p in pages)
    total_g = sum(p["grey_words"] for p in pages)
    cov = total_a / max(1, total_a + total_g)
    n_ad = sum(p["ad"] for p in pages)
    s = [f"{pdf.name}: {len(final)} articles | {joined} jumps joined, {linked} linked | page offset {offset} | "
         f"coverage {cov:.1%} of body text | ad pages {n_ad} | "
         f"flagged pages {sum(1 for p in pages if p['flags'])}/{len(pages)}", ""]
    for p in pages:
        t = p["assigned_words"] + p["grey_words"]
        s.append(f"page {p['page']:2d}: {len(p['res']['articles']):2d} articles | grey {p['grey_words'] / t if t else 0:4.0%}"
                 + (" | AD PAGE" if p["ad"] else "") + (f" | {'; '.join(p['flags'])}" if p["flags"] else ""))
    (rev / "summary.txt").write_text("\n".join(s) + "\n", encoding="utf-8")

    fc = Counter(f.split()[0] for p in pages for f in p["flags"])
    return {"pdf": pdf.name, "articles": len(final), "joined": joined, "linked": linked, "offset": offset,
            "coverage": cov, "pages": len(pages), "ads": n_ad,
            "flagged": [p["page"] for p in pages if p["flags"]], "flag_counts": dict(fc)}


def draw_overlay(page, p, blocks, rev):
    shape = page.new_shape()
    for a in p["res"]["articles"]:
        c = COLORS[(a["number"] - 1) % len(COLORS)]
        for r in a["_boxes"] + [a["_hbox"]]:
            shape.draw_rect(r)
            shape.finish(color=None, fill=c, fill_opacity=0.35)
    for s in p["res"]["grey"]:
        shape.draw_rect(s["bbox"])
        shape.finish(color=None, fill=(0.45, 0.45, 0.45), fill_opacity=0.55)
    shape.commit()
    for b in blocks:                         # see-through block tags just left of / above each block
        r = b["bbox"]
        w = 4 + 4.2 * len(b["id"])
        tag = pymupdf.Rect(r.x0 - w + 3, r.y0 - 7, r.x0 + 3, r.y0 + 1)
        page.draw_rect(tag, color=None, fill=(1, 1, 0.55), fill_opacity=0.6)
        page.insert_text((tag.x0 + 1.5, tag.y1 - 1.6), b["id"], fontsize=6.5, color=(0, 0, 0.6))
    for a in p["res"]["articles"]:           # big story numbers just above-left of each headline
        c = COLORS[(a["number"] - 1) % len(COLORS)]
        hb = a["_hbox"]
        badge = pymupdf.Rect(hb.x0 - 4, hb.y0 - 16, hb.x0 + 24, hb.y0 + 2)
        page.draw_rect(badge, color=c, fill=c, fill_opacity=0.9)
        page.insert_text((badge.x0 + 3, badge.y1 - 3), f"S{a['number']}", fontsize=13, color=(1, 1, 1))
    page.get_pixmap(dpi=110).save(rev / f"page_{p['page']:02d}.png")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("target")
    ap.add_argument("--root", default=str(Path(__file__).resolve().parent.parent))
    ap.add_argument("--all-editions", action="store_true")
    ap.add_argument("--no-pictures", action="store_true")
    args = ap.parse_args()
    target, root = Path(args.target), Path(args.root)
    pdfs = sorted(target.rglob("*.pdf")) if target.is_dir() else [target]
    if not args.all_editions:
        pdfs = [p for p in pdfs if parse_name(p.stem)[2] is None]
    if not pdfs:
        sys.exit(f"No PDFs found in {target}")
    for pdf in pdfs:
        r = process_pdf(pdf, root, pictures=not args.no_pictures)
        print(f"{r['pdf']:36s} {r['articles']:4d} articles | coverage {r['coverage']:.1%} | jumps {r['joined']} joined, "
              f"{r['linked']} linked (offset {r['offset']}) | ads {r['ads']} | flagged {len(r['flagged'])}/{r['pages']} "
              f"{r['flag_counts']}")


if __name__ == "__main__":
    main()
