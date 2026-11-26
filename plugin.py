"""Main plugin wiring for github.yunshanluan0714.social-shield v2.0.1."""

from __future__ import annotations

from datetime import datetime
import re
import time
from typing import Any
from uuid import uuid4

from maibot_sdk import HookHandler, MaiBotPlugin
from maibot_sdk.types import ErrorPolicy, HookMode, HookOrder

from .config import SocialShieldConfig
from .ooc_antibot import OOCAndAntiBotMonitor, is_system_notification_text
from .privacy_firewall import FIREWALL_REMINDER, lock_group_query_memory_calls
from .risk_guard import OutboundRiskGuard

_HEADER_RE = re.compile(r"^\s*<message\b([^>]*)>", re.S)


def _extract_components(msg: dict[str, Any]) -> list[dict[str, Any]]:
    raw = msg.get("raw_message")
    if isinstance(raw, list):
        return [c for c in raw if isinstance(c, dict)]
    if isinstance(raw, dict):
        comps = raw.get("components")
        if isinstance(comps, list):
            return [c for c in comps if isinstance(c, dict)]
    return []


def _build_reminder_item(text: str) -> dict[str, Any]:
    return {
        "item_type": "UserMessageItem",
        "meta": {"item_id": uuid4().hex, "logical_turn_id": None, "timestamp": datetime.now().isoformat()},
        "parts": [{"type": "text", "text": text}],
    }


def _inject_reminder(items: list[dict[str, Any]], text: str) -> list[dict[str, Any]]:
    updated = list(items)
    pos = len(updated)
    if updated and updated[-1].get("item_type") == "UserMessageItem":
        texts = [str(p.get("text") or "") for p in updated[-1].get("parts") or [] if isinstance(p, dict)]
        if texts and _HEADER_RE.match(texts[0]) is None:
            pos -= 1
    updated.insert(pos, _build_reminder_item(text))
    return updated


