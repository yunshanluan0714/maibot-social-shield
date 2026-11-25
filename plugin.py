"""Main plugin wiring for github.yunshanluan0714.social-shield."""

from __future__ import annotations

from datetime import datetime
import re
import time
from typing import Any
from uuid import uuid4

from maibot_sdk import HookHandler, MaiBotPlugin
from maibot_sdk.types import ErrorPolicy, HookMode, HookOrder

from .atmosphere_guard import AtmosphereMonitor
from .config import SocialShieldConfig
from .defense_guard import evaluate_defense
from .ooc_antibot import OOCAndAntiBotMonitor
from .privacy_firewall import FIREWALL_REMINDER, lock_group_query_memory_calls
from .relationship import RelationshipStore
from .risk_guard import OutboundRiskGuard

_ATTR_RE = re.compile(r'(\w+)="([^"]*)"')
_HEADER_RE = re.compile(r"^\s*<message\b([^>]*)>", re.S)


def _extract_last_user_from_items(items: list[dict[str, Any]]) -> tuple[str, str, str]:
    for item in reversed(items):
        if not isinstance(item, dict) or item.get("item_type") != "UserMessageItem":
            continue
        texts = [
            str(p.get("text") or "")
            for p in item.get("parts") or []
            if isinstance(p, dict) and p.get("type") == "text"
        ]
        if not texts:
            continue
        match = _HEADER_RE.match(texts[0])
        if match is None:
            continue
        attrs = dict(_ATTR_RE.findall(match.group(1)))
        if attrs.get("is_self_message", "").lower() == "true":
            continue
        body = "".join([texts[0][match.end() :], *texts[1:]]).strip()
        return attrs.get("user", "").strip(), body, attrs.get("user_id", "").strip()
    return "", "", ""


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
    config_model = SocialShieldConfig

    def __init__(self) -> None:
        super().__init__()
        self._running = False
        self._relation_store: RelationshipStore | None = None
        self._atmosphere = AtmosphereMonitor()
        self._ooc_antibot = OOCAndAntiBotMonitor()
        self._risk_guard = OutboundRiskGuard()
        self._bot_account = ""
        self._bot_nickname = ""
        self._bot_aliases: list[str] = []
        self._recent_senders: dict[str, tuple[str, str]] = {}

    async def on_load(self) -> None:
        self._relation_store = RelationshipStore(
            self.ctx.paths.data_dir,
            troll_threshold=self.config.relationship.troll_threshold,
            close_friend_threshold=self.config.relationship.close_friend_threshold,
        )
        self._apply_config_to_engines()
        await self._refresh_bot_identity()
        self._running = True
        self.ctx.logger.info("群气氛避雷、反图灵、隐私防火墙与风控熔断插件已加载")

    async def on_unload(self) -> None:
        self._running = False

    async def on_config_update(self, scope: str, config_data: dict[str, Any], version: str) -> None:
        self._apply_config_to_engines()
        await self._refresh_bot_identity()

    def _apply_config_to_engines(self) -> None:
        cfg = self.config
        self._atmosphere.update_rules(
            sensitive_cooldown_minutes=cfg.atmosphere.sensitive_cooldown_minutes,
            extra_sensitive_words=cfg.atmosphere.extra_sensitive_words,
            extra_sensitive_regex=cfg.atmosphere.extra_sensitive_regex,
        )
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
        if self._relation_store is not None:
            self._relation_store.troll_threshold = cfg.relationship.troll_threshold
            self._relation_store.close_friend_threshold = cfg.relationship.close_friend_threshold

    async def _refresh_bot_identity(self) -> None:
        try:
            qq = await self.ctx.config.get("bot.qq_account", "")
            nick = await self.ctx.config.get("bot.nickname", "")
            aliases = await self.ctx.config.get("bot.alias_names", [])
            if isinstance(qq, str):
                self._bot_account = qq.strip()
            if isinstance(nick, str):
                self._bot_nickname = nick.strip()
            if isinstance(aliases, list):
                self._bot_aliases = [str(a).strip() for a in aliases if str(a).strip()]
        except Exception:
            pass

    @HookHandler(
        "chat.receive.after_process",
        name="social_shield_receive_observe",
        description="观察群气氛场域、动态关系分与防Bot互搏轨迹",
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
        sender_name = str(user_info.get("user_cardname") or user_info.get("user_nickname") or user_id).strip()
        group_id = str(group_info.get("group_id") or "").strip() if group_info else ""
        session_id = str(msg.get("session_id") or "").strip()
        platform = str(msg_info.get("platform") or "qq").strip()
        if not session_id and platform:
            session_id = f"{platform}:{'group:' + group_id if group_id else 'user:' + user_id}"

        if self._bot_account and user_id == self._bot_account:
            return

        plain_text = str(msg.get("processed_plain_text") or "").strip()
        now_ts = time.time()
        if session_id and user_id:
            self._recent_senders[session_id] = (user_id, sender_name)

        if group_id and self.config.atmosphere.enabled:
            self._atmosphere.observe(session_id, user_id, plain_text, now_ts=now_ts)

        if group_id and self.config.ooc_antibot.enabled and user_id:
            self._ooc_antibot.observe_incoming(session_id, user_id, now_ts=now_ts)

        if self.config.relationship.enabled and self._relation_store is not None and user_id:
            aliases = [x for x in [self._bot_nickname, *self._bot_aliases] if x]
            mentioned = any(a.lower() in plain_text.lower() for a in aliases)
            scope = f"group:{group_id}" if group_id else "private"
            self._relation_store.observe_message(
                scope=scope,
                user_id=user_id,
                display_name=sender_name,
                text=plain_text,
                mentioned_bot=mentioned,
                now_ts=now_ts,
            )

    @HookHandler(
        "maisaka.planner.before_request",
        name="social_shield_planner_inject",
        description="按需注入群气氛避雷、动态关系、反图灵降智、跨群隐私防火墙及防互搏冷却提醒",
        mode=HookMode.BLOCKING,
        order=HookOrder.NORMAL,
        timeout_ms=2000,
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
        is_group = ":group:" in session_id
        group_id = session_id.split(":group:")[-1] if is_group else ""
        _, latest_text, latest_uid = _extract_last_user_from_items(items)
        if not latest_uid and session_id in self._recent_senders:
            latest_uid, _ = self._recent_senders[session_id]

        hints: list[str] = []
        if is_group and self.config.atmosphere.enabled:
            atm = self._atmosphere.evaluate(session_id)
            if atm.hint:
                hints.append(atm.hint)

        if self.config.ooc_antibot.enabled:
            oob = self._ooc_antibot.check_session_state(session_id)
            if oob.hint:
                hints.append(oob.hint)

        if is_group and self.config.privacy_firewall.enabled:
            hints.append(FIREWALL_REMINDER)

        if self.config.relationship.enabled and self._relation_store is not None and latest_uid:
            scope = f"group:{group_id}" if group_id else "private"
            r_hint = self._relation_store.build_hint(scope=scope, user_id=latest_uid)
            if r_hint:
                hints.append(r_hint)

        if self.config.defense.enabled and latest_text:
            _, d_hint = evaluate_defense(
                latest_text,
                anti_injection=self.config.defense.anti_injection,
                capability_downgrade=self.config.defense.capability_downgrade,
                bypass_keywords=self.config.defense.bypass_keywords,
            )
            if d_hint:
                hints.append(d_hint)

        if not hints:
            return unchanged

        updated = dict(kwargs)
        updated["items"] = _inject_reminder(items, "\n".join(hints))
        return {"action": "continue", "modified_kwargs": updated}

    @HookHandler(
        "maisaka.planner.after_response",
        name="social_shield_memory_firewall",
        description="群聊场景下强制锁定 query_memory 的 respect_filter=True，防止检索私聊记忆",
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
        is_group = ":group:" in session_id
        output_items = kwargs.get("output_items")
        if not is_group or not isinstance(output_items, list):
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
        description="执行高危气氛装死拦截、OOC/Bot互搏熔断以及单群风控频控",
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
        text = str(msg.get("processed_plain_text") or "")

        if is_group and self.config.atmosphere.enabled and self._atmosphere.is_in_sensitive_silence(stream_id):
            self.ctx.logger.warning("气氛避雷拦截出站消息: stream=%s", stream_id)
            return {"action": "abort"}

        if self.config.ooc_antibot.enabled:
            oob = self._ooc_antibot.check_outbound(stream_id, text)
            if oob.blocked:
                self.ctx.logger.warning("OOC/互搏熔断拦截出站: stream=%s reason=%s", stream_id, oob.reason)
                return {"action": "abort"}

        if self.config.risk_control.enabled:
            raw_msg = msg.get("raw_message") if isinstance(msg.get("raw_message"), dict) else {}
            components = raw_msg.get("components") if isinstance(raw_msg.get("components"), list) else []
            has_media = any(
                isinstance(c, dict) and any(k in str(c.get("type") or "").lower() for k in ("image", "emoji", "voice", "video"))
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
        if session_id and self.config.ooc_antibot.enabled:
            self._ooc_antibot.record_outbound(session_id, text)


def create_plugin() -> SocialShieldPlugin:
    return SocialShieldPlugin()
