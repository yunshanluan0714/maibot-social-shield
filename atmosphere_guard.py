"""Group atmosphere thermometer and sensitive-topic circuit breaker."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import re
import time
from typing import Iterable

_DEFAULT_SENSITIVE_RE = re.compile(
    r"(?:炸群|爆破群|开盒|人肉搜索|台独|疆独|藏独|法轮功|六四|8964|反共|政变)",
    re.I,
)
_ARGUMENT_RE = re.compile(
    r"(?:滚蛋|闭嘴|傻逼|脑残|智障|有病|死全家|操你妈|草泥马|sb|nmsl|急了|破防了|咬人|喷粪)",
    re.I,
)
_VENTING_RE = re.compile(
    r"(?:好崩溃|撑不住|难受死|失恋|分手了|被裁|失业|挂科|抑郁|焦虑|进医院|生病了|好想哭|太累了|活不下|心态崩)",
    re.I,
)
_SERIOUS_HELP_RE = re.compile(r"(?:报错|traceback|exception|怎么解决|求推荐|请问|攻略)", re.I)
_MEME_RE = re.compile(r"^(?:哈哈+|草+|乐+|6+|典+|绷不住|难绷|\+1|确实|\[表情包.*\]|\[图片.*\])$", re.I)


@dataclass(frozen=True)
class AtmosphereDecision:
    mood: str
    hint: str
    block_outbound: bool = False


class AtmosphereMonitor:
    """Detects sensitive spikes, arguments, emotional venting, and casual chaos per group."""

    def __init__(
        self,
        *,
        sensitive_cooldown_minutes: int = 10,
        extra_sensitive_words: Iterable[str] = (),
        extra_sensitive_regex: Iterable[str] = (),
    ) -> None:
        self.sensitive_cooldown_seconds = max(60, int(sensitive_cooldown_minutes) * 60)
        self._extra_words = tuple(w.strip().lower() for w in extra_sensitive_words if str(w).strip())
        compiled: list[re.Pattern[str]] = []
        for pat in extra_sensitive_regex:
            raw = str(pat or "").strip()
            if raw:
                try:
                    compiled.append(re.compile(raw, re.I))
                except re.error:
                    pass
        self._extra_regexes = tuple(compiled)
        self._windows: dict[str, deque[tuple[str, str, float]]] = {}
        self._silent_until: dict[str, float] = {}

    def update_rules(
        self,
        *,
        sensitive_cooldown_minutes: int,
        extra_sensitive_words: Iterable[str],
        extra_sensitive_regex: Iterable[str],
    ) -> None:
        self.sensitive_cooldown_seconds = max(60, int(sensitive_cooldown_minutes) * 60)
        self._extra_words = tuple(w.strip().lower() for w in extra_sensitive_words if str(w).strip())
        compiled: list[re.Pattern[str]] = []
        for pat in extra_sensitive_regex:
            raw = str(pat or "").strip()
            if raw:
                try:
                    compiled.append(re.compile(raw, re.I))
                except re.error:
                    pass
        self._extra_regexes = tuple(compiled)

    def _is_sensitive(self, text: str) -> bool:
        if not text:
            return False
        if _DEFAULT_SENSITIVE_RE.search(text):
            return True
        low = text.lower()
        if any(w in low for w in self._extra_words):
            return True
        return any(rx.search(text) for rx in self._extra_regexes)

    def observe(self, session_id: str, user_id: str, text: str, now_ts: float | None = None) -> AtmosphereDecision:
        now = float(now_ts if now_ts is not None else time.time())
        clean = str(text or "").strip()
        q = self._windows.setdefault(session_id, deque(maxlen=15))
        if clean:
            q.append((str(user_id), clean, now))

        if self._is_sensitive(clean):
            self._silent_until[session_id] = now + self.sensitive_cooldown_seconds
            return AtmosphereDecision(
                mood="HIGH_RISK_SENSITIVE",
                hint="[群气氛-高危避雷] 群内出现高危敏感或炸群内容，必须立刻装死不回，直接调用 stop。",
                block_outbound=True,
            )

        return self.evaluate(session_id, now_ts=now)

    def is_in_sensitive_silence(self, session_id: str, now_ts: float | None = None) -> bool:
        now = float(now_ts if now_ts is not None else time.time())
        return now < self._silent_until.get(session_id, 0.0)

    def evaluate(self, session_id: str, now_ts: float | None = None) -> AtmosphereDecision:
        now = float(now_ts if now_ts is not None else time.time())
        if now < self._silent_until.get(session_id, 0.0):
            return AtmosphereDecision(
                mood="HIGH_RISK_SENSITIVE",
                hint="[群气氛-高危避雷] 当前群处于敏感话题静默避险期，禁止发言或评论，直接 stop 装死。",
                block_outbound=True,
            )

        q = self._windows.get(session_id)
        if not q:
            return AtmosphereDecision(mood="NORMAL", hint="")

        recent = [(uid, txt) for uid, txt, ts in q if now - ts <= 300.0]
        if not recent:
            return AtmosphereDecision(mood="NORMAL", hint="")

        arg_users: set[str] = set()
        arg_hits = 0
        vent_hits = 0
        meme_hits = 0
        help_hits = 0

        for uid, txt in recent[-8:]:
            if self._is_sensitive(txt):
                self._silent_until[session_id] = now + self.sensitive_cooldown_seconds
                return AtmosphereDecision(
                    mood="HIGH_RISK_SENSITIVE",
                    hint="[群气氛-高危避雷] 近期包含高危敏感话题，直接 stop 装死。",
                    block_outbound=True,
                )
            if _ARGUMENT_RE.search(txt):
                arg_hits += 1
                arg_users.add(uid)
            if _VENTING_RE.search(txt):
                vent_hits += 1
            if _MEME_RE.match(txt):
                meme_hits += 1
            if _SERIOUS_HELP_RE.search(txt):
                help_hits += 1

        if arg_hits >= 3 and len(arg_users) >= 2:
            return AtmosphereDecision(
                mood="HEATED_ARGUMENT",
                hint="[群气氛-激烈对线] 群里正有人密集对线吵架。请闭嘴吃瓜，直接 stop 不要插话、不要拉偏架、不要说教。",
            )
        if vent_hits >= 1:
            return AtmosphereDecision(
                mood="EMOTIONAL_VENTING",
                hint="[群气氛-情绪倾诉] 有人正在认真倾诉低落情绪或遇到麻烦。收起玩笑与抖机灵，要么温和简短倾听安慰，要么安静不打扰。",
            )
        if help_hits >= 1:
            return AtmosphereDecision(mood="SERIOUS_HELP", hint="")
        if meme_hits >= 4:
            return AtmosphereDecision(mood="MEME_CHAOS", hint="")
        return AtmosphereDecision(mood="NORMAL", hint="")
