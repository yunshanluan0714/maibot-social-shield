"""Configuration schema for github.yunshanluan0714.social-shield v2.0.0."""

from maibot_sdk import Field, PluginConfigBase


class PluginSection(PluginConfigBase):
    __ui_label__ = "插件"
    __ui_icon__ = "package"
    __ui_order__ = 0

    enabled: bool = Field(default=True, description="启用 OOC/防Bot互搏熔断、单群风控限流与跨群隐私防火墙插件")
    config_version: str = Field(default="2.0.0", description="配置版本")


class OOCAntiBotSection(PluginConfigBase):
    __ui_label__ = "OOC 熔断与防 Bot 互搏"
    __ui_icon__ = "cpu"
    __ui_order__ = 1

    enabled: bool = Field(default=True, description="监控输出客服腔/高重复车轱辘话与双 Bot 无限对聊，自动冷却静默")
    bot_pingpong_threshold: int = Field(default=4, ge=3, le=12, description="同人快节奏连续互回多少轮触发防互搏冷却")
    bot_cooldown_minutes: int = Field(default=15, ge=1, le=120, description="触发互搏熔断后的冷却静默分钟数")
    known_bot_ids: list[str] = Field(default_factory=list, description="已知其他机器人账号 ID 列表（可选）")


class RiskControlSection(PluginConfigBase):
    __ui_label__ = "单群限流与敏感词拦截"
    __ui_icon__ = "alert-triangle"
    __ui_order__ = 2

    enabled: bool = Field(default=True, description="启用单群文本/发图频控熔断与出站敏感词拦截")
    max_group_msgs_per_minute: int = Field(default=8, ge=1, le=30, description="单群每分钟允许发送的消息总条数上限")
    max_group_media_per_minute: int = Field(default=3, ge=1, le=20, description="单群每分钟允许发送的图片/表情包条数上限")
    sensitive_words: list[str] = Field(default_factory=list, description="出站拦截敏感词列表（大小写不敏感）")
    sensitive_regex: list[str] = Field(default_factory=list, description="出站拦截正则表达式列表")


class PrivacyFirewallSection(PluginConfigBase):
    __ui_label__ = "跨群记忆隔离防火墙"
    __ui_icon__ = "lock"
    __ui_order__ = 3

    enabled: bool = Field(default=True, description="群聊下强制锁定 query_memory 的 respect_filter=True，防止跨群与私聊记忆串台")
    lock_group_memory_filter: bool = Field(default=True, description="拦截群聊内模型绕过 respect_filter 检索他群或私聊记忆")
    inject_prompt_reminder: bool = Field(
        default=False,
        description="可选：额外在群聊提示词注入防泄密提醒（默认关闭以保持常态 0 Prompt 注入，仅靠硬参数锁定隔离）",
    )


class SocialShieldConfig(PluginConfigBase):
    plugin: PluginSection = Field(default_factory=PluginSection)
    ooc_antibot: OOCAntiBotSection = Field(default_factory=OOCAntiBotSection)
    risk_control: RiskControlSection = Field(default_factory=RiskControlSection)
    privacy_firewall: PrivacyFirewallSection = Field(default_factory=PrivacyFirewallSection)
