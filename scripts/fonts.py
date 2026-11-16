"""Font families available for regenerated pages (URW base-35 Type 1 fonts shipped with Ghostscript)."""
import glob
import sys
from pathlib import Path

from reportlab.pdfbase import pdfmetrics

# family -> AFM/PFB stems for regular, bold, italic, bold italic
FAMILIES = {
    "century":   ("c059013l", "c059016l", "c059033l", "c059036l"),  # Century Schoolbook
    "times":     ("n021003l", "n021004l", "n021023l", "n021024l"),  # Times
    "palatino":  ("p052003l", "p052004l", "p052023l", "p052024l"),  # Palatino
    "bookman":   ("b018012l", "b018015l", "b018032l", "b018035l"),  # Bookman
    "helvetica": ("n019003l", "n019004l", "n019023l", "n019024l"),  # Helvetica
    "helvetica-narrow": ("n019043l", "n019044l", "n019063l", "n019064l"),
    "avantgarde": ("a010013l", "a010015l", "a010033l", "a010035l"),  # Avant Garde Gothic
    "courier":   ("n022003l", "n022004l", "n022023l", "n022024l"),  # Courier
}
STYLES = ("regular", "bold", "italic", "bolditalic")


def font_dir() -> Path:
    hits = sorted(glob.glob("/opt/homebrew/Cellar/ghostscript/*/share/ghostscript/fonts/c059013l.pfb")
                  + glob.glob("/usr/local/share/ghostscript/fonts/c059013l.pfb")
                  + glob.glob("/usr/share/fonts/type1/gsfonts/c059013l.pfb"))
    if not hits:
        sys.exit("error: URW Type 1 fonts not found; install ghostscript (brew install ghostscript)")
    return Path(hits[-1]).parent


_registered: dict[str, dict[str, str]] = {}


def load_family(family: str) -> dict[str, str]:
    """Register a family with reportlab; returns {style: reportlab font name}."""
    if family not in FAMILIES:
        sys.exit(f"error: unknown font family '{family}' (known: {', '.join(FAMILIES)})")
    if family not in _registered:
        d = font_dir()
        names = {}
        for style, stem in zip(STYLES, FAMILIES[family]):
            face = pdfmetrics.EmbeddedType1Face(str(d / f"{stem}.afm"), str(d / f"{stem}.pfb"))
            pdfmetrics.registerTypeFace(face)
            pdfmetrics.registerFont(pdfmetrics.Font(face.name, face.name, "WinAnsiEncoding"))
            names[style] = face.name
        _registered[family] = names
    return _registered[family]


def cap_height(family: str, style: str = "regular") -> float:
    """Cap height as a fraction of the font size (from the AFM file)."""
    stem = FAMILIES[family][STYLES.index(style)]
    for line in (font_dir() / f"{stem}.afm").read_text(errors="replace").splitlines():
        if line.startswith("CapHeight"):
            return float(line.split()[1]) / 1000
    return 0.72
