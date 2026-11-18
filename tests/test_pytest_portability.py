"""The documented pytest command must survive artifacts owned by another account."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("blocked_artifact", ["private_temp", "private_cache"])
def test_pytest_runs_with_unusable_previous_account_artifacts(tmp_path, blocked_artifact):
    root = Path(__file__).resolve().parents[1]
    project = tmp_path / "checkout"
    (project / "tests").mkdir(parents=True)
    shutil.copyfile(root / "pyproject.toml", project / "pyproject.toml")
    shutil.copyfile(root / "tests/conftest.py", project / "tests/conftest.py")
    (project / "tests/test_probe.py").write_text(
        "def test_temporary_storage(tmp_path):\n"
        "    payload = tmp_path / 'probe.txt'\n"
        "    payload.write_text('works')\n"
        "    assert payload.read_text() == 'works'\n",
        encoding="utf-8",
    )
    command = [sys.executable, "-m", "pytest", "-q", "--tb=short", "-W", "error"]
    if blocked_artifact == "private_temp":
        # Inject the OS boundary failure, without changing any real Windows ACL.
        # Only the stale, predictable shared bucket is inaccessible.
        (project / "inaccessible_bucket.py").write_text(
            "import os\nfrom pathlib import Path\n"
            "shared = Path(__file__).resolve().parent / 'tmp'\n"
            "original_scandir = os.scandir\n"
            "def scandir(path):\n"
            "    target = Path(path).resolve()\n"
            "    if target.parent == shared and target.name.startswith('pytest-of-'):\n"
            "        raise PermissionError('previous account owns this pytest bucket')\n"
            "    return original_scandir(path)\n"
            "os.scandir = scandir\n",
            encoding="utf-8",
        )
        command.extend(["-p", "inaccessible_bucket"])
    else:
        # A file at the old cache path makes it genuinely unusable to pytest.
        # Warnings are errors, so hiding a cache warning cannot pass this check.
        (project / ".pytest_cache").write_text("unusable previous cache", encoding="utf-8")
    completed = subprocess.run(
        command, cwd=project, env={**os.environ, "PYTHONUTF8": "1"},
        capture_output=True, text=True, timeout=20,
    )
    assert completed.returncode == 0, (completed.stdout + completed.stderr)[-4000:]
    assert "1 passed" in completed.stdout

