"""The shell scripts in scripts/ and deploy/ (run by systemd and the deploy steps) must be executable in git: a
checkout on the server takes the mode from the index, and a script committed from Windows without it fails with
"permission denied" (the big-money monitor did not start on 2026-10-26 for this reason)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_shell_scripts_are_executable_in_git() -> None:
    if shutil.which("git") is None or not (ROOT / ".git").exists():
        pytest.skip("not a git checkout")
    listing = subprocess.run(["git", "ls-files", "-s", "--", "scripts/*.sh", "deploy/*.sh"], cwd=ROOT,
                             capture_output=True, text=True, check=True).stdout
    modes = {line.split("\t")[1]: line.split()[0] for line in listing.splitlines() if line.strip()}
    assert modes, "no shell scripts found"
    assert [path for path, mode in modes.items() if mode != "100755"] == []
