# Convert Page from OCR to native PDF

Table of Content

## Introduction



## Scripts called by convert_page.py


### table_render.py
How it works (scripts/table_render.py, called by convert_page.py)
1. Ruling lines are taken from the scan and redrawn as crisp vector lines.
2. Columns come from the vertical rules, or from white gaps in unruled tables.
3. Rows come from full-width horizontal rules when they match the OCR rows one to one (the adjustment tables). Otherwise each OCR row is matched across all columns to the scanned lines by text width (the parts lists).
4. Text is placed left-aligned at the measured position of every line, in a font size estimated per table. Bold group headings are detected, and numbered steps in a cell start new lines as printed.
5. Clean-up of the OCR text: LaTeX that the OCR writes without $ signs (\mu, \textcircled{R}, \%) is converted too.
6. Quality gate: if more than 15% of a table's lines don't match the scan, or its columns can't be found, the table stays a picture with a warning. A wrong parts list is worse than a scanned one.