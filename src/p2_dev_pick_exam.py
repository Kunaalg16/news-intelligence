"""
p2_dev_pick_exam.py -- DEV TOOL (18 Oct 2026, task 6). Picks ONE fresh exam page for a paper by a seeded random draw.

WHY    Business Standard has only one never-graded exam page. A pre-registered draw (rule and seed fixed before
       running) picks a fresh one, so nobody chooses an easy or hard page, even unconsciously.
RULE   1. every page of every main-edition PDF of the paper in archive/
       2. drop pages already in any answer key (practice or exam) or listed in the Screen label files
       3. drop pages Screen would skip, ad pages, and pages where the rules find no article
       4. sort by (date, page); random.Random(SEED).choice(...)
WRITES ~/Desktop/exam_<paper>_page.pdf (that one page) and prints which page it is. Changes nothing else.
USAGE  .venv/bin/python src/p2_dev_pick_exam.py "Business Standard"
"""

import glob
import random
import re
import sys
from pathlib import Path

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parent))
from p2_extract import parse_name, is_ad_page
from p2_extract_rules import segment_page
from p2_screen import screen_page
from p2_check import read_key

SEED = 18          # fixed before the first run (18 Oct 2026); never change it to get a different page
ROOT = Path(__file__).resolve().parent.parent


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    paper = sys.argv[1]
    used = set()
    for kp in glob.glob(str(ROOT / "eval" / "*.answer_key.txt")):
        stem = Path(kp).name.replace(".answer_key.txt", "")
        used |= {(stem, pn) for pn in read_key(kp)}
    for f in glob.glob(str(ROOT / "eval" / "*screen*.txt")):
        for m in re.finditer(r"(\d{4}-\d\d-\d\d_[A-Za-z ]+?) p(\d+)", Path(f).read_text(encoding="utf-8")):
            used.add((m[1].strip(), int(m[2])))
    cands = []
    for pdf in sorted(glob.glob(str(ROOT / "archive" / "*" / "*.pdf"))):
        stem = Path(pdf).stem
        date, pub, edition = parse_name(stem)
        if pub != paper or edition:
            continue
        doc = pymupdf.open(pdf)
        for pn, page in enumerate(doc, 1):
            if (stem, pn) in used:
                continue
            v = screen_page(page)
            if v["skip_page"]:
                continue
            res = segment_page(page)
            if is_ad_page(page, res) or not res["articles"]:
                continue
            cands.append((stem, pn))
    if not cands:
        sys.exit(f"No candidate pages for {paper}")
    stem, pn = random.Random(SEED).choice(sorted(cands))
    print(f"{len(cands)} candidate pages; seed {SEED} picked: {stem} p{pn}")
    out = Path.home() / "Desktop" / f"exam_{paper.replace(' ', '_')}_page.pdf"
    src = pymupdf.open(glob.glob(str(ROOT / "archive" / "*" / f"{stem}.pdf"))[0])
    one = pymupdf.open()
    one.insert_pdf(src, from_page=pn - 1, to_page=pn - 1)
    one.save(out)
    print(f"saved {out}")


if __name__ == "__main__":
    main()
