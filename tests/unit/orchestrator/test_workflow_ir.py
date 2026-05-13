"""Unit tests for the Workflow IR schema and validator.

Covers the acceptance contract from issue #956 PR-1:

* Workflow IR models exist with a versioned schema.
* Validation rejects dangling edges, duplicate node ids, missing
  terminal paths, and missing required evidence/output schema metadata.
* Fan-out / fan-in / barrier metadata can be represented.
* No Microsoft Agent Framework or external workflow SDK dependency
  enters core. (This is enforced by ``pyproject.toml``; this test file
  only exercises the Python module imports to verify the surface remains
  framework-agnostic.)
"""

from __future__ import annotations

from pydantic import ValidationError
import pytest

from ouroboros.orchestrator.workflow_ir import (
    WORKFLOW_IR_SCHEMA_VERSION,
    EdgeKind,
    NodeKind,
    NodeOwner,
    SourceKind,
    WorkflowEdge,
    WorkflowNode,
    WorkflowSpec,
    WorkflowValidationResult,
    validate_workflow,
)


def _make_task(
    node_id: str,
    *,
    owner: NodeOwner = NodeOwner.HARNESS,
    evidence_schema_ref: str | None = None,
) -> WorkflowNode:
    return WorkflowNode(
        node_id=node_id,
        kind=NodeKind.TASK,
        owner=owner,
        evidence_schema_ref=evidence_schema_ref,
    )


def _make_terminal(node_id: str) -> WorkflowNode:
    return WorkflowNode(
        node_id=node_id,
        kind=NodeKind.TERMINAL,
        owner=NodeOwner.HARNESS,
    )


def _edge(source: str, target: str, *, kind: EdgeKind = EdgeKind.DIRECT) -> WorkflowEdge:
    return WorkflowEdge(
        edge_id=f"edge_{source}_{target}",
        source=source,
        target=target,
        kind=kind,
    )


class TestSchemaVersion:
    def test_initial_version_is_one(self) -> None:
        assert WORKFLOW_IR_SCHEMA_VERSION == 1


class TestWorkflowNode:
    def test_generates_prefixed_id(self) -> None:
        node = _make_task("node_a")
        # explicit id retained, but default factory uses the node_ prefix
        default_node = WorkflowNode(kind=NodeKind.TASK, owner=NodeOwner.HARNESS)
        assert node.node_id == "node_a"
        assert default_node.node_id.startswith("node_")

    def test_is_frozen(self) -> None:
        node = _make_task("node_a")
        with pytest.raises(ValidationError):
            node.name = "renamed"  # type: ignore[misc]

    def test_evidence_required_for_agent(self) -> None:
        with pytest.raises(ValidationError):
            WorkflowNode(
                node_id="node_agent",
                kind=NodeKind.TASK,
                owner=NodeOwner.AGENT,
            )

    def test_evidence_required_for_plugin(self) -> None:
        with pytest.raises(ValidationError):
            WorkflowNode(
                node_id="node_plugin",
                kind=NodeKind.TASK,
                owner=NodeOwner.PLUGIN,
            )

    def test_evidence_required_for_verifier(self) -> None:
        with pytest.raises(ValidationError):
            WorkflowNode(
                node_id="node_verifier",
                kind=NodeKind.TASK,
                owner=NodeOwner.VERIFIER,
            )

    def test_harness_owner_omits_evidence_ok(self) -> None:
        node = WorkflowNode(
            node_id="node_h",
            kind=NodeKind.TASK,
            owner=NodeOwner.HARNESS,
        )
        assert node.evidence_schema_ref is None

    def test_human_gate_omits_evidence_ok(self) -> None:
        node = WorkflowNode(
            node_id="node_gate",
            kind=NodeKind.TASK,
            owner=NodeOwner.HUMAN_GATE,
        )
        assert node.evidence_schema_ref is None


class TestWorkflowEdge:
    def test_self_loop_rejected(self) -> None:
        with pytest.raises(ValidationError):
            WorkflowEdge(edge_id="edge_x", source="node_a", target="node_a")

    def test_blank_endpoint_rejected(self) -> None:
        with pytest.raises(ValidationError):
            WorkflowEdge(edge_id="edge_x", source="  ", target="node_a")

    def test_endpoints_trimmed(self) -> None:
        edge = WorkflowEdge(
            edge_id="edge_x",
            source="  node_a  ",
            target="  node_b  ",
        )
        assert edge.source == "node_a"
        assert edge.target == "node_b"


