"""
p2_extract_rules.py (was segmenter.py) -- Phase 2.1 method: split one newspaper page into articles using ONLY
what the PDF already contains: every word, its position, its size, its font, and the thin printed lines
between stories. No images, no AI model, no cost.

How it decides, in plain terms:
  1. Body text size  = the size holding the most characters (half-point bins). Other sizes holding a
                       real share of the text (racing results, briefs) count as body text too.
  2. Headlines       = text clearly bigger than body text (HEADLINE_RATIO). A short label in the top 5% of
                       the page ("TIMES CITY", "Sport") is a section heading, not a story.
                       A smaller headline-size line under a headline, with no body text in between, is its
                       sub-headline ("deck"), not a new story.
     Mid-size text   = between body and headline size. Directly under a headline it is a deck; bold and
                       elsewhere it is a brief's headline ("IN BRIEF" items); otherwise left out.
  3. Each piece of body text belongs to the nearest headline ABOVE it that stretches across it -- unless a
     printed separator line sits in between. Body text left over (a feature whose headline sits over the
     middle columns only) joins the story whose text is right beside it.
  4. Photo captions (text just under a photo AND in a different font from body text) and pull-quotes
     (display-font text bigger than body text) are left out of the article body.
  5. Inside each article, columns are rebuilt from where lines start, then read top-to-bottom, left first.

Nothing here refers to a specific newspaper's font names, so it should carry over to other publishers.
The numbers below are the knobs to adjust when the scorer (p2_check.py) shows a systematic mistake.
v3 (14 Oct 2026): fixes F1-F6 below; each can be switched off for an ablation run with env NI_OFF=F1,F3.
v3.2 (18 Oct 2026): F11 -- a headline set in two sizes (2nd line bigger or smaller, same font family, stacked
tightly) is one headline, not two stories. Tried and rejected the same day: "F12" (small headlines under a much
bigger one = subheadings): fixed Mint's Plain Facts page but merged separate stories elsewhere (precision 95.4 -> 87.9).
"""

import os
import re
from collections import Counter

import pymupdf

# ---- tuning knobs (what feedback changes) --------------------------------
HEADLINE_RATIO = 1.35    # headline >= this x body size
BODY_SIZE_TOL = 1.5      # body text within +/- this many points of body size
DECK_GAP = 14            # smaller line this close under a headline = its sub-headline
DECK_GAP_MAX = 40        # v3: ... or up to this far, if no body text sits in between
KICKER_WORDS = 3         # v3: an all-lower-case headline-size label this short = kicker/logo, not a story
RULE_IGNORE_ZONE = 90    # printed lines this close under a headline don't split a story
CAPTION_ZONE = 30        # text this close under a photo, in a non-body font = caption
MIN_ARTICLE_WORDS = 20   # fewer words = label or teaser, not an article
COLUMN_MERGE = 30        # line-start positions closer than this = same column
RULE_MIN_FRAC = 0.8      # a printed line cuts a story only if >= this fraction of the headline's width
EXTRA_BODY_SHARE = 0.15  # v3: another size with >= this share of the main body size's characters is body too
SECTION_TOP = 0.05       # v3: headline-size text ending in the top 5% of the page, <= 4 words = section heading
ADOPT_DIST = 25          # v3: left-over body text joins a story whose text is within this many points
PHOTO_FRAME = 60         # v3: printed lines from a photo's top to this far below it frame the photo + caption
BRIEF_MIN_LINES = 4      # v3: ... with at least this many lines of body text running directly under it
NUMBER_TABLE_SHARE = 0.5 # v3: a text block with >= this share of number words is a data table, not article text
MID_MIN = 1.0            # v3: a bold line in another font this much above body size = a brief's headline
STACK_SIZE_RATIO = 1.35  # v3.2 (F11): stacked headline lines in one font family join even if sizes differ up to this x
# ---------------------------------------------------------------------------

CALLOUT = re.compile(r"^[₹$€£~]?\s*[\d][\d.,%]*")
OFF = set(filter(None, os.environ.get("NI_OFF", "").split(",")))
# F1 caption needs a non-body font   F2 deck by "no body in between"   F3 half-point body size + extra sizes
# F4 section headings                 F5 left-over text adoption        F6 mid-size text: decks + brief headlines
# F7 photo borders are not separators     F8 numeric callouts are not headlines
# F9 number-table blocks dropped     F10 kicker labels inside a story are not headlines
# F11 (18 Oct) headline lines stacked in one font family join even when their sizes differ (TH 9 Oct p6: 62 + 76 pt)


