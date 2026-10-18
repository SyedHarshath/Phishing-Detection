"""Read-only dataset inventory with fast and disk-backed full modes.

Fast mode streams files once for hashes, line/label counts, and parse errors;
full mode delegates exact URL and host-overlap indexing to validate_splits.
Reports are written as ``running`` partial records and atomically finalized so
interrupted scans cannot look complete.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import yaml

from mlops.dvc.validate_splits import SPLITS, _domain, validate_dataset

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATASET = ROOT / "dataset" / "big_dataset"
DEFAULT_OUTPUT = ROOT / "mlops" / "dvc" / "inventories"
TOOL_VERSION = "optimus-inventory-v1"
FAST_LABELS = {b"phishing", b"legitimate"}
MAX_RECORD_BYTES = 1_048_576


def _utc():
    return datetime.now(timezone.utc).isoformat()


def _digest_file(path: Path) -> tuple[str, str]:
    sha, md5 = hashlib.sha256(), hashlib.md5()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            sha.update(chunk); md5.update(chunk)
    return sha.hexdigest(), md5.hexdigest()


def _source_files(dataset: Path, pointer: Path | None, cache_root: Path | None,
                  from_dvc_cache: bool = False):
    if pointer is None:
        return {p.name: p for p in sorted(dataset.iterdir()) if p.is_file()}, None
    pointer_data = yaml.safe_load(pointer.read_text(encoding="utf-8"))
    outs = pointer_data.get("outs") or []
    if not outs:
        raise ValueError("DVC pointer has no outs entry")
    directory_md5 = outs[0].get("md5")
    if not directory_md5 or not directory_md5.endswith(".dir"):
        raise ValueError("DVC pointer does not reference a directory object")
    cache_root = cache_root or (pointer.parent.parent / ".dvc" / "cache")
    object_path = cache_root / "files" / "md5" / directory_md5[:2] / directory_md5[2:]
    entries = json.loads(object_path.read_text(encoding="utf-8"))
    cached_files = {entry["relpath"]: cache_root / "files" / "md5" /
                    entry["md5"][:2] / entry["md5"][2:] for entry in entries}
    files = cached_files if from_dvc_cache else {p.name: p for p in sorted(dataset.iterdir()) if p.is_file()}
    return files, {"pointer_path": str(pointer), "pointer_directory_md5": directory_md5,
                   "pointer_size_bytes": outs[0].get("size"), "pointer_file_count": outs[0].get("nfiles"),
                   "cache_directory_object": str(object_path), "cache_directory_object_exists": object_path.is_file(),
                   "pointer_entries": [{"path": entry["relpath"], "md5": entry["md5"]} for entry in entries],
                   "inventory_source": "dvc_cache_snapshot" if from_dvc_cache else "workspace"}


def _inventory_one(path: Path, relpath: str, progress=None, interval=100_000):
    sha, md5 = hashlib.sha256(), hashlib.md5()
    counts = Counter(); parsed = malformed = empty = invalid_urls = lines = 0
    invalid_labels = missing_urls = overlong = nul_bytes = 0
    started = time.monotonic()
    with path.open("rb") as stream:
        while True:
            raw = stream.readline(MAX_RECORD_BYTES + 1)
            if not raw:
                break
            sha.update(raw); md5.update(raw); lines += 1; nul_bytes += raw.count(b"\x00")
            if len(raw) > MAX_RECORD_BYTES:
                overlong += 1; malformed += 1
                while raw and not raw.endswith(b"\n"):
                    raw = stream.readline(MAX_RECORD_BYTES + 1)
                    sha.update(raw); md5.update(raw); nul_bytes += raw.count(b"\x00")
                if progress and lines % interval == 0:
                    progress(relpath, lines)
                continue
            line = raw.rstrip(b"\r\n")
            if not line:
                empty += 1
            else:
                label_b, sep, url_b = line.partition(b"\t")
                if not sep:
                    malformed += 1
                elif label_b.strip().lower() not in FAST_LABELS:
                    malformed += 1; invalid_labels += 1
                elif b"\t" in url_b:
                    malformed += 1
                elif not url_b.strip():
                    malformed += 1; missing_urls += 1
                else:
                    try:
                        url = url_b.decode("utf-8").strip()
                    except UnicodeDecodeError:
                        malformed += 1
                    else:
                        label = label_b.decode("ascii").strip().lower()
                        if _domain(url) is None:
                            invalid_urls += 1
                        parsed += 1; counts[label] += 1
            if progress and lines % interval == 0:
                progress(relpath, lines)
    stat = path.stat()
    return {"path": relpath, "size_bytes": stat.st_size,
            "mtime_utc": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
            "sha256": sha.hexdigest(), "dvc_md5": md5.hexdigest(), "line_count": lines,
            "parsed_record_count": parsed, "label_counts": dict(counts),
            "empty_records": empty, "malformed_records": malformed,
            "invalid_label_records": invalid_labels, "missing_url_records": missing_urls,
            "overlong_records": overlong, "nul_bytes": nul_bytes,
            "invalid_url_records": invalid_urls,
            "elapsed_seconds": round(time.monotonic() - started, 3)}


def _pointer_comparison(files: dict[str, Path], pointer_info: dict | None, inventory_files: list[dict]):
    if not pointer_info:
        return None
    expected = {}
    expected = {entry["path"]: entry["md5"] for entry in pointer_info["pointer_entries"]}
    actual = {item["path"]: item["dvc_md5"] for item in inventory_files}
    return {"pointer_file_set_matches": set(expected) == set(actual),
            "files_matching_pointer_md5": sorted(name for name, value in expected.items() if actual.get(name) == value),
            "files_differing_from_pointer_md5": sorted(name for name, value in expected.items() if actual.get(name) != value),
            "untracked_local_files": sorted(set(actual) - set(expected))}


def _render_markdown(report: dict) -> str:
    lines = ["# Dataset inventory", "", f"- Status: **{report['status']}**",
             f"- Mode: `{report['mode']}`", f"- Source: `{report['source']}`",
             f"- Generated UTC: `{report['generated_at_utc']}`",
             f"- Provenance: **{report['provenance_status']}**",
             f"- Inventory fingerprint: `{report.get('inventory_fingerprint', 'incomplete')}`", "",
             "| Path | Bytes | Lines | Parsed | Phishing | Legitimate | Malformed | Empty | Overlong | NUL bytes | Invalid URLs | SHA-256 |",
             "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|"]
    for item in report.get("files", []):
        counts = item.get("label_counts", {})
        lines.append(f"| `{item['path']}` | {item['size_bytes']} | {item['line_count']} | {item['parsed_record_count']} | {counts.get('phishing', 0)} | {counts.get('legitimate', 0)} | {item['malformed_records']} | {item['empty_records']} | {item['overlong_records']} | {item['nul_bytes']} | {item['invalid_url_records']} | `{item['sha256']}` |")
    if report.get("pointer_comparison"):
        lines.extend(["", "## DVC pointer comparison", "",
                      "```json", json.dumps(report["pointer_comparison"], indent=2), "```"])
    full = report.get("full_validation")
    lines.extend(["", "## Overlap checks", ""])
    if full:
        lines.append(f"- Exact URL split-overlap scan: {'completed' if full.get('completed') else 'incomplete'}")
        lines.append(f"- Hostname overlap records: {full.get('cross_split', {}).get('shared_domain_records', 'unknown')}")
        lines.append(f"- Registrable-domain overlap: {report.get('registrable_domain_overlap', {}).get('status', 'unknown')}")
        lines.append(f"- Validation input fingerprint: `{full.get('input_fingerprint', 'unknown')}`")
        lines.append(f"- Public suffix list: `{full.get('public_suffix_list', {}).get('version', 'not supplied')}` / `{full.get('public_suffix_list', {}).get('sha256', 'no checksum')}`")
        for metric in ("exact_url_overlap_by_split", "canonical_url_overlap_by_split",
                       "hostname_overlap_by_split", "registrable_domain_overlap_by_split"):
            pairs = full.get("cross_split", {}).get(metric, {})
            lines.append(f"- {metric}: `{json.dumps(pairs, sort_keys=True)}`")
    else:
        lines.append("- Exact URL and cross-split overlap scan: **not run in fast mode**")
        lines.append("- Registrable-domain overlap: **not run**")
    lines.extend(["", "## Limitations", "", *[f"- {item}" for item in report.get("limitations", [])], ""])
    return "\n".join(lines)


def create_inventory(*, dataset: Path = DEFAULT_DATASET, output_dir: Path = DEFAULT_OUTPUT,
                     mode: str = "fast", pointer: Path | None = None,
                     cache_root: Path | None = None, from_dvc_cache: bool = False,
                     source: str | None = None,
                     provenance_status: str = "unknown", progress_interval: int = 100_000,
                     psl_path: Path | None = None, psl_sha256: str | None = None,
                     psl_version: str | None = None) -> dict:
    if mode not in {"fast", "full"}:
        raise ValueError("mode must be fast or full")
    if progress_interval <= 0:
        raise ValueError("progress_interval must be positive")
    files, pointer_info = _source_files(dataset, pointer, cache_root, from_dvc_cache)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / f"inventory-{stamp}-{uuid.uuid4().hex[:8]}.json"
    markdown_path = report_path.with_suffix(".md")
    progress_path = report_path.with_suffix(".partial.json")
    report = {"schema_version": 1, "tool_version": TOOL_VERSION, "mode": mode,
        "status": "running", "started_at_utc": _utc(), "generated_at_utc": None,
        "source": source or str(dataset), "source_path": str(dataset),
        "provenance_status": provenance_status, "files": [], "pointer": pointer_info,
        "pointer_comparison": None, "full_validation": None,
        "registrable_domain_overlap": {"status": "unavailable",
            "reason": "No pinned public-suffix-list data or PSL-aware dependency is installed."},
        "schema": {"record_format": "UTF-8 line based label<TAB>URL", "labels": {"phishing": 0, "legitimate": 1}},
        "preprocessing_version": "url-character-lowercase-pad200-v1",
        "limitations": ["Fast mode does not perform duplicate or cross-split overlap indexing.",
                        "DVC hash agreement establishes a tracked snapshot, not original source or incumbent training history.",
                        "Registrable-domain overlap requires a pinned public suffix list, unavailable in this environment."]}
    progress_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    def progress(name, count):
        print(f"[{mode}] {name}: processed {count:,} records", flush=True)
        report["progress"] = {"file": name, "processed_records": count, "updated_at_utc": _utc()}
        progress_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    try:
        for name, path in files.items():
            if not path.is_file():
                raise FileNotFoundError(f"inventory source missing: {path}")
            report["files"].append(_inventory_one(path, name, progress, progress_interval))
        report["pointer_comparison"] = _pointer_comparison(files, pointer_info, report["files"])
        if mode == "full":
            split_paths = {split: files[f"{split}.txt"] for split in SPLITS}
            full = validate_dataset(dataset, progress=lambda split, n: progress(f"full:{split}", n),
                                    progress_interval=progress_interval, split_paths=split_paths,
                                    psl_path=psl_path, psl_sha256=psl_sha256, psl_version=psl_version)
            full["completed"] = True
            report["full_validation"] = full
            report["registrable_domain_overlap"] = full.get("registrable_domain_overlap", {"status": "unavailable"})
        if pointer_info:
            comp = report["pointer_comparison"]
            if comp["pointer_file_set_matches"] and not comp["files_differing_from_pointer_md5"]:
                report["provenance_status"] = ("pointer_cache_match" if pointer_info["inventory_source"] == "dvc_cache_snapshot"
                                                else "pointer_workspace_match")
            else:
                report["provenance_status"] = "pointer_mismatch"
        fingerprint_payload = [(item["path"], item["size_bytes"], item["sha256"]) for item in report["files"]]
        report["inventory_fingerprint"] = hashlib.sha256(json.dumps(fingerprint_payload, separators=(",", ":")).encode()).hexdigest()
        report["status"] = "completed"
        report["generated_at_utc"] = _utc()
        report["completed_at_utc"] = report["generated_at_utc"]
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        markdown_path.write_text(_render_markdown(report), encoding="utf-8")
        progress_path.unlink(missing_ok=True)
        return {"json_report": str(report_path), "markdown_report": str(markdown_path),
                "status": report["status"], "inventory_fingerprint": report["inventory_fingerprint"],
                "report": report}
    except Exception as exc:
        report["status"] = "incomplete"
        report["error"] = f"{type(exc).__name__}: {exc}"
        report["generated_at_utc"] = _utc()
        progress_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("fast", "full"), default="fast")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--pointer", type=Path)
    parser.add_argument("--cache-root", type=Path)
    parser.add_argument("--from-dvc-cache", action="store_true",
                        help="inventory files referenced by the pointer from local cache without restoring them")
    parser.add_argument("--source")
    parser.add_argument("--provenance-status", default="unknown")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--progress-interval", type=int, default=100_000)
    parser.add_argument("--psl-file", type=Path)
    parser.add_argument("--psl-sha256")
    parser.add_argument("--psl-version")
    args = parser.parse_args()
    result = create_inventory(dataset=args.dataset, output_dir=args.output_dir, mode=args.mode,
        pointer=args.pointer, cache_root=args.cache_root, source=args.source,
        from_dvc_cache=args.from_dvc_cache,
        provenance_status=args.provenance_status, progress_interval=args.progress_interval,
        psl_path=args.psl_file, psl_sha256=args.psl_sha256, psl_version=args.psl_version)
    print(json.dumps({k: v for k, v in result.items() if k != "report"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
