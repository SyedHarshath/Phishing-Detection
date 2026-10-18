"""Strict, streaming quality gates for the project's label<TAB>URL splits.

Run with ``python -m mlops.dvc.validate_splits``. Source files are read-only;
the JSON report is written separately. A temporary SQLite index bounds memory
while detecting exact URL duplicates and cross-split leakage.
"""
from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import sqlite3
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATASET = ROOT / "dataset" / "big_dataset"
DEFAULT_REPORT = ROOT / "mlops" / "dvc" / "validation_report.json"
SPLITS = ("train", "val", "test")
LABELS = {"phishing", "legitimate"}
MAX_LINE = 1_048_576


class PublicSuffixList:
    """A reproducible PSL resolver requiring an operator-pinned SHA-256."""
    def __init__(self, path: Path, expected_sha256: str, version: str):
        self.path = Path(path)
        self.sha256 = _sha256(self.path)
        if not expected_sha256 or self.sha256 != expected_sha256.lower():
            raise ValueError("public suffix list SHA-256 does not match the pinned value")
        if not version.strip():
            raise ValueError("public suffix list version or retrieval identifier is required")
        self.version = version
        self.rules, self.wildcards, self.exceptions = set(), set(), set()
        with self.path.open("r", encoding="utf-8") as stream:
            for raw in stream:
                rule = raw.strip().split("//", 1)[0].strip().lower().rstrip(".")
                if not rule: continue
                try:
                    if rule.startswith("!"):
                        self.exceptions.add(self._ascii(rule[1:]))
                    elif rule.startswith("*."):
                        self.wildcards.add(self._ascii(rule[2:]))
                    else:
                        self.rules.add(self._ascii(rule))
                except UnicodeError as exc:
                    raise ValueError(f"invalid PSL rule: {rule}") from exc
        if not self.rules:
            raise ValueError("PSL has no rules")

    @staticmethod
    def _ascii(domain):
        return ".".join(label.encode("idna").decode("ascii") for label in domain.split("."))

    def registrable_domain(self, hostname: str) -> str | None:
        try:
            ipaddress.ip_address(hostname.strip("[]"))
            return None
        except ValueError:
            pass
        try:
            host = self._ascii(hostname.lower().rstrip("."))
        except UnicodeError:
            return None
        labels = host.split(".")
        exception_len = max((len(rule.split(".")) for rule in self.exceptions
                             if host == rule or host.endswith("." + rule)), default=0)
        if exception_len:
            suffix_len = exception_len - 1
        else:
            suffix_len = 1  # PSL prevailing rule '*'
            for rule in self.rules:
                if host == rule or host.endswith("." + rule):
                    suffix_len = max(suffix_len, len(rule.split(".")))
            for rule in self.wildcards:
                if host.endswith("." + rule):
                    suffix_len = max(suffix_len, len(rule.split(".")) + 1)
        if len(labels) <= suffix_len:
            return None
        return ".".join(labels[-(suffix_len + 1):])


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _overlap_pair(report, metric, current_split, previous_split):
    pair = "|".join(sorted((current_split, previous_split)))
    counts = report["cross_split"].setdefault(metric + "_by_split", {})
    counts[pair] = counts.get(pair, 0) + 1


def _domain(url: str) -> str | None:
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower().rstrip(".")
        if parts.scheme.lower() not in {"http", "https"} or not host:
            return None
        return host[4:] if host.startswith("www.") else host
    except ValueError:
        return None


def _canonical_url(url: str) -> str | None:
    """Normalize URL scheme/host/default port and discard fragments."""
    try:
        parts = urlsplit(url)
        scheme = parts.scheme.lower()
        host = (parts.hostname or "").encode("idna").decode("ascii").lower().rstrip(".")
        if scheme not in {"http", "https"} or not host:
            return None
        port = parts.port
        if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
            host = f"{host}:{port}"
        path = parts.path or "/"
        return urlunsplit((scheme, host, path, parts.query, ""))
    except (ValueError, UnicodeError):
        return None


