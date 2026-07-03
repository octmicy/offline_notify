"""麦麦掉线通知插件入口。

功能：周期性探测 MaiBot 的 napcat / snowluma 等适配器是否在线，掉线时通过
Server 酱 Webhook 向号主手机推送通知，恢复上线时可选推送恢复通知。

探测方式见 config.py / notifier.py 的说明：默认通过正向 WebSocket 调用 OneBot
get_status 动作查询 QQ 在线状态（伪装请求，不发送任何聊天消息）；进阶的 http /
send 方式见 config.py 顶部 DEFAULT_* 常量与 AdapterConfig 的说明。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, ClassVar, Iterable, Optional

from maibot_sdk import MaiBotPlugin

from .config import (
    DEFAULT_ONLINE_FIELD,
    DEFAULT_PROBE_ACTION,
    DEFAULT_PROBE_TIMEOUT_SEC,
    OfflineNotifySettings,
)
from .notifier import probe_http, probe_ws, push_serverchan


class OfflineNotifyPlugin(MaiBotPlugin):
    """适配器掉线监控插件主类。"""

    config_model = OfflineNotifySettings

    # 本插件只关心自身配置热重载，不订阅 bot/model 全局配置变更
    config_reload_subscriptions: ClassVar[Iterable[str]] = ()

    def __init__(self) -> None:
        super().__init__()
        # 后台监控任务句柄
        self._monitor_task: Optional[asyncio.Task[None]] = None
        # 每个适配器的运行时状态：name -> 状态字典
        self._adapter_states: dict[str, dict[str, Any]] = {}

    # ── 生命周期 ──────────────────────────────────────────────────────

    async def on_load(self) -> None:
        """插件加载：启动后台监控任务。"""

        self.ctx.logger.info("[麦麦掉线通知] 插件已加载")
        self._start_monitor()

    async def on_unload(self) -> None:
        """插件卸载：停止后台监控任务。"""

        self._stop_monitor()
        self.ctx.logger.info("[麦麦掉线通知] 插件已卸载")

    async def on_config_update(self, scope: str, config_data: dict, version: str) -> None:
        """配置热重载：重启监控任务以应用新的适配器配置。

        self.config 在 Runner 调用本回调前已被基类更新为最新实例，故直接重启即可。
        """

        self.ctx.logger.info(f"[麦麦掉线通知] 配置更新: scope={scope}, version={version}")
        if scope == "self":
            self._stop_monitor()
            # 保留各适配器已知在线状态，避免重启导致重复通知
            self._start_monitor()

    # ── 监控任务管理 ──────────────────────────────────────────────────

    def _start_monitor(self) -> None:
        """启动后台监控循环（若配置启用且当前未运行）。"""

        try:
            settings = self.config
        except RuntimeError:
            self.ctx.logger.warning("[麦麦掉线通知] 配置尚未就绪，跳过启动监控")
            return

        if not settings.plugin.enabled:
            self.ctx.logger.info("[麦麦掉线通知] 插件已关闭监控（plugin.enabled=false）")
            return

        if self._monitor_task is not None and not self._monitor_task.done():
            self.ctx.logger.debug("[麦麦掉线通知] 监控任务已在运行，跳过重复启动")
            return

        self._monitor_task = asyncio.create_task(self._monitor_loop(), name="offline-notify-monitor")
        adapter_list = settings.adapters.adapters
        self.ctx.logger.info(
            f"[麦麦掉线通知] 监控已启动，间隔 {settings.plugin.check_interval_sec}s，"
            f"适配器数 {len(adapter_list)}"
        )

    def _stop_monitor(self) -> None:
        """停止后台监控循环。"""

        task = self._monitor_task
        self._monitor_task = None
        if task is not None and not task.done():
            task.cancel()
            # 交由 _monitor_loop 捕获 CancelledError；这里不 await，避免阻塞卸载流程

    # ── 监控主循环 ────────────────────────────────────────────────────

    async def _monitor_loop(self) -> None:
        """周期性探测全部适配器并处理状态变迁。

        被取消（卸载 / 重启）时安静退出。
        """

        try:
            while True:
                try:
                    settings = self.config
                except RuntimeError:
                    # 配置未就绪，稍后重试
                    await asyncio.sleep(5.0)
                    continue

                if not settings.plugin.enabled:
                    # 运行中被关闭：清空状态并退出循环
                    self._adapter_states.clear()
                    self.ctx.logger.info("[麦麦掉线通知] 运行中检测到 enabled=false，停止监控")
                    return

                await self._probe_once(settings)

                # 两次探测之间的间隔；用 sleep 让取消能及时生效
                await asyncio.sleep(max(1.0, float(settings.plugin.check_interval_sec)))
        except asyncio.CancelledError:
            self.ctx.logger.debug("[麦麦掉线通知] 监控任务被取消")
            raise
        except Exception:  # noqa: BLE001
            # 兜底：循环内异常不应让任务静默退出，记录后继续下一轮
            self.ctx.logger.exception("[麦麦掉线通知] 监控循环异常")
            await asyncio.sleep(5.0)

    async def _probe_once(self, settings: OfflineNotifySettings) -> None:
        """对所有启用的适配器各执行一次探测并处理状态变迁。"""

        for adapter in settings.adapters.adapters:
            if not adapter.enabled or not adapter.name:
                continue
            try:
                online, detail = await self._probe_adapter(adapter)
            except Exception as exc:  # noqa: BLE001
                online, detail = False, f"探测异常: {exc}"

            await self._handle_state_change(settings, adapter.name, online, detail)

    async def _probe_adapter(self, adapter: Any) -> tuple[bool, str]:
        """对单个适配器执行探测，返回 (是否在线, 说明文本)。

        默认走正向 WebSocket（见 config.py 的 DEFAULT_* 常量）：通过 OneBot
        ``get_status`` 动作查 QQ 在线状态，全程不发送任何聊天消息，napcat /
        snowluma 通用。

        精简后的 AdapterConfig 只暴露 name / enabled / host / port / token 五个
        字段，进阶参数（探测方式、动作名、在线字段、超时等）统一回退到 DEFAULT_*
        常量。若在 AdapterConfig 中重新加回这些字段（见 config.py 顶部说明），
        本方法会自动读取，支持 ws / http / send 三种探测方式。

        Args:
            adapter: AdapterConfig 实例。

        Returns:
            tuple[bool, str]: (在线状态, 说明文本)。
        """

        probe_type = getattr(adapter, "probe_type", "ws")
        action = getattr(adapter, "action", DEFAULT_PROBE_ACTION)
        online_field = getattr(adapter, "online_field", DEFAULT_ONLINE_FIELD)
        timeout = getattr(adapter, "timeout_sec", DEFAULT_PROBE_TIMEOUT_SEC)

        if probe_type == "ws":
            # 伪装请求：通过正向 WebSocket 调用 get_status，查 QQ 在线状态，
            # 不发送任何聊天消息。napcat / snowluma 通用。
            return await probe_ws(
                adapter.host,
                adapter.port,
                token=adapter.token,
                action=action,
                online_field=online_field,
                timeout=timeout,
            )

        if probe_type == "http":
            # 伪装请求：直接查适配器 HTTP 状态接口，不发送任何聊天消息
            return await probe_http(
                getattr(adapter, "url", ""),
                method=getattr(adapter, "method", "GET"),
                token=adapter.token,
                online_field=online_field,
                timeout=timeout,
            )

        if probe_type == "send":
            # 回退方式：向目标会话发一条探测消息，依据返回 bool 判定
            stream_id = getattr(adapter, "stream_id", "")
            if not stream_id:
                return False, "send 方式未配置 stream_id"
            probe_text = getattr(adapter, "probe_text", "⚠️适配器存活探测（请忽略）")
            try:
                ok = await self.ctx.send.text(text=probe_text, stream_id=stream_id)
            except Exception as exc:  # noqa: BLE001
                return False, f"发送探测失败: {exc}"
            # send.text 返回 bool（True=发送成功视为在线，False=链路不可用）
            return bool(ok), "在线（send 探测成功）" if ok else "离线（send 探测失败）"

        return False, f"未知探测方式: {probe_type}"

    # ── 状态变迁与通知 ────────────────────────────────────────────────

    async def _handle_state_change(
        self,
        settings: OfflineNotifySettings,
        name: str,
        online: bool,
        detail: str,
    ) -> None:
        """根据单次探测结果更新状态，并在掉线/恢复时推送通知。

        防抖与冷却策略：
          - 在线→离线需连续失败达到 fail_threshold 次才推送。
          - 重复推送受 notify_cooldown_sec 冷却限制。
          - 离线→在线一次性推送恢复通知（若开启 notify_recovery）。
        """

        state = self._adapter_states.setdefault(
            name,
            {
                "online": True,  # 初始假设在线，避免启动即误报
                "fail_count": 0,
                "last_notify_ts": 0.0,
                "notified_offline": False,
            },
        )

        now = time.monotonic()
        threshold = max(1, int(settings.plugin.fail_threshold))
        cooldown = max(0.0, float(settings.plugin.notify_cooldown_sec))

        if online:
            state["fail_count"] = 0
            if not state["online"]:
                # 离线 → 在线：恢复
                state["online"] = True
                state["notified_offline"] = False
                self.ctx.logger.info(f"[麦麦掉线通知] {name} 已恢复上线: {detail}")
                if settings.notify.notify_recovery:
                    await self._notify(
                        settings,
                        name,
                        title=f"【{settings.notify.bot_name}】{name} 已恢复上线",
                        desp=self._build_desp(
                            settings, name, online=True, detail=detail
                        ),
                    )
            else:
                self.ctx.logger.debug(f"[麦麦掉线通知] {name} 探测正常: {detail}")
            return

        # 离线
        state["fail_count"] += 1
        self.ctx.logger.warning(
            f"[麦麦掉线通知] {name} 探测失败 ({state['fail_count']}/{threshold}): {detail}"
        )

        if state["fail_count"] < threshold:
            # 还未达到判定阈值，暂不通知
            return

        state["online"] = False

        # 冷却：掉线期间避免重复轰炸，超过冷却时间才再次提醒
        if state["notified_offline"] and (now - state["last_notify_ts"]) < cooldown:
            return

        self.ctx.logger.error(f"[麦麦掉线通知] {name} 判定掉线，推送通知: {detail}")
        pushed, msg = await self._notify(
            settings,
            name,
            title=f"【{settings.notify.bot_name}】{name} 适配器掉线！",
            desp=self._build_desp(settings, name, online=False, detail=detail),
        )
        if pushed:
            state["notified_offline"] = True
            state["last_notify_ts"] = now
        else:
            self.ctx.logger.error(f"[麦麦掉线通知] 通知推送失败: {msg}")

    async def _notify(
        self,
        settings: OfflineNotifySettings,
        name: str,
        *,
        title: str,
        desp: str,
    ) -> tuple[bool, str]:
        """推送一条 Server 酱通知。"""

        notify = settings.notify
        if not notify.serverchan_sendkey:
            self.ctx.logger.warning("[麦麦掉线通知] 未配置 SendKey，跳过推送")
            return False, "未配置 SendKey"
        return await push_serverchan(
            api_base=notify.serverchan_api_base,
            sendkey=notify.serverchan_sendkey,
            title=title,
            desp=desp,
            channel=notify.channel,
        )

    def _build_desp(
        self,
        settings: OfflineNotifySettings,
        name: str,
        *,
        online: bool,
        detail: str,
    ) -> str:
        """构造通知正文（Markdown）。

        用 monotonic 时间会随重启重置，因此正文里用 time.time() 真实时间戳。
        """

        # 在 _handle_state_change 内调用，monotonic 不适合展示，这里取墙钟时间
        ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time()))
        status_text = "✅ 在线" if online else "❌ 离线"
        return (
            f"**机器人**：{settings.notify.bot_name}\n\n"
            f"**适配器**：{name}\n\n"
            f"**状态**：{status_text}\n\n"
            f"**时间**：{ts}\n\n"
            f"**详情**：{detail}\n"
        )


def create_plugin() -> MaiBotPlugin:
    """MaiBot 插件工厂函数。"""

    return OfflineNotifyPlugin()
