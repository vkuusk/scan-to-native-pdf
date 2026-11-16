#!/usr/bin/env python
"""Step 4: regenerate scanned pages as native PDFs.

    manuals/<manual>/output/pages/p-NNN/page.pdf + ocr/page_res.json
      + manuals/<manual>/manual.toml             (step 3)
      + manuals/<manual>/corrections/p-NNN.toml  (optional, hand-made OCR fixes)
    ->  manuals/<manual>/output/pages/p-NNN/result.pdf  and  warnings.txt (if any)

How a page is rebuilt:
  - the page is deskewed and every OCR text block is measured line by line on the scan
  - the block's words are distributed over the measured lines (line breaks chosen to best match
    each scanned line's width), set in the manual's font and stretched to the scanned line width,
    so indents, centering, justification and line breaks follow the original
  - pictures, tables and anything else that is not text are cropped from the (straightened) scan

Pages marked drawing/scan_only in manuals/<manual>/pages.toml, and unlisted pages that look like
schematics or PCB layouts (little ink inside text blocks), are skipped; a stale result.pdf is removed
so the merge step uses the scanned page.

Corrections file format (match = how the block's OCR text starts):
    [[block]]
    match = "(2) Move Record Head"
    text = "(2) Move Record Head Indicator ... record and playback heads."   # replace the text
    [[block]]
    match = "Fig. 3.1"
    style = "caption"     # force a style: body, heading, subheading, title, caption, page_number
    [[block]]
    match = "xyz"
    drop = true           # leave the block out
    [[block]]
    match = "Supply Tape Guide Check Bar"
    image = true          # keep the block as a picture from the scan
    [[block]]
    match = "(2) Move Record Head"
    lines_below = 1       # the OCR box missed scanned lines: grow it (also lines_above)

Usage:
    python scripts/convert_page.py <manual> [--pages 5,7-9]
"""
import argparse
import json
import math
import re
import statistics as st
import sys
import tomllib

from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

import cleanup_diagrams
import table_render
from common import (cleanup_level, corrections_file, existing_pages, manual_out, manual_toml, page_dir, page_types,
                    parse_pages,
                    tables_as_image)
from fonts import load_family
from measure import (BOLD_RATIO, DPI, DRAWING_TEXT_INK, HEADING_RE, LABEL_RE, TEXT_LABELS, analyse_page,
                     text_ink_share, text_start_after_label, typographic)

IMAGE_PAD_PT = 3.0         # margin around cropped pictures
MIN_HSCALE = 0.85          # lines too long for the scanned width are condensed down to this
MAX_WORDSPACE_EM = 0.6     # extra space between words beyond this -> set the line unjustified
FIT_WARN = (0.80, 1.12)    # natural/scanned width outside this range -> probably wrong OCR text
STYLES = ("body", "heading", "subheading", "title", "caption", "page_number")


def load_manual(manual):
    path = manual_toml(manual)
    if not path.is_file():
        sys.exit(f"error: {path} not found; run init_fonts.py first")
    cfg = tomllib.loads(path.read_text())
    fonts = load_family(cfg["font"]["family"])
    body = cfg["body"]
    styles = {}
    for s in STYLES:
        sec = cfg.get(s, {})
        bold = sec.get("bold", s in ("heading", "subheading"))
        styles[s] = (fonts["bold" if bold else "regular"], float(sec.get("size", body["size"])))
    return styles, float(body["pitch"]), fonts


def load_corrections(manual, n):
    path = corrections_file(manual, n)
    if not path.is_file():
        return []
    items = tomllib.loads(path.read_text()).get("block", [])
    for c in items:
        c["used"] = False
    return items


def _norm(s):
    return re.sub(r"[^0-9a-z]", "", s.lower())


