import tempfile
import unittest
import zipfile
import json
from pathlib import Path

from scripts.package_chrome_extension import (
    API_PERMISSION, CONTENT_MATCHES, DEFAULT_API_BASE, EXTENSION, REQUIRED_FILES,
    _checked_base_url, _checked_privacy_url, build_package, package_sha256,
    validate_extension,
)

# These known public origins are syntax fixtures only. The tests make no HTTP
# request and do not represent an OPTIMUS deployment or privacy-policy host.
FIXTURE_API = "https://api.github.com"
FIXTURE_PRIVACY = "https://www.iana.org/"

class ChromeExtensionPackageTests(unittest.TestCase):
    def test_manifest_has_only_required_permissions_and_api_host(self):
        manifest = validate_extension()
        self.assertEqual(manifest["host_permissions"], [API_PERMISSION])
        self.assertEqual(set(manifest["content_scripts"][0]["matches"]), CONTENT_MATCHES)
        self.assertEqual(set(manifest["permissions"]), {"tabs", "storage"})
        self.assertIn(manifest["action"]["default_icon"], REQUIRED_FILES)
        self.assertTrue((EXTENSION / manifest["action"]["default_icon"]).is_file())

    def test_package_is_allowlisted_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "extension.zip"
            build_package(output)
            with zipfile.ZipFile(output) as archive:
                self.assertEqual(set(archive.namelist()), set(REQUIRED_FILES))
                self.assertFalse(any(name.lower().endswith((".db", ".env", ".h5", ".jsonl"))
                                     for name in archive.namelist()))
            with self.assertRaises(FileExistsError):
                build_package(output)

    def test_production_package_has_only_https_api_and_policy_origins(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "production.zip"
            build_package(output, api_base_url=FIXTURE_API,
                          privacy_policy_url=FIXTURE_PRIVACY)
            self.assertEqual(len(package_sha256(output)), 64)
            with zipfile.ZipFile(output) as archive:
                self.assertIsNone(archive.testzip())
                manifest = json.loads(archive.read("manifest.json"))
                background = archive.read("background.js").decode()
                popup = archive.read("popup.js").decode()
                html = archive.read("popup.html").decode()
            self.assertEqual(manifest["host_permissions"], [f"{FIXTURE_API}/*"])
            self.assertNotIn(DEFAULT_API_BASE, background + popup + str(manifest))
            self.assertIn(FIXTURE_PRIVACY, html)

    def test_production_api_url_rejects_invalid_scheme_host_port_authority_and_components(self):
        invalid = (
            "http://api.github.com",
            "https://bad host.github.com",
            "https://-bad.github.com",
            "https://api.example.test",
            "https://localhost",
            "https://api.github.com:bad",
            "https://api.github.com:0",
            "https://api.github.com:65536",
            "https://api.github.com:",
            "https://user:secret@api.github.com",
            "https://api.github.com/path",
            "https://api.github.com?token=x",
            "https://api.github.com?",
            "https://api.github.com#section",
            "https://api.github.com#",
        )
        for url in invalid:
            with self.subTest(url=url), self.assertRaises(ValueError):
                _checked_base_url(url, production=True)
        self.assertEqual(_checked_base_url("https://API.GITHUB.COM:443/", production=True),
                         "https://api.github.com")

    def test_production_privacy_url_rejects_invalid_host_port_and_components(self):
        invalid = (
            "http://www.iana.org/privacy",
            "https://bad host.iana.org/privacy",
            "https://privacy.example.test/policy",
            "https://www.iana.org:bad/privacy",
            "https://www.iana.org:65536/privacy",
            "https://www.iana.org:/privacy",
            "https://user:secret@www.iana.org/privacy",
            "https://www.iana.org/privacy?source=extension",
            "https://www.iana.org/privacy?",
            "https://www.iana.org/privacy#terms",
            "https://www.iana.org/privacy#",
        )
        for url in invalid:
            with self.subTest(url=url), self.assertRaises(ValueError):
                _checked_privacy_url(url)
        self.assertEqual(_checked_privacy_url("https://WWW.IANA.ORG:443/privacy"),
                         "https://www.iana.org/privacy")

    def test_consent_versions_match_and_notice_describes_automatic_url_scope(self):
        background = (EXTENSION / "background.js").read_text(encoding="utf-8")
        popup = (EXTENSION / "popup.js").read_text(encoding="utf-8")
        html = (EXTENSION / "popup.html").read_text(encoding="utf-8")
        import re
        background_version = re.search(r'const CONSENT_VERSION = "([^"]+)"', background).group(1)
        popup_version = re.search(r'const CONSENT_VERSION = "([^"]+)"', popup).group(1)
        self.assertEqual(background_version, popup_version)
        self.assertIn("automatically sends the full URL for each qualifying", html)
        self.assertIn("fragment when present", html)
        self.assertIn("An authorized reviewer may access", html)
        self.assertIn('href="#consentPanel"', html)
        self.assertIn("hasConsent().then(consented => consented ? scanUrl(url) : null)", background)
        self.assertGreaterEqual(background.count('if (!consented) throw new Error('), 2)
        self.assertIn("return sendFeedback(message.predictionId, message.actualLabel)", background)
        self.assertIn("scanButton.disabled = !consented", popup)
        self.assertIn('chrome.storage.local.remove(["optimusConsentVersion", "optimusConsentAt"])', popup)

    def test_production_package_requires_https_and_real_policy_url(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "production.zip"
            with self.assertRaisesRegex(ValueError, "HTTPS"):
                build_package(output, api_base_url="http://api.example.test",
                              privacy_policy_url="https://privacy.example.test/optimus")
            self.assertFalse(output.exists())
            with self.assertRaisesRegex(ValueError, "both API and privacy-policy"):
                build_package(output, api_base_url="https://api.example.test")


if __name__ == "__main__":
    unittest.main()
