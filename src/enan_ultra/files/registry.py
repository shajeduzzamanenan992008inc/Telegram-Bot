"""Signature-aware file intake with bounded, pluggable parsers."""

from __future__ import annotations

import asyncio
import bz2
from contextvars import ContextVar
import gzip
import io
from itertools import islice
import logging
import lzma
import mimetypes
import re
import subprocess
import tarfile
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Callable

from PIL import Image, UnidentifiedImageError
from docx import Document
from openpyxl import load_workbook
from pptx import Presentation
from pypdf import PdfReader

from enan_ultra.config import Settings
from enan_ultra.files.base import FileAdapter
from enan_ultra.models import ContentChunk, ExtractedContent

logger = logging.getLogger(__name__)


TEXT_EXTENSIONS = {
    ".txt", ".md", ".csv", ".tsv", ".json", ".jsonl", ".py", ".log", ".ini", ".cfg",
    ".toml", ".yaml", ".yml", ".xml", ".html", ".htm", ".css", ".js", ".ts", ".tsx",
    ".jsx", ".sql", ".sh", ".ps1", ".bat", ".java", ".kt", ".c", ".h", ".cpp", ".hpp",
    ".cs", ".go", ".rs", ".php", ".rb", ".swift", ".r", ".ipynb", ".diff", ".patch",
    ".env", ".properties", ".graphql", ".proto", ".dockerfile", ".makefile", ".rst",
}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff", ".gif"}
ARCHIVE_EXTENSIONS = {".zip", ".tar", ".tgz", ".tar.gz", ".gz", ".bz2", ".xz"}
AUDIO_EXTENSIONS = {".ogg", ".opus", ".mp3", ".wav", ".m4a", ".aac", ".flac", ".wma"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}


def _safe_text(data: bytes) -> str:
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16", errors="replace")
    if data.startswith(b"\xef\xbb\xbf"):
        return data.decode("utf-8-sig", errors="replace")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("utf-8", errors="replace")


def _is_probably_text(data: bytes) -> bool:
    if not data:
        return True
    sample = data[:4096]
    if b"\x00" in sample:
        return False
    decoded = sample.decode("utf-8", errors="replace")
    return decoded.count("\ufffd") / max(1, len(decoded)) < 0.05


