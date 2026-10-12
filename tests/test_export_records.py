import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from export_records import ExportError, export, normalize_chart, prepare, publish, rename_directory
from validate_data import validate_data


def temporary_directory():
    return tempfile.TemporaryDirectory(prefix=".export-test-", dir=Path(__file__).resolve().parent)


class ExportTests(unittest.TestCase):
    def test_transient_windows_directory_lock_is_retried(self):
        with temporary_directory() as directory:
            root = Path(directory); source = root / "source"; source.mkdir()
            destination = root / "destination"
            original = Path.rename
            calls = []
            def locked_once(path, target):
                calls.append(path)
                if len(calls) == 1:
                    raise PermissionError("Transient file indexing lock")
                return original(path, target)
            with patch.object(Path, "rename", autospec=True, side_effect=locked_once), patch("export_records.time.sleep"):
                rename_directory(source, destination)
            self.assertEqual(len(calls), 2)
            self.assertTrue(destination.is_dir())

    def test_preserves_best_outside_history_and_zero_bp(self):
        source = {"timestamps": ["20260101-120000"], "history": {
            "20260101-120000": {"score": {"value": 100}, "miss_count": {"value": 0}}},
            "best": {"score": {"value": 200, "options": None}}}
        chart = normalize_chart(source, "sample", [])
        self.assertEqual(chart["best"]["score"], {"value": 200, "options": None})
        self.assertEqual(chart["history"][0]["missCount"], 0)
        self.assertIsNone(chart["history"][0]["options"])
        self.assertIsNone(chart["history"][0]["playSpeed"])

    def test_unlisted_history_retained_and_missing_history_rejected(self):
        warnings = []
        chart = normalize_chart({"history": {"20260101-120000": {}, "20260201-120000": {}}}, "sample", warnings)
        self.assertEqual([row["timestamp"] for row in chart["history"]], ["20260201-120000", "20260101-120000"])
        self.assertEqual(len(warnings), 1)
        with self.assertRaises(ExportError):
            normalize_chart({"timestamps": ["20260101-120000"]}, "sample", [])

    def test_all_chart_types_and_battle_level(self):
        from export_records import STYLES, DIFFICULTIES
        with temporary_directory() as directory:
            records = Path(directory)
            title = "架空曲 Café ガ"
            source = {style: {difficulty: {"best": {"score": {"value": 0}}} for difficulty in DIFFICULTIES} for style in STYLES}
            (records / (title.encode().hex() + ".json")).write_text(json.dumps(source), encoding="utf-8")
            (records / "summary.json").write_text("invalid, ignored", encoding="utf-8")
            files, manifest, warnings = prepare(records, {title: {"SP": {"ANOTHER": 10}, "DP": {"ANOTHER": 12}}})
            song = json.loads(next(raw for name, raw in files.items() if name.startswith("songs/")))
            self.assertEqual(len(song["charts"]), 3)
            self.assertTrue(all(len(charts) == 5 for charts in song["charts"].values()))
            self.assertEqual(song["charts"]["DP BATTLE"]["ANOTHER"]["level"], 10)
            self.assertEqual(song["charts"]["DP"]["ANOTHER"]["level"], 12)
            self.assertEqual(manifest["songCount"], 1)

    def test_idempotent_export_and_failure_keeps_previous_output(self):
        with temporary_directory() as directory:
            root = Path(directory)
            records = root / "records"; records.mkdir()
            source = records / ("架空曲".encode().hex() + ".json")
            source.write_text(json.dumps({"SP": {"ANOTHER": {"best": {"score": {"value": 123}}}}}), encoding="utf-8")
            config = root / "config.json"
            config.write_text(json.dumps({"recordsPath": "records", "outputPath": "data"}), encoding="utf-8")
            self.assertTrue(export(config))
            output = root / "data"
            self.assertEqual(validate_data(output), (1, 0))
            before = {p.relative_to(output): (p.read_bytes(), p.stat().st_mtime_ns) for p in output.rglob("*.json")}
            self.assertFalse(export(config))
            self.assertEqual(before, {p.relative_to(output): (p.read_bytes(), p.stat().st_mtime_ns) for p in output.rglob("*.json")})
            source.write_text('{"SP":', encoding="utf-8")
            with self.assertRaises(ExportError): export(config)
            self.assertEqual(before, {p.relative_to(output): (p.read_bytes(), p.stat().st_mtime_ns) for p in output.rglob("*.json")})

    def test_removes_deleted_song_and_refuses_unmanaged_files(self):
        with temporary_directory() as directory:
            root = Path(directory); records = root / "records"; records.mkdir()
            for name in ("架空曲1", "架空曲2"):
                (records / (name.encode().hex() + ".json")).write_text(json.dumps({"SP": {"NORMAL": {"best": {"score": {"value": 1}}}}}), encoding="utf-8")
            files, manifest, _ = prepare(records, {})
            publish(root / "data", files, manifest)
            (records / ("架空曲1".encode().hex() + ".json")).unlink()
            files, manifest, _ = prepare(records, {})
            publish(root / "data", files, manifest)
            self.assertEqual(len(list((root / "data/songs").glob("*.json"))), 1)
            keep = root / "data/keep.txt"; keep.write_text("keep", encoding="utf-8")
            with self.assertRaises(ExportError): publish(root / "data", files, manifest)
            self.assertEqual(keep.read_text(), "keep")

    def test_validator_rejects_broken_reference_and_changed_best(self):
        with temporary_directory() as directory:
            root = Path(directory); records = root / "records"; records.mkdir()
            (records / ("曲".encode().hex() + ".json")).write_text(json.dumps({"SP": {"NORMAL": {
                "best": {"score": {"value": 100}}, "timestamps": ["20260101-000000"],
                "history": {"20260101-000000": {"score": {"value": 50}}}}}}), encoding="utf-8")
            files, manifest, _ = prepare(records, {})
            output = root / "data"; publish(output, files, manifest)
            self.assertEqual(validate_data(output), (1, 1))
            song_path = next((output / "songs").glob("*.json"))
            song = json.loads(song_path.read_text(encoding="utf-8"))
            song["charts"]["SP"]["NORMAL"]["best"]["score"]["value"] = 99
            song_path.write_text(json.dumps(song), encoding="utf-8")
            with self.assertRaisesRegex(ExportError, "ハッシュ"):
                validate_data(output)
            publish(output, files, manifest)
            index_path = output / "index.json"
            index = json.loads(index_path.read_text(encoding="utf-8"))
            index["songs"][0]["file"] = "../config.local.json"
            index_path.write_text(json.dumps(index), encoding="utf-8")
            with self.assertRaisesRegex(ExportError, "参照"):
                validate_data(output)

    def test_invalid_field_and_date_rejected(self):
        for entry in ({"score": {"value": "123"}}, {"miss_count": {"value": -1}}, {"options": {"battle": "false"}}):
            with self.assertRaises(ExportError):
                normalize_chart({"history": {"20260101-120000": entry}}, "sample", [])
        with self.assertRaises(ExportError):
            normalize_chart({"history": {"20260230-120000": {}}}, "sample", [])


if __name__ == "__main__":
    unittest.main()
