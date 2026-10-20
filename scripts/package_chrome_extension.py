"""Build allowlisted development or HTTPS production extension packages."""
from __future__ import annotations

import argparse
import hashlib
from html import escape
import ipaddress
import json
import re
import shutil
import tempfile
import zipfile
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / "extension"
REQUIRED_FILES = (
    "manifest.json", "background.js", "content.js", "popup.html",
    "popup.css", "popup.js", "OptimUS Neon Robot Emblem.png",
)
MANIFEST_PERMISSIONS = {"tabs", "storage"}
DEFAULT_API_BASE = "http://127.0.0.1:8000"
API_PERMISSION = f"{DEFAULT_API_BASE}/*"
CONTENT_MATCHES = {"http://*/*", "https://*/*"}
PRIVACY_HREF = "#consentPanel"
PLACEHOLDER_SUFFIXES = {"example", "invalid", "test", "localhost", "local"}
RESERVED_EXAMPLE_DOMAINS = {"example.com", "example.net", "example.org"}


def _public_dns_hostname(hostname: str) -> str:
    """Return normalized ASCII DNS name or reject non-production placeholders.

    This checks syntax and reserved test names only. It does not establish DNS
    ownership, public reachability, or TLS validity.
    """
    if not hostname or hostname.endswith(".") or any(c.isspace() or ord(c) < 33 for c in hostname):
        raise ValueError("production URL must use a valid public DNS hostname")
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        pass
    else:
        raise ValueError("production URL must use a public DNS hostname, not an IP address")
    try:
        host = hostname.encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise ValueError("production URL contains an invalid hostname") from exc
    if len(host) > 253 or len(host.split(".")) < 2:
        raise ValueError("production URL must use a fully qualified DNS hostname")
    labels = host.split(".")
    if any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in labels):
        raise ValueError("production URL contains an invalid hostname")
    tld = labels[-1]
    if not (re.fullmatch(r"[a-z]{2,63}", tld) or re.fullmatch(r"xn--[a-z0-9-]{2,59}[a-z0-9]", tld)):
        raise ValueError("production URL must use a public DNS suffix")
    if tld in PLACEHOLDER_SUFFIXES or any(
        host == reserved or host.endswith("." + reserved) for reserved in RESERVED_EXAMPLE_DOMAINS
    ):
        raise ValueError("production URL cannot use a reserved placeholder hostname")
    return host


def _reject_url_whitespace(value: str) -> None:
    if not value or value != value.strip() or any(ord(char) <= 32 or ord(char) == 127 for char in value):
        raise ValueError("URL must not contain whitespace or control characters")


def _checked_base_url(value: str, *, production: bool) -> str:
    _reject_url_whitespace(value)
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise ValueError("API URL contains malformed host or port syntax") from exc
    authority = parsed.netloc.rsplit("@", 1)[-1]
    if (not parsed.scheme or not hostname or parsed.username is not None or parsed.password is not None
            or parsed.path not in {"", "/"} or parsed.query or parsed.fragment
            or "?" in value or "#" in value or authority.endswith(":")):
        raise ValueError("API URL must be an origin with no credentials, path, query, or fragment")
    if production:
        if parsed.scheme.lower() != "https":
            raise ValueError("production API URL must use HTTPS")
        normalized_host = _public_dns_hostname(hostname)
    elif value != DEFAULT_API_BASE:
        raise ValueError("development API URL is fixed to the loopback demo service")
    else:
        normalized_host = hostname.lower()
    if not production and normalized_host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("development API URL must use loopback")
    if port is not None and not 1 <= port <= 65535:
        raise ValueError("API URL contains an invalid port")
    if production:
        port_suffix = f":{port}" if port is not None and port != 443 else ""
        return f"https://{normalized_host}{port_suffix}"
    return f"{parsed.scheme}://{parsed.netloc}"


def _permission_pattern(api_base_url: str) -> str:
    return f"{api_base_url}/*"


def _checked_privacy_url(value: str) -> str:
    _reject_url_whitespace(value)
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise ValueError("production privacy-policy URL contains malformed host or port syntax") from exc
    authority = parsed.netloc.rsplit("@", 1)[-1]
    if (parsed.scheme.lower() != "https" or not hostname
            or parsed.username is not None or parsed.password is not None
            or parsed.query or parsed.fragment or "?" in value or "#" in value
            or authority.endswith(":")):
        raise ValueError("production privacy-policy URL must be a public HTTPS URL without credentials or query")
    normalized_host = _public_dns_hostname(hostname)
    if port is not None and not 1 <= port <= 65535:
        raise ValueError("production privacy-policy URL contains an invalid port")
    port_suffix = f":{port}" if port is not None and port != 443 else ""
    return f"https://{normalized_host}{port_suffix}{parsed.path}"


