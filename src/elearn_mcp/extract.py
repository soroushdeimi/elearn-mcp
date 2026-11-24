"""Text extraction from course material files."""

import json
import logging
import pathlib
import re
import unicodedata

import docx
import pptx
from pypdf import PdfReader
from pypdf.errors import PyPdfError

# pypdf warns on every page about optional fonts; it does not change the text we keep
logging.getLogger("pypdf").setLevel(logging.ERROR)

SUPPORTED = {".pdf", ".pptx", ".docx", ".txt", ".md", ".py", ".ipynb"}
# bump when extraction output changes so old text copies get rebuilt
VERSION = 2

# pypdf splits words with a newline-space-newline; real line breaks are plain "\n"
_WORD_BREAK = re.compile(r"\n[ \t]*[ \t]\n")
# pypdf emits Persian/Arabic-Indic digit runs reversed (e.g. "۵۰۴۱" for "۱۴۰۵")
_DIGIT_RUN = re.compile(r"[٠-٩۰-۹](?:[٠-٩۰-۹]|[:/.٫][٠-٩۰-۹])*")


def clean_pdf_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = _WORD_BREAK.sub(" ", text)
    text = _DIGIT_RUN.sub(lambda m: m.group(0)[::-1], text)
    lines = []
    for line in text.split("\n"):
        if ")" in line and "(" in line and line.find(")") < line.find("("):
            line = line.replace("(", "\x00").replace(")", "(").replace("\x00", ")")
        lines.append(line.strip())
    return re.sub(r"  +", " ", "\n".join(lines))


def _extract_pdf(path: pathlib.Path) -> str:
    reader = PdfReader(str(path))
    pages = []
    for number, page in enumerate(reader.pages, start=1):
        try:
            pages.append(clean_pdf_text(page.extract_text() or ""))
        except PyPdfError:
            # one broken page (e.g. pypdf's safety limits) should not lose the whole book
            pages.append(f"[page {number}: text could not be extracted]")
    return "\n\n".join(pages)


def _extract_pptx(path: pathlib.Path) -> str:
    prs = pptx.Presentation(str(path))
    parts = []
    for i, slide in enumerate(prs.slides, 1):
        parts.append(f"## Slide {i}")
        for shape in slide.shapes:
            if shape.has_text_frame:
                parts.append(shape.text_frame.text)
        if slide.has_notes_slide:
            parts.append(slide.notes_slide.notes_text_frame.text)
    return "\n".join(parts)


def _extract_docx(path: pathlib.Path) -> str:
    doc = docx.Document(str(path))
    parts = [p.text for p in doc.paragraphs]
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                parts.append(cell.text)
    return "\n".join(parts)


def _extract_ipynb(path: pathlib.Path) -> str:
    nb = json.loads(path.read_text(encoding="utf-8"))
    return "\n\n".join("".join(cell.get("source", [])) for cell in nb["cells"])


def extract_text(path) -> str:
    path = pathlib.Path(path)
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return _extract_pdf(path)
    if suffix == ".pptx":
        return _extract_pptx(path)
    if suffix == ".docx":
        return _extract_docx(path)
    if suffix in {".txt", ".md", ".py"}:
        return path.read_text(encoding="utf-8")
    if suffix == ".ipynb":
        return _extract_ipynb(path)
    raise ValueError(f"unsupported file type: {suffix}")


def write_text_copy(path) -> pathlib.Path:
    path = pathlib.Path(path)
    out = path.with_name(path.name + ".txt")
    out.write_text(extract_text(path), encoding="utf-8")
    return out
