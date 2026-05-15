"""Tests for Claude plugin skill execution guide artifact."""

from pathlib import Path

from ouroboros.backends.capabilities import render_backend_skill_capability_guide


def test_claude_plugin_ships_rendered_skill_capability_guide() -> None:
    guide_path = Path(".claude-plugin") / "SKILL_CAPABILITY_GUIDE.md"

    # The Claude plugin artifact is generated from the backend capability registry;
    # update it by rendering this helper rather than hand-editing the snapshot.
    assert guide_path.read_text(encoding="utf-8") == render_backend_skill_capability_guide("claude")