def validate_extension(source: Path = EXTENSION, *, api_base_url: str = DEFAULT_API_BASE) -> dict:
    api_base_url = _checked_base_url(api_base_url, production=api_base_url != DEFAULT_API_BASE)
    missing = [name for name in REQUIRED_FILES if not (source / name).is_file()]
    if missing:
        raise FileNotFoundError(f"required extension files are missing: {', '.join(missing)}")
    manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("manifest_version") != 3:
        raise ValueError("extension package must use Manifest V3")
    if set(manifest.get("permissions", [])) != MANIFEST_PERMISSIONS:
        raise ValueError("unexpected extension permissions")
    expected_permission = _permission_pattern(api_base_url)
    if set(manifest.get("host_permissions", [])) != {expected_permission}:
        raise ValueError("host permission must be limited to the configured API origin")
    content_scripts = manifest.get("content_scripts", [])
    if len(content_scripts) != 1 or set(content_scripts[0].get("matches", [])) != CONTENT_MATCHES:
        raise ValueError("content script matches must be limited to HTTP and HTTPS pages")
    background = (source / "background.js").read_text(encoding="utf-8")
    popup = (source / "popup.js").read_text(encoding="utf-8")
    html = (source / "popup.html").read_text(encoding="utf-8")
    if f'const API_BASE = "{api_base_url}"' not in background or f'const API_BASE = "{api_base_url}"' not in popup:
        raise ValueError("popup and service worker API origins do not match manifest host permission")
    if "optimusConsentVersion" not in background or "enableScanButton" not in popup:
        raise ValueError("extension must gate collection on explicit stored user consent")
    import re
    privacy_link = re.search(r'<a id="privacyPolicyLink" href="([^"]+)"', html)
    if not privacy_link:
        raise ValueError("extension must link to privacy information")
    if privacy_link.group(1) != PRIVACY_HREF:
        _checked_privacy_url(privacy_link.group(1))
    references = [manifest.get("action", {}).get("default_popup"),
                  manifest.get("action", {}).get("default_icon")]
    references += manifest.get("background", {}).get("service_worker", "").split()
    references += [name for item in content_scripts for name in item.get("js", [])]
    for name in references:
        if name and name not in REQUIRED_FILES:
            raise ValueError(f"manifest references a file outside the package allowlist: {name}")
    return manifest


def _production_stage(source: Path, api_base_url: str, privacy_policy_url: str, target: Path) -> None:
    for name in REQUIRED_FILES:
        shutil.copy2(source / name, target / name)
    for name in ("background.js", "popup.js"):
        path = target / name
        text = path.read_text(encoding="utf-8")
        if text.count(DEFAULT_API_BASE) != 1:
            raise ValueError(f"expected one development API origin in {name}")
        path.write_text(text.replace(DEFAULT_API_BASE, api_base_url), encoding="utf-8")
    html_path = target / "popup.html"
    html = html_path.read_text(encoding="utf-8")
    if f'href="{PRIVACY_HREF}"' not in html:
        raise ValueError("development privacy-information link was not found")
    safe_policy_url = escape(privacy_policy_url, quote=True)
    html = html.replace(f'href="{PRIVACY_HREF}"',
                        f'href="{safe_policy_url}" target="_blank" rel="noopener noreferrer"')
    html_path.write_text(html, encoding="utf-8")
    manifest_path = target / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["host_permissions"] = [_permission_pattern(api_base_url)]
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def build_package(output: Path, source: Path = EXTENSION, *,
                  api_base_url: str | None = None,
                  privacy_policy_url: str | None = None) -> Path:
    production = api_base_url is not None or privacy_policy_url is not None
    if production and (api_base_url is None or privacy_policy_url is None):
        raise ValueError("production packaging requires both API and privacy-policy HTTPS URLs")
    api_base_url = _checked_base_url(api_base_url or DEFAULT_API_BASE, production=production)
    if production:
        privacy_policy_url = _checked_privacy_url(privacy_policy_url or "")
    output = output.resolve()
    if output.exists():
        raise FileExistsError(f"refusing to overwrite existing package: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)

    if production:
        with tempfile.TemporaryDirectory(prefix="optimus-extension-build-") as temp_dir:
            staged = Path(temp_dir)
            _production_stage(source, api_base_url, privacy_policy_url or "", staged)
            validate_extension(staged, api_base_url=api_base_url)
            _write_archive(output, staged)
    else:
        validate_extension(source, api_base_url=api_base_url)
        _write_archive(output, source)
    return output


def _write_archive(output: Path, source: Path) -> None:
    with zipfile.ZipFile(output, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in REQUIRED_FILES:
            archive.write(source / name, arcname=name)
    try:
        with zipfile.ZipFile(output, "r") as archive:
            if sorted(archive.namelist()) != sorted(REQUIRED_FILES) or archive.testzip() is not None:
                raise RuntimeError("package contents or CRC do not match the extension allowlist")
            for name in archive.namelist():
                if name.lower().endswith((".db", ".sqlite", ".env", ".h5", ".keras", ".jsonl")):
                    raise RuntimeError(f"forbidden development artifact in package: {name}")
    except Exception:
        output.unlink(missing_ok=True)
        raise


def package_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--production-api-url", help="required HTTPS API origin for a production build")
    parser.add_argument("--privacy-policy-url", help="required publicly hosted HTTPS policy URL for a production build")
    args = parser.parse_args()
    production = args.production_api_url is not None or args.privacy_policy_url is not None
    if production and not (args.production_api_url and args.privacy_policy_url):
        parser.error("--production-api-url and --privacy-policy-url must be provided together")
    default_name = "optimus-chrome-extension-production.zip" if production else "optimus-chrome-extension.zip"
    target = build_package(args.output or ROOT / "dist" / default_name,
        api_base_url=args.production_api_url, privacy_policy_url=args.privacy_policy_url)
    print(f"Created {target}")
    print(f"SHA-256: {package_sha256(target)}")
    print(f"Included files: {', '.join(REQUIRED_FILES)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
