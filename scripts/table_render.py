"""Render OCR tables (HTML from PaddleOCR-VL) as native text at the positions measured on the scan.

For one table block on a deskewed page:
  1. ruling lines: long horizontal/vertical ink runs inside the table -> drawn as vector lines
  2. columns: from vertical rules spanning most of the table; tables without rules: the widest
     white gaps. The count must match the HTML column count.
  3. per column, the text lines are measured (rules removed) and the column's non-empty cells are
     matched, in order, to consecutive lines by width; words are set at the measured positions,
     left-aligned as printed (no stretching), in a size estimated for this table.
  4. bold lines (group headings in parts lists) are detected by stroke thickness.
If anything does not add up, render() returns False and the caller keeps the table as a picture.
"""
import html
import re
import statistics as st
from html.parser import HTMLParser

import cv2
import numpy as np
from reportlab.pdfbase.pdfmetrics import stringWidth

from measure import BOLD_RATIO, line_bands, typographic

MIN_RULE_PX = 70           # a ruling line is at least this long (288 dpi: ~6 mm), longer than any glyph
FULL_RULE = 0.5            # vertical rules spanning this share of the table height separate columns
MIN_COL_PX = 12
FIT_WARN = (0.70, 1.15)
TABLE_BOLD = 1.25         # stroke vs. table median above which a table line is bold
MAX_PROBLEMS = 0.15      # more mismatched lines than this share -> keep the table as a picture
FULL_ROW_RULE = 0.6       # horizontal rules spanning this share of the table width separate rows


class _TableParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows, self._row, self._cell, self._span = [], None, None, 1

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self._row = []
        elif tag in ("td", "th"):
            self._cell, self._span = [], int(dict(attrs).get("colspan", 1) or 1)
        elif tag == "br" and self._cell is not None:
            self._cell.append("\n")

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._row.append(("".join(self._cell).strip(), self._span))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            self.rows.append(self._row)
            self._row = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def parse_table(text):
    """HTML table -> (n_columns, rows) where rows[r][c] = cell text ('' if empty). Cells spanning
    several columns are kept in their first column. Rows without any text are dropped."""
    p = _TableParser()
    p.feed(text)
    if not p.rows:
        return 0, []
    ncols = max(sum(span for _, span in row) for row in p.rows)
    rows = []
    for row in p.rows:
        cells, c = [""] * ncols, 0
        for cell, span in row:
            cell = html.unescape(cell).replace("\\n", "\n")
            if c < ncols:
                cells[c] = typographic(cell) if cell.strip() else ""
            c += span
        if any(cells):
            rows.append(cells)
    return ncols, rows


def line_slots(col_bands):
    """Group the measured lines of all columns into horizontal slots (one printed line of the table).
    Returns a list of slots; slot[c] = list of band dicts of column c on that line."""
    items = sorted(((bd["base"], c, bd) for c, bands in enumerate(col_bands) for bd in bands), key=lambda t: t[0])
    if not items:
        return []
    heights = [bd["bottom"] - bd["top"] for _, _, bd in items]
    tol = 0.5 * st.median(heights)
    slots, cur, cur_base = [], None, None
    for base, c, bd in items:
        if cur is None or base - cur_base > tol:
            cur, cur_base = [[] for _ in col_bands], base
            slots.append(cur)
        cur[c].append(bd)
    return slots