def _chunk_text(text: str, source: str, max_chars: int = 6000) -> list[ContentChunk]:
    text = text.strip()
    if not text:
        return []
    chunks: list[ContentChunk] = []
    start = 0
    while start < len(text):
        end = min(start + max_chars, len(text))
        if end < len(text):
            boundary = text.rfind("\n", start + max_chars // 2, end)
            if boundary > start:
                end = boundary
        part = text[start:end].strip()
        if part:
            chunks.append(ContentChunk(part, source if start == 0 else f"{source} (part {len(chunks) + 1})"))
        start = max(end, start + 1)
    return chunks


def _chunk_lines(text: str, source: str, max_chars: int = 6000) -> list[ContentChunk]:
    lines = text.splitlines(keepends=True)
    chunks: list[ContentChunk] = []
    block: list[str] = []
    size = 0
    first_line = 1
    for number, line in enumerate(lines, start=1):
        if block and size + len(line) > max_chars:
            chunks.append(ContentChunk("".join(block).strip(), f"{source} lines {first_line}-{number - 1}"))
            block, size, first_line = [], 0, number
        block.append(line)
        size += len(line)
    if block:
        chunks.append(ContentChunk("".join(block).strip(), f"{source} lines {first_line}-{len(lines)}"))
    return [chunk for chunk in chunks if chunk.text]


class BaseAdapter:
    name = "base"

    def detect(self, filename: str, mime_type: str, data: bytes) -> bool:
        raise NotImplementedError

    async def extract(
        self, filename: str, mime_type: str, data: bytes, settings: Settings, depth: int = 0
    ) -> list[ContentChunk]:
        chunks, _ = await self.extract_with_media(filename, mime_type, data, settings, depth)
        return chunks

    async def extract_with_media(
        self, filename: str, mime_type: str, data: bytes, settings: Settings, depth: int = 0
    ) -> tuple[list[ContentChunk], tuple[bytes, ...]]:
        raise NotImplementedError


class TextAdapter(BaseAdapter):
    name = "text-code"

    def detect(self, filename: str, mime_type: str, data: bytes) -> bool:
        ext = Path(filename.lower()).suffix
        if ext in TEXT_EXTENSIONS:
            return True
        return (mime_type.startswith("text/") or mime_type in {"application/json", "application/xml"}) and _is_probably_text(data)

    async def extract_with_media(
        self, filename: str, mime_type: str, data: bytes, settings: Settings, depth: int = 0
    ) -> tuple[list[ContentChunk], tuple[bytes, ...]]:
        text = _safe_text(data[: settings.max_input_chars * 8])
        return _chunk_lines(text, filename), ()


class PdfAdapter(BaseAdapter):
    name = "pdf"

    def detect(self, filename: str, mime_type: str, data: bytes) -> bool:
        return data.startswith(b"%PDF-") or Path(filename.lower()).suffix == ".pdf"

    async def extract_with_media(
        self, filename: str, mime_type: str, data: bytes, settings: Settings, depth: int = 0
    ) -> tuple[list[ContentChunk], tuple[bytes, ...]]:
        return await asyncio.to_thread(self._extract_sync, data, settings)

    @staticmethod
    def _extract_sync(data: bytes, settings: Settings) -> tuple[list[ContentChunk], tuple[bytes, ...]]:
        try:
            reader = PdfReader(io.BytesIO(data), strict=False)
            if reader.is_encrypted:
                return [ContentChunk("This PDF is encrypted and could not be read.", "PDF")], ()
            chunks: list[ContentChunk] = []
            page_count = min(len(reader.pages), settings.max_document_pages)
            used_chars = 0
            for index, page in enumerate(reader.pages[:page_count], start=1):
                if used_chars >= settings.max_input_chars * 8:
                    break
                text = page.extract_text(extraction_mode="layout") or page.extract_text() or ""
                if text.strip():
                    text = text[:max(0, settings.max_input_chars * 8 - used_chars)]
                    used_chars += len(text)
                    chunks.extend(_chunk_text(text, f"page {index}"))
            if chunks:
                return chunks, ()
            # Scanned PDFs: only attempt a small, bounded OCR sample. No shell is invoked.
            if shutil_which("pdftoppm") and shutil_which("tesseract"):
                with tempfile.TemporaryDirectory(prefix="enan-pdf-") as temp:
                    pdf_path = Path(temp) / "input.pdf"
                    pdf_path.write_bytes(data)
                    out_prefix = Path(temp) / "page"
                    rendered = subprocess.run(
                        ["pdftoppm", "-f", "1", "-l", str(min(page_count, 10)), "-png", "-scale-to", "2048", str(pdf_path), str(out_prefix)],
                        capture_output=True,
                        timeout=40,
                        check=False,
                    )
                    if rendered.returncode == 0:
                        for image_path in sorted(Path(temp).glob("page-*.png")):
                            match = re.search(r"-(\d+)\.png$", image_path.name)
                            page_no = int(match.group(1)) if match else 1
                            ocr = subprocess.run(
                                ["tesseract", str(image_path), "stdout", "-l", "eng+ben"],
                                capture_output=True,
                                text=True,
                                timeout=20,
                                check=False,
                            )
                            if ocr.returncode != 0:
                                ocr = subprocess.run(
                                    ["tesseract", str(image_path), "stdout", "-l", "eng"],
                                    capture_output=True,
                                    text=True,
                                    timeout=20,
                                    check=False,
                                )
                            chunks.extend(_chunk_text(ocr.stdout or "", f"OCR page {page_no}"))
            if chunks:
                return chunks, ()
            return [], ()
        except Exception as exc:
            return [ContentChunk(f"PDF parse failed safely: {type(exc).__name__}", "PDF")], ()


def _pptx_shape_texts(shape: object, depth: int = 0) -> list[str]:
    """Text of one slide shape: text frames, table cells and (bounded) grouped shapes."""
    texts: list[str] = []
    if getattr(shape, "has_text_frame", False):
        text = shape.text_frame.text.strip()
        if text:
            texts.append(text)
    if getattr(shape, "has_table", False):
        for row in shape.table.rows:
            cells = [cell.text.strip() for cell in row.cells]
            if any(cells):
                texts.append(" | ".join(cells))
    children = getattr(shape, "shapes", None)
    if children is not None and depth < 5:
        for child in children:
            texts.extend(_pptx_shape_texts(child, depth + 1))
    return texts


def _pptx_slide_texts(slide: object) -> list[str]:
    texts: list[str] = []
    for shape in slide.shapes:
        texts.extend(_pptx_shape_texts(shape))
    if getattr(slide, "has_notes_slide", False):
        frame = slide.notes_slide.notes_text_frame
        notes = frame.text.strip() if frame is not None else ""
        if notes:
            texts.append(f"Speaker notes: {notes}")
    return texts


class OfficeAdapter(BaseAdapter):
    name = "office"

    def __init__(self, registry: "FileRegistry") -> None:
        self.registry = registry

    def detect(self, filename: str, mime_type: str, data: bytes) -> bool:
        ext = Path(filename.lower()).suffix
        return ext in {".docx", ".xlsx", ".pptx", ".epub"} or _office_extension(data) is not None

    async def extract_with_media(
        self, filename: str, mime_type: str, data: bytes, settings: Settings, depth: int = 0
    ) -> tuple[list[ContentChunk], tuple[bytes, ...]]:
        entries, expanded = await asyncio.to_thread(_zip_expanded_size, data)
        if entries < 0 or entries > settings.max_archive_files or expanded > settings.max_archive_expanded_bytes:
            return [ContentChunk("Office package exceeds the configured safe expansion limits.", filename, "warning")], ()
        if depth > 0 and not self.registry.consume_archive_totals(entries, expanded, settings):
            return [ContentChunk("Office package exceeds the cumulative archive limits.", filename, "warning")], ()
        return await asyncio.to_thread(self._extract_sync, filename, data, settings.max_input_chars)

    @staticmethod
    def _extract_sync(filename: str, data: bytes, max_chars: int) -> tuple[list[ContentChunk], tuple[bytes, ...]]:
        ext = _office_extension(data) or Path(filename.lower()).suffix
        stream = io.BytesIO(data)
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as package:
                entries = package.infolist()
                if len(entries) > 200 or sum(item.file_size for item in entries) > 50 * 1024 * 1024:
                    return [ContentChunk("Office package exceeds the safe expansion limits.", filename, "warning")], ()
            if ext == ".docx":
                doc = Document(stream)
                parts: list[str] = []
                used = 0
                for index, paragraph in enumerate(doc.paragraphs, start=1):
                    if used >= max_chars * 8:
                        break
                    text = paragraph.text.strip()
                    if text and used < max_chars * 8:
                        text = text[:max(0, max_chars * 8 - used)]
                        parts.append(f"Paragraph {index}: {text}")
                        used += len(text)
                for ti, table in enumerate(doc.tables, start=1):
                    if used >= max_chars * 8:
                        break
                    for row_number, row in enumerate(table.rows[:5000], start=1):
                        remaining = max_chars * 8 - used
                        if remaining <= 0:
                            break
                        row_text = f"Table {ti} row {row_number}: " + " | ".join(cell.text[:remaining] for cell in row.cells)
                        row_text = row_text[:remaining]
                        parts.append(row_text)
                        used += len(row_text)
                return _chunk_text("\n".join(parts)[:max_chars * 8], "document"), ()
            if ext == ".xlsx":
                book = load_workbook(stream, read_only=True, data_only=True)
                chunks = []
                total_chars = 0
                for sheet in book.worksheets:
                    rows = []
                    for row_number, row in enumerate(sheet.iter_rows(values_only=True), start=1):
                        if row_number > 5000 or total_chars >= max_chars * 8:
                            break
                        values = [str(value) if value is not None else "" for value in row]
                        if any(values):
                            row_text = (f"row {row_number}: " + " | ".join(values))[:max(0, max_chars * 8 - total_chars)]
                            rows.append(row_text)
                            total_chars += len(row_text)
                    chunks.extend(_chunk_text("\n".join(rows)[:max_chars * 8], f"sheet {sheet.title}"))
                    if total_chars >= max_chars * 8:
                        break
                book.close()
                return chunks, ()
            if ext == ".pptx":
                presentation = Presentation(stream)
                chunks = []
                used_chars = 0
                # ``presentation.slides`` is not sliceable (slides[:n] raises AttributeError).
                for index, slide in enumerate(islice(presentation.slides, 500), start=1):
                    if used_chars >= max_chars * 8:
                        break
                    texts = _pptx_slide_texts(slide)
                    slide_text = "\n".join(texts)[:max(0, max_chars * 8 - used_chars)]
                    used_chars += len(slide_text)
                    chunks.extend(_chunk_text(slide_text, f"slide {index}"))
                return chunks, ()
            if ext == ".epub":
                with zipfile.ZipFile(stream) as archive:
                    parts = []
                    used_chars = 0
                    for name in archive.namelist():
                        if used_chars >= max_chars * 8:
                            break
                        if name.lower().endswith((".xhtml", ".html", ".htm")) and ".." not in PurePosixPath(name).parts:
                            raw = archive.read(name)[:max_chars * 8 - used_chars]
                            body = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", _safe_text(raw))
                            text = re.sub(r"(?s)<[^>]+>", " ", body)
                            text = re.sub(r"\s+", " ", text).strip()
                            if text:
                                parts.extend(_chunk_text(text, name))
                                used_chars += len(text)
                return parts, ()
        except Exception as exc:
            return [ContentChunk(f"Office document parse failed safely: {type(exc).__name__}", filename)], ()
        return [], ()


class ImageAdapter(BaseAdapter):
    name = "image"

    def detect(self, filename: str, mime_type: str, data: bytes) -> bool:
        is_webp = data.startswith(b"RIFF") and data[8:12] == b"WEBP"
        return (
            data.startswith((b"\x89PNG", b"\xff\xd8\xff")) or is_webp
            or mime_type.startswith("image/") or Path(filename.lower()).suffix in IMAGE_EXTENSIONS
        )

    async def extract_with_media(
        self, filename: str, mime_type: str, data: bytes, settings: Settings, depth: int = 0
    ) -> tuple[list[ContentChunk], tuple[bytes, ...]]:
        chunks: list[ContentChunk] = []
        try:
            image = Image.open(io.BytesIO(data))
            if image.width * image.height > settings.max_image_pixels:
                return [ContentChunk("Image exceeds the configured pixel limit.", "image", "warning")], ()
            image.verify()
            image = Image.open(io.BytesIO(data)).convert("RGB")
            image.thumbnail((2048, 2048))
            normalized = b""
            for quality, size in ((86, 2048), (72, 1600), (58, 1280), (42, 960)):
                candidate = image.copy()
                candidate.thumbnail((size, size))
                out = io.BytesIO()
                candidate.save(out, format="JPEG", quality=quality, optimize=True)
                if len(out.getvalue()) <= settings.max_image_bytes:
                    normalized = out.getvalue()
                    break
            if not normalized:
                return [ContentChunk("Image exceeds the safe normalized image size.", "image", "warning")], ()
            chunks.extend(await asyncio.to_thread(self._decode_codes_and_ocr, normalized))
            return chunks, (normalized,)
        except (UnidentifiedImageError, OSError):
            return [], ()

    @staticmethod
    def _decode_codes_and_ocr(data: bytes) -> list[ContentChunk]:
        chunks: list[ContentChunk] = []
        try:
            import cv2
            import numpy as np

            image = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
            if image is not None:
                detector = cv2.QRCodeDetector()
                ok, decoded, _points, _ = detector.detectAndDecodeMulti(image)
                values = list(decoded) if ok else []
                if not values:
                    value, _points, _ = detector.detectAndDecode(image)
                    values = [value] if value else []
                for index, value in enumerate(values, start=1):
                    chunks.append(ContentChunk(value, f"QR code {index}", "qr"))
                try:
                    barcode = cv2.barcode_BarcodeDetector()
                    result = barcode.detectAndDecode(image)
                    if isinstance(result, tuple) and len(result) >= 2:
                        for index, value in enumerate(result[1] or [], start=1):
                            if value:
                                chunks.append(ContentChunk(str(value), f"barcode {index}", "barcode"))
                except (AttributeError, cv2.error):
                    pass
        except Exception:
            pass
        if shutil_which("tesseract"):
            try:
                with tempfile.TemporaryDirectory(prefix="enan-ocr-") as temp:
                    path = Path(temp) / "image.jpg"
                    path.write_bytes(data)
                    result = subprocess.run(
                        ["tesseract", str(path), "stdout", "-l", "eng+ben"],
                        capture_output=True,
                        text=True,
                        timeout=20,
                        check=False,
                    )
                    text = (result.stdout or "").strip()
                    if text:
                        chunks.extend(_chunk_text(text, "OCR image"))
            except (OSError, subprocess.TimeoutExpired):
                pass
        return chunks


class AudioAdapter(BaseAdapter):
    name = "audio"

    def __init__(self, router: object | None, remote_media_allowed: ContextVar[bool]) -> None:
        self.router = router
        self.remote_media_allowed = remote_media_allowed

    def detect(self, filename: str, mime_type: str, data: bytes) -> bool:
        suffix = Path(filename.lower()).suffix
        is_wave = data.startswith(b"RIFF") and data[8:12] == b"WAVE"
        signature = (
            data.startswith((b"OggS", b"fLaC", b"ID3")) or is_wave
            or data[:2] in {b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"}
        )
        return signature or mime_type.startswith("audio/") or suffix in AUDIO_EXTENSIONS

    async def extract_with_media(
        self, filename: str, mime_type: str, data: bytes, settings: Settings, depth: int = 0
    ) -> tuple[list[ContentChunk], tuple[bytes, ...]]:
        if not self.remote_media_allowed.get():
            return [ContentChunk("Audio transcription was skipped in local-only privacy mode.", "audio", "warning")], ()
        if not settings.voice_input_enabled or self.router is None:
            return [], ()
        try:
            from enan_ultra.services.media import MediaProcessor

            transcript = await MediaProcessor(settings, self.router).transcribe(data, Path(filename).name[:120], mime_type)
            if not transcript:
                return [], ()
            return [ContentChunk(transcript, "audio transcript", "audio")], ()
        except Exception as exc:
            return [ContentChunk(f"Audio transcription is unavailable ({type(exc).__name__}).", "audio", "warning")], ()


class VideoAdapter(BaseAdapter):
    name = "video"

    def __init__(self, router: object | None, remote_media_allowed: ContextVar[bool]) -> None:
        self.router = router
        self.remote_media_allowed = remote_media_allowed

    def detect(self, filename: str, mime_type: str, data: bytes) -> bool:
        suffix = Path(filename.lower()).suffix
        is_avi = data.startswith(b"RIFF") and data[8:12] == b"AVI "
        signature = (
            len(data) >= 12 and data[4:8] == b"ftyp" or data.startswith(b"\x1aE\xdf\xa3") or is_avi
        )
        return signature or mime_type.startswith("video/") or suffix in VIDEO_EXTENSIONS

    async def extract_with_media(
        self, filename: str, mime_type: str, data: bytes, settings: Settings, depth: int = 0
    ) -> tuple[list[ContentChunk], tuple[bytes, ...]]:
        if self.router is None:
            return [], ()
        from enan_ultra.services.media import MediaProcessor

        try:
            result = await MediaProcessor(settings, self.router).video(data, filename)
            chunks: list[ContentChunk] = []
            if result.audio and settings.voice_input_enabled and self.remote_media_allowed.get():
                transcript = await self.router.transcribe(result.audio, "video-audio.mp3", "audio/mpeg")
                if transcript:
                    chunks.append(ContentChunk(transcript, "video audio transcript", "audio"))
            elif result.audio and not self.remote_media_allowed.get():
                chunks.append(ContentChunk("Video audio transcription was skipped in local-only privacy mode.", "video audio", "warning"))
            interval = result.duration / max(1, len(result.frames))
            for index, _frame in enumerate(result.frames):
                chunks.append(ContentChunk(f"Sampled video frame at {index * interval:.1f}s.", "video frame", "image", {"timestamp_seconds": index * interval}))
            return chunks, result.frames
        except Exception as exc:
            return [ContentChunk(f"Video processing is unavailable ({type(exc).__name__}).", "video", "warning")], ()


class TextOnlyArchiveAdapter(BaseAdapter):
    """ZIP/TAR/GZIP reader that never extracts paths onto the filesystem."""

    name = "archive"

    def __init__(self, registry: "FileRegistry") -> None:
        self.registry = registry

    def detect(self, filename: str, mime_type: str, data: bytes) -> bool:
        ext = Path(filename.lower()).suffix
        return data.startswith(b"PK\x03\x04") or data.startswith(b"\x1f\x8b") or ext in ARCHIVE_EXTENSIONS or tarfile.is_tarfile(io.BytesIO(data))

    async def extract_with_media(
        self, filename: str, mime_type: str, data: bytes, settings: Settings, depth: int = 0
    ) -> tuple[list[ContentChunk], tuple[bytes, ...]]:
        if depth >= settings.max_archive_depth:
            return [], ()
        chunks: list[ContentChunk] = []
        images: list[bytes] = []
        expanded = 0
        files_seen = 0
        limit_hit = False
        try:
            members: list[tuple[str, int, Callable[[], bytes]]] = []
            stream = io.BytesIO(data)
            if zipfile.is_zipfile(stream):
                with zipfile.ZipFile(stream) as archive:
                    for info in archive.infolist():
                        if info.is_dir():
                            continue
                        if _unsafe_archive_path(info.filename) or info.flag_bits & 0x1:
                            continue
                        members.append((info.filename, info.file_size, lambda i=info: _read_zip_member(archive, i, settings.max_archive_expanded_bytes)))
                    for member_name, size, reader in members:
                        if not self.registry.consume_archive_member(size, settings):
                            limit_hit = True
                            break
                        expanded += size
                        files_seen += 1
                        raw = reader()
                        sub, sub_images = await self.registry.extract_bytes(
                            member_name, mimetypes.guess_type(member_name)[0] or "application/octet-stream", raw, settings, depth + 1
                        )
                        chunks.extend(ContentChunk(item.text, f"{filename}!/{member_name} → {item.source}", item.modality, item.metadata) for item in sub)
                        images.extend(sub_images[:max(0, 4 - len(images))])
            else:
                stream = io.BytesIO(data)
                try:
                    with tarfile.open(fileobj=stream, mode="r:*") as archive:
                        for info in archive:
                            if not info.isfile() or _unsafe_archive_path(info.name):
                                continue
                            if not self.registry.consume_archive_member(info.size, settings):
                                limit_hit = True
                                break
                            expanded += info.size
                            files_seen += 1
                            extracted = archive.extractfile(info)
                            if extracted:
                                raw = extracted.read(min(settings.max_archive_expanded_bytes, info.size + 1))
                                sub, sub_images = await self.registry.extract_bytes(
                                    info.name, mimetypes.guess_type(info.name)[0] or "application/octet-stream", raw, settings, depth + 1
                                )
                                chunks.extend(ContentChunk(item.text, f"{filename}!/{info.name} → {item.source}", item.modality, item.metadata) for item in sub)
                                images.extend(sub_images[:max(0, 4 - len(images))])
                except (tarfile.TarError, OSError):
                    # Single-file gzip/bzip2 stream.
                    remaining = self.registry.remaining_archive_bytes(settings)
                    if remaining <= 0:
                        return [ContentChunk("Compressed file exceeds the configured expansion/file-count limit.", filename, "warning")], ()
                    if data.startswith(b"\x1f\x8b"):
                        with gzip.GzipFile(fileobj=io.BytesIO(data)) as compressed:
                            raw = compressed.read(remaining + 1)
                    elif filename.lower().endswith(".bz2"):
                        raw = bz2.BZ2Decompressor().decompress(data, max_length=remaining + 1)
                    elif filename.lower().endswith((".xz", ".lzma")):
                        with lzma.LZMAFile(io.BytesIO(data)) as compressed:
                            raw = compressed.read(remaining + 1)
                    else:
                        raw = b""
                    if len(raw) > remaining or not self.registry.consume_archive_member(len(raw), settings):
                        return [ContentChunk("Compressed file exceeds the configured expansion limit.", filename, "warning")], ()
                    if raw:
                        inner_name = Path(filename).stem
                        sub, sub_images = await self.registry.extract_bytes(
                            inner_name, mimetypes.guess_type(inner_name)[0] or "text/plain", raw, settings, depth + 1
                        )
                        chunks.extend(ContentChunk(item.text, f"{filename} → {item.source}", item.modality, item.metadata) for item in sub)
                        images.extend(sub_images[:max(0, 4 - len(images))])
            if limit_hit:
                chunks.append(ContentChunk("Archive processing stopped at the configured expansion/file-count limit.", filename, "warning"))
            return chunks, tuple(images)
        except (zipfile.BadZipFile, OSError, EOFError, ValueError):
            return [], ()


class FileRegistry:
    def __init__(self, router: object | None = None) -> None:
        self.router = router
        self._archive_budget: ContextVar[dict[str, int] | None] = ContextVar("archive_budget", default=None)
        self._remote_media_allowed: ContextVar[bool] = ContextVar("remote_media_allowed", default=True)
        self.adapters: list[FileAdapter] = []
        # Adapters are ordered: format signatures first, generic text last.
        for adapter in (
            PdfAdapter(), OfficeAdapter(self), ImageAdapter(), AudioAdapter(router, self._remote_media_allowed),
            VideoAdapter(router, self._remote_media_allowed),
            TextOnlyArchiveAdapter(self), TextAdapter(),
        ):
            self.register(adapter)

    def register(self, adapter: FileAdapter) -> None:
        self.adapters.append(adapter)

    async def extract_bytes(
        self, filename: str, mime_type: str, data: bytes, settings: Settings, depth: int = 0
    ) -> tuple[list[ContentChunk], tuple[bytes, ...]]:
        if len(data) > settings.max_upload_bytes and depth == 0:
            raise ValueError("File exceeds MAX_UPLOAD_BYTES")
        if depth > 0 and (
            mime_type.startswith(("audio/", "video/"))
            or Path(filename.lower()).suffix in AUDIO_EXTENSIONS | VIDEO_EXTENSIONS
        ):
            return [ContentChunk("Nested audio/video is not sent for remote transcription automatically.", filename, "warning")], ()
        token = None
        if self._archive_budget.get() is None:
            token = self._archive_budget.set({"expanded": 0, "files": 0})
        try:
            for adapter in self.adapters:
                try:
                    matched = adapter.detect(filename, mime_type, data)
                except Exception as exc:
                    logger.warning(
                        "File type detection failed for adapter %s (%s)",
                        adapter.name, type(exc).__name__,
                    )
                    continue
                if not matched:
                    continue
                try:
                    if isinstance(adapter, BaseAdapter):
                        return await adapter.extract_with_media(filename, mime_type, data, settings, depth)
                    return await adapter.extract(filename, mime_type, data, settings, depth), ()
                except Exception as exc:
                    logger.warning(
                        "File parsing failed for adapter %s (%s)",
                        adapter.name, type(exc).__name__,
                    )
                    return [
                        ContentChunk(
                            f"The {adapter.name} parser failed safely ({type(exc).__name__}).",
                            filename, "warning",
                        )
                    ], ()
            if _is_probably_text(data):
                return _chunk_text(_safe_text(data[: settings.max_input_chars * 8]), filename), ()
            return [], ()
        finally:
            if token is not None:
                self._archive_budget.reset(token)

    def consume_archive_member(self, expanded_bytes: int, settings: Settings) -> bool:
        """Apply a single cumulative expanded-size and member-count budget across nested archives."""
        budget = self._archive_budget.get()
        if budget is None:
            return False
        if budget["files"] + 1 > settings.max_archive_files or budget["expanded"] + expanded_bytes > settings.max_archive_expanded_bytes:
            return False
        budget["files"] += 1
        budget["expanded"] += max(0, expanded_bytes)
        return True

    def consume_archive_totals(self, files: int, expanded_bytes: int, settings: Settings) -> bool:
        budget = self._archive_budget.get()
        if budget is None or budget["files"] + files > settings.max_archive_files:
            return False
        if budget["expanded"] + expanded_bytes > settings.max_archive_expanded_bytes:
            return False
        budget["files"] += files
        budget["expanded"] += expanded_bytes
        return True

    def remaining_archive_bytes(self, settings: Settings) -> int:
        budget = self._archive_budget.get()
        if budget is None:
            return 0
        if budget["files"] >= settings.max_archive_files:
            return 0
        return max(0, settings.max_archive_expanded_bytes - budget["expanded"])

    async def extract(
        self, filename: str, mime_type: str, data: bytes, settings: Settings, *,
        allow_remote_media: bool = True,
    ) -> ExtractedContent:
        safe_name = Path(filename.replace("\\", "/")).name[:255] or "upload.bin"
        detected_mime = _sniff_mime(data, mime_type)
        remote_token = self._remote_media_allowed.set(allow_remote_media)
        try:
            chunks, images = await self.extract_bytes(safe_name, detected_mime, data, settings)
        finally:
            self._remote_media_allowed.reset(remote_token)
        warnings = [chunk.text for chunk in chunks if chunk.modality == "warning"]
        readable_chunks = tuple(chunk for chunk in chunks if chunk.modality != "warning")
        if not readable_chunks and not images:
            if not warnings:
                warnings.append("No local parser recognized this file. It can be sent to a model with file-input support, if available.")
        return ExtractedContent(safe_name, detected_mime, readable_chunks, images, tuple(warnings))


def _unsafe_archive_path(name: str) -> bool:
    path = PurePosixPath(name.replace("\\", "/"))
    return path.is_absolute() or ".." in path.parts or name.startswith(("/", "\\"))


def _read_zip_member(archive: zipfile.ZipFile, info: zipfile.ZipInfo, limit: int) -> bytes:
    with archive.open(info) as source:
        content = source.read(limit + 1)
    if len(content) > limit:
        raise ValueError("Archive member exceeds safe read limit")
    return content


def _zip_expanded_size(data: bytes) -> tuple[int, int]:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = [item for item in archive.infolist() if not item.is_dir()]
            return len(entries), sum(item.file_size for item in entries)
    except (OSError, zipfile.BadZipFile):
        return -1, -1


def _office_extension(data: bytes) -> str | None:
    if not data.startswith(b"PK"):
        return None
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = set(archive.namelist())
        if "word/document.xml" in names:
            return ".docx"
        if "xl/workbook.xml" in names:
            return ".xlsx"
        if "ppt/presentation.xml" in names:
            return ".pptx"
        if "META-INF/container.xml" in names:
            return ".epub"
    except (OSError, zipfile.BadZipFile):
        return None
    return None


def _sniff_mime(data: bytes, declared: str) -> str:
    if data.startswith(b"%PDF-"):
        return "application/pdf"
    if data.startswith(b"\x89PNG"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    if data.startswith(b"PK\x03\x04"):
        return declared or "application/zip"
    if data.startswith(b"\x1f\x8b"):
        return "application/gzip"
    if data.startswith(b"OggS"):
        return "audio/ogg"
    return declared or "application/octet-stream"


def shutil_which(program: str) -> str | None:
    """Small local wrapper avoids importing subprocess-discovered binaries elsewhere."""
    from shutil import which

    return which(program)
