import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from export_records import ExportError, export
from sync_records import GitHub, SyncError, check_pr_changes, git, github_links, synchronize, sync_lock


class FakeGitHub:
    """Git uses real local repositories. No test contacts GitHub."""
    def __init__(self, remote):
        self.remote = remote
        self.prs = {}
        self.create_attempts = 0
        self.auto_calls = 0
        self.fail_create = self.fail_auto = self.fail_after_create = False

    def check_settings(self):
        pass

    def find_pr(self, branch):
        pr = self.prs.get(branch)
        if pr and pr["state"] == "OPEN":
            pr["headRefOid"] = git(self.remote, "rev-parse", f"refs/heads/{branch}").stdout.strip()
        return pr

    def create_pr(self, branch, manifest):
        self.create_attempts += 1
        if self.fail_create:
            self.fail_create = False
            raise SyncError("PR create failed")
        pr = {"number": len(self.prs) + 1, "url": f"https://example.invalid/pr/{len(self.prs) + 1}",
              "state": "OPEN", "headRefOid": git(self.remote, "rev-parse", f"refs/heads/{branch}").stdout.strip()}
        self.prs[branch] = pr
        if self.fail_after_create:
            self.fail_after_create = False
            raise SyncError("Response lost after create")
        return pr

    def auto_merge(self, pr, head):
        self.auto_calls += 1
        if self.fail_auto:
            self.fail_auto = False
            raise SyncError("Auto-merge failed")
        assert pr["headRefOid"] == head
        pr["autoMergeRequest"] = {"mergeMethod": "SQUASH"}

    def close_pr(self, pr):
        pr["state"] = "CLOSED"


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix=".export-sync-test-", dir=Path(__file__).resolve().parent)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.records = self.root / "records"; self.records.mkdir()
        self.work = self.root / "work"; self.work.mkdir()
        self.remote = self.root / "remote.git"
        git(self.root, "init", "--bare", "--initial-branch=main", str(self.remote))
        git(self.work, "init", "-b", "main")
        self.configure(self.work)
        git(self.work, "remote", "add", "origin", str(self.remote))
        (self.work / ".gitignore").write_text("config.local.json\n.export-*/\n", encoding="utf-8")
        (self.work / "README.md").write_text("Test viewer\n", encoding="utf-8")
        (self.work / "config.local.json").write_text(json.dumps({"recordsPath": "../records", "outputPath": "data"}), encoding="utf-8")
        self.set_record(100)
        export(self.work / "config.local.json")
        git(self.work, "add", ".gitignore", "README.md", "data")
        git(self.work, "commit", "-m", "Initial fixture")
        git(self.work, "push", "-u", "origin", "main")
        self.github = FakeGitHub(self.remote)
        self.addCleanup(self.clean_worktree)

    def clean_worktree(self):
        path = self.sync_worktree()
        if path.exists():
            assert path.resolve().is_relative_to(self.root.resolve())
            # Disposable fixture only; production never removes dirty worktrees.
            git(self.work, "worktree", "remove", "--force", str(path))

    def configure(self, root):
        for key, value in (("user.name", "Viewer Test"), ("user.email", "viewer-test@example.invalid"),
                           ("core.autocrlf", "false"), ("commit.gpgsign", "false")):
            git(root, "config", key, value)

    def set_record(self, score):
        self.record = self.records / ("架空曲".encode().hex() + ".json")
        self.record.write_text(json.dumps({"DP": {"ANOTHER": {"best": {"score": {"value": score}}}}}), encoding="utf-8")

    def head(self, root=None, ref="HEAD"):
        return git(root or self.work, "rev-parse", ref).stdout.strip()

    def sync(self):
        return synchronize(self.work, Path("config.local.json"), self.github)

    def state(self):
        return json.loads((self.work / ".git/viewer-sync-state.json").read_text(encoding="utf-8"))

    def sync_worktree(self):
        return self.work / ".git/viewer-sync-worktree"

    def peer(self):
        peer = self.root / ("peer-" + uuid.uuid4().hex[:8])
        git(self.root, "clone", str(self.remote), str(peer)); self.configure(peer)
        return peer

    def peer_update(self):
        peer = self.peer()
        (peer / "README.md").write_text("Remote update\n", encoding="utf-8")
        git(peer, "add", "README.md"); git(peer, "commit", "-m", "Remote update")
        git(peer, "push", "origin", "main")
        return self.head(peer)

    def merge_pr(self):
        branch = self.state()["branch"]
        peer = self.peer()
        git(peer, "fetch", "origin", branch); git(peer, "merge", "--squash", "FETCH_HEAD")
        git(peer, "commit", "-m", "Simulated GitHub squash merge"); git(peer, "push", "origin", "main")
        self.github.prs[branch]["state"] = "MERGED"
        return self.head(peer)

    def test_protected_main_never_pushed_and_pending_pr_reused(self):
        hook = self.remote / "hooks/pre-receive"
        hook.write_text('#!/bin/sh\nwhile read old new ref; do\n if [ "$ref" = "refs/heads/main" ]; then exit 1; fi\ndone\n', encoding="utf-8", newline="\n")
        hook.chmod(0o755)
        initial = self.head(); self.set_record(200)
        self.assertTrue(self.sync())
        branch = self.state()["branch"]; updated = self.head(self.sync_worktree())
        self.assertNotEqual(initial, updated)
        self.assertEqual(self.head(), initial); self.assertEqual(self.head(self.remote), initial)
        self.assertEqual(updated, self.head(self.remote, branch))
        self.assertTrue(check_pr_changes(self.sync_worktree(), initial, updated))
        self.assertEqual(git(self.work, "status", "--porcelain").stdout, "")
        self.assertFalse(self.sync()); self.assertEqual(self.github.create_attempts, 1)
        self.assertEqual(self.github.auto_calls, 1)
        self.assertEqual(self.head(self.sync_worktree()), updated)
        self.set_record(300); self.assertTrue(self.sync())
        self.assertEqual(self.state()["branch"], branch); self.assertEqual(self.github.create_attempts, 1)

    def test_no_change_creates_neither_branch_nor_pr(self):
        initial = self.head(); self.assertFalse(self.sync())
        self.assertEqual(self.head(), initial); self.assertFalse(self.sync_worktree().exists())
        self.assertFalse((self.work / ".git/viewer-sync-state.json").exists())
        self.assertEqual(self.github.create_attempts, 0)

    def test_failed_push_retried_even_when_export_unchanged(self):
        hook = self.remote / "hooks/pre-receive"
        hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8", newline="\n"); hook.chmod(0o755)
        initial = self.head(); self.set_record(200)
        with self.assertRaisesRegex(SyncError, "保持"): self.sync()
        pending = self.head(self.sync_worktree()); hook.unlink()
        self.assertFalse(self.sync())
        self.assertEqual(self.head(self.remote, self.state()["branch"]), pending)
        self.assertEqual(self.head(self.remote), initial); self.assertEqual(self.github.create_attempts, 1)

    def test_pr_creation_failure_and_lost_response_recoverable(self):
        self.set_record(200); self.github.fail_create = True
        with self.assertRaisesRegex(SyncError, "再試行"): self.sync()
        pending = self.head(self.sync_worktree()); self.github.fail_after_create = True
        with self.assertRaisesRegex(SyncError, "再試行"): self.sync()
        self.assertFalse(self.sync()); self.assertEqual(len(self.github.prs), 1)
        self.assertEqual(self.head(self.sync_worktree()), pending)

    def test_auto_merge_failure_retried_without_duplicate_pr(self):
        self.set_record(200); self.github.fail_auto = True
        with self.assertRaisesRegex(SyncError, "再試行"): self.sync()
        self.assertFalse(self.sync()); self.assertEqual(self.github.create_attempts, 1)
        self.assertEqual(self.github.auto_calls, 2)

    def test_squash_merge_followed_by_new_update_uses_new_branch(self):
        self.set_record(200); self.sync(); previous = self.state()["branch"]
        merged = self.merge_pr(); self.set_record(300)
        self.assertTrue(self.sync()); self.assertEqual(self.head(), merged)
        self.assertNotEqual(self.state()["branch"], previous); self.assertEqual(self.github.create_attempts, 2)

    def test_merged_pr_without_new_data_cleans_up_without_new_pr(self):
        self.set_record(200); self.sync(); self.merge_pr()
        self.assertFalse(self.sync()); self.assertFalse(self.sync_worktree().exists())
        self.assertFalse((self.work / ".git/viewer-sync-state.json").exists())
        self.assertEqual(self.github.create_attempts, 1)

    def test_closed_unmerged_pr_stops_and_preserves_branch(self):
        self.set_record(200); self.sync(); branch = self.state()["branch"]
        self.github.prs[branch]["state"] = "CLOSED"
        with self.assertRaisesRegex(SyncError, "閉じられ"): self.sync()
        self.assertEqual(self.state()["branch"], branch)

    def test_pending_branch_code_changes_not_auto_merged(self):
        self.set_record(200); self.sync(); worktree = self.sync_worktree()
        (worktree / "README.md").write_text("Unexpected code change\n", encoding="utf-8")
        git(worktree, "add", "README.md"); git(worktree, "commit", "-m", "Unexpected code")
        with self.assertRaisesRegex(ExportError, "data以外"): self.sync()
        self.assertEqual(self.github.auto_calls, 1)

    def test_remote_code_update_merged_into_pending_data_pr(self):
        self.set_record(200); self.sync(); remote = self.peer_update(); self.set_record(300)
        self.assertTrue(self.sync()); self.assertEqual(self.head(), remote)
        self.assertTrue(check_pr_changes(self.sync_worktree(), remote, "HEAD"))
        self.assertEqual(self.github.create_attempts, 1)

    def test_return_to_main_data_closes_unnecessary_pr(self):
        self.set_record(200); self.sync(); branch = self.state()["branch"]
        self.set_record(100); self.sync()
        self.assertEqual(self.github.prs[branch]["state"], "CLOSED"); self.assertFalse(self.sync_worktree().exists())

    def test_dirty_and_staged_changes_stop_before_export(self):
        initial = self.head(); manifest = (self.work / "data/manifest.json").read_bytes()
        self.set_record(200); (self.work / "README.md").write_text("Local edit\n", encoding="utf-8")
        with self.assertRaisesRegex(ExportError, "data以外"): self.sync()
        self.assertEqual((self.work / "data/manifest.json").read_bytes(), manifest)
        git(self.work, "add", "README.md")
        with self.assertRaisesRegex(ExportError, "ステージ済み"): self.sync()
        self.assertEqual(self.head(), initial)

    def test_divergence_and_unpushed_main_commits_not_pushed(self):
        self.set_record(200); export(self.work / "config.local.json")
        git(self.work, "add", "data"); git(self.work, "commit", "-m", "Legacy local data"); local = self.head()
        with self.assertRaisesRegex(SyncError, "未送信"): self.sync()
        self.assertEqual(self.head(), local); self.peer_update()
        with self.assertRaisesRegex(SyncError, "分岐"): self.sync()
        self.assertEqual(self.head(), local)

    def test_invalid_record_does_not_change_main_or_create_worktree(self):
        initial = self.head(); self.record.write_text("invalid", encoding="utf-8")
        with self.assertRaises(ExportError): self.sync()
        self.assertEqual(self.head(), initial); self.assertFalse(self.sync_worktree().exists())
        self.assertFalse((self.work / ".git/viewer-sync.lock").exists())

    def test_lock_non_main_and_in_progress_merge_prevent_sync(self):
        with sync_lock(self.work):
            with self.assertRaisesRegex(SyncError, "実行中"): self.sync()
        marker = self.work / ".git/MERGE_HEAD"; marker.write_text(self.head() + "\n", encoding="utf-8")
        with self.assertRaisesRegex(ExportError, "進行中"): self.sync()
        marker.unlink(); git(self.work, "switch", "-c", "test-branch")
        with self.assertRaisesRegex(ExportError, "main"): self.sync()


