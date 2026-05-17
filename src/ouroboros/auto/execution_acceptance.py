"""Execution-facing acceptance criteria normalization for auto-generated Seeds."""

from __future__ import annotations

from ouroboros.core.seed import Seed

_META_NEEDLES = (
    "auto session id",
    "auto-session",
    "blocker recurrence",
    "dispatch through mcp",
    "dispatched to the mcp",
    "final report",
    "interview closure",
    "last_question",
    "manual fallback",
    "mcp dispatch",
    "ouroboros_auto",
    "previous blocker",
    "run session id",
    "seed grade",
    "seed id",
    "seed path",
)

_CONCRETE_EXECUTION_NEEDLES = (
    ".py",
    "changed files",
    "create ",
    "created",
    "exists",
    "file",
    "hello_auto",
    "pytest",
    "return",
    "test",
    "uv run",
    "write",
)


def normalize_execution_acceptance(seed: Seed) -> Seed:
    """Remove auto-observation/reporting criteria from execution Seeds.

    Auto observation prompts often include reporting requirements such as
    "manual fallback was not used" or "final report includes seed id". Those
    are wrapper/reporting duties, not implementation duties for the execution
    worker. Keep concrete file/test criteria and drop only meta-observation
    criteria when doing so still leaves executable acceptance criteria.
    """
    criteria = tuple(ac for ac in seed.acceptance_criteria if ac and ac.strip())
    if not criteria:
        return seed

    concrete = tuple(ac for ac in criteria if _is_concrete_execution_criterion(ac))
    filtered = tuple(ac for ac in concrete if not _is_auto_observation_criterion(ac))
    if not filtered or filtered == criteria:
        return seed
    return seed.model_copy(update={"acceptance_criteria": filtered})


def _is_auto_observation_criterion(criterion: str) -> bool:
    lowered = criterion.casefold()
    return any(needle in lowered for needle in _META_NEEDLES)


def _is_concrete_execution_criterion(criterion: str) -> bool:
    lowered = criterion.casefold()
    return any(needle in lowered for needle in _CONCRETE_EXECUTION_NEEDLES)
