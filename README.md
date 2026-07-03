# 麦麦掉线通知

监控 MaiBot 的 **napcat / snowluma 适配器**是否在线，当适配器掉线时通过
**Server 酱 Webhook** 向号主手机推送通知，恢复上线时可选推送恢复通知。

> 适用于 MaiBot Plugin SDK 2.5.x+，主程序 1.0.0+。

---

## 工作原理

MaiBot 的 SDK 没有暴露「适配器在线状态」查询接口，也没有掉线 Hook。本插件
通过**主动探测**来判定适配器是否在线。

### 为什么默认用 WebSocket 探测

查阅主程序自带的 `napcat-adapter` 与 `snowluma-adapter` 源码可知，两者使用
**同一套协议**：都在各自端口开一个正向 WebSocket，对外提供 OneBot 11 动作
接口，鉴权都用 `?access_token=` 查询参数。

| 适配器 | 默认 WS 端口 |
|--------|-------------|
| napcat | 7998 |
| snowluma | 7988 |

因此插件的做法是：**临时连一下这个 WS，发一个 `get_status` 动作，读取返回的
`data.online`** —— 全程不向任何聊天会话发送可见消息，对群聊零打扰，且 napcat /
snowluma 通吃。

### 探测方式

默认且唯一暴露在配置里的探测方式是 **WebSocket**：临时连一下适配器的正向 WS，
发一个 `get_status` 动作，读取返回的 `data.online` —— 全程不向任何聊天会话发送
可见消息，对群聊零打扰，napcat / snowluma 通吃。

> 进阶用户若需要 `http`（查 HTTP 状态接口）或 `send`（发探测消息）方式，可在
> `config.py` 的 `AdapterConfig` 中重新加回 `probe_type` 等字段，`plugin.py` 会
> 自动识别（详见 `config.py` 顶部 `DEFAULT_*` 常量的说明）。

---

## 安装

把 `offline_notify` 文件夹放到 MaiBot 插件目录：

```
D:\MaiBot\data\plugins\offline_notify\
```

依赖 `aiohttp`（用于 WS 探测）。napcat / snowluma 适配器自身也依赖它，
MaiBot 运行环境通常已安装；若未安装：

```bash
pip install aiohttp
```

重启 MaiBot 或在 WebUI 重载插件即可。

