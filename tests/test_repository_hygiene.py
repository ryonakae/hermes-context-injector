import importlib.util
import subprocess
from pathlib import Path

import yaml


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def load_plugin():
    path = repo_root() / "__init__.py"
    spec = importlib.util.spec_from_file_location(
        "hermes_context_injector_hygiene_under_test",
        path,
        submodule_search_locations=[str(path.parent)],
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


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


def test_example_config_lists_every_supported_platform():
    root = repo_root()
    plugin = load_plugin()
    data = yaml.safe_load((root / "config.example.yaml").read_text(encoding="utf-8"))

    assert set(data["platforms"]) == set(plugin.SUPPORTED_PLATFORM_KEYS)
