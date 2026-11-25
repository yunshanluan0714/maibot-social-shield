"""Configuration schema for github.yunshanluan0714.social-shield."""

from maibot_sdk import Field, PluginConfigBase


class PluginSection(PluginConfigBase):
    __ui_label__ = "插件"
    __ui_icon__ = "package"
    __ui_order__ = 0

    enabled: bool = Field(default=True, description="启用群气氛避雷、反图灵、隐私防火墙与风控熔断插件")
    config_version: str = Field(default="1.0.0", description="配置版本")


class AtmosphereSection(PluginConfigBase):
    __ui_label__ = "群气氛温度计与避雷"
    __ui_icon__ = "thermometer"
    __ui_order__ = 1

    enabled: bool = Field(default=True, description="识别群聊水群/求助/对线/倾诉/高危敏感场域并自动避雷")
    sensitive_cooldown_minutes: int = Field(default=10, ge=1, le=120, description="踩到高危敏感/炸群内容后该群静默装死分钟数")
    extra_sensitive_words: list[str] = Field(default_factory=list, description="额外高危避雷词列表")
    extra_sensitive_regex: list[str] = Field(default_factory=list, description="额外高危避雷正则列表")


class RelationshipSection(PluginConfigBase):
    __ui_label__ = "动态关系与杠精处理"
    __ui_icon__ = "users"
    __ui_order__ = 2

    enabled: bool = Field(default=True, description="按互动自动维护群友亲密度与杠精冷处理状态")
    troll_threshold: int = Field(default=-25, ge=-100, le=-5, description="低于该分值进入冷处理（减少理会/敷衍或无视）")
    close_friend_threshold: int = Field(default=35, ge=10, le=100, description="高于该分值进入熟人互怼口吻")


class DefenseSection(PluginConfigBase):
    __ui_label__ = "反图灵测试与能力降级"
    __ui_icon__ = "shield"
    __ui_order__ = 3

    enabled: bool = Field(default=True, description="启用人设中立的抗提示词注入、防测机与硬核题能力降级")
    anti_injection: bool = Field(default=True, description="检测测机/越权/套取系统提示词话术并引导贴合人设的打岔")
    capability_downgrade: bool = Field(
        default=True,
        description="对高数推导/完整法条背诵/长篇全文翻译/整套业务代码主动降智推脱",
    )
    bypass_keywords: list[str] = Field(
        default_factory=list,
        description="豁免能力降级的专业领域关键词（如某特定游戏或学习专题词，留空则仅保护常规闲聊）",
    )


class PrivacyFirewallSection(PluginConfigBase):
    __ui_label__ = "跨群与私聊防火墙"
    __ui_icon__ = "lock"
    __ui_order__ = 4

    enabled: bool = Field(default=True, description="群聊下强制锁死 query_memory 隔离参数，并注入跨群与私聊防泄密提醒")
    lock_group_memory_filter: bool = Field(default=True, description="拦截群聊内模型绕过 respect_filter 检索私聊记忆")


class OOCAntiBotSection(PluginConfigBase):
    __ui_label__ = "OOC熔断与防Bot互搏"
    __ui_icon__ = "cpu"
    __ui_order__ = 5

    enabled: bool = Field(default=True, description="监控输出车轱辘话/客服腔与双Bot无限对聊，自动冷却静默")
    bot_pingpong_threshold: int = Field(default=4, ge=3, le=12, description="同人快节奏连续互回多少轮触发防互搏冷却")
    bot_cooldown_minutes: int = Field(default=15, ge=1, le=120, description="触发互搏熔断后的冷却静默分钟数")
    known_bot_ids: list[str] = Field(default_factory=list, description="已知其他机器人账号 ID 列表（可选）")


class RiskControlSection(PluginConfigBase):
    __ui_label__ = "风控限流与敏感词"
    __ui_icon__ = "alert-triangle"
    __ui_order__ = 6

    enabled: bool = Field(default=True, description="启用单群文本/发图频控熔断与出站敏感词拦截")
    max_group_msgs_per_minute: int = Field(default=8, ge=1, le=30, description="单群每分钟允许发送的消息总条数上限")
    max_group_media_per_minute: int = Field(default=3, ge=1, le=20, description="单群每分钟允许发送的图片/表情包条数上限")
    sensitive_words: list[str] = Field(default_factory=list, description="出站拦截敏感词列表（大小写不敏感）")
    sensitive_regex: list[str] = Field(default_factory=list, description="出站拦截正则表达式列表")


class SocialShieldConfig(PluginConfigBase):
    plugin: PluginSection = Field(default_factory=PluginSection)
    atmosphere: AtmosphereSection = Field(default_factory=AtmosphereSection)
    relationship: RelationshipSection = Field(default_factory=RelationshipSection)
    defense: DefenseSection = Field(default_factory=DefenseSection)
    privacy_firewall: PrivacyFirewallSection = Field(default_factory=PrivacyFirewallSection)
    ooc_antibot: OOCAntiBotSection = Field(default_factory=OOCAntiBotSection)
    risk_control: RiskControlSection = Field(default_factory=RiskControlSection)
