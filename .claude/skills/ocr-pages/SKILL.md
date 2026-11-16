---
name: ocr-pages
description: Run PaddleOCR-VL on pages of a scanned manual in this project (step 2), splitting the scan into pages first if needed. Use when the user asks to OCR pages, a page range, or a whole manual, e.g. "OCR pages 8-12 of nakamichi-bx-300" or "run OCR on the new manual".
---

# OCR pages of a manual

Produces `manuals/<manual>/output/pages/p-NNN/ocr/` (`page_res.json`, `page.md`, `imgs/`) for the requested
pages. See AGENTS.md for the pipeline and layout.

## Steps

1. **Identify the manual and pages.** The manual is a directory under `manuals/`
   (`ls manuals/`). If the user didn't name one and there is more than one, ask. Pages: what the
   user said, or all pages.

   **New manual?** If `manuals/<manual>/manual.toml` does not exist, the manual has not been
   started: do not OCR all pages, even if asked to "OCR the manual". Follow "Starting a new
   manual" in AGENTS.md — split, show thumbnails, and **ask the user to pick 2–3 representative
   text pages** (suggest candidates covering the layouts and type styles); OCR only those, then
   continue with `init_fonts.py --pages <those>`. Explain why: calibration and converter are
   checked on this manual's layouts before spending time on every page.

   Pages listed as `drawing` or `scan_only` in `manuals/<manual>/pages.toml` are skipped by
   the script; don't try to force them.

2. **Make sure the pages are split.** If `manuals/<manual>/output/pages/` is missing or lacks requested
   pages, run:
   ```
   uv run python scripts/split_scan_to_pages.py <manual>
   ```

3. **Check the OCR server** (MLX-VLM on port 8111):
   ```
   scripts/vlm-server.sh status
   ```
   - running / port in use by another server → use it as is.
   - not running → start it with the wrapper (background, localhost only, model preloaded; uses
     a few GB of memory) and tell the user you did:
     ```
     scripts/vlm-server.sh start
     ```
     When the OCR run is finished, stop it again (`scripts/vlm-server.sh stop`) — but only a server
     you started; never stop one the user started.

4. **Run OCR.** It loads the model once, skips pages that already have results, and continues
   past failures. About 15 s per page, so run long ranges in the background and report progress:
   ```
   uv run python scripts/ocr_scanned_page.py <manual> --pages <range>
   ```
   Add `--force` only if the user wants existing results redone (e.g. after a model change) —
   say that it replaces earlier OCR output. Corrections in `manuals/<manual>/corrections/` match blocks by text, so
   re-OCR can make some corrections stop matching; mention this when using `--force` on pages
   that have a corrections file.

5. **Report**: pages done, skipped, failed (with the error).

   **Drawings.** After OCR the script flags pages that look like schematics or PCB layouts (little
   of the page's ink inside text blocks) and prints the `pages.toml` line to add. Show the user
   the list with the evidence (text-ink share, page size, a thumbnail if useful) and ask to
   confirm; on confirmation add them under `drawing` in `manuals/<manual>/pages.toml`
   (create it with the header comment from AGENTS.md if missing). These pages stay as scanned
   pages — schematics need a different approach that is not designed yet; don't convert them.

6. **Next step**: for a new manual, run `uv run python scripts/init_fonts.py <manual> --pages <the
   representative pages>` and review `manual.toml` with the user, then the `rebuild-page` skill on
   those pages. For a started manual, suggest the `rebuild-page` skill for the new text pages.
