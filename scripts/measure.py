"""Measuring text lines on a scanned page (shared by init_fonts.py and convert_page.py).

A page is rendered at DPI, deskewed, and every OCR layout box is mapped onto the straightened
image; text boxes are split into measured lines ("bands") with baseline, left/right ink edges
and stroke thickness.
"""
import math
import re
import statistics as st

import numpy as np
import pypdfium2 as pdfium
from PIL import Image

DPI = 288                  # measurement resolution (scans are ~300 dpi)
INK = 150                  # grey level below which a pixel counts as ink
BOLD_RATIO = 1.08          # stroke thickness vs. nearby body lines above which a line is bold
LOCAL_PX = 300             # "nearby": baselines within this many px (~1 inch at 288 dpi)
LABEL_RE = re.compile(r"^(\(\w{1,3}\)|Note:)\s+(.*)$", re.S)  # "(1) text", "(a) text", "Note: text"
HEADING_RE = re.compile(r"^\d+(\.\d+)+\.?\s+\S")              # "3.1. Record Head ..."
TEXT_LABELS = {"text", "paragraph_title", "doc_title", "figure_title", "number", "header", "footer",
               "abstract", "aside_text", "footnote", "reference", "reference_content", "vision_footnote",
               "figure_caption", "table_title", "chart_title", "content"}   # content = table of contents


LATEX = {r"\mu": "µ", r"\textcircled{R}": "®", r"\textcircled{C}": "©", r"\circ": "°", r"^{\circ}": "°",
         r"^\circ": "°", r"\pm": "±", r"\times": "×", r"\div": "÷", r"\cdot": "·", r"\Omega": "Ω",
         r"\%": "%", r"\sim": "~", r"\approx": "≈", r"\leq": "≤", r"\geq": "≥", r"\le": "≤", r"\ge": "≥",
         r"\to": "→", r"\rightarrow": "→", r"\infty": "∞", r"\alpha": "α", r"\beta": "β", r"\Delta": "Δ",
         r"\mathrm": "", r"\text": "", "{": "", "}": "", "~": " "}


def clean_latex(s: str) -> str:
    """The OCR model writes some symbols as inline LaTeX ('70 $ \\mu $s'); turn them into characters."""
    def repl(m):
        t = m.group(1).strip()
        for k in sorted(LATEX, key=len, reverse=True):
            t = t.replace(k, LATEX[k])
        t = re.sub(r"\\[a-zA-Z]+", "", t)      # drop unknown commands
        return t.replace("^", "").replace("_", "").strip()
    s = re.sub(r"\$([^$]*)\$", repl, s)          # spaces around $...$ are kept as written
    # the same commands also appear without $...$ (e.g. in table cells: "0.1 \mu 50V")
    s = re.sub(r"\\(" + "|".join(re.escape(k[1:]) for k in sorted(LATEX, key=len, reverse=True)
                                  if k.startswith("\\") and k[1:].isalpha()) + r")(?![a-zA-Z])",
               lambda m: LATEX["\\" + m.group(1)], s)
    for k in sorted(LATEX, key=len, reverse=True):  # bare non-alphabetic commands: \textcircled{R}, \%
        if k.startswith("\\") and not k[1:].isalpha():
            s = s.replace(k, LATEX[k])
    s = re.sub(r"µ (?=[a-zA-Z])", "µ", s)            # "70 µ s" -> "70 µs" (µ belongs to its unit)
    return re.sub(r"[ \t]{2,}", " ", s)


UNICODE_FIX = {"Ⓡ": "®", "Ⓒ": "©", "−": "-", "‐": "-", "‑": "-", "′": "'", "″": '"', "µ": "µ", "Ω": "Ω"}


def typographic(s: str) -> str:
    s = clean_latex(s)
    for k, v in UNICODE_FIX.items():
        s = s.replace(k, v)
    s = re.sub(r'(^|[\s(\[])"', lambda m: m.group(1) + "“", s)
    return s.replace('"', "”").replace("'", "’")


