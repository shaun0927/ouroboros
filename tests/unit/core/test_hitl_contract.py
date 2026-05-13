from __future__ import annotations

import dataclasses

import pytest

from ouroboros.core.hitl_contract import (
    HumanInputKind,
    HumanInputRequest,
    HumanInputResponse,
    HumanInputResponseKind,
    HumanInputRiskClass,
    HumanInputSource,
    HumanInputTimeoutAction,
)


def test_human_input_request_serializes_wait_contract() -> None:
    request = HumanInputRequest(
        request_id="hitl-1",
        session_id="session-1",
        run_id="run-1",
        invocation_id="invoke-1",
        created_by="deep-interview",
        kind=HumanInputKind.SINGLE_SELECT,
        source=HumanInputSource.INTERVIEW,
        risk_class=HumanInputRiskClass.MATERIAL_BRANCH,
        question="Which track should run first?",
        options=("plugin", "runtime"),
        required_permission="plugin:execute",
        timeout_seconds=60,
        timeout_action=HumanInputTimeoutAction.EXPIRE_BLOCKED,
        resume_target="deep-interview:round-2",
        surface="structured_question",
        payload={"redacted": True},
    )

    data = request.to_event_data()

    assert request.aggregate_id == "run-1"
    assert data["kind"] == "single_select"
    assert data["source"] == "interview"
    assert data["risk_class"] == "material_branch"
    assert data["timeout_action"] == "expire_blocked"
    assert data["required_permission"] == "plugin:execute"
    assert data["options"] == ["plugin", "runtime"]


def test_select_request_requires_options() -> None:
    with pytest.raises(ValueError, match="requires at least one option"):
        HumanInputRequest(
            request_id="hitl-1",
            session_id="session-1",
            created_by="plan",
            kind=HumanInputKind.SINGLE_SELECT,
            source=HumanInputSource.PLAN_APPROVAL,
            risk_class=HumanInputRiskClass.MATERIAL_BRANCH,
            question="Pick one",
            resume_target="plan:approval",
        )


def test_request_rejects_secret_like_persisted_payload() -> None:
    with pytest.raises(ValueError, match="secret-like"):
        HumanInputRequest(
            request_id="hitl-1",
            session_id="session-1",
            created_by="plugin-firewall",
            kind=HumanInputKind.APPROVAL,
            source=HumanInputSource.PLUGIN_FIREWALL,
            risk_class=HumanInputRiskClass.CREDENTIAL_GATED,
            question="Approve?",
            resume_target="plugin:permission",
            payload={"api_key": "should-not-persist"},
        )


def test_human_input_response_serializes_matching_answer() -> None:
    response = HumanInputResponse(
        request_id="hitl-1",
        session_id="session-1",
        run_id="run-1",
        actor="local-user",
        response_kind=HumanInputResponseKind.APPROVAL,
        approval_decision=True,
        surface="cli",
    )

    data = response.to_event_data()

    assert response.aggregate_id == "run-1"
    assert data["request_id"] == "hitl-1"
    assert data["actor"] == "local-user"
    assert data["response_kind"] == "approval"
    assert data["approval_decision"] is True


def test_response_is_frozen() -> None:
    response = HumanInputResponse(
        request_id="hitl-1",
        actor="local-user",
        response_kind=HumanInputResponseKind.TEXT,
        text="continue",
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        response.actor = "other"  # type: ignore[misc]


def test_response_rejects_approval_decision_on_non_approval() -> None:
    with pytest.raises(ValueError, match="approval_decision"):
        HumanInputResponse(
            request_id="hitl-1",
            actor="local-user",
            response_kind=HumanInputResponseKind.TEXT,
            text="yes",
            approval_decision=True,
        )