class TestValidateWorkflow:
    def test_minimal_valid_spec(self) -> None:
        spec = WorkflowSpec(
            source=SourceKind.SYNTHETIC,
            nodes=(_make_task("a"), _make_terminal("end")),
            edges=(_edge("a", "end", kind=EdgeKind.TERMINAL),),
        )
        result = validate_workflow(spec)
        assert isinstance(result, WorkflowValidationResult)
        assert result.ok is True
        assert result.errors == ()

    def test_duplicate_node_id(self) -> None:
        spec = WorkflowSpec(
            source=SourceKind.SYNTHETIC,
            nodes=(_make_task("a"), _make_task("a"), _make_terminal("end")),
            edges=(_edge("a", "end"),),
        )
        result = validate_workflow(spec)
        assert result.ok is False
        codes = {e.code for e in result.errors}
        assert "duplicate_node_id" in codes

    def test_duplicate_edge_id(self) -> None:
        spec = WorkflowSpec(
            source=SourceKind.SYNTHETIC,
            nodes=(_make_task("a"), _make_task("b"), _make_terminal("end")),
            edges=(
                WorkflowEdge(edge_id="dup", source="a", target="b"),
                WorkflowEdge(edge_id="dup", source="b", target="end"),
            ),
        )
        result = validate_workflow(spec)
        assert result.ok is False
        assert any(e.code == "duplicate_edge_id" for e in result.errors)

    def test_dangling_edge(self) -> None:
        spec = WorkflowSpec(
            source=SourceKind.SYNTHETIC,
            nodes=(_make_task("a"), _make_terminal("end")),
            edges=(_edge("a", "ghost"),),
        )
        result = validate_workflow(spec)
        assert result.ok is False
        assert any(e.code == "dangling_edge" for e in result.errors)

    def test_no_terminal_node(self) -> None:
        spec = WorkflowSpec(
            source=SourceKind.SYNTHETIC,
            nodes=(_make_task("a"), _make_task("b")),
            edges=(_edge("a", "b"),),
        )
        result = validate_workflow(spec)
        assert result.ok is False
        assert any(e.code == "no_terminal_node" for e in result.errors)

    def test_unreachable_terminal(self) -> None:
        # Two tasks loop to each other; terminal sits alone with no incoming.
        spec = WorkflowSpec(
            source=SourceKind.SYNTHETIC,
            nodes=(_make_task("a"), _make_task("b"), _make_terminal("end")),
            edges=(_edge("a", "b"), _edge("b", "a")),
        )
        result = validate_workflow(spec)
        assert result.ok is False
        assert any(e.code == "unreachable_terminal" for e in result.errors)

    def test_isolated_node_warning(self) -> None:
        spec = WorkflowSpec(
            source=SourceKind.SYNTHETIC,
            nodes=(
                _make_task("a"),
                _make_task("orphan"),
                _make_terminal("end"),
            ),
            edges=(_edge("a", "end"),),
        )
        result = validate_workflow(spec)
        assert result.ok is True  # warning, not error
        assert any(w.code == "isolated_node" for w in result.warnings)

    def test_missing_evidence_schema_detected_by_validator(self) -> None:
        # Build a spec that bypasses Pydantic re-validation (mirrors a future
        # load-from-untrusted-JSON path). Both the node and the enclosing
        # spec are constructed with ``model_construct`` so the per-node
        # validator does not pre-empt the dedicated validator rule.
        bad_node = WorkflowNode.model_construct(
            schema_version=WORKFLOW_IR_SCHEMA_VERSION,
            node_id="agent_bad",
            kind=NodeKind.TASK,
            owner=NodeOwner.AGENT,
            evidence_schema_ref=None,
            capability_envelope=(),
            runtime_hints={},
            metadata={},
            name="",
            input_schema_ref=None,
        )
        end_node = _make_terminal("end")
        edge = _edge("agent_bad", "end")
        spec = WorkflowSpec.model_construct(
            schema_version=WORKFLOW_IR_SCHEMA_VERSION,
            spec_id="wfspec_test",
            source=SourceKind.SYNTHETIC,
            source_ref=None,
            nodes=(bad_node, end_node),
            edges=(edge,),
            metadata={},
        )
        result = validate_workflow(spec)
        assert result.ok is False
        assert any(e.code == "missing_evidence_schema" for e in result.errors)


class TestFanOutFanInRepresentation:
    """#956 acceptance criterion #4 — fan-out / fan-in / barrier
    metadata must be representable even if not fully executed yet."""

    def test_fan_out_fan_in_round_trip(self) -> None:
        spec = WorkflowSpec(
            source=SourceKind.SYNTHETIC,
            nodes=(
                _make_task("root"),
                WorkflowNode(
                    node_id="split",
                    kind=NodeKind.FAN_OUT,
                    owner=NodeOwner.HARNESS,
                ),
                _make_task("left"),
                _make_task("right"),
                WorkflowNode(
                    node_id="join",
                    kind=NodeKind.FAN_IN,
                    owner=NodeOwner.HARNESS,
                ),
                _make_terminal("end"),
            ),
            edges=(
                _edge("root", "split"),
                WorkflowEdge(
                    edge_id="fanout_left",
                    source="split",
                    target="left",
                    kind=EdgeKind.FAN_OUT,
                ),
                WorkflowEdge(
                    edge_id="fanout_right",
                    source="split",
                    target="right",
                    kind=EdgeKind.FAN_OUT,
                ),
                WorkflowEdge(
                    edge_id="fanin_left",
                    source="left",
                    target="join",
                    kind=EdgeKind.FAN_IN,
                ),
                WorkflowEdge(
                    edge_id="fanin_right",
                    source="right",
                    target="join",
                    kind=EdgeKind.FAN_IN,
                ),
                _edge("join", "end", kind=EdgeKind.TERMINAL),
            ),
        )
        result = validate_workflow(spec)
        assert result.ok is True, result.errors


class TestNoExternalFrameworkDependency:
    """Smoke-test that the IR module does not import MAF / DurableTask /
    Azure workflow SDKs at runtime. Acceptance criterion #6 of #956."""

    def test_module_imports_are_repo_local(self) -> None:
        import ouroboros.orchestrator.workflow_ir as wf_ir

        # The module's top-level imports must not pull in forbidden SDKs.
        forbidden_prefixes = (
            "agent_framework",
            "azure.durabletask",
            "microsoft.agent",
        )
        for module_name in list(__import__("sys").modules):
            assert not any(
                module_name.startswith(prefix) for prefix in forbidden_prefixes
            ), f"Forbidden dependency loaded transitively: {module_name}"
        # Tautological assertion to keep the import alive in the test body.
        assert wf_ir.WORKFLOW_IR_SCHEMA_VERSION == 1
