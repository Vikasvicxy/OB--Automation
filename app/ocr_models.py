"""Normalized OCR result models shared across all backends and parsers.

Both RapidOCR and any optional PaddleOCR backend normalize their raw
output into these structures so downstream parsers never depend on a
single OCR library's raw response format.

License: Apache 2.0 (consistent with RapidOCR and PaddleOCR).
No third-party code is copied; these are original data models.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class OCRLine:
    """A single OCR-detected text line with spatial and confidence metadata.

    Coordinates are in image-pixel space (origin top-left).
    The four corners of the bounding box are stored as (x1,y1) top-left
    and (x2,y2) bottom-right for convenience.  ``order`` reflects the
    reading-order index assigned by the backend or post-processing step.
    """

    text: str
    confidence: float = 0.0
    x1: float = 0.0
    y1: float = 0.0
    x2: float = 0.0
    y2: float = 0.0
    page: int = 0
    order: int = 0

    @property
    def width(self) -> float:
        return self.x2 - self.x1

    @property
    def height(self) -> float:
        return self.y2 - self.y1

    @property
    def center_y(self) -> float:
        return (self.y1 + self.y2) / 2.0

    @property
    def center_x(self) -> float:
        return (self.x1 + self.x2) / 2.0

    @property
    def bottom(self) -> float:
        return self.y2

    @property
    def top(self) -> float:
        return self.y1

    def vertical_gap(self, other: "OCRLine") -> float:
        """Signed vertical gap: positive = other is below self."""
        return other.y1 - self.y2

    def is_above(self, other: "OCRLine", tolerance: float = 0.0) -> bool:
        """True if self is vertically above other (within tolerance)."""
        return self.bottom <= other.top + tolerance

    def is_same_line(self, other: "OCRLine", tolerance: float = 10.0) -> bool:
        """True if self and other are on approximately the same vertical level."""
        return abs(self.center_y - other.center_y) < tolerance

    def overlaps_y(self, other: "OCRLine") -> bool:
        """True if vertical ranges overlap."""
        return self.top < other.bottom and other.top < self.bottom

    @property
    def is_numeric_only(self) -> bool:
        digits = sum(1 for c in self.text if c.isdigit())
        return digits > 0 and all(c.isdigit() or c in " -/" for c in self.text.strip())

    @property
    def label_lower(self) -> str:
        return self.text.strip().lower()


@dataclass
class FieldEvidence:
    """Evidence for an extracted field value.

    Every extracted field carries its value, confidence level, the
    ``source`` document type (Aadhaar, Screenshot, Manual), and
    ``reason`` describing how the value was determined.  ``source_lines``
    keeps the OCR text lines that contributed to the extraction (for
    auditability) and ``box_refs`` stores internal bounding-box
    references that are NOT exposed in the normal UI.
    """

    value: str = ""
    confidence: str = "Missing"  # High | Review | Missing
    source: str = "Manual"
    reason: str = ""
    source_lines: list[str] = field(default_factory=list)
    box_refs: list[tuple[float, float, float, float]] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "value": self.value,
            "source": self.source,
            "confidence": self.confidence,
            "error": None,
        }

    @property
    def is_missing(self) -> bool:
        return not self.value or self.confidence == "Missing"
