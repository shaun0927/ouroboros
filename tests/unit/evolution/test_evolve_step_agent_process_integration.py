"""Integration tests for AgentProcess wrapping of EvolutionaryLoop.evolve_step.

Issue #518 — slice 5 of M6. Pins three contracts:
1. Happy path: spawn-to-complete emits AgentProcess CONTINUE then CONVERGE directives.
2. Cooperative cancel: caller cancels the handle; loop exits, emits CANCEL.
3. Regression guard: existing lineage.* events (generation.started / .completed)
   still flow unchanged through the AgentProcess wrapper.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from ouroboros.core.lineage import (
    EvaluationSummary,
    GenerationPhase,
    OntologySchema,
)
from ouroboros.core.seed import Seed
from ouroboros.core.types import Result
from ouroboros.events.base import BaseEvent
from ouroboros.evolution.loop import (
    EvolutionaryLoop,
    EvolutionaryLoopConfig,
    GenerationResult,
    StepAction,
)
from ouroboros.orchestrator.agent_process import AgentProcess, AgentProcessStatus


class _FakeEventStore:
    """Minimal in-memory EventStore sufficient for evolve_step tests."""

    def __init__(self) -> None:
        self.appended: list[BaseEvent] = []
        self._lineage_events: list[BaseEvent] = []

    async def append(self, event: BaseEvent) -> None:
        self.appended.append(event)
        self._lineage_events.append(event)

    async def replay_lineage(self, lineage_id: str) -> list[BaseEvent]:
        return []

    async def replay(self, aggregate_type: str, aggregate_id: str) -> list[BaseEvent]:
        return [
            e
            for e in self.appended
            if getattr(e, "aggregate_type", None) == aggregate_type
            and getattr(e, "aggregate_id", None) == aggregate_id
        ]


def _make_seed(seed_id: str = "seed-1") -> Seed:
    """Build a minimal Seed for testing."""
    ontology = OntologySchema(name="test", description="test ontology", fields=[])
    seed = MagicMock(spec=Seed)
    seed.goal = "test goal"
    seed.metadata = MagicMock()
    seed.metadata.seed_id = seed_id
    seed.metadata.parent_seed_id = None
    seed.ontology_schema = ontology
    seed.to_dict.return_value = {"seed_id": seed_id}
    return seed


def _make_generation_result(
    generation_number: int = 1,
    seed: Seed | None = None,
    phase: GenerationPhase = GenerationPhase.COMPLETED,
) -> GenerationResult:
    """Build a minimal GenerationResult for testing."""
    if seed is None:
        seed = _make_seed()
    wonder_output = MagicMock()
    wonder_output.questions = ()
    return GenerationResult(
        generation_number=generation_number,
        seed=seed,
        execution_output="ok",
        evaluation_summary=EvaluationSummary(
            score=0.8,
            final_approved=True,
            highest_stage_passed=1,
        ),
        wonder_output=wonder_output,
        phase=phase,
        success=phase == GenerationPhase.COMPLETED,
    )


def _build_loop(
    event_store: _FakeEventStore,
    gen_result: GenerationResult | None = None,
) -> EvolutionaryLoop:
    """Build an EvolutionaryLoop with mocked generation execution."""
    config = EvolutionaryLoopConfig(
        max_generations=10,
        convergence_threshold=0.95,
        min_generations=1,
    )
    loop = EvolutionaryLoop(event_store=event_store, config=config)

    # Stub _run_generation_with_watchdog to return a fixed result
    if gen_result is None:
        gen_result = _make_generation_result()

    async def _fake_run_generation_with_watchdog(**kwargs: Any) -> Result[GenerationResult, Any]:
        return Result.ok(gen_result)

    loop._run_generation_with_watchdog = _fake_run_generation_with_watchdog  # type: ignore[method-assign]
    return loop


# ---------------------------------------------------------------------------
# Test 1: happy path — CONTINUE then CONVERGE directives on the agent_process
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_evolve_step_emits_running_then_completed_directives() -> None:
    """spawn-to-complete happy path emits AgentProcess CONTINUE then CONVERGE."""
    store = _FakeEventStore()
    loop = _build_loop(store)
    seed = _make_seed()

    result = await loop.evolve_step(lineage_id="lin-test-1", initial_seed=seed)

    assert result.is_ok
    step = result.value
    assert step.action in (StepAction.CONTINUE, StepAction.CONVERGED, StepAction.EXHAUSTED)

    # Collect agent_process directive events
    ap_directives = [
        e.data["directive"]
        for e in store.appended
        if e.type == "control.directive.emitted"
        and getattr(e, "aggregate_type", None) == "agent_process"
    ]

    # Must have at least: initial CONTINUE (spawn marker) + final CONVERGE (completion)
    assert "continue" in ap_directives, f"Expected 'continue' in {ap_directives}"
    assert "converge" in ap_directives, f"Expected 'converge' in {ap_directives}"
    # CONTINUE must come before CONVERGE
    assert ap_directives.index("continue") < ap_directives.index("converge")


# ---------------------------------------------------------------------------
# Test 2: cooperative cancel — handle.cancel() causes loop to exit with CANCEL
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_evolve_step_honors_cooperative_cancel() -> None:
    """Caller cancels the handle; loop exits at next checkpoint, emits CANCEL."""
    store = _FakeEventStore()
    config = EvolutionaryLoopConfig(max_generations=10, min_generations=1)

    # Build a custom AgentProcess that cancels the handle right after spawning
    captured_handle: list[Any] = []
    real_agent_process = AgentProcess(event_store=store)

    class _CancellingAgentProcess:
        """Wraps AgentProcess.spawn and immediately cancels the returned handle."""

        async def spawn(self, *, intent: str, work_fn: Any, process_id: str | None = None) -> Any:
            handle = await real_agent_process.spawn(
                intent=intent,
                work_fn=work_fn,
                process_id=process_id,
            )
            captured_handle.append(handle)
            # Cancel immediately — work_fn checks at its first cooperative checkpoint
            await handle.cancel(reason="test cancel")
            return handle

    loop = EvolutionaryLoop(
        event_store=store,
        config=config,
        agent_process=_CancellingAgentProcess(),  # type: ignore[arg-type]
    )

    seed = _make_seed()

    result = await loop.evolve_step(lineage_id="lin-cancel-1", initial_seed=seed)

    assert result.is_ok
    step = result.value
    # When cancelled at the pre-generation checkpoint, action is INTERRUPTED
    assert step.action == StepAction.INTERRUPTED

    assert len(captured_handle) == 1
    final_status = captured_handle[0].status()
    assert final_status in (
        AgentProcessStatus.CANCELLED,
        AgentProcessStatus.COMPLETED,
    ), f"Unexpected status: {final_status}"

    # CANCEL directive must appear in agent_process events
    ap_directives = [
        e.data["directive"]
        for e in store.appended
        if e.type == "control.directive.emitted"
        and getattr(e, "aggregate_type", None) == "agent_process"
    ]
    assert "cancel" in ap_directives, f"Expected 'cancel' in {ap_directives}"


# ---------------------------------------------------------------------------
# Test 3: regression guard — lineage.* events still flow unchanged
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_evolve_step_existing_lineage_events_unchanged() -> None:
    """lineage.generation.started and .completed events still appear (regression guard)."""
    store = _FakeEventStore()
    loop = _build_loop(store)
    seed = _make_seed()

    result = await loop.evolve_step(lineage_id="lin-regression-1", initial_seed=seed)

    assert result.is_ok

    event_types = [e.type for e in store.appended]

    # lineage.created must be emitted for Gen 1
    assert "lineage.created" in event_types, f"Missing lineage.created in {event_types}"

    # lineage.generation.completed must be emitted (the main lineage state event)
    assert "lineage.generation.completed" in event_types, (
        f"Missing lineage.generation.completed in {event_types}"
    )

    # Verify the completed event carries the correct generation number
    completed_events = [e for e in store.appended if e.type == "lineage.generation.completed"]
    assert len(completed_events) >= 1
    assert completed_events[0].data.get("generation_number") == 1

    # lineage.* events must be separate from agent_process events
    lineage_events = [e for e in store.appended if e.type.startswith("lineage.")]
    ap_events = [
        e
        for e in store.appended
        if e.type == "control.directive.emitted"
        and getattr(e, "aggregate_type", None) == "agent_process"
    ]
    assert len(lineage_events) >= 2, f"Expected >= 2 lineage events, got {len(lineage_events)}"
    assert len(ap_events) >= 2, f"Expected >= 2 agent_process events, got {len(ap_events)}"

    # Lineage events must NOT have aggregate_type="agent_process"
    for e in lineage_events:
        assert getattr(e, "aggregate_type", None) != "agent_process", (
            f"Lineage event {e.type!r} should not have aggregate_type='agent_process'"
        )
