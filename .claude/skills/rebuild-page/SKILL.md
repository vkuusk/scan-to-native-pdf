---
name: rebuild-page
description: Convert OCR'd pages of a scanned manual into native PDFs (step 4) and review each result against the scan, writing OCR corrections until the page matches. Use when the user asks to convert, rebuild, regenerate, review or fix a page, or reports something wrong on a converted page (missing text, overprinting, wrong words, merged sections).
---

# Rebuild and review a page

Goal: `manuals/<manual>/output/pages/p-NNN/result.pdf` that matches the scan — same text (as printed),
same lines, positions, headings and pictures. Fixes go into `manuals/<manual>/corrections/p-NNN.toml`
(format and rules in AGENTS.md); never edit `output/` by hand.

## Preconditions

- The page has OCR (`p-NNN/ocr/page_res.json`); if not, use the `ocr-pages` skill first.
- `manuals/<manual>/manual.toml` exists; if not, the manual hasn't been started — follow "Starting a
  new manual" in AGENTS.md (2–3 representative pages chosen by the user, then
  `init_fonts.py --pages <those>`).
- The page is a text page. `convert_page.py` skips pages typed `drawing`/`scan_only` in
  `manuals/<manual>/pages.toml` and unlisted pages that look like schematics/PCB layouts. If
  it says "skipped, looks like a schematic/drawing", don't work around it: show the user the page
  and ask whether to add it to `drawing` (the usual case — kept as the scanned page; schematics
  need a different approach, not designed yet) or, if it really is a text page, to `text`.

## Loop, per page

1. **Convert**
   ```
   uv run python scripts/convert_page.py <manual> --pages N
   ```
   It prints warnings and writes them to `p-NNN/warnings.txt`.

2. **Look at the result and the scan** (scratchpad paths for the images):
   ```
   pdftoppm -r 100 -png manuals/<manual>/output/pages/p-NNN/result.pdf <scratch>/rNNN
   pdftoppm -r 100 -png manuals/<manual>/output/pages/p-NNN/page.pdf   <scratch>/oNNN
   ```
   Read both images. Compare section by section: headings present and bold where the scan is
   bold, every line of text present, no overprinting, line breaks and indents like the scan,
   pictures and captions in place.

3. **Investigate each warning and each visible difference.** Zoom into the scan for the exact
   printed text (crop a region rendered at ~200 dpi with PIL and read it). To see how the OCR
   split the page and which scanned lines each block got, print the blocks:
   ```
   uv run python - <<'EOF'
   import json, sys; sys.path.insert(0, "scripts")
   from common import page_dir
   from measure import analyse_page
   d = page_dir("<manual>", N)
   pg = analyse_page(d / "page.pdf", json.loads((d / "ocr/page_res.json").read_text()))
   for i, b in enumerate(pg["blocks"]):
       print(i, b["label"], [round(v * pg["pt"]) for v in b["box"]], len(b["bands"]), repr(b["raw"][:70]))
   EOF
   ```
   (This shows boxes before the automatic repairs and line sharing done by convert_page.)

   Typical causes and fixes:
   | symptom / warning | usual cause | fix |
   |---|---|---|
   | "text does not fit the scanned lines" | OCR text wrong, merged or truncated; printed hyphenation | `text = ...` with the scan's wording; `\n` at printed hyphens (`instal-\nlation`) |
   | "text line not covered by any OCR block" | OCR box too small | `lines_below`/`lines_above` on the block above/below, plus its full `text` |
   | "OCR returned no text for this block" | empty OCR block | correction with the printed `at = [x, y]` and `text` |
   | words differ from the scan, no warning | OCR misread or modernised spelling | `text = ...` exactly as printed |
   | heading or caption wrong weight/size everywhere | `manual.toml` | tell the user; adjust `manual.toml` only with their OK |
   | a text area that is really a drawing/table | layout detection | `image = true` |
   | the whole page is mostly a diagram with a little text | not a text page | propose `drawing` in `pages.toml` |
   | "table: N line(s) not fitting …" (table rendered) | wrong cell text in the OCR HTML | correction on the table block with the full corrected HTML as `text` (match = start of the `<table>…` HTML) |
   | "table: kept as a picture — too many mismatches" | OCR table structure doesn't match the scan (merged tables, missed rows) | usually leave it as a picture; a corrected HTML `text` can make it render |
   | a rendered table looks wrong although no warning | — | add the page to `[tables] keep_as_image` in `pages.toml` and report it |
   | junk block | layout detection | `drop = true` |

4. **Write the corrections** into `manuals/<manual>/corrections/p-NNN.toml` (create the file with a header
   comment if missing). One `[[block]]` per fix with a `# why` comment. `match` is the start of
   the block's OCR text exactly as in `page_res.json` (straight quotes, LaTeX and all) — copy it.
   Write `text` as printed on the scan, never "improved".

5. **Re-convert and re-check** (steps 1–2) until there are no warnings other than `auto-fix:`
   notes and the page matches the scan. Stop after ~3 rounds on one problem and describe it to
   the user instead of piling up workarounds.

6. **If the problem is in the code, not the OCR** (a pattern that will recur on other pages, e.g.
   lines assigned to the wrong block, a layout the converter mishandles), say so and propose a
   fix in `scripts/` rather than covering it with corrections. After any code change, re-convert
   the pages that already have corrections and confirm they still come out clean.

## Report

Per page: what was wrong, which corrections were added (and why), which warnings remain and
why they are acceptable, and anything that needs the user's decision. Show the user the result
render when they asked about a specific problem.
