"""Outbound risk control: per-group text/media rate limits and sensitive word filter."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import re
import time
from typing import Iterable


@dataclass(frozen=True)
class RiskDecision:
    allowed: bool
    reason: str = ""


class OutboundRiskGuard:
    """Enforces per-group sending rate limits and sensitive-content blocking."""

    def __init__(
        self,
        *,
        max_group_msgs_per_minute: int = 8,
        max_group_media_per_minute: int = 3,
        sensitive_words: Iterable[str] = (),
        sensitive_regex: Iterable[str] = (),
    ) -> None:
        self.max_group_msgs_per_minute = max(1, int(max_group_msgs_per_minute))
        self.max_group_media_per_minute = max(1, int(max_group_media_per_minute))
        self._words: tuple[str, ...] = tuple(w.strip().lower() for w in sensitive_words if str(w).strip())
        compiled: list[re.Pattern[str]] = []
        for pattern in sensitive_regex:
            raw = str(pattern or "").strip()
            if not raw:
                continue
            try:
                compiled.append(re.compile(raw, re.I))
            except re.error:
                continue
        self._regexes: tuple[re.Pattern[str], ...] = tuple(compiled)
        self._msg_times: dict[str, deque[float]] = {}
        self._media_times: dict[str, deque[float]] = {}

    def update_rules(
        self,
        *,
        max_group_msgs_per_minute: int,
        max_group_media_per_minute: int,
        sensitive_words: Iterable[str],
        sensitive_regex: Iterable[str],
    ) -> None:
        self.max_group_msgs_per_minute = max(1, int(max_group_msgs_per_minute))
        self.max_group_media_per_minute = max(1, int(max_group_media_per_minute))
        self._words = tuple(w.strip().lower() for w in sensitive_words if str(w).strip())
        compiled: list[re.Pattern[str]] = []
        for pattern in sensitive_regex:
            raw = str(pattern or "").strip()
            if not raw:
                continue
            try:
                compiled.append(re.compile(raw, re.I))
            except re.error:
                continue
        self._regexes = tuple(compiled)

    def check_outbound(
        self,
        *,
        stream_id: str,
        is_group: bool,
        text: str,
        has_media: bool,
        now_ts: float | None = None,
    ) -> RiskDecision:
        clean_lower = str(text or "").lower()
        if clean_lower:
            for word in self._words:
                if word in clean_lower:
                    return RiskDecision(allowed=False, reason="sensitive_word_blocked")
            for rx in self._regexes:
                if rx.search(text):
                    return RiskDecision(allowed=False, reason="sensitive_regex_blocked")

        if not is_group or not stream_id:
            return RiskDecision(allowed=True)

        now = float(now_ts if now_ts is not None else time.time())
        cutoff = now - 60.0

        msg_q = self._msg_times.setdefault(stream_id, deque())
        while msg_q and msg_q[0] < cutoff:
            msg_q.popleft()
        if len(msg_q) >= self.max_group_msgs_per_minute:
            return RiskDecision(allowed=False, reason="group_msg_rate_limit")

        if has_media:
            media_q = self._media_times.setdefault(stream_id, deque())
            while media_q and media_q[0] < cutoff:
                media_q.popleft()
            if len(media_q) >= self.max_group_media_per_minute:
                return RiskDecision(allowed=False, reason="group_media_rate_limit")
            media_q.append(now)

        msg_q.append(now)
        return RiskDecision(allowed=True)
