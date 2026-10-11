import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from export_records import ExportError, export
from sync_records import SyncError, git, github_links, synchronize, sync_lock


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

    def configure(self, root):
        git(root, "config", "user.name", "Viewer Test")
        git(root, "config", "user.email", "viewer-test@example.invalid")
        git(root, "config", "core.autocrlf", "false")
        git(root, "config", "commit.gpgsign", "false")

    def set_record(self, score):
        self.record = self.records / ("架空曲".encode().hex() + ".json")
        self.record.write_text(json.dumps({"DP": {"ANOTHER": {"best": {"score": {"value": score}}}}}), encoding="utf-8")

    def head(self, root=None):
        return git(root or self.work, "rev-parse", "HEAD").stdout.strip()

    def sync(self):
        return synchronize(self.work, Path("config.local.json"))

    def peer_update(self):
        peer = self.root / "peer"
        git(self.root, "clone", str(self.remote), str(peer))
        self.configure(peer)
        (peer / "README.md").write_text("Remote update\n", encoding="utf-8")
        git(peer, "add", "README.md")
        git(peer, "commit", "-m", "Remote update")
        git(peer, "push", "origin", "main")
        return self.head(peer)

    def test_change_commits_only_data_and_second_run_is_noop(self):
        initial = self.head()
        self.set_record(200)
        self.assertTrue(self.sync())
        updated = self.head()
        self.assertNotEqual(initial, updated)
        self.assertEqual(updated, self.head(self.remote))
        files = git(self.work, "diff", "--name-only", initial, updated).stdout.splitlines()
        self.assertTrue(files and all(name.startswith("data/") for name in files))
        self.assertEqual(git(self.work, "status", "--porcelain").stdout, "")
        self.assertFalse(self.sync())
        self.assertEqual(self.head(), updated)

    def test_failed_push_is_retried_even_when_export_unchanged(self):
        hook = self.remote / "hooks" / "pre-receive"
        hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8", newline="\n")
        hook.chmod(0o755)
        initial = self.head()
        self.set_record(200)
        with self.assertRaisesRegex(SyncError, "未送信コミット"):
            self.sync()
        pending = self.head()
        self.assertNotEqual(pending, initial)
        self.assertEqual(self.head(self.remote), initial)
        self.assertEqual(git(self.work, "status", "--porcelain").stdout, "")
        hook.unlink()
        self.assertFalse(self.sync())
        self.assertEqual(self.head(self.remote), pending)

    def test_unrelated_dirty_or_staged_changes_stop_before_export(self):
        initial = self.head()
        manifest = (self.work / "data/manifest.json").read_bytes()
        self.set_record(200)
        (self.work / "README.md").write_text("Local edit\n", encoding="utf-8")
        with self.assertRaisesRegex(ExportError, "data以外"):
            self.sync()
        self.assertEqual((self.work / "data/manifest.json").read_bytes(), manifest)
        git(self.work, "add", "README.md")
        with self.assertRaisesRegex(ExportError, "ステージ済み"):
            self.sync()
        self.assertEqual(self.head(), initial)

    def test_remote_updates_are_fast_forwarded(self):
        remote_head = self.peer_update()
        self.assertFalse(self.sync())
        self.assertEqual(self.head(), remote_head)
        self.assertEqual((self.work / "README.md").read_text(), "Remote update\n")

    def test_divergence_stops_without_resetting_local_history(self):
        remote_head = self.peer_update()
        self.set_record(200)
        export(self.work / "config.local.json")
        git(self.work, "add", "data")
        git(self.work, "commit", "-m", "Local data")
        local_head = self.head()
        with self.assertRaisesRegex(SyncError, "分岐"):
            self.sync()
        self.assertEqual(self.head(), local_head)
        self.assertEqual(self.head(self.remote), remote_head)

    def test_unpushed_code_commit_is_not_published_by_sync(self):
        remote_head = self.head(self.remote)
        (self.work / "README.md").write_text("Local code commit\n", encoding="utf-8")
        git(self.work, "add", "README.md")
        git(self.work, "commit", "-m", "Local code")
        with self.assertRaisesRegex(ExportError, "data以外"):
            self.sync()
        self.assertEqual(self.head(self.remote), remote_head)

    def test_invalid_record_does_not_change_committed_data(self):
        initial = self.head()
        manifest = (self.work / "data/manifest.json").read_bytes()
        self.record.write_text("invalid", encoding="utf-8")
        with self.assertRaises(ExportError):
            self.sync()
        self.assertEqual(self.head(), initial)
        self.assertEqual((self.work / "data/manifest.json").read_bytes(), manifest)
        self.assertFalse((self.work / ".git/viewer-sync.lock").exists())

    def test_lock_and_non_main_branch_prevent_sync(self):
        with sync_lock(self.work):
            with self.assertRaisesRegex(SyncError, "実行中"):
                self.sync()
        git(self.work, "switch", "-c", "test-branch")
        with self.assertRaisesRegex(ExportError, "main"):
            self.sync()

    def test_in_progress_merge_is_not_overwritten(self):
        marker = self.work / ".git/MERGE_HEAD"
        marker.write_text(self.head() + "\n", encoding="utf-8")
        with self.assertRaisesRegex(ExportError, "進行中"):
            self.sync()
        self.assertTrue(marker.exists())

    def test_github_links_support_https_and_ssh(self):
        for url in ("https://github.com/person/viewer.git", "git@github.com:person/viewer.git", "ssh://git@github.com/person/viewer.git"):
            self.assertEqual(github_links(url), {"actions": "https://github.com/person/viewer/actions", "pages": "https://person.github.io/viewer/"})
        self.assertEqual(github_links("https://github.com/person/person.github.io.git")["pages"], "https://person.github.io/")


if __name__ == "__main__":
    unittest.main()
