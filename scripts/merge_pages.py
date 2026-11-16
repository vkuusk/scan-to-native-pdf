#!/usr/bin/env python
"""Step 5: merge the pages of a manual into one PDF.

    manuals/<manual>/output/pages/p-NNN/result.pdf   (converted page)
    manuals/<manual>/output/pages/p-NNN/page.pdf     (scanned page: fallback)
    ->  manuals/<manual>/output/<manual>.pdf

The project cover page (make_cover_page.py) comes first unless --no-cover; it is built at the
manual's most common page size. Then every page of the scan appears once, in order. A page uses its converted result.pdf unless
  - it is marked drawing or scan_only in manuals/<manual>/pages.toml, or
  - it has no result.pdf (not converted yet, or skipped as a drawing),
in which case the scanned page is used. Pages are copied unchanged (no re-encoding).

The report lists which pages are scans, which converted pages still have warnings, and
converted pages that are stale (OCR or corrections changed after result.pdf was written:
re-run convert_page.py for them).

Usage:
    python scripts/merge_pages.py <manual> [-o OUT.pdf] [--converted-only] [--no-cover]
"""
import argparse
import sys
from pathlib import Path

import pypdfium2 as pdfium

from common import (corrections_file, existing_pages, manual_out, manual_toml, page_dir, page_types,
                    page_types_file, scan_pdf)
from make_cover_page import build_cover, manual_page_size


def ranges(nums):
    """[1,2,3,7,9,10] -> '1-3,7,9-10'"""
    out, start, prev = [], None, None
    for n in nums:
        if start is None:
            start = prev = n
        elif n == prev + 1:
            prev = n
        else:
            out.append(f"{start}-{prev}" if prev > start else str(start))
            start = prev = n
    if start is not None:
        out.append(f"{start}-{prev}" if prev > start else str(start))
    return ",".join(out) or "-"


def is_stale(manual, n):
    d = page_dir(manual, n)
    result = d / "result.pdf"
    inputs = [d / "page.pdf", d / "ocr" / "page_res.json", corrections_file(manual, n),
              manual_toml(manual)]
    t = result.stat().st_mtime
    return any(p.is_file() and p.stat().st_mtime > t for p in inputs)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("manual")
    ap.add_argument("-o", "--out", help="output file (default: manuals/<manual>/output/<manual>.pdf)")
    ap.add_argument("--converted-only", action="store_true",
                    help="leave out pages without a converted result (for reviewing the conversions)")
    ap.add_argument("--no-cover", action="store_true",
                    help="don't prepend the project cover page (make_cover_page.py)")
    args = ap.parse_args()

    total = len(pdfium.PdfDocument(scan_pdf(args.manual)))
    split = existing_pages(args.manual)
    missing = [n for n in range(1, total + 1) if n not in split]
    if missing:
        sys.exit(f"error: pages {ranges(missing)} are not split; run split_scan_to_pages.py first")

    types = page_types(args.manual)
    out_path = Path(args.out) if args.out else manual_out(args.manual) / f"{args.manual}.pdf"

    merged = pdfium.PdfDocument.new()
    cover = None
    if not args.no_cover:     # project cover page, at the manual's most common page size
        w, h = manual_page_size(args.manual)
        cover_path = build_cover(manual_out(args.manual) / "cover-page.pdf", w, h)
        cover = pdfium.PdfDocument(cover_path)
        merged.import_pages(cover, [0])
    used = {"converted": [], "drawing": [], "scan_only": [], "not converted": []}
    with_warnings, stale = [], []
    for n in range(1, total + 1):
        d = page_dir(args.manual, n)
        kind = types.get(n)
        if kind in ("drawing", "scan_only"):
            src, key = d / "page.pdf", kind
        elif (d / "result.pdf").is_file():
            src, key = d / "result.pdf", "converted"
            if (d / "warnings.txt").is_file():
                count = sum(1 for ln in (d / "warnings.txt").read_text().splitlines()
                            if ln.strip() and not ln.startswith("auto-fix:"))
                if count:
                    with_warnings.append(f"{n} ({count})")
            if is_stale(args.manual, n):
                stale.append(n)
        else:
            src, key = d / "page.pdf", "not converted"
        used[key].append(n)
        if args.converted_only and key != "converted":
            continue
        doc = pdfium.PdfDocument(src)
        merged.import_pages(doc, [0])
        doc.close()

    if len(merged) == 0:
        sys.exit("error: nothing to merge (no converted pages)")
    tmp = out_path.with_suffix(".tmp")
    merged.save(tmp)
    merged.close()
    tmp.replace(out_path)

    print(f"{out_path}: {sum(len(v) for k, v in used.items() if not args.converted_only or k == 'converted')} pages"
          + (" + cover page" if cover else ""))
    print(f"  converted:      {len(used['converted']):3d}  pages {ranges(used['converted'])}")
    print(f"  drawing (scan): {len(used['drawing']):3d}  pages {ranges(used['drawing'])}")
    print(f"  scan_only:      {len(used['scan_only']):3d}  pages {ranges(used['scan_only'])}")
    print(f"  not converted:  {len(used['not converted']):3d}  pages {ranges(used['not converted'])}"
          + ("  (left out)" if args.converted_only else "  (scan used)"))
    if with_warnings:
        print(f"  converted with open warnings (page (count)): {', '.join(with_warnings)}")
    if stale:
        print(f"  STALE — inputs changed after conversion, re-run convert_page.py --pages {ranges(stale)}")
    if not page_types_file(args.manual).is_file():
        print(f"  note: no {page_types_file(args.manual).name}; drawing pages are only known once OCR'd")


if __name__ == "__main__":
    main()
