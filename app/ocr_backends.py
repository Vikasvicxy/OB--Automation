"""OCR backend abstraction layer.

Provides a uniform interface over:
  - RapidOCR (default, lightweight, ONNX-based)
  - PaddleOCR (optional, heavier, better layout analysis)

The active backend is selected via the ``OCR_BACKEND`` environment
variable (default ``rapidocr``).  If PaddleOCR is requested but not
installed, the module falls back to RapidOCR.

All backends normalize their output into :class:`OCRLine` instances so
downstream parsers never depend on a single OCR library's raw response
format.

License: Apache 2.0 (consistent with both RapidOCR and PaddleOCR).
No third-party source code is copied; only official public APIs are used.
"""

from __future__ import annotations

import io
import logging
import os
import re
from typing import Optional

import numpy as np
from PIL import Image, ImageEnhance, ImageOps

from app.ocr_models import OCRLine

logger = logging.getLogger("teamhr.ocr.backends")

# ---------------------------------------------------------------------------
# Backend singleton
# ---------------------------------------------------------------------------

_backend = None
_backend_name: Optional[str] = None


def get_backend_name() -> str:
    return _backend_name or "rapidocr"


def _normalise_box(box) -> tuple[float, float, float, float]:
    """Convert RapidOCR / PaddleOCR box to (x1, y1, x2, y2).

    Both libraries return 4-corner boxes: [[x1,y1],[x2,y2],[x3,y3],[x4,y4]].
    We take the axis-aligned bounding rectangle.
    """
    pts = np.array(box, dtype=float)
    x_min = float(pts[:, 0].min())
    y_min = float(pts[:, 1].min())
    x_max = float(pts[:, 0].max())
    y_max = float(pts[:, 1].max())
    return x_min, y_min, x_max, y_max


# ---------------------------------------------------------------------------
# RapidOCR backend
# ---------------------------------------------------------------------------


class RapidOCRBackend:
    """Wrapper around ``rapidocr_onnxruntime.RapidOCR``."""

    name = "rapidocr"

    def __init__(self):
        from rapidocr_onnxruntime import RapidOCR
        self._engine = RapidOCR()
        logger.info("RapidOCR backend initialised")

    def ocr_image(self, img: Image.Image) -> list[OCRLine]:
        """Run OCR on a PIL Image and return normalised OCRLines."""
        arr = np.asarray(img.convert("RGB"))
        result, _elapsed = self._engine(arr)
        lines: list[OCRLine] = []
        if not result:
            return lines
        for idx, item in enumerate(result):
            box, text, score = item[0], item[1], item[2]
            txt = str(text).strip()
            if not txt:
                continue
            try:
                conf = float(score)
            except (ValueError, TypeError):
                conf = 0.0
            x1, y1, x2, y2 = _normalise_box(box)
            lines.append(OCRLine(
                text=txt, confidence=conf,
                x1=x1, y1=y1, x2=x2, y2=y2,
                order=idx,
            ))
        return lines


# ---------------------------------------------------------------------------
# PaddleOCR backend (optional)
# ---------------------------------------------------------------------------


class PaddleOCRBackend:
    """Wrapper around ``paddleocr.PaddleOCR`` (optional dependency).

    Only activated when ``OCR_BACKEND=paddleocr`` AND the ``paddleocr``
    package is importable.  Uses the standard PP-OCR text detection +
    recognition pipeline (no heavy PP-Structure models required).
    """

    name = "paddleocr"

    def __init__(self):
        try:
            from paddleocr import PaddleOCR
            self._engine = PaddleOCR(use_angle_cls=True, lang="en", show_log=False)
            logger.info("PaddleOCR backend initialised")
        except ImportError as exc:
            raise ImportError(
                "paddleocr package is not installed. "
                "Install with: pip install paddleocr paddlepaddle"
            ) from exc

    def ocr_image(self, img: Image.Image) -> list[OCRLine]:
        """Run OCR on a PIL Image and return normalised OCRLines."""
        arr = np.asarray(img.convert("RGB"))
        result = self._engine.ocr(arr, cls=True)
        lines: list[OCRLine] = []
        if not result or not result[0]:
            return lines
        for idx, item in enumerate(result[0]):
            box, (text, score) = item[0], item[1]
            txt = str(text).strip()
            if not txt:
                continue
            try:
                conf = float(score)
            except (ValueError, TypeError):
                conf = 0.0
            x1, y1, x2, y2 = _normalise_box(box)
            lines.append(OCRLine(
                text=txt, confidence=conf,
                x1=x1, y1=y1, x2=x2, y2=y2,
                order=idx,
            ))
        return lines


