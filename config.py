"""麦麦掉线通知插件配置模型。

配置结构：
  [plugin]        插件总开关与监控节奏
  [notify]        Server 酱推送与通知策略
  [[adapters]]    需要监控的适配器列表（napcat / snowluma / 任意 OneBot HTTP 端点）

探测原理：
  SDK 没有暴露“适配器在线状态”查询接口，也没有掉线 Hook。因此本插件采用
  “伪装请求”方式：直接向适配器自身的状态接口发起一次 HTTP 请求（例如 napcat
  的 /get_status），仅查询 QQ 是否在线，不会向任何聊天会话发送可见消息。
  对于没有 HTTP 状态接口的适配器，可回退为 send 探测（向指定会话发一条探测
  消息，根据返回 bool 判定链路是否可用）。
"""

from __future__ import annotations

from typing import ClassVar, List

from maibot_sdk import Field, PluginConfigBase

# ── 适配器探测的进阶默认值（所有适配器共享，不暴露到 WebUI）─────────────
# 如需调整这些参数，直接改下面常量即可，无需改 config.toml。
# 想恢复每适配器独立配置，可在 AdapterConfig 里重新加回对应字段并在 plugin.py 读取。
DEFAULT_PROBE_ACTION = "get_status"
"""WS 探测调用的 OneBot 动作名。get_status 返回 QQ 在线状态。"""

DEFAULT_ONLINE_FIELD = "data.online"
"""响应 JSON 中表示在线的布尔字段路径（点分隔）。留空则改用 OneBot status 判定。"""

DEFAULT_PROBE_TIMEOUT_SEC = 10.0
"""单次 WS 探测的连接与等待响应总超时（秒）。"""


class PluginOptions(PluginConfigBase):
    """插件总开关与监控节奏配置。"""

    __ui_label__: ClassVar[str] = "插件设置"
    __ui_order__: ClassVar[int] = 0

    enabled: bool = Field(
        default=True,
        description="是否启用掉线监控。",
        json_schema_extra={
            "label": "启用监控",
            "hint": "关闭后插件不会启动后台监控任务。",
            "order": 0,
        },
    )
    # 配置版本号：MaiBot 加载策略要求 [plugin] 段必须包含 config_version，
    # 否则会以“配置版本非法”拒绝加载。修改配置结构时需同步递增此版本号。
    config_version: str = Field(
        default="1.1.0",
        description="插件配置结构版本号。",
        json_schema_extra={
            "disabled": True,
            "hidden": True,
            "label": "配置版本",
            "order": 99,
        },
    )
    check_interval_sec: float = Field(
        default=60.0,
        description="两次探测之间的间隔（秒）。",
        json_schema_extra={
            "label": "探测间隔（秒）",
            "hint": "建议 30~300 秒。过短会增加适配器压力，过长则掉线发现延迟。",
            "order": 1,
            "step": 1,
        },
    )
    fail_threshold: int = Field(
        default=2,
        description="连续探测失败多少次后才判定为掉线并通知。",
        json_schema_extra={
            "label": "掉线判定阈值",
            "hint": "连续失败达到该次数才推送掉线通知，避免网络抖动误报。",
            "order": 2,
            "step": 1,
        },
    )
    notify_cooldown_sec: float = Field(
        default=300.0,
        description="同一适配器重复通知的最小间隔（秒）。",
        json_schema_extra={
            "label": "通知冷却（秒）",
            "hint": "掉线期间避免重复轰炸，达到冷却时间后会再次提醒。",
            "order": 3,
            "step": 1,
        },
    )


class NotifyConfig(PluginConfigBase):
    """Server 酱推送与通知策略配置。"""

    __ui_label__: ClassVar[str] = "通知设置"
    __ui_order__: ClassVar[int] = 1

    serverchan_sendkey: str = Field(
        default="",
        description="Server 酱 Turbo 的 SendKey（sct 开头）。",
        json_schema_extra={
            "label": "Server 酱 SendKey",
            "hint": "在 https://sct.ftqq.com 登录后获取；填空则不会推送。",
            "input_type": "password",
            "order": 0,
            "placeholder": "SCT...",
        },
    )
    serverchan_api_base: str = Field(
        default="https://sctapi.ftqq.com",
        description="Server 酱推送接口基址。",
        json_schema_extra={
            "label": "推送接口基址",
            "hint": "一般无需修改，默认官方接口。",
            "order": 1,
        },
    )
    channel: str = Field(
        default="",
        description="可选的推送通道（留空走默认通道）。",
        json_schema_extra={
            "label": "推送通道",
            "hint": "Server 酱通道名，留空则推送到默认通道（微信/Server 酱 App）。",
            "order": 2,
            "placeholder": "可留空",
        },
    )
    notify_recovery: bool = Field(
        default=True,
        description="适配器恢复上线时是否推送恢复通知。",
        json_schema_extra={
            "label": "推送恢复通知",
            "hint": "开启后，掉线的适配器重新上线时也会推送一条通知。",
            "order": 3,
        },
    )
    bot_name: str = Field(
        default="麦麦",
        description="通知文案中显示的机器人名称。",
        json_schema_extra={
            "label": "机器人名称",
            "hint": "用于通知标题/正文，便于号主识别是哪个麦麦掉线。",
            "order": 4,
            "placeholder": "麦麦",
        },
    )


