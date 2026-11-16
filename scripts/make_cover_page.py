#!/usr/bin/env python
"""The project's cover page, prepended by merge_pages.py.

A single page in the style of the HiFi Engine notice page: the "Scan-to-Native-PDF" wordmark set in
URW Gothic (Avant Garde clone, from the Ghostscript fonts), a short note, and a clickable link to
the project on GitHub. The layout is designed for A4 and scaled to any page size: merge_pages.py
builds the cover at the manual's most common page size (manuals/<manual>/output/cover-page.pdf).

Preview / standalone use:
    python scripts/make_cover_page.py [<manual>] [--url URL] [-o OUT.pdf]
        with <manual>: the manual's page size, else A4
"""
import argparse
from collections import Counter
from pathlib import Path

import pypdfium2 as pdfium
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

from common import manual_out, scan_pdf
from fonts import load_family

DEFAULT_URL = "https://github.com/vkuusk/scan-to-native-pdf"
BLUE = (0.0, 0.2, 0.75)
GREY = (0.35, 0.35, 0.35)


def manual_page_size(manual):
    """Most common displayed page size (w, h in pt, rotation applied) of the manual's scan."""
    doc = pdfium.PdfDocument(scan_pdf(manual))
    sizes = Counter(tuple(round(v, 1) for v in doc[i].get_size()) for i in range(len(doc)))
    return sizes.most_common(1)[0][0]


def build_cover(path, width, height, url=DEFAULT_URL):
    """Draw the cover page at the given size (layout designed for A4, scaled to fit)."""
    gothic = load_family("avantgarde")
    book, demi = gothic["regular"], gothic["bold"]
    s = min(width / A4[0], height / A4[1])          # scale of the A4 design
    cx = width / 2
    Path(path).parent.mkdir(parents=True, exist_ok=True)

    c = canvas.Canvas(str(path), pagesize=(width, height), initialFontName=book)
    c.setTitle("Scan-to-Native-PDF")
    c.setAuthor("scan-to-native-pdf")
    c.setCreator("scan-to-native-pdf")

    def centred(y, text, font, size, colour=(0, 0, 0)):
        c.setFillColorRGB(*colour)
        c.setFont(font, size * s)
        c.drawCentredString(cx, y, text)
        return stringWidth(text, font, size * s)

    # wordmark near the top, as on the HiFi Engine page: 'Native' and 'PDF' in blue
    parts = [("Scan-to-", (0, 0, 0)), ("Native", BLUE), ("-", (0, 0, 0)), ("PDF", BLUE)]
    size = 34 * s
    mark_w = sum(stringWidth(t, demi, size) for t, _ in parts)
    x, y = cx - mark_w / 2, height - 150 * s
    for text, colour in parts:
        c.setFillColorRGB(*colour)
        c.setFont(demi, size)
        c.drawString(x, y, text)
        x += stringWidth(text, demi, size)
    c.setStrokeColorRGB(*BLUE)
    c.setLineWidth(1.5 * s)
    c.line(cx - mark_w / 2, y - 12 * s, cx + mark_w / 2, y - 12 * s)
    centred(y - 36 * s, "scanned manuals rebuilt with real, selectable text", book, 12, GREY)

    # note and link near the bottom
    y = 150 * s
    centred(y, "This PDF was rebuilt from a scanned manual: the text was recognised (OCR)", book, 10)
    centred(y - 14 * s, "and set as real text; pictures and drawings come from the scan.", book, 10)
    centred(y - 40 * s, "For the tool and how it works, please visit", book, 10)
    lw = centred(y - 56 * s, url.removeprefix("https://"), demi, 11, BLUE)
    c.linkURL(url, (cx - lw / 2, y - 60 * s, cx + lw / 2, y - 45 * s), relative=0)

    c.showPage()
    c.save()
    return path


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("manual", nargs="?", help="use this manual's most common page size (default: A4)")
    ap.add_argument("--url", default=DEFAULT_URL, help="project link (default: %(default)s)")
    ap.add_argument("-o", "--out", type=Path,
                    help="output file (default: manuals/<manual>/output/cover-page.pdf; without <manual>: "
                         "./cover-page.pdf)")
    args = ap.parse_args()
    if args.out is None:
        args.out = manual_out(args.manual) / "cover-page.pdf" if args.manual else Path("cover-page.pdf")
    w, h = manual_page_size(args.manual) if args.manual else A4
    build_cover(args.out, w, h, args.url)
    print(f"wrote {args.out} ({w:.0f} x {h:.0f} pt)")


if __name__ == "__main__":
    main()
