# Examples

Each example is a manual directory with its settings and OCR corrections — everything except the
scan itself, which is not included (copyright). To use one, copy it to `manuals/`, put the scan in
its `input/` folder and run the steps from the main README:

```
cp -r examples/<manual> manuals/
```

The tools expect exactly one PDF in `input/`; its file name doesn't matter.

## nakamichi-bx-300

Get the **Nakamichi BX-300 service manual** from [HiFi Engine](https://www.hifiengine.com)
(search for "Nakamichi BX-300", free registration required). The file this example was made with
is `hfe.nakamichi.483075.6659.pdf` (34 pages); put it in `manuals/nakamichi-bx-300/input/`.

The settings (`pages.toml`, `manual.toml`, `corrections/`) were made for that scan. A different
scan of the same manual may need different corrections.

## About the corrections

`corrections/p-NNN.toml` fixes OCR mistakes page by page (misread words, lost lines, wrong line
breaks). Corrections are written either by hand or by an AI agent (see `AGENTS.md` and the
`rebuild-page` procedure), and **every correction has to be reviewed against the scan** before it
is trusted. Agents make mistakes too, and the OCR model tends to "modernise" wording (e.g. the
manual's "insure" becomes "ensure"); a correction must restore what is printed, not improve it.