class AdapterConfig(PluginConfigBase):
    """单个被监控适配器的配置。

    为让新手快速上手，本配置只暴露 5 个最常用字段：name / enabled / host / port /
    token。探测方式固定为 ``ws``（通过正向 WebSocket 调用 OneBot ``get_status`` 动作
    查询在线状态，napcat / snowluma 通用，且不发送任何聊天消息）。

    其余进阶参数（在线字段路径、动作名、超时等）所有适配器共享合理默认值，见本模块
    顶部 ``DEFAULT_*`` 常量；如需调整请修改源码常量。
    """

    __ui_label__: ClassVar[str] = "适配器"
    __ui_order__: ClassVar[int] = 2

    name: str = Field(
        default="",
        description="适配器显示名称，用于通知文案。",
        json_schema_extra={
            "label": "适配器名称",
            "hint": "例如：napcat、snowluma、QQ主号。",
            "placeholder": "napcat",
        },
    )
    enabled: bool = Field(
        default=True,
        description="是否监控该适配器。",
        json_schema_extra={
            "label": "启用该适配器监控",
        },
    )
    host: str = Field(
        default="127.0.0.1",
        description="适配器正向 WebSocket 主机地址。",
        json_schema_extra={
            "label": "主机地址",
            "hint": "⚠️ 必填，因人而异！运行 napcat/snowluma 的机器地址。"
            "本机部署填 127.0.0.1；适配器在另一台机器则填该机 IP。"
            "需与你 napcat-adapter / snowluma-adapter 配置里的 host 一致。",
            "placeholder": "127.0.0.1",
        },
    )
    port: int = Field(
        default=0,
        description="适配器正向 WebSocket 端口。",
        json_schema_extra={
            "label": "端口",
            "hint": "⚠️ 必填，因人而异！你 napcat/snowluma 实际监听的端口，"
            "需与 napcat-adapter / snowluma-adapter 配置里的 port 完全一致。"
            "（napcat 常见 7998，snowluma 常见 7988，但很多人会改）。",
            "step": 1,
            "placeholder": "7998",
        },
    )
    token: str = Field(
        default="",
        description="适配器访问令牌（未启用鉴权可留空）。",
        json_schema_extra={
            "label": "访问令牌",
            "hint": "与 napcat-adapter / snowluma-adapter 配置中的 token 保持一致；未开鉴权留空。",
            "input_type": "password",
            "placeholder": "可留空",
        },
    )


class AdaptersSection(PluginConfigBase):
    """适配器监控列表配置节。

    把 ``adapters`` 包进一个独立的 PluginConfigBase 子模型，是为了让 SDK 的
    Schema 生成器把它识别为一个「具名配置节」（而非落到 ``general`` 兜底节）。
    直接用 ``List[AdapterConfig]`` 作为顶层字段时，SDK 不会为它生成具名 section，
    导致 WebUI 不渲染该列表的编辑界面。详见 SDK config.py 的
    ``generate_plugin_config_schema``。
    """

    __ui_label__: ClassVar[str] = "适配器监控"
    __ui_order__: ClassVar[int] = 2

    adapters: List[AdapterConfig] = Field(
        default_factory=lambda: [
            # napcat 适配器：正向 WebSocket 默认端口 7998
            AdapterConfig(
                name="napcat",
                enabled=False,
                host="127.0.0.1",
                port=7998,
                token="",
            ),
            # snowluma 适配器：luma 服务正向 WebSocket 默认端口 7988
            AdapterConfig(
                name="snowluma",
                enabled=False,
                host="127.0.0.1",
                port=7988,
                token="",
            ),
        ],
        description="被监控的适配器列表。",
    )


class OfflineNotifySettings(PluginConfigBase):
    """麦麦掉线通知插件完整配置。"""

    plugin: PluginOptions = Field(default_factory=PluginOptions)
    notify: NotifyConfig = Field(default_factory=NotifyConfig)
    adapters: AdaptersSection = Field(default_factory=AdaptersSection)
