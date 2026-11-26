"""Unit tests for maibot-social-shield v2.0.0 pure logic modules."""

import unittest

from ooc_antibot import OOCAndAntiBotMonitor, is_system_notification_text
from privacy_firewall import lock_group_query_memory_calls
from risk_guard import OutboundRiskGuard


class SocialShieldTests(unittest.TestCase):
    def test_ooc_antibot_and_system_notice_exemption(self) -> None:
        oob = OOCAndAntiBotMonitor(bot_pingpong_threshold=3, bot_cooldown_minutes=15)
        t = 1000.0
        for _ in range(3):
            oob.observe_incoming("g2", "bot2", now_ts=t)
            oob.record_outbound("g2", "ok", now_ts=t + 2)
            t += 4
        self.assertEqual(oob.observe_incoming("g2", "bot2", now_ts=t).reason, "pingpong_bot_loop")
        self.assertEqual(oob.check_outbound("g3", "作为一个AI，希望对您有所帮助").reason, "ooc_cliche_blocked")

        self.assertTrue(is_system_notification_text("【防撤回提醒】 来源：群聊 某群 内容：test"))
        for idx in range(4):
            note = f"【防撤回提醒】 来源：群聊 某群 (1000) 发送者：某人 时间：17:49:0{idx} 内容：第{idx}句"
            self.assertFalse(oob.check_outbound("p_md5", note, now_ts=10.0 + idx).blocked)
            oob.record_outbound("p_md5", note, now_ts=10.0 + idx)

    def test_outbound_risk_guard(self) -> None:
        guard = OutboundRiskGuard(
            max_group_msgs_per_minute=2,
            max_group_media_per_minute=1,
            sensitive_words=["敏感词"],
            sensitive_regex=[r"违禁\d+"],
        )
        self.assertFalse(guard.check_outbound(stream_id="g1", is_group=True, text="这是敏感词", has_media=False).allowed)
        self.assertFalse(guard.check_outbound(stream_id="g1", is_group=True, text="触发违禁99", has_media=False).allowed)
        self.assertTrue(guard.check_outbound(stream_id="g1", is_group=True, text="a", has_media=True, now_ts=10.0).allowed)
        self.assertFalse(guard.check_outbound(stream_id="g1", is_group=True, text="b", has_media=True, now_ts=12.0).allowed)
        self.assertTrue(guard.check_outbound(stream_id="g1", is_group=True, text="c", has_media=False, now_ts=13.0).allowed)
        self.assertFalse(guard.check_outbound(stream_id="g1", is_group=True, text="d", has_media=False, now_ts=14.0).allowed)

    def test_privacy_firewall_locks_query_memory(self) -> None:
        items = [
            {
                "item_type": "FunctionCallItem",
                "tool_call": {"func_name": "query_memory", "arguments": {"query": "秘密", "respect_filter": False}},
            }
        ]
        locked, changed = lock_group_query_memory_calls(items, is_group=True)
        self.assertTrue(changed)
        self.assertTrue(locked[0]["tool_call"]["arguments"]["respect_filter"])

        untouched, changed_priv = lock_group_query_memory_calls(items, is_group=False)
        self.assertFalse(changed_priv)
        self.assertFalse(untouched[0]["tool_call"]["arguments"]["respect_filter"])


if __name__ == "__main__":
    unittest.main()