def repair_ocr_text(blocks, styles, warnings):
    """Undo two common OCR slips (blocks without a correction only):
      - a line repeated at the end of one block and the start of another: keep it only where it starts
        the block (list items begin blocks)
      - a block returned with no text while its line was appended to a neighbouring block (e.g. a
        section heading glued onto the previous paragraph): move the line back if it fits the
        empty block's scanned width"""
    for a in blocks:
        segs = a["raw"].split("\n")
        if len(segs) < 2 or a.get("fix"):     # never touch blocks with a hand correction
            continue
        tail = _norm(typographic(segs[-1]))
        if tail and any(o is not a and _norm(typographic(o["raw"].split("\n")[0])) == tail for o in blocks):
            warnings.append(f'auto-fix: removed line repeated in the next block: "{segs[-1][:50]}"')
            a["raw"] = "\n".join(segs[:-1])
            a["text"] = typographic(a["raw"])

    for e in blocks:
        if e["raw"] or len(e["bands"]) != 1 or e.get("fix"):
            continue
        bd = e["bands"][0]
        target = (bd["right"] - bd["left"]) * 72 / DPI
        best = None
        for b in blocks:
            segs = b["raw"].split("\n")
            if b is e or len(segs) < 2 or b.get("fix"):
                continue
            for pos in (0, -1):
                seg = typographic(segs[pos]).strip()
                font, size = styles["heading" if HEADING_RE.match(seg) else "body"]
                r = stringWidth(seg, font, size) / max(target, 1.0)
                if 0.85 <= r <= 1.15 and (best is None or abs(r - 1) < abs(best[0] - 1)):
                    best = (r, b, pos)
        if best:
            _, b, pos = best
            segs = b["raw"].split("\n")
            seg = segs.pop(pos)
            b["raw"], b["text"] = "\n".join(segs), typographic("\n".join(segs))
            e["raw"], e["text"] = seg.strip(), typographic(seg.strip())
            warnings.append(f'auto-fix: block without OCR text got the line "{seg.strip()[:50]}" '
                            f"that the OCR had attached to a neighbouring block")


def block_style(b):
    """Default style of a text block from its OCR label and text."""
    label, text = b["label"], b["raw"]
    if label in ("figure_title", "figure_caption", "table_title", "chart_title"):
        return "caption"
    if label == "number":
        return "page_number"
    if HEADING_RE.match(text):
        return "heading"
    if label in ("paragraph_title", "doc_title"):
        return "title"
    return "body"


def distribute(words, lines):
    """Split words over lines so each line's natural width best matches its scanned width.
    lines: [(font, size, target_width_pt)]. Returns a list of word lists (one per line)."""
    n, m = len(lines), len(words)
    if m < n:
        return [[w] for w in words] + [[] for _ in range(n - m)]
    # prefix widths per distinct font
    pref, space = {}, {}
    for f, s, _ in lines:
        if (f, s) not in pref:
            acc = [0.0]
            for w in words:
                acc.append(acc[-1] + stringWidth(w, f, s))
            pref[(f, s)], space[(f, s)] = acc, stringWidth(" ", f, s)

    def cost(i, j, k):            # line i holds words[j:k]
        f, s, target = lines[i]
        nat = pref[(f, s)][k] - pref[(f, s)][j] + (k - j - 1) * space[(f, s)]
        r = nat / max(target, 1.0)
        return (r - 1) ** 2 * (4 if r > 1 else 1)   # overflowing a line is worse than a gap

    INF = float("inf")
    best = [[INF] * (m + 1) for _ in range(n + 1)]
    back = [[0] * (m + 1) for _ in range(n + 1)]
    best[0][0] = 0.0
    for i in range(1, n + 1):
        for k in range(i, m - (n - i) + 1):
            for j in range(i - 1, k):
                if best[i - 1][j] < INF:
                    c = best[i - 1][j] + cost(i - 1, j, k)
                    if c < best[i][k]:
                        best[i][k], back[i][k] = c, j
    out, k = [], m
    for i in range(n, 0, -1):
        j = back[i][k]
        out.append(words[j:k])
        k = j
    return out[::-1]


