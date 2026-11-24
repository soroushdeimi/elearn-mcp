import json
import pathlib

import docx
import pptx
import pytest
from pypdf import PdfWriter

from elearn_mcp import extract


def make_pdf(path: pathlib.Path):
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    with open(path, "wb") as f:
        writer.write(f)


def test_extract_pdf_blank_page_is_empty(tmp_path):
    path = tmp_path / "blank.pdf"
    make_pdf(path)
    assert extract.extract_text(path) == ""


def test_extract_pdf_joins_pages_and_none(tmp_path, monkeypatch):
    path = tmp_path / "doc.pdf"
    make_pdf(path)

    class FakePage:
        def __init__(self, text):
            self._text = text

        def extract_text(self):
            return self._text

    class FakeReader:
        def __init__(self, *a, **kw):
            self.pages = [FakePage("page one"), FakePage(None), FakePage("page three")]

    monkeypatch.setattr(extract, "PdfReader", FakeReader)
    assert extract.extract_text(path) == "page one\n\n\n\npage three"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("ﻫﻮش\n \nﻣﺼﻨﻮﻋﯽ", "هوش مصنوعی"),
        ("پاییز ۵۰۴۱", "پاییز ۱۴۰۵"),
        ("ساعت ۹۵:۳۲", "ساعت ۲۳:۵۹"),
        ("دکتر نمونه ) teacher@example.ac.ir (", "دکتر نمونه ( teacher@example.ac.ir )"),
        ("f(x) = 2 (ok)", "f(x) = 2 (ok)"),
        ("version 2026 and ۱", "version 2026 and ۱"),
        ("line one\nline two", "line one\nline two"),
    ],
)
def test_clean_pdf_text_golden(raw, expected):
    assert extract.clean_pdf_text(raw) == expected


def test_clean_pdf_text_word_break_with_tabs_and_extra_spaces():
    assert extract.clean_pdf_text("ﻫﻮش\n \t \nﻣﺼﻨﻮﻋﯽ") == "هوش مصنوعی"


def test_clean_pdf_text_reverses_arabic_indic_digits_with_separators():
    assert extract.clean_pdf_text("۹۵:۳۲/۵۰۴۱") == "۱۴۰۵/۲۳:۵۹"


def test_clean_pdf_text_keeps_latin_digits_and_single_digit():
    assert extract.clean_pdf_text("v2026 and ۱") == "v2026 and ۱"


def test_clean_pdf_text_collapses_spaces_and_strips_line_ends():
    assert extract.clean_pdf_text("a   b  \nc d") == "a b\nc d"


def test_extract_pdf_applies_cleaning(tmp_path, monkeypatch):
    path = tmp_path / "doc.pdf"
    make_pdf(path)

    class FakePage:
        def extract_text(self):
            return "ﻫﻮش\n \nﻣﺼﻨﻮﻋﯽ"

    class FakeReader:
        def __init__(self, *a, **kw):
            self.pages = [FakePage()]

    monkeypatch.setattr(extract, "PdfReader", FakeReader)
    assert extract.extract_text(path) == "هوش مصنوعی"


def test_extract_pptx_slides_and_notes(tmp_path):
    path = tmp_path / "deck.pptx"
    prs = pptx.Presentation()
    slide1 = prs.slides.add_slide(prs.slide_layouts[5])
    slide1.shapes.title.text = "First slide"
    slide1.notes_slide.notes_text_frame.text = "note one"
    slide2 = prs.slides.add_slide(prs.slide_layouts[5])
    slide2.shapes.title.text = "Second slide"
    prs.save(path)
    text = extract.extract_text(path)
    assert "## Slide 1" in text
    assert "## Slide 2" in text
    assert "First slide" in text
    assert "Second slide" in text
    assert "note one" in text
    assert text.index("## Slide 1") < text.index("First slide") < text.index("## Slide 2")


def test_extract_docx_paragraphs_and_table(tmp_path):
    path = tmp_path / "doc.docx"
    doc = docx.Document()
    doc.add_paragraph("intro paragraph")
    table = doc.add_table(rows=1, cols=2)
    table.rows[0].cells[0].text = "cell a"
    table.rows[0].cells[1].text = "cell b"
    doc.save(path)
    text = extract.extract_text(path)
    assert "intro paragraph" in text
    assert "cell a" in text
    assert "cell b" in text


def test_extract_ipynb_concatenates_cell_source(tmp_path):
    path = tmp_path / "nb.ipynb"
    nb = {
        "cells": [
            {"cell_type": "code", "source": ["print(1)\n", "print(2)"]},
            {"cell_type": "markdown", "source": ["# title"]},
        ]
    }
    path.write_text(json.dumps(nb), encoding="utf-8")
    assert extract.extract_text(path) == "print(1)\nprint(2)\n\n# title"


def test_extract_plain_text(tmp_path):
    path = tmp_path / "notes.md"
    path.write_text("hello\nworld", encoding="utf-8")
    assert extract.extract_text(path) == "hello\nworld"


def test_extract_unsupported_suffix_raises(tmp_path):
    path = tmp_path / "data.xyz"
    path.write_text("x", encoding="utf-8")
    with pytest.raises(ValueError, match="unsupported file type: \\.xyz"):
        extract.extract_text(path)


def test_write_text_copy_name_and_content(tmp_path):
    path = tmp_path / "a.pdf"
    make_pdf(path)
    out = extract.write_text_copy(path)
    assert out == tmp_path / "a.pdf.txt"
    assert out.read_text(encoding="utf-8") == ""


def test_extract_pdf_keeps_going_past_a_broken_page(tmp_path, monkeypatch):
    from pypdf.errors import LimitReachedError

    path = tmp_path / "book.pdf"
    make_pdf(path)

    class FakePage:
        def __init__(self, text):
            self._text = text

        def extract_text(self):
            if self._text is None:
                raise LimitReachedError("Reached maximum number of tokens per stream.")
            return self._text

    class FakeReader:
        def __init__(self, *a, **kw):
            self.pages = [FakePage("one"), FakePage(None), FakePage("three")]

    monkeypatch.setattr(extract, "PdfReader", FakeReader)
    text = extract.extract_text(path)
    assert text == "one\n\n[page 2: text could not be extracted]\n\nthree"
