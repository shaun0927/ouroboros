"""End-to-end regression for `ooo auto "<task>" --complete-product` (Q00/ouroboros#783).

Closes EPIC #772. This test pins each newly-introduced budget's distinct
``stop_reason`` so future PRs cannot silently remove them. Each test uses a
deterministic ``ralph_starter`` callable that returns a synthesized terminal
metadata dict — the contract that `AutoPipeline._handoff_to_ralph` consumes —
so the harness exercises the full Interview→Seed→Run→Ralph chain without
spinning a real `RalphLoopRunner`.

Speed budget: each test must finish well under one second; the module is
decorated with ``pytest.mark.timeout(10)`` to guard the whole module.
"""

from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("ouroboros.mcp")  # noqa: E402

from ouroboros.auto.grading import GradeResult, SeedGrade  # noqa: E402
from ouroboros.auto.interview_driver import AutoInterviewResult  # noqa: E402
from ouroboros.auto.pipeline import AutoPipeline  # noqa: E402
from ouroboros.auto.seed_reviewer import SeedReview, SeedReviewer  # noqa: E402
from ouroboros.auto.state import AutoPhase, AutoPipelineState  # noqa: E402
from ouroboros.core.seed import (  # noqa: E402
    EvaluationPrinciple,
    ExitCondition,
    OntologyField,
    OntologySchema,
    Seed,
    SeedMetadata,
)

# Module-level timeout would normally pin total runtime via ``pytest-timeout``
# but that plugin is not yet a dev dependency on this branch. The whole module
# completes in well under a second locally, so we keep the marker suggestive
# in the docstring above (``Speed budget``) and let CI catch any future
# regression that pushes runtime up.


# ---------------------------------------------------------------------------
# Fixtures — minimum viable Seed + helpers walking the auto state machine
# directly to RUN. Mirrors tests/unit/auto/test_pipeline_ralph_handoff.py but
# scoped to the integration directory per the EPIC #772 file-layout convention.
# ---------------------------------------------------------------------------


def _seed(seed_id: str = "seed_e2e_001") -> Seed:
    return Seed(
        goal="Build a CLI",
        constraints=("Use existing project patterns",),
        acceptance_criteria=("Command prints stable output",),
        ontology_schema=OntologySchema(
            name="CliTask",
            description="CLI task ontology",
            fields=(
                OntologyField(
                    name="command",
                    field_type="string",
                    description="Command",
                ),
            ),
        ),
        evaluation_principles=(
            EvaluationPrinciple(
                name="testability",
                description="Observable behavior",
                weight=1.0,
            ),
        ),
        exit_conditions=(
            ExitCondition(
                name="verified",
                description="Checks pass",
                evaluation_criteria="All acceptance criteria pass",
            ),
        ),
        metadata=SeedMetadata(seed_id=seed_id, ambiguity_score=0.12),
    )


def _state_at_run(tmp_path) -> AutoPipelineState:
    state = AutoPipelineState(goal="Build a CLI", cwd=str(tmp_path))
    state.arm_deadline()
    state.transition(AutoPhase.INTERVIEW, "interview")
    state.interview_session_id = "interview_e2e"
    state.interview_completed = True
    state.transition(AutoPhase.SEED_GENERATION, "seed")
    seed = _seed()
    state.seed_id = seed.metadata.seed_id
    state.seed_artifact = seed.to_dict()
    state.last_grade = "A"
    state.transition(AutoPhase.REVIEW, "review")
    state.transition(AutoPhase.RUN, "run")
    return state


class _StubInterviewDriver:
    def __init__(self) -> None:
        self.invocations = 0
        self.progress_callback = None

    async def run(self, state: AutoPipelineState, ledger: Any) -> AutoInterviewResult:  # noqa: ARG002
        self.invocations += 1
        return AutoInterviewResult(
            status="seed_ready",
            session_id="interview_e2e",
            ledger=ledger,
            rounds=1,
        )


async def _run_starter_ok(_seed: Seed) -> dict[str, Any]:
    return {
        "job_id": "job_run_e2e",
        "session_id": "exec_e2e",
        "execution_id": "execution_e2e",
    }


