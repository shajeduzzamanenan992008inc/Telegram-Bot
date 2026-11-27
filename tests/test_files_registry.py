"""Uploaded-file extraction with real Office/PDF/media containers."""

from __future__ import annotations

import asyncio
import io
import struct
import wave
import zipfile

import pytest

from enan_ultra.files.registry import FileRegistry


def extract(settings, name, data, router=None, mime=""):
    return asyncio.run(FileRegistry(router).extract(name, mime, data, settings))


def all_text(result) -> str:
    return "\n".join(chunk.text for chunk in result.chunks)


def build_presentation():
    from pptx import Presentation
    from pptx.util import Inches

    presentation = Presentation()
    first = presentation.slides.add_slide(presentation.slide_layouts[1])
    first.shapes.title.text = "Quarterly review"
    first.placeholders[1].text = "Revenue up 12%"
    first.notes_slide.notes_text_frame.text = "mention the churn drop"
    second = presentation.slides.add_slide(presentation.slide_layouts[5])
    second.shapes.title.text = "Table slide"
    table = second.shapes.add_table(2, 2, Inches(1), Inches(2), Inches(4), Inches(1)).table
    table.cell(0, 0).text, table.cell(0, 1).text = "Region", "Sales"
    table.cell(1, 0).text, table.cell(1, 1).text = "Dhaka", "42"
    group = second.shapes.add_group_shape()
    group.shapes.add_textbox(0, 0, Inches(2), Inches(1)).text_frame.text = "grouped text"
    buffer = io.BytesIO()
    presentation.save(buffer)
    return buffer.getvalue()


def test_pptx_text_tables_groups_and_notes_are_extracted(make_settings):
    """Regression: slicing presentation.slides raised AttributeError, so no .pptx could be read."""
    result = extract(make_settings(), "deck.pptx", build_presentation())
    text = all_text(result)
    for expected in ("Quarterly review", "Revenue up 12%", "Speaker notes: mention the churn drop",
                     "Region | Sales", "Dhaka | 42", "grouped text"):
        assert expected in text
    assert "parse failed" not in text.lower()
    assert [chunk.source for chunk in result.chunks] == ["slide 1", "slide 2"]


def test_empty_presentation_does_not_fail(make_settings):
    from pptx import Presentation

    buffer = io.BytesIO()
    Presentation().save(buffer)
    result = extract(make_settings(), "empty.pptx", buffer.getvalue())
    assert "parse failed" not in all_text(result).lower()


def test_docx_and_xlsx_are_read(make_settings):
    import docx
    import openpyxl

    document = docx.Document()
    document.add_paragraph("Paragraph about বাংলা text.")
    table = document.add_table(rows=1, cols=2)
    table.cell(0, 0).text, table.cell(0, 1).text = "left", "right"
    buffer = io.BytesIO()
    document.save(buffer)
    text = all_text(extract(make_settings(), "note.docx", buffer.getvalue()))
    assert "বাংলা" in text and "left" in text and "right" in text

    workbook = openpyxl.Workbook()
    workbook.active.title = "Sales"
    workbook.active.append(["name", "qty"])
    workbook.active.append(["apple", 3])
    buffer = io.BytesIO()
    workbook.save(buffer)
    result = extract(make_settings(), "book.xlsx", buffer.getvalue())
    assert "apple" in all_text(result)
    assert any("Sales" in chunk.source for chunk in result.chunks)


def wav_bytes() -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(8000)
        handle.writeframes(b"\x00\x00" * 800)
    return buffer.getvalue()


def avi_bytes() -> bytes:
    return b"RIFF" + struct.pack("<I", 12) + b"AVI " + b"LIST" + struct.pack("<I", 0)


def image_bytes(fmt: str) -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (32, 32), "red").save(buffer, fmt)
    return buffer.getvalue()


@pytest.mark.parametrize("name,payload,expected", [
    ("voice.wav", wav_bytes, "audio"),
    ("clip.avi", avi_bytes, "video"),
    ("photo.png", lambda: image_bytes("PNG"), "image"),
    ("photo.webp", lambda: image_bytes("WEBP"), "image"),
])
def test_riff_containers_reach_the_right_adapter(name, payload, expected):
    """Regression: every RIFF file (WAV audio, AVI video) used to be claimed by the image adapter."""
    data = payload()
    adapter = next(item.name for item in FileRegistry(None).adapters if item.detect(name, "", data))
    assert adapter == expected


def test_wav_audio_is_transcribed(make_settings):
    calls = []

    class Router:
        async def transcribe(self, data, filename, mime):
            calls.append(filename)
            return "spoken words"

    result = extract(make_settings(), "note.wav", wav_bytes(), router=Router(), mime="audio/wav")
    assert calls == ["note.wav"]
    assert all_text(result) == "spoken words"


def test_zip_members_with_path_traversal_are_skipped(make_settings):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("a.txt", "first file")
        archive.writestr("dir/b.md", "# second")
        archive.writestr("../evil.txt", "traversal")
    text = all_text(extract(make_settings(), "bundle.zip", buffer.getvalue()))
    assert "first file" in text and "# second" in text
    assert "traversal" not in text


@pytest.mark.parametrize("payload,expected", [
    ("বাংলা লেখা".encode("utf-8"), "বাংলা লেখা"),
    ("hello utf16".encode("utf-16"), "hello utf16"),
    ("café".encode("utf-8-sig"), "café"),
])
def test_text_decoding(make_settings, payload, expected):
    assert expected in all_text(extract(make_settings(), "note.txt", payload))