def validate_dataset(dataset: Path, progress=None, progress_interval: int = 100_000,
                    split_paths: dict[str, Path] | None = None,
                    psl_path: Path | None = None, psl_sha256: str | None = None,
                    psl_version: str | None = None) -> dict:
    if (psl_path or psl_sha256 or psl_version) and not (psl_path and psl_sha256 and psl_version):
        raise ValueError("PSL path, pinned SHA-256, and version are all required together")
    if progress_interval <= 0:
        raise ValueError("progress_interval must be positive")
    psl = PublicSuffixList(psl_path, psl_sha256, psl_version) if psl_path else None
    report = {"created_at_utc": datetime.now(timezone.utc).isoformat(),
              "dataset": str(dataset), "schema_version": 1, "splits": {},
              "cross_split": {}, "errors": [],
              "public_suffix_list": ({"version": psl.version, "sha256": psl.sha256,
                                        "source_path": str(psl.path), "status": "pinned"} if psl else
                                       {"status": "unavailable", "reason": "PSL path and pinned SHA-256 were not supplied"})}
    if not dataset.is_dir():
        return {**report, "errors": [f"Dataset directory missing: {dataset}"], "passed": False}

    # Temporary disk-backed index avoids retaining millions of URLs in RAM.
    with tempfile.TemporaryDirectory(prefix="optimus-validation-") as tmp:
        conn = sqlite3.connect(str(Path(tmp) / "urls.sqlite"))
        conn.execute("CREATE TABLE urls (url_hash TEXT PRIMARY KEY, url TEXT NOT NULL, split TEXT NOT NULL, label TEXT NOT NULL)")
        conn.execute("CREATE TABLE urls_by_split (url_hash TEXT NOT NULL, split TEXT NOT NULL, label TEXT NOT NULL, PRIMARY KEY(url_hash,split))")
        conn.execute("CREATE TABLE canonical_urls (url_hash TEXT PRIMARY KEY, url TEXT NOT NULL, split TEXT NOT NULL, label TEXT NOT NULL)")
        conn.execute("CREATE TABLE canonical_by_split (url_hash TEXT NOT NULL, split TEXT NOT NULL, label TEXT NOT NULL, PRIMARY KEY(url_hash,split))")
        conn.execute("CREATE TABLE domains (domain TEXT NOT NULL, split TEXT NOT NULL, PRIMARY KEY(domain,split))")
        conn.execute("CREATE TABLE registrable_domains (domain TEXT NOT NULL, split TEXT NOT NULL, PRIMARY KEY(domain,split))")
        for split in SPLITS:
            path = Path(split_paths[split]) if split_paths and split in split_paths else dataset / f"{split}.txt"
            detail = {"path": str(path), "size_bytes": path.stat().st_size if path.is_file() else 0,
                      "records": 0, "empty_lines": 0, "malformed": 0,
                      "invalid_urls": 0, "duplicate_urls": 0, "canonical_duplicate_urls": 0,
                      "label_counts": Counter(),
                      "sha256": None}
            if not path.is_file() or detail["size_bytes"] == 0:
                report["errors"].append(f"{split}: file missing or empty")
                report["splits"][split] = detail
                continue
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                line_number = 0
                while True:
                    raw = stream.readline(MAX_LINE + 1)
                    if not raw:
                        break
                    line_number += 1
                    digest.update(raw)
                    if len(raw) > MAX_LINE and not raw.endswith(b"\n"):
                        detail["malformed"] += 1
                        while raw and not raw.endswith(b"\n"):
                            raw = stream.readline(MAX_LINE + 1)
                            digest.update(raw)
                        continue
                    if b"\x00" in raw:
                        detail["malformed"] += 1
                        continue
                    try:
                        line = raw.decode("utf-8").rstrip("\r\n")
                    except UnicodeDecodeError:
                        detail["malformed"] += 1
                        continue
                    if not line:
                        detail["empty_lines"] += 1
                        continue
                    label, sep, url = line.partition("\t")
                    label, url = label.strip().lower(), url.strip()
                    if not sep or "\t" in url or label not in LABELS or not url:
                        detail["malformed"] += 1
                        continue
                    host = _domain(url)
                    if host is None:
                        detail["invalid_urls"] += 1
                        continue
                    detail["records"] += 1
                    detail["label_counts"][label] += 1
                    key = hashlib.sha256(url.encode("utf-8")).hexdigest()
                    existing_domain_splits = {row[0] for row in conn.execute("SELECT split FROM domains WHERE domain=?", (host,))}
                    if existing_domain_splits and split not in existing_domain_splits:
                        report["cross_split"].setdefault("shared_domain_records", 0)
                        report["cross_split"]["shared_domain_records"] += 1
                        for previous_split in existing_domain_splits:
                            if split != previous_split: _overlap_pair(report, "hostname_overlap", split, previous_split)
                    conn.execute("INSERT OR IGNORE INTO domains VALUES (?,?)", (host, split))
                    previous = conn.execute("SELECT split,label FROM urls WHERE url_hash=?", (key,)).fetchone()
                    if previous:
                        detail["duplicate_urls"] += 1
                        if previous[0] != split:
                            report["cross_split"].setdefault("exact_url_overlap", 0)
                            report["cross_split"]["exact_url_overlap"] += 1
                        if previous[1] != label:
                            report["errors"].append(f"conflicting labels for duplicate URL in {split}, line {line_number}")
                    else:
                        conn.execute("INSERT INTO urls VALUES (?,?,?,?)", (key, url, split, label))
                    for previous_split, previous_label in conn.execute("SELECT split,label FROM urls_by_split WHERE url_hash=?", (key,)):
                        if previous_split != split:
                            _overlap_pair(report, "exact_url_overlap", split, previous_split)
                            if previous_label != label:
                                report["cross_split"].setdefault("exact_label_conflicts", 0)
                                report["cross_split"]["exact_label_conflicts"] += 1
                    conn.execute("INSERT OR IGNORE INTO urls_by_split VALUES (?,?,?)", (key, split, label))
                    canonical = _canonical_url(url)
                    if canonical is not None:
                        canonical_key = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
                        canonical_previous = conn.execute("SELECT split,label FROM canonical_urls WHERE url_hash=?", (canonical_key,)).fetchone()
                        if canonical_previous:
                            detail["canonical_duplicate_urls"] += 1
                            if canonical_previous[0] != split:
                                report["cross_split"].setdefault("canonical_url_overlap", 0)
                                report["cross_split"]["canonical_url_overlap"] += 1
                            if canonical_previous[1] != label:
                                report["errors"].append(f"conflicting labels for canonical URL in {split}, line {line_number}")
                        else:
                            conn.execute("INSERT INTO canonical_urls VALUES (?,?,?,?)", (canonical_key, canonical, split, label))
                        for previous_split, previous_label in conn.execute("SELECT split,label FROM canonical_by_split WHERE url_hash=?", (canonical_key,)):
                            if previous_split != split:
                                _overlap_pair(report, "canonical_url_overlap", split, previous_split)
                                if previous_label != label:
                                    report["cross_split"].setdefault("canonical_label_conflicts", 0)
                                    report["cross_split"]["canonical_label_conflicts"] += 1
                        conn.execute("INSERT OR IGNORE INTO canonical_by_split VALUES (?,?,?)", (canonical_key, split, label))
                    if psl:
                        registrable = psl.registrable_domain(host)
                        if registrable:
                            prior = {row[0] for row in conn.execute("SELECT split FROM registrable_domains WHERE domain=?", (registrable,))}
                            for previous_split in prior:
                                if previous_split != split: _overlap_pair(report, "registrable_domain_overlap", split, previous_split)
                            conn.execute("INSERT OR IGNORE INTO registrable_domains VALUES (?,?)", (registrable, split))
                    if progress and detail["records"] % progress_interval == 0:
                        progress(split, detail["records"])
            detail["sha256"] = digest.hexdigest()
            detail["label_counts"] = dict(detail["label_counts"])
            if detail["malformed"]:
                report["errors"].append(f"{split}: {detail['malformed']} malformed record(s)")
            if detail["invalid_urls"]:
                report["errors"].append(f"{split}: {detail['invalid_urls']} invalid URL(s)")
            if detail["records"] == 0:
                report["errors"].append(f"{split}: no valid records")
            if detail["duplicate_urls"]:
                report["errors"].append(f"{split}: {detail['duplicate_urls']} duplicate URL(s)")
            if detail["canonical_duplicate_urls"]:
                report["errors"].append(f"{split}: {detail['canonical_duplicate_urls']} canonical duplicate URL(s)")
            report["splits"][split] = detail
        conn.commit()
        conn.close()

    if report["cross_split"].get("exact_url_overlap"):
        report["errors"].append("exact URL overlap across dataset splits")
    if report["cross_split"].get("canonical_url_overlap"):
        report["errors"].append("canonical URL overlap across dataset splits")
    report["passed"] = not report["errors"]
    report["registrable_domain_overlap"] = ({"status": "completed", "overlap_by_split":
        report["cross_split"].get("registrable_domain_overlap_by_split", {})} if psl else
        {"status": "unavailable", "reason": "Hostname overlap is counted; no pinned public suffix list was supplied."})
    report["status"] = "completed"
    report["completed_at_utc"] = datetime.now(timezone.utc).isoformat()
    report["input_fingerprint"] = hashlib.sha256(json.dumps(
        {split: detail["sha256"] for split, detail in sorted(report["splits"].items())},
        sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return report


def audit_external_overlap(*, training_sources: dict[str, Path], evaluation_path: Path,
                           psl_path: Path, psl_sha256: str, psl_version: str,
                           progress=None, progress_interval: int = 100_000) -> dict:
    """Disk-backed exact, canonical, host and PSL registrable-domain audit."""
    if not training_sources or progress_interval <= 0:
        raise ValueError("training sources and a positive progress interval are required")
    psl = PublicSuffixList(psl_path, psl_sha256, psl_version)
    source_hashes = {}
    with tempfile.TemporaryDirectory(prefix="optimus-overlap-") as tmp:
        conn = sqlite3.connect(str(Path(tmp) / "overlap.sqlite"))
        conn.execute("CREATE TABLE exact (key TEXT NOT NULL, source TEXT NOT NULL, label TEXT NOT NULL, PRIMARY KEY(key,source,label))")
        conn.execute("CREATE TABLE canonical (key TEXT NOT NULL, source TEXT NOT NULL, label TEXT NOT NULL, PRIMARY KEY(key,source,label))")
        conn.execute("CREATE TABLE host (key TEXT NOT NULL, source TEXT NOT NULL, PRIMARY KEY(key,source))")
        conn.execute("CREATE TABLE registrable (key TEXT NOT NULL, source TEXT NOT NULL, PRIMARY KEY(key,source))")
        def consume(path: Path, source_name: str, is_eval: bool):
            path = Path(path)
            digest = hashlib.sha256()
            count = malformed = 0
            result = {name: {key: 0 for key in ("exact_url_overlap", "canonical_url_overlap",
                "hostname_overlap", "registrable_domain_overlap", "exact_label_conflicts",
                "canonical_label_conflicts")} for name in training_sources}
            with path.open("rb") as stream:
                while True:
                    raw = stream.readline(MAX_LINE + 1)
                    if not raw: break
                    digest.update(raw)
                    if len(raw) > MAX_LINE or b"\x00" in raw:
                        malformed += 1; continue
                    try: line = raw.decode("utf-8").rstrip("\r\n")
                    except UnicodeDecodeError: malformed += 1; continue
                    label, sep, url = line.partition("\t")
                    label, url = label.strip().lower(), url.strip()
                    host = _domain(url) if sep and label in LABELS and url else None
                    if not sep or label not in LABELS or not url or host is None:
                        malformed += 1; continue
                    count += 1
                    exact_key = hashlib.sha256(url.encode("utf-8")).hexdigest()
                    canonical = _canonical_url(url)
                    canonical_key = hashlib.sha256(canonical.encode()).hexdigest() if canonical else None
                    registrable_domain = psl.registrable_domain(host)
                    if not is_eval:
                        conn.execute("INSERT OR IGNORE INTO exact VALUES (?,?,?)", (exact_key, source_name, label))
                        if canonical_key: conn.execute("INSERT OR IGNORE INTO canonical VALUES (?,?,?)", (canonical_key, source_name, label))
                        conn.execute("INSERT OR IGNORE INTO host VALUES (?,?)", (host, source_name))
                        if registrable_domain: conn.execute("INSERT OR IGNORE INTO registrable VALUES (?,?)", (registrable_domain, source_name))
                    else:
                        for table, key, metric, conflict_metric in (
                                ("exact", exact_key, "exact_url_overlap", "exact_label_conflicts"),
                                ("canonical", canonical_key, "canonical_url_overlap", "canonical_label_conflicts")):
                            if key:
                                rows = conn.execute(f"SELECT source,label FROM {table} WHERE key=?", (key,)).fetchall()
                                for known_source, known_label in rows:
                                    result[known_source][metric] += 1
                                    if known_label != label: result[known_source][conflict_metric] += 1
                        for table, key, metric in (("host", host, "hostname_overlap"),
                                ("registrable", registrable_domain, "registrable_domain_overlap")):
                            if key:
                                for (known_source,) in conn.execute(f"SELECT source FROM {table} WHERE key=?", (key,)):
                                    result[known_source][metric] += 1
                    if progress and count % progress_interval == 0:
                        progress(source_name, count)
            if malformed:
                raise ValueError(f"{source_name}: {malformed} malformed records; overlap audit incomplete")
            return digest.hexdigest(), count, result

        training_counts = {}
        for name, path in sorted(training_sources.items()):
            digest, count, _ = consume(Path(path), name, False)
            source_hashes[name] = digest; training_counts[name] = count
            conn.commit()
        eval_sha, eval_count, overlap = consume(Path(evaluation_path), "evaluation", True)
        conn.commit(); conn.close()
    input_fingerprint = hashlib.sha256(json.dumps({
        "training": source_hashes, "evaluation": eval_sha,
        "psl_version": psl.version, "psl_sha256": psl.sha256,
    }, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {"status": "completed", "completed_at_utc": datetime.now(timezone.utc).isoformat(),
        "input_fingerprint": input_fingerprint,
        "psl": {"version": psl.version, "sha256": psl.sha256},
        "training_inputs": {name: {"path": str(Path(training_sources[name])), "sha256": source_hashes[name],
                                   "records": training_counts[name]} for name in sorted(training_sources)},
        "evaluation_input": {"path": str(Path(evaluation_path)), "sha256": eval_sha, "records": eval_count},
        "overlap_by_training_source": overlap}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--psl-file", type=Path)
    parser.add_argument("--psl-sha256")
    parser.add_argument("--psl-version", help="PSL release tag or immutable retrieval identifier")
    parser.add_argument("--progress-interval", type=int, default=100_000)
    args = parser.parse_args()
    report_path = args.report
    partial_path = report_path.with_suffix(report_path.suffix + ".partial.json")
    report = {"status": "running", "dataset": str(args.dataset), "started_at_utc": datetime.now(timezone.utc).isoformat()}
    partial_path.parent.mkdir(parents=True, exist_ok=True)
    partial_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    def progress(split, count):
        report.update({"progress": {"split": split, "processed_records": count}, "updated_at_utc": datetime.now(timezone.utc).isoformat()})
        partial_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"[full] {split}: processed {count:,} records", flush=True)
    try:
        report = validate_dataset(args.dataset, progress=progress, progress_interval=args.progress_interval,
            psl_path=args.psl_file, psl_sha256=args.psl_sha256, psl_version=args.psl_version)
    except Exception as exc:
        report.update({"status": "incomplete", "error": f"{type(exc).__name__}: {exc}"})
        partial_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        raise
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2), encoding="utf-8")
    partial_path.unlink(missing_ok=True)
    print(f"Validation {'PASSED' if report['passed'] else 'FAILED'}; report: {args.report}")
    for split, detail in report["splits"].items():
        print(f"{split}: {detail['records']:,} records, {detail['malformed']} malformed, {detail['empty_lines']} empty lines, {detail['duplicate_urls']} duplicate URLs")
    if report["errors"]:
        print("\n".join(report["errors"]))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
