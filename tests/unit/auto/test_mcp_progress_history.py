"""Tests for the MCP progress event history surfaced by AutoHandler."""

from __future__ import annotations

import pytest

from ouroboros.auto.pipeline import AutoPipelineResult
from ouroboros.auto.progress import AutoProgressEvent
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


def test_result_meta_serializes_progress_events_in_order() -> None:
    events = [
        AutoProgressEvent(
            auto_session_id="auto_test",
            phase="interview",
            kind="phase",
            message="asking interview round 1/12",
            timestamp="2026-05-01T12:00:00+00:00",
        ),
        AutoProgressEvent(
            auto_session_id="auto_test",
            phase="review",
            kind="grade",
            message="Seed grade A",
            grade="A",
            timestamp="2026-05-01T12:25:00+00:00",
        ),
        AutoProgressEvent(
            auto_session_id="auto_test",
            phase="repair",
            kind="repair",
            message="repair round 1",
            round=1,
            timestamp="2026-05-01T12:20:00+00:00",
        ),
    ]

    meta = _result_meta(_result(), progress_events=events)

    history = meta["progress_events"]
    assert len(history) == 3
    # Each entry must be JSON-friendly (no enums/dataclasses left behind) and
    # must elide the redundant top-level auto_session_id.
    for entry in history:
        assert "auto_session_id" not in entry
        assert set(entry.keys()) == {"phase", "kind", "message", "round", "grade", "timestamp"}
    assert [entry["kind"] for entry in history] == ["phase", "grade", "repair"]
    assert history[1]["grade"] == "A"
    assert history[2]["round"] == 1


@pytest.mark.asyncio
async def test_auto_handler_meta_includes_progress_events_emitted_during_run(
    monkeypatch, tmp_path
) -> None:
    captured_session: dict[str, str] = {}

    class FakePipeline:
        def __init__(self, *args, **kwargs):  # noqa: ANN002, ANN003, ARG002
            self._cb = kwargs.get("progress_callback")

        async def run(self, run_state):  # noqa: ANN001
            captured_session["id"] = run_state.auto_session_id
            if self._cb is not None:
                self._cb(
                    AutoProgressEvent(
                        auto_session_id=run_state.auto_session_id,
                        phase="interview",
                        kind="phase",
                        message="asking interview round 1/12",
                        timestamp="2026-05-01T12:00:00+00:00",
                    )
                )
                self._cb(
                    AutoProgressEvent(
                        auto_session_id=run_state.auto_session_id,
                        phase="review",
                        kind="grade",
                        message="Seed grade A",
                        grade="A",
                        timestamp="2026-05-01T12:25:00+00:00",
                    )
                )
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
    history = result.value.meta["progress_events"]
    assert len(history) == 2
    assert history[0]["kind"] == "phase"
    assert history[0]["phase"] == "interview"
    assert history[1]["kind"] == "grade"
    assert history[1]["grade"] == "A"
    # Top-level auto_session_id is still authoritative.
    assert result.value.meta["auto_session_id"] == captured_session["id"]


@pytest.mark.asyncio
async def test_auto_handler_meta_omits_progress_events_when_pipeline_emits_none(
    monkeypatch, tmp_path
) -> None:
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

    monkeypatch.setattr(auto_module, "AutoStore", lambda: AutoStore(tmp_path / "store"))
    monkeypatch.setattr(auto_module, "AutoPipeline", FakePipeline)
    monkeypatch.setattr(auto_module, "InterviewHandler", FakeHandler)
    monkeypatch.setattr(auto_module, "GenerateSeedHandler", FakeHandler)
    monkeypatch.setattr(auto_module, "ExecuteSeedHandler", FakeHandler)
    monkeypatch.setattr(auto_module, "StartExecuteSeedHandler", FakeHandler)

    result = await AutoHandler().handle({"goal": "Build a CLI", "cwd": str(tmp_path)})

    assert result.is_ok
    assert "progress_events" not in result.value.meta
