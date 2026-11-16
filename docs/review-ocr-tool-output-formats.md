# Review: output formats of OCR / document-parsing tools

*Researched 2026-11-15 (web search and docs). Purpose: what would change if this project used a
different OCR engine than PaddleOCR-VL. Decision for now: **stay with PaddleOCR-VL**.*

## What this project needs from OCR

`convert_page.py` measures text lines on the scan itself, so the OCR only has to provide, per page,
a list of **blocks**, each with:

- a **label** (text, heading, caption, image, number, …)
- a **box** in page coordinates
- the block's **text**, in reading order

Line- or word-level coordinates are *not* required. A tool that outputs blocks with boxes can be
plugged in with an adapter that writes the same `ocr/page_res.json` shape; a tool without
coordinates (Markdown only) would need its text aligned to the measured lines — a real change.

## Summary

None of the VLM tools gives line-level coordinates in normal page parsing; the ones that do are
classic engines (Tesseract hOCR/ALTO/TSV, Docling's standard pipeline, Surya). olmOCR 2 has no
coordinates at all.

| Tool | Formats | Coordinate granularity | Labels | Mac M4 | 12 GB RTX 4070 | License |
|---|---|---|---|---|---|---|
| **PaddleOCR-VL** (current) | JSON, Markdown | block (v1.5 "spotting": lines, unverified) | rich | yes (MLX-VLM, in use) | yes | Apache-2.0 |
| olmOCR 2 | YAML front matter + Markdown (HTML tables, LaTeX), Dolma | none (figure box only encoded in an image file name) | none (`is_table`/`is_diagram` flags) | community GGUF/MLX builds only, ~30–40 s/page | at the official minimum (≥12 GB), untested | Apache-2.0 |
| Docling, standard pipeline | DoclingDocument JSON, Markdown, HTML | block + table cell; line/word in the parser layer (exposure for OCR'd pages unverified) | rich | yes (CPU/MPS) | yes | MIT |
| Docling, GraniteDocling VLM | DocTags → JSON/MD/HTML | block, on a 0–500 grid | rich | yes (MLX) | yes | Apache-2.0 |
| Marker | Markdown, JSON, HTML, Chunks | block polygons (lines not documented) | rich | yes (llama.cpp/MPS) | likely (VRAM not stated) | code Apache-2.0, weights modified OpenRAIL-M |
| Surya 2.5 | JSON, HTML per block | block polygons | rich + reading order | yes, slow (~0.2 pages/s on M4 Max) | vLLM in Docker, VRAM not stated | code Apache-2.0, weights modified OpenRAIL-M |
| Tesseract / OCRmyPDF | txt, hOCR, ALTO, TSV, PDF with text layer | block / paragraph / line / word | structural only | yes (CPU) | n/a (CPU) | Apache-2.0 / MPL-2.0 |

## Details per tool

### olmOCR 2 (allenai)
- Output: per page YAML front matter (`primary_language`, `is_rotation_valid`,
  `rotation_correction`, `is_table`, `is_diagram`) followed by Markdown; the pipeline writes Dolma
  documents, with `--markdown` also `.md` files.
  [model card](https://huggingface.co/allenai/olmOCR-2-7B-1025/blob/main/README.md),
  [repo](https://github.com/allenai/olmocr)
- Coordinates: **none** for text. The prompt asks for tables as HTML and equations as LaTeX;
  figures become Markdown image links named `page_startx_starty_width_height.png` — the only
  location hint, model-generated and unvalidated. No header/footer labels.
  [prompts.py](https://raw.githubusercontent.com/allenai/olmocr/main/olmocr/prompts/prompts.py)
- Formatting: Markdown bold/italic possible; no font information.
- Hardware: the official pipeline needs NVIDIA ≥ 12 GB (tested 4090, L40S, A100, H100), runs on
  vLLM, FP8 by default. A 4070 meets the minimum on paper but is untested; KV cache will be tight
  (inferred).
- Mac: community builds only — GGUF via LM Studio / llama.cpp (needs the mmproj file) and
  `mlx-community/olmOCR-2-7B-1025-8bit`. One report: ~37 s/page via GGUF on an M5 Pro, MLX ~20 %
  faster. These skip the pipeline's rendering (1288 px), rotation handling and retries.
  [GGUF](https://huggingface.co/lmstudio-community/olmOCR-2-7B-1025-GGUF),
  [report](https://newsletter.codecut.ai/p/extracting-pdf-tables-on-apple-silicon)

### Docling (IBM)
- Output: DoclingDocument (JSON), exported to Markdown, HTML or DocTags.
  [docs](https://docling-project.github.io/docling/concepts/docling_document/)
- Standard pipeline: layout model + TableFormer + pluggable OCR engine. Items carry provenance
  bounding boxes; table cells have boxes. Line and word cells exist in the parser layer
  (`SegmentedPdfPage.textline_cells`, word cells —
  [docling-parse](https://github.com/docling-project/docling-parse)); whether OCR-produced cells are
  exposed the same way is **unverified**.
- VLM pipeline (GraniteDocling 258M / SmolDocling): DocTags give each element
  `<loc_x1><loc_y1><loc_x2><loc_y2>` on a 0–500 grid scaled to the page — **block-level**, ~0.2 %
  precision. Table cells are OTSL structure without boxes.
  [paper](https://arxiv.org/html/2503.11576v1)
- Labels: title, section_header, text, list_item, caption, footnote, formula (LaTeX), table,
  picture, code, page_header, page_footer, document_index, form/key-value.
  [labels.py](https://raw.githubusercontent.com/docling-project/docling-core/main/docling_core/types/doc/labels.py)
- Figures can be exported as cropped images. Bold/italic: a formatting model exists; not shown to
  be filled for scanned pages (unverified).
- Hardware: MLX variants on Apple Silicon (SmolDocling MLX ~6.2 s/page on M3 Max; no GraniteDocling
  timing published). [vision models](https://docling-project.github.io/docling/usage/vision_models/)
  Small models — a 4070 is fine (inferred).
- License: Docling MIT (from memory); GraniteDocling Apache-2.0
  ([model](https://huggingface.co/ibm-granite/granite-docling-258M)).

### Marker / Surya (datalab)
- Marker output: Markdown, JSON (page → block tree), HTML, Chunks. Each block has a 4-corner
  `polygon`, apparently in PDF points (inferred). Block types include SectionHeader, Text, ListItem,
  Caption, Footnote, Table, Figure, Picture, Equation, Code, PageHeader, PageFooter,
  TableOfContents, Form, Handwriting. Line/Span exist internally; not documented in the JSON.
  Tables as HTML, math as LaTeX (`$$` in Markdown, `<math>` in HTML), images saved or base64,
  `<i>` in HTML; no font metadata. [repo](https://github.com/datalab-to/marker)
- Surya 2.5 (689M): OCR output is **block-level** — `label`, `polygon`/`bbox`, `reading_order`,
  `html`, `confidence`; no line/word boxes documented. Labels like Marker plus Diagram,
  ChemicalBlock, Bibliography. Tables as HTML with rowspan/colspan; math KaTeX LaTeX in `<math>`.
  [repo](https://github.com/datalab-to/surya)
- Hardware: NVIDIA via vLLM in Docker (benchmarks on H100, 4.5 pages/s; VRAM not stated);
  Apple Silicon via llama.cpp or experimental `mlx_vlm`, ~0.2 pages/s on M4 Max. Marker `fast`
  mode runs on CPU/MPS.
- License: code Apache-2.0; weights modified OpenRAIL-M (free for research, personal and
  startups < $5M; other commercial use needs a paid plan).

### OCRmyPDF / Tesseract
- Tesseract output: txt, hOCR, TSV, PDF (image + invisible text), ALTO (from memory).
  [usage](https://tesseract-ocr.github.io/tessdoc/Command-Line-Usage.html) TSV/hOCR give a page →
  block → paragraph → line → word hierarchy with boxes and word confidence; hOCR adds baseline and
  x-size. Layout labels are structural only (no headings, tables, captions); no reading-order
  model. Bold/italic only with the legacy engine (`--oem 0`), not LSTM.
  [thread](https://groups.google.com/g/tesseract-ocr/c/DOoCWd4GBC8/m/iQEMIUUsBAAJ)
- OCRmyPDF: PDF with a text layer (fpdf2 renderer by default) and `--sidecar` text; hOCR only as
  an intermediate (`-k` keeps it). [advanced](https://ocrmypdf.readthedocs.io/en/latest/advanced.html)
- Hardware: CPU only. License: Tesseract Apache-2.0, OCRmyPDF MPL-2.0 (from memory).

### PaddleOCR-VL (reference)
- Default pipeline: **block-level only**, confirmed from this project's output
  (`manuals/<manual>/output/pages/p-NNN/ocr/page_res.json`): `width`, `height`, `parsing_res_list`
  (`block_label`, `block_content`, `block_bbox`, `block_order`, `block_polygon_points`) and
  `layout_det_res` (block boxes). No lines or words.
- v1.5 adds "text spotting" (text-line localisation + recognition, reportedly quadrilaterals) via a
  separate `Spotting:` prompt; output format and pipeline integration unverified.
  [vLLM recipe](https://recipes.vllm.ai/PaddlePaddle/PaddleOCR-VL-1.5),
  [model](https://huggingface.co/PaddlePaddle/PaddleOCR-VL-1.5); v1.6 exists
  ([model](https://huggingface.co/PaddlePaddle/PaddleOCR-VL)).
- License: Apache-2.0.

## What switching (or adding) an engine would change here

1. **An adapter in step 2** that writes the same `ocr/page_res.json` shape: labels mapped to ours,
   coordinates converted (Docling 0–500 grid, Marker PDF points, Tesseract pixels). Steps 3–5 stay.
2. **Text clean-up per engine** — LaTeX is handled (`measure.clean_latex`); Marker/Surya HTML
   (`<i>`, `<math>`) would need stripping.
3. **Corrections may stop matching**: they match on the start of the OCR text, so a different
   engine's text can break them.
4. olmOCR 2 would need text-to-line alignment (no coordinates) — a bigger change.

## Options noted for later

- **Run PaddleOCR-VL on the RTX 4070**: PaddleOCR can use a vLLM server
  (`vl_rec_backend="vllm-server"`); only the server URL changes on the Mac side.
- **Second engine as a cross-check**: most corrections so far were word misreads ("ensure" for the
  printed "insure", "Choku", "i6)"). Comparing Tesseract's words per block with PaddleOCR-VL's would
  flag likely misreads automatically.
- **Benchmark**: pages with complete corrections (nakamichi-bx-300 p. 5, 7) are effectively ground
  truth for comparing engines.
