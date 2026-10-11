"""Build a GitHub Pages artifact containing only the viewer and validated JSON."""
import argparse
import json
from pathlib import Path
import re
import shutil
import sys
import tempfile

from export_records import ExportError, require
from validate_data import validate_data

WEB_FILES = ("index.html", "styles.css", "app.js", "format.js")
MARKER = ".viewer-site.json"
REDIRECT = """<!doctype html>
<html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>IIDX プレイ記録</title>
<script>location.replace('./web/' + location.search + location.hash);</script>
</head><body><a href="./web/">プレイ記録を開く</a></body></html>
"""


def build_site(root, output):
    root, output = root.resolve(), output.resolve()
    require(output != root and not root.is_relative_to(output), "出力先にソースディレクトリを指定できません")
    for source in (root / "web", root / "data", root / "tools", root / "tests", root / ".git"):
        require(not output.is_relative_to(source), "出力先はソース・記録・Git管理領域とは別にしてください")
    songs, rows = validate_data(root / "data")
    for name in WEB_FILES:
        path = root / "web" / name
        require(path.is_file() and not path.is_symlink(), f"画面ファイルがありません: {name}")
    if output.exists():
        require(output.is_dir() and not output.is_symlink(), "出力先は通常のディレクトリにしてください")
        if any(output.iterdir()):
            try:
                marker = json.loads((output / MARKER).read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise ExportError("出力先はviewerの生成サイトではありません。専用ディレクトリを指定してください") from exc
            require(marker == {"generator": "inf-notebook-viewer-site", "version": 1}, "出力先の識別情報が不正です")
            # Do not discard files somebody added to a preview output directory.
            allowed = {"index.html", ".nojekyll", MARKER}
            allowed |= {f"web/{name}" for name in WEB_FILES}
            allowed |= {f"data/{path.relative_to(root / 'data').as_posix()}" for path in (root / "data").rglob("*.json")}
            # Old song JSONs may disappear in the next build; still owned by this builder.
            for path in output.rglob("*"):
                require(not path.is_symlink(), "生成サイト内にシンボリックリンクがあります")
                relative = path.relative_to(output).as_posix()
                if path.is_file():
                    require(relative in allowed or re.fullmatch(r"data/songs/[0-9a-f]{64}\.json", relative),
                            f"生成サイト内に管理対象外のファイルがあります: {relative}")
                else:
                    require(relative in {"web", "data", "data/songs"}, "生成サイト内に管理対象外のディレクトリがあります")
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".export-site-", dir=output.parent)).resolve()
    backup = staging.with_name(staging.name + "-backup")
    require(staging.parent == output.parent and backup.parent == output.parent, "一時出力の場所が不正です")
    try:
        (staging / "web").mkdir()
        for name in WEB_FILES:
            shutil.copyfile(root / "web" / name, staging / "web" / name)
        # Validation above guarantees that data has exactly the referenced JSONs.
        shutil.copytree(root / "data", staging / "data")
        (staging / "index.html").write_text(REDIRECT, encoding="utf-8")
        (staging / ".nojekyll").write_text("", encoding="utf-8")
        (staging / MARKER).write_text(json.dumps({"generator": "inf-notebook-viewer-site", "version": 1}), encoding="utf-8")
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
    print(f"サイト生成完了: {songs}曲 / {rows}履歴 / {output}")
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(".preview/site"))
    args = parser.parse_args()
    try:
        build_site(Path(__file__).resolve().parents[1], args.output)
    except (ExportError, OSError, ValueError) as exc:
        print(f"エラー: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