def assign_rows(rows, slots, nat_width):
    """Assign each OCR row to >= 1 consecutive line slots (all slots used, in order), so that in
    every column the row's cell text fits the scanned lines of those slots. nat_width(text) -> px.
    Returns [(start, end)] per row, or None if impossible."""
    n, m, ncols = len(rows), len(slots), len(slots[0]) if slots else 0
    if n == 0 or m == 0:
        return None
    # prefix sums per column: ink width and number of lines in slots[0:j]
    wsum = [[0.0] * (m + 1) for _ in range(ncols)]
    lsum = [[0] * (m + 1) for _ in range(ncols)]
    for c in range(ncols):
        for j, s in enumerate(slots):
            wsum[c][j + 1] = wsum[c][j] + sum(b["right"] - b["left"] for b in s[c])
            lsum[c][j + 1] = lsum[c][j] + len(s[c])
    nat = [[nat_width(cell) if cell else 0.0 for cell in row] for row in rows]

    def cost(i, j, k):
        total = 0.0
        for c in range(ncols):
            lines = lsum[c][k] - lsum[c][j]
            if not rows[i][c]:
                total += 1.5 * lines                 # scanned text the OCR row does not explain
                continue
            if lines == 0:
                total += 3.0                         # OCR text with no scanned line here
                continue
            if not slots[j][c]:
                total += 2.0                         # a row's cells start on its first line
            r = nat[i][c] / max(1.0, wsum[c][k] - wsum[c][j])
            total += 4 * (r - 1) ** 2 if r > 1 else max(0.0, (lines - 1) / lines - r) ** 2 * 6
        return total

    inf = float("inf")
    best = [[inf] * (m + 1) for _ in range(n + 1)]
    back = [[0] * (m + 1) for _ in range(n + 1)]
    best[0][0] = 0.0
    max_span = 40                                    # a row covers at most this many printed lines
    for i in range(1, n + 1):
        for k in range(0, m + 1):
            # j == k: the row gets no scanned line (OCR row the scan does not show) - penalised
            for j in range(max(0, k - max_span), k + 1):
                if best[i - 1][j] == inf:
                    continue
                v = best[i - 1][j] + cost(i - 1, j, k)
                if v < best[i][k]:
                    best[i][k], back[i][k] = v, j
    if best[n][m] == inf:
        return None
    out, k = [], m
    for i in range(n, 0, -1):
        j = back[i][k]
        out.append((j, k))
        k = j
    return out[::-1]


def find_rules(reg):
    """Masks of horizontal and vertical ruling lines in a binary region (1 = ink)."""
    k = MIN_RULE_PX
    horiz = cv2.morphologyEx(reg, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (k, 1)))
    vert = cv2.morphologyEx(reg, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, k)))
    return horiz, vert


def rule_boxes(mask):
    n, _, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    return [tuple(int(v) for v in stats[i, :4]) for i in range(1, n)]   # x, y, w, h


