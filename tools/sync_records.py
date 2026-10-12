"""Generate viewer data and submit an auto-merge PR without pushing main."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import pickle
import re
import shutil
import subprocess
import sys
import tempfile
import uuid

from export_records import ExportError, export, publish, read_json_stable, require
from validate_data import validate_data

DATA_PATH = re.compile(r"data/(?:index\.json|manifest\.json|songs/[0-9a-f]{64}\.json)\Z")
SYNC_BRANCH = re.compile(r"data/update-records-\d{8}T\d{6}-[0-9a-f]{8}\Z")


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


def check_pr_changes(root, base, head):
    """Check the same three-dot diff GitHub presents for the PR."""
    changed = [path for path in git(root, "diff", "--name-only", "-z", f"{base}...{head}").stdout.split("\0") if path]
    require(all(DATA_PATH.fullmatch(path) for path in changed), "更新PRにdata以外の変更があります。自動マージは予約しません")
    return bool(changed)


def github_links(remote_url):
    match = re.fullmatch(r"(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)([\w.-]+)/([\w.-]+?)(?:\.git)?/?", remote_url)
    if not match:
        return None
    owner, repository = match.groups()
    return {"actions": f"https://github.com/{owner}/{repository}/actions",
            "pages": f"https://{owner}.github.io/" + ("" if repository.lower() == f"{owner}.github.io".lower() else f"{repository}/")}


class GitHub:
    def __init__(self, root, remote_url):
        links = github_links(remote_url)
        require(links is not None, "originにはgithub.comのリポジトリを指定してください")
        self.root = root
        self.repository = links["actions"].removeprefix("https://github.com/").removesuffix("/actions")
        self.merge_method = None

    def gh(self, *args):
        try:
            result = subprocess.run(["gh", *args], cwd=self.root, capture_output=True, encoding="utf-8", errors="replace")
        except FileNotFoundError as exc:
            raise SyncError("GitHub CLI (gh)をインストールし、gh auth loginを実行してください") from exc
        if result.returncode:
            raise SyncError(f"GitHub CLIが失敗しました。認証・PR・リポジトリ設定を確認してください。\n{result.stderr.strip()}")
        return result.stdout.strip()

    def check_settings(self):
        self.gh("auth", "status", "--hostname", "github.com")
        settings = json.loads(self.gh("api", f"repos/{self.repository}"))
        require(settings.get("default_branch") == "main", "デフォルトブランチをmainに設定してください")
        require(settings.get("allow_auto_merge") is True, "Settings → GeneralでAllow auto-mergeを有効にしてください")
        for method in ("squash", "merge", "rebase"):
            key = "allow_merge_commit" if method == "merge" else f"allow_{method}_merge"
            if settings.get(key):
                self.merge_method = method
                return
        raise SyncError("利用できるPRのマージ方法がありません")

    def find_pr(self, branch):
        prs = json.loads(self.gh("pr", "list", "--repo", self.repository, "--state", "all", "--base", "main", "--head", branch,
                                 "--limit", "10", "--json", "number,url,state,headRefOid,isCrossRepository,autoMergeRequest"))
        prs = [pr for pr in prs if not pr["isCrossRepository"]]
        require(len(prs) <= 1, "更新ブランチに複数のPRがあります。手動で確認してください")
        return prs[0] if prs else None

    def create_pr(self, branch, manifest):
        body = (f"inf-notebookの記録から生成した閲覧用JSONを更新します。変更対象はdataのみです。\n\n"
                f"- 楽曲: {manifest['songCount']}曲\n- プレイ履歴: {manifest['historyCount']}件\n"
                "- ローカルでJSONの形式・参照・件数・内容ハッシュを検証済み\n")
        with tempfile.TemporaryDirectory(prefix=".export-pr-", dir=self.root) as directory:
            path = Path(directory) / "body.md"
            path.write_text(body, encoding="utf-8")
            self.gh("pr", "create", "--repo", self.repository, "--base", "main", "--head", branch,
                    "--title", "プレイ記録を更新", "--body-file", str(path))
        pr = self.find_pr(branch)
        require(pr is not None, "作成したPRを取得できませんでした。同じコマンドで再試行してください")
        return pr

    def auto_merge(self, pr, head):
        self.gh("pr", "merge", str(pr["number"]), "--repo", self.repository, "--auto", f"--{self.merge_method}",
                "--match-head-commit", head)

    def close_pr(self, pr):
        self.gh("pr", "close", str(pr["number"]), "--repo", self.repository)


def save_state(path, state):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state), encoding="utf-8")
    temporary.replace(path)


def assert_sync_worktree(worktree, branch):
    require(worktree.is_dir() and not worktree.is_symlink(), "同期用worktreeがありません。READMEの復旧手順を確認してください")
    require(git(worktree, "branch", "--show-current").stdout.strip() == branch, "同期用worktreeのブランチが一致しません")
    for name in ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "rebase-merge", "rebase-apply"):
        require(not git_path(worktree, name).exists(), "同期用worktreeでGit操作が進行中です。先に解決してください")
    assert_worktree(worktree)


def release_worktree(root, worktree, state_path):
    require(worktree == git_path(root, "viewer-sync-worktree"), "同期用worktreeの場所が不正です")
    require(not git(worktree, "status", "--porcelain").stdout, "同期用worktreeに変更があります。自動削除せず保持します")
    git(root, "worktree", "remove", str(worktree))
    # Keep the local branch and its commits recoverable; never force-delete it.
    state_path.unlink()


@contextmanager
def candidate_data(root, config_path, config, reference):
    """Generate in an ignored temporary directory; leave the user's main intact."""
    validate_data(reference / "data")
    with tempfile.TemporaryDirectory(prefix=".export-sync-", dir=root) as directory:
        temporary = Path(directory)
        shutil.copytree(reference / "data", temporary / "data")
        candidate_config = dict(config)
        for key in ("recordsPath", "musicTablePath"):
            value = config.get(key)
            require(value is None or isinstance(value, str) and bool(value), f"設定.{key}: パス文字列が必要です")
            candidate_config[key] = str((config_path.parent / value).resolve()) if value else None
        candidate_config["outputPath"] = str((temporary / "data").resolve())
        temporary_config = temporary / "config.json"
        temporary_config.write_text(json.dumps(candidate_config), encoding="utf-8")
        export(temporary_config)
        validate_data(temporary / "data")
        yield temporary / "data"


