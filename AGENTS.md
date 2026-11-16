# scan-to-native-pdf

Rebuilds scanned manual pages as native PDFs: real, selectable text set in a matching font at the
positions measured on the scan, with pictures cropped from the (deskewed) scan.

## Pipeline (run with `uv run python scripts/<step>.py <manual> [--pages 5,7-9]`)

| step | script | in → out |
|---|---|---|
| 1 | `split_scan_to_pages.py` | `manuals/<manual>/input/<scan>.pdf` → `manuals/<manual>/output/pages/p-NNN/page.pdf` |
| 2 | `ocr_scanned_page.py` | `page.pdf` → `p-NNN/ocr/page_res.json`, `page.md`, `imgs/` (needs the MLX-VLM server) |
| 3 | `init_fonts.py` | pages with OCR → `manuals/<manual>/manual.toml` (font family, sizes, weights, pitch) |
| 4 | `convert_page.py` | `page.pdf` + OCR + `manual.toml` + `manuals/<manual>/corrections/p-NNN.toml` → `p-NNN/result.pdf`, `warnings.txt` |
| – | `survey_pages.py` | all OCR'd pages → `manuals/<manual>/output/survey.md` + `survey-thumbs.png`: per-page text-ink share, prose and table words, suggested class (text / tables / mixed / drawing) |
| 5 | `merge_pages.py` | `result.pdf` files (scanned page for drawing/scan_only/unconverted pages) → `manuals/<manual>/output/<manual>.pdf`; reports scan pages, open warnings and stale conversions; `--converted-only` for review copies; prepends the project cover page, built at the manual's most common page size into `output/cover-page.pdf` (`--no-cover` to omit) |
| – | `make_cover_page.py` | the cover page (Scan-to-Native-PDF wordmark in URW Gothic, clickable GitHub link; A4 design scaled to the page); used by `merge_pages.py`, or run alone for a preview (`<manual>` for its size, default A4) |

Steps 1–2 skip work already done (`--force` redoes it); steps 3–4 always regenerate.
Step 4 cleans pictures with `--cleanup-level` (default 1: despeckle + true 1-bit line art; 0: none),
implemented in `scripts/cleanup_diagrams.py` (`--preview` shows before/after for one page). Never
use clean-up that redraws or invents detail (AI upscaling): fidelity to the scan comes first.
`<manual>` is the directory name under `manuals/`, e.g. `nakamichi-bx-300`. Pages are 1-based, 3-digit.
Steps 2–4 skip pages typed `drawing`/`scan_only` (see Page types).

## Workflows

Step-by-step procedures for agents, in `.claude/skills/` (plain Markdown; Claude Code loads them as
skills, other agents can read and follow them):

- `ocr-pages/SKILL.md` — OCR pages of a manual (server check, new-manual rules, drawing pages)
- `rebuild-page/SKILL.md` — convert a page and review it against the scan, writing corrections
  until it matches

## Starting a new manual

Never OCR and convert a whole new manual in one go. The font calibration and the converter need
to be checked on this manual's layouts first:

1. Split the scan (step 1) and look at a few page thumbnails
   (`pdftoppm -r 20 -png manuals/<manual>/output/pages/p-NNN/page.pdf ...`).
2. **Ask the user to pick 2–3 representative text pages** — together they should cover the main
   layouts (single/two columns, figures with captions, lists, tables) and the type styles
   (headings, sub-headings, body). Suggest candidates from the thumbnails; the user decides.
3. OCR only those pages (step 2), then `init_fonts.py <manual> --pages <those>` (step 3).
   Review `manual.toml` with the user (font, bold detection, sizes).
4. Convert and review those pages (`rebuild-page` skill) until they match the scan; fix code
   problems found here before going further.
5. Then OCR the rest. Classify pages flagged as drawings (see below) with the user, and convert
   and review the remaining text pages in batches.

A manual is "started" once `manuals/<manual>/manual.toml` exists.

## Page types (`manuals/<manual>/pages.toml`)

```toml
[pages]
drawing = [21, 26]   # schematics, PCB layouts: not run through the text pipeline
scan_only = [1]      # keep the scanned page unchanged (cover, photos, ...)
text = [12]          # convert even though it looks like a drawing
```

- Pages not listed are decided automatically by the share of the page's ink inside OCR text
  blocks: text pages ~60–70 %, schematics/PCB layouts < 5 %; below 20 % a page is treated as a
  drawing — step 2 reports it with the line to add, step 3 leaves it out of the calibration and
  step 4 skips it (and removes a stale `result.pdf`). The share needs the page's OCR, which is
  cheap on drawings (~4 s).
