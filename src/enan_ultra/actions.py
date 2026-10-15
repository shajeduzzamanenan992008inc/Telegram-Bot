"""Validated, permission checked Telegram actions exposed to the model."""

from __future__ import annotations

import json
import io
import hashlib
import logging
from dataclasses import dataclass
from typing import Any

from telegram import InputFile, Update

from enan_ultra.config import Settings
from enan_ultra.models import ActionResult
from enan_ultra.services.code_sandbox import CodeSandbox
from enan_ultra.services.integrations import HomeAssistantClient, VMControl
from enan_ultra.security import is_admin

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ActionContext:
    update: Update
    settings: Settings
    media_enabled: bool
    router: Any
    db: Any
    home_assistant: HomeAssistantClient | None = None
    vm_control: VMControl | None = None
    local_only: bool = False


class ActionRegistry:
    def __init__(
        self, settings: Settings | None = None, *, home_assistant: HomeAssistantClient | None = None,
        vm_control: VMControl | None = None, sandbox: CodeSandbox | None = None,
    ) -> None:
        self.home_assistant = home_assistant
        self.vm_control = vm_control or VMControl()
        self.sandbox = sandbox or (CodeSandbox(settings) if settings else None)
        self._actions = {
            "send_poll": self._send_poll,
            "send_sticker": self._send_sticker,
            "generate_image": self._generate_image,
            "home_assistant_action": self._home_assistant_action,
            "restart_bot_service": self._restart_bot_service,
        }

    async def execute(self, name: str, arguments: str, context: ActionContext) -> ActionResult:
        handler = self._actions.get(name)
        if not handler:
            return ActionResult(False, "Unknown action")
        try:
            payload = json.loads(arguments)
        except (TypeError, json.JSONDecodeError):
            return ActionResult(False, "Invalid action JSON")
        if not isinstance(payload, dict):
            return ActionResult(False, "Action arguments must be an object")
        message = context.update.effective_message
        chat = context.update.effective_chat
        action_key = hashlib.sha256(
            json.dumps({"name": name, "arguments": payload}, sort_keys=True,
                         ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        claimed = False
        if message is not None and chat is not None and hasattr(context.db, "claim_action_execution"):
            try:
                claimed = await context.db.claim_action_execution(
                    chat.id, message.message_id, action_key, name,
                    context.update.effective_user.id if context.update.effective_user else chat.id,
                )
            except Exception as exc:
                logger.warning("Action idempotency guard failed (%s)", type(exc).__name__)
                return ActionResult(False, "Action safety check is unavailable; nothing was executed")
            if not claimed:
                return ActionResult(False, "This identical action was already handled for this Telegram message")
        outcome = ActionResult(False, "Action failed safely")
        try:
            outcome = await handler(payload, context)
        except Exception as exc:
            logger.warning("Telegram action %s failed (%s)", name, type(exc).__name__)
        finally:
            if claimed:
                try:
                    await context.db.finish_action_execution(
                        chat.id, message.message_id, action_key,
                        "complete" if outcome.ok else "failed", outcome.message,
                    )
                except Exception as exc:
                    logger.error("Action execution audit update failed (%s)", type(exc).__name__)
        return outcome

    async def approve(self, approval_id: int, approver_id: int, context: Any) -> ActionResult:
        record = await context.db.consume_approval(
            approval_id, approver_id, context.settings.effective_admin_user_ids,
        )
        if record is None:
            return ActionResult(False, "Approval is expired, already handled, or you are not its authorized approver")
        kind = str(record["kind"])
        payload = record["payload"]
        try:
            if kind == "home_assistant_action":
                if approver_id not in context.settings.effective_admin_user_ids or not self.home_assistant:
                    raise PermissionError("Admin approval or Home Assistant integration is unavailable")
                await self.home_assistant.call_service(
                    str(payload["service"]), list(payload["entity_ids"]), dict(payload.get("service_data") or {}),
                )
                await context.db.finish_approval(approval_id, "approved", "Home Assistant action completed")
                return ActionResult(True, "Home Assistant action completed")
            if kind == "vm_restart":
                if approver_id not in context.settings.effective_admin_user_ids:
                    raise PermissionError("Admin approval is required")
                self.vm_control.request_restart()
                await context.db.finish_approval(approval_id, "approved", "bot service restart scheduled")
                return ActionResult(True, "Bot service graceful restart scheduled")
            if kind == "code_test":
                if record["requester_id"] != approver_id or not self.sandbox:
                    raise PermissionError("The project owner must approve its test")
                import base64
                report = await self.sandbox.run_python_tests(base64.b64decode(payload["archive_b64"], validate=True))
                state = "approved" if report.exit_code == 0 and not report.timed_out else "failed"
                await context.db.finish_approval(approval_id, state, f"sandbox exit={report.exit_code}; timeout={report.timed_out}")
                output = report.output.strip() or "pytest produced no output."
                return ActionResult(report.exit_code == 0 and not report.timed_out,
                                    f"Sandbox result (exit {report.exit_code}{', timeout' if report.timed_out else ''}):\n{output[-3000:]}")
            raise ValueError("Unknown approval action")
        except Exception as exc:
            await context.db.finish_approval(approval_id, "failed", type(exc).__name__)
            logger.warning("Approved action %s failed (%s)", kind, type(exc).__name__)
            return ActionResult(False, "Approved action failed safely; check its integration configuration")

    async def deny(self, approval_id: int, approver_id: int, context: Any) -> ActionResult:
        denied = await context.db.deny_approval(
            approval_id, approver_id, context.settings.effective_admin_user_ids,
        )
        return ActionResult(denied, "Approval denied" if denied else "Approval is unavailable or you are not its authorized approver")

    async def _send_poll(self, args: dict[str, Any], context: ActionContext) -> ActionResult:
        question = args.get("question")
        options = args.get("options")
        if not isinstance(question, str) or not question.strip() or len(question) > 300:
            return ActionResult(False, "Poll question is invalid")
        if not isinstance(options, list) or not 2 <= len(options) <= 10:
            return ActionResult(False, "Poll needs 2–10 options")
        if any(not isinstance(item, str) or not item.strip() or len(item) > 100 for item in options):
            return ActionResult(False, "Poll options are invalid")
        if not context.media_enabled:
            return ActionResult(False, "User has disabled media")
        msg = context.update.effective_message
        if msg is None:
            return ActionResult(False, "No message context")
        await msg.reply_poll(question.strip(), [item.strip() for item in options], is_anonymous=True)
        return ActionResult(True, "Poll sent")

    async def _send_sticker(self, args: dict[str, Any], context: ActionContext) -> ActionResult:
        file_id = args.get("file_id")
        configured = context.settings.sticker_file_ids
        if not context.settings.sticker_enabled or not context.media_enabled:
            return ActionResult(False, "Stickers are disabled")
        if not isinstance(file_id, str) or file_id not in configured:
            return ActionResult(False, "Sticker is not in the configured allowlist")
        msg = context.update.effective_message
        if msg is None:
            return ActionResult(False, "No message context")
        await msg.reply_sticker(file_id)
        return ActionResult(True, "Sticker sent")

    async def _generate_image(self, args: dict[str, Any], context: ActionContext) -> ActionResult:
        if context.local_only:
            return ActionResult(False, "Image generation is disabled in local-only privacy mode")
        if not context.router.openrouter:
            return ActionResult(False, "Image generation requires a configured OpenRouter provider")
        prompt = args.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 2000:
            return ActionResult(False, "Image prompt is invalid")
        if not context.settings.image_generation_enabled or not context.media_enabled:
            return ActionResult(False, "Image generation is disabled")
        user = context.update.effective_user
        reserve = await context.router.estimate_image_cost()
        event_id, reason = await context.db.reserve_usage(
            user.id, context.settings.image_model or "image", reserve,
            context.settings.user_daily_request_limit, context.settings.max_daily_cost_usd,
            context.settings.user_monthly_request_limit, context.settings.user_monthly_cost_limit_usd,
        )
        if event_id is None:
            return ActionResult(False, f"{reason or 'usage'} limit blocks image generation")
        try:
            data, _ = await context.router.generate_image(prompt.strip())
            if not data or len(data) > context.settings.max_image_bytes:
                raise ValueError("Generated image exceeds configured limit")
            msg = context.update.effective_message
            await msg.reply_photo(InputFile(io.BytesIO(data), filename="generated-image.jpg"), caption="Your generated image 🎨")
            await context.db.finish_usage(event_id, context.settings.image_model or "image", {})
            return ActionResult(True, "Generated image sent")
        except Exception as exc:
            logger.warning("Image action failed (%s)", type(exc).__name__)
            return ActionResult(False, "Image generation failed")

    async def _home_assistant_action(self, args: dict[str, Any], context: ActionContext) -> ActionResult:
        user = context.update.effective_user
        if not user or not is_admin(context.settings, user.id):
            return ActionResult(False, "Home Assistant control is admin-only")
        if not self.home_assistant:
            return ActionResult(False, "Home Assistant integration is not configured")
        service = args.get("service")
        entities = args.get("entity_ids")
        data = args.get("service_data", {})
        if not isinstance(service, str) or service not in context.settings.ha_allowed_services:
            return ActionResult(False, "Home Assistant service is not allowlisted")
        if not isinstance(entities, list) or not entities or len(entities) > 10:
            return ActionResult(False, "Provide 1–10 allowlisted entity IDs")
        if any(not isinstance(entity, str) or entity not in context.settings.ha_allowed_entities for entity in entities):
            return ActionResult(False, "One or more entities are not allowlisted")
        if not isinstance(data, dict) or len(data) > 5:
            return ActionResult(False, "Home Assistant service data is invalid")
        safe_data: dict[str, Any] = {}
        for key, value in data.items():
            if key == "brightness":
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 255:
                    return ActionResult(False, "Brightness must be between 0 and 255")
                safe_data[key] = int(value)
            elif key == "temperature":
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not 5 <= value <= 40:
                    return ActionResult(False, "Temperature must be between 5 and 40")
                safe_data[key] = float(value)
            elif key in {"color_temp", "hvac_mode"} and isinstance(value, str) and len(value) <= 40:
                safe_data[key] = value
            else:
                return ActionResult(False, "Unsupported Home Assistant service data field")
        summary = f"Home Assistant {service} · {', '.join(entities)}"
        approval_id = await context.db.create_approval(
            user.id, "home_assistant_action", summary,
            {"service": service, "entity_ids": entities, "service_data": safe_data}, "admin",
        )
        return ActionResult(True, f"Action requires admin confirmation #{approval_id}", {"approval_id": approval_id})

    async def _restart_bot_service(self, args: dict[str, Any], context: ActionContext) -> ActionResult:
        user = context.update.effective_user
        if not user or not is_admin(context.settings, user.id):
            return ActionResult(False, "Bot service controls are admin-only")
        approval_id = await context.db.create_approval(
            user.id, "vm_restart", "Restart the Enan Ultra Telegram service", {}, "admin",
        )
        return ActionResult(True, f"Service restart requires admin confirmation #{approval_id}", {"approval_id": approval_id})
