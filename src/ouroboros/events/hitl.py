"""Event factories for human-in-the-loop WAIT/RESUME contracts."""

from __future__ import annotations

from ouroboros.core.hitl_contract import HumanInputRequest, HumanInputResponse
from ouroboros.events.base import BaseEvent


def create_hitl_requested_event(request: HumanInputRequest) -> BaseEvent:
    return BaseEvent(
        type=HumanInputRequest.REQUESTED_EVENT_TYPE,
        aggregate_type="hitl",
        aggregate_id=request.aggregate_id,
        data=request.to_event_data(),
    )


def create_hitl_answered_event(response: HumanInputResponse) -> BaseEvent:
    return BaseEvent(
        type=HumanInputResponse.ANSWERED_EVENT_TYPE,
        aggregate_type="hitl",
        aggregate_id=response.aggregate_id,
        data=response.to_event_data(),
    )


def create_hitl_timed_out_event(request: HumanInputRequest, *, reason: str) -> BaseEvent:
    data = request.to_event_data()
    data["reason"] = reason
    return BaseEvent(
        type=HumanInputRequest.TIMED_OUT_EVENT_TYPE,
        aggregate_type="hitl",
        aggregate_id=request.aggregate_id,
        data=data,
    )


def create_hitl_cancelled_event(
    request: HumanInputRequest, *, reason: str, actor: str | None = None
) -> BaseEvent:
    data = request.to_event_data()
    data["reason"] = reason
    if actor is not None:
        data["actor"] = actor
    return BaseEvent(
        type=HumanInputRequest.CANCELLED_EVENT_TYPE,
        aggregate_type="hitl",
        aggregate_id=request.aggregate_id,
        data=data,
    )
