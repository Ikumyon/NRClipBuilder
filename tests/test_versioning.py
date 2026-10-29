"""Release contract tests; no Qt or native extension is needed."""

import importlib.util
import json
from pathlib import Path
import subprocess
import tomllib
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "packaging" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


versioning = load("versioning")
assets = load("verify_assets")


class VersionTests(unittest.TestCase):
    def test_supported_tags(self):
        for tag, expected in (
            ("v1.2.3", "1.2.3"), ("v0.0.0", "0.0.0"),
            ("v2.3.4-alpha.1", "2.3.4-alpha.1"),
            ("v2.3.4-beta.2", "2.3.4-beta.2"), ("v2.3.4-rc.3", "2.3.4-rc.3"),
        ):
            with self.subTest(tag=tag):
                self.assertEqual(versioning.version_from_tag(tag), expected)

    def test_invalid_tags(self):
        for tag in ("", "1.2.3", "v1.2", "v01.2.3", "v1.2.3-dev", "v1.2.3-rc.01",
                    "v1.2.3-preview.1", "v1.2.3+build", "v1.2.3\n", "v65536.0.0"):
            with self.subTest(tag=tag), self.assertRaises(ValueError):
                versioning.version_from_tag(tag)

    def test_versions_and_dependencies_stay_consistent(self):
        original_cargo = tomllib.loads((ROOT / "src/native/Cargo.toml").read_text(encoding="utf-8"))
        original_lock = tomllib.loads((ROOT / "Cargo.lock").read_text(encoding="utf-8"))
        original_web = json.loads((ROOT / "src/web/package.json").read_text(encoding="utf-8"))
        original_web_lock = json.loads((ROOT / "src/web/package-lock.json").read_text(encoding="utf-8"))
        for version in (versioning.DEVELOPMENT_VERSION, "1.2.3", "1.2.3-alpha.1", "1.2.3-beta.2", "1.2.3-rc.3"):
            with self.subTest(version=version):
                updates = versioning.version_updates(ROOT, version)
                cargo = tomllib.loads(updates["src/native/Cargo.toml"])
                lock = tomllib.loads(updates["Cargo.lock"])
                web = json.loads(updates["src/web/package.json"])
                web_lock = json.loads(updates["src/web/package-lock.json"])
                self.assertEqual(cargo["package"]["version"], version)
                self.assertEqual(web["version"], version)
                self.assertEqual(web_lock["version"], version)
                self.assertEqual(web_lock["packages"][""]["version"], version)
                native = next(p for p in lock["package"] if p["name"] == "nrclip-native")
                self.assertEqual(native["version"], version)
                cargo["package"]["version"] = original_cargo["package"]["version"]
                native["version"] = next(p for p in original_lock["package"] if p["name"] == "nrclip-native")["version"]
                web["version"] = original_web["version"]
                web_lock["version"] = original_web_lock["version"]
                web_lock["packages"][""]["version"] = original_web_lock["packages"][""]["version"]
                self.assertEqual(cargo, original_cargo)
                self.assertEqual(lock, original_lock)
                self.assertEqual(web, original_web)
                self.assertEqual(web_lock, original_web_lock)

    def test_python_version_normalization(self):
        from packaging.version import Version

        for source, expected in (("0.0.0-dev", "0.0.0.dev0"), ("1.2.3", "1.2.3"),
                                 ("1.2.3-alpha.1", "1.2.3a1"), ("1.2.3-beta.2", "1.2.3b2"),
                                 ("1.2.3-rc.3", "1.2.3rc3")):
            self.assertEqual(str(Version(source)), expected)

    def test_release_patch_applies_on_this_checkout(self):
        for version in ("1.2.3", "1.2.3-alpha.1", "1.2.3-beta.2", "1.2.3-rc.3"):
            with self.subTest(version=version):
                diff = versioning.version_patch(ROOT, version)
                if not diff:
                    continue
                subprocess.run(
                    ["git", "apply", "--check", "--ignore-space-change", "-"],
                    input=diff.encode("utf-8"), cwd=ROOT, check=True,
                )


class AssetTests(unittest.TestCase):
    def test_required_assets(self):
        good = [{"name": n, "size": 123, "state": "uploaded"} for n in assets.ASSET_NAMES]
        with patch.object(Path, "stat") as stat:
            stat.return_value.st_size = 123
            assets.verify_assets(good, "artifacts")
            for bad in ([], good[:1], [dict(a, size=0) for a in good],
                        [dict(a, size=124) for a in good], [dict(a, state="starter") for a in good]):
                with self.subTest(assets=bad), self.assertRaises(ValueError):
                    assets.verify_assets(bad, "artifacts")
            stat.return_value.st_size = 0
            with self.assertRaises(ValueError):
                assets.verify_assets(good, "artifacts")


if __name__ == "__main__":
    unittest.main()
