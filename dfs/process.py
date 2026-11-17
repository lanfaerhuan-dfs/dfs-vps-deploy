"""Run platform commands without copying secrets or raw output to the terminal."""
import os
import signal
import subprocess
from pathlib import Path

from .model import DeployError


def run(args, label, *, timeout=30, data=None, require=True, workdir=None):
    try:
        with subprocess.Popen(
            args, stdin=subprocess.PIPE if data is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=workdir, start_new_session=True,
        ) as child:
            try:
                stdout, stderr = child.communicate(data, timeout=timeout)
            except (subprocess.TimeoutExpired, KeyboardInterrupt, DeployError):
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                child.communicate()
                raise
    except subprocess.TimeoutExpired:
        raise DeployError(f"{label}超时。") from None
    except OSError:
        raise DeployError(f"{label}无法执行（{Path(args[0]).name}）。") from None
    if require and child.returncode:
        raise DeployError(f"{label}失败（退出码 {child.returncode}）。")
    return subprocess.CompletedProcess(args, child.returncode, stdout, stderr)


def download(url, dest):
    args = ["curl", "-q", "--noproxy", "*", "--proto", "=https", "--proto-redir", "=https",
            "-fsSL", "--connect-timeout", "8", "--max-time", "75", "--retry", "2",
            "--retry-max-time", "100", "--output", str(dest), url]
    run(args, "下载固定发行件", timeout=105)