def deskew(img: Image.Image):
    """Skew angle from horizontal-projection sharpness; returns (angle, straightened image)."""
    small = img.reduce(4)

    def sharp(a):
        r = np.asarray(small.rotate(a, resample=Image.BILINEAR, fillcolor=255), float)
        return (255 - r).sum(1).var()
    best = max(np.arange(-3, 3.01, 0.25), key=sharp)
    best = max(np.arange(best - 0.25, best + 0.26, 0.05), key=sharp)
    return float(best), img.rotate(best, resample=Image.BICUBIC, fillcolor=255)


def rotate_box(bbox, angle, size):
    """Move a box found on the skewed scan to where its content lies after deskew(angle)."""
    cx, cy = size[0] / 2, size[1] / 2
    x0, y0, x1, y1 = bbox
    dx, dy = (x0 + x1) / 2 - cx, (y0 + y1) / 2 - cy
    a = math.radians(angle)
    sx = cx + dx * math.cos(a) + dy * math.sin(a) - (x0 + x1) / 2
    sy = cy - dx * math.sin(a) + dy * math.cos(a) - (y0 + y1) / 2
    return [round(x0 + sx), round(y0 + sy), round(x1 + sx), round(y1 + sy)]


def stroke_width(reg) -> float:
    """Mean horizontal ink run length: thicker for bold type."""
    runs = int((np.diff(reg.astype(np.int8), axis=1) == 1).sum() + reg[:, 0].sum())
    return float(reg.sum()) / max(runs, 1)


def line_bands(ink, bbox, grow=True):
    """Text lines inside a box of the deskewed page, top to bottom.
    Slivers of neighbouring lines poking into the box are dropped."""
    h, w = ink.shape
    x0, y0, x1, y1 = bbox
    xa, xb, ya, yb = max(0, x0 - 8), min(w, x1 + 8), max(0, y0 - 4), min(h, y1 + 4)
    reg = ink[ya:yb, xa:xb]
    prof = reg.sum(1).astype(float)
    if prof.size == 0 or prof.max() == 0:
        return []
    thr = max(1.0, 0.04 * prof.max())
    raw, s = [], None
    for y in range(len(prof) + 1):
        on = y < len(prof) and prof[y] > thr
        if on and s is None:
            s = y
        elif not on and s is not None:
            raw.append((s, y))
            s = None
    if not raw:
        return []
    tallest = max(e - s for s, e in raw)
    out = []
    for s, e in raw:
        if e - s < 0.55 * tallest:
            continue
        seg = prof[s:e]
        base = s + max(i for i, v in enumerate(seg) if v > 0.45 * seg.max())
        l, r = ink_extent(reg[s:e])
        if l is None:
            continue
        l, r = grow_line(ink[ya + s:ya + e], xa + l, xa + r) if grow else (xa + l, xa + r)
        out.append(dict(top=ya + s, bottom=ya + e, base=ya + base, left=l, right=r,
                        stroke=stroke_width(ink[ya + s:ya + e, l:r])))
    return out


def grow_line(strip, l, r):
    """OCR boxes are sometimes narrower than the printed line: follow the ink beyond the box edges
    while it continues (gaps up to ~ a word space)."""
    h = strip.shape[0]
    has = strip.sum(0) > 0
    gap = max(3, int(0.45 * h))
    x = r
    while x < len(has):
        nxt = np.where(has[x:x + gap])[0]
        if len(nxt) == 0:
            break
        x += int(nxt[-1]) + 1
    r = x
    x = l
    while x > 0:
        prv = np.where(has[max(0, x - gap):x])[0]
        if len(prv) == 0:
            break
        x = max(0, x - gap) + int(prv[0])
    return x, r


