#!/usr/bin/env python
"""Clean up pictures cropped from scanned pages (used by convert_page.py --cleanup-level).

Only pages scanned in black and white (1-bit source images) are ever thresholded or cleaned:
their pictures contain nothing but black and white, so 1-bit storage loses nothing. Pages scanned
in grey or colour keep every tone and colour at all levels (photos, shaded PCB drawings, colour
pictures), stored as high-quality JPEG.

Levels (black-and-white scans)
  0  no clean-up: the crop is embedded as 8-bit greyscale, exactly as rendered from the scan
  1  lossless clean-up of black-and-white line art:
       - clean threshold to pure black and white (Otsu)
       - remove isolated specks (small ink dots with no other ink nearby; dots close to other
         ink, e.g. the periods in "P.C.B.", are kept)
       - fill pin-holes inside black areas
       - embedded as a 1-bit image (much smaller and crisper than greyscale)
     Pictures that are not line art (photos, halftones) are left as greyscale.
  (higher levels, e.g. vectorisation, are not implemented)

Nothing is redrawn or invented: every remaining black pixel comes from the scan.

Preview the effect on one page without converting it:
    python scripts/cleanup_diagrams.py <manual> --page 5 [--level 1] --preview <dir>
"""
import argparse
import hashlib
import json
import sys
import zlib
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

LEVELS = (0, 1)
SPECK_MM = 0.35          # ink blobs smaller than this (diameter) ...
ISOLATION_MM = 0.8       # ... with no other ink within this distance are removed
HOLE_MM = 0.15           # white holes smaller than this inside black are filled (1-2 px pin-holes only:
                         # larger ones may be letter counters in small figure labels)
LINE_ART_MAX_GREY = 0.15 # line art: at most this share of pixels in mid grey (edges only)
JPEG_QUALITY = 92        # grey/colour pictures


def is_line_art(grey: np.ndarray) -> bool:
    mid = ((grey > 60) & (grey < 200)).mean()
    return mid < LINE_ART_MAX_GREY


def _mm(mm, dpi):
    return mm / 25.4 * dpi


