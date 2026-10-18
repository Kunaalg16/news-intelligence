"""
p2_screen.py -- Phase 2.1a Screen, v0.1 (17 Oct 2026; 18 Oct: Kunaal's two changes below). WRITTEN, NOT RUN on gold labels: measured in task 5 with
`p2_check.py --screen`, together with everything else (decision D11). Imported, never run on its own.

WHAT   Decides, before splitting, which parts of a page are not news and must not reach the splitter:
       a whole page, or rectangular zones on it. Returns {"skip_page": bool, "zones": [Rect], "why": [...]}.
WHY    Puzzles, notices, tenders, classifieds, obituary ads and advertorials break the news-layout assumptions the
       splitting rules rely on (decided 15 Oct). Cheapest first: free signals only in v0, no model calls.
v0 TIERS (each found on TUNING pages only, 17 Oct; gate pages unseen)
  S1 no text layer      page with < 50 words, or >= 60% of it covered by dense vector drawing with no text
                        (tenders, full-page ads, drawn market tables; C5, C6 on sheet C)
  S2 labelled frame     a printed frame (stroked rectangle) holding a screen label ("PUBLIC NOTICE", "TENDER",
                        "SUDOKU", "MARKETING INITIATIVE" ...) -> the frame is a zone. On news+ads pages the rules'
                        stories had 0 framed words while 40-80% of left-out text was framed (C13, T6, T13, C1);
                        a frame ALONE is not enough (T11: a real 321-word news box is framed), hence the label.
  S3 frame-heavy page   >= 70% of the page's words sit inside frames AND >= 6 frames -> every frame is a zone
                        (T14, T15: every "story" the rules found there was framed notice text).
                        v0.1 (18 Oct, Kunaal): S3 no longer skips the whole page. A page can be ~10% real articles
                        on top and ~90% bordered notices, so each bordered box is blanked and the unboxed text is kept.
  NO TEXT LAYER LIST    v0.1 (18 Oct, Kunaal): a page skipped by S1 for too few words carries "no_text_layer": True,
                        so it is listed (scorer notes; later the runner's summary -> needs_ocr/), never dropped silently.
  Number tables are already dropped per block by the rules (F9). Puzzles without a frame (TH, BL crosswords) and
  label-less zones (TOI "TIMES TRIBUTE" printed as a picture) are NOT handled in v0: next tier, after labels.
KNOBS  the constants below; the label list becomes per-paper once review/zone_labels.csv (p2_dev_zone_labels.py)
       has been read over the whole archive.
"""

import re

import numpy as np
import pymupdf

MIN_TEXT_WORDS = 50       # S1: fewer words on the page = no text layer
DRAW_TILE = 24            # S1: tile size (pt) for the vector-drawing map
DRAW_MIN = 20             # S1: drawing items in a tile to call it "drawn"
NO_TEXT_SHARE = 0.6       # S1: drawn, text-free share of the page that makes it a skip page
FRAME_MIN_W, FRAME_MIN_H = 60, 40   # S2: frames smaller than this are icons / bullets
FRAMED_PAGE_SHARE = 0.7   # S3
FRAMED_PAGE_MIN = 6       # S3
SCREEN_LABELS = re.compile(
    r"PUBLIC NOTICE|NOTICE INVITING|E-?TENDER|TENDERS? (?:NOTICE|INVITED)|TENDER NOTICE|E-AUCTION|SALE NOTICE|"
    r"CORRIGENDUM|SUDOKU|CROSSWORD|QUIZ|SOLUTION TO|MARKETING INITIATIVE|ADVERTORIAL|"
    r"IN LOVING MEMORY|OBITUAR|REMEMBRANCE|PRAYER MEETING|CHANGE OF NAME|MATRIMONIAL|CLASSIFIED",
    re.I)


