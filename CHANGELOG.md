# 更新日志

本文件记录「麦麦掉线通知」插件各版本变更。

格式参考 Keep a Changelog，版本号遵循语义化版本（SemVer）。

## [1.1.0] - 2026-07-03

### 变更

- **精简适配器配置**：`AdapterConfig` 从 9 个字段精简为 5 个最常用字段
  （`name` / `enabled` / `host` / `port` / `token`），让小白能快速看懂和配置。
- 探测方式固定为 WebSocket（`ws`），进阶参数（`action` / `online_field` /
  `timeout_sec` 等）移至 `config.py` 顶部 `DEFAULT_*` 常量，所有适配器共享。
- `http` / `send` 两种探测方式不再暴露在配置中；进阶用户可在 `AdapterConfig`
  重新加回 `probe_type` 字段，`plugin.py` 会自动识别。
- 修复 `_probe_adapter` 读取已删除字段导致的 `AttributeError`（此前会使所有
  适配器被误判为离线）。
- `config_version` 升至 `1.1.0`。

## [1.0.0] - 2026-07-02

### 新增

- 首个版本。
- 周期性探测 MaiBot 的 napcat / snowluma 适配器是否在线。
- 掉线时通过 Server 酱 Turbo Webhook 向号主手机推送通知。
- 适配器恢复上线时可选推送恢复通知。
- 三种探测方式：
  - `ws`（默认，推荐）：通过正向 WebSocket 调用 OneBot `get_status` 动作，
    仅查询 QQ 在线状态，不发送任何聊天消息，napcat / snowluma 通用。
  - `http`：请求适配器 HTTP 状态接口，适用于只开了 HTTP 服务的适配器。
  - `send`：向指定会话发送探测消息，按 `ctx.send.text` 返回值判定链路可用性。
- 防抖：连续失败达到阈值（`fail_threshold`）才判定掉线，避免网络抖动误报。
- 冷却：掉线期间重复通知受 `notify_cooldown_sec` 限制，避免轰炸。
- 支持配置热重载：`on_config_update` 检测到自身配置变更后自动重启监控任务。
- WebUI 配置 Schema 自动生成。
