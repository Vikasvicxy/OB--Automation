"""Development-only local visual OCR debug view.

Renders an annotated image with bounding boxes showing:
  - Selected Name / DOB / Address regions
  - Rejected candidate regions
  - Confidence levels

Default: OFF. Enable with OCR_DEBUG_VIEW=true.
Binds to localhost only. Never saves images permanently by default.
Privacy: never writes full Aadhaar or full address to disk.
"""

from __future__ import annotations

import io
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

from app.evidence import FieldResult


DEBUG_VIEW_ENABLED = os.environ.get("OCR_DEBUG_VIEW", "").strip().lower() in (
    "true", "1", "yes", "on"
)

# Temporary directory for debug images (cleaned after session)
_DEBUG_TEMP_DIR: Optional[Path] = None


def _get_temp_dir() -> Path:
    global _DEBUG_TEMP_DIR
    if _DEBUG_TEMP_DIR is None:
        _DEBUG_TEMP_DIR = Path(tempfile.mkdtemp(prefix="teamhr_debug_"))
    return _DEBUG_TEMP_DIR


def cleanup_debug_images() -> None:
    """Delete all temporary debug images. Call at session/request end."""
    global _DEBUG_TEMP_DIR
    if _DEBUG_TEMP_DIR and _DEBUG_TEMP_DIR.exists():
        for f in _DEBUG_TEMP_DIR.iterdir():
            try:
                f.unlink()
            except OSError:
                pass
        try:
            _DEBUG_TEMP_DIR.rmdir()
        except OSError:
            pass
    _DEBUG_TEMP_DIR = None


# ── Colors for field labels ──────────────────────────────────────────────────

_COLORS = {
    "NAME": "#22cc66",
    "DOB": "#3399ff",
    "AADHAAR": "#ff6633",
    "ADDRESS": "#cc66ff",
    "REJECTED": "#cc3333",
}

_CONFIDENCE_BG = {
    "High": "#22cc66",
    "Review": "#ffaa00",
    "Missing": "#888888",
    "Conflict": "#cc3333",
}


def _get_font(size: int = 14):
    for name in ["arial.ttf", "Arial.ttf", "DejaVuSans.ttf", "consola.ttf"]:
        try:
            return ImageFont.truetype(name, size)
        except (OSError, IOError):
            continue
    return ImageFont.load_default()


@dataclass
class DebugBox:
    """A single annotated bounding box for the debug view."""
    label: str
    confidence: str
    x1: float
    y1: float
    x2: float
    y2: float
    score: float = 0.0


def render_debug_view(
    image_bytes: bytes,
    field_results: dict[str, FieldResult],
    filename: str = "debug",
) -> Optional[str]:
    """Render an annotated debug image and return its temp file path.

    Returns None if debug view is disabled or rendering fails.
    The image is saved to a temporary directory and should be deleted
    after the session/request.
    """
    if not DEBUG_VIEW_ENABLED:
        return None

    try:
        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        draw = ImageDraw.Draw(img, "RGBA")
        font = _get_font(14)
        font_bold = _get_font(16)

        boxes: list[DebugBox] = []

        # Extract boxes from field results
        for field_name, fr in field_results.items():
            if fr.selected and fr.selected.box_refs:
                color_key = field_name.upper().replace("_NUMBER", "")
                if color_key not in _COLORS:
                    color_key = "REJECTED"
                for bx in fr.selected.box_refs:
                    boxes.append(DebugBox(
                        label=f"{color_key} {fr.confidence}",
                        confidence=fr.confidence,
                        x1=bx[0], y1=bx[1], x2=bx[2], y2=bx[3],
                        score=fr.selected.score,
                    ))

            # Rejected candidates
            for c in fr.all_candidates:
                if c.is_rejected and c.box_refs:
                    for bx in c.box_refs:
                        boxes.append(DebugBox(
                            label=f"REJECTED: {c.rejected_reason[:30]}",
                            confidence="Missing",
                            x1=bx[0], y1=bx[1], x2=bx[2], y2=bx[3],
                            score=c.score,
                        ))

        # Draw boxes
        for box in boxes:
            color = _COLORS.get(box.label.split()[0], "#888888")
            bg_color = _CONFIDENCE_BG.get(box.confidence, "#888888")

            # Draw rectangle
            draw.rectangle(
                [box.x1, box.y1, box.x2, box.y2],
                outline=color, width=3,
            )

            # Draw label background
            label_text = box.label
            bbox = font.getbbox(label_text)
            tw = bbox[2] - bbox[0]
            th = bbox[3] - bbox[1]
            label_y = max(0, box.y1 - th - 6)
            draw.rectangle(
                [box.x1, label_y, box.x1 + tw + 8, label_y + th + 4],
                fill=bg_color,
            )
            draw.text(
                (box.x1 + 4, label_y + 2),
                label_text, fill="white", font=font,
            )

        # Save to temp
        temp_dir = _get_temp_dir()
        out_path = temp_dir / f"{filename}_debug.png"
        img.save(str(out_path), "PNG")
        return str(out_path)

    except Exception:
        return None


def render_debug_overlay_base64(
    image_bytes: bytes,
    field_results: dict[str, FieldResult],
) -> Optional[str]:
    """Render debug overlay and return as base64 data URI (for HTML img src).

    Returns None if debug view is disabled.
    """
    if not DEBUG_VIEW_ENABLED:
        return None

    import base64

    try:
        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        draw = ImageDraw.Draw(img, "RGBA")
        font = _get_font(14)

        for field_name, fr in field_results.items():
            if fr.selected and fr.selected.box_refs:
                color_key = field_name.upper().replace("_NUMBER", "")
                color = _COLORS.get(color_key, "#888888")
                bg = _CONFIDENCE_BG.get(fr.confidence, "#888888")

                for bx in fr.selected.box_refs:
                    draw.rectangle(
                        [bx[0], bx[1], bx[2], bx[3]],
                        outline=color, width=3,
                    )
                    label = f"{color_key} {fr.confidence}"
                    bbox = font.getbbox(label)
                    tw = bbox[2] - bbox[0]
                    th = bbox[3] - bbox[1]
                    ly = max(0, bx[1] - th - 6)
                    draw.rectangle(
                        [bx[0], ly, bx[0] + tw + 8, ly + th + 4],
                        fill=bg,
                    )
                    draw.text((bx[0] + 4, ly + 2), label, fill="white", font=font)

            for c in fr.all_candidates:
                if c.is_rejected and c.box_refs:
                    for bx in c.box_refs:
                        draw.rectangle(
                            [bx[0], bx[1], bx[2], bx[3]],
                            outline="#cc3333", width=2,
                        )
                        label = "REJECTED"
                        bbox = font.getbbox(label)
                        tw = bbox[2] - bbox[0]
                        th = bbox[3] - bbox[1]
                        ly = max(0, bx[1] - th - 6)
                        draw.rectangle(
                            [bx[0], ly, bx[0] + tw + 8, ly + th + 4],
                            fill="#cc3333",
                        )
                        draw.text((bx[0] + 4, ly + 2), label, fill="white", font=font)

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        return f"data:image/png;base64,{b64}"

    except Exception:
        return None
