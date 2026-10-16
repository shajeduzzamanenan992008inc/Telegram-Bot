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
        for item in history:
            role = "You" if item.get("role") == "user" else "AI"
            body = str(item.get("content") or "").strip()
            if not body:
                continue
            markdown.extend([f"## {role}", "", body, ""])
        source = "\n".join(markdown).strip() + "\n"
        if len(source) > cls.MAX_EXPORT_CHARS:
            raise ValueError("This conversation is too large to export as one document.")
        if export_format == "md":
            return "conversation.md", source.encode("utf-8"), "text/markdown"
        if export_format == "txt":
            plain = re.sub(r"(?m)^#{1,6}\s*", "", source)
            plain = re.sub(r"(?m)^_Exported (.+)_$", r"Exported \1", plain)
            return "conversation.txt", plain.encode("utf-8"), "text/plain"
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
        for chunk in chunks:
            source = chunk.get("source", "Document")
            content = chunk.get("content", "").strip()
            if content:
                blocks.extend([f"## {source}", "", content, ""])
        source_text = "\n".join(blocks).strip() + "\n"
        if len(source_text) > cls.MAX_EXPORT_CHARS:
            raise ValueError("This document is too large to export as one report.")
        stem = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(title).stem).strip("._-")[:80] or "document"
        if export_format == "md":
            return f"{stem}.md", source_text.encode("utf-8"), "text/markdown"
        plain = re.sub(r"(?m)^#{1,6}\s*", "", source_text)
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

    @classmethod
    def _render_pdf(cls, markdown: str) -> bytes:
        fonts = cls._font_paths()
        if fonts is None and re.search(r"[\u0980-\u09ff]", markdown):
            raise RuntimeError(
                "A Bengali TrueType font is required for Bangla PDF exports. Install fonts-noto-core."
            )
        regular_name, bold_name = "Helvetica", "Helvetica-Bold"
        if fonts is not None:
            regular_path, bold_path = fonts
            pdfmetrics.registerFont(TTFont("OmegaExport", str(regular_path), shapable=True))
            pdfmetrics.registerFont(TTFont("OmegaExportBold", str(bold_path), shapable=True))
            regular_name, bold_name = "OmegaExport", "OmegaExportBold"

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
        in_code = False
        code_lines: list[str] = []
        for block in markdown.split("\n\n"):
            if block.lstrip().startswith("```"):
                if in_code:
                    story.append(Paragraph("<br/>".join(escape(line) for line in code_lines), code))
                    story.append(Spacer(1, 8))
                    code_lines = []
                    in_code = False
                else:
                    in_code = True
                continue
            if in_code:
                code_lines.extend(block.splitlines())
                continue
            if not block.strip():
                continue
            lines = block.splitlines()
            if lines[0].startswith("# "):
                story.append(Paragraph(escape(lines[0][2:].strip()), title))
                if len(lines) > 1:
                    story.append(Paragraph("<br/>".join(escape(line) for line in lines[1:]), body))
            elif lines[0].startswith("## "):
                story.append(Paragraph(escape(lines[0][3:].strip()), heading))
                if len(lines) > 1:
                    story.append(Paragraph("<br/>".join(escape(line) for line in lines[1:]), body))
            else:
                story.append(Paragraph("<br/>".join(escape(line) for line in lines), body))
        if in_code and code_lines:
            story.append(Paragraph("<br/>".join(escape(line) for line in code_lines), code))
        output = io.BytesIO()
        document = SimpleDocTemplate(
            output, pagesize=A4, rightMargin=48, leftMargin=48, topMargin=48, bottomMargin=48,
            title="Conversation export", author="Enan Ultra",
        )
        document.build(story)
        return output.getvalue()
