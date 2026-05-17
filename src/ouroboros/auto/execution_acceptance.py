"""Execution-facing acceptance criteria normalization for auto-generated Seeds."""

from __future__ import annotations

from ouroboros.core.seed import Seed

_AUTO_DISPATCH_MARKERS = (
    "`ooo auto` is dispatched",
    "ooo auto is dispatched",
    "handled by ouroboros auto/mcp",
    "handled by ouroboros auto",
    "dispatch through mcp",
    "dispatched to the mcp",
    "mcp dispatch",
)

_FINAL_REPORT_WRAPPER_PREFIXES = (
    "final report includes auto session id",
    "the final report includes auto session id",
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

    The classifier is intentionally narrow: it recognizes the observation
    wrapper's own reporting/dispatch obligations, while preserving product
    requirements that merely mention concepts such as manual fallback, final
    reports, IDs, blocker history, or interview fields.
    """
    lowered = " ".join(criterion.casefold().split())
    if any(marker in lowered for marker in _AUTO_DISPATCH_MARKERS):
        return True
    if "manual fallback" in lowered and (
        "not used" in lowered or "was not used" in lowered or "is not used" in lowered
    ):
        return True
    if lowered.startswith(_FINAL_REPORT_WRAPPER_PREFIXES):
        return True
    return "ouroboros_auto" in lowered and (
        "unavailable" in lowered or "interpreted as normal text" in lowered
    )
