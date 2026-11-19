import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dfs import cli, linux
from dfs.model import DeployError


class ToolUpdateTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.source = self.root / "download"
        config = self.source / "config"
        config.mkdir(parents=True)
        (config / "xray.service").write_text("unit fixture")
        (self.source / "dfs").mkdir()
        (self.source / "dfs_vps_deploy.py").write_text("# entry point fixture")
        paths = patch.multiple(linux, CONFIG=config, TOOL=self.root / "installed",
                               LAUNCHER=self.root / "launcher", XRAY_UNIT=self.root / "unit", VERSION="0.1.2")
        paths.start()
        self.addCleanup(paths.stop)

    def previous_launcher(self, version="0.1.1"):
        # Exact wrapper written by both earlier releases.
        return ("#!/bin/sh\nexport PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin\n"
                f'exec /usr/bin/python3 -EsB "{linux.TOOL}/{version}/dfs_vps_deploy.py" "$@"\n').encode()

    def test_repair_activates_new_tool_before_rendering(self):
        linux.LAUNCHER.write_bytes(self.previous_launcher())
        state = cli.Deployment(node=None, mode="NEW", phase="ready")
        store = cli.Store(self.root)

        def verify_active(node, live):
            self.assertTrue(live)
            self.assertEqual(linux.LAUNCHER.read_bytes(), self.previous_launcher("0.1.2"))
            self.assertEqual(linux.LAUNCHER.stat().st_mode & 0o777, 0o755)
            self.assertEqual((linux.TOOL / "0.1.2/config/xray.service").read_text(), "unit fixture")

        with (
            patch.object(store, "snapshot", side_effect=store.pending.touch),
            patch.object(linux, "write_configs", side_effect=verify_active) as write_configs,
            patch.object(linux, "restart_services"),
            patch.object(cli, "final_checks"),
            patch.object(cli, "note"),
        ):
            cli.local_operation(store, state, "repair")
        write_configs.assert_called_once_with(state.node, live=True)
        with patch.object(linux, "atomic_file") as write:
            linux.install_tool()
        write.assert_not_called()

    def test_newer_or_unrecognized_launchers_preserved(self):
        for content in (self.previous_launcher("0.1.10"), b"#!/bin/sh\n# local wrapper\n"):
            with self.subTest(content=content):
                linux.LAUNCHER.write_bytes(content)
                with self.assertRaises(DeployError):
                    linux.install_tool()
                self.assertEqual(linux.LAUNCHER.read_bytes(), content)
                self.assertFalse(linux.TOOL.exists())
        linux.LAUNCHER.unlink()
        linux.LAUNCHER.symlink_to(self.root / "missing")
        with self.assertRaises(DeployError):
            linux.install_tool()
        self.assertTrue(linux.LAUNCHER.is_symlink())

    def test_partial_copy_preserves_working_launcher_and_allows_retry(self):
        linux.LAUNCHER.write_bytes(self.previous_launcher())
        with patch.object(linux.shutil, "copyfile", side_effect=OSError("copy interrupted")):
            with self.assertRaises(OSError):
                linux.install_tool()
        self.assertEqual(linux.LAUNCHER.read_bytes(), self.previous_launcher())
        self.assertFalse((linux.TOOL / "0.1.2").exists())
        linux.install_tool()
        self.assertEqual(linux.LAUNCHER.read_bytes(), self.previous_launcher("0.1.2"))
