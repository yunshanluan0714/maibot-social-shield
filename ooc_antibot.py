"""OOC / repetition circuit breaker and anti-bot-loop guard."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import re
import time
from typing import Iterable

_OOC_PATTERNS = (
    re.compile(r"(?:作为一个|身为)(?:AI|人工智能|语言模型|大模型)", re.I),
    re.compile(r"(?:希望(?:这|以上内容)?能帮到您|很高兴为您服务|请问还有什么我可以帮您|非常理解您的感受)", re.I),
    re.compile(r"您说得太对了|感谢您的提问|祝您生活愉快", re.I),
    re.compile(r"首先[，,].{4,}其次[，,].{4,}(?:最后|综上所述)[，,]", re.S),
)


@dataclass(frozen=True)
class MonitorDecision:
    blocked: bool
    reason: str = ""
    hint: str = ""


def _char_bigrams(text: str) -> set[str]:
    clean = "".join(str(text or "").split()).lower()
    if len(clean) < 2:
        return {clean} if clean else set()
    return {clean[i : i + 2] for i in range(len(clean) - 1)}


def jaccard_similarity(a: str, b: str) -> float:
    sa, sb = _char_bigrams(a), _char_bigrams(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


class OOCAndAntiBotMonitor:
    """Detects OOC/robotic repetition and bot-vs-bot ping-pong loops."""

    def __init__(
        self,
        *,
        bot_pingpong_threshold: int = 4,
        bot_cooldown_minutes: int = 15,
        known_bot_ids: Iterable[str] = (),
    ) -> None:
        self.bot_pingpong_threshold = max(3, int(bot_pingpong_threshold))
        self.bot_cooldown_seconds = max(60.0, float(bot_cooldown_minutes) * 60.0)
        self.known_bot_ids = {str(x).strip() for x in known_bot_ids if str(x).strip()}
        self._bot_replies: dict[str, deque[str]] = {}
        self._turns: dict[str, deque[tuple[str, bool, float]]] = {}
        self._session_cooldown_until: dict[str, tuple[float, str]] = {}

    def update_config(
        self,
        *,
        bot_pingpong_threshold: int,
        bot_cooldown_minutes: int,
        known_bot_ids: Iterable[str],
    ) -> None:
        self.bot_pingpong_threshold = max(3, int(bot_pingpong_threshold))
        self.bot_cooldown_seconds = max(60.0, float(bot_cooldown_minutes) * 60.0)
        self.known_bot_ids = {str(x).strip() for x in known_bot_ids if str(x).strip()}

    def observe_incoming(self, session_id: str, user_id: str, now_ts: float | None = None) -> MonitorDecision:
        now = float(now_ts if now_ts is not None else time.time())
        uid = str(user_id or "").strip()
        if not session_id or not uid:
            return MonitorDecision(blocked=False)

        q = self._turns.setdefault(session_id, deque(maxlen=20))
        q.append((uid, False, now))

        if uid in self.known_bot_ids:
            if self._count_pingpong(q, uid) >= 2:
                self._session_cooldown_until[session_id] = (now + self.bot_cooldown_seconds, "known_bot_loop")
                return MonitorDecision(
                    blocked=True,
                    reason="known_bot_loop",
                    hint="[防Bot互搏熔断] 检测到正在和另一个机器人来回互回，立即 stop 进入冷却静默。",
                )

        if self._count_pingpong(q, uid) >= self.bot_pingpong_threshold:
            self._session_cooldown_until[session_id] = (now + self.bot_cooldown_seconds, "pingpong_bot_loop")
            return MonitorDecision(
                blocked=True,
                reason="pingpong_bot_loop",
                hint="[防Bot互搏熔断] 短时间内与同一对象连续快节奏互回过多轮，疑似触发双机对聊，立即 stop 强制静默冷却。",
            )

        return self.check_session_state(session_id, now_ts=now)

    def _count_pingpong(self, turns: deque[tuple[str, bool, float]], target_uid: str) -> int:
        pairs = 0
        saw_bot = False
        prev_ts: float | None = None
        for uid, is_bot, ts in reversed(turns):
            if prev_ts is not None and (prev_ts - ts) > 20.0:
                break
            prev_ts = ts
            if is_bot:
                saw_bot = True
            elif uid == target_uid:
                if saw_bot:
                    pairs += 1
                    saw_bot = False
                elif pairs == 0:
                    continue
                else:
                    break
            else:
                break
        return pairs

    def check_session_state(self, session_id: str, now_ts: float | None = None) -> MonitorDecision:
        now = float(now_ts if now_ts is not None else time.time())
        until, reason = self._session_cooldown_until.get(session_id, (0.0, ""))
        if now < until:
            return MonitorDecision(
                blocked=True,
                reason=reason,
                hint="[熔断冷却中] 当前会话正处于防 OOC/防互搏静默冷却期，请直接 stop 不要回复。",
            )
        return MonitorDecision(blocked=False)

    def check_outbound(self, session_id: str, text: str, now_ts: float | None = None) -> MonitorDecision:
        now = float(now_ts if now_ts is not None else time.time())
        state = self.check_session_state(session_id, now_ts=now)
        if state.blocked:
            return state

        clean = str(text or "").strip()
        if not clean:
            return MonitorDecision(blocked=False)

        if any(rx.search(clean) for rx in _OOC_PATTERNS):
            self._session_cooldown_until[session_id] = (now + 180.0, "ooc_cliche_blocked")
            return MonitorDecision(blocked=True, reason="ooc_cliche_blocked")

        history = self._bot_replies.get(session_id)
        if history and len(clean) >= 6:
            sim_hits = sum(1 for prev in list(history)[-3:] if jaccard_similarity(clean, prev) >= 0.72)
            if sim_hits >= 2:
                self._session_cooldown_until[session_id] = (now + 300.0, "ooc_repetition_loop")
                return MonitorDecision(blocked=True, reason="ooc_repetition_loop")

        return MonitorDecision(blocked=False)

    def record_outbound(self, session_id: str, text: str, now_ts: float | None = None) -> None:
        now = float(now_ts if now_ts is not None else time.time())
        if not session_id:
            return
        self._turns.setdefault(session_id, deque(maxlen=20)).append(("__bot__", True, now))
        clean = str(text or "").strip()
        if clean:
            self._bot_replies.setdefault(session_id, deque(maxlen=6)).append(clean)