class SocialShieldPlugin(MaiBotPlugin):
    """OOC/防Bot互搏熔断、单群出站风控与跨群记忆隔离防火墙插件。"""

    config_model = SocialShieldConfig

    def __init__(self) -> None:
        super().__init__()
        self._running = False
        self._ooc_antibot = OOCAndAntiBotMonitor()
        self._risk_guard = OutboundRiskGuard()
        self._bot_account = ""
        self._session_to_group: dict[str, str] = {}

    async def on_load(self) -> None:
        self._apply_config_to_engines()
        await self._refresh_bot_identity()
        await self._warm_stream_mappings()
        self._running = True
        self.ctx.logger.info("OOC 熔断、防Bot互搏与单群风控防火墙 v2.0.1 已加载")

    async def on_unload(self) -> None:
        self._running = False

    async def on_config_update(self, scope: str, config_data: dict[str, Any], version: str) -> None:
        del scope, config_data, version
        self._apply_config_to_engines()
        await self._refresh_bot_identity()

    def _apply_config_to_engines(self) -> None:
        cfg = self.config
        self._ooc_antibot.update_config(
            bot_pingpong_threshold=cfg.ooc_antibot.bot_pingpong_threshold,
            bot_cooldown_minutes=cfg.ooc_antibot.bot_cooldown_minutes,
            known_bot_ids=cfg.ooc_antibot.known_bot_ids,
        )
        self._risk_guard.update_rules(
            max_group_msgs_per_minute=cfg.risk_control.max_group_msgs_per_minute,
            max_group_media_per_minute=cfg.risk_control.max_group_media_per_minute,
            sensitive_words=cfg.risk_control.sensitive_words,
            sensitive_regex=cfg.risk_control.sensitive_regex,
        )

    async def _refresh_bot_identity(self) -> None:
        try:
            qq = await self.ctx.config.get("bot.qq_account", "")
            if isinstance(qq, str):
                self._bot_account = qq.strip()
        except Exception:
            pass

    async def _warm_stream_mappings(self) -> None:
        try:
            getter = getattr(self.ctx.chat, "get_all_streams", None) or getattr(self.ctx.chat, "get_group_streams", None)
            if getter is None:
                return
            raw = await getter(platform="qq")
            streams = raw.get("streams", []) if isinstance(raw, dict) else (raw if isinstance(raw, list) else [])
            if not isinstance(streams, list):
                return
            for st in streams:
                if not isinstance(st, dict):
                    continue
                sid = str(st.get("session_id") or st.get("stream_id") or "").strip()
                if not sid:
                    continue
                gid = str(st.get("group_id") or "").strip()
                if st.get("is_group_session") or st.get("chat_type") == "group" or gid:
                    if gid:
                        self._session_to_group[sid] = gid
                elif st.get("is_group_session") is False or st.get("chat_type") == "private":
                    self._session_to_group.setdefault(sid, "")
        except Exception:
            pass

    def _resolve_group_id(self, session_id: str) -> str:
        sid = str(session_id or "").strip()
        if sid in self._session_to_group:
            return self._session_to_group[sid]
        if ":group:" in sid:
            return sid.split(":group:")[-1]
        return ""

    def _record_message_scope(self, session_id: str, info: dict[str, Any]) -> None:
        if not session_id:
            return
        group = info.get("group_info")
        if isinstance(group, dict) and group.get("group_id"):
            self._session_to_group[session_id] = str(group["group_id"]).strip()
        elif "group_info" in info and group is None and isinstance(info.get("user_info"), dict):
            self._session_to_group.setdefault(session_id, "")

    @HookHandler(
        "chat.receive.after_process",
        name="social_shield_receive_observe",
        description="维护会话映射并观测防 Bot 互搏对话轨迹",
        mode=HookMode.OBSERVE,
        order=HookOrder.NORMAL,
        timeout_ms=2000,
        error_policy=ErrorPolicy.SKIP,
    )
    async def on_receive_after_process(self, **kwargs: Any) -> None:
        if not self._running or not self.config.plugin.enabled:
            return
        msg = kwargs.get("message")
        if not isinstance(msg, dict):
            return

        msg_info = msg.get("message_info") if isinstance(msg.get("message_info"), dict) else {}
        user_info = msg_info.get("user_info") if isinstance(msg_info.get("user_info"), dict) else {}
        group_info = msg_info.get("group_info") if isinstance(msg_info.get("group_info"), dict) else None
        user_id = str(user_info.get("user_id") or "").strip()
        group_id = str(group_info.get("group_id") or "").strip() if group_info else ""
        session_id = str(msg.get("session_id") or "").strip()
        platform = str(msg.get("platform") or msg_info.get("platform") or "qq").strip()
        if not session_id and platform:
            session_id = f"{platform}:{'group:' + group_id if group_id else 'user:' + user_id}"

        if self._bot_account and user_id == self._bot_account:
            return

        self._record_message_scope(session_id, msg_info)

        if group_id and self.config.ooc_antibot.enabled and user_id:
            self._ooc_antibot.observe_incoming(session_id, user_id, now_ts=time.time())

    @HookHandler(
        "maisaka.planner.before_request",
        name="social_shield_planner_inject",
        description="仅在熔断冷却期或显式开启防火墙提醒时注入最小提示（常态零注入）",
        mode=HookMode.BLOCKING,
        order=HookOrder.NORMAL,
        timeout_ms=1500,
        error_policy=ErrorPolicy.SKIP,
    )
    async def on_planner_before_request(self, **kwargs: Any) -> dict[str, Any]:
        unchanged = {"action": "continue", "modified_kwargs": kwargs}
        if not self._running or not self.config.plugin.enabled:
            return unchanged
        items = kwargs.get("items")
        if not isinstance(items, list):
            return unchanged

        session_id = str(kwargs.get("session_id") or "").strip()
        is_group = bool(self._resolve_group_id(session_id))
        hints: list[str] = []

        if self.config.ooc_antibot.enabled:
            oob = self._ooc_antibot.check_session_state(session_id)
            if oob.hint:
                hints.append(oob.hint)

        if is_group and self.config.privacy_firewall.enabled and self.config.privacy_firewall.inject_prompt_reminder:
            hints.append(FIREWALL_REMINDER)

        if not hints:
            return unchanged

        updated = dict(kwargs)
        updated["items"] = _inject_reminder(items, "\n".join(hints))
        return {"action": "continue", "modified_kwargs": updated}

    @HookHandler(
        "maisaka.planner.after_response",
        name="social_shield_memory_firewall",
        description="群聊场景下强制锁定 query_memory 的 respect_filter=True，防止检索私聊或他群记忆",
        mode=HookMode.BLOCKING,
        order=HookOrder.EARLY,
        timeout_ms=1500,
        error_policy=ErrorPolicy.SKIP,
    )
    async def on_planner_after_response(self, **kwargs: Any) -> dict[str, Any]:
        unchanged = {"action": "continue", "modified_kwargs": kwargs}
        if (
            not self._running
            or not self.config.plugin.enabled
            or not self.config.privacy_firewall.enabled
            or not self.config.privacy_firewall.lock_group_memory_filter
        ):
            return unchanged

        session_id = str(kwargs.get("session_id") or "").strip()
        known_private = (self._session_to_group[session_id] == "" if session_id in self._session_to_group
                         else ":user:" in session_id and ":group:" not in session_id)
        output_items = kwargs.get("output_items")
        if known_private or not isinstance(output_items, list):
            return unchanged

        locked, changed = lock_group_query_memory_calls(output_items, is_group=True)
        if not changed:
            return unchanged

        updated = dict(kwargs)
        updated["output_items"] = locked
        return {"action": "continue", "modified_kwargs": updated}

    @HookHandler(
        "send_service.before_send",
        name="social_shield_risk_before_send",
        description="执行 OOC/Bot 互搏熔断以及单群出站风控限流与敏感词拦截",
        mode=HookMode.BLOCKING,
        order=HookOrder.EARLY,
        timeout_ms=2000,
        error_policy=ErrorPolicy.SKIP,
    )
    async def on_before_send(self, **kwargs: Any) -> dict[str, Any]:
        unchanged = {"action": "continue", "modified_kwargs": kwargs}
        if not self._running or not self.config.plugin.enabled:
            return unchanged

        msg = kwargs.get("message")
        if not isinstance(msg, dict):
            return unchanged

        msg_info = msg.get("message_info") if isinstance(msg.get("message_info"), dict) else {}
        group_info = msg_info.get("group_info") if isinstance(msg_info.get("group_info"), dict) else None
        is_group = group_info is not None
        stream_id = str(msg.get("session_id") or (group_info.get("group_id") if group_info else "") or "").strip()
        self._record_message_scope(stream_id, msg_info)
        text = str(msg.get("processed_plain_text") or "")

        if is_system_notification_text(text):
            return unchanged

        if self.config.ooc_antibot.enabled:
            oob = self._ooc_antibot.check_outbound(stream_id, text)
            if oob.blocked:
                self.ctx.logger.warning("OOC/互搏熔断拦截出站: stream=%s reason=%s", stream_id, oob.reason)
                return {"action": "abort"}

        if self.config.risk_control.enabled:
            components = _extract_components(msg)
            has_media = any(
                any(k in str(c.get("type") or "").lower() for k in ("image", "emoji", "voice", "video", "record"))
                for c in components
            )
            decision = self._risk_guard.check_outbound(
                stream_id=stream_id,
                is_group=is_group,
                text=text,
                has_media=has_media,
            )
            if not decision.allowed:
                self.ctx.logger.warning("风控拦截出站消息: stream=%s reason=%s", stream_id, decision.reason)
                return {"action": "abort"}

        return unchanged

    @HookHandler(
        "send_service.after_send",
        name="social_shield_after_send",
        description="记录机器人出站文本轨迹用于 OOC 与 Bot 互搏监测",
        mode=HookMode.OBSERVE,
        order=HookOrder.NORMAL,
        timeout_ms=1500,
        error_policy=ErrorPolicy.SKIP,
    )
    async def on_after_send(self, **kwargs: Any) -> None:
        if not self._running or not self.config.plugin.enabled or not kwargs.get("sent", True):
            return
        msg = kwargs.get("message")
        if not isinstance(msg, dict):
            return
        session_id = str(msg.get("session_id") or "").strip()
        text = str(msg.get("processed_plain_text") or "").strip()
        if session_id and self.config.ooc_antibot.enabled and not is_system_notification_text(text):
            self._ooc_antibot.record_outbound(session_id, text)


def create_plugin() -> SocialShieldPlugin:
    return SocialShieldPlugin()