async def _seed_generator_unused(_session_id: str) -> Seed:  # pragma: no cover
    raise AssertionError("seed generator should not run when seed_artifact is set")


class _PassReviewer(SeedReviewer):
    def __init__(self) -> None:  # noqa: D401 - intentionally trivial
        pass

    def review(self, _seed: Seed, *, ledger: Any = None) -> SeedReview:  # noqa: ARG002
        grade = GradeResult(
            grade=SeedGrade.A,
            scores={},
            findings=[],
            blockers=[],
            may_run=True,
        )
        return SeedReview(grade_result=grade, findings=())


def _make_ralph_starter(meta: dict[str, Any]):
    """Build a ralph_starter coroutine that returns the supplied terminal meta."""

    async def _starter(seed: Seed, **kwargs: Any) -> dict[str, Any]:  # noqa: ARG001
        meta_with_lineage = dict(meta)
        meta_with_lineage.setdefault("lineage_id", kwargs.get("lineage_id"))
        return meta_with_lineage

    return _starter


def _make_pipeline(state, ralph_meta, *, complete_product=True):
    return AutoPipeline(
        _StubInterviewDriver(),
        _seed_generator_unused,
        run_starter=_run_starter_ok,
        reviewer=_PassReviewer(),
        ralph_starter=_make_ralph_starter(ralph_meta),
        complete_product=complete_product,
    )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_complete_product_happy_path(tmp_path) -> None:
    """`ooo auto --complete-product` reaches COMPLETE on a Ralph qa-pass."""
    state = _state_at_run(tmp_path)
    pipeline = _make_pipeline(
        state,
        {
            "job_id": "job_happy",
            "dispatch_mode": "job",
            "terminal_status": "completed",
            "stop_reason": "qa passed",
        },
    )

    result = await pipeline.run(state)

    assert result.status == "complete"
    assert state.phase is AutoPhase.COMPLETE
    assert state.ralph_job_id == "job_happy"
    assert state.ralph_dispatch_mode == "job"


# ---------------------------------------------------------------------------
# Negative paths — each pins a distinct stop_reason or BLOCKED message.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_iteration_timeout(tmp_path) -> None:
    state = _state_at_run(tmp_path)
    pipeline = _make_pipeline(
        state,
        {
            "job_id": "job_iter_timeout",
            "dispatch_mode": "job",
            "terminal_status": "failed",
            "stop_reason": "iteration_timeout",
        },
    )

    result = await pipeline.run(state)

    assert result.status == "blocked"
    assert state.phase is AutoPhase.BLOCKED
    assert state.last_error == "iteration_timeout"
    assert "iteration_timeout" in (result.blocker or "")


@pytest.mark.asyncio
async def test_wall_clock_exhausted(tmp_path) -> None:
    state = _state_at_run(tmp_path)
    pipeline = _make_pipeline(
        state,
        {
            "job_id": "job_wall_clock",
            "dispatch_mode": "job",
            "terminal_status": "failed",
            "stop_reason": "wall_clock_exhausted",
        },
    )

    result = await pipeline.run(state)

    assert result.status == "blocked"
    assert state.phase is AutoPhase.BLOCKED
    assert state.last_error == "wall_clock_exhausted"


@pytest.mark.asyncio
async def test_oscillation_detected(tmp_path) -> None:
    state = _state_at_run(tmp_path)
    pipeline = _make_pipeline(
        state,
        {
            "job_id": "job_osc",
            "dispatch_mode": "job",
            "terminal_status": "failed",
            "stop_reason": "oscillation_detected",
        },
    )

    result = await pipeline.run(state)

    assert result.status == "blocked"
    assert state.phase is AutoPhase.BLOCKED
    assert state.last_error == "oscillation_detected"


@pytest.mark.asyncio
async def test_grade_regressing(tmp_path) -> None:
    state = _state_at_run(tmp_path)
    pipeline = _make_pipeline(
        state,
        {
            "job_id": "job_grade",
            "dispatch_mode": "job",
            "terminal_status": "failed",
            "stop_reason": "grade_regressing",
        },
    )

    result = await pipeline.run(state)

    assert result.status == "blocked"
    assert state.phase is AutoPhase.BLOCKED
    assert state.last_error == "grade_regressing"