# ---------------------------------------------------------------------------
# Image preprocessing helpers
# ---------------------------------------------------------------------------


def preprocess_for_ocr(img: Image.Image) -> Image.Image:
    """Apply light preprocessing to improve OCR quality.

    - Convert to RGB
    - Auto-contrast
    - Light sharpening via contrast enhancement
    Does NOT rotate or warp (that would require layout analysis).
    """
    img = img.convert("RGB")
    img = ImageOps.autocontrast(img, cutoff=1)
    enhancer = ImageEnhance.Contrast(img)
    img = enhancer.enhance(1.2)
    return img


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_backend():
    """Return the active OCR backend (lazy-initialised singleton)."""
    global _backend, _backend_name
    if _backend is not None:
        return _backend

    requested = os.environ.get("OCR_BACKEND", "rapidocr").strip().lower()

    if requested == "paddleocr":
        try:
            _backend = PaddleOCRBackend()
            _backend_name = "paddleocr"
            return _backend
        except ImportError:
            logger.warning(
                "PaddleOCR requested but not installed; falling back to RapidOCR"
            )

    _backend = RapidOCRBackend()
    _backend_name = "rapidocr"
    return _backend


def ocr_image_bytes(img_bytes: bytes) -> list[OCRLine]:
    """Run OCR on raw image bytes, return normalised OCRLines."""
    img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    backend = get_backend()
    return backend.ocr_image(img)


def ocr_pdf_bytes(pdf_bytes: bytes) -> list[OCRLine]:
    """Render PDF pages to images and OCR each, returning normalised OCRLines."""
    import pymupdf

    backend = get_backend()
    all_lines: list[OCRLine] = []
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    try:
        for page_idx, page in enumerate(doc):
            pix = page.get_pixmap(dpi=200)
            img_bytes = pix.tobytes("png")
            img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
            page_lines = backend.ocr_image(img)
            for line in page_lines:
                line.page = page_idx
            all_lines.extend(page_lines)
    finally:
        doc.close()
    return all_lines


def ocr_file(file_bytes: bytes, filename: str) -> list[OCRLine]:
    """Dispatch to the right reader based on file type.

    Returns normalised OCRLines with bounding boxes.
    """
    name = (filename or "").lower()
    if name.endswith(".pdf"):
        return ocr_pdf_bytes(file_bytes)
    return ocr_image_bytes(file_bytes)


def ocr_file_fallback(file_bytes: bytes, filename: str) -> list[OCRLine]:
    """OCR with preprocessing fallback.

    If initial OCR produces very few lines, retry with preprocessing.
    """
    lines = ocr_file(file_bytes, filename)
    if len(lines) >= 3:
        return lines

    # Retry with preprocessing
    name = (filename or "").lower()
    if name.endswith(".pdf"):
        return lines  # PDF preprocessing is harder; skip for now

    try:
        img = Image.open(io.BytesIO(file_bytes)).convert("RGB")
        enhanced = preprocess_for_ocr(img)
        backend = get_backend()
        enhanced_lines = backend.ocr_image(enhanced)
        if len(enhanced_lines) > len(lines):
            logger.info("Preprocessing improved OCR: %d -> %d lines",
                        len(lines), len(enhanced_lines))
            return enhanced_lines
    except Exception:
        pass
    return lines