def synchronize(root, config_path, github=None):
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
    require(not assert_worktree(root), "mainに未コミットのdataがあります。保存・コミット内容を確認してから実行してください")
    github = github or GitHub(root, remote_url)
    github.check_settings()
    with sync_lock(root):
        print("origin/mainの更新を確認しています…", flush=True)
        git(root, "fetch", "--no-tags", "origin", "main:refs/remotes/origin/main")
        remote = git(root, "rev-parse", "refs/remotes/origin/main").stdout.strip()
        head = git(root, "rev-parse", "HEAD").stdout.strip()
        if head != remote:
            if ancestor(root, head, remote):
                changed = git(root, "diff", "--name-only", "-z", head, remote).stdout.split("\0")
                git(root, "merge", "--ff-only", remote)
                if any(path.startswith("tools/") for path in changed):
                    raise SyncError("同期ツールの更新を取り込みました。新しいコードを使うため、同じ同期コマンドをもう一度実行してください")
            elif ancestor(root, remote, head):
                raise SyncError("mainに未送信コミットがあります。作業ブランチからPRを作成してマージしてください")
            else:
                raise SyncError("mainとorigin/mainの履歴が分岐しています。自動で解決せず停止しました")
        assert_main(root)
        state_path, worktree = git_path(root, "viewer-sync-state.json"), git_path(root, "viewer-sync-worktree")
        require(state_path.parent == git_path(root, ".") and worktree.parent == state_path.parent,
                "同期状態の保存場所が不正です")
        state, pr = None, None
        if state_path.exists():
            state = read_json_stable(state_path)
            require(isinstance(state, dict) and state.get("remote") == remote_url
                    and isinstance(state.get("branch"), str) and SYNC_BRANCH.fullmatch(state["branch"]), "同期状態が不正です")
            assert_sync_worktree(worktree, state["branch"])
            pr = github.find_pr(state["branch"])
            if pr and pr["state"] == "MERGED":
                release_worktree(root, worktree, state_path)
                state, pr = None, None
            elif pr and pr["state"] == "CLOSED":
                raise SyncError(f"更新PRはマージせず閉じられています。再開するか内容を確認してください: {pr['url']}")
        else:
            require(not worktree.exists(), "状態ファイルのない同期用worktreeがあります。READMEの復旧手順を確認してください")
        reference = worktree if state else root
        if state:
            check_pr_changes(worktree, remote, "HEAD")
            if not ancestor(worktree, remote, "HEAD"):
                require(not assert_worktree(worktree), "同期用worktreeのdataに未コミット変更があります。先に確認してください")
                git(worktree, "merge", "--no-edit", remote)
                check_pr_changes(worktree, remote, "HEAD")
        with candidate_data(root, config_path, config, reference) as candidate:
            manifest = read_json_stable(candidate / "manifest.json")
            if manifest["contentHash"] == read_json_stable(root / "data/manifest.json")["contentHash"]:
                if state:
                    if pr:
                        github.close_pr(pr)
                    release_worktree(root, worktree, state_path)
                print("記録に変更がありません。PR・pushは不要です。")
                return False
            if not state:
                branch = "data/update-records-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8]
                git(root, "worktree", "add", "-b", branch, str(worktree), remote)
                state = {"branch": branch, "remote": remote_url}
                save_state(state_path, state)
            files = {path.relative_to(candidate).as_posix(): path.read_bytes() for path in candidate.rglob("*.json") if path.name != "manifest.json"}
            publish(worktree / "data", files, manifest)
        validate_data(worktree / "data")
        assert_sync_worktree(worktree, state["branch"])
        git(worktree, "add", "-A", "--", "data")
        changed = git(worktree, "diff", "--cached", "--quiet", allowed=(0, 1)).returncode != 0
        if changed:
            try:
                git(worktree, "commit", "-m", "プレイ記録を更新", "--", "data")
            except SyncError:
                git(worktree, "restore", "--staged", "--", "data")
                raise
        if not check_pr_changes(worktree, remote, "HEAD"):
            if pr:
                github.close_pr(pr)
            release_worktree(root, worktree, state_path)
            print("記録がmainと一致しています。更新PRは不要です。")
            return changed
        head = git(worktree, "rev-parse", "HEAD").stdout.strip()
        try:
            git(worktree, "push", "--set-upstream", "origin", f"HEAD:refs/heads/{state['branch']}")
            pr = github.find_pr(state["branch"]) or github.create_pr(state["branch"], manifest)
            if pr["state"] == "MERGED":
                require(pr["headRefOid"] == head, "前のPRがマージ済みです。保持した更新は同じコマンドの再実行で新しいPRに送信できます")
            else:
                require(pr["state"] == "OPEN" and pr["headRefOid"] == head, "PRの状態または送信したコミットが一致しません。再実行してください")
                if not pr.get("autoMergeRequest"):
                    github.auto_merge(pr, head)
        except (SyncError, ExportError) as exc:
            raise SyncError(f"PR送信・自動マージ予約が完了していません。更新ブランチとコミットは保持しています。同じコマンドで再試行してください。\n{exc}") from exc
        print(f"更新PR: {pr['url']}\nPR送信・自動マージの設定が完了しました。マージ・Pages公開の完了は別途確認してください。")
        links = github_links(remote_url)
        if links:
            print(f"公開状況: {links['actions']}\nPages URL: {links['pages']}")
        return changed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("config.local.json"))
    parser.add_argument("--check-pr", nargs=2, metavar=("BASE_SHA", "HEAD_SHA"), help="CI用: PRの変更対象がdataのみか検証")
    args = parser.parse_args()
    try:
        root = Path(__file__).resolve().parents[1]
        if args.check_pr:
            require(all(re.fullmatch(r"[0-9a-f]{40}", sha) for sha in args.check_pr), "PRのコミットSHAが不正です")
            require(check_pr_changes(root, *args.check_pr), "更新PRにデータの差分がありません")
            print("更新PRの変更対象はdataのみです")
        else:
            synchronize(root, args.config)
    except (ExportError, SyncError, OSError, ValueError, pickle.UnpicklingError, EOFError) as exc:
        print(f"エラー: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