> 安装后务必先看下面的 [配置](#配置) 一节，把适配器的 `host` / `port` 改成你自己的，
> 否则插件会因连不上适配器而一直报掉线。

---

## 配置

编辑插件目录下的 `config.toml`。

### 1. 填写 Server 酱 SendKey

在 [https://sct.ftqq.com](https://sct.ftqq.com) 登录后获取 SendKey（`sct` 开头），
填入：

```toml
[notify]
serverchan_sendkey = "SCT你的sendkey"
```

### 2. 启用要监控的适配器（⚠️ 必改 IP 和端口）

默认预置了 napcat 和 snowluma 两条（`enabled = false`）。把要监控的改成 `true`。

> **⚠️ 重要：`host` 和 `port` 必须改成你自己的，因人而异！**
>
> 每个人部署 napcat / snowluma 的机器 IP 和端口都不一样，**不要直接用下面的默认值**，
> 否则大概率连不上。请打开你自己的 `napcat-adapter` / `snowluma-adapter` 的 `config.toml`，
> 把对应字段原样抄过来：
>
> | 本插件字段 | napcat-adapter 配置字段 | snowluma-adapter 配置字段 |
> |-----------|------------------------|--------------------------|
> | `host` | `napcat_server.host` | `luma_client.server` |
> | `port` | `napcat_server.port` | `luma_client.port` |
> | `token` | `napcat_server.token` | `luma_client.token` |
>
> - **本机部署**：`host` 填 `127.0.0.1`
> - **适配器在另一台机器**：`host` 填那台机器的 IP（如 `192.168.1.100`）
> - **端口**：napcat 常见 7998、snowluma 常见 7988，但很多人会改，以你的配置为准

```toml
[adapters]
# napcat 适配器
[[adapters.adapters]]
name = "napcat"
enabled = true
host = "127.0.0.1"              # ← 改成你的 napcat 机器 IP
port = 7998                     # ← 改成你的 napcat 实际端口
token = ""                      # 与 napcat-adapter 的 napcat_server.token 一致

# snowluma 适配器
[[adapters.adapters]]
name = "snowluma"
enabled = true
host = "127.0.0.1"              # ← 改成你的 snowluma 机器 IP
port = 7988                     # ← 改成你的 snowluma 实际端口
token = ""                      # 与 snowluma-adapter 的 luma_client.token 一致
```

### 完整配置项

```toml
[plugin]
enabled = true                  # 总开关
config_version = "1.1.0"        # 配置结构版本号（勿改）
check_interval_sec = 60         # 两次探测间隔（秒）
fail_threshold = 2              # 连续失败多少次才判定掉线并通知
notify_cooldown_sec = 300       # 同一适配器重复通知最小间隔（秒）

[notify]
serverchan_sendkey = ""         # Server 酱 SendKey（sct 开头，必填）
serverchan_api_base = "https://sctapi.ftqq.com"  # 一般无需修改
channel = ""                    # 推送通道，留空走默认通道
notify_recovery = true          # 适配器恢复上线时是否也推送通知
bot_name = "麦麦"               # 通知文案中的机器人名称

[adapters]
# 每个适配器只需这 5 个字段；探测方式固定为 WebSocket，无需额外配置
[[adapters.adapters]]
name = "napcat"                 # 适配器显示名称
enabled = false                 # 是否监控该适配器
host = "127.0.0.1"              # 适配器机器 IP（本机填 127.0.0.1）
port = 7998                     # 适配器正向 WebSocket 端口
token = ""                      # 访问令牌（未启用鉴权留空）
```

所有配置也可在 WebUI 的插件设置页修改，改后自动热重载。

---

## 通知示例

掉线：

```
【麦麦】napcat 适配器掉线！

机器人：麦麦
适配器：napcat
状态：❌ 离线
时间：2026-07-02 22:38:00
详情：WebSocket 连接失败: Connection refused
```

恢复：

```
【麦麦】napcat 已恢复上线

机器人：麦麦
适配器：napcat
状态：✅ 在线
时间：2026-07-02 22:39:00
详情：在线（data.online=True）
```

---

## 防抖与冷却

- **防抖**：单次探测失败不立即通知，需连续失败 `fail_threshold` 次（默认 2）才判定掉线，
  避免网络抖动误报。
- **冷却**：掉线期间不重复轰炸，达到 `notify_cooldown_sec`（默认 300 秒）后才再次提醒。
- **恢复**：掉线的适配器重新探测成功即一次性推送恢复通知（可由 `notify_recovery` 关闭）。

---

## 常见问题（连不上 / 一直报掉线）

1. **`host` / `port` 没改成自己的**
   这是最常见原因。默认值 `127.0.0.1:7998` 只对本机部署 napcat 的人有效。
   请按上面表格，从你的 `napcat-adapter` / `snowluma-adapter` 配置里抄真实值。

2. **适配器在另一台机器，但填了 `127.0.0.1`**
   `127.0.0.1` 只指本机。若 napcat/snowluma 跑在别的机器，要填那台机器的局域网 IP
   （如 `192.168.1.100`），并确保该机器的 WS 端口对本机可达（防火墙放行）。

3. **`token` 不一致**
   若适配器开了鉴权，本插件的 `token` 必须与适配器配置里的完全一致，否则 WS 连接
   会被拒绝（详情里会出现 `401` 或连接立即关闭）。

4. **端口填错 / 适配器没开 WS 服务**
   napcat / snowluma 需要开启「正向 WebSocket」服务。确认你在适配器配置里看到的
   端口确实在监听（可在适配器机器上 `netstat -ano | findstr 端口号` 验证）。

5. **临时关掉防抖快速验证**
   把 `fail_threshold = 1`、`check_interval_sec = 15`，可在约 15 秒内看到第一次判定，
   便于调试。验证通过后再调回正常值。

排查时看 MaiBot 日志，插件会输出每次探测的详情，例如：
`[麦麦掉线通知] napcat 探测失败 (1/2): WebSocket 连接失败: Connection refused`。

---

## 局限性

WS `get_status` 探测的是「napcat/snowluma 进程存活 + QQ 账号在线」，**不直接等于**
「napcat↔MaiBot 的 maim_message 链路是否在线」。绝大多数情况下两者一致
（适配器进程活着、QQ 登录着，WS 就通），但若适配器进程正常、仅与 MaiBot 的连接
异常断开，本探测仍会判「在线」。如需覆盖此极端情况，可在 `config.py` 中给适配器
加回 `probe_type = "send"` 字段做交叉验证（进阶用法，见 `config.py` 顶部说明）。

---

## 文件结构

```
offline_notify/
├── _manifest.json   # 插件清单
├── config.py        # 配置模型（PluginConfigBase + Field）
├── config.toml      # 用户配置
├── notifier.py      # ws/http 探测 + Server 酱推送
├── plugin.py        # 插件入口（生命周期 + 监控循环）
├── __init__.py      # 包标识
├── requirements.txt # 运行时依赖
├── README.md        # 本文档
└── CHANGELOG.md     # 更新日志
```

---

## 许可证

MIT