def ink_extent(line):
    """Left/right ink edges of one text line, ignoring isolated specks at either end."""
    h = line.shape[0]
    colsum = line.sum(0)
    cols = np.where(colsum > 0)[0]
    if len(cols) == 0:
        return None, None
    # split into runs separated by gaps wider than ~ half the line height
    segs, start = [], cols[0]
    for a, b in zip(cols, cols[1:]):
        if b - a > 0.5 * h:
            segs.append((start, a + 1))
            start = b
    segs.append((start, cols[-1] + 1))

    def speck(sg):
        return sg[1] - sg[0] < 0.3 * h and colsum[sg[0]:sg[1]].sum() < 0.05 * h * h
    while len(segs) > 1 and speck(segs[0]):
        segs.pop(0)
    while len(segs) > 1 and speck(segs[-1]):
        segs.pop()
    return int(segs[0][0]), int(segs[-1][1])


def text_start_after_label(ink, band, max_label_px):
    """x where the text starts after a label like '(1)': first ink after a wide gap that begins
    within max_label_px of the line's left edge. None if there is no such gap."""
    reg = ink[band["top"]:band["bottom"], band["left"]:band["right"]]
    cols = np.where(reg.sum(0) > 0)[0]
    min_gap = 0.6 * (band["bottom"] - band["top"])
    for a, b in zip(cols, cols[1:]):
        if a > max_label_px:
            return None
        if b - a > min_gap:
            return band["left"] + int(b)
    return None


DRAWING_TEXT_INK = 0.20    # below this share of the page's ink inside text blocks -> drawing


def text_ink_share(page_pdf, res) -> float:
    """Share of the page's ink that lies inside OCR text or table blocks. Text pages: ~0.6-0.7;
    schematics and PCB layouts: < 0.05 (the layout model either calls the whole drawing an image or
    finds no blocks at all — both leave little ink inside text blocks)."""
    img = pdfium.PdfDocument(page_pdf)[0].render(scale=1).to_pil().convert("L")
    ink = np.asarray(img) < INK
    total = ink.sum()
    if total == 0:
        return 0.0
    k = img.width / res["width"]
    mask = np.zeros_like(ink)
    for b in res["parsing_res_list"]:
        if b["block_label"] in TEXT_LABELS or b["block_label"] == "table":   # tables are text too
            x0, y0, x1, y1 = (int(v * k) for v in b["block_bbox"])
            mask[max(0, y0):y1, max(0, x0):x1] = True
    return float((ink & mask).sum() / total)


def source_tone(page) -> str:
    """'bw', 'grey' or 'colour': how the page was scanned, from its embedded image(s).
    1-bit scans hold only black and white, so storing their pictures as 1-bit loses nothing;
    grey and colour scans must keep their tones."""
    import pypdfium2.raw as pdfium_c
    kinds = set()
    grey_spaces = (pdfium_c.FPDF_COLORSPACE_DEVICEGRAY, pdfium_c.FPDF_COLORSPACE_CALGRAY)
    for obj in page.get_objects(filter=[pdfium_c.FPDF_PAGEOBJ_IMAGE]):
        meta = obj.get_metadata()
        if meta.bits_per_pixel == 1:
            kinds.add("bw")
        elif meta.colorspace in grey_spaces:
            kinds.add("grey")
        else:                     # RGB, CMYK, Lab, ICC, indexed palettes ...: keep colour when unsure
            kinds.add("colour")
    for kind in ("colour", "grey", "bw"):
        if kind in kinds:
            return kind
    return "grey"    # no embedded image (vector page): don't threshold anything


def overlap(bd, box):
    return max(0, min(bd["bottom"], box[3]) - max(bd["top"], box[1]))


def dedupe_bands(blocks, need=None):
    """OCR boxes of neighbouring blocks often overlap, so one scanned line can be measured in two
    blocks. Keep each line in only one block: the one whose text needs more lines than it has
    (need(block) -> estimated line count), else the one whose box covers it best."""
    def surplus(blk):
        n = need(blk) if need else None
        return 0 if n is None else len(blk["bands"]) - n
    for i, a in enumerate(blocks):
        for b in blocks[i + 1:]:
            for bd in list(a["bands"]):
                for od in list(b["bands"]):
                    v_overlap = min(bd["bottom"], od["bottom"]) - max(bd["top"], od["top"])
                    same = (v_overlap > 0.5 * min(bd["bottom"] - bd["top"], od["bottom"] - od["top"]) and
                            min(bd["right"], od["right"]) - max(bd["left"], od["left"]) >
                            0.5 * min(bd["right"] - bd["left"], od["right"] - od["left"]))
                    if not same:
                        continue
                    sa, sb = surplus(a), surplus(b)
                    keep_a = sa < sb if sa != sb else overlap(bd, a["box"]) >= overlap(od, b["box"])
                    if keep_a:
                        b["bands"].remove(od)
                    else:
                        a["bands"].remove(bd)
                        break


