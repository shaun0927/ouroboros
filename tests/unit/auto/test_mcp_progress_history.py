"""Tests for the MCP progress event history surfaced by AutoHandler."""

from __future__ import annotations

from typing import Any

import pytest

from ouroboros.auto.pipeline import AutoPipelineResult
from ouroboros.auto.state import AutoStore
from ouroboros.mcp.tools import auto_handler as auto_module
from ouroboros.mcp.tools.auto_handler import AutoHandler, _result_meta


def _result() -> AutoPipelineResult:
    return AutoPipelineResult(
        status="complete",
        auto_session_id="auto_test",
        phase="complete",
        last_progress_message="execution started for grade A Seed",
        last_progress_at="2026-05-01T12:30:00+00:00",
    )


def test_result_meta_omits_progress_events_when_empty() -> None:
    meta = _result_meta(_result(), progress_events=[])

    assert "progress_events" not in meta


def test_result_meta_passes_persisted_progress_events_through_unchanged() -> None:
    persisted: list[dict[str, Any]] = [
        {
            "phase": "interview",
            "kind": "phase",
            "message": "asking interview round 1/12",
            "round": None,
            "grade": None,
            "timestamp": "2026-05-01T12:00:00+00:00",
        },
        {
            "phase": "review",
            "kind": "grade",
            "message": "Seed grade A",
            "round": None,
            "grade": "A",
            "timestamp": "2026-05-01T12:25:00+00:00",
        },
        {
            "phase": "repair",
            "kind": "repair",
            "message": "repair round 1",
            "round": 1,
            "grade": None,
            "timestamp": "2026-05-01T12:20:00+00:00",
        },
    ]

    meta = _result_meta(_result(), progress_events=persisted)

    history = meta["progress_events"]
    assert history == persisted
    # The handler returns a defensive copy so consumers cannot mutate
    # the persisted log through the meta payload.
    assert history is not persisted
    for entry in history:
        assert "auto_session_id" not in entry
        assert set(entry.keys()) == {"phase", "kind", "message", "round", "grade", "timestamp"}


@pytest.mark.asyncio
async def test_auto_handler_meta_includes_persisted_progress_events_after_resume(
    monkeypatch, tmp_path
) -> None:
    """A second handle() invocation must keep prior session events.

    The progress event history is persisted on ``AutoPipelineState`` so a
    resumed session reads back every event from earlier invocations,
    not just the events emitted by the current ``handle()`` call.
    """
    captured_session: dict[str, str] = {}
    store_root = tmp_path / "store"

    class FakePipeline:
        def __init__(self, *args, **kwargs):  # noqa: ANN002, ANN003, ARG002
            pass

        async def run(self, run_state):  # noqa: ANN001
            captured_session["id"] = run_state.auto_session_id
            # Pre-existing history persisted by an earlier invocation.
            run_state.progress_events.append(
                {
                    "phase": "interview",
                    "kind": "phase",
                    "message": "asking interview round 1/12",
                    "round": None,
                    "grade": None,
                    "timestamp": "2026-05-01T12:00:00+00:00",
                }
            )
            # New event recorded during *this* invocation.
            run_state.progress_events.append(
                {
                    "phase": "review",
                    "kind": "grade",
                    "message": "Seed grade A",
                    "round": None,
                    "grade": "A",
                    "timestamp": "2026-05-01T12:25:00+00:00",
                }
            )
            AutoStore(store_root).save(run_state)
            return AutoPipelineResult(
                status="complete",
                auto_session_id=run_state.auto_session_id,
                phase="complete",
            )

    class FakeHandler:
        def __init__(self, *args, **kwargs):  # noqa: ANN002, ANN003, ARG002
            pass

    monkeypatch.setattr(auto_module, "AutoStore", lambda: AutoStore(store_root))
    monkeypatch.setattr(auto_module, "AutoPipeline", FakePipeline)
    monkeypatch.setattr(auto_module, "InterviewHandler", FakeHandler)
    monkeypatch.setattr(auto_module, "GenerateSeedHandler", FakeHandler)
    monkeypatch.setattr(auto_module, "ExecuteSeedHandler", FakeHandler)
    monkeypatch.setattr(auto_module, "StartExecuteSeedHandler", FakeHandler)

    result = await AutoHandler().handle({"goal": "Build a CLI", "cwd": str(tmp_path)})

    assert result.is_ok
    history = result.value.meta["progress_events"]
    assert len(history) == 2
    assert history[0]["kind"] == "phase"
    assert history[0]["phase"] == "interview"
    assert history[1]["kind"] == "grade"
    assert history[1]["grade"] == "A"
    assert result.value.meta["auto_session_id"] == captured_session["id"]