def clean(img: Image.Image, level: int, dpi: float, source: str = "bw") -> Image.Image:
    """Return the cleaned picture. source = how the page was scanned ('bw', 'grey', 'colour',
    see measure.source_tone). Only black-and-white scans are thresholded/cleaned (mode '1');
    grey and colour scans keep their tones (mode 'L' / 'RGB') at every level — photos, shaded PCB
    drawings and colour pictures must not become black and white."""
    if level not in LEVELS:
        raise ValueError(f"cleanup level {level} not supported (supported: {LEVELS})")
    if source != "bw":
        return img.convert("RGB" if source == "colour" else "L")
    grey = np.asarray(img.convert("L"))
    if level == 0 or not is_line_art(grey):
        return img.convert("L")

    _, bw = cv2.threshold(grey, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    ink = (bw == 0).astype(np.uint8)

    # isolated specks
    speck_area = np.pi * (_mm(SPECK_MM, dpi) / 2) ** 2
    n, labels, stats, _ = cv2.connectedComponentsWithStats(ink, connectivity=8)
    small = np.zeros(n, bool)
    small[1:] = stats[1:, cv2.CC_STAT_AREA] < speck_area
    if small.any():
        # A small blob is a speck only if NO other ink (stroke or dot) lies within the isolation
        # radius. Halftone screens (e.g. copper areas on PCB drawings) are fields of small dots
        # with close neighbours, so they are kept; scanner specks sit alone in white space.
        r = max(1, int(round(_mm(ISOLATION_MM, dpi))))
        h, w = ink.shape
        remove = np.zeros(n, bool)
        for i in np.nonzero(small)[0]:
            x, y = stats[i, cv2.CC_STAT_LEFT], stats[i, cv2.CC_STAT_TOP]
            bw_, bh = stats[i, cv2.CC_STAT_WIDTH], stats[i, cv2.CC_STAT_HEIGHT]
            ya, yb, xa, xb = max(0, y - r), min(h, y + bh + r), max(0, x - r), min(w, x + bw_ + r)
            win = labels[ya:yb, xa:xb]
            remove[i] = not ((win > 0) & (win != i)).any()
        ink[remove[labels]] = 0

    # pin-holes in black areas
    hole_area = np.pi * (_mm(HOLE_MM, dpi) / 2) ** 2
    white = (1 - ink).astype(np.uint8)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(white, connectivity=4)
    holes = np.zeros(n, bool)
    holes[1:] = stats[1:, cv2.CC_STAT_AREA] < hole_area
    ink[holes[labels]] = 1

    return Image.fromarray(((1 - ink) * 255).astype(np.uint8)).convert("1", dither=Image.Dither.NONE)


def draw(c, img: Image.Image, x, y, w, h):
    """Draw a picture on a reportlab canvas. Mode '1' images are embedded as true 1-bit images
    (reportlab's drawImage would expand them to 8-bit RGB); others go through drawImage."""
    if img.mode != "1":
        # grey/colour tones: JPEG at high quality (Flate on scanned tones is several times larger)
        import io
        from reportlab.lib.utils import ImageReader
        buf = io.BytesIO()
        extra = {"subsampling": 0} if img.mode == "RGB" else {}   # full colour resolution
        img.save(buf, "JPEG", quality=JPEG_QUALITY, **extra)
        buf.seek(0)
        c.drawImage(ImageReader(buf), x, y, w, h)
        return
    from reportlab.pdfbase import pdfdoc
    c._currentPageHasImages = 1              # as drawImage does: page resources list the images
    raw = img.tobytes()                      # rows packed MSB-first, 1 = white (= DeviceGray 1)
    name = "bw" + hashlib.md5(raw + repr(img.size).encode()).hexdigest()
    reg = c._doc.getXObjectName(name)
    if not c._doc.idToObject.get(reg):
        obj = pdfdoc.PDFImageXObject(name)
        obj.width, obj.height = img.size
        obj.bitsPerComponent = 1
        obj.colorSpace = "DeviceGray"
        obj.streamContent = zlib.compress(raw, 9)
        obj._filters = ("FlateDecode",)
        obj.mask = None
        c._setXObjects(obj)
        c._doc.Reference(obj, reg)
        c._doc.addForm(name, obj)
    c.saveState()
    c.translate(x, y)
    c.scale(w, h)
    c._code.append(f"/{reg} Do")
    c.restoreState()
    c._formsinuse.append(name)               # as drawImage does: list it in the page's XObjects


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("manual")
    ap.add_argument("--page", type=int, required=True)
    ap.add_argument("--level", type=int, default=1, choices=LEVELS)
    ap.add_argument("--preview", type=Path, required=True, help="directory for before/after PNGs")
    args = ap.parse_args()

    from common import page_dir
    from measure import DPI, analyse_page
    d = page_dir(args.manual, args.page)
    if not (d / "ocr/page_res.json").is_file():
        sys.exit("error: page has no OCR results")
    pg = analyse_page(d / "page.pdf", json.loads((d / "ocr/page_res.json").read_text()))
    args.preview.mkdir(parents=True, exist_ok=True)
    for i, b in enumerate(b for b in pg["blocks"] if b["label"] == "image"):
        x0, y0, x1, y1 = (int(v) for v in b["box"])
        crop = pg["picture_img"].crop((x0, y0, x1, y1))
        out = clean(crop, args.level, DPI, pg["source"])
        base = args.preview / f"p{args.page:03d}-img{i + 1}"
        crop.save(f"{base}-before.png")
        out.save(f"{base}-after-level{args.level}.png")
        print(f"{base}: {'line art' if is_line_art(np.asarray(crop)) else 'not line art'} -> mode {out.mode}")


if __name__ == "__main__":
    main()
