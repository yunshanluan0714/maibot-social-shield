"""Cross-group & private-to-group privacy firewall helpers (zero host patch required)."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

FIREWALL_REMINDER = (
    "[隐私与跨群防火墙] 严禁将仅在私聊或其他群聊中得知的私人信息、八卦或内部黑话带入当前群聊。"
)


def lock_group_query_memory_calls(output_items: list[Any], *, is_group: bool) -> tuple[list[Any], bool]:
    """Ensure any query_memory call in a group chat forces respect_filter=True."""
    if not is_group or not isinstance(output_items, list):
        return output_items, False

    changed = False
    updated = deepcopy(output_items)
    for item in updated:
        if not isinstance(item, dict) or item.get("item_type") != "FunctionCallItem":
            continue
        call = item.get("tool_call")
        if not isinstance(call, dict):
            continue
        if str(call.get("func_name") or "").strip() != "query_memory":
            continue
        args = call.get("arguments")
        if not isinstance(args, dict):
            args = {}
            call["arguments"] = args
        if args.get("respect_filter") is not True:
            args["respect_filter"] = True
            changed = True
    return updated, changed
