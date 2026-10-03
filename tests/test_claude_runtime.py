import os
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from capability_manager.manager import default_data_dir
from capability_manager.store import Store
from hooks.session_end import main as session_end
from scripts.serve import configure_data_dir


class ClaudeRuntimeTests(unittest.TestCase):
    def test_mcp_server_and_session_end_share_the_same_data_dir(self):
        with tempfile.TemporaryDirectory() as temporary:
            plugins = Path(temporary) / "plugins"
            root = plugins / "cache" / "team" / "contextual-capability-manager" / "0.1.6"
            root.mkdir(parents=True)
            expected = (plugins / "data" / "contextual-capability-manager-team").resolve()
            with patch.dict(os.environ, {"CLAUDE_PLUGIN_DATA": "${CLAUDE_PLUGIN_DATA}"}, clear=True):
                os.environ.pop("CAPMGR_DATA_DIR", None)
                self.assertEqual(configure_data_dir(root), expected)
                self.assertEqual(default_data_dir(), expected)
                Store(default_data_dir() / "state.sqlite3").activate("session", "skill")
            with patch.dict(os.environ, {"CLAUDE_PLUGIN_DATA": str(expected),
                                      "CAPMGR_INCLUDE_CLAUDE_CATALOG": "0"}, clear=True):
                os.environ.pop("CAPMGR_DATA_DIR", None)
                store = Store(default_data_dir() / "state.sqlite3")
                self.assertTrue(store.is_active("session", "skill"))
                with patch("sys.stdin", io.StringIO('{"session_id":"session"}')):
                    session_end()
                self.assertFalse(store.is_active("session", "skill"))

    def test_explicit_absolute_plugin_data_is_preserved(self):
        with tempfile.TemporaryDirectory() as temporary:
            data = Path(temporary) / "data"
            with patch.dict(os.environ, {"CLAUDE_PLUGIN_DATA": str(data)}, clear=True):
                self.assertEqual(configure_data_dir(Path(temporary) / "source"), data)
                self.assertEqual(default_data_dir(), data)


if __name__ == "__main__":
    unittest.main()
