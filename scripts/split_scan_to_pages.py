#!/usr/bin/env python
"""Step 1: split a scanned manual into one PDF per page.

    manuals/<manual>/input/<scan>.pdf  ->  manuals/<manual>/output/pages/p-NNN/page.pdf

Pages are copied unchanged (no re-encoding). Existing page files are kept unless --force.

Usage:
    python scripts/split_scan_to_pages.py <manual> [--pages 1-5,9] [--force]
"""
import argparse

import pypdfium2 as pdfium

from common import page_dir, parse_pages, scan_pdf


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("manual", help="name of the manual's directory under manuals/")
    ap.add_argument("--pages", help="pages to extract, e.g. '5' or '1-3,7' (default: all)")
    ap.add_argument("--force", action="store_true", help="overwrite pages that already exist")
    args = ap.parse_args()

    src_path = scan_pdf(args.manual)
    src = pdfium.PdfDocument(src_path)
    total = len(src)
    pages = parse_pages(args.pages, list(range(1, total + 1)))
    print(f"{src_path.name}: {total} pages")

    written = skipped = 0
    for n in pages:
        out = page_dir(args.manual, n) / "page.pdf"
        if out.exists() and not args.force:
            skipped += 1
            continue
        out.parent.mkdir(parents=True, exist_ok=True)
        doc = pdfium.PdfDocument.new()
        doc.import_pages(src, [n - 1])
        tmp = out.with_suffix(".tmp")
        doc.save(tmp)
        doc.close()
        tmp.replace(out)   # never leave a half-written page.pdf behind
        written += 1

    print(f"written {written}, skipped {skipped} (already present) -> {page_dir(args.manual, 1).parent}")


if __name__ == "__main__":
    main()
