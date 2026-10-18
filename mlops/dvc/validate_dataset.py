"""Validate the PhishGuard URL dataset without loading the whole dataset into memory.

Usage:
    python -m mlops.dvc.validate_dataset
    python -m mlops.dvc.validate_dataset --dataset dataset/big_dataset
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

DEFAULT_DATASET = Path("dataset/big_dataset")
REPORT_PATH = Path("mlops/dvc/dataset_report.json")


def iter_text_files(dataset: Path):
    for path in sorted(dataset.rglob("*")):
        if path.is_file() and path.suffix.lower() in {".txt", ".csv", ".tsv"}:
            yield path


def inspect_delimited(path: Path):
    counts = {}
    rows = 0
    with path.open("r", encoding="utf-8", errors="replace", newline="") as f:
        sample = f.read(8192)
        f.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",\t;")
        except csv.Error:
            dialect = csv.excel_tab if path.suffix.lower() == ".tsv" else csv.excel
        reader = csv.reader(f, dialect)
        for row in reader:
            if not row:
                continue
            rows += 1
            # Count likely labels when present.
            for value in row:
                label = value.strip().lower()
                if label in {"phishing", "legitimate"}:
                    counts[label] = counts.get(label, 0) + 1
    return rows, counts


def inspect_text(path: Path):
    rows = 0
    counts = {}
    with path.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            if line.strip():
                rows += 1
                parts = [p.strip().lower() for p in line.rstrip("\n").split("\t")]
                for value in parts:
                    if value in {"phishing", "legitimate"}:
                        counts[value] = counts.get(value, 0) + 1
    return rows, counts


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--report", type=Path, default=REPORT_PATH)
    args = parser.parse_args()

    dataset = args.dataset
    if not dataset.exists():
        raise SystemExit(f"Dataset path not found: {dataset}")

    files = list(iter_text_files(dataset))
    if not files:
        raise SystemExit(f"No .txt/.csv/.tsv files found under {dataset}")

    total_rows = 0
    label_counts = {}
    file_reports = []

    for path in files:
        if path.suffix.lower() in {".csv", ".tsv"}:
            rows, counts = inspect_delimited(path)
        else:
            rows, counts = inspect_text(path)

        total_rows += rows
        for label, count in counts.items():
            label_counts[label] = label_counts.get(label, 0) + count

        file_reports.append({
            "path": str(path),
            "rows": rows,
            "labels": counts,
        })

    report = {
        "dataset": str(dataset),
        "files": len(files),
        "rows": total_rows,
        "labels": label_counts,
        "files_detail": file_reports,
    }

    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print(f"Dataset: {dataset}")
    print(f"Files: {len(files)}")
    print(f"Non-empty rows: {total_rows:,}")
    print(f"Detected labels: {label_counts}")
    print(f"Report: {args.report}")


if __name__ == "__main__":
    main()
