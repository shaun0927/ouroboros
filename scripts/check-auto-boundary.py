#!/usr/bin/env python3
"""Enforce the `ooo auto` product boundary at PR time.

Per Q00/ouroboros#725, `ooo auto` has a permanent product boundary:
`goal → interview → Seed → handoff`. Domain-specific operational
workflows (GitHub PR ops, Jira, Slack, …) belong in plugins, not in
core auto.

This script greps a watched set of `ooo auto` source files for
forbidden domain keywords and exits non-zero if any are found. It is
the mechanical enforcement layer paired with #734's documentary work.

Run locally:
    python3 scripts/check-auto-boundary.py

CI:
    .github/workflows/auto-boundary.yml runs this on every PR.

Allowlist:
    Lines that genuinely need a forbidden keyword (rare; usually a
    legacy import) can be marked with the trailing comment
    `# domain-keyword-allowed: <reason>` to bypass the check. Each
    allowlist usage requires reviewer sign-off.

To extend the watched set, add to `WATCHED_FILES` below; to extend
the keyword list, add to `FORBIDDEN_PATTERNS`.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


# Files that constitute the `ooo auto` core product boundary. New
# domain branches in any of these is the failure mode this script
# catches.
WATCHED_FILES: tuple[str, ...] = (
    "src/ouroboros/cli/commands/auto.py",
    "src/ouroboros/auto/pipeline.py",
    "src/ouroboros/auto/interview_driver.py",
    "src/ouroboros/auto/state.py",
    "src/ouroboros/auto/adapters.py",
    "src/ouroboros/auto/grading.py",
    "src/ouroboros/auto/seed_repairer.py",
    "src/ouroboros/auto/seed_reviewer.py",
    "src/ouroboros/auto/progress.py",
)

# Forbidden domain keywords. Each is a regex; matches are case-insensitive.
# Keywords are chosen for high precision (low false-positive rate) on the
# specific domains that drove #689 and the framing in #725.
FORBIDDEN_PATTERNS: tuple[str, ...] = (
    r"\bgithub\b",
    r"\bpull_request\b",
    r"/pulls?/",
    r"\bapi\.github\.com\b",
    r"\bjira\b",
    r"\bslack\b",
    r"\blinear\.app\b",
    # The exact PR-route keyword from #689; precise enough to catch even
    # if someone uses snake_case identifiers.
    r"\bgithub_pr\b",
)

# Marker comment that allowlists a single line.
ALLOWLIST_MARKER = "domain-keyword-allowed:"


def _scan_file(path: Path) -> list[tuple[int, str, str]]:
    """Return offending (line_no, line, matched_pattern) tuples for `path`.

    Lines carrying the allowlist marker are skipped. Lines inside string
    literals or comments are still checked — the docstring at the top of
    auto.py would catch a stray keyword, which is the desired behavior.
    """
    findings: list[tuple[int, str, str]] = []
    if not path.is_file():
        # Missing watched files are not an error in themselves; this script
        # is forward-looking.
        return findings
    text = path.read_text(encoding="utf-8")
    for lineno, line in enumerate(text.splitlines(), start=1):
        if ALLOWLIST_MARKER in line:
            continue
        for pattern in FORBIDDEN_PATTERNS:
            if re.search(pattern, line, flags=re.IGNORECASE):
                findings.append((lineno, line.rstrip(), pattern))
                break
    return findings


def main() -> int:
    all_findings: list[tuple[Path, int, str, str]] = []
    for rel in WATCHED_FILES:
        path = REPO_ROOT / rel
        for lineno, line, pattern in _scan_file(path):
            all_findings.append((path, lineno, line, pattern))

    if not all_findings:
        print(
            "ooo-auto-boundary: OK "
            f"({len(WATCHED_FILES)} files scanned, 0 findings)"
        )
        return 0

    sys.stderr.write(
        "ooo-auto-boundary: FAILED — domain keywords leaked into core auto.\n"
        "Per Q00/ouroboros#725, these belong in a UserLevel plugin, not in `ooo auto`.\n\n"
    )
    for path, lineno, line, pattern in all_findings:
        rel = path.relative_to(REPO_ROOT)
        sys.stderr.write(
            f"  {rel}:{lineno}: matched {pattern}\n    {line}\n"
        )
    sys.stderr.write(
        "\n"
        "If a forbidden keyword is genuinely necessary on a line (rare), append\n"
        f"  # {ALLOWLIST_MARKER} <reason>\n"
        "and add a brief PR-description rationale.\n"
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
