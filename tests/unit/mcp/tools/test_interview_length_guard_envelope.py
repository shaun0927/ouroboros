"""Regression test for Q00/ouroboros#831.

The interview length-guard branch (oversized ``initial_context``) returns a
fixed meta-directive in the question slot.  Without a structured envelope,
MCP clients (notably the Claude Code plugin) cannot distinguish it from a
normal interview question and mis-route it through AskUserQuestion, causing
multi-minute hangs.  After the fix the response must carry:

* ``is_error=True``
* ``meta.recoverable=True``
* ``meta.reason="initial_context_too_large"``
* ``meta.expected_action="resend_with_summary"``
* ``meta.max_chars=MAX_PROMPT_SAFE_INITIAL_CONTEXT_CHARS``

while preserving the human-readable text body verbatim so the CLI interview
UX is unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path

import pytest

from ouroboros.bigbang.interview import (
    INITIAL_CONTEXT_SUMMARY_QUESTION,
    MAX_PROMPT_SAFE_INITIAL_CONTEXT_CHARS,
    InterviewState,
    InterviewStatus,
)
from ouroboros.core.types import Result
from ouroboros.mcp.errors import MCPServerError
from ouroboros.mcp.tools.authoring_handlers import InterviewHandler


@dataclass(slots=True)
class _LengthGuardEngine:
    """Engine stub: returns the length-guard meta-directive as next question."""

    state_dir: Path
    saved_states: list[InterviewState] = field(default_factory=list)

    async def start_interview(
        self,
        initial_context: str,
        cwd: str | None = None,
        interview_id: str | None = None,
    ) -> Result[InterviewState, MCPServerError]:
        sid = interview_id or "interview_lengthguard0001"
        state = InterviewState(
            interview_id=sid,
            initial_context=initial_context,
            status=InterviewStatus.IN_PROGRESS,
        )
        await self.save_state(state)
        return Result.ok(state)

    async def ask_next_question(self, state: InterviewState) -> Result[str, MCPServerError]:
        # Mirror the in-process engine's length-guard branch.
        return Result.ok(INITIAL_CONTEXT_SUMMARY_QUESTION)

    async def save_state(self, state: InterviewState) -> Result[Path, MCPServerError]:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        path = self.state_dir / f"interview_{state.interview_id}.json"
        path.write_text(
            json.dumps({"interview_id": state.interview_id}),
            encoding="utf-8",
        )
        self.saved_states.append(state)
        return Result.ok(path)

    async def load_state(
        self, session_id: str
    ) -> Result[InterviewState, MCPServerError]:  # pragma: no cover - unused
        raise NotImplementedError


@pytest.mark.asyncio
async def test_start_with_oversized_context_returns_structured_length_guard_envelope(
    tmp_path: Path,
) -> None:
    """Start path: oversized initial_context must surface a structured signal."""
    engine = _LengthGuardEngine(state_dir=tmp_path)
    handler = InterviewHandler(
        interview_engine=engine,
        agent_runtime_backend=None,
        opencode_mode=None,
        data_dir=tmp_path,
    )

    # Padding makes the validator/length guard trip even though the engine
    # stub itself is the one returning the meta-directive; the body just has
    # to be plausibly long.
    oversized = "x" * (MAX_PROMPT_SAFE_INITIAL_CONTEXT_CHARS + 100)

    outcome = await handler.handle(
        {"initial_context": oversized, "cwd": str(tmp_path)},
    )

    assert outcome.is_ok, "handler must surface a recoverable result"
    mcp_result = outcome.value

    # Contract: structured envelope for the length-guard branch.
    assert mcp_result.is_error is True, "length-guard response must set is_error=True"
    meta = mcp_result.meta or {}
    assert meta.get("recoverable") is True
    assert meta.get("reason") == "initial_context_too_large"
    assert meta.get("expected_action") == "resend_with_summary"
    assert meta.get("max_chars") == MAX_PROMPT_SAFE_INITIAL_CONTEXT_CHARS
    assert isinstance(meta.get("session_id"), str) and meta["session_id"]

    # Text body MUST be preserved verbatim — CLI UX unchanged.
    assert mcp_result.content, "response must carry at least one content item"
    text_body = mcp_result.content[0].text
    assert INITIAL_CONTEXT_SUMMARY_QUESTION in text_body
    assert "Interview started" in text_body


@pytest.mark.asyncio
async def test_normal_question_does_not_set_length_guard_meta(tmp_path: Path) -> None:
    """Sanity guard: a normal first question keeps is_error=False and no length-guard meta."""

    @dataclass(slots=True)
    class _NormalEngine:
        state_dir: Path
        saved_states: list[InterviewState] = field(default_factory=list)

        async def start_interview(
            self,
            initial_context: str,
            cwd: str | None = None,
            interview_id: str | None = None,
        ) -> Result[InterviewState, MCPServerError]:
            sid = interview_id or "interview_normal000000001"
            state = InterviewState(
                interview_id=sid,
                initial_context=initial_context,
                status=InterviewStatus.IN_PROGRESS,
            )
            await self.save_state(state)
            return Result.ok(state)

        async def ask_next_question(self, state: InterviewState) -> Result[str, MCPServerError]:
            return Result.ok("What is the primary user persona?")

        async def save_state(self, state: InterviewState) -> Result[Path, MCPServerError]:
            self.state_dir.mkdir(parents=True, exist_ok=True)
            path = self.state_dir / f"interview_{state.interview_id}.json"
            path.write_text(
                json.dumps({"interview_id": state.interview_id}),
                encoding="utf-8",
            )
            self.saved_states.append(state)
            return Result.ok(path)

        async def load_state(
            self, session_id: str
        ) -> Result[InterviewState, MCPServerError]:  # pragma: no cover - unused
            raise NotImplementedError

    engine = _NormalEngine(state_dir=tmp_path)
    handler = InterviewHandler(
        interview_engine=engine,
        agent_runtime_backend=None,
        opencode_mode=None,
        data_dir=tmp_path,
    )

    outcome = await handler.handle(
        {"initial_context": "Build a CLI", "cwd": str(tmp_path)},
    )

    assert outcome.is_ok
    mcp_result = outcome.value
    assert mcp_result.is_error is False
    meta = mcp_result.meta or {}
    # None of the length-guard keys may appear on a normal response.
    assert "reason" not in meta
    assert "expected_action" not in meta
    assert "max_chars" not in meta
