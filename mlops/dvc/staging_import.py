"""Stage operator-supplied datasets without touching active dataset paths.

The importer verifies identity and a supplied provenance attestation. It cannot
prove the truth of that attestation; authoritative registration is separate.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import tarfile
import tempfile
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from mlops.dvc.validate_splits import LABELS, _domain, validate_dataset

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STAGING = ROOT / "mlops" / "dvc" / "staging"
REQUIRED_MANIFEST_FIELDS = ("source_name", "source_reference", "version", "acquired_at_utc",
                            "acquisition_method", "source_attestation_reference", "files", "splits")


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _safe_relpath(raw: str) -> PurePosixPath:
    path = PurePosixPath(raw.replace("\\", "/"))
    if path.is_absolute() or not path.parts or any(part in ("", ".", "..") for part in path.parts):
        raise ValueError(f"unsafe manifest path: {raw!r}")
    return path


def _manifest(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    missing = [key for key in REQUIRED_MANIFEST_FIELDS if key not in data]
    if missing:
        raise ValueError(f"provenance manifest missing fields: {', '.join(missing)}")
    if not all(isinstance(data.get(k), str) and data[k].strip() for k in REQUIRED_MANIFEST_FIELDS[:6]):
        raise ValueError("source identity and attestation fields must be non-empty strings")
    acquired = datetime.fromisoformat(data["acquired_at_utc"].replace("Z", "+00:00"))
    if acquired.tzinfo is None:
        raise ValueError("acquired_at_utc must include a timezone")
    if acquired > datetime.now(timezone.utc):
        raise ValueError("acquired_at_utc cannot be in the future")
    if data.get("record_format") != "UTF-8 label<TAB>URL, one record per line":
        raise ValueError("unsupported or missing record_format")
    if data.get("labels") != {"phishing": 0, "legitimate": 1}:
        raise ValueError("labels must map phishing=0 and legitimate=1")
    if not isinstance(data["files"], dict) or not data["files"]:
        raise ValueError("files must map relative paths to SHA-256 checksums")
    files = {}
    for raw, digest in data["files"].items():
        rel = _safe_relpath(raw)
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest.lower()):
            raise ValueError(f"invalid SHA-256 for {raw!r}")
        files[rel.as_posix()] = digest.lower()
    if not isinstance(data["splits"], dict) or set(data["splits"]) != {"train", "val", "test"}:
        raise ValueError("splits must define exactly train, val, and test")
    for split, raw in data["splits"].items():
        rel = _safe_relpath(raw).as_posix()
        if rel not in files:
            raise ValueError(f"split {split} references an unhashed file")
        data["splits"][split] = rel
    data["files"] = files
    return data


def _open_source(source: Path, rel: str, temp_root: Path):
    """Return a readable stream from a directory, zip or tar without extracting paths."""
    if source.is_dir():
        target = source / Path(*PurePosixPath(rel).parts)
        current = source
        for part in PurePosixPath(rel).parts:
            current = current / part
            if current.is_symlink(): raise ValueError(f"symlinked source path is not allowed: {rel}")
        if source.resolve() not in target.resolve().parents or target.is_symlink() or not target.is_file():
            raise ValueError(f"missing, symlinked or escaping source file: {rel}")
        return target.open("rb")
    if zipfile.is_zipfile(source):
        archive = zipfile.ZipFile(source)
        try:
            info = archive.getinfo(rel)
            mode = (info.external_attr >> 16) & 0o170000
            if mode == 0o120000 or info.is_dir():
                raise ValueError(f"archive member is not a regular file: {rel}")
            return _ClosingReader(archive.open(info, "r"), archive)
        except Exception:
            archive.close()
            raise
    if tarfile.is_tarfile(source):
        archive = tarfile.open(source, "r:*")
        try:
            member = archive.getmember(rel)
            if not member.isfile():
                raise ValueError(f"archive member is not a regular file: {rel}")
            stream = archive.extractfile(member)
            if stream is None:
                raise ValueError(f"cannot read archive member: {rel}")
            return _ClosingReader(stream, archive)
        except Exception:
            archive.close()
            raise
    raise ValueError("source must be a directory, .zip, or tar archive")


class _ClosingReader:
    def __init__(self, stream, archive): self.stream, self.archive = stream, archive
    def __enter__(self): return self
    def __exit__(self, *args): self.stream.close(); self.archive.close()
    def read(self, *args): return self.stream.read(*args)


def _copy_verified(source: Path, rel: str, target: Path, expected_sha: str, temp_root: Path):
    h = hashlib.sha256()
    target.parent.mkdir(parents=True, exist_ok=True)
    with _open_source(source, rel, temp_root) as src, target.open("xb") as dst:
        while True:
            chunk = src.read(1024 * 1024)
            if not chunk: break
            h.update(chunk); dst.write(chunk)
    actual = h.hexdigest()
    if actual != expected_sha:
        target.unlink(missing_ok=True)
        raise ValueError(f"source checksum mismatch for {rel}: expected {expected_sha}, got {actual}")
    try: target.chmod(0o444)
    except OSError: pass
    return actual


def _normalize_split(original: Path, normalized: Path, split: str, max_line_bytes=1_048_576):
    stats = {"lines": 0, "records": 0, "empty_records": 0, "malformed_records": 0,
             "invalid_urls": 0, "label_counts": {"phishing": 0, "legitimate": 0}, "exclusion_reason_counts": {}}
    exclusion_path = normalized.parent / f"{split}.exclusions.jsonl"
    exclusion_digest = hashlib.sha256()
    with original.open("rb") as src, normalized.open("xb") as dst, exclusion_path.open("xb") as excluded:
        line_number = 0
        while True:
            raw = src.readline(max_line_bytes + 1)
            if not raw: break
            line_number += 1
            stats["lines"] += 1
            line_hash = hashlib.sha256(raw)
            reason = None
            if len(raw) > max_line_bytes:
                reason = "overlong"
                while raw and not raw.endswith(b"\n"):
                    raw = src.readline(max_line_bytes + 1)
                    line_hash.update(raw)
            elif b"\x00" in raw: reason = "nul_byte"
            else:
                try: line = raw.decode("utf-8").rstrip("\r\n")
                except UnicodeDecodeError: line = None; reason = "invalid_utf8"
                if reason is None and line == "":
                    stats["empty_records"] += 1
                    stats["exclusion_reason_counts"]["empty_record"] = stats["exclusion_reason_counts"].get("empty_record", 0) + 1
                    exclusion_line = json.dumps({"line": line_number, "reason": "empty_record",
                        "source_line_sha256": line_hash.hexdigest()}, separators=(",", ":")).encode() + b"\n"
                    excluded.write(exclusion_line); exclusion_digest.update(exclusion_line)
                    continue
                if reason is None:
                    label, sep, url = line.partition("\t")
                    label, url = label.strip().lower(), url.strip()
                    if not sep or "\t" in url or not url or label not in LABELS: reason = "invalid_record_schema_or_label"
                    else:
                        if _domain(url) is None:
                            stats["invalid_urls"] += 1
                            reason = "invalid_http_url"
            if reason:
                stats["malformed_records"] += 1
                stats["exclusion_reason_counts"][reason] = stats["exclusion_reason_counts"].get(reason, 0) + 1
                exclusion_line = json.dumps({"line": line_number, "reason": reason,
                    "source_line_sha256": line_hash.hexdigest()}, separators=(",", ":")).encode() + b"\n"
                excluded.write(exclusion_line); exclusion_digest.update(exclusion_line)
                continue
            stats["records"] += 1
            stats["label_counts"][label] += 1
            dst.write(f"{label}\t{url}\n".encode("utf-8"))
    stats["exclusions_file"] = exclusion_path.name
    stats["exclusions_sha256"] = exclusion_digest.hexdigest()
    return stats


def import_dataset(source: Path, manifest_path: Path, *, staging_root: Path = DEFAULT_STAGING) -> dict:
    source, manifest_path = Path(source), Path(manifest_path)
    if not source.exists() or not manifest_path.is_file(): raise FileNotFoundError("source and provenance manifest must exist")
    manifest = _manifest(manifest_path)
    source_sha = _sha(source) if source.is_file() else None
    if source.is_file():
        expected_container_sha = manifest.get("source_container_sha256")
        if not isinstance(expected_container_sha, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", expected_container_sha):
            raise ValueError("archive import requires source_container_sha256 in the provenance manifest")
        if source_sha != expected_container_sha.lower():
            raise ValueError("source archive checksum does not match provenance manifest")
    manifest_sha = _sha(manifest_path)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    stage = Path(staging_root) / f"stage-{stamp}-{uuid.uuid4().hex[:8]}"
    stage.mkdir(parents=True, exist_ok=False)
    try:
        original_root = stage / "original"
        actual_files = {}
        for rel, expected in sorted(manifest["files"].items()):
            target = original_root / Path(*PurePosixPath(rel).parts)
            actual_files[rel] = _copy_verified(source, rel, target, expected, stage)
        manifest_copy = {**manifest, "manifest_sha256": manifest_sha,
                         "source_container_sha256": source_sha,
                         "source_content_verified": True,
                         "source_origin_cryptographically_verified": False}
        source_manifest_copy = stage / "source_provenance_manifest.json"
        shutil.copyfile(manifest_path, source_manifest_copy)
        try: source_manifest_copy.chmod(0o444)
        except OSError: pass
        (stage / "provenance_manifest.json").write_text(json.dumps(manifest_copy, indent=2), encoding="utf-8")
        normalized = stage / "normalized"
        normalized.mkdir()
        split_stats = {}
        split_paths = {}
        for split, rel in manifest["splits"].items():
            dst = normalized / f"{split}.txt"
            split_stats[split] = _normalize_split(original_root / Path(*PurePosixPath(rel).parts), dst, split)
            split_paths[split] = dst
        validation = validate_dataset(normalized, split_paths=split_paths)
        report = {"schema_version": 1, "status": "staged", "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "stage_id": stage.name, "source": {k: manifest_copy.get(k) for k in
                ("source_name", "source_reference", "version", "acquired_at_utc", "acquisition_method",
                 "source_attestation_reference", "manifest_sha256", "source_container_sha256",
                 "source_content_verified", "source_origin_cryptographically_verified")},
            "original_files": [{"path": rel, "sha256": digest, "size_bytes": (original_root / Path(*PurePosixPath(rel).parts)).stat().st_size}
                               for rel, digest in actual_files.items()],
            "normalized_splits": split_stats,
            "normalized_sha256": {split: _sha(path) for split, path in split_paths.items()},
            "validation": validation,
            "transformations": ["UTF-8 decoding", "strip record boundary whitespace", "lowercase label", "normalize line endings to LF"],
            "registration": {"approved": False, "authoritative_for_training": False},
            "limitations": ["Hashes prove identity, not source origin.",
                            "No source was fetched; URLs in records were not visited.",
                            "Operator review is required before authoritative registration."]}
        (stage / "inventory.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        (stage / "inventory.md").write_text(_render_report(report), encoding="utf-8")
        return report
    except Exception as exc:
        (stage / "IMPORT_INCOMPLETE.json").write_text(json.dumps({"status": "incomplete",
            "error": f"{type(exc).__name__}: {exc}", "created_at_utc": datetime.now(timezone.utc).isoformat()}, indent=2), encoding="utf-8")
        raise


def _render_report(report):
    rows = ["# Staged dataset inventory", "", f"- Status: **{report['status']}**",
            f"- Stage ID: `{report['stage_id']}`", f"- Source: `{report['source']['source_name']}` version `{report['source']['version']}`",
            f"- Manifest SHA-256: `{report['source']['manifest_sha256']}`", "",
            "| Split | Lines | Records | Phishing | Legitimate | Malformed | Invalid URL | Normalized SHA-256 |",
            "|---|---:|---:|---:|---:|---:|---:|---|"]
    for split, stat in report["normalized_splits"].items():
        rows.append(f"| {split} | {stat['lines']} | {stat['records']} | {stat['label_counts']['phishing']} | {stat['label_counts']['legitimate']} | {stat['malformed_records']} | {stat['invalid_urls']} | `{report['normalized_sha256'][split]}` |")
    rows += ["", f"- Strict validation passed: `{report['validation'].get('passed', False)}`",
             f"- Authoritative registration approved: `{report['registration']['approved']}`", "",
             "Hashes establish file identity only. Review source evidence and validation before separate operator approval.", ""]
    return "\n".join(rows)


def approve_authoritative(stage: Path, *, operator: str, approval_reference: str,
                          reviewed_exclusions: bool = False) -> dict:
    stage = Path(stage).resolve()
    inventory_path = stage / "inventory.json"
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    if not operator.strip() or not approval_reference.strip(): raise ValueError("operator and approval_reference are required")
    if inventory.get("status") != "staged" or inventory.get("validation", {}).get("passed") is not True:
        raise ValueError("only a completed, passing staged dataset can be registered")
    exclusions = sum(item.get("empty_records", 0) + item.get("malformed_records", 0)
                     for item in inventory.get("normalized_splits", {}).values())
    if exclusions and not reviewed_exclusions:
        raise ValueError("operator must explicitly attest that staged exclusions were reviewed")
    manifest_path = stage / "provenance_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    source_manifest_path = stage / "source_provenance_manifest.json"
    if _sha(source_manifest_path) != inventory.get("source", {}).get("manifest_sha256"):
        raise ValueError("original staged provenance manifest changed after import")
    if manifest.get("manifest_sha256") != inventory.get("source", {}).get("manifest_sha256"):
        raise ValueError("staged provenance manifest differs from imported inventory")
    source_manifest = _manifest(source_manifest_path)
    if any(manifest.get(key) != source_manifest.get(key) for key in REQUIRED_MANIFEST_FIELDS):
        raise ValueError("staged provenance metadata changed after import")
    for item in inventory.get("original_files", []):
        original = stage / "original" / Path(*PurePosixPath(item["path"]).parts)
        if (not original.is_file() or _sha(original) != item.get("sha256")
                or _sha(original) != manifest.get("files", {}).get(item["path"])):
            raise ValueError(f"staged original changed after import: {item.get('path')}")
    for split, expected in inventory.get("normalized_sha256", {}).items():
        normalized = stage / "normalized" / f"{split}.txt"
        if not normalized.is_file() or _sha(normalized) != expected:
            raise ValueError(f"normalized split changed after import: {split}")
    validation = validate_dataset(stage / "normalized")
    if validation.get("passed") is not True:
        raise ValueError("staged dataset no longer passes strict validation")
    registration = {"approved": True, "authoritative_for_training": True,
        "operator": operator.strip(), "approval_reference": approval_reference.strip(),
        "exclusions_reviewed": bool(reviewed_exclusions),
        "approved_at_utc": datetime.now(timezone.utc).isoformat(),
        "stage_id": inventory["stage_id"], "manifest_sha256": manifest["manifest_sha256"],
        "source_reference": manifest["source_reference"],
        "verification_evidence": approval_reference.strip(), "verified_by": operator.strip(),
        "verified": True, "split_sha256": inventory["normalized_sha256"],
        "normalized_sha256": inventory["normalized_sha256"]}
    target = stage / "registration.json"
    if target.exists(): raise FileExistsError("registration already exists; inspect it before taking further action")
    target.write_text(json.dumps(registration, indent=2), encoding="utf-8")
    inventory["registration"] = registration
    inventory_path.write_text(json.dumps(inventory, indent=2), encoding="utf-8")
    (stage / "inventory.md").write_text(_render_report(inventory), encoding="utf-8")
    return registration


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    imp = sub.add_parser("import")
    imp.add_argument("source", type=Path); imp.add_argument("--manifest", required=True, type=Path)
    imp.add_argument("--staging-root", type=Path, default=DEFAULT_STAGING)
    approve = sub.add_parser("approve")
    approve.add_argument("stage", type=Path); approve.add_argument("--operator", required=True)
    approve.add_argument("--approval-reference", required=True)
    approve.add_argument("--reviewed-exclusions", action="store_true")
    args = parser.parse_args()
    result = import_dataset(args.source, args.manifest, staging_root=args.staging_root) if args.command == "import" else approve_authoritative(args.stage, operator=args.operator, approval_reference=args.approval_reference, reviewed_exclusions=args.reviewed_exclusions)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__": raise SystemExit(main())
