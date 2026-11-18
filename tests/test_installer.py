import hashlib
import io
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import unittest


TEMPLATE = Path(__file__).resolve().parents[1] / "scripts" / "install.sh.in"


@unittest.skipUnless(shutil.which("sha256sum"), "install.sh targets Debian with sha256sum")
class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.archive = self.directory / "fixture.tar.gz"
        self.launcher = self.directory / "installer"
        bootstrap = b"""#!/bin/bash
IFS= read -r choice
printf 'Started bootstrap with %s and %s. Choice: %s\\n' "$1" "$2" "$choice"
"""
        with tarfile.open(self.archive, "w:gz") as package:
            item = tarfile.TarInfo("dfs-vps-deploy/bootstrap.sh")
            item.size = len(bootstrap)
            package.addfile(item, io.BytesIO(bootstrap))

        curl = self.directory / "curl"
        curl.write_text("""#!/bin/bash
set -euo pipefail
while (( $# >= 2 )); do
    if [[ "$1" == -o ]]; then
        cp -- "$DFS_TEST_ARCHIVE" "$2"
        exit
    fi
    shift
done
exit 2
""")
        curl.chmod(0o700)

    def launch(self, checksum):
        template = TEMPLATE.read_text()
        self.launcher.write_text(
            template.replace("@ARCHIVE_URL@", "https://example.invalid/fixture.tar.gz")
            .replace("@ARCHIVE_SHA256@", checksum)
        )
        environment = os.environ | {
            "PATH": f"{self.directory}:{os.environ['PATH']}",
            "DFS_TEST_ARCHIVE": str(self.archive),
        }
        return subprocess.run(
            ["bash", str(self.launcher), "migrate", "--no-dns"], input="2\n",
            capture_output=True, text=True, timeout=10, env=environment,
        )

    def test_verified_archive_preserves_arguments_and_stdin(self):
        result = self.launch(hashlib.sha256(self.archive.read_bytes()).hexdigest())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("source.tar.gz: OK", result.stdout)
        self.assertIn("Started bootstrap with migrate and --no-dns. Choice: 2", result.stdout)

    def test_wrong_checksum_prevents_running_bootstrap(self):
        result = self.launch("0" * 64)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("Started bootstrap", result.stdout)
