# scan-to-native-pdf

Rebuild scanned manuals as native PDFs with real, selectable text, using PaddleOCR-VL.

## Why

I service vintage electronics as a hobby and use AI agents to discuss service procedures and
troubleshooting. Scanned service manuals are hard to use for that: a scan is just pictures of
pages, so neither people nor agents can search it or quote it reliably.

This tool converts a scanned manual into a native PDF. The text is recognised (OCR) and set as
real text, in a font matching the original and at the positions measured on the scan. Pictures,
drawings and schematics are kept from the scan, cleaned up without losing detail. Parts lists and
adjustment tables become real text too, where the OCR is reliable enough.

## Requirements

- macOS on Apple Silicon (tested on an M4); OCR runs locally with
  [PaddleOCR-VL](https://huggingface.co/PaddlePaddle/PaddleOCR-VL) on an MLX-VLM server
- [uv](https://docs.astral.sh/uv/) (Python 3.12 is pinned) and Homebrew Ghostscript for the fonts
  (`brew install ghostscript`)

```
uv sync
```

## Usage

Each manual has its own directory. Put the scanned PDF in `manuals/<manual>/input/`, for example
`manuals/nakamichi-bx-300/input/service-manual.pdf`, then run the steps with that manual name:

| step | command | result |
|---|---|---|
| 1 split | `uv run python scripts/split_scan_to_pages.py <manual>` | `manuals/<manual>/output/pages/p-NNN/page.pdf`, one per page |
| 2 OCR | `scripts/vlm-server.sh start`<br>`uv run python scripts/ocr_scanned_page.py <manual> --pages 4-7` | `…/pages/p-NNN/ocr/` (text, layout, figures) |
| 3 fonts | `uv run python scripts/init_fonts.py <manual> --pages 4-7` | `manuals/<manual>/manual.toml` (font, sizes, line spacing) |
| 4 convert | `uv run python scripts/convert_page.py <manual> --pages 4-7` | `…/pages/p-NNN/result.pdf` and `warnings.txt` |
| 5 merge | `uv run python scripts/merge_pages.py <manual>` | `manuals/<manual>/output/<manual>.pdf` |

- Start with 2–3 representative pages for steps 2–4, check the result, then do the rest.
- `scripts/survey_pages.py <manual>` classifies OCR'd pages (text, tables, drawings) into
  `output/survey.md`. Schematics and PCB layouts stay as scanned pages.
- Fixes and decisions are small files next to the manual: `pages.toml` (page types, picture
  clean-up, tables) and `corrections/p-NNN.toml` (OCR text fixes, checked against the scan).
- The merged PDF uses the scanned page wherever a page was not converted, so it is always complete.

## Try the example

`examples/nakamichi-bx-300/` holds the settings and OCR corrections for the Nakamichi BX-300
service manual from HiFi Engine — everything except the scan itself:

```
cp -r examples/nakamichi-bx-300 manuals/
# download the manual into manuals/nakamichi-bx-300/input/ (see examples/README.md)
uv run python scripts/split_scan_to_pages.py nakamichi-bx-300
scripts/vlm-server.sh start
uv run python scripts/ocr_scanned_page.py nakamichi-bx-300
uv run python scripts/convert_page.py nakamichi-bx-300
uv run python scripts/merge_pages.py nakamichi-bx-300
```

The result is `manuals/nakamichi-bx-300/output/nakamichi-bx-300.pdf`. `manuals/` is your working
area and is not tracked by git.

## Using it with an AI agent

The whole workflow — OCR, reviewing each page against the scan, writing corrections — is meant to
be run together with a coding agent. [AGENTS.md](AGENTS.md) describes the pipeline, the file layout
and the rules for agents; `.claude/skills/` holds step-by-step procedures (`ocr-pages`,
`rebuild-page`) that Claude Code loads as skills and other agents can read as plain instructions.

## License

MIT — see [LICENSE](LICENSE). Scanned manuals are not part of this repository.
