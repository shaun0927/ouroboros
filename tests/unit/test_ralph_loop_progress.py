"""Regression tests for the no-progress / oscillation guards in Ralph loop.

Covers issue #778: stop early when ``evolve_step`` produces the same finding
set across iterations or when the QA grade strictly regresses, instead of
burning the entire ``max_generations`` wall-clock budget.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from ouroboros.core.types import Result
from ouroboros.mcp.tools.ralph_handlers import RalphHandler
from ouroboros.mcp.types import ContentType, MCPContentItem, MCPToolResult
from ouroboros.ralph_loop import RalphLoopConfig, RalphLoopRunner


@dataclass
class _ScriptedEvolveHandler:
    """Evolve handler that returns a fixed sequence of meta payloads."""

    metas: list[dict[str, Any]]
    calls: int = 0
    seen_arguments: list[dict[str, Any]] = field(default_factory=list)

    async def handle(self, arguments: dict[str, Any]):
        self.seen_arguments.append(arguments)
        index = min(self.calls, len(self.metas) - 1)
        meta = dict(self.metas[index])
        meta.setdefault("lineage_id", arguments["lineage_id"])
        meta.setdefault("generation", self.calls + 1)
        meta.setdefault("action", "continue")
        self.calls += 1
        return Result.ok(
            MCPToolResult(
                content=(MCPContentItem(type=ContentType.TEXT, text="step"),),
                is_error=False,
                meta=meta,
            )
        )


@dataclass
class _ImmediateEvolveHandler:
    """Trivial evolve handler used only by RalphHandler validation tests."""

    async def handle(self, arguments: dict[str, Any]):  # pragma: no cover - unused path
        return Result.ok(
            MCPToolResult(
                content=(MCPContentItem(type=ContentType.TEXT, text="ok"),),
                is_error=False,
                meta={
                    "lineage_id": arguments["lineage_id"],
                    "generation": 1,
                    "action": "converged",
                },
            )
        )


def _findings(*labels: str) -> list[dict[str, Any]]:
    return [{"id": label, "msg": f"finding-{label}"} for label in labels]


@pytest.mark.asyncio
async def test_oscillation_detected_after_window_of_identical_findings_hashes() -> None:
    """3 iterations sharing one findings_hash with no QA pass must stop early."""
    repeated = _findings("a", "b")
    evolve = _ScriptedEvolveHandler(
        metas=[
            {"action": "continue", "findings": repeated},
            {"action": "continue", "findings": repeated},
            {"action": "continue", "findings": repeated},
            {"action": "continue", "findings": repeated},
        ]
    )
    runner = RalphLoopRunner(evolve)

    result = await runner.run(
        RalphLoopConfig(
            lineage_id="lin_osc",
            max_generations=5,
            oscillation_window=3,
        )
    )

    assert result.status == "failed"
    assert result.stop_reason == "oscillation_detected"
    assert result.iteration_count == 3
    # All three iterations carry the same hash.
    hashes = {item.findings_hash for item in result.iterations}
    assert len(hashes) == 1
    assert next(iter(hashes)) is not None
    # The handler must not have been invoked a fourth time.
    assert evolve.calls == 3


@pytest.mark.asyncio
async def test_grade_regressing_two_iterations_strictly_decreasing() -> None:
    """[0.8, 0.5] over the default window of 2 must stop with grade_regressing."""
    evolve = _ScriptedEvolveHandler(
        metas=[
            {"action": "continue", "qa": {"score": 0.8, "verdict": "fail"}},
            {"action": "continue", "qa": {"score": 0.5, "verdict": "fail"}},
            {"action": "continue", "qa": {"score": 0.4, "verdict": "fail"}},
        ]
    )
    runner = RalphLoopRunner(evolve)

    result = await runner.run(
        RalphLoopConfig(
            lineage_id="lin_regress",
            max_generations=5,
            grade_regression_window=2,
        )
    )

    assert result.status == "failed"
    assert result.stop_reason == "grade_regressing"
    assert result.iteration_count == 2
    assert [item.grade for item in result.iterations] == [0.8, 0.5]
    assert evolve.calls == 2


@pytest.mark.asyncio
async def test_grade_with_none_resets_regression_streak() -> None:
    """[0.8, None] must NOT trigger grade_regressing; None is a neutral observation."""
    evolve = _ScriptedEvolveHandler(
        metas=[
            {"action": "continue", "qa": {"score": 0.8, "verdict": "fail"}},
            # No qa block at all → grade is None.
            {"action": "continue"},
        ]
    )
    runner = RalphLoopRunner(evolve)

    result = await runner.run(
        RalphLoopConfig(
            lineage_id="lin_none_resets",
            max_generations=2,
            grade_regression_window=2,
        )
    )

    # Loop ran to max_generations rather than tripping a no-progress guard.
    assert result.status == "completed"
    assert result.stop_reason == "max_generations reached"
    assert result.iteration_count == 2
    assert [item.grade for item in result.iterations] == [0.8, None]


@pytest.mark.asyncio
async def test_equal_grades_do_not_trigger_grade_regressing() -> None:
    """[0.8, 0.8] is flat, not strictly decreasing — must not stop early."""
    evolve = _ScriptedEvolveHandler(
        metas=[
            {"action": "continue", "qa": {"score": 0.8, "verdict": "fail"}},
            {"action": "continue", "qa": {"score": 0.8, "verdict": "fail"}},
        ]
    )
    runner = RalphLoopRunner(evolve)

    result = await runner.run(
        RalphLoopConfig(
            lineage_id="lin_flat",
            max_generations=2,
            grade_regression_window=2,
        )
    )

    assert result.status == "completed"
    assert result.stop_reason == "max_generations reached"
    assert result.iteration_count == 2
    assert [item.grade for item in result.iterations] == [0.8, 0.8]


@pytest.mark.asyncio
async def test_mixed_hashes_do_not_trigger_oscillation_stop() -> None:
    """Same hash 2× then a new hash on iteration 3 must not stop with oscillation."""
    repeated = _findings("a")
    different = _findings("b")
    evolve = _ScriptedEvolveHandler(
        metas=[
            {"action": "continue", "findings": repeated},
            {"action": "continue", "findings": repeated},
            {"action": "continue", "findings": different},
        ]
    )
    runner = RalphLoopRunner(evolve)

    result = await runner.run(
        RalphLoopConfig(
            lineage_id="lin_mixed",
            max_generations=3,
            oscillation_window=3,
        )
    )

    assert result.status == "completed"
    assert result.stop_reason == "max_generations reached"
    assert result.iteration_count == 3
    hashes = [item.findings_hash for item in result.iterations]
    assert hashes[0] == hashes[1]
    assert hashes[2] != hashes[0]


@pytest.mark.asyncio
async def test_ralph_handler_rejects_oscillation_window_zero() -> None:
    handler = RalphHandler(evolve_handler=_ImmediateEvolveHandler())  # type: ignore[arg-type]

    result = await handler.handle(
        {
            "lineage_id": "lin_osc_zero",
            "oscillation_window": 0,
        }
    )

    assert result.is_err
    assert "oscillation_window" in str(result.error)
    assert "between 1 and 10" in str(result.error)


@pytest.mark.asyncio
async def test_ralph_handler_rejects_oscillation_window_above_ceiling() -> None:
    handler = RalphHandler(evolve_handler=_ImmediateEvolveHandler())  # type: ignore[arg-type]

    result = await handler.handle(
        {
            "lineage_id": "lin_osc_high",
            "oscillation_window": 11,
        }
    )

    assert result.is_err
    assert "oscillation_window" in str(result.error)
    assert "between 1 and 10" in str(result.error)


@pytest.mark.asyncio
async def test_ralph_handler_rejects_grade_regression_window_zero() -> None:
    handler = RalphHandler(evolve_handler=_ImmediateEvolveHandler())  # type: ignore[arg-type]

    result = await handler.handle(
        {
            "lineage_id": "lin_grade_zero",
            "grade_regression_window": 0,
        }
    )

    assert result.is_err
    assert "grade_regression_window" in str(result.error)
    assert "between 1 and 10" in str(result.error)


@pytest.mark.asyncio
async def test_precomputed_findings_hash_is_used_verbatim() -> None:
    """If meta provides a findings_hash string, it must pass through unchanged."""
    evolve = _ScriptedEvolveHandler(
        metas=[
            {"action": "continue", "findings_hash": "deadbeef"},
            {"action": "continue", "findings_hash": "deadbeef"},
            {"action": "continue", "findings_hash": "deadbeef"},
        ]
    )
    runner = RalphLoopRunner(evolve)

    result = await runner.run(
        RalphLoopConfig(
            lineage_id="lin_precomputed",
            max_generations=5,
            oscillation_window=3,
        )
    )

    assert result.stop_reason == "oscillation_detected"
    assert all(item.findings_hash == "deadbeef" for item in result.iterations)


@pytest.mark.asyncio
async def test_letter_grade_b_maps_to_three_quarters() -> None:
    """Grade letter ``B`` must yield 0.75 and feed regression detection."""
    evolve = _ScriptedEvolveHandler(
        metas=[
            {"action": "continue", "qa": {"grade": "A", "verdict": "fail"}},
            {"action": "continue", "qa": {"grade": "B", "verdict": "fail"}},
        ]
    )
    runner = RalphLoopRunner(evolve)

    result = await runner.run(
        RalphLoopConfig(
            lineage_id="lin_letters",
            max_generations=5,
            grade_regression_window=2,
        )
    )

    assert result.stop_reason == "grade_regressing"
    assert [item.grade for item in result.iterations] == [1.0, 0.75]
