"""Pin the current ``ooo auto --runtime <backend>`` semantics.

Documented in ``docs/auto-runtime-semantics.md``: ``--runtime`` is the same
value for both authoring (in-process MCP handler) and run-handoff
(dispatcher), and plugin/subagent dispatch in the run handoff is gated on
opencode plugin mode. These tests are observation-grade — they document the
contract so any future change is a deliberate edit, not an accident.
"""

from __future__ import annotations

import pytest

from ouroboros.auto.state import AutoPipelineState, AutoStore
from ouroboros.mcp.tools.authoring_handlers import GenerateSeedHandler, InterviewHandler
from ouroboros.mcp.tools.execution_handlers import (
    ExecuteSeedHandler,
    StartExecuteSeedHandler,
)
from ouroboros.mcp.tools.subagent import should_dispatch_via_plugin


@pytest.mark.parametrize(
    "runtime",
    ["claude", "codex", "hermes", "gemini", "copilot", "kiro"],
)
def test_runtime_propagates_to_authoring_and_run_handoff(runtime: str) -> None:
    """The same ``--runtime`` value reaches authoring (interview / Seed
    generation) AND run-handoff (execute / start-execute) handlers. This
    locks the contract documented in #690 — a future change that fans the
    runtime out per phase will fail this test rather than silently drift.
    """
    interview = InterviewHandler(agent_runtime_backend=runtime)
    generate = GenerateSeedHandler(agent_runtime_backend=runtime)
    execute = ExecuteSeedHandler(agent_runtime_backend=runtime)
    start_execute = StartExecuteSeedHandler(execute_handler=execute, agent_runtime_backend=runtime)

    # Authoring side
    assert interview.agent_runtime_backend == runtime
    assert generate.agent_runtime_backend == runtime
    # Run-handoff side
    assert execute.agent_runtime_backend == runtime
    assert start_execute.agent_runtime_backend == runtime
    # Cross-phase invariant: every handler agrees on the runtime.
    assert (
        interview.agent_runtime_backend
        == generate.agent_runtime_backend
        == execute.agent_runtime_backend
        == start_execute.agent_runtime_backend
    )


@pytest.mark.parametrize(
    "runtime",
    ["claude", "codex", "hermes", "gemini", "copilot", "kiro"],
)
def test_runtime_persisted_on_state_round_trip(runtime: str, tmp_path) -> None:
    """``state.runtime_backend`` survives JSON round-trip — handlers that
    read this field on resume see the original value."""
    store = AutoStore(tmp_path)
    state = AutoPipelineState(goal="Build a CLI", cwd=str(tmp_path))
    state.runtime_backend = runtime
    store.save(state)

    loaded = store.load(state.auto_session_id)
    assert loaded.runtime_backend == runtime


@pytest.mark.parametrize(
    "runtime,opencode_mode,expected",
    [
        ("claude", None, False),
        ("codex", None, False),
        ("codex", "plugin", False),  # plugin mode irrelevant for non-opencode
        ("opencode", None, False),
        ("opencode", "subprocess", False),
        ("opencode", "plugin", True),
    ],
)
def test_should_dispatch_via_plugin_matrix(
    runtime: str, opencode_mode: str | None, expected: bool
) -> None:
    """Plugin/subagent dispatch is opt-in via opencode plugin mode only."""
    assert should_dispatch_via_plugin(runtime, opencode_mode) is expected


def test_codex_runtime_does_not_imply_plugin_dispatch() -> None:
    """Regression: ``--runtime codex`` MUST NOT trigger plugin dispatch.
    The first interview question is generated in-process by the authoring
    handler that talks to Codex; it does not become a Codex subagent task."""
    assert should_dispatch_via_plugin("codex", None) is False
    assert should_dispatch_via_plugin("codex", "plugin") is False
    assert should_dispatch_via_plugin("codex", "subprocess") is False