def _family(font: str) -> str:
    return font.split("+")[-1].split("-")[0]


NUM_WORD = re.compile(r"^[-+(]?[₹$]?[\d.,:%/()-]+[%)]?$")


def _is_number_table(b):
    """v3 (F9): a text block where >= NUMBER_TABLE_SHARE of the words are numbers = stock/data table."""
    words = [w for l in b["lines"] for s in l["spans"] for w in s["text"].split()]
    return len(words) >= 8 and sum(bool(NUM_WORD.match(w)) for w in words) >= NUMBER_TABLE_SHARE * len(words)


def _spans(page):
    out = []
    for b in page.get_text("dict")["blocks"]:
        if b["type"] != 0:
            continue
        if "F9" not in OFF and _is_number_table(b):
            continue
        for line in b["lines"]:
            if abs(line["dir"][1]) > 0.1:        # skip rotated margin codes
                continue
            for s in line["spans"]:
                t = s["text"].strip()
                if t:
                    out.append({"bbox": pymupdf.Rect(s["bbox"]), "text": t, "size": s["size"],
                                "font": s["font"], "bold": bool(s["flags"] & 16) or "Bold" in s["font"]
                                or "Black" in s["font"]})
    return out


def _horizontal_rules(page, minlen=40):
    """Thin horizontal printed lines: (y, x0, x1). get_cdrawings = the fast, raw form of get_drawings."""
    rules = []
    for g in page.get_cdrawings():
        for it in g["items"]:
            if it[0] == "l":
                (ax, ay), (bx, by) = it[1], it[2]
                if abs(ay - by) < 1 and abs(ax - bx) > minlen:
                    rules.append((ay, min(ax, bx), max(ax, bx)))
            elif it[0] == "re":
                x0, y0, x1, y1 = it[1]
                if abs(y1 - y0) < 2 and abs(x1 - x0) > minlen:
                    rules.append((min(y0, y1), min(x0, x1), max(x0, x1)))
    return rules


def _overlaps_x(a, b):
    return a.x0 < b.x1 and a.x1 > b.x0


def _merge_headlines(heads, body=()):
    merged = []
    for h in sorted(heads, key=lambda s: (s["bbox"].y0, s["bbox"].x0)):
        for m in merged:
            same_size = abs(h["size"] - m["size"]) < 1.5
            stacked = -2 <= h["bbox"].y0 - m["bbox"].y1 < h["size"] * 1.3
            if not same_size and "F11" not in OFF and h.get("font") and m.get("font") \
                    and _family(h["font"]) == _family(m["font"]) \
                    and max(h["size"], m["size"]) <= STACK_SIZE_RATIO * min(h["size"], m["size"]) \
                    and -2 <= h["bbox"].y0 - m["bbox"].y1 < 0.4 * min(h["size"], m["size"]):
                same_size = True                  # v3.2 F11: one headline set in two sizes
            same_line = abs(h["bbox"].y0 - m["bbox"].y0) < max(2, 0.3 * h["size"]) and 0 <= h["bbox"].x0 - m["bbox"].x1 < 15
            if same_size and ((stacked and _overlaps_x(h["bbox"], m["bbox"])) or same_line):
                m["bbox"] |= h["bbox"]
                m["text"] += " " + h["text"]
                break
        else:
            merged.append({"bbox": pymupdf.Rect(h["bbox"]), "text": h["text"], "size": h["size"], "deck": [],
                           "minor": h.get("minor", False), "font": h.get("font")})
    for m in merged:
        m["own_y1"] = m["bbox"].y1          # bottom of the headline itself, before any deck is folded in
    # fold sub-headlines (decks) into the bigger headline above them
    merged.sort(key=lambda m: m["bbox"].y0)
    final = []
    for m in merged:
        def is_deck(p):
            if not (p["size"] > m["size"] + 3 and _overlaps_x(m["bbox"], p["bbox"])):
                return False
            gap = m["bbox"].y0 - p["bbox"].y1
            if 0 <= gap < DECK_GAP:
                return True
            if "F2" in OFF:
                return False
            gap = m["bbox"].y0 - p["own_y1"]     # v3: measured from the headline itself, so decks don't chain
            if not (0 <= gap < DECK_GAP_MAX) or p["deck"]:
                return False
            # v3: further down is still a deck if no body text sits between headline and this line
            x0, x1 = max(m["bbox"].x0, p["bbox"].x0), min(m["bbox"].x1, p["bbox"].x1)
            return not any(p["bbox"].y1 - 1 < s["bbox"].y0 and s["bbox"].y1 < m["bbox"].y0 + 1
                           and s["bbox"].x0 < x1 and s["bbox"].x1 > x0 for s in body)
        parent = next((p for p in reversed(final) if is_deck(p)), None)
        if parent:
            parent["deck"].append(m["text"])
            parent["bbox"] |= m["bbox"]
        else:
            final.append(m)
    return final