def put(c, x, y, s, font, size, wordspace=0.0, hscale=100.0):
    t = c.beginText(x, y)
    t.setFont(font, size)
    t.setWordSpace(wordspace)
    t.setHorizScale(hscale)
    t.textOut(s)
    c.drawText(t)


def winansi(s, warn):
    try:
        s.encode("cp1252")
        return s
    except UnicodeEncodeError:
        bad = sorted({ch for ch in s if not ch.encode("cp1252", "ignore")})
        warn(f"characters not in the font encoding replaced by '?': {' '.join(bad)}")
        return "".join(ch if ch.encode("cp1252", "ignore") else "?" for ch in s)


def draw_picture(c, pg, box, page_h, cleanup_level):
    """Crop a region of the straightened scan, clean it up (cleanup_diagrams.py) and draw it."""
    pad = IMAGE_PAD_PT / pg["pt"]
    w, h = pg["img"].size
    x0, y0 = max(0, int(box[0] - pad)), max(0, int(box[1] - pad))
    x1, y1 = min(w, int(box[2] + pad)), min(h, int(box[3] + pad))
    if x1 <= x0 or y1 <= y0:
        return
    crop = cleanup_diagrams.clean(pg["picture_img"].crop((x0, y0, x1, y1)), cleanup_level, DPI, pg["source"])
    pt = pg["pt"]
    cleanup_diagrams.draw(c, crop, x0 * pt, page_h - y1 * pt, (x1 - x0) * pt, (y1 - y0) * pt)


