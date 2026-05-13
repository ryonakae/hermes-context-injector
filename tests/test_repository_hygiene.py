import subprocess
from pathlib import Path


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo_root(),
        text=True,
        capture_output=True,
        check=False,
    )


def test_example_config_is_tracked_and_local_config_is_ignored():
    root = repo_root()

    assert (root / "config.example.yaml").exists()

    tracked = git("ls-files", "config.example.yaml", "config.yaml")
    assert tracked.returncode == 0, tracked.stderr
    tracked_files = set(tracked.stdout.splitlines())
    assert "config.example.yaml" in tracked_files
    assert "config.yaml" not in tracked_files

    ignored = git("check-ignore", "config.yaml")
    assert ignored.returncode == 0, ignored.stderr
    assert ignored.stdout.strip() == "config.yaml"