def _columns(spans):
    # lines indented beside a drop-cap letter must not count as a column of their own
    caps = [s["bbox"] for s in spans if s.get("dropcap")]
    def beside_cap(s):
        return any(c.x1 - 2 <= s["bbox"].x0 <= c.x1 + 60 and c.y0 - 4 <= s["bbox"].y0 <= c.y1 for c in caps)
    starts = Counter(round(s["bbox"].x0 / 2) * 2 for s in spans if not beside_cap(s))
    xs = sorted(x for x, c in starts.items() if c >= 3) or [min(s["bbox"].x0 for s in spans)]
    cols = [xs[0]]
    for x in xs[1:]:
        if x - cols[-1] > COLUMN_MERGE:
            cols.append(x)
    bounds = cols + [1e9]
    return [(bounds[k] - 2 if k else -1e9, bounds[k + 1] - 2) for k in range(len(cols))]


def _clean(text: str) -> dict:
    """Fix known rough edges and pull out page-jump markers."""
    info = {"continues_to": None, "see_also_page": None}
    text = re.sub(r"[\x00-\x08\x0b-\x1f]", "", text)
    text = re.sub(r"(\s*\b[CMYK]\b){3,}\s*$", "", text)                 # printer marks
    m = re.search(r"CONTINUED ON\s*»?\s*PAGE\s*(\d+)", text)
    if m:
        info["continues_to"] = int(m.group(1))
        text = text[:m.start()] + text[m.end():]
    m = re.search(r"DETAILS ON\s*»?\s*PAGE\s*(\d+)", text)
    if m:
        info["see_also_page"] = int(m.group(1))
        text = text[:m.start()] + text[m.end():]
    text = re.sub(r"\s+[A-Z][A-Z ]{4,}\s*»?\s*PAGE\s*\d+\s*$", "", text)   # teaser pointer at the end
    m = re.search(r"»?\s*Page\s+(\d+)\s*$", text)
    if m:
        info["see_also_page"] = int(m.group(1))
        text = text[:m.start()]
    text = re.sub(r"(\w)- ([a-z])", r"\1\2", text)                      # re-join hyphenated words
    info["text"] = re.sub(r"\s+", " ", text).strip()
    return info


def _run_below(s, body):
    """Lines of body text running down from just under span s, each starting at s's left edge (+-6 pt)."""
    col = sorted((b for b in body if abs(b["bbox"].x0 - s["bbox"].x0) < 6 and b["bbox"].y0 >= s["bbox"].y1 - 1),
                 key=lambda b: b["bbox"].y0)
    n, y = 0, s["bbox"].y1
    for b in col:
        if b["bbox"].y0 - y > 14:
            break
        n, y = n + 1, max(y, b["bbox"].y1)
    return n


def _body_sizes(sp):
    """Main body size + any other size carrying a real share of the running text."""
    if "F3" in OFF:
        return Counter(round(s["size"]) for s in sp).most_common(1)[0][0], []
    chars = Counter()
    for s in sp:
        chars[round(s["size"] * 2) / 2] += len(s["text"])
    main = max((z for z in chars if 5 <= z <= 13), key=lambda z: chars[z], default=chars.most_common(1)[0][0])
    extra = [z for z, n in chars.items() if z != main and abs(z - main) >= BODY_SIZE_TOL
             and main - 3 <= z < main and n >= EXTRA_BODY_SHARE * chars[main]]
    return main, extra


