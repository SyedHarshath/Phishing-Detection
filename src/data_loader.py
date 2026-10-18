"""Bounded-memory loader for the project's tab-delimited URL data splits."""

from __future__ import annotations

import random
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

PARSER_VERSION = "binary-tab-loader-v1"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATASET_DIR = PROJECT_ROOT / "dataset" / "big_dataset"
LABEL_TO_ID = {"phishing": 0, "legitimate": 1}
MAX_RECORD_BYTES = 1024 * 1024


class DatasetFormatError(ValueError):
    """Raised when a split has no valid records or is not UTF-8 tabular data."""


@dataclass(frozen=True)
class DatasetSplit:
    urls: list[str]
    labels: list[int]
    split: str
    parsed_records: int
    malformed_records: int
    label_counts: dict[str, int]
    sample_size: int
    parser_version: str = PARSER_VERSION


def load_split(
    split: str,
    sample_size: int = 10_000,
    seed: int = 42,
    dataset_dir: Path | str | None = None,
) -> DatasetSplit:
    """Load a bounded, approximately class-balanced sample from one split.

    Sampling is a per-class reservoir sample, so only up to ``2 * sample_size``
    candidate rows are held while the source file is streamed. ``sample_size``
    is the maximum total number of returned records.
    """
    if split not in {"train", "val", "test"}:
        raise ValueError(f"Unknown split {split!r}; expected train, val, or test.")
    if sample_size < 1:
        raise ValueError("sample_size must be at least 1.")

    root = Path(dataset_dir) if dataset_dir is not None else DATASET_DIR
    path = root / f"{split}.txt"
    if not path.is_file():
        raise FileNotFoundError(f"Dataset split not found: {path}")

    # Refuse obvious binary/corrupt data before interpreting lines.
    with path.open("rb") as handle:
        prefix = handle.read(8192)
    if prefix and (b"\x00" in prefix or prefix.startswith((b"\x1f\x8b", b"PK\x03\x04"))):
        raise DatasetFormatError(
            f"{path} is binary, compressed, or zero-filled; it is not the "
            "documented UTF-8 tab-delimited split."
        )

    rng = random.Random(seed)
    capacity = sample_size
    reservoirs: dict[int, list[tuple[str, int]]] = {0: [], 1: []}
    seen = Counter()
    malformed = 0
    parsed_total = 0

    with path.open("rb") as handle:
        while True:
            raw = handle.readline(MAX_RECORD_BYTES + 1)
            if not raw:
                break
            if len(raw) > MAX_RECORD_BYTES and not raw.endswith(b"\n"):
                malformed += 1
                while raw and not raw.endswith(b"\n"):
                    raw = handle.readline(MAX_RECORD_BYTES + 1)
                continue
            line = raw.rstrip(b"\r\n")
            if not line:
                continue
            parts = line.split(b"\t")
            if len(parts) != 2 or not parts[1] or parts[0].lower() not in (
                b"phishing",
                b"legitimate",
            ):
                malformed += 1
                continue
            try:
                url = parts[1].decode("utf-8")
            except UnicodeDecodeError:
                malformed += 1
                continue

            label = LABEL_TO_ID[parts[0].decode("ascii").lower()]
            parsed_total += 1
            seen[label] += 1
            reservoir = reservoirs[label]
            if len(reservoir) < capacity:
                reservoir.append((url, label))
            else:
                index = rng.randrange(seen[label])
                if index < capacity:
                    reservoir[index] = (url, label)

    if parsed_total == 0:
        raise DatasetFormatError(
            f"No valid label<TAB>URL records found in {path}; "
            f"malformed records: {malformed}."
        )

    target = min(sample_size, parsed_total)
    per_class = target // 2
    selected = []
    for label in (0, 1):
        selected.extend(reservoirs[label][:per_class])

    remaining = target - len(selected)
    if remaining:
        selected_labels = {
            0: sum(row[1] == 0 for row in selected),
            1: sum(row[1] == 1 for row in selected),
        }
        extras = []
        for label in (0, 1):
            extras.extend(reservoirs[label][selected_labels[label] :])
        rng.shuffle(extras)
        selected.extend(extras[:remaining])

    rng.shuffle(selected)
    return DatasetSplit(
        urls=[url for url, _ in selected],
        labels=[label for _, label in selected],
        split=split,
        parsed_records=parsed_total,
        malformed_records=malformed,
        label_counts={
            "phishing": int(seen[0]),
            "legitimate": int(seen[1]),
        },
        sample_size=len(selected),
    )
