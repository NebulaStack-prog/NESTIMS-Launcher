import hashlib
import importlib.util
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / "NESTIMS_Launcher.py"


class LauncherTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        env = patch.dict(os.environ, {"LOCALAPPDATA": temp.name})
        env.start()
        self.addCleanup(env.stop)
        spec = importlib.util.spec_from_file_location("launcher", SOURCE)
        self.app = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.app)

    def product(self, extension="exe"):
        name = "Example." + extension
        return dict(id="NS-001", name="Example", repo="Example",
                    category="Infotech", description="Test product",
                    release=dict(version="v1.0", name=name, size=12,
                                 digest="sha256:" + "a"*64,
                                 url=f"https://github.com/NebulaStack-prog/Example/releases/download/v1.0/{name}"))

    def test_catalog(self):
        p = self.product()
        self.assertEqual(self.app.parse_catalog([p])["NS-001"],
                         ("Example", "Example", "Test product", "Infotech"))

    def test_duplicate_id(self):
        with self.assertRaises(ValueError):
            self.app.parse_catalog([self.product(), self.product()])

    def test_invalid_category(self):
        p = self.product()
        p["category"] = "Unknown"
        with self.assertRaises(ValueError):
            self.app.parse_catalog([p])

    def test_release_exe(self):
        releases, errors = self.app.manifest_releases([self.product()])
        self.assertEqual(releases["NS-001"]["package_type"], "exe")
        self.assertFalse(errors)

    def test_release_jar(self):
        p = self.product("jar")
        p["jar_release"] = p.pop("release")
        releases, errors = self.app.manifest_releases([p])
        self.assertEqual(releases["NS-001"]["package_type"], "jar")
        self.assertFalse(errors)

    def test_missing_release(self):
        p = self.product()
        p["release"] = None
        releases, errors = self.app.manifest_releases([p])
        self.assertFalse(releases)
        self.assertIn("NS-001", errors)

    def test_invalid_metadata(self):
        for key, value in [("size", -1), ("digest", "invalid"),
                           ("url", "https://example.org/Example.exe"),
                           ("name", "../Example.exe"), ("version", "../v1")]:
            with self.subTest(key=key):
                p = self.product()
                p["release"][key] = value
                with self.assertRaises(ValueError):
                    self.app.manifest_releases([p])

    def test_package_path(self):
        self.assertEqual(self.app.executable("NS-001", "jar").parent, self.app.ROOT)
        for key, kind in [("../unsafe", "exe"), ("NS-001", "zip")]:
            with self.assertRaises(ValueError):
                self.app.executable(key, kind)

    def test_sha256(self):
        path = self.app.ROOT / "sample.bin"
        path.write_bytes(b"NESTIMS")
        self.assertEqual(self.app.file_hash(path), hashlib.sha256(b"NESTIMS").hexdigest())

    def test_library_roundtrip(self):
        data = {"NS-001": {"version": "v1.0", "sha256": "a"*64, "package_type": "exe"}}
        self.app.save_state(data)
        self.assertEqual(self.app.read_state(), data)
        self.app.STATE.write_text("invalid", encoding="utf-8")
        self.assertEqual(self.app.read_state(), {})

    def test_cached_catalog(self):
        self.assertEqual(self.app.read_manifest(), self.app.DEFAULT_CATALOG)
        p = self.product()
        self.app.CATALOG_FILE.write_text(json.dumps([p]), encoding="utf-8")
        self.assertEqual(self.app.load_catalog(), self.app.parse_catalog([p]))

    def test_fetch_catalog(self):
        class Response(io.BytesIO):
            def geturl(self):
                return "https://example.org/catalog.json"
        payload = [self.product()]
        with patch.object(self.app.urllib.request, "urlopen", return_value=Response(json.dumps(payload).encode())):
            self.assertEqual(self.app.fetch_catalog(), payload)

    def test_invalid_remote_catalog(self):
        class Response(io.BytesIO):
            def geturl(self):
                return "https://example.org/catalog.json"
        with patch.object(self.app.urllib.request, "urlopen", return_value=Response(b"{}")):
            with self.assertRaises(ValueError):
                self.app.fetch_catalog()