def segment_page(page) -> dict:
    sp = _spans(page)
    if not sp:
        return {"body_size": None, "articles": [], "grey": [], "labels": []}
    body_size, extra_sizes = _body_sizes(sp)
    H = page.rect.height

    def is_body_size(s):
        return abs(s["size"] - body_size) < BODY_SIZE_TOL or any(abs(s["size"] - z) < 0.5 for z in extra_sizes)

    head_min = HEADLINE_RATIO * body_size
    heads = [s for s in sp if s["size"] >= head_min and len(s["text"]) > 1]
    drop_caps = [dict(s, dropcap=True) for s in sp if s["size"] >= 2 * body_size and len(s["text"]) == 1]
    section = []
    if "F4" not in OFF:     # v3: page section headings ("TIMES CITY", "Sport") are labels, not stories
        section = [s for s in heads if s["bbox"].y1 < SECTION_TOP * H]
        heads = [s for s in heads if s not in section]

    if "F8" not in OFF:     # v3: "₹8.4 lakh", "70%", "$50 bn": data callouts, not story headlines
        def stacked(s):     # another headline line of about the same size directly above/below it
            return any(o is not s and abs(o["size"] - s["size"]) < 1.5 and _overlaps_x(o["bbox"], s["bbox"])
                       and (abs(o["bbox"].y0 - s["bbox"].y1) < 0.5 * s["size"]
                            or abs(s["bbox"].y0 - o["bbox"].y1) < 0.5 * s["size"]) for o in heads)
        callout = [s for s in heads if len(s["text"].split()) <= 3 and CALLOUT.match(s["text"]) and not stacked(s)]
        heads = [s for s in heads if s not in callout]
        section += callout

    if "F10" not in OFF:    # v3: "street cred": a short label sitting inside a story's columns is not a headline
        def kicker(h):      # all lower-case and short: a recurring column/sidebar logo, never a story headline
            t = h["text"]
            return len(t.split()) <= KICKER_WORDS and any(c.isalpha() for c in t) and t == t.lower()
        heads_m = _merge_headlines(heads)
        kick = [h for h in heads_m if kicker(h)]
        if kick:
            heads = [s for s in heads if not any(k["bbox"].contains(s["bbox"]) for k in kick)]
            section += [{"text": k["text"]} for k in kick]

    photos = [pymupdf.Rect(i["bbox"]) for i in page.get_image_info() if pymupdf.Rect(i["bbox"]).width > 40]
    body_all = [s for s in sp if is_body_size(s)]
    fonts = Counter()
    for s in body_all:
        fonts[s["font"]] += len(s["text"])
    body_font = fonts.most_common(1)[0][0] if fonts else None

    def is_mid(s):
        """v3: between body and headline size -- or a bold line in another family >= 1 pt above body size
        (brief headlines can sit inside the body-size tolerance)."""
        if len(s["text"]) <= 1 or s["size"] >= head_min:
            return False
        if s["size"] >= body_size + BODY_SIZE_TOL and not is_body_size(s):
            return True
        return (s["bold"] and s["size"] >= body_size + MID_MIN and body_font
                and _family(s["font"]) != _family(body_font))

    if "F6" not in OFF:
        body_all = [s for s in body_all if not is_mid(s)]

    def is_caption(s):
        if "F1" not in OFF and s["font"] == body_font:     # v3: captions are set in a different font
            return False
        return any(0 <= s["bbox"].y0 - r.y1 < CAPTION_ZONE and s["bbox"].x0 < r.x1 and s["bbox"].x1 > r.x0
                   for r in photos)

    body = [s for s in body_all if not is_caption(s)] + drop_caps

    # v3: mid-size text (between body and headline size): decks under headlines, bold brief headlines
    mid_deck = []
    if "F6" not in OFF:
        mids = [s for s in sp if is_mid(s)]
        big = _merge_headlines(heads, body)
        for s in mids:
            above = [h for h in big if _overlaps_x(h["bbox"], s["bbox"]) and -2 <= s["bbox"].y0 - h["bbox"].y1 < DECK_GAP_MAX]
            h = min(above, key=lambda h: s["bbox"].y0 - h["bbox"].y1, default=None)
            if h and not any(h["bbox"].y1 - 1 < b["bbox"].y0 and b["bbox"].y1 < s["bbox"].y0 + 1
                             and _overlaps_x(b["bbox"], s["bbox"]) for b in body):
                mid_deck.append(s)                 # sits right under a headline: part of its deck
            elif s["bold"] and _run_below(s, body) >= BRIEF_MIN_LINES:
                heads.append(dict(s, minor=True))  # a brief's own headline: a column of body text runs under it
    headlines = _merge_headlines(heads, body)
    # mid-size deck lines join the body pool so they are assigned to the headline above them
    body += mid_deck
    rules = _horizontal_rules(page)
    if "F7" not in OFF:     # v3: lines framing a photo and its caption are not story separators
        rules = [(ry, rx0, rx1) for ry, rx0, rx1 in rules
                 if not any(r.y0 - 4 < ry < r.y1 + PHOTO_FRAME and rx0 > r.x0 - 6 and rx1 < r.x1 + 6
                            for r in photos)]

    groups = {i: [] for i in range(len(headlines))}
    grey = []
    for s in body:
        cx = (s["bbox"].x0 + s["bbox"].x1) / 2
        best, best_gap = None, 1e9
        for i, h in enumerate(headlines):
            hb = h["bbox"]
            if not (hb.x0 - 6 <= cx <= hb.x1 + 6) or hb.y1 > s["bbox"].y0 + 2:
                continue
            if any(hb.y1 + RULE_IGNORE_ZONE < ry < s["bbox"].y0 - 1 and rx0 <= cx <= rx1
                   and (rx1 - rx0) >= RULE_MIN_FRAC * hb.width for ry, rx0, rx1 in rules):
                continue
            gap = s["bbox"].y0 - hb.y1
            if gap < best_gap:
                best, best_gap = i, gap
        (groups[best] if best is not None else grey).append(s)

    if "F5" not in OFF:
        # v3: left-over body text joins the story whose text sits right beside it (feature layouts where the
        # headline spans only the middle columns). Repeats so a whole column can chain on, line by line.
        # Only text set like the story's own body (same font family, same size) can join it: keeps tables,
        # scorecards and sidebars in another face out.
        def style(g):
            c = Counter((_family(x["font"]), round(x["size"] * 2) / 2) for x in g if not x.get("dropcap"))
            return c.most_common(1)[0][0] if c else None
        styles = {i: style(groups[i]) for i in groups}
        changed = True
        while changed and grey:
            changed = False
            for s in list(grey):
                best, best_d = None, ADOPT_DIST
                for i, h in enumerate(headlines):
                    if h["bbox"].y0 > s["bbox"].y1 or not groups[i] or not styles[i]:
                        continue                      # never adopt into a story whose headline is below
                    fam, size = styles[i]
                    if _family(s["font"]) != fam or abs(s["size"] - size) > 0.6:
                        continue
                    for r in [g["bbox"] for g in groups[i]]:
                        dx = max(r.x0 - s["bbox"].x1, s["bbox"].x0 - r.x1, 0)
                        dy = max(r.y0 - s["bbox"].y1, s["bbox"].y0 - r.y1, 0)
                        d = (dx * dx + dy * dy) ** 0.5
                        if d < best_d:
                            best, best_d = i, d
                if best is not None:
                    groups[best].append(s)
                    grey.remove(s)
                    changed = True

    articles, labels = [], []
    for i, h in enumerate(headlines):
        g = groups[i]
        # drop pull-quotes: display-font text bigger than body text
        chars = Counter()
        for s in g:
            chars[_family(s["font"])] += len(s["text"])
        main_family = chars.most_common(1)[0][0] if chars else None
        kept = [s for s in g if s.get("dropcap") or s in mid_deck
                or not (_family(s["font"]) != main_family and s["size"] > body_size + 0.5)]
        tokens, pieces = [], []
        for c0, c1 in (_columns(kept) if kept else []):
            col = sorted([s for s in kept if c0 <= s["bbox"].x0 < c1],
                         key=lambda s: (round(s["bbox"].y0), s["bbox"].x0))
            tokens.extend(col)
            if col:
                pieces.append(" ".join(s["text"] for s in col))   # for the free 'order' check only
        byline = []
        while tokens and tokens[0]["bold"] and not tokens[0].get("dropcap") and len(byline) < 3 \
                and len(" ".join(t["text"] for t in byline + [tokens[0]]).split()) <= 5:
            byline.append(tokens.pop(0))
        raw = ""
        for t in tokens:
            raw += t["text"] + ("" if t.get("dropcap") else " ")
        info = _clean(raw)
        entry = {"headline": h["text"], "deck": " ".join(h["deck"]),
                 "byline": " ".join(t["text"] for t in byline),
                 "body": info["text"], "words": len(info["text"].split()),
                 "continues_to": info["continues_to"], "see_also_page": info["see_also_page"],
                 "_hbox": h["bbox"], "_boxes": [s["bbox"] for s in kept + byline], "_pieces": pieces}
        (articles if entry["words"] >= MIN_ARTICLE_WORDS else labels).append(entry)
    labels += [{"headline": s["text"], "words": 0, "section": True} for s in section]

    return {"body_size": body_size, "articles": articles, "grey": grey, "labels": labels}