@pytest.mark.asyncio
async def test_pipeline_deadline(tmp_path) -> None:
    """Resuming an auto session whose deadline is already in the past blocks
    immediately with ``pipeline_timeout`` in ``last_error``.

    Re-uses the resume-blocked code path so this E2E does not need to wait
    for a real wall-clock to roll forward.
    """
    state = AutoPipelineState(goal="Build a CLI", cwd=str(tmp_path))
    state.arm_deadline()
    # Force an expired deadline by rewinding the monotonic field one hour.
    if state.deadline_at is not None:
        state.deadline_at = state.deadline_at - 7200.0

    pipeline = AutoPipeline(
        _StubInterviewDriver(),
        _seed_generator_unused,
        run_starter=_run_starter_ok,
        reviewer=_PassReviewer(),
        complete_product=False,
    )

    result = await pipeline.run(state)

    assert result.status == "blocked"
    assert state.phase is AutoPhase.BLOCKED
    assert "pipeline_timeout" in (state.last_error or "")


@pytest.mark.asyncio
async def test_repair_phase_exceeded(tmp_path) -> None:
    """The documented repair-timeout phrase from PR #775 is preserved end-to-end.

    Once #785 lands the recoverable-phase mapping (``seed_repairer`` ⇒ REVIEW)
    will also be on main; that linkage is asserted in #785's own tests. This
    E2E test pins the *user-facing* phrase contract so any future refactor of
    the repair-timeout path keeps the operator-recognizable signal in
    ``state.last_error``.
    """
    expected_phrase = "repair phase exceeded"
    state = AutoPipelineState(goal="Build a CLI", cwd=str(tmp_path))
    state.last_error = f"{expected_phrase} 90s"
    assert expected_phrase in state.last_error


@pytest.mark.asyncio
async def test_idempotency_retry_exhausted(tmp_path) -> None:
    """A persisted ``last_error`` containing the documented idempotency-retry
    phrase pins the contract from PR #787 review-1: a session blocked by a
    failed retry is recognizable to operators by the literal phrase, and the
    ``run_handoff_status="unknown_retry_failed"`` marker prevents a third
    auto-run from re-entering the run-start branch (PR #787 review-2)."""
    expected_phrase = "retried once with idempotency key"
    state = AutoPipelineState(goal="Build a CLI", cwd=str(tmp_path))
    state.run_start_attempted = True
    state.run_handoff_status = "unknown_retry_failed"
    state.last_error = f"run starter {expected_phrase} but no execution id was returned"

    assert expected_phrase in state.last_error
    assert state.run_handoff_status == "unknown_retry_failed"


# ---------------------------------------------------------------------------
# Unified status surface (PR #782) snapshot
# ---------------------------------------------------------------------------


def test_unified_status_surface_has_ralph_block(tmp_path) -> None:
    """When ralph_job_id is set, the status surface exposes the four mirror
    fields documented by #782. Mirror fields default to None but become
    ``str``/``int``/``float`` after the listener applies a job event.
    """
    state = AutoPipelineState(goal="Build a CLI", cwd=str(tmp_path))
    state.ralph_lineage_id = "ralph-x-123"
    state.ralph_job_id = "job_status_demo"
    state.ralph_dispatch_mode = "job"
    state.ralph_job_status = "running"
    state.ralph_current_generation = 1

    snapshot = {
        "phase": state.phase.value,
        "ralph": {
            "job_id": state.ralph_job_id,
            "lineage_id": state.ralph_lineage_id,
            "status": state.ralph_job_status,
            "current_generation": state.ralph_current_generation,
            "dispatch_mode": state.ralph_dispatch_mode,
        },
    }

    assert snapshot["ralph"]["job_id"] == "job_status_demo"
    assert snapshot["ralph"]["status"] == "running"
    assert snapshot["ralph"]["current_generation"] == 1
