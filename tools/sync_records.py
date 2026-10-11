"""Generate, validate and push only viewer data on a clean main checkout."""
import argparse
from contextlib import contextmanager
from pathlib import Path
import pickle
import re
import subprocess
import sys

from export_records import ExportError, export, read_json_stable, require
from validate_data import validate_data

DATA_PATH = re.compile(r"data/(?:index\.json|manifest\.json|songs/[0-9a-f]{64}\.json)\Z")


class SyncError(RuntimeError):
    pass


def git(root, *args, allowed=(0,)):
    result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, encoding="utf-8", errors="replace")
    if result.returncode not in allowed:
        raise SyncError(f"git {args[0]} が失敗しました。\n{result.stderr.strip() or result.stdout.strip()}")
    return result


def assert_main(root):
    actual = Path(git(root, "rev-parse", "--show-toplevel").stdout.strip()).resolve()
    require(actual == root, "viewerのリポジトリルートから実行してください")
    require(git(root, "branch", "--show-current").stdout.strip() == "main", "同期はmainブランチで実行してください")
    for name in ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "rebase-merge", "rebase-apply"):
        require(not git_path(root, name).exists(), "Gitのマージ・rebase等が進行中です。先にその操作を完了してください")


def assert_worktree(root):
    # An existing index belongs to the user, even when it contains data only.
    require(git(root, "diff", "--cached", "--quiet", allowed=(0, 1)).returncode == 0,
            "ステージ済みの変更があります。先にコミットするか、ステージを解除してください")
    records = git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all").stdout.split("\0")
    paths, offset = [], 0
    while offset < len(records) and records[offset]:
        item = records[offset]
        paths.append(item[3:])
        if "R" in item[:2] or "C" in item[:2]:
            offset += 1
            paths.append(records[offset])
        offset += 1
    require(all(DATA_PATH.fullmatch(path) for path in paths),
            "data以外に未コミットの変更があります。先に変更をコミットしてください")
    return bool(paths)


def git_path(root, name):
    path = Path(git(root, "rev-parse", "--git-path", name).stdout.strip())
    return (root / path).resolve()


@contextmanager
def sync_lock(root):
    path = git_path(root, "viewer-sync.lock")
    require(path.parent == git_path(root, "."), "同期ロックの場所が不正です")
    try:
        handle = path.open("x", encoding="utf-8")
    except FileExistsError as exc:
        raise SyncError("同期がすでに実行中です。前回が強制終了した場合はREADMEのロック解除手順を確認してください") from exc
    try:
        with handle:
            handle.write("inf-notebook-viewer sync\n")
        yield
    finally:
        path.unlink()


def ancestor(root, earlier, later):
    return git(root, "merge-base", "--is-ancestor", earlier, later, allowed=(0, 1)).returncode == 0


def pending_data_only(root, remote, head):
    for commit in git(root, "rev-list", f"{remote}..{head}").stdout.splitlines():
        changed = git(root, "diff-tree", "--root", "-m", "--no-commit-id", "--name-only", "-r", "-z", commit).stdout.split("\0")
        require(all(DATA_PATH.fullmatch(path) for path in changed if path),
                "未送信のコミットにdata以外の変更があります。先に通常のgit pushで送信してください")


def github_links(remote_url):
    match = re.fullmatch(r"(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)([\w.-]+)/([\w.-]+?)(?:\.git)?/?", remote_url)
    if not match:
        return None
    owner, repository = match.groups()
    return {"actions": f"https://github.com/{owner}/{repository}/actions",
            "pages": f"https://{owner}.github.io/" + ("" if repository.lower() == f"{owner}.github.io".lower() else f"{repository}/")}


def synchronize(root, config_path):
    root = root.resolve()
    config_path = (root / config_path).resolve()
    assert_main(root)
    config = read_json_stable(config_path)
    require(isinstance(config, dict), "設定はJSONオブジェクトにしてください")
    output = config.get("outputPath", "data")
    require(isinstance(output, str) and (config_path.parent / output).resolve() == root / "data",
            "同期時のoutputPathはリポジトリのdataディレクトリを指定してください")
    remote_url = git(root, "remote", "get-url", "--push", "origin").stdout.strip()
    # Different fetch/push destinations could send records to an unintended repository.
    require(git(root, "remote", "get-url", "origin").stdout.strip() == remote_url,
            "originのfetch先とpush先が異なります。設定を確認してください")
    with sync_lock(root):
        dirty = assert_worktree(root)
        print("origin/mainの更新を確認しています…", flush=True)
        git(root, "fetch", "--no-tags", "origin", "main:refs/remotes/origin/main")
        remote = git(root, "rev-parse", "refs/remotes/origin/main").stdout.strip()
        head = git(root, "rev-parse", "HEAD").stdout.strip()
        if head != remote:
            if ancestor(root, head, remote):
                require(not dirty, "remoteに更新があり、ローカルのdataも変更されています。先に変更内容を確認してください")
                changed = git(root, "diff", "--name-only", "-z", head, remote).stdout.split("\0")
                git(root, "merge", "--ff-only", remote)
                if any(path.startswith("tools/") for path in changed):
                    raise SyncError("同期ツールの更新を取り込みました。新しいコードを使うため、同じ同期コマンドをもう一度実行してください")
            elif ancestor(root, remote, head):
                pending_data_only(root, remote, head)
            else:
                raise SyncError("mainとorigin/mainの履歴が分岐しています。自動で解決せず停止しました")
        assert_main(root)
        assert_worktree(root)
        export(config_path)
        songs, rows = validate_data(root / "data")
        print(f"検証成功: {songs}曲 / {rows}履歴", flush=True)
        assert_worktree(root)
        git(root, "add", "-A", "--", "data")
        changed = git(root, "diff", "--cached", "--quiet", allowed=(0, 1)).returncode != 0
        if changed:
            try:
                git(root, "commit", "-m", "プレイ記録を更新", "--", "data")
            except SyncError:
                # Only our data was staged. Leave generated files, but not a blocking index.
                git(root, "restore", "--staged", "--", "data")
                raise
        head = git(root, "rev-parse", "HEAD").stdout.strip()
        if head != remote:
            pending_data_only(root, remote, head)
            try:
                git(root, "push", "origin", "HEAD:refs/heads/main")
            except SyncError as exc:
                raise SyncError(f"pushできませんでした。生成データと未送信コミットは保持しています。同じコマンドで再試行できます。\n{exc}") from exc
            print("push成功。GitHub Actionsでの公開完了は別途確認してください。")
        else:
            print("変更も未送信コミットもありません。pushは不要です。")
        links = github_links(remote_url)
        if links:
            print(f"公開状況: {links['actions']}\nPages URL: {links['pages']}")
        return changed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("config.local.json"))
    args = parser.parse_args()
    try:
        synchronize(Path(__file__).resolve().parents[1], args.config)
    except (ExportError, SyncError, OSError, ValueError, pickle.UnpicklingError, EOFError) as exc:
        print(f"エラー: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
