"""Unit tests for maibot-social-shield v2.0.0 pure logic modules."""

import importlib
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock

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

    def test_v148_snapshot_locks_args_without_overwriting_query_or_original(self):
        item = {"item_type": "FunctionCallItem", "tool_call": {
            "call_id": "call-1", "func_name": "query_memory",
            "args": {"query": "群内约定", "respect_filter": False}, "extra_content": {}}}
        locked, changed = lock_group_query_memory_calls([item], is_group=True)
        self.assertTrue(changed)
        self.assertEqual(locked[0]["tool_call"]["args"], {"query": "群内约定", "respect_filter": True})
        self.assertFalse(item["tool_call"]["args"]["respect_filter"])
        self.assertNotIn("arguments", locked[0]["tool_call"])


@unittest.skipUnless(importlib.util.find_spec("maibot_sdk"), "requires installed MaiBot SDK")
class PlannerHookTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        package = Path(__file__).resolve().parent
        sys.path.insert(0, str(package.parent))
        try:
            self.plugin = importlib.import_module(package.name + ".plugin").create_plugin()
        finally:
            sys.path.pop(0)
        self.plugin.set_plugin_config(self.plugin.build_default_config())
        streams = [{"session_id": "warm-group", "group_id": "group-a", "is_group_session": True},
                   {"session_id": "warm-private", "group_id": None, "is_group_session": False}]
        self.plugin._set_context(SimpleNamespace(logger=Mock(), config=SimpleNamespace(get=AsyncMock(return_value="")),
            chat=SimpleNamespace(get_all_streams=AsyncMock(return_value={"success": True, "streams": streams}))))
        await self.plugin.on_load()
        self.addAsyncCleanup(self.plugin.on_unload)

    async def assert_filter(self, session, expected):
        args = {"query": "约定", "respect_filter": False}
        item = {"item_type": "FunctionCallItem",
                "meta": {"item_id": "item-1", "logical_turn_id": "turn-1", "timestamp": "2026-11-26T08:00:00"},
                "tool_call": {"call_id": "call-1", "func_name": "query_memory", "args": args, "extra_content": {}}}
        result = await self.plugin.on_planner_after_response(session_id=session, output_items=[item],
                                                             item_schema_version=1)
        new_call = result["modified_kwargs"]["output_items"][0]["tool_call"]
        self.assertEqual(new_call["args"], {"query": "约定", "respect_filter": expected})
        self.assertFalse(args["respect_filter"])

    async def test_warm_and_unknown_session_scope(self):
        await self.assert_filter("warm-group", True)
        await self.assert_filter("warm-private", False)
        await self.assert_filter("as-yet-unseen", True)

    async def test_receive_establishes_scope_and_incomplete_send_cannot_erase_it(self):
        for sid, group in (("new-group", {"group_id": "group-b"}), ("new-private", None)):
            await self.plugin.on_receive_after_process(message={
                "platform": "qq", "session_id": sid, "message_info": {
                    "user_info": {"user_id": "member-a"}, "group_info": group}})
        await self.plugin.on_before_send(message={"session_id": "new-group", "processed_plain_text": ""})
        await self.assert_filter("new-group", True)
        await self.assert_filter("new-private", False)


if __name__ == "__main__":
    unittest.main()
