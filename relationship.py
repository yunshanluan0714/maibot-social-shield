"""Per-(scope, user_id) evolving relationship score and troll cold-shoulder state."""

from __future__ import annotations

import json
from pathlib import Path
import re
import time
from typing import Any

_PROVOKE_RE = re.compile(
    r"(?:滚|闭嘴|傻逼|脑残|智障|废物|垃圾|有病|发什么癫|弱智|人工智障|真蠢|烦死|恶心|别在这|少管闲事)",
    re.I,
)
_FRIENDLY_RE = re.compile(
    r"(?:谢谢|多谢|辛苦|牛啊|厉害|好用|喜欢|爱你|抱抱|贴贴|真棒|可爱|早安|晚安|帮大忙)",
    re.I,
)


class RelationshipStore:
    """Tracks per-(scope, user_id) affinity score and recent attitude signals."""

    def __init__(
        self,
        data_dir: Path | str,
        *,
        troll_threshold: int = -25,
        close_friend_threshold: int = 35,
    ) -> None:
        self._dir = Path(data_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._path = self._dir / "relationships.json"
        self.troll_threshold = int(troll_threshold)
        self.close_friend_threshold = int(close_friend_threshold)
        self._scores: dict[str, dict[str, Any]] = {}
        self._load()

    def _key(self, scope: str, user_id: str) -> str:
        return f"{scope}:{user_id}"

    def observe_message(
        self,
        *,
        scope: str,
        user_id: str,
        display_name: str,
        text: str,
        mentioned_bot: bool,
        now_ts: float | None = None,
    ) -> int:
        clean_uid = str(user_id or "").strip()
        if not clean_uid:
            return 0
        now = float(now_ts if now_ts is not None else time.time())
        key = self._key(scope, clean_uid)
        entry = self._scores.setdefault(
            key,
            {
                "scope": scope,
                "user_id": clean_uid,
                "name": display_name or clean_uid,
                "score": 0,
                "troll_hits": 0,
                "updated_at": now,
            },
        )
        if display_name:
            entry["name"] = display_name[:32]

        hours_elapsed = max(0.0, (now - float(entry.get("updated_at", now))) / 3600.0)
        if hours_elapsed >= 12.0:
            step = int(hours_elapsed // 12)
            cur = int(entry.get("score", 0))
            if cur > 0:
                entry["score"] = max(0, cur - step)
            elif cur < 0:
                entry["score"] = min(0, cur + step)

        delta = 0
        clean = str(text or "").strip()
        if clean and _PROVOKE_RE.search(clean):
            delta -= 12 if mentioned_bot else 6
            entry["troll_hits"] = int(entry.get("troll_hits", 0)) + 1
        elif clean and _FRIENDLY_RE.search(clean):
            delta += 4 if mentioned_bot else 2
        elif mentioned_bot and clean:
            delta += 1

        entry["score"] = max(-100, min(100, int(entry.get("score", 0)) + delta))
        entry["updated_at"] = now
        if delta != 0 or len(self._scores) <= 200:
            self._save()
        return int(entry["score"])

    def get_score(self, scope: str, user_id: str) -> int:
        entry = self._scores.get(self._key(scope, str(user_id or "").strip()))
        return int(entry.get("score", 0)) if entry else 0

    def build_hint(self, *, scope: str, user_id: str, display_name: str = "") -> str:
        score = self.get_score(scope, user_id)
        label = display_name or user_id
        if score <= self.troll_threshold:
            return (
                f"[互动关系] {label} 近期多次恶意挑衅或抬杠（关系分 {score}），"
                "请做冷处理：尽量不理会（stop），必要时只回一个「？」或敷衍一句晾着，别跟杠精纠缠讲道理。"
            )
        if score >= self.close_friend_threshold:
            return f"[互动关系] {label} 是熟络老群友（关系分 {score}），语气可以更自然随意、允许轻度互怼开玩笑。"
        if score <= -10:
            return f"[互动关系] {label} 态度偏冲（关系分 {score}），保持客气疏离，点到为止即可。"
        return ""

    def _load(self) -> None:
        if not self._path.is_file():
            return
        try:
            raw = json.loads(self._path.read_text("utf-8"))
            if isinstance(raw, dict):
                self._scores = raw
        except Exception:
            self._scores = {}

    def _save(self) -> None:
        if len(self._scores) > 1000:
            ordered = sorted(
                self._scores.items(),
                key=lambda kv: float(kv[1].get("updated_at", 0.0)),
                reverse=True,
            )[:800]
            self._scores = dict(ordered)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._scores, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(self._path)
