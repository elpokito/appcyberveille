"""Tests de l'app (PWA) : syntaxe des scripts et logique des révisions espacées, avec Node."""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
NODE = shutil.which("node")


@pytest.mark.skipif(not NODE, reason="Node.js absent")
@pytest.mark.parametrize("script", ["app.js", "revision.js", "sw.js"])
def test_syntaxe_des_scripts(script):
    result = subprocess.run([NODE, "--check", str(ROOT / "docs" / script)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(not NODE, reason="Node.js absent")
def test_revisions_espacees():
    result = subprocess.run([NODE, str(ROOT / "tests" / "revision_test.js")], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr + result.stdout