def orphan_lines(ink, blocks, pt):
    """Text-like ink lines that no OCR block covers (they would be missing from the output)."""
    mask = ink.copy()
    for b in blocks:
        boxes = [b["box"]] if not b["bands"] else [(bd["left"], bd["top"], bd["right"], bd["bottom"]) for bd in b["bands"]]
        for x0, y0, x1, y1 in boxes:
            mask[max(0, int(y0) - 6):int(y1) + 6, max(0, int(x0) - 6):int(x1) + 6] = 0
    found = []
    for bd in line_bands(mask, (0, 0, mask.shape[1], mask.shape[0])):
        h = bd["bottom"] - bd["top"]
        if bd["right"] - bd["left"] > 6 * h and h > 8:
            found.append(f"y={bd['base'] * pt:.0f}pt x={bd['left'] * pt:.0f}-{bd['right'] * pt:.0f}pt")
    return found


def analyse_page(page_pdf, res, extend=None, pitch_pt=0.0, prepare=None, need=None):
    """Render, deskew and measure one page. Returns a dict with the straightened image, ink mask,
    pt-per-px, page size in pt, the OCR blocks with their measured line bands, and 'orphans'
    (text lines outside every block). Every band gets 'ratio' = stroke thickness relative to nearby
    body lines (bold detection). extend = {block index: (lines above, lines below)} grows boxes
    that the OCR drew too small; prepare(blocks) may fix block texts before lines shared by two
    blocks are assigned using need(block) -> lines the text needs."""
    page = pdfium.PdfDocument(page_pdf)[0]
    source = source_tone(page)
    rendered = page.render(scale=DPI / 72).to_pil()
    img = rendered.convert("L")
    k = img.width / res["width"]
    angle, straight = deskew(img)
    ink = (np.asarray(straight) < INK).astype(np.uint8)
    # pictures are cropped from this one: colour kept for colour scans
    picture_img = (rendered.convert("RGB").rotate(angle, resample=Image.BICUBIC, fillcolor=(255, 255, 255))
                   if source == "colour" else straight)
    pitch_px = pitch_pt * DPI / 72
    blocks = []
    for i, b in enumerate(res["parsing_res_list"]):
        label, raw = b["block_label"], b["block_content"].strip()
        box = rotate_box([v * k for v in b["block_bbox"]], angle, img.size)
        if extend and i in extend:
            above, below = extend[i]
            box = [box[0], box[1] - round(above * pitch_px), box[2], box[3] + round(below * pitch_px)]
        bands = line_bands(ink, box) if label in TEXT_LABELS else []
        blocks.append(dict(label=label, raw=raw, text=typographic(raw), box=box, bands=bands))
    if prepare:
        prepare(blocks)
    dedupe_bands(blocks, need)

    body = [bd for b in blocks if b["label"] == "text" and not HEADING_RE.match(b["raw"]) for bd in b["bands"]]
    page_stroke = st.median(bd["stroke"] for bd in body) if body else 1.0
    for b in blocks:
        for bd in b["bands"]:
            near = [o["stroke"] for o in body if o is not bd and abs(o["base"] - bd["base"]) < LOCAL_PX
                    and o["left"] < bd["right"] and bd["left"] < o["right"]]
            bd["ratio"] = bd["stroke"] / (st.median(near) if len(near) >= 3 else page_stroke)
    pt = 72 / DPI
    return dict(img=straight, picture_img=picture_img, source=source, ink=ink, angle=angle, pt=pt, blocks=blocks,
                orphans=orphan_lines(ink, blocks, pt),
                width_pt=straight.width * pt, height_pt=straight.height * pt)
