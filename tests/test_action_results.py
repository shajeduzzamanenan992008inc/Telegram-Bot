from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from enan_ultra.actions import ActionRegistry
from enan_ultra.models import ActionResult


def _context() -> SimpleNamespace:
    return SimpleNamespace(
        update=SimpleNamespace(
            effective_message=SimpleNamespace(message_id=12),
            effective_chat=SimpleNamespace(id=34),
            effective_user=SimpleNamespace(id=56),
        ),
        db=SimpleNamespace(
            claim_action_execution=AsyncMock(return_value=True),
            finish_action_execution=AsyncMock(),
        ),
    )


def test_action_result_defaults_and_rejects_invalid_contract_values() -> None:
    assert ActionResult(True, "sent").status == "done"
    assert ActionResult(False, "failed").status == "failed"
    with pytest.raises(ValueError, match="status"):
        ActionResult(True, "result", status="success")
    with pytest.raises(ValueError, match="success flag"):
        ActionResult(False, "request pending", status="unverified")
    with pytest.raises(ValueError, match="duration"):
        ActionResult(True, "result", duration_ms=-1)


@pytest.mark.parametrize(
    ("status", "evidence"),
    [
        ("done", ("Telegram API accepted the request",)),
        ("unverified", ()),
    ],
)
def test_action_results_include_safe_execution_metadata(status: str, evidence: tuple[str, ...]) -> None:
    async def scenario() -> None:
        registry = ActionRegistry()

        async def handler(_payload, _context):
            return ActionResult(
                True, "Operation accepted", {"approval_id": 9},
                status=status, verification_evidence=evidence,
            )

        registry._actions["example"] = handler
        context = _context()
        outcome = await registry.execute("example", '{"safe":true}', context)

        assert outcome.status == status
        assert outcome.tool_name == "example"
        assert len(outcome.operation_id) == 32
        assert outcome.duration_ms >= 0
        assert outcome.verification_evidence == evidence
        assert outcome.to_tool_result() == {
            "tool_name": "example",
            "operation_id": outcome.operation_id,
            "status": status,
            "duration_ms": outcome.duration_ms,
            "result": {"message": "Operation accepted", "data": {"approval_id": 9}},
            "diagnostic": "",
            "verification_evidence": list(evidence),
        }
        context.db.finish_action_execution.assert_awaited_once()
        recorded = json.loads(context.db.finish_action_execution.await_args.args[4])
        assert recorded["status"] == status
        assert context.db.finish_action_execution.await_args.args[3] == "complete"

    asyncio.run(scenario())


def test_failed_action_has_safe_diagnostic_and_never_claims_success() -> None:
    async def scenario() -> None:
        registry = ActionRegistry()

        async def fail(_payload, _context):
            raise RuntimeError("private token=do-not-expose")

        registry._actions["example"] = fail
        context = _context()
        outcome = await registry.execute("example", "{}", context)

        assert outcome.status == "failed"
        assert not outcome.ok
        assert outcome.diagnostic == "RuntimeError"
        assert "do-not-expose" not in json.dumps(outcome.to_tool_result())
        assert context.db.finish_action_execution.await_args.args[3] == "failed"

    asyncio.run(scenario())


def test_invalid_or_replayed_action_requests_fail_closed_with_operation_metadata() -> None:
    async def scenario() -> None:
        registry = ActionRegistry()
        context = _context()
        invalid = await registry.execute("send_poll", "{", context)
        assert invalid.status == "failed"
        assert invalid.diagnostic == "invalid_arguments"
        assert invalid.operation_id
        context.db.claim_action_execution.return_value = False
        replay = await registry.execute("send_poll", "{}", context)
        assert replay.status == "failed"
        assert replay.diagnostic == "duplicate_action"
        assert replay.operation_id != invalid.operation_id

    asyncio.run(scenario())


def test_approval_commands_also_return_operation_metadata() -> None:
    async def scenario() -> None:
        registry = ActionRegistry()
        context = SimpleNamespace(
            settings=SimpleNamespace(effective_admin_user_ids=frozenset({56})),
            db=SimpleNamespace(
                consume_approval=AsyncMock(return_value=None),
                deny_approval=AsyncMock(return_value=True),
            ),
        )

        rejected = await registry.approve(7, 56, context)
        denied = await registry.deny(8, 56, context)

        assert rejected.status == "failed"
        assert rejected.tool_name == "approve"
        assert rejected.operation_id
        assert denied.status == "done"
        assert denied.tool_name == "deny"
        assert denied.operation_id
        assert rejected.operation_id != denied.operation_id

    asyncio.run(scenario())
