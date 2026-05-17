from __future__ import annotations

from ouroboros.auto.execution_acceptance import normalize_execution_acceptance
from ouroboros.core.seed import (
    EvaluationPrinciple,
    ExitCondition,
    OntologyField,
    OntologySchema,
    Seed,
    SeedMetadata,
)


def _seed(*criteria: str) -> Seed:
    return Seed(
        goal="Verify ooo auto with a minimal coding task",
        constraints=("Only edit hello_auto.py and tests/test_hello_auto.py",),
        acceptance_criteria=criteria,
        ontology_schema=OntologySchema(
            name="HelloAuto",
            description="Minimal coding task",
            fields=(OntologyField(name="file", field_type="string", description="File"),),
        ),
        evaluation_principles=(
            EvaluationPrinciple(name="testability", description="Runnable tests pass"),
        ),
        exit_conditions=(
            ExitCondition(
                name="verified",
                description="Targeted test passes",
                evaluation_criteria="All execution criteria pass",
            ),
        ),
        metadata=SeedMetadata(seed_id="seed_test", ambiguity_score=0.1),
    )


def test_normalize_execution_acceptance_drops_auto_report_criteria() -> None:
    seed = _seed(
        "`ooo auto` is dispatched to the MCP tool `ouroboros_auto`.",
        "Manual fallback is not used.",
        "`hello_auto.py` defines `hello_auto()` returning exactly `hello from ooo auto`.",
        "`tests/test_hello_auto.py` imports `hello_auto` and asserts the exact return value.",
        "The exact command `uv run pytest tests/test_hello_auto.py` passes.",
        "Final report includes auto session id, seed id, seed path, and test result.",
    )

    normalized = normalize_execution_acceptance(seed)

    assert normalized.acceptance_criteria == (
        "`hello_auto.py` defines `hello_auto()` returning exactly `hello from ooo auto`.",
        "`tests/test_hello_auto.py` imports `hello_auto` and asserts the exact return value.",
        "The exact command `uv run pytest tests/test_hello_auto.py` passes.",
    )


def test_normalize_execution_acceptance_keeps_original_when_filter_would_empty() -> None:
    seed = _seed("Final report includes auto session id and seed id.")

    assert normalize_execution_acceptance(seed) is seed
