"""Execution-facing acceptance criteria normalization for auto-generated Seeds."""

from __future__ import annotations

from ouroboros.core.seed import Seed

_AUTO_REPORTING_CRITERION_PHRASES = (
    "auto session id",
    "auto-session",
    "blocker recurrence",
    "`ooo auto` is dispatched",
    "ooo auto is dispatched",
    "dispatch through mcp",
    "dispatched to the mcp",
    "handled by ouroboros auto/mcp",
    "handled by ouroboros auto",
    "final report",
    "interview closure",
    "last_question",
    "manual fallback",
    "mcp dispatch",
    "ouroboros_auto` is unavailable",
    "`ouroboros_auto` is unavailable",
    "previous blocker",
    "run session id",
    "seed grade",
    "seed id",
    "seed path",
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

    filtered = tuple(ac for ac in criteria if not is_auto_reporting_acceptance_criterion(ac))
    if not filtered or filtered == criteria:
        return seed
    return seed.model_copy(update={"acceptance_criteria": filtered})


def is_auto_reporting_acceptance_criterion(criterion: str) -> bool:
    """Return true for wrapper/report-only criteria, not execution requirements.

    This is intentionally a denylist of known auto/session reporting phrases.
    User-authored execution criteria are preserved by default because the auto
    Seed contract must not be narrowed by a vocabulary allowlist.
    """
    lowered = criterion.casefold()
    return any(phrase in lowered for phrase in _AUTO_REPORTING_CRITERION_PHRASES)
