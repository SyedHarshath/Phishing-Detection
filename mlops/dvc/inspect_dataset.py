"""Inspect the dataset's bytes and schema without modifying source files."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

PARSER_VERSION = "binary-tab-loader-v1"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATASET = PROJECT_ROOT / "dataset" / "big_dataset"
DEFAULT_REPORT = PROJECT_ROOT / "mlops" / "dvc" / "dataset_schema_report.json"
SPLITS = ("train", "val", "test")
LABELS = {b"phishing", b"legitimate"}
MAX_RECORD_BYTES = 1024 * 1024
CHUNK_BYTES = 1024 * 1024
MAGIC_TYPES = (
    (b"\x1f\x8b", "gzip"),
    (b"PK\x03\x04", "zip"),
    (b"\x89PNG\r\n\x1a\n", "PNG"),
    (b"\x80\x04", "Python pickle"),
)


def _byte_profile(path: Path) -> dict:
    size = path.stat().st_size
    with path.open("rb") as handle:
        prefix = handle.read(64)
    nul_bytes = 0
    tab_bytes = 0
    line_feed_bytes = 0
    carriage_return_bytes = 0
    sampled = 0
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK_BYTES):
            sampled += len(chunk)
            nul_bytes += chunk.count(0)
            tab_bytes += chunk.count(b"\t")
            line_feed_bytes += chunk.count(b"\n")
            carriage_return_bytes += chunk.count(b"\r")

    magic_type = next(
        (kind for signature, kind in MAGIC_TYPES if prefix.startswith(signature)),
        None,
    )
    try:
        prefix.decode("utf-8")
        utf8_prefix = True
    except UnicodeDecodeError:
        utf8_prefix = False

    if size and nul_bytes == size:
        detected_type = "zero-filled binary"
        encoding = None
    elif magic_type:
        detected_type = f"compressed/container ({magic_type})"
        encoding = None
    elif nul_bytes:
        detected_type = "binary or mixed-content"
        encoding = None
    elif utf8_prefix:
        detected_type = "UTF-8 delimited text candidate"
        encoding = "UTF-8"
    else:
        detected_type = "unknown binary or non-UTF-8 text"
        encoding = None

    return {
        "size_bytes": size,
        "detected_type": detected_type,
        "encoding": encoding,
        "compression_or_container": magic_type,
        "prefix_hex": prefix.hex(" "),
        "prefix_repr": repr(prefix),
        "nul_byte_count": nul_bytes,
        "nul_byte_ratio": round(nul_bytes / sampled, 8) if sampled else 0.0,
        "tab_byte_count": tab_bytes,
        "line_feed_byte_count": line_feed_bytes,
        "carriage_return_byte_count": carriage_return_bytes,
        "utf8_prefix_decodes": utf8_prefix,
    }


def _parse_text_split(path: Path) -> dict:
    label_counts: Counter[str] = Counter()
    samples = []
    non_empty_records = 0
    malformed_records = 0
    line_feed_count = 0
    carriage_return_count = 0
    first_record = None
    overlong_records = 0

    with path.open("rb") as handle:
        while True:
            raw = handle.readline(MAX_RECORD_BYTES + 1)
            if not raw:
                break
            if len(raw) > MAX_RECORD_BYTES and not raw.endswith(b"\n"):
                overlong_records += 1
                while raw and not raw.endswith(b"\n"):
                    raw = handle.readline(MAX_RECORD_BYTES + 1)
                malformed_records += 1
                continue

            line_feed_count += raw.count(b"\n")
            carriage_return_count += raw.count(b"\r")
            line = raw.rstrip(b"\r\n")
            if not line:
                continue

            non_empty_records += 1
            parts = line.split(b"\t")
            if first_record is None:
                first_record = parts
            if (
                len(parts) != 2
                or parts[0].lower() not in LABELS
                or not parts[1]
            ):
                malformed_records += 1
                continue
            try:
                parts[1].decode("utf-8")
            except UnicodeDecodeError:
                malformed_records += 1
                continue

            label = parts[0].decode("ascii").lower()
            label_counts[label] += 1
            if len(samples) < 5:
                samples.append(
                    {
                        "label": label,
                        "url": parts[1].decode("utf-8", errors="replace"),
                    }
                )

    header_present = bool(
        first_record
        and len(first_record) == 2
        and first_record[0].lower() in {b"label", b"class"}
        and first_record[1].lower() == b"url"
    )
    parsed = sum(label_counts.values())
    if parsed and malformed_records == 0:
        status = "valid"
        confidence = "high"
    elif parsed:
        status = "valid_with_malformed_records"
        confidence = "high"
    else:
        status = "no_parseable_records"
        confidence = "none"

    return {
        "schema": {
            "record_layout": "tab-delimited",
            "fixed_width": False,
            "record_delimiter": "line feed (LF or CRLF)",
            "field_delimiter": "tab",
            "field_order": ["label", "url"],
            "header_present": header_present,
            "label_encoding": {"phishing": 0, "legitimate": 1},
        },
        "record_count": non_empty_records,
        "record_count_estimate": non_empty_records,
        "record_count_estimate_note": "Counted from non-empty text lines.",
        "parsed_record_count": parsed,
        "malformed_record_count": malformed_records,
        "overlong_record_count": overlong_records,
        "record_terminators": {
            "line_feed_bytes": line_feed_count,
            "carriage_return_bytes": carriage_return_count,
        },
        "label_counts": dict(label_counts),
        "sample_records": samples,
        "parsing_status": status,
        "parsing_confidence": confidence,
    }


def inspect_split(path: Path) -> dict:
    profile = _byte_profile(path)
    if profile["detected_type"] != "UTF-8 delimited text candidate":
        return {
            **profile,
            "record_layout": "undetermined; no record or field delimiters observed",
            "schema": None,
            "record_count": 0,
            "record_count_estimate": None,
            "record_count_estimate_note": (
                "No record delimiters or field delimiters were observed."
                if profile["detected_type"] == "zero-filled binary"
                else "The binary/container format has no text record estimate."
            ),
            "parsed_record_count": 0,
            "malformed_record_count": None,
            "label_counts": {},
            "sample_records": [],
            "parsing_status": "not_parseable_as_text",
            "parsing_confidence": "none",
        }
    return {**profile, **_parse_text_split(path)}


def build_report(dataset_dir: Path) -> dict:
    split_reports = {}
    for split in SPLITS:
        path = dataset_dir / f"{split}.txt"
        if not path.is_file():
            split_reports[split] = {
                "parsing_status": "missing",
                "parsing_confidence": "none",
                "error": f"Dataset split not found: {path}",
            }
            continue
        split_reports[split] = inspect_split(path)

    valid = all(
        split_reports[split].get("parsed_record_count", 0) > 0
        for split in SPLITS
    )
    return {
        "parser": PARSER_VERSION,
        "dataset_directory": str(dataset_dir),
        "schema": {
            "record_layout": "tab-delimited text in parseable splits",
            "fixed_width": False,
            "record_format": "UTF-8 text, one record per line",
            "field_delimiter": "tab",
            "field_order": ["label", "url"],
            "header_present": False,
            "labels": {"phishing": 0, "legitimate": 1},
            "record_terminator": "LF or CRLF",
        },
        "splits": split_reports,
        "overall_status": "parseable" if valid else "blocked",
        "overall_parsing_confidence": (
            "high" if valid and all(
                report.get("parsing_confidence") == "high"
                for report in split_reports.values()
            ) else "low"
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()
    if not args.dataset.is_dir():
        raise FileNotFoundError(f"Dataset directory not found: {args.dataset}")

    report = build_report(args.dataset)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Dataset schema report: {args.report}")
    print(f"Overall status: {report['overall_status']}")
    for split, details in report["splits"].items():
        parsed_count = details.get("parsed_record_count")
        malformed_count = details.get("malformed_record_count")
        parsed_text = f"{parsed_count:,}" if parsed_count is not None else "N/A"
        malformed_text = (
            f"{malformed_count:,}" if malformed_count is not None else "N/A"
        )
        print(
            f"{split}: {parsed_text} parsed, {malformed_text} malformed, "
            f"{details.get('parsing_status')}"
        )


if __name__ == "__main__":
    main()
