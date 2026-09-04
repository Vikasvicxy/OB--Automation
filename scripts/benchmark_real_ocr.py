#!/usr/bin/env python3
"""Real-image OCR benchmark: flat-text vs layout-aware extraction.

Runs the actual RapidOCR backend on real Aadhaar images/PDFs placed in
data/ocr_benchmark/input/ and compares the old flat-text extraction path
against the new layout-aware path.

Usage:
    python scripts/benchmark_real_ocr.py
    python scripts/benchmark_real_ocr.py --input data/ocr_benchmark/input

All OCR runs locally.  No images leave the machine.  No full Aadhaar
numbers or full addresses are written to any output file.

Output:
    data/ocr_benchmark/output/benchmark_results.csv
    data/ocr_benchmark/output/benchmark_summary.txt
    data/ocr_benchmark/output/benchmark_report.html  (if generated)
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Ensure project root is on sys.path
# ---------------------------------------------------------------------------

_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent
sys.path.insert(0, str(_PROJECT_ROOT))

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SUPPORTED_EXTS = {".jpg", ".jpeg", ".png", ".pdf"}

DEFAULT_INPUT = _PROJECT_ROOT / "data" / "ocr_benchmark" / "input"
DEFAULT_OUTPUT = _PROJECT_ROOT / "data" / "ocr_benchmark" / "output"
DEFAULT_GT = _PROJECT_ROOT / "data" / "ocr_benchmark" / "ground_truth.csv"


# ---------------------------------------------------------------------------
# Name normalisation (case + whitespace only — never drops initials)
# ---------------------------------------------------------------------------


def _norm_name(s: str) -> str:
    """Normalise a name for comparison: lowercase, collapse whitespace.

    Does NOT strip initials or reorder tokens.
    """
    return " ".join(s.strip().lower().split())


# ---------------------------------------------------------------------------
# DOB normalisation
# ---------------------------------------------------------------------------


def _norm_dob(s: str) -> str:
    """Normalise DOB: accept DD/MM/YYYY, DD-MM-YYYY, DD.MM.YYYY.

    Returns DD/MM/YYYY or empty string.
    """
    s = s.strip().replace("-", "/").replace(".", "/")
    m = re.match(r"(\d{2})/(\d{2})/(\d{4})", s)
    if m:
        return f"{m.group(1)}/{m.group(2)}/{m.group(3)}"
    # Also accept YYYY-MM-DD
    m2 = re.match(r"(\d{4})/(\d{2})/(\d{2})", s)
    if m2:
        return f"{m2.group(3)}/{m2.group(2)}/{m2.group(1)}"
    return s


# ---------------------------------------------------------------------------
# Ground truth loader
# ---------------------------------------------------------------------------


@dataclass
class GroundTruth:
    file_name: str
    expected_name: str = ""
    expected_dob: str = ""
    expected_aadhaar_last4: str = ""
    expected_address_present: str = "unknown"  # yes / no / unknown
    notes: str = ""


def load_ground_truth(path: Path) -> dict[str, GroundTruth]:
    """Load ground truth CSV, keyed by file_name."""
    gt: dict[str, GroundTruth] = {}
    if not path.exists():
        return gt
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            fn = row.get("file_name", "").strip()
            if not fn:
                continue
            gt[fn] = GroundTruth(
                file_name=fn,
                expected_name=row.get("expected_name", "").strip(),
                expected_dob=row.get("expected_dob", "").strip(),
                expected_aadhaar_last4=row.get("expected_aadhaar_last4", "").strip(),
                expected_address_present=row.get("expected_address_present", "unknown").strip().lower(),
                notes=row.get("notes", "").strip(),
            )
    return gt


# ---------------------------------------------------------------------------
# Extraction results
# ---------------------------------------------------------------------------


@dataclass
class ExtractionResult:
    name: str = ""
    dob: str = ""
    aadhaar_last4: str = ""
    address_present: str = "no"
    confidence: str = "Missing"
    runtime_ms: float = 0.0
    manual_review_needed: bool = True
    error: str = ""
    evidence_top_score: float = 0.0
    evidence_runner_up_score: float = 0.0
    evidence_margin: float = 0.0
    evidence_needs_review: bool = True


# ---------------------------------------------------------------------------
# FLAT-TEXT extraction path (old)
# ---------------------------------------------------------------------------


def run_flat_extraction(file_bytes: bytes, filename: str) -> ExtractionResult:
    """Run the old flat-text extraction path on a real image/PDF."""
    from app.ocr import (
        extract_text, extract_aadhaar_name, extract_aadhaar_num,
        extract_dob, extract_address,
    )

    t0 = time.perf_counter()
    try:
        lines = extract_text(file_bytes, filename)
    except Exception as exc:
        ms = (time.perf_counter() - t0) * 1000
        return ExtractionResult(error=str(exc), runtime_ms=ms)

    name, conf = extract_aadhaar_name(lines)
    dob = extract_dob(lines) or ""
    aadhaar_full = extract_aadhaar_num(lines)
    address = extract_address(lines)
    ms = (time.perf_counter() - t0) * 1000

    aadhaar_last4 = aadhaar_full[-4:] if aadhaar_full else ""
    address_present = "yes" if address and len(address) >= 8 else "no"
    manual_review = conf != "High" or not name

    return ExtractionResult(
        name=name,
        dob=dob,
        aadhaar_last4=aadhaar_last4,
        address_present=address_present,
        confidence=conf,
        runtime_ms=ms,
        manual_review_needed=manual_review,
    )


# ---------------------------------------------------------------------------
# LAYOUT-AWARE extraction path (new)
# ---------------------------------------------------------------------------


def run_layout_extraction(file_bytes: bytes, filename: str) -> ExtractionResult:
    """Run the new layout-aware extraction path on a real image/PDF."""
    from app.ocr import extract_ocr_lines
    from app.document_parsers.aadhaar import parse_aadhaar

    t0 = time.perf_counter()
    try:
        ocr_lines = extract_ocr_lines(file_bytes, filename)
    except Exception as exc:
        ms = (time.perf_counter() - t0) * 1000
        return ExtractionResult(error=str(exc), runtime_ms=ms)

    try:
        result = parse_aadhaar(ocr_lines)
    except Exception as exc:
        ms = (time.perf_counter() - t0) * 1000
        return ExtractionResult(error=str(exc), runtime_ms=ms)

    # Run evidence scoring on the OCR lines
    evidence_top_score = 0.0
    evidence_runner_up = 0.0
    evidence_margin = 0.0
    evidence_needs_review = True
    try:
        from app.evidence import score_name_candidates
        name_result = score_name_candidates(ocr_lines)
        if name_result.selected:
            evidence_top_score = name_result.selected.score
        if name_result.runner_up:
            evidence_runner_up = name_result.runner_up.score
        evidence_margin = name_result.margin
        evidence_needs_review = name_result.needs_review
    except Exception:
        pass

    ms = (time.perf_counter() - t0) * 1000

    name_ev = result.name
    dob_ev = result.dob
    num_ev = result.aadhaar_number
    addr_ev = result.address

    name = name_ev.value
    dob = dob_ev.value
    aadhaar_last4 = num_ev.value[-4:] if num_ev.value else ""
    address_present = "yes" if addr_ev.value and len(addr_ev.value) >= 8 else "no"
    conf = name_ev.confidence if name else "Missing"
    manual_review = conf != "High" or not name

    return ExtractionResult(
        name=name,
        dob=dob,
        aadhaar_last4=aadhaar_last4,
        address_present=address_present,
        confidence=conf,
        runtime_ms=ms,
        manual_review_needed=manual_review,
        evidence_top_score=evidence_top_score,
        evidence_runner_up_score=evidence_runner_up,
        evidence_margin=evidence_margin,
        evidence_needs_review=evidence_needs_review,
    )


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


@dataclass
class FieldScore:
    label: str  # Exact / Normalized Match / Wrong / Missing / Skipped
    detail: str = ""


def score_name(extracted: str, expected: str) -> FieldScore:
    if not expected:
        return FieldScore("Skipped", "no ground truth")
    if not extracted:
        return FieldScore("Missing", "no value extracted")
    if extracted == expected:
        return FieldScore("Exact")
    if _norm_name(extracted) == _norm_name(expected):
        return FieldScore("Normalized Match")
    return FieldScore("Wrong", f"got '{extracted}'")


def score_dob(extracted: str, expected: str) -> FieldScore:
    if not expected:
        return FieldScore("Skipped", "no ground truth")
    if not extracted:
        return FieldScore("Missing", "no value extracted")
    if _norm_dob(extracted) == _norm_dob(expected):
        return FieldScore("Exact")
    return FieldScore("Wrong", f"got '{extracted}'")


def score_aadhaar_last4(extracted: str, expected: str) -> FieldScore:
    if not expected:
        return FieldScore("Skipped", "no ground truth")
    if not extracted:
        return FieldScore("Missing", "no value extracted")
    if extracted == expected:
        return FieldScore("Exact")
    return FieldScore("Wrong", f"got '{extracted}'")


def score_address_present(extracted: str, expected: str) -> FieldScore:
    if expected == "unknown":
        return FieldScore("Skipped", "unknown in ground truth")
    if not expected:
        return FieldScore("Skipped", "no ground truth")
    if extracted == expected:
        return FieldScore("Exact")
    return FieldScore("Wrong", f"got '{extracted}', expected '{expected}'")


def determine_winner(
    flat_scores: dict[str, FieldScore],
    layout_scores: dict[str, FieldScore],
) -> str:
    """Determine per-file winner: LAYOUT, FLAT, TIE, or INSUFFICIENT."""
    scorable = ["name", "dob", "aadhaar_last4", "address_present"]
    flat_wins = 0
    layout_wins = 0
    has_ground = False

    for field_name in scorable:
        fs = flat_scores[field_name]
        ls = layout_scores[field_name]
        if fs.label == "Skipped" and ls.label == "Skipped":
            continue
        has_ground = True
        flat_correct = fs.label in ("Exact", "Normalized Match")
        layout_correct = ls.label in ("Exact", "Normalized Match")
        if layout_correct and not flat_correct:
            layout_wins += 1
        elif flat_correct and not layout_correct:
            flat_wins += 1

    if not has_ground:
        return "INSUFFICIENT_GROUND_TRUTH"
    if layout_wins > flat_wins:
        return "LAYOUT"
    if flat_wins > layout_wins:
        return "FLAT"
    return "TIE"


# ---------------------------------------------------------------------------
# File discovery
# ---------------------------------------------------------------------------


def discover_files(input_dir: Path) -> list[Path]:
    """Find all supported image/PDF files in the input directory."""
    files = []
    for entry in sorted(input_dir.iterdir()):
        if entry.is_file() and entry.suffix.lower() in SUPPORTED_EXTS:
            files.append(entry)
    return files


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------


def _write_results_csv(rows: list[dict], output_path: Path) -> None:
    """Write benchmark_results.csv."""
    if not rows:
        return
    fieldnames = [
        "file_name",
        "flat_name_result", "layout_name_result",
        "flat_dob_result", "layout_dob_result",
        "flat_aadhaar_result", "layout_aadhaar_result",
        "flat_address_result", "layout_address_result",
        "flat_runtime_ms", "layout_runtime_ms",
        "flat_manual_review_needed", "layout_manual_review_needed",
        "layout_confidence",
        "layout_top_score", "layout_runner_up_score",
        "layout_margin", "layout_evidence_needs_review",
        "winner",
    ]
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _acc(items: list[str]) -> float:
    """Accuracy = count of Exact or Normalized Match / count of scored items."""
    scored = [i for i in items if i not in ("Skipped", "")]
    if not scored:
        return 0.0
    correct = sum(1 for i in scored if i in ("Exact", "Normalized Match"))
    return (correct / len(scored)) * 100.0


def _write_summary(
    summary_path: Path,
    total_files: int,
    scored_files: int,
    flat_name_scores: list[str],
    layout_name_scores: list[str],
    flat_dob_scores: list[str],
    layout_dob_scores: list[str],
    flat_aad_scores: list[str],
    layout_aad_scores: list[str],
    flat_addr_scores: list[str],
    layout_addr_scores: list[str],
    flat_review_count: int,
    layout_review_count: int,
    flat_runtimes: list[float],
    layout_runtimes: list[float],
) -> None:
    flat_all = flat_name_scores + flat_dob_scores + flat_aad_scores + flat_addr_scores
    layout_all = layout_name_scores + layout_dob_scores + layout_aad_scores + layout_addr_scores

    lines = [
        "=" * 60,
        "REAL OCR BENCHMARK SUMMARY",
        "=" * 60,
        "",
        f"Total files:          {total_files}",
        f"Scored files:         {scored_files}",
        "",
        "--- NAME ---",
        f"  Flat accuracy:   {_acc(flat_name_scores):.1f}%",
        f"  Layout accuracy: {_acc(layout_name_scores):.1f}%",
        "",
        "--- DOB ---",
        f"  Flat accuracy:   {_acc(flat_dob_scores):.1f}%",
        f"  Layout accuracy: {_acc(layout_dob_scores):.1f}%",
        "",
        "--- AADHAAR LAST 4 ---",
        f"  Flat accuracy:   {_acc(flat_aad_scores):.1f}%",
        f"  Layout accuracy: {_acc(layout_aad_scores):.1f}%",
        "",
        "--- ADDRESS PRESENCE ---",
        f"  Flat accuracy:   {_acc(flat_addr_scores):.1f}%",
        f"  Layout accuracy: {_acc(layout_addr_scores):.1f}%",
        "",
        "--- OVERALL FIELD ACCURACY ---",
        f"  Flat:   {_acc(flat_all):.1f}%",
        f"  Layout: {_acc(layout_all):.1f}%",
        "",
        "--- MANUAL REVIEW RATE ---",
        f"  Flat:   {(flat_review_count / max(scored_files, 1)) * 100:.1f}%",
        f"  Layout: {(layout_review_count / max(scored_files, 1)) * 100:.1f}%",
        "",
        "--- AVERAGE RUNTIME ---",
        f"  Flat:   {(sum(flat_runtimes) / max(len(flat_runtimes), 1)):.1f}ms",
        f"  Layout: {(sum(layout_runtimes) / max(len(layout_runtimes), 1)):.1f}ms",
        "",
        "=" * 60,
    ]

    # Improvement
    flat_acc = _acc(flat_all)
    layout_acc = _acc(layout_all)
    diff = layout_acc - flat_acc
    sign = "+" if diff >= 0 else ""
    lines.append(f"Improvement: {sign}{diff:.1f} percentage points (layout vs flat)")
    lines.append("=" * 60)

    summary_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_html_report(
    html_path: Path,
    total_files: int,
    scored_files: int,
    file_rows: list[dict],
    flat_name_scores: list[str],
    layout_name_scores: list[str],
    flat_dob_scores: list[str],
    layout_dob_scores: list[str],
    flat_aad_scores: list[str],
    layout_aad_scores: list[str],
    flat_addr_scores: list[str],
    layout_addr_scores: list[str],
) -> None:
    """Generate a local HTML benchmark report."""
    flat_all = flat_name_scores + flat_dob_scores + flat_aad_scores + flat_addr_scores
    layout_all = layout_name_scores + layout_dob_scores + layout_aad_scores + layout_addr_scores

    def _score_cell(label: str) -> str:
        color = {"Exact": "#2d6", "Normalized Match": "#ad0", "Wrong": "#e44", "Missing": "#aaa", "Skipped": "#888"}.get(label, "#888")
        return f'<td style="color:{color};font-weight:600">{label}</td>'

    rows_html = ""
    for r in file_rows:
        winner = r["winner"]
        w_color = {"LAYOUT": "#2d6", "FLAT": "#36d", "TIE": "#888", "INSUFFICIENT_GROUND_TRUTH": "#aaa"}.get(winner, "#888")
        rows_html += "<tr>"
        rows_html += f'<td>{r["file_name"]}</td>'
        rows_html += _score_cell(r["flat_name_result"])
        rows_html += _score_cell(r["layout_name_result"])
        rows_html += _score_cell(r["flat_dob_result"])
        rows_html += _score_cell(r["layout_dob_result"])
        rows_html += _score_cell(r["flat_aadhaar_result"])
        rows_html += _score_cell(r["layout_aadhaar_result"])
        rows_html += _score_cell(r["flat_address_result"])
        rows_html += _score_cell(r["layout_address_result"])
        rows_html += f'<td>{r["flat_runtime_ms"]:.1f}ms</td>'
        rows_html += f'<td>{r["layout_runtime_ms"]:.1f}ms</td>'
        rows_html += f'<td style="color:{w_color};font-weight:700">{winner}</td>'
        rows_html += "</tr>\n"

    flat_acc = _acc(flat_all)
    layout_acc = _acc(layout_all)
    diff = layout_acc - flat_acc
    sign = "+" if diff >= 0 else ""

    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>OCR Benchmark Report</title>
<style>
body {{ font-family: sans-serif; margin: 2em; }}
h1 {{ border-bottom: 2px solid #333; padding-bottom: .3em; }}
table {{ border-collapse: collapse; margin: 1em 0; }}
th, td {{ border: 1px solid #ccc; padding: 6px 10px; text-align: left; }}
th {{ background: #f5f5f5; }}
.metrics {{ display: flex; gap: 2em; margin: 1em 0; }}
.metric-box {{ background: #f9f9f9; border: 1px solid #ddd; padding: 1em 1.5em; border-radius: 6px; }}
.metric-box h3 {{ margin: 0 0 .5em; }}
.metric-val {{ font-size: 1.4em; font-weight: 700; }}
</style></head><body>
<h1>OCR Benchmark Report</h1>
<div class="metrics">
  <div class="metric-box"><h3>Files</h3><div class="metric-val">{scored_files} / {total_files}</div></div>
  <div class="metric-box"><h3>Flat Accuracy</h3><div class="metric-val">{flat_acc:.1f}%</div></div>
  <div class="metric-box"><h3>Layout Accuracy</h3><div class="metric-val">{layout_acc:.1f}%</div></div>
  <div class="metric-box"><h3>Improvement</h3><div class="metric-val">{sign}{diff:.1f}pp</div></div>
</div>
<table>
<tr>
  <th>File</th>
  <th>Flat Name</th><th>Layout Name</th>
  <th>Flat DOB</th><th>Layout DOB</th>
  <th>Flat Aadhaar4</th><th>Layout Aadhaar4</th>
  <th>Flat Address</th><th>Layout Address</th>
  <th>Flat ms</th><th>Layout ms</th>
  <th>Winner</th>
</tr>
{rows_html}
</table>
<p><em>No full Aadhaar numbers or full addresses are shown in this report.</em></p>
</body></html>"""

    html_path.write_text(html, encoding="utf-8")


