"""Validate viewer JSON files and all references without loading source records."""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sys

from export_records import DIFFICULTIES, STYLES, VERSION, ExportError, object_value, options, require, scalar, timestamp


def read(path):
    value = json.loads(path.read_text(encoding="utf-8"))
    object_value(value, path.name)
    require(value.get("schemaVersion") == VERSION, f"{path.name}: 未対応のschemaVersion")
    return value


def validate_data(root):
    manifest, index = read(root / "manifest.json"), read(root / "index.json")
    require(manifest.get("generator") == "inf-notebook-viewer", "manifest: generatorが不正です")
    require(manifest.get("index") == "index.json", "manifest: indexが不正です")
    require(isinstance(index.get("songs"), list) and bool(index["songs"]), "index: songsは空でない配列にしてください")
    try:
        datetime.fromisoformat(manifest["generatedAt"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ExportError("manifest: generatedAtが不正です") from exc
    ids, contents = set(), {"index.json": (root / "index.json").read_bytes()}
    history_count, latest = 0, None
    for entry in index["songs"]:
        object_value(entry, "index.song")
        song_id, title = entry.get("id"), entry.get("title")
        require(isinstance(title, str) and bool(title), "index: 曲名が不正です")
        require(song_id == hashlib.sha256(title.encode("utf-8")).hexdigest(), f"{title}: IDが一致しません")
        require(song_id not in ids, f"{title}: IDが重複しています")
        ids.add(song_id)
        filename = f"songs/{song_id}.json"
        require(entry.get("file") == filename, f"{title}: ファイル参照が不正です")
        song = read(root / filename)
        require(song.get("id") == song_id and song.get("title") == title, f"{title}: 楽曲JSONが一致しません")
        contents[filename] = (root / filename).read_bytes()
        charts = object_value(song.get("charts"), title + ".charts")
        require(bool(charts) and set(charts) <= set(STYLES), f"{title}: プレイスタイルが不正です")
        chart_index = object_value(entry.get("charts"), title + ".index.charts")
        require(set(chart_index) == set(charts), f"{title}: プレイスタイル一覧が一致しません")
        for style, difficulties in charts.items():
            object_value(difficulties, title + "/" + style)
            require(bool(difficulties) and set(difficulties) <= set(DIFFICULTIES), f"{title}: 難易度が不正です")
            object_value(chart_index[style], title + ".index." + style)
            require(set(chart_index[style]) == set(difficulties), f"{title}: 難易度一覧が一致しません")
            for difficulty, chart in difficulties.items():
                context = f"{title}/{style}/{difficulty}"
                object_value(chart, context)
                require(set(chart) == {"best", "history", "level"}, context + ": 譜面の項目が不正です")
                scalar(chart.get("level"), True, context + ".level")
                best = object_value(chart.get("best"), context + ".best")
                for key in ("score", "missCount"):
                    item = object_value(best.get(key), context + ".best." + key)
                    require("value" in item and "options" in item, context + ": bestの項目が欠けています")
                    scalar(item["value"], True, context)
                    validate_options(item["options"], context)
                rows = chart.get("history")
                require(isinstance(rows, list), context + ": historyは配列にしてください")
                ts_values = []
                for row in rows:
                    object_value(row, context)
                    require(set(row) == {"timestamp", "clearType", "djLevel", "score", "missCount", "playSpeed", "options"},
                            context + ": 履歴の項目が不正です")
                    ts_values.append(timestamp(row["timestamp"], context))
                    for key in ("score", "missCount", "playSpeed"):
                        scalar(row[key], True, context + "." + key)
                    for key in ("clearType", "djLevel"):
                        scalar(row[key], False, context + "." + key)
                    validate_options(row["options"], context)
                require(ts_values == sorted(set(ts_values), reverse=True), context + ": 履歴が日付降順でないか重複しています")
                summary = chart_index[style][difficulty]
                require(summary == {"level": chart.get("level"), "historyCount": len(rows)}, context + ": 検索一覧と履歴が一致しません")
                history_count += len(rows)
                if ts_values:
                    latest = max(latest or ts_values[0], ts_values[0])
    require(manifest.get("songCount") == len(ids) and manifest.get("historyCount") == history_count,
            "manifest: 件数が一致しません")
    require(manifest.get("latestPlay") == latest, "manifest: 最新プレイ日時が一致しません")
    actual = {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}
    require(actual == set(contents) | {"manifest.json"}, "出力ディレクトリに不足・余分なファイルがあります")
    digest = hashlib.sha256()
    for name, content in sorted(contents.items()):
        digest.update(name.encode("utf-8") + b"\0" + content)
    require(digest.hexdigest() == manifest.get("contentHash"), "manifest: データのハッシュが一致しません")
    return len(ids), history_count


def validate_options(value, context):
    if value is None:
        return
    object_value(value, context + ".options")
    require(set(value) <= {"arrange", "flip", "assist", "battle", "allscratch", "regularspeed", "special", "unknown"},
            context + ": オプション項目が不正です")
    if "unknown" in value:
        object_value(value["unknown"], context + ".options.unknown")
    options({key: val for key, val in value.items() if key != "unknown"}, context + ".options")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", nargs="?", type=Path, default=Path("data"))
    args = parser.parse_args()
    try:
        songs, rows = validate_data(args.directory)
        print(f"検証成功: {songs}曲 / {rows}履歴")
    except (ExportError, OSError, ValueError) as exc:
        print(f"エラー: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