- **Schematics and PCB layouts need a different approach** (e.g. vector tracing of the line art,
  OCR of component labels only) that has not been designed yet. Until then they stay as scanned
  pages in the merged PDF. Do not write corrections for them or try to force them through
  `convert_page.py`.
- Per-page settings in the same file (independent of the page type):
  ```toml
  [cleanup]
  level = 1             # picture clean-up default for the manual (convert_page --cleanup-level overrides)
  level_0 = [18, 19]    # pages whose pictures stay exactly as scanned
  [tables]
  keep_as_image = [9]   # pages whose tables stay pictures instead of being rendered as text
  ```
- Tables count as text for the drawing test, so pages with parts lists are converted: their tables
  are rendered as text by `scripts/table_render.py` (rules as vector lines, columns from vertical
  rules or white gaps, rows from full-width rules or matched by text width). A table whose OCR
  doesn't match the scan well (> 15 % mismatched lines, wrong column count) is kept as a picture
  with a warning — never render a table badly; a wrong parts list is worse than a scanned one.
- Classification is the user's decision: propose additions to `pages.toml`, list the evidence
  (text-ink share, A3 size, what the thumbnail shows), and add them once confirmed. Mixed pages
  (half parts list, half diagram) are borderline — ask.

## Layout and rules

One directory per manual:

```
manuals/<manual>/
  input/<scan>.pdf          the scanned manual (exactly one PDF)
  output/                   everything generated: pages/p-NNN/{page.pdf,ocr/,result.pdf,warnings.txt},
                            survey.md, survey-thumbs.png, <manual>.pdf (merged)
  pages.toml                page types, [cleanup], [tables] — the user's decisions
  manual.toml               typography, written by init_fonts.py, hand-tunable
  corrections/p-NNN.toml    per-page OCR text fixes (hand-made, keep them)
```

- `.gitignore` excludes `input/`, `output/` and `corrections/` at any depth (copyrighted scans,
  generated files, excerpts of the manual's text). `pages.toml` and `manual.toml` are not ignored.
  Never commit what `.gitignore` excludes; git itself is the user's job.
- **Never edit files in `output/` by hand** — everything there is regenerated. Decisions and fixes go
  in `pages.toml`, `manual.toml` and `corrections/`.
- `manual.toml` may be hand-tuned, but re-running `init_fonts.py` overwrites it: note hand edits
  and re-apply them (or compare with git) after re-running step 3.
- Code: `scripts/common.py` (paths), `scripts/fonts.py` (URW font families from Ghostscript),
  `scripts/measure.py` (render, deskew, measure text lines — shared by steps 3 and 4).

## Corrections file (`manuals/<manual>/corrections/p-NNN.toml`)

Blocks are matched by how their OCR text starts (`match`), or by a point inside the block
(`at = [x, y]` in pt, for blocks the OCR returned without text — the warning prints the point).

```toml
[[block]]
match = "(2) Move Record Head"     # start of the block's OCR text (as in ocr/page_res.json)
text = "(2) Move ... heads."       # replacement text; "\n" = forced line break (use for printed
                                   #   hyphenation: "mechan-\nical")
lines_below = 1                    # OCR box missed scanned lines: grow it (also lines_above)
# style = "caption"                # body | heading | subheading | title | caption | page_number
# drop = true                      # leave the block out
# image = true                     # keep the block as a picture from the scan
```

Rules for writing corrections:
- Only write what the **scan** shows — check each fix against a zoomed crop of `page.pdf`.
  Keep the manual's own spelling (e.g. "insure", not "ensure"; the OCR model modernises).
- Put a short `# why` comment on each `[[block]]`.
- A block with a correction is never touched by the automatic OCR repairs.

## Environment notes

- Python 3.12 via uv (`.python-version`); paddlepaddle has no wheels for 3.14.
- OCR backend: a local MLX-VLM server on port 8111. `mlx_vlm.server` (package mlx-vlm) is a generic
  OpenAI-compatible server for vision-language models; step 2 asks it for
  `PaddlePaddle/PaddleOCR-VL-1.6`. Manage it with `scripts/vlm-server.sh start|stop|restart|status|log`
  (runs from the project venv, bound to 127.0.0.1, model preloaded; pid and log in `.run/`).
  `stop` only stops a server the script started.
- Fonts: URW Type 1 fonts shipped with Homebrew Ghostscript (`brew install ghostscript`).
- Network may be unavailable to Claude; use `uv run --offline` if uv tries to resolve packages.