# ---------------------------------------------------------------------------
# Console output
# ---------------------------------------------------------------------------


def _print_file_result(
    filename: str,
    flat: ExtractionResult,
    layout: ExtractionResult,
    flat_scores: dict[str, FieldScore],
    layout_scores: dict[str, FieldScore],
    winner: str,
) -> None:
    print(f"\n  {filename}")
    for field_name in ("name", "dob", "aadhaar_last4", "address_present"):
        fs = flat_scores[field_name]
        ls = layout_scores[field_name]
        label = field_name.replace("_last4", " last4").replace("_present", " present").title()
        status = "PASS" if fs.label in ("Exact", "Normalized Match") else fs.label
        status_l = "PASS" if ls.label in ("Exact", "Normalized Match") else ls.label
        print(f"    {label:18s}  Flat: {status:18s}  Layout: {status_l:18s}")

    w_color = {"LAYOUT": "\033[92m", "FLAT": "\033[94m", "TIE": "\033[90m", "INSUFFICIENT_GROUND_TRUTH": "\033[90m"}.get(winner, "")
    print(f"    Winner: {w_color}{winner}\033[0m  (flat {flat.runtime_ms:.0f}ms  layout {layout.runtime_ms:.0f}ms)")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Real-image OCR benchmark: flat-text vs layout-aware extraction"
    )
    parser.add_argument(
        "--input", type=Path, default=DEFAULT_INPUT,
        help="Directory containing test images/PDFs (default: data/ocr_benchmark/input/)",
    )
    parser.add_argument(
        "--ground-truth", type=Path, default=DEFAULT_GT,
        help="Path to ground_truth.csv (default: data/ocr_benchmark/ground_truth.csv)",
    )
    parser.add_argument(
        "--output", type=Path, default=DEFAULT_OUTPUT,
        help="Output directory for reports (default: data/ocr_benchmark/output/)",
    )
    args = parser.parse_args()

    input_dir: Path = args.input.resolve()
    gt_path: Path = args.ground_truth.resolve()
    output_dir: Path = args.output.resolve()

    print("=" * 60)
    print("REAL OCR BENCHMARK")
    print(f"Input:      {input_dir}")
    print(f"Ground truth: {gt_path}")
    print(f"Output:     {output_dir}")
    print("=" * 60)

    # Validate input
    if not input_dir.exists():
        print(f"\nERROR: Input directory does not exist: {input_dir}")
        print(f"Create it and place your test Aadhaar images/PDFs inside:")
        print(f"  mkdir -p {input_dir}")
        sys.exit(1)

    files = discover_files(input_dir)
    if not files:
        print(f"\nNo supported files found in {input_dir}")
        print(f"Place .jpg, .jpeg, .png, or .pdf files there.")
        sys.exit(0)

    output_dir.mkdir(parents=True, exist_ok=True)

    # Load ground truth
    gt = load_ground_truth(gt_path)
    if gt:
        print(f"\nLoaded {len(gt)} ground truth entries")
    else:
        print(f"\nNo ground truth loaded from {gt_path}")
        print(f"Results will show extraction output without scoring.")

    print(f"\nFound {len(files)} files to benchmark")

    # Initialise OCR engine once
    print("\nInitialising OCR engine...")
    from app.ocr_backends import get_backend
    backend = get_backend()
    print(f"Backend: {backend.name}")

    # Process each file
    csv_rows: list[dict] = []
    flat_name_scores: list[str] = []
    layout_name_scores: list[str] = []
    flat_dob_scores: list[str] = []
    layout_dob_scores: list[str] = []
    flat_aad_scores: list[str] = []
    layout_aad_scores: list[str] = []
    flat_addr_scores: list[str] = []
    layout_addr_scores: list[str] = []
    flat_review_count = 0
    layout_review_count = 0
    flat_runtimes: list[float] = []
    layout_runtimes: list[float] = []
    scored_count = 0

    for file_path in files:
        file_bytes = file_path.read_bytes()
        filename = file_path.name
        ground = gt.get(filename)

        # Run both extraction paths
        flat_result = run_flat_extraction(file_bytes, filename)
        layout_result = run_layout_extraction(file_bytes, filename)

        flat_runtimes.append(flat_result.runtime_ms)
        layout_runtimes.append(layout_result.runtime_ms)

        # Score against ground truth
        flat_scores = {
            "name": score_name(flat_result.name, ground.expected_name if ground else ""),
            "dob": score_dob(flat_result.dob, ground.expected_dob if ground else ""),
            "aadhaar_last4": score_aadhaar_last4(flat_result.aadhaar_last4, ground.expected_aadhaar_last4 if ground else ""),
            "address_present": score_address_present(flat_result.address_present, ground.expected_address_present if ground else "unknown"),
        }
        layout_scores = {
            "name": score_name(layout_result.name, ground.expected_name if ground else ""),
            "dob": score_dob(layout_result.dob, ground.expected_dob if ground else ""),
            "aadhaar_last4": score_aadhaar_last4(layout_result.aadhaar_last4, ground.expected_aadhaar_last4 if ground else ""),
            "address_present": score_address_present(layout_result.address_present, ground.expected_address_present if ground else "unknown"),
        }

        winner = determine_winner(flat_scores, layout_scores)
        if winner != "INSUFFICIENT_GROUND_TRUTH":
            scored_count += 1

        flat_name_scores.append(flat_scores["name"].label)
        layout_name_scores.append(layout_scores["name"].label)
        flat_dob_scores.append(flat_scores["dob"].label)
        layout_dob_scores.append(layout_scores["dob"].label)
        flat_aad_scores.append(flat_scores["aadhaar_last4"].label)
        layout_aad_scores.append(layout_scores["aadhaar_last4"].label)
        flat_addr_scores.append(flat_scores["address_present"].label)
        layout_addr_scores.append(layout_scores["address_present"].label)
        if flat_result.manual_review_needed:
            flat_review_count += 1
        if layout_result.manual_review_needed:
            layout_review_count += 1

        # Console output
        _print_file_result(filename, flat_result, layout_result,
                           flat_scores, layout_scores, winner)

        # CSV row (no full Aadhaar/address)
        csv_rows.append({
            "file_name": filename,
            "flat_name_result": flat_scores["name"].label,
            "layout_name_result": layout_scores["name"].label,
            "flat_dob_result": flat_scores["dob"].label,
            "layout_dob_result": layout_scores["dob"].label,
            "flat_aadhaar_result": flat_scores["aadhaar_last4"].label,
            "layout_aadhaar_result": layout_scores["aadhaar_last4"].label,
            "flat_address_result": flat_scores["address_present"].label,
            "layout_address_result": layout_scores["address_present"].label,
            "flat_runtime_ms": round(flat_result.runtime_ms, 1),
            "layout_runtime_ms": round(layout_result.runtime_ms, 1),
            "flat_manual_review_needed": flat_result.manual_review_needed,
            "layout_manual_review_needed": layout_result.manual_review_needed,
            "layout_confidence": layout_result.confidence,
            "layout_top_score": round(layout_result.evidence_top_score, 1),
            "layout_runner_up_score": round(layout_result.evidence_runner_up_score, 1),
            "layout_margin": round(layout_result.evidence_margin, 1),
            "layout_evidence_needs_review": layout_result.evidence_needs_review,
            "winner": winner,
        })

    # Write reports
    results_csv = output_dir / "benchmark_results.csv"
    _write_results_csv(csv_rows, results_csv)

    summary_txt = output_dir / "benchmark_summary.txt"
    _write_summary(
        summary_txt,
        total_files=len(files),
        scored_files=scored_count,
        flat_name_scores=flat_name_scores,
        layout_name_scores=layout_name_scores,
        flat_dob_scores=flat_dob_scores,
        layout_dob_scores=layout_dob_scores,
        flat_aad_scores=flat_aad_scores,
        layout_aad_scores=layout_aad_scores,
        flat_addr_scores=flat_addr_scores,
        layout_addr_scores=layout_addr_scores,
        flat_review_count=flat_review_count,
        layout_review_count=layout_review_count,
        flat_runtimes=flat_runtimes,
        layout_runtimes=layout_runtimes,
    )

    html_path = output_dir / "benchmark_report.html"
    _write_html_report(
        html_path,
        total_files=len(files),
        scored_files=scored_count,
        file_rows=csv_rows,
        flat_name_scores=flat_name_scores,
        layout_name_scores=layout_name_scores,
        flat_dob_scores=flat_dob_scores,
        layout_dob_scores=layout_dob_scores,
        flat_aad_scores=flat_aad_scores,
        layout_aad_scores=layout_aad_scores,
        flat_addr_scores=flat_addr_scores,
        layout_addr_scores=layout_addr_scores,
    )

    # Final summary
    flat_all = flat_name_scores + flat_dob_scores + flat_aad_scores + flat_addr_scores
    layout_all = layout_name_scores + layout_dob_scores + layout_aad_scores + layout_addr_scores

    print("\n" + "=" * 60)
    print("REAL OCR BENCHMARK COMPLETE")
    print("=" * 60)
    print(f"Files processed:        {len(files)}")
    print(f"Flat overall accuracy:  {_acc(flat_all):.1f}%")
    print(f"Layout overall accuracy: {_acc(layout_all):.1f}%")
    diff = _acc(layout_all) - _acc(flat_all)
    sign = "+" if diff >= 0 else ""
    print(f"Improvement:            {sign}{diff:.1f} percentage points")
    print(f"Flat review rate:       {(flat_review_count / max(len(files), 1)) * 100:.1f}%")
    print(f"Layout review rate:     {(layout_review_count / max(len(files), 1)) * 100:.1f}%")
    print(f"Average flat runtime:   {(sum(flat_runtimes) / max(len(flat_runtimes), 1)):.1f}ms")
    print(f"Average layout runtime: {(sum(layout_runtimes) / max(len(layout_runtimes), 1)):.1f}ms")
    print(f"\nReport path:            {results_csv}")
    print(f"Summary path:           {summary_txt}")
    print(f"HTML report:            {html_path}")


if __name__ == "__main__":
    main()
