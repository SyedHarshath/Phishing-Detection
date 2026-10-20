"""Read-only extraction of prediction/feedback disagreements.

This creates review artifacts only. It never changes the SQLite database or
registers a training batch; the established lifecycle exporter remains the
only way to create a worker-consumable batch.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sqlite3
import sys
from collections import Counter
from pathlib import Path

from mlops.dvc.validate_splits import _canonical_url, _domain

ROOT = Path(__file__).resolve().parents[2]
LABELS = {"phishing", "legitimate"}
FIELDS = (
    "id", "url", "prediction", "feedback", "confidence", "model_version",
    "timestamp", "feedback_timestamp", "verification_status",
    "label_provenance", "verified_label", "verified_by", "verified_at",
    "training_eligible",
)
TABLES = {"predictions", "predictions3"}


def normalize_label(value: object) -> str | None:
    """Normalize only the project's two canonical labels; reject aliases."""
    if not isinstance(value, str):
        return None
    label = value.strip().casefold()
    return label if label in LABELS else None


def _table(conn: sqlite3.Connection, requested: str | None) -> str:
    available = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if requested is not None:
        if requested not in TABLES or requested not in available:
            raise ValueError(f"requested table is unavailable: {requested}")
        return requested
    candidates = sorted(TABLES & available)
    if len(candidates) != 1:
        raise ValueError("select exactly one existing predictions table with --table")
    return candidates[0]


def _load_split_domains(dataset_dir: Path) -> tuple[dict[str, set[str]], list[str]]:
    """Read train/val/test in the repository's label<TAB>URL format."""
    domains: dict[str, set[str]] = {name: set() for name in ("train", "val", "test")}
    problems: list[str] = []
    for split in domains:
        path = dataset_dir / f"{split}.txt"
        if not path.is_file():
            problems.append(f"{split}_split_missing")
            continue
        malformed = 0
        try:
            with path.open("r", encoding="utf-8") as stream:
                for line in stream:
                    if not line.strip():
                        continue
                    label, sep, url = line.rstrip("\r\n").partition("\t")
                    host = _domain(url.strip()) if sep and normalize_label(label) else None
                    if not sep or not url.strip() or host is None:
                        malformed += 1
                        continue
                    domains[split].add(host)
        except (OSError, UnicodeError):
            problems.append(f"{split}_split_unreadable")
            continue
        if malformed:
            problems.append(f"{split}_split_malformed_records:{malformed}")
    return domains, problems


