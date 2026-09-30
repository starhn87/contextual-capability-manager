import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from capability_manager.claude_catalog import discover
from capability_manager.installer import check_static_skill, _fetch_git
from capability_manager.manager import CapabilityManager


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


class ClaudeCatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.plugins = self.root / "claude-plugins"
        self.marketplace = self.plugins / "marketplaces/team"
        self.package = self.marketplace / "plugins/mds-plugin"
        skill = self.package / "skills/mds-usage/SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text("---\nname: mds-usage\n---\nUse MDS components.", encoding="utf-8")
        write_json(self.package / ".claude-plugin/plugin.json",
                   {"name": "mds-plugin", "version": "1.0.0"})
        write_json(self.marketplace / ".claude-plugin/marketplace.json", {
            "name": "team", "plugins": [
                {"name": "mds-plugin", "description": "MDS design system components",
                 "source": "./plugins/mds-plugin"},
                {"name": "remote-design", "description": "Special design guidance",
                 "source": {"source": "git-subdir", "url": "https://github.com/team/skills.git",
                            "path": "plugins/design", "sha": "a" * 40}},
                {"name": "untrusted-path", "description": "Unsafe path",
                 "source": "./../../other"},
            ]})

    def test_registered_marketplaces_need_no_manual_catalog(self):
        env = {"CLAUDE_PLUGIN_DATA": str(self.root / "runtime"),
               "CAPMGR_CLAUDE_PLUGINS_DIR": str(self.plugins)}
        with patch.dict(os.environ, env):
            manager = CapabilityManager()
            self.assertIn("claude-team-mds-plugin", manager.catalog)
            self.assertIn("claude-team-remote-design", manager.catalog)
            self.assertNotIn("claude-team-untrusted-path", manager.catalog)
            self.assertEqual(manager.catalog_paths, [])
            result = manager.resolve_static("Use a plugin for MDS design system components", "session")
        self.assertEqual(result["status"], "activated")
        self.assertIn("Use MDS components", result["capability"]["skills"][0]["instructions"])
        self.assertFalse((self.root / "runtime/catalog.json").exists())

    def test_installed_and_executable_local_plugins_are_not_duplicated(self):
        write_json(self.plugins / "installed_plugins.json", {"plugins": {
            "mds-plugin@team": [{"scope": "user"}]}})
        self.assertNotIn("claude-team-mds-plugin", discover(self.plugins))
        (self.plugins / "installed_plugins.json").unlink()
        (self.package / "hooks").mkdir()
        self.assertNotIn("claude-team-mds-plugin", discover(self.plugins))

    def test_executable_remote_candidate_returns_review_status(self):
        env = {"CLAUDE_PLUGIN_DATA": str(self.root / "runtime"),
               "CAPMGR_CLAUDE_PLUGINS_DIR": str(self.plugins)}
        with patch.dict(os.environ, env):
            manager = CapabilityManager()
            with patch.object(manager, "activate", side_effect=PermissionError("review required")):
                result = manager.resolve("Use a plugin for remote design guidance", "session")
        self.assertEqual(result["status"], "requires_review")
        self.assertEqual(result["capability_id"], "claude-team-remote-design")

    def test_remote_git_requires_pinned_commit_and_static_skills(self):
        source = self.root / "source"
        source.mkdir()
        subprocess.run(["git", "init", "-q", str(source)], check=True)
        subprocess.run(["git", "-C", str(source), "config", "user.email", "test@example.com"], check=True)
        subprocess.run(["git", "-C", str(source), "config", "user.name", "Test"], check=True)
        package = source / "plugins/design"
        package.mkdir(parents=True)
        (package / "SKILL.md").write_text("Use pinned guidance.", encoding="utf-8")
        subprocess.run(["git", "-C", str(source), "add", "."], check=True)
        subprocess.run(["git", "-C", str(source), "commit", "-qm", "Add skill"], check=True)
        sha = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"],
                                      text=True).strip()
        staging = self.root / "staging"
        staging.mkdir()
        fetched = _fetch_git({"url": source.as_uri(), "sha": sha,
                              "subdir": "plugins/design"}, staging)
        self.assertEqual((fetched / "SKILL.md").read_text(), "Use pinned guidance.")
        (fetched / "hooks").mkdir()
        with self.assertRaises(PermissionError):
            check_static_skill(fetched)


if __name__ == "__main__":
    unittest.main()
