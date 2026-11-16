#!/usr/bin/env python
"""Survey the OCR'd pages of a manual and suggest how each page should be handled.

For every page with OCR results it measures
  - size           page format (A4/A3/...)
  - text_ink       share of the page's ink inside OCR text blocks (text pages ~0.6-0.7, drawings < 0.05)
  - outside_ink    share of ink outside every OCR block (a drawing the layout model did not detect)
  - image_area     share of the page area in OCR image blocks
  - words          words in text blocks; prose = words in paragraph-like blocks (>= PROSE_MIN words),
                   i.e. legends, notes, descriptions worth converting even on a drawing page
and suggests a class:
  text     convert normally (convert_page.py)
  tables   most text is in tables (parts lists, adjustment tables) — the OCR returns them as HTML;
           converting them needs a table renderer (not implemented yet; tables are kept as pictures)
  mixed    mostly drawing, but with prose worth converting (legends/notes) — needs a mode that keeps
           the drawing and replaces only the text areas (not implemented yet)
  drawing  schematic / PCB / exploded view with only labels: keep the scanned page

Writes manuals/<manual>/output/survey.md and a thumbnail contact sheet survey-thumbs.png next to it.
Classification is a suggestion; the decision goes into manuals/<manual>/pages.toml.

Usage:
    python scripts/survey_pages.py <manual> [--pages 1-34]
"""
import argparse
import json
import re
import sys

import numpy as np
import pypdfium2 as pdfium
from PIL import Image, ImageDraw, ImageFont

from common import existing_pages, manual_out, page_dir, page_types, parse_pages
from measure import DRAWING_TEXT_INK, INK, TEXT_LABELS, clean_latex

PROSE_MIN = 12          # words in a block for it to count as prose (legend, note, description)
MIXED_PROSE = 40        # prose words on a drawing-like page to call it "mixed"
TEXT_INK_TEXT = 0.35    # text_ink above this -> text page
TABLE_WORDS = 150       # words inside OCR table blocks for a page to count as "tables"


def page_format(w, h):
    a, b = sorted((w, h))
    for name, (x, y) in {"A5": (420, 595), "A4": (595, 842), "Letter": (612, 792), "A3": (842, 1191)}.items():
        if abs(a - x) < 15 and abs(b - y) < 15:
            return name
    return f"{a:.0f}x{b:.0f}"


def measure(manual, n):
    d = page_dir(manual, n)
    res = json.loads((d / "ocr/page_res.json").read_text())
    page = pdfium.PdfDocument(d / "page.pdf")[0]
    img = page.render(scale=1).to_pil().convert("L")
    ink = np.asarray(img) < INK
    total = max(1, int(ink.sum()))
    k = img.width / res["width"]
    in_text = np.zeros_like(ink)
    in_any = np.zeros_like(ink)
    image_area = 0.0
    words = prose = table_words = 0
    n_text = n_img = 0
    for b in res["parsing_res_list"]:
        x0, y0, x1, y1 = (int(v * k) for v in b["block_bbox"])
        in_any[max(0, y0):y1, max(0, x0):x1] = True
        label = b["block_label"]
        if label == "table":     # parts lists, adjustment tables: text, returned by the OCR as HTML
            table_words += len(re.sub(r"<[^>]+>", " ", b["block_content"]).split())
        if label in TEXT_LABELS:
            in_text[max(0, y0):y1, max(0, x0):x1] = True
            wc = len(clean_latex(b["block_content"]).split())
            words += wc
            n_text += 1
            if wc >= PROSE_MIN:
                prose += wc
        elif label in ("image", "chart", "table", "seal"):
            image_area += (x1 - x0) * (y1 - y0) / (img.width * img.height)
            n_img += 1
    text_ink = float((ink & in_text).sum() / total)
    outside = float((ink & ~in_any).sum() / total)
    if text_ink >= TEXT_INK_TEXT:
        cls = "text"
    elif table_words >= TABLE_WORDS:
        cls = "tables"
    elif prose >= MIXED_PROSE:
        cls = "mixed"
    elif text_ink >= DRAWING_TEXT_INK:
        cls = "text?"      # borderline: look at it
    else:
        cls = "drawing"
    w_pt, h_pt = img.width, img.height       # scale=1 -> 1 px per pt
    return dict(page=n, size=page_format(w_pt, h_pt), text_ink=text_ink, outside=outside,
                image_area=image_area, words=words, prose=prose, table_words=table_words,
                n_text=n_text, n_img=n_img, cls=cls, thumb=img)


def contact_sheet(rows, path, cols=6, tw=220):
    th = int(tw * 1.42)
    sheet = Image.new("RGB", (cols * tw, ((len(rows) + cols - 1) // cols) * (th + 22)), "white")
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default()
    colour = {"text": (0, 120, 0), "mixed": (200, 120, 0), "drawing": (180, 0, 0), "text?": (120, 0, 160),
              "tables": (0, 70, 200)}
    for i, r in enumerate(rows):
        t = r["thumb"].copy()
        t.thumbnail((tw - 8, th - 8))
        x, y = (i % cols) * tw, (i // cols) * (th + 22)
        sheet.paste(t, (x + 4, y + 22))
        draw.text((x + 4, y + 4), f"p{r['page']} {r['cls']} {r['size']} ink {r['text_ink']:.0%} prose {r['prose']}",
                  fill=colour.get(r["cls"], (0, 0, 0)), font=font)
    sheet.save(path)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("manual")
    ap.add_argument("--pages", help="pages to survey (default: all with OCR results)")
    args = ap.parse_args()

    with_ocr = [n for n in existing_pages(args.manual) if (page_dir(args.manual, n) / "ocr/page_res.json").is_file()]
    if not with_ocr:
        sys.exit("error: no pages with OCR results; run ocr_scanned_page.py first")
    pages = parse_pages(args.pages, with_ocr)
    missing = [n for n in existing_pages(args.manual) if n not in with_ocr]
    types = page_types(args.manual)

    rows = [measure(args.manual, n) for n in pages]
    lines = [f"# Page survey: {args.manual}", "",
             "| page | size | text ink | ink outside blocks | image+table area | text blocks | words | prose words "
             "| table words | suggested | pages.toml |",
             "|---:|---|---:|---:|---:|---:|---:|---:|---:|---|---|"]
    for r in rows:
        lines.append(f"| {r['page']} | {r['size']} | {r['text_ink']:.0%} | {r['outside']:.0%} | {r['image_area']:.0%} "
                     f"| {r['n_text']} | {r['words']} | {r['prose']} | {r['table_words']} | **{r['cls']}** "
                     f"| {types.get(r['page'], '')} |")
    by = {}
    for r in rows:
        by.setdefault(r["cls"], []).append(r["page"])
    lines += ["", "Suggested: " + "; ".join(f"{k}: {', '.join(map(str, v))}" for k, v in sorted(by.items()))]
    if missing:
        lines.append(f"Not surveyed (no OCR): {', '.join(map(str, missing))}")
    out = manual_out(args.manual)
    (out / "survey.md").write_text("\n".join(lines) + "\n")
    contact_sheet(rows, out / "survey-thumbs.png")
    print("\n".join(lines))
    print(f"\nwrote {out / 'survey.md'} and {out / 'survey-thumbs.png'}")


if __name__ == "__main__":
    main()
