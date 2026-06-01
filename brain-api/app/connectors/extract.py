"""Best-effort text extraction for SharePoint files. Returns None for unsupported
types or on any extraction error (the file is then skipped + counted by the runner)."""
from __future__ import annotations

import csv
import io
import logging
import re

logger = logging.getLogger(__name__)

_TEXT_EXT = {".txt", ".md", ".markdown", ".csv", ".log", ".json", ".yaml", ".yml"}
_HTML_EXT = {".html", ".htm"}
_DOCX_EXT = {".docx"}
_PDF_EXT = {".pdf"}
_SUPPORTED = _TEXT_EXT | _HTML_EXT | _DOCX_EXT | _PDF_EXT


def _ext(name: str) -> str:
    i = name.rfind(".")
    return name[i:].lower() if i >= 0 else ""


def is_supported(name: str, mime: str | None) -> bool:
    return _ext(name) in _SUPPORTED


def _strip_html(raw: str) -> str:
    raw = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", raw)
    text = re.sub(r"(?s)<[^>]+>", " ", raw)
    text = (text.replace("&nbsp;", " ").replace("&amp;", "&")
                .replace("&lt;", "<").replace("&gt;", ">"))
    return re.sub(r"\s+\n", "\n", re.sub(r"[ \t]+", " ", text)).strip()


def extract_text(data: bytes, mime: str | None, name: str) -> str | None:
    ext = _ext(name)
    try:
        if ext in _TEXT_EXT:
            if ext == ".csv":
                rows = list(csv.reader(io.StringIO(data.decode("utf-8", "replace"))))
                return "\n".join(",".join(r) for r in rows).strip()
            return data.decode("utf-8", "replace").strip()
        if ext in _HTML_EXT:
            return _strip_html(data.decode("utf-8", "replace"))
        if ext in _DOCX_EXT:
            import docx  # python-docx
            doc = docx.Document(io.BytesIO(data))
            return "\n".join(p.text for p in doc.paragraphs if p.text).strip()
        if ext in _PDF_EXT:
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(data))
            return "\n".join((pg.extract_text() or "") for pg in reader.pages).strip()
    except Exception as e:  # noqa: BLE001 — extraction is best-effort; skip on failure
        logger.warning("extract_text failed for %s (%s): %s", name, mime, e)
        return None
    return None
