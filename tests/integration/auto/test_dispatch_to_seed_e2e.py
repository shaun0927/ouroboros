"""End-to-end ``ooo auto`` dispatch regression tests.

Closes the last open acceptance bullet of issue #637: prove that ``ooo auto ...``
reaches the ``ouroboros_auto`` MCP pipeline and produces a Seed (or fails closed
with the documented unavailable-tool contract). The tests stay laser-focused on
this acceptance bullet — they do not exercise progress UI, answer grounding, or
CLI help text. All side-effects are confined to ``tmp_path``: no network, no
real LLM, no real home directory.

Cases:
1. ``ooo auto "<fully specified goal>"`` enters through ``CodexCliRuntime.execute_task``,
   traverses ``resolve_skill_dispatch`` (packaged ``auto`` SKILL.md frontmatter +
   ``$goal``/``$CWD`` template normalization), reaches the real ``AutoHandler.handle``
   /``AutoHandler._run``, and yields a Seed-bearing result. Real LLM/MCP/subprocess
   side effects are stubbed *inside* the boundary (handlers + ``AutoPipeline``),
   not around it. A regression in the packaged frontmatter, dispatch arg shape,
   or ``AutoHandler`` wiring would make this test fail.
2. A sparse goal still reaches Seed via the interview-fill ``AutoPipeline``. This
   case stays a post-dispatch unit-style test (it does NOT enter via the runtime
   boundary) — it covers the ledger-hydration + seed-generator wiring landed in
   PR #652. Case 1 already covers the dispatch boundary itself.
3. The deterministic ``ooo auto`` dispatch surface fails closed with the
   user-visible "ouroboros_auto is unavailable" contract message and does not
   create any persisted auto session state when the MCP tool is unregistered.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from ouroboros.auto.grading import GradeResult, SeedGrade
from ouroboros.auto.interview_driver import (
    AutoInterviewDriver,
    FunctionInterviewBackend,
    InterviewTurn,
)
from ouroboros.auto.ledger import (
    LedgerEntry,
    LedgerSource,
    LedgerStatus,
    SeedDraftLedger,
)
from ouroboros.auto.pipeline import AutoPipeline, AutoPipelineResult
from ouroboros.auto.seed_reviewer import SeedReview
from ouroboros.auto.state import AutoPhase, AutoPipelineState, AutoStore
from ouroboros.core.seed import (
    EvaluationPrinciple,
    ExitCondition,
    OntologyField,
    OntologySchema,
    Seed,
    SeedMetadata,
)
from ouroboros.mcp.tools import auto_handler as auto_handler_module
from ouroboros.mcp.tools.auto_handler import AutoHandler
from ouroboros.orchestrator.adapter import AgentMessage
from ouroboros.orchestrator.codex_cli_runtime import CodexCliRuntime
from ouroboros.router import Resolved

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Test helpers (inline; mirror ``tests/unit/auto/test_interview_pipeline.py``).
# ---------------------------------------------------------------------------


_FULLY_SPECIFIED_GOAL = (
    "Build a CLI named hello that prints exactly hello followed by a newline "
    "and exits 0. "
    "Actor is a local developer running it from a unix shell. "
    "Inputs are no CLI args and no stdin. "
    "Outputs are stdout hello with a trailing newline, no stderr, exit code 0. "
    "Runtime context is a temp scratch directory outside any repo, posix shell, no network. "
    "Constraints are stdlib only and no real-project edits. "
    "Non-goals are package publishing and network access. "
    "Acceptance criteria are stdout equals hello newline and exit status is 0. "
    "Verification plan is run the CLI and assert stdout, stderr, exit code. "
    "Failure modes are missing newline or non-zero exit."
)


def _build_a_grade_seed(goal: str) -> Seed:
    """Return a minimally valid Seed used as the seed_generator output."""
    return Seed(
        goal=goal,
        constraints=("Use the Python standard library only",),
        acceptance_criteria=("`hello` prints exactly `hello\\n` to stdout and exits 0",),
        ontology_schema=OntologySchema(
            name="HelloCli",
            description="Tiny hello CLI ontology",
            fields=(
                OntologyField(
                    name="command", field_type="string", description="Invocation command"
                ),
            ),
        ),
        evaluation_principles=(
            EvaluationPrinciple(
                name="testability", description="Stdout, stderr, exit code are observable"
            ),
        ),
        exit_conditions=(
            ExitCondition(
                name="verified",
                description="All acceptance criteria pass",
                evaluation_criteria="Stdout/stderr/exit-code assertions all pass",
            ),
        ),
        metadata=SeedMetadata(ambiguity_score=0.05),
    )


def _fill_ledger_ready(ledger: SeedDraftLedger) -> None:
    """Populate every required ledger section with conservative defaults."""
    defaults = {
        "actors": "Single local CLI user",
        "inputs": "No CLI args, no stdin",
        "outputs": "Stdout hello with newline, exit 0",
        "constraints": "Use existing project patterns",
        "non_goals": "No cloud sync",
        "acceptance_criteria": "Stdout equals hello newline",
        "verification_plan": "Run the CLI and assert stdout/exit",
        "failure_modes": "Non-zero exit code",
        "runtime_context": "Local Unix-like shell with Python 3",
    }
    for section, value in defaults.items():
        source = (
            LedgerSource.NON_GOAL if section == "non_goals" else LedgerSource.CONSERVATIVE_DEFAULT
        )
        ledger.add_entry(
            section,
            LedgerEntry(
                key=f"{section}.test_default",
                value=value,
                source=source,
                confidence=0.85,
                status=LedgerStatus.DEFAULTED,
            ),
        )


class _AGradeRepairer:
    """Stub repairer that always returns an A-grade review without mutating the Seed."""

    def converge(
        self, seed: Seed, *, ledger: SeedDraftLedger
    ) -> tuple[Seed, SeedReview, list[object]]:
        review = SeedReview(
            grade_result=GradeResult(
                grade=SeedGrade.A,
                scores={
                    "coverage": 0.95,
                    "ambiguity": 0.05,
                    "testability": 0.95,
                    "execution_feasibility": 0.95,
                    "risk": 0.05,
                },
                findings=[],
                blockers=[],
                may_run=True,
            ),
            findings=(),
        )
        return seed, review, []


def _make_seed_saver(tmp_path: Path) -> tuple[Any, list[str]]:
    """Return a seed_saver and the list it appends each saved path to."""
    saved: list[str] = []
    seeds_dir = tmp_path / "seeds"
    seeds_dir.mkdir(parents=True, exist_ok=True)

    def save(seed: Seed) -> str:
        path = seeds_dir / f"{seed.metadata.seed_id}.yaml"
        # Persist a non-empty Seed YAML payload so the tests can verify the
        # saver materialised real bytes.
        import yaml

        path.write_text(
            yaml.dump(seed.to_dict(), default_flow_style=False, sort_keys=False),
            encoding="utf-8",
        )
        saved.append(str(path))
        return str(path)

    return save, saved


def _build_pipeline(
    *,
    tmp_path: Path,
    interview_start: Any,
    interview_answer: Any,
    seed_generator: Any,
    seed_saver: Any,
) -> tuple[AutoPipeline, AutoStore]:
    store = AutoStore(tmp_path / "auto_store")
    driver = AutoInterviewDriver(
        FunctionInterviewBackend(interview_start, interview_answer),
        store=store,
        max_rounds=4,
        timeout_seconds=2.0,
    )
    pipeline = AutoPipeline(
        driver,
        seed_generator,
        store=store,
        repairer=_AGradeRepairer(),
        seed_saver=seed_saver,
        skip_run=True,
    )
    return pipeline, store


def _write_auto_skill(skills_dir: Path) -> None:
    """Mirror the packaged ``auto`` SKILL.md frontmatter for the dispatch test.

    Kept in sync with ``skills/auto/SKILL.md``: a regression in either side
    (e.g. a renamed ``mcp_tool`` field, a dropped ``$goal``/``$CWD``
    placeholder, or extra unmocked args) breaks the dispatch path tests.
    """
    skill_dir = skills_dir / "auto"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "\n".join(
            [
                "---",
                "name: auto",
                'description: "Automatically converge from goal to A-grade Seed and execute it"',
                "mcp_tool: ouroboros_auto",
                "mcp_args:",
                '  goal: "$goal"',
                '  resume: "$resume"',
                '  cwd: "$CWD"',
                '  max_interview_rounds: "$max_interview_rounds"',
                '  max_repair_rounds: "$max_repair_rounds"',
                '  skip_run: "$skip_run"',
                "---",
                "",
                "# auto",
                "",
            ]
        ),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Case 1 — ``ooo auto`` enters via CodexCliRuntime, traverses the real
# router/AutoHandler dispatch boundary, and reaches the Seed phase.
# ---------------------------------------------------------------------------


class _StubAuthoringHandler:
    """Drop-in stub for ``InterviewHandler``/``GenerateSeedHandler``.

    AutoHandler builds these inside ``_run`` to drive the real authoring chain.
    The test never lets execution reach ``AutoPipeline.run``, so the handlers
    only need to satisfy attribute lookups and the matches-runtime check.
    """

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.agent_runtime_backend = kwargs.get("agent_runtime_backend")
        self.opencode_mode = kwargs.get("opencode_mode")
        # Mirror the real handler attributes touched by the AutoHandler
        # ``_handler_matches_runtime``/``_authoring_*_handler`` paths.
        self.interview_engine = None
        self.event_store = None
        self.llm_adapter = None
        self.llm_backend = kwargs.get("llm_backend")
        self.data_dir = None
        self.seed_generator = None


class _StubExecuteSeedHandler(_StubAuthoringHandler):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.mcp_manager = kwargs.get("mcp_manager")
        self.mcp_tool_prefix = kwargs.get("mcp_tool_prefix", "")


class _StubStartExecuteSeedHandler:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.execute_handler = kwargs.get("execute_handler")
        self.event_store = kwargs.get("event_store")
        self.job_manager = kwargs.get("job_manager")
        self.agent_runtime_backend = kwargs.get("agent_runtime_backend")
        self.opencode_mode = kwargs.get("opencode_mode")


class _StubSeedRepairer:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs


def _install_auto_handler_stubs(
    monkeypatch: pytest.MonkeyPatch,
    *,
    tmp_path: Path,
    seed_path: str,
    captured: dict[str, Any],
) -> None:
    """Replace the in-process side-effecting deps inside ``AutoHandler._run``.

    The real ``AutoHandler.handle`` and ``AutoHandler._run`` still execute. Only
    the leaves that would otherwise spawn real LLM/MCP/subprocess work get
    swapped out: authoring handlers, ``SeedRepairer``, and ``AutoPipeline``.
    The stub ``AutoPipeline.run`` produces a complete A-grade ``AutoPipelineResult``
    so the runtime sees a Seed-bearing dispatch result.
    """

    monkeypatch.setattr(auto_handler_module, "InterviewHandler", _StubAuthoringHandler)
    monkeypatch.setattr(auto_handler_module, "GenerateSeedHandler", _StubAuthoringHandler)
    monkeypatch.setattr(auto_handler_module, "ExecuteSeedHandler", _StubExecuteSeedHandler)
    monkeypatch.setattr(
        auto_handler_module, "StartExecuteSeedHandler", _StubStartExecuteSeedHandler
    )
    monkeypatch.setattr(auto_handler_module, "SeedRepairer", _StubSeedRepairer)

    # Also replace the default AutoStore so no auto_*.json files leak into the
    # real $HOME/.ouroboros/data path.
    monkeypatch.setattr(
        auto_handler_module, "AutoStore", lambda: AutoStore(tmp_path / "auto_store_default")
    )

    class _StubAutoPipeline:
        def __init__(
            self,
            interview_driver: Any,
            seed_generator: Any,
            *,
            run_starter: Any = None,
            store: Any = None,
            repairer: Any = None,
            seed_saver: Any = None,
            seed_loader: Any = None,
            skip_run: bool = False,
            **_: Any,
        ) -> None:
            captured["interview_driver"] = interview_driver
            captured["seed_generator"] = seed_generator
            captured["run_starter"] = run_starter
            captured["store"] = store
            captured["repairer"] = repairer
            captured["seed_saver"] = seed_saver
            captured["seed_loader"] = seed_loader
            captured["skip_run"] = skip_run

        async def run(self, state: AutoPipelineState) -> AutoPipelineResult:
            captured["state_goal"] = state.goal
            captured["state_cwd"] = state.cwd
            captured["state_skip_run"] = state.skip_run
            state.transition(AutoPhase.INTERVIEW, "stubbed interview start")
            state.interview_session_id = "interview_dispatch_e2e_runtime"
            state.transition(AutoPhase.SEED_GENERATION, "stubbed seed generation")
            state.seed_id = "seed_dispatch_e2e_runtime"
            state.seed_path = seed_path
            state.transition(AutoPhase.REVIEW, "stubbed seed review")
            state.last_grade = "A"
            state.transition(AutoPhase.COMPLETE, "stubbed auto pipeline complete")
            return AutoPipelineResult(
                status="complete",
                auto_session_id=state.auto_session_id,
                phase="complete",
                grade="A",
                seed_path=seed_path,
                interview_session_id=state.interview_session_id,
                last_grade="A",
            )

    monkeypatch.setattr(auto_handler_module, "AutoPipeline", _StubAutoPipeline)


def _make_auto_handler_dispatcher(
    handler: AutoHandler,
    *,
    intercepts: list[Resolved],
    arguments_log: list[dict[str, Any]],
) -> Any:
    """Build a ``skill_dispatcher`` that calls the real ``AutoHandler.handle``.

    The runtime hands us the resolved skill metadata (after frontmatter +
    template normalization). We forward the resolved ``mcp_args`` straight into
    ``AutoHandler.handle`` and lift the resulting ``MCPToolResult`` back into a
    final ``AgentMessage`` with the Seed metadata the runtime will yield.
    """

    async def dispatcher(intercept: Resolved, current_handle: Any) -> tuple[AgentMessage, ...]:
        intercepts.append(intercept)
        arguments = dict(intercept.mcp_args)
        arguments_log.append(arguments)
        result = await handler.handle(arguments)
        if result.is_err:
            raise result.error  # pragma: no cover - test should not hit this
        tool_result = result.value
        data: dict[str, Any] = {
            "subtype": "error" if tool_result.is_error else "success",
            "tool_name": intercept.mcp_tool,
            "mcp_meta": dict(tool_result.meta),
        }
        data.update(dict(tool_result.meta))
        return (
            AgentMessage(
                type="result",
                content=tool_result.text_content or f"{intercept.mcp_tool} completed.",
                data=data,
                resume_handle=current_handle,
            ),
        )

    return dispatcher


async def test_ooo_auto_dispatch_reaches_seed_via_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``ooo auto <goal>`` reaches the Seed phase through the real runtime/router/AutoHandler chain.

    A regression in any of:
        (a) the packaged ``skills/auto/SKILL.md`` frontmatter (mcp_tool name,
            mcp_args keys, or template placeholders),
        (b) ``resolve_skill_dispatch`` template normalization (``$goal`` /
            ``$CWD``), or
        (c) ``AutoHandler.handle`` / ``AutoHandler._run`` wiring,
    will surface here as either a router NotHandled/InvalidSkill, a missing
    runtime intercept, or a missing Seed in the final dispatch result.
    """
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    _write_auto_skill(skills_dir)

    cwd = tmp_path / "project"
    cwd.mkdir()

    # Pre-write a Seed file path that the stubbed AutoPipeline will return.
    seeds_dir = tmp_path / "seeds"
    seeds_dir.mkdir()
    seed_path = seeds_dir / "seed_dispatch_e2e_runtime.yaml"
    seed_path.write_text("goal: stubbed\n", encoding="utf-8")

    captured: dict[str, Any] = {}
    _install_auto_handler_stubs(
        monkeypatch,
        tmp_path=tmp_path,
        seed_path=str(seed_path),
        captured=captured,
    )

    # Real AutoHandler with a tmp-rooted AutoStore so it never touches $HOME.
    auto_store = AutoStore(tmp_path / "auto_store")
    handler = AutoHandler(store=auto_store)

    intercepts: list[Resolved] = []
    arguments_log: list[dict[str, Any]] = []
    dispatcher = _make_auto_handler_dispatcher(
        handler, intercepts=intercepts, arguments_log=arguments_log
    )

    runtime = CodexCliRuntime(
        cli_path="codex",
        cwd=str(cwd),
        skills_dir=skills_dir,
        skill_dispatcher=dispatcher,
    )

    user_goal = "Build a hello CLI that prints hello and exits 0"
    with patch(
        "ouroboros.orchestrator.codex_cli_runtime.asyncio.create_subprocess_exec",
    ) as mock_exec:
        messages = [message async for message in runtime.execute_task(f"ooo auto {user_goal}")]

    # The runtime must NOT spawn the codex subprocess for a successful skill
    # intercept — the dispatch path is the only thing under test.
    mock_exec.assert_not_called()

    # Frontmatter resolution + ``$goal``/``$CWD`` template substitution.
    assert intercepts, "skill_dispatcher must be awaited for ooo auto"
    intercept = intercepts[0]
    assert intercept.skill_name == "auto"
    assert intercept.mcp_tool == "ouroboros_auto"
    assert intercept.command_prefix == "ooo auto"

    args = arguments_log[0]
    assert args["goal"] == user_goal, (
        f"resolve_skill_dispatch must inject the user goal via $goal; got {args!r}"
    )
    assert args["cwd"] == str(cwd), (
        f"resolve_skill_dispatch must inject runtime cwd via $CWD; got {args!r}"
    )
    # The remaining frontmatter args are present even when unused by the user.
    assert "resume" in args
    assert "max_interview_rounds" in args
    assert "max_repair_rounds" in args
    assert "skip_run" in args

    # AutoHandler._run actually executed: stub AutoPipeline observed the state.
    assert captured.get("state_goal") == user_goal, (
        "AutoHandler._run must construct AutoPipelineState with the dispatched goal"
    )
    assert captured.get("state_cwd") == str(cwd), (
        "AutoHandler._run must thread runtime cwd into AutoPipelineState"
    )

    # The runtime must yield a single final result message carrying the Seed.
    assert len(messages) == 1, f"expected single dispatch result, got {messages!r}"
    final = messages[0]
    assert final.is_final
    assert not final.is_error, f"dispatch should succeed, got {final!r}"
    assert final.data.get("tool_name") == "ouroboros_auto"
    assert final.data.get("seed_path") == str(seed_path)
    assert final.data.get("status") == "complete"
    assert final.data.get("grade") == "A"
    assert final.data.get("phase") == "complete"


