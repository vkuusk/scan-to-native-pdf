#!/usr/bin/env python
"""Step 3: calibrate typography for a manual and write manuals/<manual>/manual.toml.

Measured on the scanned pages that already have OCR results (step 2), after deskewing:
  - font family: each candidate font renders the OCR text of single body lines, and the
                 rendering is compared with the scan (shape correlation); override with --font
  - body size (from line widths), line pitch, hanging indent of "(1)"-style items
  - size and weight (bold or regular, from stroke thickness) of section headings ("3.1. ..."),
    bold sub-headings, the page title, figure captions and the page number

convert_page.py reads manual.toml; edit it by hand if a value looks wrong.

Usage:
    python scripts/init_fonts.py <manual> [--pages 3-9] [--font century]
"""
import argparse
import json
import statistics as st
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont
from reportlab.pdfbase.pdfmetrics import stringWidth

from common import existing_pages, manual_toml, page_dir, page_types, parse_pages
from fonts import FAMILIES, cap_height, font_dir, load_family
from measure import (BOLD_RATIO, DRAWING_TEXT_INK, HEADING_RE, LABEL_RE, analyse_page, text_ink_share,
                     text_start_after_label)

MAX_SHAPE_SAMPLES = 40     # body lines used for the font shape comparison


def shape_score(sample, pil_font_path, size_px) -> float:
    """Normalised correlation between the scanned line and the text rendered in a candidate font."""
    crop = sample["crop"]
    h, w = crop.shape
    font = ImageFont.truetype(pil_font_path, size_px)
    canvas = Image.new("L", (w + 12, h + 12), 0)
    ImageDraw.Draw(canvas).text((6, 6 + sample["base_in_crop"]), sample["text"], font=font, fill=255, anchor="ls")
    blur = ImageFilter.GaussianBlur(1.5)
    ren = np.asarray(canvas.filter(blur), float)
    scan = np.asarray(Image.fromarray((crop * 255).astype(np.uint8)).filter(blur), float)
    scan = (scan - scan.mean()) / (scan.std() + 1e-6)
    best = -1.0
    for dy in range(0, 13, 2):
        for dx in range(0, 13, 2):
            win = ren[dy:dy + h, dx:dx + w]
            if win.std() == 0:
                continue
            best = max(best, float((scan * (win - win.mean()) / win.std()).mean()))
    return best