def column_ranges(vert_boxes, text_reg, height, ncols):
    """Column x-ranges (region coordinates): between full-height vertical rules, else at white gaps."""
    w = text_reg.shape[1]
    seps = sorted(x + bw / 2 for x, _, bw, bh in vert_boxes if bh >= FULL_RULE * height)
    cuts = [0] + [s for s in seps] + [w]
    ranges = [(a, b) for a, b in zip(cuts, cuts[1:]) if b - a >= MIN_COL_PX and text_reg[:, int(a):int(b)].any()]
    if len(ranges) == ncols:
        return [(int(a), int(b)) for a, b in ranges]
    # no usable rules: the ncols-1 widest interior white gaps of the column projection
    has = text_reg.any(0)
    xs = np.nonzero(has)[0]
    if len(xs) == 0:
        return None
    gaps, start = [], None
    for x in range(xs[0], xs[-1] + 1):
        if not has[x] and start is None:
            start = x
        elif has[x] and start is not None:
            gaps.append((x - start, start, x))
            start = None
    if len(gaps) < ncols - 1:
        return None
    best = sorted(sorted(gaps, reverse=True)[:ncols - 1], key=lambda g: g[1])
    edges = [xs[0]] + [(g[1] + g[2]) // 2 for g in best] + [xs[-1] + 1]
    return [(int(a), int(b)) for a, b in zip(edges, edges[1:])]


def render(c, pg, box, table_html, fonts, body_size, put, distribute, warn):
    """Draw the table on canvas c. Returns True if rendered as text, False to keep it as a picture."""
    ink_all, pt, page_h = pg["ink"], pg["pt"], pg["height_pt"]
    H, W = ink_all.shape
    x0, y0 = max(0, int(box[0]) - 8), max(0, int(box[1]) - 8)
    x1, y1 = min(W, int(box[2]) + 8), min(H, int(box[3]) + 8)
    reg = ink_all[y0:y1, x0:x1].astype(np.uint8)
    ncols, rows = parse_table(table_html)
    if ncols == 0:
        warn("table: no rows in the OCR HTML; kept as a picture")
        return False

    horiz, vert = find_rules(reg)
    rules = cv2.dilate(horiz | vert, np.ones((5, 5), np.uint8))
    text_reg = reg & (1 - rules)
    ranges = column_ranges(rule_boxes(vert), text_reg, reg.shape[0], ncols)
    if not ranges:
        warn(f"table: could not find {ncols} columns on the scan; kept as a picture")
        return False

    # text ink of the table only (rules removed), in page coordinates, for line measurement
    text_ink = np.zeros_like(ink_all)
    text_ink[y0:y1, x0:x1] = text_reg
    col_bands = [line_bands(text_ink, (x0 + a, y0, x0 + b, y1), grow=False) for a, b in ranges]
    slots = line_slots(col_bands)
    reg_font, bold_font = fonts["regular"], fonts["bold"]

    def width_plan(size):
        return assign_rows(rows, slots, lambda t: stringWidth(" ".join(t.split()), reg_font, size) / pt)

    # rows: from full-width horizontal rules when they delimit exactly the OCR rows (ruled grids),
    # else matched by text width (unruled tables such as parts lists)
    spans = rule_rows(rule_boxes(horiz), slots, reg.shape[1], y0, len(rows))
    how = "ruled rows"
    if spans is None:
        how = "rows matched by width"
        spans = width_plan(body_size)
    if spans is None:
        warn(f"table: {len(rows)} OCR rows cannot be matched to {len(slots)} scanned lines; kept as a picture")
        return False
    # font size for this table, from cells set on a single scanned line
    ratios = []
    for row, (j, k) in zip(rows, spans):
        for ci, cell in enumerate(row):
            lines = [bd for s in slots[j:k] for bd in s[ci]]
            words = " ".join(cell.split())
            if len(lines) == 1 and len(words) >= 4:
                ratios.append((lines[0]["right"] - lines[0]["left"]) * pt / stringWidth(words, reg_font, 1.0))
    size = float(np.clip(st.median(ratios), 0.6 * body_size, 1.3 * body_size)) if ratios else body_size
    if how != "ruled rows":
        spans = width_plan(size)

    strokes = [bd["stroke"] for bands in col_bands for bd in bands]
    med_stroke = st.median(strokes) if strokes else 1.0

    # plan every line first, then decide whether the result is good enough
    ops, bad, missing, unexplained, total = [], 0, 0, 0, 0
    for row, (j, k) in zip(rows, spans):
        for ci, cell in enumerate(row):
            cb = [bd for s in slots[j:k] for bd in s[ci]]
            if not cell:
                unexplained += len(cb)
                total += len(cb)
                continue
            if not cb:
                missing += 1
                total += 1
                continue
            fonts_l = [bold_font if bd["stroke"] > TABLE_BOLD * med_stroke else reg_font for bd in cb]
            specs = [(f, size, (bd["right"] - bd["left"]) * pt) for f, bd in zip(fonts_l, cb)]
            segs = [s.split() for s in split_items(cell).split("\n") if s.strip()]
            groups = segs if len(segs) == len(cb) else distribute_segments(segs, specs, distribute)
            for bd, f, grp in zip(cb, fonts_l, groups):
                total += 1
                if not grp:
                    unexplained += 1
                    continue
                line = " ".join(grp)
                target = (bd["right"] - bd["left"]) * pt
                nat = stringWidth(line, f, size)
                r = nat / max(target, 1.0)
                if len(line) > 3 and not FIT_WARN[0] <= r <= FIT_WARN[1]:
                    bad += 1
                hs = max(80.0, 100.0 * target / nat) if nat > target * 1.03 else 100.0
                ops.append((bd["left"] * pt, page_h - bd["base"] * pt, line, f, size, hs))

    problems = bad + missing + unexplained
    detail = (f"{bad} line(s) not fitting their scanned width, {missing} OCR cell(s) without a scanned line, "
              f"{unexplained} scanned line(s) without OCR text, of {total} ({how})")
    if total == 0 or problems / total > MAX_PROBLEMS:
        warn(f"table: kept as a picture — too many mismatches between OCR and scan: {detail}. "
             f"Fix the table's OCR text with a correction, or leave it as a picture")
        return False

    # ruling lines as vector rectangles, then the text
    c.saveState()
    c.setFillColorRGB(0, 0, 0)
    for mask in (horiz, vert):
        for x, y, w, h in rule_boxes(mask):
            c.rect((x0 + x) * pt, page_h - (y0 + y + h) * pt, w * pt, h * pt, stroke=0, fill=1)
    c.restoreState()
    for x, y, line, f, s, hs in ops:
        put(c, x, y, line, f, s, 0.0, hs)
    if problems:
        warn(f"table: {detail} — OCR text may be wrong or incomplete")
    return True


ITEM_RE = re.compile(r"(?<=[.:;)])\s*(?=\d{1,2}\.\s)")


def split_items(cell):
    """Numbered steps inside a cell start on a new line as printed ("... position.2. Adjust" ->
    "... position.\n2. Adjust"); only for cells that are numbered lists ("1. ...")."""
    return ITEM_RE.sub("\n", cell) if re.match(r"\s*1\.\s", cell) else cell


def distribute_segments(segs, specs, distribute):
    """Spread several forced-break segments (word lists) over the cell's scanned lines: each segment
    gets >= 1 consecutive lines, chosen so the segment widths fit, then words are distributed
    within each segment. Falls back to one flowing paragraph if there are more segments than lines."""
    if not segs:
        return [[] for _ in specs]
    words = [w for s in segs for w in s]
    if len(segs) == 1 or len(segs) > len(specs):
        return distribute(words, specs)
    nat = [stringWidth(" ".join(s), specs[0][0], specs[0][1]) for s in segs]
    tw = [t for _, _, t in specs]
    n, m = len(segs), len(specs)
    inf = float("inf")
    best = [[inf] * (m + 1) for _ in range(n + 1)]
    back = [[0] * (m + 1) for _ in range(n + 1)]
    best[0][0] = 0.0
    for i in range(1, n + 1):
        for k in range(i, m - (n - i) + 1):
            for j in range(i - 1, k):
                if best[i - 1][j] == inf:
                    continue
                cap, lines = sum(tw[j:k]) or 1.0, k - j
                r = nat[i - 1] / cap
                c = 4 * (r - 1) ** 2 if r > 1 else max(0.0, (lines - 1) / lines - r) ** 2 * 6
                if best[i - 1][j] + c < best[i][k]:
                    best[i][k], back[i][k] = best[i - 1][j] + c, j
    cuts, k = [], m
    for i in range(n, 0, -1):
        j = back[i][k]
        cuts.append((j, k))
        k = j
    out = []
    for seg, (j, k) in zip(segs, cuts[::-1]):
        out += distribute(seg, specs[j:k]) if k - j > 1 else [seg]
    return out


def rule_rows(hboxes, slots, width, y0, nrows):
    """Row spans from full-width horizontal rules, if they delimit exactly nrows non-empty bands."""
    ys = sorted(y + h / 2 for x, y, w, h in hboxes if w >= FULL_ROW_RULE * width)
    merged = []
    for y in ys:
        if not merged or y - merged[-1] > 6:
            merged.append(y)
    if len(merged) < 2:
        return None
    bases = [min(bd["base"] for col in s for bd in col) - y0 for s in slots]
    spans = []
    for a, b in zip(merged, merged[1:]):
        idx = [i for i, base in enumerate(bases) if a < base < b]
        if idx:
            spans.append((idx[0], idx[-1] + 1))
    # every slot must belong to a row, and the rows must match the OCR rows one to one
    if len(spans) != nrows or sum(k - j for j, k in spans) != len(slots):
        return None
    return spans