def _frames(page):
    W, H = page.rect.width, page.rect.height
    out = []
    for g in page.get_cdrawings():
        if g.get("type") not in ("s", "fs"):          # stroked outlines only (filled boxes are shading)
            continue
        for it in g["items"]:
            if it[0] == "re":
                r = pymupdf.Rect(it[1])
                if FRAME_MIN_W < r.width < 0.9 * W and FRAME_MIN_H < r.height < 0.9 * H:
                    out.append(r)
    return out


def _drawn_text_free_share(page, words):
    W, H = page.rect.width, page.rect.height
    nx, ny = int(W // DRAW_TILE) + 1, int(H // DRAW_TILE) + 1
    draw, text = np.zeros((ny, nx)), np.zeros((ny, nx))
    for g in page.get_cdrawings():
        for it in g["items"]:
            p = it[1]
            if it[0] == "re":
                x, y = (p[0] + p[2]) / 2, (p[1] + p[3]) / 2
            elif it[0] == "qu":                        # a quad: four corner points
                x, y = (p[0][0] + p[2][0]) / 2, (p[0][1] + p[2][1]) / 2
            else:                                      # line / curve: its first point
                x, y = p[0], p[1]
            if 0 <= x < W and 0 <= y < H:
                draw[int(y // DRAW_TILE), int(x // DRAW_TILE)] += 1
    for w in words:
        x, y = (w[0] + w[2]) / 2, (w[1] + w[3]) / 2
        if 0 <= x < W and 0 <= y < H:
            text[int(y // DRAW_TILE), int(x // DRAW_TILE)] += 1
    return float(((draw >= DRAW_MIN) & (text == 0)).mean())


def screen_page(page):
    words = [w for w in page.get_text("words") if w[4].strip()]
    if len(words) < MIN_TEXT_WORDS:
        return {"skip_page": True, "zones": [], "no_text_layer": True,
                "why": [f"S1 no text layer ({len(words)} words) -> needs_ocr list"]}
    share = _drawn_text_free_share(page, words)
    if share >= NO_TEXT_SHARE:
        return {"skip_page": True, "zones": [], "why": [f"S1 {share:.0%} drawn, text-free"]}
    frames = _frames(page)
    C = [((w[0] + w[2]) / 2, (w[1] + w[3]) / 2) for w in words]
    framed = sum(1 for c in C if any(f.contains(pymupdf.Point(c)) for f in frames))
    zones, why = [], []
    if len(frames) >= FRAMED_PAGE_MIN and framed >= FRAMED_PAGE_SHARE * len(words):
        # v0.1: blank each bordered box (outermost only), keep the unboxed text
        for f in sorted(frames, key=lambda r: -r.get_area()):
            if not any(z.contains(f) for z in zones):
                zones.append(f)
        return {"skip_page": False, "zones": zones,
                "why": [f"S3 {framed}/{len(words)} words framed: {len(zones)} boxes blanked, unboxed text kept"]}
    lines = []
    for b in page.get_text("dict")["blocks"]:
        for l in b.get("lines", []):
            t = " ".join(s["text"] for s in l["spans"]).strip()
            if t and SCREEN_LABELS.search(t):
                lines.append((t, pymupdf.Rect(l["bbox"])))
    for f in frames:
        hit = next((t for t, r in lines if f.contains(r)), None)
        if hit and not any(z.contains(f) for z in zones):
            zones = [z for z in zones if not f.contains(z)] + [f]
            why.append(f"S2 frame labelled '{hit[:30]}'")
    return {"skip_page": False, "zones": zones, "why": why}


def screened(point_or_rect, verdict):
    """True if a word centre (Point) or a box's centre (Rect) is screened out by this verdict."""
    if verdict["skip_page"]:
        return True
    p = point_or_rect if isinstance(point_or_rect, pymupdf.Point) else \
        pymupdf.Point((point_or_rect.x0 + point_or_rect.x1) / 2, (point_or_rect.y0 + point_or_rect.y1) / 2)
    return any(z.contains(p) for z in verdict["zones"])
