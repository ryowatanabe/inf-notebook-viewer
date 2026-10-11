"""Export inf-notebook records without importing or modifying inf-notebook."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import gzip
import hashlib
import json
import math
from pathlib import Path
import pickle
import re
import shutil
import sys
import tempfile
import time

VERSION = 1
STYLES = ("SP", "DP", "DP BATTLE")
DIFFICULTIES = ("BEGINNER", "NORMAL", "HYPER", "ANOTHER", "LEGGENDARIA")
TIMESTAMP = re.compile(r"\d{8}-\d{6}\Z")
HEX_NAME = re.compile(r"(?:[0-9a-fA-F]{2})+\Z")
FLAGS = ("battle", "allscratch", "regularspeed", "special")
OPTION_STRINGS = ("arrange", "flip", "assist")


class ExportError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise ExportError(message)


def object_value(value, context):
    require(isinstance(value, dict), f"{context}: オブジェクトが必要です")
    return value


def timestamp(value, context):
    require(isinstance(value, str) and TIMESTAMP.fullmatch(value), f"{context}: 不正な日時 {value!r}")
    try:
        datetime.strptime(value, "%Y%m%d-%H%M%S")
    except ValueError as exc:
        raise ExportError(f"{context}: 不正な日時 {value!r}") from exc
    return value


def scalar(value, numeric, context):
    if value is None:
        return None
    if numeric:
        require(type(value) in (int, float) and math.isfinite(value) and value >= 0,
                f"{context}: 非負の数値またはnullが必要です")
    else:
        require(isinstance(value, str), f"{context}: 文字列またはnullが必要です")
    return value


def options(value, context):
    if value is None:
        return None
    value = object_value(value, context)
    result = {}
    for key in OPTION_STRINGS:
        if key in value:
            result[key] = scalar(value[key], False, f"{context}.{key}")
    for key in FLAGS:
        if key in value:
            require(value[key] is None or type(value[key]) is bool, f"{context}.{key}: booleanが必要です")
            result[key] = value[key]
    # Preserve unfamiliar option fields instead of silently calling them normal.
    unknown = {key: val for key, val in value.items() if key not in OPTION_STRINGS + FLAGS}
    if unknown:
        result["unknown"] = unknown
    return result


def field(entry, key, numeric, context):
    value = entry.get(key)
    if value is None:
        return None
    return scalar(object_value(value, f"{context}.{key}").get("value"), numeric, f"{context}.{key}.value")


def best_value(value, numeric, context):
    if value is None:
        return {"value": None, "options": None}
    value = object_value(value, context)
    return {"value": scalar(value.get("value"), numeric, context),
            "options": options(value.get("options"), context + ".options")}


def read_json_stable(path, attempts=3):
    """Avoid accepting a file observed while Notebook.save is rewriting it."""
    last = None
    for attempt in range(attempts):
        try:
            before = path.stat()
            raw = path.read_bytes()
            after = path.stat()
            require((before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns),
                    f"{path.name}: 読み取り中に更新されました")
            return json.loads(raw.decode("utf-8-sig"))
        except (OSError, UnicodeError, ValueError) as exc:
            last = exc
            if attempt + 1 < attempts:
                time.sleep(0.15)
    raise ExportError(f"{path.name}: 読み取りに失敗しました: {last}")


def load_music_table(path):
    if path is None:
        return {}
    # Only a user-configured, trusted local inf-notebook resource is accepted.
    with gzip.open(path, "rb") as stream:
        table = pickle.load(stream)
    table = object_value(table, "楽曲テーブル")
    return object_value(table.get("musics"), "楽曲テーブル.musics")


def normalize_chart(source, context, warnings):
    source = object_value(source, context)
    history = object_value(source.get("history", {}), context + ".history")
    times = source.get("timestamps", [])
    require(isinstance(times, list), f"{context}.timestamps: 配列が必要です")
    for ts in times:
        timestamp(ts, context)
        require(ts in history, f"{context}: timestampsに対応する履歴がありません: {ts}")
    extra = set(history) - set(times)
    if extra:
        warnings.append(f"{context}: timestampsにない履歴{len(extra)}件を保持しました")
    rows = []
    for ts in sorted(history, reverse=True):
        timestamp(ts, context)
        entry = object_value(history[ts], context + ".history." + ts)
        rows.append({
            "timestamp": ts,
            "clearType": field(entry, "clear_type", False, context),
            "djLevel": field(entry, "dj_level", False, context),
            "score": field(entry, "score", True, context),
            "missCount": field(entry, "miss_count", True, context),
            "playSpeed": scalar(entry.get("playspeed"), True, context + ".playspeed"),
            "options": options(entry.get("options"), context + ".options"),
        })
    best = object_value(source.get("best", {}), context + ".best")
    return {"best": {"score": best_value(best.get("score"), True, context + ".best.score"),
                     "missCount": best_value(best.get("miss_count"), True, context + ".best.miss_count")},
            "history": rows}


def encode(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")


def prepare(records, music_table):
    require(records.is_dir(), f"recordsディレクトリがありません: {records}")
    files, songs, warnings = {}, [], []
    latest, history_count = None, 0
    for path in sorted(records.glob("*.json")):
        if not HEX_NAME.fullmatch(path.stem):
            continue
        try:
            title = bytes.fromhex(path.stem).decode("utf-8")
        except UnicodeError as exc:
            raise ExportError(f"{path.name}: 曲名をUTF-8として復号できません") from exc
        source = object_value(read_json_stable(path), title)
        require(not (set(source) - set(STYLES)), f"{title}: 未対応のプレイスタイル {set(source) - set(STYLES)}")
        song_id = hashlib.sha256(title.encode("utf-8")).hexdigest()
        charts, availability = {}, {}
        for style in STYLES:
            style_source = object_value(source.get(style, {}), title + "/" + style)
            require(not (set(style_source) - set(DIFFICULTIES)), f"{title}/{style}: 未対応の難易度")
            style_charts, style_index = {}, {}
            for difficulty in DIFFICULTIES:
                if difficulty not in style_source:
                    continue
                chart = normalize_chart(style_source[difficulty], f"{title}/{style}/{difficulty}", warnings)
                if not chart["history"] and all(item["value"] is None for item in chart["best"].values()):
                    continue
                metadata = music_table.get(title, {})
                level = metadata.get("DP" if style == "DP" else "SP", {}).get(difficulty)
                if isinstance(level, str) and level.isascii() and level.isdecimal():
                    level = int(level)
                chart["level"] = scalar(level, True, title + ".level")
                style_charts[difficulty] = chart
                style_index[difficulty] = {"level": chart["level"], "historyCount": len(chart["history"])}
                history_count += len(chart["history"])
                if chart["history"]:
                    newest = chart["history"][0]["timestamp"]
                    latest = max(latest or newest, newest)
            if style_charts:
                charts[style], availability[style] = style_charts, style_index
        if not charts:
            continue
        filename = f"songs/{song_id}.json"
        require(filename not in files, f"{title}: 曲名IDが重複しています")
        files[filename] = encode({"schemaVersion": VERSION, "id": song_id, "title": title, "charts": charts})
        songs.append({"id": song_id, "title": title, "file": filename, "charts": availability})
    require(bool(songs), "閲覧可能な楽曲記録がありません。既存出力は更新しません")
    songs.sort(key=lambda song: (song["title"].casefold(), song["title"]))
    files["index.json"] = encode({"schemaVersion": VERSION, "songs": songs})
    digest = hashlib.sha256()
    for name, content in sorted(files.items()):
        digest.update(name.encode("utf-8") + b"\0" + content)
    manifest = {"schemaVersion": VERSION, "generator": "inf-notebook-viewer", "index": "index.json",
                "contentHash": digest.hexdigest(), "latestPlay": latest,
                "songCount": len(songs), "historyCount": history_count}
    return files, manifest, warnings


def publish(output, files, manifest):
    """Stage everything first; never merge generated data into an arbitrary folder."""
    old_manifest = None
    if output.exists():
        require(output.is_dir() and not output.is_symlink(), "出力先は通常のディレクトリにしてください")
        if any(output.iterdir()):
            try:
                old_manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise ExportError("出力先に管理対象外のファイルがあります。専用の出力先を指定してください") from exc
            require(old_manifest.get("generator") == "inf-notebook-viewer", "出力先はviewerの生成ディレクトリではありません")
            allowed = {"manifest.json", "index.json", "songs"}
            require(all(p.name in allowed and not p.is_symlink() for p in output.iterdir()), "出力先に管理対象外のファイルがあります")
            songs_dir = output / "songs"
            if songs_dir.exists():
                require(songs_dir.is_dir() and all(p.is_file() and not p.is_symlink() and re.fullmatch(r"[0-9a-f]{64}\.json", p.name)
                                                for p in songs_dir.iterdir()), "songsに管理対象外のファイルがあります")
    if old_manifest and old_manifest.get("contentHash") == manifest["contentHash"]:
        expected = set(files) | {"manifest.json"}
        actual = {p.relative_to(output).as_posix() for p in output.rglob("*") if p.is_file()}
        if actual == expected and all((output / name).read_bytes() == raw for name, raw in files.items()):
            return False
        manifest["generatedAt"] = old_manifest.get("generatedAt", datetime.now(timezone.utc).isoformat())
    else:
        manifest["generatedAt"] = datetime.now(timezone.utc).isoformat()
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".export-", dir=output.parent))
    backup = staging.with_name(staging.name + "-backup")
    try:
        for name, raw in {**files, "manifest.json": encode(manifest)}.items():
            target = staging / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
        if output.exists():
            output.rename(backup)
        try:
            staging.rename(output)
        except OSError:
            if backup.exists():
                backup.rename(output)
            raise
        if backup.exists():
            shutil.rmtree(backup)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return True


def export(config_path):
    config_path = config_path.resolve()
    config = object_value(read_json_stable(config_path), "設定")
    def configured_path(key, default=None):
        value = config.get(key, default)
        require(value is None or isinstance(value, str) and bool(value), f"設定.{key}: パス文字列が必要です")
        return (config_path.parent / value).resolve() if value else None
    records = configured_path("recordsPath")
    require(records is not None, "設定.recordsPathが必要です")
    output = configured_path("outputPath", "data")
    table_path = configured_path("musicTablePath")
    require(output != records and not output.is_relative_to(records) and not records.is_relative_to(output),
            "出力先とrecordsは互いに内包しない別ディレクトリにしてください")
    if table_path:
        require(not table_path.is_relative_to(output), "楽曲テーブルを出力先に置くことはできません")
    require(not config_path.is_relative_to(output), "設定ファイルを出力先に置くことはできません")
    files, manifest, warnings = prepare(records, load_music_table(table_path))
    changed = publish(output, files, manifest)
    for warning in warnings:
        print("警告: " + warning, file=sys.stderr)
    size = sum(p.stat().st_size for p in output.rglob("*.json"))
    print(f"{'生成完了' if changed else '変更なし'}: {manifest['songCount']}曲 / {manifest['historyCount']}履歴 / {size:,} bytes")
    print(f"出力先: {output}")
    return changed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("config.local.json"))
    args = parser.parse_args()
    try:
        export(args.config)
    except (ExportError, OSError, ValueError, pickle.UnpicklingError, EOFError) as exc:
        print(f"エラー: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