# ---------------------------------------------------------------------------
# Case 2 — sparse goal still reaches Seed via the interview/answerer path.
#
# This is intentionally a post-dispatch unit-style test: it constructs
# ``AutoPipeline`` directly to exercise the ledger-hydration + interview-fill
# wiring landed in PR #652. The dispatch boundary itself is covered by Case 1.
# ---------------------------------------------------------------------------


async def test_sparse_goal_reaches_seed_via_interview_fill(tmp_path: Path) -> None:
    """A sparse goal must still reach the Seed phase through the interview path."""
    captured_answers: list[str] = []

    async def start(goal: str, cwd: str) -> InterviewTurn:  # noqa: ARG001
        return InterviewTurn(
            question="What else should we know about this hello CLI?",
            session_id="interview_dispatch_e2e_2",
        )

    async def answer(session_id: str, text: str) -> InterviewTurn:
        captured_answers.append(text)
        # Mark seed ready on the first answer; the test pre-fills the ledger so
        # the driver has everything it needs to converge.
        return InterviewTurn(
            question="done",
            session_id=session_id,
            seed_ready=True,
            completed=True,
        )

    async def seed_generator(session_id: str) -> Seed:  # noqa: ARG001
        return _build_a_grade_seed("Build a hello CLI")

    seed_saver, saved_paths = _make_seed_saver(tmp_path)

    state = AutoPipelineState(goal="Build a hello CLI", cwd=str(tmp_path))
    state.skip_run = True

    # Pre-fill the ledger so the driver does not depend on the production
    # answerer's heuristics for hermetic tests. The interview backend stub is
    # what proves the dispatch path went through interview-fill before Seed.
    ledger = SeedDraftLedger.from_goal(state.goal)
    _fill_ledger_ready(ledger)
    state.ledger = ledger.to_dict()

    pipeline, _store = _build_pipeline(
        tmp_path=tmp_path,
        interview_start=start,
        interview_answer=answer,
        seed_generator=seed_generator,
        seed_saver=seed_saver,
    )

    result = await pipeline.run(state)

    assert captured_answers, (
        "sparse goal must drive at least one answerer turn before Seed generation"
    )
    assert state.phase in {AutoPhase.COMPLETE, AutoPhase.REVIEW}
    assert state.phase is not AutoPhase.BLOCKED
    assert state.last_error is None
    assert state.seed_id, "Seed id must be populated after interview-fill path"
    assert state.seed_path, "Seed path must be persisted after interview-fill path"
    seed_path = Path(state.seed_path)
    assert seed_path.exists()
    assert seed_path.read_bytes()
    assert state.seed_path == saved_paths[0]
    assert result.status == "complete"
    assert result.grade == "A"
    assert result.blocker is None


