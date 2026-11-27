"""Conversation and document export."""

from __future__ import annotations

import io

from pypdf import PdfReader

from enan_ultra.services.document_export import DocumentExportService as Export

HISTORY = [
    {"role": "user", "content": "How do I print in Python? Also 5 < 6 & more"},
    {"role": "assistant", "content": (
        "Use print:\n\n```python\n# a comment\nprint('hello')\nif True:\n    x = 1\n```\n\n"
        "That prints hello. Another block:\n\n```js\nconsole.log(1)\n\nconsole.log(2)\n```\n\nThe end of the answer."
    )},
    {"role": "user", "content": "Thanks!"},
]


def pdf_text(data: bytes) -> str:
    return "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(data)).pages)


def test_pdf_export_keeps_fenced_code_and_following_text():
    """Regression: fenced code was dropped and later paragraphs were rendered as code."""
    name, data, mime = Export.render(HISTORY, "pdf")
    assert (name, mime) == ("conversation.pdf", "application/pdf")
    text = pdf_text(data)
    for expected in ("print('hello')", "x = 1", "console.log(1)", "console.log(2)",
                     "That prints hello", "The end of the answer", "Thanks!", "5 < 6 & more"):
        assert expected in text
    assert "```" not in text
    assert "_Exported" not in text


def test_pdf_export_closes_a_truncated_code_fence():
    history = [
        {"role": "assistant", "content": "Intro\n\n```sh\n#!/bin/bash\necho unterminated"},
        {"role": "user", "content": "Thanks!"},
    ]
    text = pdf_text(Export.render(history, "pdf")[1])
    assert "echo unterminated" in text and "Thanks!" in text
    assert "## You" not in text


def test_txt_export_does_not_mangle_code_comments():
    """Regression: '#' prefixes were stripped from every line, including code comments and shebangs."""
    history = [{"role": "assistant", "content": "```sh\n#!/bin/bash\n# comment\necho hi\n```"}]
    text = Export.render(history, "txt")[1].decode("utf-8")
    assert "#!/bin/bash" in text and "# comment" in text
    assert text.startswith("Conversation export")


def test_markdown_export_keeps_structure():
    text = Export.render(HISTORY, "md")[1].decode("utf-8")
    assert text.startswith("# Conversation export")
    assert "## You" in text and "## AI" in text


def test_document_export_txt_keeps_content_verbatim():
    name, data, _ = Export.render_document(
        "script.py", "text/x-python", [{"source": "lines 1-3", "content": "# comment\nprint(1)"}], "txt",
    )
    assert name == "script.txt"
    assert "# comment\nprint(1)" in data.decode("utf-8")
