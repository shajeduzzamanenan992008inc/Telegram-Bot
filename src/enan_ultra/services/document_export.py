"""Private conversation exports in Markdown, UTF-8 text, and PDF formats."""

from __future__ import annotations

import io
import re
from datetime import UTC, datetime
from html import escape
from pathlib import Path
from typing import Any, ClassVar

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

# Noto Sans Bengali (and Nirmala on Windows) is a Bengali-script font: it has no Latin
# glyphs, so Latin text rendered with it lands on .notdef and comes out of the PDF as
# blank boxes and unextractable NUL characters. Only Bengali runs use that font.
BENGALI_RUN = re.compile(r"[\u0980-\u09ff]+")


def _close_open_fence(body: str) -> str:
    """Close a truncated reply's unterminated code fence so it cannot swallow later messages."""
    fences = sum(1 for line in body.splitlines() if line.strip().startswith("```"))
    return body + "\n```" if fences % 2 else body


def _preserve_indentation(text: str) -> str:
    """ReportLab paragraphs collapse whitespace; keep code indentation and column alignment."""
    text = text.replace("\t", "    ")
    return re.sub(r" {2,}|^ ", lambda match: "&nbsp;" * len(match.group()), text)


class DocumentExportService:
    """Render saved chat history without writing private content to disk."""

    SUPPORTED_FORMATS: ClassVar[frozenset[str]] = frozenset({"md", "markdown", "txt", "pdf"})
    MAX_EXPORT_CHARS = 250_000

    @classmethod
    def normalize_format(cls, requested: str) -> str:
        value = requested.strip().casefold().lstrip(".")
        if value not in cls.SUPPORTED_FORMATS:
            raise ValueError("Choose one of: md, txt, pdf")
        return "md" if value == "markdown" else value

    @classmethod
    def render(cls, history: list[dict[str, Any]], requested: str) -> tuple[str, bytes, str]:
        export_format = cls.normalize_format(requested)
        if not history:
            raise ValueError("There is no saved conversation to export yet.")
        timestamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
        markdown = ["# Conversation export", f"_Exported {timestamp}_", ""]
        plain = ["Conversation export", f"Exported {timestamp}", ""]
        for item in history:
            role = "You" if item.get("role") == "user" else "AI"
            body = str(item.get("content") or "").strip()
            if not body:
                continue
            markdown.extend([f"## {role}", "", _close_open_fence(body), ""])
            plain.extend([role, "", body, ""])
        source = "\n".join(markdown).strip() + "\n"
        if len(source) > cls.MAX_EXPORT_CHARS:
            raise ValueError("This conversation is too large to export as one document.")
        if export_format == "md":
            return "conversation.md", source.encode("utf-8"), "text/markdown"
        if export_format == "txt":
            # Built from the entries, not by stripping "#" from the Markdown: message bodies
            # (code comments, shebangs, headings) must be exported verbatim.
            return "conversation.txt", ("\n".join(plain).strip() + "\n").encode("utf-8"), "text/plain"
        return "conversation.pdf", cls._render_pdf(source), "application/pdf"

    @classmethod
    def render_document(
        cls, filename: str, mime_type: str, chunks: list[dict[str, str]], requested: str,
    ) -> tuple[str, bytes, str]:
        export_format = cls.normalize_format(requested)
        if export_format not in {"md", "txt"}:
            raise ValueError("Saved document export supports md or txt.")
        if not chunks:
            raise ValueError("This saved document has no extracted text to export.")
        title = Path(filename).name[:255] or "Document"
        blocks = [f"# {title}", f"MIME type: {mime_type}", ""]
        plain_blocks = [title, f"MIME type: {mime_type}", ""]
        for chunk in chunks:
            source = chunk.get("source", "Document")
            content = chunk.get("content", "").strip()
            if content:
                blocks.extend([f"## {source}", "", content, ""])
                plain_blocks.extend([str(source), "", content, ""])
        source_text = "\n".join(blocks).strip() + "\n"
        if len(source_text) > cls.MAX_EXPORT_CHARS:
            raise ValueError("This document is too large to export as one report.")
        stem = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(title).stem).strip("._-")[:80] or "document"
        if export_format == "md":
            return f"{stem}.md", source_text.encode("utf-8"), "text/markdown"
        plain = "\n".join(plain_blocks).strip() + "\n"
        return f"{stem}.txt", plain.encode("utf-8"), "text/plain"

    @staticmethod
    def _font_paths() -> tuple[Path, Path] | None:
        candidates = (
            (Path("/usr/share/fonts/truetype/noto/NotoSansBengali-Regular.ttf"),
             Path("/usr/share/fonts/truetype/noto/NotoSansBengali-Bold.ttf")),
            (Path("/usr/share/fonts/opentype/noto/NotoSansBengali-Regular.ttf"),
             Path("/usr/share/fonts/opentype/noto/NotoSansBengali-Bold.ttf")),
            (Path("C:/Windows/Fonts/Nirmala.ttf"), Path("C:/Windows/Fonts/NirmalaB.ttf")),
        )
        for regular, bold in candidates:
            if regular.is_file():
                return regular, bold if bold.is_file() else regular
        return None

    @staticmethod
    def _markup(text: str, bengali_font: str | None) -> str:
        """Escaped text with Bengali runs switched to the Bengali font; Latin keeps its own."""
        if bengali_font is None:
            return text
        return BENGALI_RUN.sub(lambda match: f'<font name="{bengali_font}">{match.group(0)}</font>', text)

    @classmethod
    def _render_pdf(cls, markdown: str) -> bytes:
        fonts = cls._font_paths()
        if fonts is None and re.search(r"[\u0980-\u09ff]", markdown):
            raise RuntimeError(
                "A Bengali TrueType font is required for Bangla PDF exports. Install fonts-noto-core."
            )
        regular_name, bold_name = "Helvetica", "Helvetica-Bold"
        bengali_regular = bengali_bold = None
        if fonts is not None:
            regular_path, bold_path = fonts
            pdfmetrics.registerFont(TTFont("OmegaExport", str(regular_path), shapable=True))
            pdfmetrics.registerFont(TTFont("OmegaExportBold", str(bold_path), shapable=True))
            bengali_regular, bengali_bold = "OmegaExport", "OmegaExportBold"

        styles = getSampleStyleSheet()
        title = ParagraphStyle(
            "ExportTitle", parent=styles["Title"], fontName=bold_name, fontSize=18,
            leading=24, textColor=colors.HexColor("#183153"), spaceAfter=10,
        )
        heading = ParagraphStyle(
            "ExportHeading", parent=styles["Heading2"], fontName=bold_name, fontSize=13,
            leading=18, textColor=colors.HexColor("#24527a"), spaceBefore=12, spaceAfter=5,
        )
        body = ParagraphStyle(
            "ExportBody", parent=styles["BodyText"], fontName=regular_name, fontSize=10,
            leading=15, shaping=1, spaceAfter=8, wordWrap="CJK",
        )
        code = ParagraphStyle(
            "ExportCode", parent=body, fontName=regular_name, fontSize=8.5, leading=12,
            backColor=colors.HexColor("#f1f4f8"), borderPadding=6, leftIndent=5,
        )
        story: list[Any] = []
        paragraph: list[str] = []
        code_lines: list[str] = []
        in_code = False

        def flush_paragraph() -> None:
            if paragraph:
                lines = [cls._markup(escape(line), bengali_regular) for line in paragraph]
                story.append(Paragraph("<br/>".join(lines), body))
                paragraph.clear()

        def flush_code() -> None:
            if code_lines:
                rendered = [
                    cls._markup(_preserve_indentation(escape(line)), bengali_regular)
                    for line in code_lines
                ]
                story.append(Paragraph("<br/>".join(rendered), code))
                story.append(Spacer(1, 8))
                code_lines.clear()

        for line in markdown.splitlines():
            if line.strip().startswith("```"):
                if in_code:
                    flush_code()
                else:
                    flush_paragraph()
                in_code = not in_code
                continue
            if in_code:
                code_lines.append(line)
                continue
            if line.startswith("# "):
                flush_paragraph()
                story.append(Paragraph(cls._markup(escape(line[2:].strip()), bengali_bold), title))
            elif line.startswith("## "):
                flush_paragraph()
                story.append(Paragraph(cls._markup(escape(line[3:].strip()), bengali_bold), heading))
            elif not line.strip():
                flush_paragraph()
            else:
                exported = re.fullmatch(r"_(Exported .+)_", line)
                paragraph.append(exported.group(1) if exported else line)
        flush_paragraph()
        flush_code()  # an unterminated fence still exports what it contains
        output = io.BytesIO()
        document = SimpleDocTemplate(
            output, pagesize=A4, rightMargin=48, leftMargin=48, topMargin=48, bottomMargin=48,
            title="Conversation export", author="Enan Ultra",
        )
        document.build(story)
        return output.getvalue()
