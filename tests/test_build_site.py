import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from build_site import WEB_FILES, build_site
from export_records import ExportError, prepare, publish
from validate_data import validate_data


class BuildTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix=".export-build-test-", dir=Path(__file__).resolve().parent)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source"; self.source.mkdir()
        records = self.root / "records"; records.mkdir()
        (records / ("架空曲".encode().hex() + ".json")).write_text(json.dumps({"DP": {"ANOTHER": {"best": {"score": {"value": 100}}}}}), encoding="utf-8")
        files, manifest, _ = prepare(records, {})
        publish(self.source / "data", files, manifest)
        web = self.source / "web"; web.mkdir()
        for name in WEB_FILES:
            (web / name).write_bytes((Path(__file__).resolve().parents[1] / "web" / name).read_bytes())
        (self.source / "config.local.json").write_text("Private path", encoding="utf-8")
        (web / "private.txt").write_text("Do not publish", encoding="utf-8")
        self.output = self.root / "site"

    def test_build_includes_only_site_and_validated_json(self):
        build_site(self.source, self.output)
        self.assertEqual(validate_data(self.output / "data"), (1, 0))
        self.assertFalse((self.output / "config.local.json").exists())
        self.assertFalse((self.output / "web/private.txt").exists())
        self.assertTrue((self.output / ".nojekyll").exists())
        self.assertIn("'./web/' + location.search + location.hash", (self.output / "index.html").read_text(encoding="utf-8"))
        self.assertIn("new URL('../data/', import.meta.url)", (self.output / "web/app.js").read_text(encoding="utf-8"))
        build_site(self.source, self.output)

    def test_invalid_input_keeps_previous_build(self):
        build_site(self.source, self.output)
        before = {p.relative_to(self.output): p.read_bytes() for p in self.output.rglob("*") if p.is_file()}
        (self.source / "data/index.json").write_text("invalid", encoding="utf-8")
        with self.assertRaises(ValueError):
            build_site(self.source, self.output)
        self.assertEqual(before, {p.relative_to(self.output): p.read_bytes() for p in self.output.rglob("*") if p.is_file()})

    def test_refuses_source_or_unmanaged_output(self):
        for path in (self.source, self.source / "web/site", self.source / "data/site", self.source / ".git/site"):
            with self.assertRaises(ExportError):
                build_site(self.source, path)
        self.output.mkdir()
        (self.output / "keep.txt").write_text("Keep", encoding="utf-8")
        with self.assertRaises(ExportError):
            build_site(self.source, self.output)
        self.assertTrue((self.output / "keep.txt").exists())


if __name__ == "__main__":
    unittest.main()