def robust(vals):
    """Median after dropping values far from it (OCR errors, merged lines)."""
    if not vals:
        return None
    m = st.median(vals)
    return st.median([v for v in vals if abs(v - m) <= 0.15 * abs(m)] or vals)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("manual")
    ap.add_argument("--pages", help="pages to measure (default: all pages with OCR results)")
    ap.add_argument("--font", choices=list(FAMILIES), help="force a font family instead of detecting it")
    args = ap.parse_args()

    with_ocr = [n for n in existing_pages(args.manual) if (page_dir(args.manual, n) / "ocr/page_res.json").is_file()]
    if not with_ocr:
        sys.exit("error: no pages with OCR results; run ocr_scanned_page.py first")
    pages = parse_pages(args.pages, with_ocr)
    # calibrate on text pages only: skip pages marked drawing/scan_only and unlisted pages that look like drawings
    types = page_types(args.manual)
    skipped = []
    for n in list(pages):
        d = page_dir(args.manual, n)
        if types.get(n) in ("drawing", "scan_only") or (
                types.get(n) != "text" and
                text_ink_share(d / "page.pdf", json.loads((d / "ocr/page_res.json").read_text())) < DRAWING_TEXT_INK):
            pages.remove(n)
            skipped.append(n)
    if skipped:
        print(f"not measuring drawing/scan-only pages: {','.join(map(str, skipped))}")
    if not pages:
        sys.exit("error: no text pages to measure")

    body_lines, special, pitch, indent = [], [], [], []
    for n in pages:
        d = page_dir(args.manual, n)
        pg = analyse_page(d / "page.pdf", json.loads((d / "ocr/page_res.json").read_text()))
        pt, ink = pg["pt"], pg["ink"]
        print(f"p-{n:03d}: skew {pg['angle']:+.2f} deg")
        for b in pg["blocks"]:
            label, text, bands = b["label"], b["text"], b["bands"]
            if not bands or not b["raw"] or label not in ("text", "paragraph_title", "figure_title", "number"):
                continue
            pitch += [(b2["base"] - b1["base"]) * pt for b1, b2 in zip(bands, bands[1:])]
            m = LABEL_RE.match(text)
            if label == "text" and m and m.group(1) != "Note:" and len(bands) >= 2:
                indent.append((st.median(bd["left"] for bd in bands[1:]) - bands[0]["left"]) * pt)
            if len(bands) != 1:
                continue
            bd = bands[0]
            start, body = bd["left"], text
            if m and label == "text":
                start, body = text_start_after_label(ink, bd, 4 * (bd["bottom"] - bd["top"])), m.group(2)
                if start is None:
                    continue
            item = dict(text=body, w=(bd["right"] - start) * pt, h=(bd["bottom"] - bd["top"]) * pt,
                        bold=bd["ratio"] > BOLD_RATIO, ratio=bd["ratio"])
            if HEADING_RE.match(text):
                special.append(dict(item, kind="heading"))
            elif label == "paragraph_title":
                special.append(dict(item, kind="title"))
            elif label == "figure_title":
                special.append(dict(item, kind="caption"))
            elif label == "number":
                special.append(dict(item, kind="page_number"))
            elif item["bold"]:
                special.append(dict(item, kind="subheading"))
            elif len(body) >= 12:
                item["crop"] = ink[bd["top"] - 2:bd["bottom"] + 2, start - 2:bd["right"] + 2].astype(float)
                item["base_in_crop"] = bd["base"] - bd["top"] + 2
                item["px_per_pt"] = 1 / pt
                body_lines.append(item)

    if len(body_lines) < 3:
        sys.exit(f"error: only {len(body_lines)} usable body lines; OCR more pages or choose other --pages")
    pitch_pt = robust([p for p in pitch if p > 0])
    indent_pt = robust(indent) or 0.0

    # ---- font family ------------------------------------------------------------------------------
    def sizes(family, style="regular", items=body_lines):
        f = load_family(family)[style]
        return [it["w"] / stringWidth(it["text"], f, 1.0) for it in items]

    scores = {}
    if not args.font:
        sample = body_lines[:: max(1, len(body_lines) // MAX_SHAPE_SAMPLES)][:MAX_SHAPE_SAMPLES]
        for family in FAMILIES:
            path = str(font_dir() / f"{FAMILIES[family][0]}.pfb")
            size = robust(sizes(family, items=sample))
            scores[family] = st.mean(shape_score(s, path, size * s["px_per_pt"]) for s in sample)
    family = args.font or max(scores, key=scores.get)
    body = robust(sizes(family))

    def measure(kind):
        items = [s for s in special if s["kind"] == kind]
        if not items:
            return None
        bold = sum(s["bold"] for s in items) * 2 > len(items)
        style = "bold" if bold else "regular"
        if kind == "page_number":     # digits: size from glyph height
            size = robust([s["h"] / cap_height(family, style) for s in items])
        else:
            size = robust(sizes(family, style, items))
        return dict(size=round(size, 2), bold=bold, samples=len(items),
                    ratios=" ".join(f"{s['ratio']:.2f}" for s in items))

    sections = {k: measure(k) for k in ("heading", "subheading", "title", "caption", "page_number")}

    # ---- report + manual.toml ----------------------------------------------------------------------
    if scores:
        print("font shape match (higher is better): "
              + ", ".join(f"{f} {s:.3f}" for f, s in sorted(scores.items(), key=lambda x: -x[1])))
    print(f"font: {family}{' (forced)' if args.font else ''}")
    print(f"body: size {body:.2f}pt, line pitch {pitch_pt:.2f}pt, hanging indent {indent_pt:.1f}pt "
          f"({len(body_lines)} lines, {len(pitch)} pitch and {len(indent)} indent samples)")
    for k, v in sections.items():
        print(f"{k}: " + (f"size {v['size']}pt, {'bold' if v['bold'] else 'regular'} ({v['samples']} samples, "
                          f"stroke vs. nearby body: {v['ratios']}; bold above {BOLD_RATIO})" if v else "not found"))

    out = manual_toml(args.manual)
    t = [f"# typography for {args.manual}; written by init_fonts.py, edit as needed",
         f"# measured on pages {','.join(map(str, pages))}; sizes in pt",
         "", "[font]", f'family = "{family}"   # one of: {", ".join(FAMILIES)}',
         "", "[body]", f"size = {body:.2f}", f"pitch = {pitch_pt:.2f}", f"indent = {indent_pt:.1f}"]
    for k, v in sections.items():
        if v:
            t += ["", f"[{k}]", f"size = {v['size']}", f"bold = {str(v['bold']).lower()}"]
    if scores:
        t += ["", "[calibration]", "font_match = { " + ", ".join(f'"{f}" = {s:.4f}' for f, s in scores.items()) + " }"]
    out.write_text("\n".join(t) + "\n")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
