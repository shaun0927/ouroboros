"""Self-tests for `scripts/check-auto-boundary.py`.

The guard's value is proportional to its precision: it must catch real
domain-keyword leaks AND must not false-positive on benign code (e.g. a
docstring referencing `ooo auto`'s product boundary). Both directions
are exercised here.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts" / "check-auto-boundary.py"


def _load_module():
    """Load the hyphenated script as a module so we can call `main()`
    directly with a custom REPO_ROOT."""
    spec = importlib.util.spec_from_file_location("check_auto_boundary", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def test_clean_repo_passes_via_subprocess() -> None:
    """The current `ooo auto` source must pass the guard.

    This is the runtime invariant the guard exists to protect: at any
    point in main, every watched file is free of forbidden keywords.
    """
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"guard failed on a presumed-clean main:\nSTDOUT: {result.stdout}\nSTDERR: {result.stderr}"
    )
    assert "OK" in result.stdout


def test_offending_file_is_caught(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A synthetic file containing a forbidden keyword must be caught."""
    module = _load_module()
    fake_repo = tmp_path / "repo"
    watched_dir = fake_repo / "src" / "ouroboros" / "cli" / "commands"
    watched_dir.mkdir(parents=True)
    offending = watched_dir / "auto.py"
    offending.write_text(
        "def handle(url: str) -> None:\n"
        "    if 'github.com' in url:\n"
        "        do_pr_things(url)\n"
    )

    monkeypatch.setattr(module, "REPO_ROOT", fake_repo)
    monkeypatch.setattr(
        module,
        "WATCHED_FILES",
        ("src/ouroboros/cli/commands/auto.py",),
    )
    rc = module.main()
    assert rc == 1


def test_allowlist_marker_bypasses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A line carrying the allowlist marker is not flagged."""
    module = _load_module()
    fake_repo = tmp_path / "repo"
    watched_dir = fake_repo / "src" / "ouroboros" / "cli" / "commands"
    watched_dir.mkdir(parents=True)
    offending = watched_dir / "auto.py"
    offending.write_text(
        "# Routing reuses an unrelated GitHub adapter import. # domain-keyword-allowed: legacy plumbing\n"
        "x = 1\n"
    )

    monkeypatch.setattr(module, "REPO_ROOT", fake_repo)
    monkeypatch.setattr(
        module,
        "WATCHED_FILES",
        ("src/ouroboros/cli/commands/auto.py",),
    )
    rc = module.main()
    assert rc == 0


def test_missing_watched_file_does_not_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If a watched file is removed (e.g. in a refactor), the guard does
    NOT crash — it simply has nothing to scan for that path."""
    module = _load_module()
    fake_repo = tmp_path / "repo"
    fake_repo.mkdir()
    monkeypatch.setattr(module, "REPO_ROOT", fake_repo)
    monkeypatch.setattr(
        module,
        "WATCHED_FILES",
        ("src/ouroboros/cli/commands/auto.py",),  # path that doesn't exist
    )
    rc = module.main()
    assert rc == 0


def test_each_forbidden_pattern_independently_caught(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """For each forbidden pattern, a synthetic offender is caught.

    This is a meta-test: it confirms the pattern list itself is wired
    into the scan loop, so future additions to FORBIDDEN_PATTERNS take
    effect without wiring code.
    """
    module = _load_module()

    samples = {
        r"\bgithub\b": "host = 'github.com'",
        r"\bpull_request\b": "if 'pull_request' in payload: ...",
        r"/pulls?/": "uri = '/pulls/42'",
        r"\bapi\.github\.com\b": "host = 'api.github.com'",
        r"\bjira\b": "issue = 'jira issue OUR-1'",
        r"\bslack\b": "channel = 'slack #x'",
        r"\blinear\.app\b": "url = 'https://linear.app/...'",
        r"\bgithub_pr\b": "if event == 'github_pr': ...",
    }
    import re as _re
    for i, pattern in enumerate(module.FORBIDDEN_PATTERNS):
        assert pattern in samples, f"add a sample for {pattern!r}"
        # Sanitize pattern for use as a directory name (every regex
        # metachar would otherwise leak into the path).
        safe = _re.sub(r"[^a-zA-Z0-9]", "_", pattern).strip("_")
        fake_repo = tmp_path / f"case-{i}-{safe}"
        watched_dir = fake_repo / "src" / "ouroboros" / "cli" / "commands"
        watched_dir.mkdir(parents=True)
        (watched_dir / "auto.py").write_text(samples[pattern] + "\n")
        monkeypatch.setattr(module, "REPO_ROOT", fake_repo)
        monkeypatch.setattr(
            module,
            "WATCHED_FILES",
            ("src/ouroboros/cli/commands/auto.py",),
        )
        rc = module.main()
        assert rc == 1, f"pattern {pattern!r} not caught for sample"