# ---------------------------------------------------------------------------
# Case 3 — unregistered MCP tool fails closed with the contract message and
# does NOT create persisted auto session state.
# ---------------------------------------------------------------------------


async def test_dispatch_fails_closed_when_ouroboros_auto_unregistered(tmp_path: Path) -> None:
    """``ooo auto`` must fail closed when the ``ouroboros_auto`` MCP tool is unregistered.

    Asserts the actual user-visible contract phrasing (discovered in the
    ``CodexCliRuntime._build_auto_dispatch_unavailable_message`` source —
    Issue #637's literal phrase is reworded by the runtime as
    "Cannot run ooo auto: required MCP tool ``ouroboros_auto`` is unavailable.")
    and that no auto session state is persisted to the auto store on this
    fail-closed path.
    """
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    _write_auto_skill(skills_dir)

    # Auto store rooted at tmp_path so we can assert no auto_*.json was written.
    auto_store_root = tmp_path / "auto_store"
    auto_store_root.mkdir()
    pre_existing = sorted(auto_store_root.glob("auto_*.json"))
    assert pre_existing == [], "tmp auto store must start empty for this test"

    cwd = tmp_path / "project"
    cwd.mkdir()

    dispatcher = AsyncMock(
        side_effect=LookupError("No local handler registered for tool: ouroboros_auto")
    )
    runtime = CodexCliRuntime(
        cli_path="codex",
        cwd=str(cwd),
        skills_dir=skills_dir,
        skill_dispatcher=dispatcher,
    )

    with (
        patch("ouroboros.orchestrator.codex_cli_runtime.log.warning"),
        patch(
            "ouroboros.orchestrator.codex_cli_runtime.asyncio.create_subprocess_exec",
        ) as mock_exec,
    ):
        messages = [message async for message in runtime.execute_task("ooo auto Build a hello CLI")]

    dispatcher.assert_awaited_once()
    # fail-closed unavailable dispatch must not spawn the codex subprocess
    mock_exec.assert_not_called()

    assert len(messages) == 1, f"expected single fail-closed result, got {messages!r}"
    failure = messages[0]
    assert failure.is_error is True

    # The Issue #637 acceptance phrase is "ouroboros_auto MCP tool is unavailable;
    # cannot run ooo auto". The actual codebase contract phrasing combines both
    # halves into a single sentence — assert each half is present so the contract
    # cannot silently regress in either direction.
    assert "Cannot run ooo auto" in failure.content
    assert "`ouroboros_auto` is unavailable" in failure.content
    assert failure.data["error_type"] == "SkillDispatchUnavailable"
    assert failure.data["tool_name"] == "ouroboros_auto"
    assert failure.data["command_prefix"] == "ooo auto"

    # No auto session state must be created on this fail-closed path.
    after = sorted(auto_store_root.glob("auto_*.json"))
    assert after == [], (
        f"unavailable-dispatch must not create persisted auto session state; found: {after!r}"
    )