def convert(manual, n, styles, pitch_pt, cleanup_level=1, family_fonts=None, tables_image=False):
    d = page_dir(manual, n)
    res = json.loads((d / "ocr/page_res.json").read_text())
    fixes = load_corrections(manual, n)
    block_fix, extend = {}, {}
    for i, b in enumerate(res["parsing_res_list"]):
        fix = next((f for f in fixes if "match" in f and f["match"]
                    and b["block_content"].strip().startswith(f["match"])), None)
        if fix:
            fix["used"] = True
            block_fix[i] = fix
            if fix.get("lines_above") or fix.get("lines_below"):
                extend[i] = (int(fix.get("lines_above", 0)), int(fix.get("lines_below", 0)))
    warnings = []
    body_font, body_size = styles["body"]

    def set_text(b, raw):
        b["raw"] = raw.strip()
        b["text"] = typographic(b["raw"])

    def prepare(blocks):
        """Apply corrections and undo typical OCR slips before lines are assigned to blocks."""
        px = DPI / 72
        for i, b in enumerate(blocks):
            b["fix"] = block_fix.get(i)
            for f in fixes:     # position matches: at = [x, y] in pt, inside the block's box
                if "at" in f and not b["fix"]:
                    x, y = f["at"][0] * px, f["at"][1] * px
                    if b["box"][0] <= x <= b["box"][2] and b["box"][1] <= y <= b["box"][3]:
                        b["fix"], f["used"] = f, True
            if b["fix"] and "text" in b["fix"]:
                set_text(b, b["fix"]["text"])
        repair_ocr_text([b for b in blocks if b["label"] in TEXT_LABELS], styles, warnings)

    def need(b):
        """Lines the block's text needs at the scanned line width (None: unknown)."""
        if not b["raw"] or not b["bands"]:
            return None
        width = max((bd["right"] - bd["left"]) for bd in b["bands"]) * 72 / DPI   # full line width
        return sum(max(1, math.ceil(stringWidth(seg, body_font, body_size) / width - 0.05))
                   for seg in b["text"].split("\n") if seg.strip())

    pg = analyse_page(d / "page.pdf", res, extend, pitch_pt, prepare, need)
    pt, ink, page_h = pg["pt"], pg["ink"], pg["height_pt"]
    warnings += [f"text line not covered by any OCR block (missing from output): {o}" for o in pg["orphans"]]

    out = d / "result.pdf"
    c = canvas.Canvas(str(out), pagesize=(pg["width_pt"], page_h), initialFontName=styles["body"][0])
    c.setTitle(f"{manual} page {n}")
    c.setCreator("scan-to-native-pdf")

    for b in pg["blocks"]:
        fix = b["fix"]
        if fix and fix.get("drop"):
            continue
        short = b["raw"][:40].replace("\n", " ") or f"<no OCR text> at y={b['box'][1] * pt:.0f}pt"

        def warn(msg):
            w = f'block "{short}": {msg}'
            if w not in warnings:
                warnings.append(w)

        if b["label"] == "image" or (fix and fix.get("image")):
            draw_picture(c, pg, b["box"], page_h, cleanup_level)
            continue
        if b["label"] == "table":
            html_text = (fix or {}).get("text", b["raw"])   # a correction may replace the table HTML
            if tables_image or not table_render.render(
                    c, pg, b["box"], html_text, family_fonts, styles["body"][1], put, distribute, warn):
                draw_picture(c, pg, b["box"], page_h, cleanup_level)
            continue
        if b["label"] in TEXT_LABELS and b["bands"] and not b["raw"]:
            x0, y0 = b["box"][0] * pt, b["box"][1] * pt
            warn(f"OCR returned no text for this block; kept as a picture from the scan. To set its text, "
                 f"add a correction with at = [{x0 + 3:.0f}, {y0 + 3:.0f}] and text = \"...\"")
            draw_picture(c, pg, b["box"], page_h, cleanup_level)
            continue
        if not b["bands"]:
            if b["label"] in TEXT_LABELS and b["raw"]:
                warn("no scanned line left for this block (its lines went to an overlapping block); "
                     "text not drawn — check the neighbouring blocks")
            elif b["label"] not in TEXT_LABELS:
                draw_picture(c, pg, b["box"], page_h, cleanup_level)   # tables, formulas, unknown labels: keep as picture
            continue

        style = (fix or {}).get("style") or block_style(b)
        bands = b["bands"]
        # label ("(1)", "(a)", "Note:") in the left column of the first line?
        label, body = None, b["text"]
        m = LABEL_RE.match(b["text"])
        starts = [bd["left"] for bd in bands]
        if m and style in ("body", "heading"):
            f0, s0 = styles[style]
            ts = text_start_after_label(ink, bands[0], 1.6 * stringWidth(m.group(1), f0, s0) / pt + 8)
            if ts is not None:
                label, body, starts[0] = m.group(1), m.group(2), ts
        words = body.split()
        # the OCR's own line breaks, if they match the scanned lines one to one
        ocr_lines = [[winansi(w, warn) for w in seg.split()] for seg in body.split("\n") if seg.strip()]
        # per-line style: headings may share a block with body lines; bold one-line items are sub-headings
        line_styles = []
        for i, bd in enumerate(bands):
            if style == "heading":
                line_styles.append("heading" if i == 0 or bd["ratio"] > BOLD_RATIO else "body")
            elif (style == "body" and len(bands) == 1 and bd["ratio"] > BOLD_RATIO and label
                  and not b["text"].rstrip().endswith(".")):     # sub-headings are not sentences
                line_styles.append("subheading")
            else:
                line_styles.append(style)

        targets = [((bd["right"] - x) * pt) for bd, x in zip(bands, starts)]
        spec = [(*styles[s], t) for s, t in zip(line_styles, targets)]
        words = [winansi(w, warn) for w in words]
        groups = ocr_lines if len(ocr_lines) == len(bands) > 1 else distribute(words, spec)
        if len(words) < len(bands):
            warn(f"{len(bands)} lines on the scan but only {len(words)} words; text missing?")

        bad = []
        for i, (bd, x, grp, (font, size, target)) in enumerate(zip(bands, starts, groups, spec)):
            y = page_h - bd["base"] * pt
            if i == 0 and label:
                put(c, bd["left"] * pt, y, label, font, size)
            if not grp:
                continue
            line = " ".join(grp)
            nat = stringWidth(line, font, size)
            if len(line) <= 3:     # page numbers, single marks: centre on the scanned glyphs
                put(c, (x + bd["right"]) / 2 * pt - nat / 2, y, line, font, size)
                continue
            ratio = nat / max(target, 1.0)
            if not FIT_WARN[0] <= ratio <= FIT_WARN[1]:
                bad.append(f"{i + 1}:{ratio:.2f}")
            ws, hs = 0.0, 100.0
            if nat > target:
                hs = max(MIN_HSCALE, target / nat) * 100
            elif len(grp) > 1:
                ws = (target - nat) / (len(grp) - 1)
                if ws > MAX_WORDSPACE_EM * size:    # don't spread a short line over a long gap
                    ws = 0.0
            put(c, x * pt, y, line, font, size, ws, hs)
        if bad:
            warn(f"text does not fit the scanned lines (line:natural/scanned width {', '.join(bad)}); "
                 f"OCR text may be wrong or incomplete")

    for f in fixes:
        if not f["used"]:
            where = f'starts with "{f["match"]}"' if "match" in f else f"contains point {f.get('at')}"
            warnings.append(f"correction not applied, no block {where}")
    c.showPage()
    c.save()

    wfile = d / "warnings.txt"
    if warnings:
        wfile.write_text("\n".join(warnings) + "\n")
    elif wfile.exists():
        wfile.unlink()
    return out, warnings, pg["angle"]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("manual")
    ap.add_argument("--pages", help="pages to convert, e.g. '5' or '1-3,7' (default: all pages with OCR)")
    ap.add_argument("--cleanup-level", type=int, choices=cleanup_diagrams.LEVELS,
                    help="picture clean-up for all selected pages (see cleanup_diagrams.py): 0 = none, "
                         "1 = despeckle + 1-bit line art. Default: per page from pages.toml [cleanup], else 1")
    args = ap.parse_args()

    styles, pitch_pt, family_fonts = load_manual(args.manual)
    with_ocr = [n for n in existing_pages(args.manual) if (page_dir(args.manual, n) / "ocr/page_res.json").is_file()]
    if not with_ocr:
        sys.exit("error: no pages with OCR results; run ocr_scanned_page.py first")
    types = page_types(args.manual)
    for n in parse_pages(args.pages, with_ocr):
        d = page_dir(args.manual, n)
        kind = types.get(n)
        reason = None
        if kind in ("drawing", "scan_only"):
            reason = f"marked {kind} in pages.toml"
        elif kind != "text":
            share = text_ink_share(d / "page.pdf", json.loads((d / "ocr/page_res.json").read_text()))
            if share < DRAWING_TEXT_INK:
                reason = (f"looks like a schematic/drawing ({share:.0%} of its ink in text blocks); add it to "
                          f"pages.toml as drawing = [...], or as text = [{n}] to convert it anyway")
        if reason:
            for stale in ("result.pdf", "warnings.txt"):    # the merge step uses the scan instead
                (d / stale).unlink(missing_ok=True)
            print(f"p-{n:03d}: skipped, {reason}")
            continue
        level = args.cleanup_level if args.cleanup_level is not None else cleanup_level(args.manual, n)
        out, warnings, angle = convert(args.manual, n, styles, pitch_pt, level, family_fonts,
                                       tables_as_image(args.manual, n))
        print(f"p-{n:03d}: skew {angle:+.2f} deg -> {out.relative_to(manual_out(args.manual))}"
              + (f", {len(warnings)} warnings (see warnings.txt)" if warnings else ""))
        for w in warnings:
            print(f"   - {w}")


if __name__ == "__main__":
    main()
