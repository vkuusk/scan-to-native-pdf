#!/usr/bin/env python
"""Step 2: OCR split pages with PaddleOCR-VL through a local MLX-VLM server.

    manuals/<manual>/output/pages/p-NNN/page.pdf  ->  .../p-NNN/ocr/
                                               page_res.json, page.md, imgs/

Start the server first:
    scripts/vlm-server.sh start

The model pipeline is loaded once and reused for every page. Pages that already have
OCR results are skipped unless --force. A failing page is reported and the run continues.
Pages marked drawing/scan_only in manuals/<manual>/pages.toml are skipped; after OCR, pages
that look like schematics or PCB layouts are reported with the pages.toml line to add.

Usage:
    python scripts/ocr_scanned_page.py <manual> [--pages 1-5,9] [--force]
"""
import argparse
import json
import shutil
import socket
import sys
import time
from urllib.parse import urlparse

from common import existing_pages, page_dir, page_types, page_types_file, parse_pages
from measure import DRAWING_TEXT_INK, text_ink_share

DEFAULT_SERVER = "http://localhost:8111/"
DEFAULT_MODEL = "PaddlePaddle/PaddleOCR-VL-1.6"


def server_up(url: str) -> bool:
    u = urlparse(url)
    try:
        with socket.create_connection((u.hostname, u.port or 80), timeout=3):
            return True
    except OSError:
        return False


def ocr_done(n_dir) -> bool:
    return (n_dir / "ocr" / "page_res.json").is_file()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("manual", help="name of the manual's directory under manuals/")
    ap.add_argument("--pages", help="pages to OCR, e.g. '5' or '1-3,7' (default: all)")
    ap.add_argument("--force", action="store_true", help="redo pages that already have OCR results")
    ap.add_argument("--server-url", default=DEFAULT_SERVER, help="MLX-VLM server URL (default: %(default)s)")
    ap.add_argument("--model", default=DEFAULT_MODEL, help="model served by MLX-VLM (default: %(default)s)")
    args = ap.parse_args()

    available = existing_pages(args.manual)
    if not available:
        sys.exit(f"error: no split pages for '{args.manual}'; run split_scan_to_pages.py first")
    pages = parse_pages(args.pages, available)
    types = page_types(args.manual)
    excluded = [n for n in pages if types.get(n) in ("drawing", "scan_only")]
    if excluded:
        print(f"skipping pages marked drawing/scan_only in {page_types_file(args.manual).name}: "
              f"{','.join(map(str, excluded))}")
    pages = [n for n in pages if n not in excluded]
    todo = [n for n in pages if args.force or not ocr_done(page_dir(args.manual, n))]
    print(f"{len(pages)} pages selected, {len(pages) - len(todo)} already done, {len(todo)} to OCR")
    if not todo:
        report_drawings(args.manual, pages, types)
        return

    if not server_up(args.server_url):
        sys.exit(f"error: MLX-VLM server not reachable at {args.server_url}; start it with: scripts/vlm-server.sh start")

    from paddleocr import PaddleOCRVL   # slow import; only after the checks above

    pipeline = PaddleOCRVL(
        vl_rec_backend="mlx-vlm-server",
        vl_rec_server_url=args.server_url,
        vl_rec_api_model_name=args.model,
    )

    failed = []
    for i, n in enumerate(todo, 1):
        d = page_dir(args.manual, n)
        tmp = d / "ocr.tmp"
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir()
        t0 = time.monotonic()
        try:
            for res in pipeline.predict(input=str(d / "page.pdf")):
                res.save_to_json(save_path=tmp)
                res.save_to_markdown(save_path=tmp)
            # Paddle names files after the input plus a page index (page_0_res.json, page_0.md);
            # normalise to fixed names for the later steps.
            js, md = sorted(tmp.glob("*_res.json")), sorted(tmp.glob("*.md"))
            if len(js) != 1 or len(md) != 1:
                raise RuntimeError(f"unexpected OCR files: {sorted(p.name for p in tmp.iterdir())}")
            js[0].rename(tmp / "page_res.json")
            md[0].rename(tmp / "page.md")
        except Exception as e:  # keep going with the other pages
            shutil.rmtree(tmp, ignore_errors=True)
            failed.append(n)
            print(f"[{i}/{len(todo)}] p-{n:03d} FAILED: {e}", flush=True)
            continue
        shutil.rmtree(d / "ocr", ignore_errors=True)
        tmp.rename(d / "ocr")
        print(f"[{i}/{len(todo)}] p-{n:03d} done in {time.monotonic() - t0:.0f}s", flush=True)

    report_drawings(args.manual, pages, types)
    if failed:
        sys.exit(f"failed pages: {','.join(map(str, failed))}")


def report_drawings(manual, pages, types):
    """Flag OCR'd pages that look like schematics/PCB layouts (little ink inside text blocks)."""
    suspects = []
    for n in pages:
        d = page_dir(manual, n)
        if types.get(n) or not ocr_done(d):
            continue
        share = text_ink_share(d / "page.pdf", json.loads((d / "ocr/page_res.json").read_text()))
        if share < DRAWING_TEXT_INK:
            suspects.append(n)
            print(f"p-{n:03d}: looks like a schematic/drawing — only {share:.0%} of its ink is in text blocks")
    if suspects:
        print(f"\nThese pages are not suited to text conversion. If confirmed, add them to "
              f"{page_types_file(manual).relative_to(page_types_file(manual).parents[2])}:\n"
              f"  [pages]\n  drawing = [{', '.join(map(str, suspects))}]")


if __name__ == "__main__":
    main()