def extract(db_path: Path | str, output_dir: Path | str, *, table: str | None = None,
            dataset_dir: Path | str = ROOT / "dataset" / "big_dataset") -> dict:
    """Export disagreements and eligible subset from a SQLite DB opened read-only."""
    db = Path(db_path).resolve(strict=True)
    out = Path(output_dir)
    if out.exists():
        raise FileExistsError(f"output directory already exists: {out}")

    conn = sqlite3.connect(db.as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        source = _table(conn, table)
        actual_columns = {row[1] for row in conn.execute(f"PRAGMA table_info({source})")}
        missing = set(FIELDS) - actual_columns
        if missing:
            raise ValueError(f"prediction table lacks required fields: {', '.join(sorted(missing))}")
        # Table names are selected from the fixed allowlist above.
        rows = conn.execute(f"SELECT {','.join(FIELDS)} FROM {source} ORDER BY id").fetchall()
    finally:
        conn.close()

    domains, split_problems = _load_split_domains(Path(dataset_dir))
    review: list[dict] = []
    invalid_rows = feedback_rows = pending_feedback = pending_disagreements = 0
    duplicate_urls: Counter[str] = Counter()
    for row in rows:
        record = dict(row)
        raw_feedback = record["feedback"]
        if raw_feedback is None or (isinstance(raw_feedback, str) and not raw_feedback.strip()):
            continue
        feedback_rows += 1
        is_pending = (record["verification_status"] or "").strip().casefold() == "pending_verification"
        if is_pending:
            pending_feedback += 1
        prediction_label = normalize_label(record["prediction"])
        feedback_label = normalize_label(raw_feedback)
        url = record["url"]
        host = _domain(url.strip()) if isinstance(url, str) and url.strip() else None
        try:
            confidence_valid = math.isfinite(float(record["confidence"]))
            id_valid = int(record["id"]) > 0
        except (TypeError, ValueError, OverflowError):
            confidence_valid = id_valid = False
        if prediction_label is None or feedback_label is None or host is None or not confidence_valid or not id_valid:
            invalid_rows += 1
            continue
        canonical = _canonical_url(url.strip())
        if canonical is None:
            invalid_rows += 1
            continue
        duplicate_urls[canonical] += 1
        if prediction_label == feedback_label:
            continue
        record["normalized_prediction"] = prediction_label
        record["normalized_feedback"] = feedback_label
        review.append(record)
        if is_pending:
            pending_disagreements += 1

    # A repeated URL is retained for review but none of its duplicate records
    # is eligible for the candidate subset.
    duplicate_rows = sum(count - 1 for count in duplicate_urls.values() if count > 1)
    duplicate_keys = {url for url, count in duplicate_urls.items() if count > 1}
    blocked_domains = domains["val"] | domains["test"]
    train_canonicals: set[str] = set()
    train_path = Path(dataset_dir) / "train.txt"
    if train_path.is_file():
        try:
            with train_path.open("r", encoding="utf-8") as stream:
                for line in stream:
                    label, sep, url = line.rstrip("\r\n").partition("\t")
                    if sep and normalize_label(label) and _domain(url.strip()):
                        canonical = _canonical_url(url.strip())
                        if canonical:
                            train_canonicals.add(canonical)
        except (OSError, UnicodeError):
            split_problems.append("train_split_unreadable")

    approved: list[tuple[dict, str]] = []
    blocked_by_overlap = 0
    if not split_problems:
        for record in review:
            target = normalize_label(record["verified_label"])
            canonical = _canonical_url(record["url"].strip())
            host = _domain(record["url"].strip())
            eligible = (
                (record["verification_status"] or "").strip().casefold() == "verified"
                and record["training_eligible"] in (1, "1")
                and target is not None
                and isinstance(record["label_provenance"], str)
                and bool(record["label_provenance"].strip())
            )
            if not eligible:
                continue
            if canonical in duplicate_keys or canonical in train_canonicals or host in blocked_domains:
                blocked_by_overlap += 1
                continue
            approved.append((record, target))

    out.mkdir(parents=True, exist_ok=False)
    review_path = out / "disagreement-review.csv"
    approved_path = out / "approved-training-eligible.tsv"
    provenance_path = out / "approved-training-provenance.jsonl"
    summary_path = out / "summary.json"
    review_fields = [*FIELDS, "normalized_prediction", "normalized_feedback"]
    with review_path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=review_fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(review)
    with approved_path.open("w", encoding="utf-8", newline="\n") as stream:
        for record, target in approved:
            stream.write(f"{target}\t{record['url'].strip()}\n")
    with provenance_path.open("w", encoding="utf-8", newline="\n") as stream:
        for record, target in approved:
            stream.write(json.dumps({"id": record["id"], "target_label": target,
                                    "label_provenance": record["label_provenance"],
                                    "verified_by": record["verified_by"],
                                    "verified_at": record["verified_at"]}, ensure_ascii=False) + "\n")

    summary = {
        "database": str(db), "table": source, "total_records": len(rows),
        "records_with_feedback": feedback_rows, "disagreements": len(review),
        "invalid_feedback_rows": invalid_rows, "duplicate_feedback_urls": duplicate_rows,
        "pending_verification_feedback_records": pending_feedback,
        "pending_verification_disagreements": pending_disagreements,
        "approved_training_examples": len(approved),
        "eligible_rows_blocked_by_split_overlap_or_duplicates": blocked_by_overlap,
        "approved_subset_blocked_by_split_check": bool(split_problems),
        "split_check_problems": split_problems,
        "review_file": str(review_path), "approved_subset_file": str(approved_path),
        "approved_provenance_file": str(provenance_path),
        "note": "Feedback disagreements are proposed labels. Only verified, eligible, provenance-bearing records are included in the approved subset. This output is not registered as a lifecycle batch.",
    }
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True, help="SQLite file; opened read-only")
    parser.add_argument("--table", choices=sorted(TABLES), help="choose when both supported tables exist")
    parser.add_argument("--output-dir", type=Path, required=True, help="must be a new directory")
    parser.add_argument("--dataset-dir", type=Path, default=ROOT / "dataset" / "big_dataset")
    args = parser.parse_args(argv)
    try:
        print(json.dumps(extract(args.db, args.output_dir, table=args.table,
                                 dataset_dir=args.dataset_dir), indent=2))
    except (OSError, ValueError, sqlite3.Error) as exc:
        print(f"extraction failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
