"""Shared paths and helpers for the scan-to-native-pdf steps.

Layout, one directory per manual:
    manuals/<manual>/
      input/<scan>.pdf                  the scanned manual (exactly one PDF)          git-ignored
      output/pages/p-NNN/page.pdf       one page of the scan                          git-ignored
      output/pages/p-NNN/ocr/           OCR results for that page                     git-ignored
      output/pages/p-NNN/result.pdf     regenerated page                              git-ignored
      output/<manual>.pdf               merged result                                 git-ignored
      pages.toml                        page types, [cleanup], [tables] settings      tracked
      manual.toml                       typography (init_fonts.py; hand-tunable)      tracked
      corrections/p-NNN.toml            per-page OCR text fixes                       tracked
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANUALS = ROOT / "manuals"


def manual_dir(manual: str) -> Path:
    return MANUALS / manual


def known_manuals() -> list[str]:
    return sorted(p.name for p in MANUALS.iterdir() if p.is_dir()) if MANUALS.is_dir() else []


def corrections_file(manual: str, page: int) -> Path:
    return manual_dir(manual) / "corrections" / f"p-{page:03d}.toml"


def manual_toml(manual: str) -> Path:
    return manual_dir(manual) / "manual.toml"


PAGE_TYPES = ("text", "drawing", "scan_only")


def page_types_file(manual: str) -> Path:
    return manual_dir(manual) / "pages.toml"


def page_types(manual: str) -> dict[int, str]:
    """Page classification from manuals/<manual>/pages.toml:
        drawing   = [21, 26]   # schematics, PCB layouts: not run through the text pipeline
        scan_only = [1]        # keep the scanned page unchanged (cover, photos, ...)
        text      = [12]       # convert even though it looks like a drawing
    Pages not listed: decided automatically (text-ink share)."""
    f = page_types_file(manual)
    data = _pages_toml(manual)
    table = data.get("pages", {k: v for k, v in data.items() if k not in PAGES_SECTIONS})
    out = {}
    for kind in PAGE_TYPES:
        for n in table.get(kind, []):
            if n in out:
                sys.exit(f"error: page {n} listed twice in {f} ({out[n]} and {kind})")
            out[int(n)] = kind
    unknown = set(table) - set(PAGE_TYPES)
    if unknown:
        sys.exit(f"error: unknown page type(s) {sorted(unknown)} in {f}; use {', '.join(PAGE_TYPES)}")
    return out


PAGES_SECTIONS = ("pages", "cleanup", "tables")


def _pages_toml(manual: str) -> dict:
    import tomllib
    f = page_types_file(manual)
    return tomllib.loads(f.read_text()) if f.is_file() else {}


def cleanup_level(manual: str, page: int, default: int = 1) -> int:
    """Picture clean-up level for a page from pages.toml:
        [cleanup]
        level = 1            # default for the manual
        level_0 = [18, 19]   # per-page overrides: level_<N> = [pages]"""
    sec = _pages_toml(manual).get("cleanup", {})
    for key, pages in sec.items():
        if key.startswith("level_") and page in pages:
            return int(key.split("_", 1)[1])
    return int(sec.get("level", default))


def tables_as_image(manual: str, page: int) -> bool:
    """[tables] keep_as_image = [9]  -> tables on these pages stay pictures (not rendered as text)."""
    return page in _pages_toml(manual).get("tables", {}).get("keep_as_image", [])


def scan_pdf(manual: str) -> Path:
    """The single scanned PDF in manuals/<manual>/input/."""
    d = manual_dir(manual) / "input"
    if not d.is_dir():
        sys.exit(f"error: no input directory {d} (manuals found: {', '.join(known_manuals()) or 'none'})")
    pdfs = sorted(d.glob("*.pdf"))
    if len(pdfs) != 1:
        sys.exit(f"error: expected exactly one PDF in {d}, found {len(pdfs)}: {[p.name for p in pdfs]}")
    return pdfs[0]


def manual_out(manual: str) -> Path:
    return manual_dir(manual) / "output"


def pages_dir(manual: str) -> Path:
    return manual_out(manual) / "pages"


def page_dir(manual: str, page: int) -> Path:
    """Page numbers are 1-based."""
    return pages_dir(manual) / f"p-{page:03d}"


def existing_pages(manual: str) -> list[int]:
    d = pages_dir(manual)
    return sorted(int(p.name[2:]) for p in d.glob("p-[0-9][0-9][0-9]") if (p / "page.pdf").is_file())


def parse_pages(spec: str | None, available: list[int]) -> list[int]:
    """'3', '1-5', '1,4,7-9' or None/'all' -> sorted page numbers, limited to available pages."""
    if not spec or spec == "all":
        return list(available)
    wanted = set()
    for part in spec.split(","):
        a, _, b = part.partition("-")
        wanted.update(range(int(a), int(b or a) + 1))
    missing = sorted(wanted - set(available))
    if missing:
        sys.exit(f"error: pages not available: {missing}")
    return sorted(wanted)