class GitHubTests(unittest.TestCase):
    def test_github_links_support_https_and_ssh(self):
        for url in ("https://github.com/person/viewer.git", "git@github.com:person/viewer.git", "ssh://git@github.com/person/viewer.git"):
            self.assertEqual(github_links(url), {"actions": "https://github.com/person/viewer/actions", "pages": "https://person.github.io/viewer/"})

    def test_auto_merge_pins_head_without_admin_bypass(self):
        provider = GitHub(Path.cwd(), "https://github.com/person/viewer.git")
        provider.merge_method = "squash"; provider.gh = Mock()
        provider.auto_merge({"number": 5}, "a" * 40)
        provider.gh.assert_called_once_with("pr", "merge", "5", "--repo", "person/viewer", "--auto", "--squash", "--match-head-commit", "a" * 40)

    def test_settings_require_auto_merge_and_accept_merge_commits(self):
        provider = GitHub(Path.cwd(), "https://github.com/person/viewer.git")
        provider.gh = Mock(side_effect=["", json.dumps({"default_branch": "main", "allow_auto_merge": False})])
        with self.assertRaisesRegex(ExportError, "Allow auto-merge"): provider.check_settings()
        provider.gh = Mock(side_effect=["", json.dumps({"default_branch": "main", "allow_auto_merge": True, "allow_merge_commit": True})])
        provider.check_settings(); self.assertEqual(provider.merge_method, "merge")


if __name__ == "__main__":
    unittest.main()