@pytest.mark.asyncio
async def test_auto_handler_meta_omits_progress_events_when_state_log_empty(
    monkeypatch, tmp_path
) -> None:
    class FakePipeline:
        def __init__(self, *args, **kwargs):  # noqa: ANN002, ANN003, ARG002
            pass

        async def run(self, run_state):  # noqa: ANN001
            AutoStore(tmp_path / "store").save(run_state)
            return AutoPipelineResult(
                status="complete",
                auto_session_id=run_state.auto_session_id,
                phase="complete",
            )

    class FakeHandler:
        def __init__(self, *args, **kwargs):  # noqa: ANN002, ANN003, ARG002
            pass

    monkeypatch.setattr(auto_module, "AutoStore", lambda: AutoStore(tmp_path / "store"))
    monkeypatch.setattr(auto_module, "AutoPipeline", FakePipeline)
    monkeypatch.setattr(auto_module, "InterviewHandler", FakeHandler)
    monkeypatch.setattr(auto_module, "GenerateSeedHandler", FakeHandler)
    monkeypatch.setattr(auto_module, "ExecuteSeedHandler", FakeHandler)
    monkeypatch.setattr(auto_module, "StartExecuteSeedHandler", FakeHandler)

    result = await AutoHandler().handle({"goal": "Build a CLI", "cwd": str(tmp_path)})

    assert result.is_ok
    assert "progress_events" not in result.value.meta


@pytest.mark.asyncio
async def test_auto_handler_meta_tolerates_unloadable_state_after_run(
    monkeypatch, tmp_path
) -> None:
    """If the store cannot be re-read, the meta payload is still produced.

    A degraded store must never poison an otherwise-successful run.
    """

    class FakePipeline:
        def __init__(self, *args, **kwargs):  # noqa: ANN002, ANN003, ARG002
            pass

        async def run(self, run_state):  # noqa: ANN001
            return AutoPipelineResult(
                status="complete",
                auto_session_id=run_state.auto_session_id,
                phase="complete",
            )

    class FakeHandler:
        def __init__(self, *args, **kwargs):  # noqa: ANN002, ANN003, ARG002
            pass

    class _ExplodingStore:
        def __init__(self, *_args, **_kwargs):
            pass

        def load(self, _session_id):
            raise RuntimeError("simulated store failure")

        def save(self, _state):  # pragma: no cover - unused in this path
            return None

    monkeypatch.setattr(auto_module, "AutoStore", _ExplodingStore)
    monkeypatch.setattr(auto_module, "AutoPipeline", FakePipeline)
    monkeypatch.setattr(auto_module, "InterviewHandler", FakeHandler)
    monkeypatch.setattr(auto_module, "GenerateSeedHandler", FakeHandler)
    monkeypatch.setattr(auto_module, "ExecuteSeedHandler", FakeHandler)
    monkeypatch.setattr(auto_module, "StartExecuteSeedHandler", FakeHandler)

    result = await AutoHandler().handle({"goal": "Build a CLI", "cwd": str(tmp_path)})

    assert result.is_ok
    assert "progress_events" not in result.value.meta
