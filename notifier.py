"""探测与通知的工具函数。

核心思路：napcat 与 snowluma 适配器都通过「正向 WebSocket」对外提供 OneBot 11
动作接口（napcat 默认端口 7998、snowluma 默认端口 7988，鉴权用 ``?access_token=``
查询参数）。因此最通用的「伪装请求」就是：临时连一下这个 WS，发一个 ``get_status``
动作，读取返回的 ``data.online`` ——全程不向任何聊天会话发送可见消息，对群聊零打扰，
且同时兼容 napcat 和 snowluma。

WebSocket 探测依赖 ``aiohttp``（主程序运行环境已自带，snowluma 适配器亦依赖它）。
对于个别只开了 HTTP 接口、或没有任何状态接口只能发消息探测的场景，另提供 ``http``
与 ``send`` 两种回退方式。
"""

from __future__ import annotations

import asyncio
import json
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any, Dict, Optional

# Server 酱单次请求超时（秒）
_SERVERCHAN_TIMEOUT = 15.0


def _get_by_dot_path(data: Any, path: str) -> Any:
    """按点分路径从嵌套字典/列表中取值。

    Args:
        data: 已解析的 JSON 数据（通常是 dict）。
        path: 点分字段路径，例如 ``data.online`` 或 ``data.0.name``。

    Returns:
        路径对应的值；路径不存在则返回 ``None``。
    """

    if not path:
        return None
    current: Any = data
    for segment in path.split("."):
        if current is None:
            return None
        # 既支持字典键访问，也支持列表下标访问
        if isinstance(current, dict):
            current = current.get(segment)
        elif isinstance(current, list):
            try:
                current = current[int(segment)]
            except (ValueError, IndexError):
                return None
        else:
            return None
    return current


def _request_json(
    url: str,
    *,
    method: str = "GET",
    token: str = "",
    timeout: float = 10.0,
    form_data: Optional[Dict[str, str]] = None,
) -> tuple[int, Any]:
    """同步发起一次 HTTP 请求并返回 (状态码, 解析后的 JSON)。

    约定：任何网络异常、超时、非 JSON 响应都向上抛出，由调用方捕获并视为“探测失败”。

    Args:
        url: 完整请求 URL。
        method: ``GET`` 或 ``POST``。
        token: 可选的访问令牌，以 ``Authorization: Bearer <token>`` 头部发送。
        timeout: 请求超时（秒）。
        form_data: POST 时的表单字段；为 None 表示无请求体。

    Returns:
        tuple[int, Any]: (HTTP 状态码, 解析后的 JSON 对象)。响应不是 JSON 时体为 None。

    Raises:
        urllib.error.URLError: 网络不可达 / 超时 / 连接被拒。
        ValueError: 响应体不是合法 JSON。
    """

    headers: Dict[str, str] = {"Accept": "application/json"}
    if token:
        # OneBot 鉴权两种写法都带上，兼容 napcat 与其他实现
        headers["Authorization"] = f"Bearer {token}"
    body: Optional[bytes] = None
    if form_data is not None:
        body = urllib.parse.urlencode(form_data).encode("utf-8")
        headers["Content-Type"] = "application/x-www-form-urlencoded"

    request = urllib.request.Request(url=url, data=body, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        status = getattr(response, "status", 200)
        raw = response.read()
    try:
        parsed = json.loads(raw.decode("utf-8")) if raw else None
    except (json.JSONDecodeError, UnicodeDecodeError):
        parsed = None
    return status, parsed


async def probe_ws(
    host: str,
    port: int,
    *,
    token: str = "",
    action: str = "get_status",
    online_field: str = "data.online",
    timeout: float = 10.0,
    ws_path: str = "",
) -> tuple[bool, str]:
    """通过 OneBot 正向 WebSocket 执行一次动作探测，返回 (是否在线, 说明文本)。

    兼容 napcat 与 snowluma：两者均在 ``ws://host:port`` 暴露 OneBot 11 动作接口，
    鉴权用 ``?access_token=<token>`` 查询参数。默认调用 ``get_status``，读取
    ``data.online`` 字段判定 QQ 是否在线。

    判定规则：
      1. 连接 / 发送 / 接收异常或超时 → 离线。
      2. online_field 非空：取该字段，真值视为在线，假值 / 缺失视为离线。
      3. online_field 为空：响应 ``status == "ok"`` 视为在线。

    Args:
        host: 适配器主机地址。
        port: 适配器正向 WebSocket 端口。
        token: 访问令牌（未启用鉴权可留空）。
        action: 要调用的 OneBot 动作名，默认 ``get_status``。
        online_field: 在线字段路径，默认 ``data.online``。
        timeout: 连接与等待响应的总超时（秒）。
        ws_path: WebSocket 路径，一般留空。

    Returns:
        tuple[bool, str]: (在线状态, 说明文本)。
    """

    if not host or not port:
        return False, "未配置 host/port"

    # 动态导入 aiohttp：主程序运行环境自带，但避免在缺失时让整个插件加载失败
    try:
        import aiohttp
    except ImportError:
        return False, "缺少 aiohttp 依赖，无法进行 WebSocket 探测"

    # 构造 ws 地址，附带 access_token 鉴权查询参数
    base = f"ws://{host}:{port}{ws_path}"
    if token:
        base = f"{base}?{urllib.parse.urlencode({'access_token': token})}"

    echo = uuid.uuid4().hex
    session: Optional["aiohttp.ClientSession"] = None
    ws: Optional["aiohttp.ClientWebSocketResponse"] = None
    try:
        session = aiohttp.ClientSession()
        try:
            ws = await asyncio.wait_for(session.ws_connect(base), timeout=timeout)
        except asyncio.TimeoutError:
            return False, "WebSocket 连接超时"
        except Exception as exc:  # noqa: BLE001
            return False, f"WebSocket 连接失败: {exc}"

        # 发送动作请求（OneBot 11 约定：action + params + echo）
        await ws.send_json({"action": action, "params": {}, "echo": echo})

        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False, "等待响应超时"
            try:
                msg = await asyncio.wait_for(ws.receive(), timeout=remaining)
            except asyncio.TimeoutError:
                return False, "等待响应超时"

            if msg.type == aiohttp.WSMsgType.TEXT:
                try:
                    payload = msg.json()
                except (json.JSONDecodeError, ValueError):
                    continue
                # 只处理与本请求 echo 匹配的响应（忽略适配器主动推送的事件）
                if payload.get("echo") != echo:
                    continue

                status = payload.get("status")
                retcode = payload.get("retcode")
                if online_field:
                    value = _get_by_dot_path(payload, online_field)
                    if value is None:
                        return False, f"响应缺少字段 {online_field}（status={status}, retcode={retcode}）"
                    return bool(value), f"在线（{online_field}={value}）"
                # online_field 为空时，用 OneBot 状态码判定
                return status == "ok", f"status={status}, retcode={retcode}"

            elif msg.type in (
                aiohttp.WSMsgType.CLOSED,
                aiohttp.WSMsgType.CLOSE,
                aiohttp.WSMsgType.CLOSING,
                aiohttp.WSMsgType.ERROR,
            ):
                return False, "WebSocket 连接已关闭"
    finally:
        if ws is not None:
            try:
                await ws.close()
            except Exception:  # noqa: BLE001
                pass
        if session is not None:
            try:
                await session.close()
            except Exception:  # noqa: BLE001
                pass


async def probe_http(
    url: str,
    *,
    method: str,
    token: str,
    online_field: str,
    timeout: float,
) -> tuple[bool, str]:
    """执行一次 HTTP 探测，返回 (是否在线, 说明文本)。

    适用于只开了 HTTP 接口的适配器（例如 napcat 的 HTTP 服务）。判定规则：
      1. 请求异常 / 超时 / 连接失败 → 离线。
      2. 在线字段路径非空：取该字段，真值视为在线，假值视为离线。
      3. 在线字段路径为空：HTTP 状态码 200 视为在线。

    Args:
        url: 适配器状态接口 URL。
        method: 请求方法。
        token: 访问令牌。
        online_field: 在线字段路径。
        timeout: 请求超时（秒）。

    Returns:
        tuple[bool, str]: (在线状态, 说明文本，用于日志和通知正文)。
    """

    if not url:
        return False, "未配置探测 URL"

    try:
        status, payload = await asyncio.to_thread(
            _request_json, url, method=method, token=token, timeout=timeout
        )
    except urllib.error.HTTPError as exc:
        # HTTP 错误码（如 401 鉴权失败、404 接口不存在）
        return False, f"HTTP {exc.code}: {exc.reason}"
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        # 连接失败 / 超时 / 适配器进程未运行
        return False, f"连接失败: {exc.reason if hasattr(exc, 'reason') else exc}"
    except Exception as exc:  # noqa: BLE001
        return False, f"探测异常: {exc}"

    # 在线字段路径为空：仅用 HTTP 200 判定
    if not online_field:
        return (status == 200), f"HTTP {status}"

    value = _get_by_dot_path(payload, online_field)
    if value is None:
        return False, f"响应缺少字段 {online_field}（HTTP {status}）"
    return bool(value), f"在线（HTTP {status}, {online_field}={value}）"


async def push_serverchan(
    api_base: str,
    sendkey: str,
    title: str,
    desp: str,
    *,
    channel: str = "",
) -> tuple[bool, str]:
    """通过 Server 酱 Turbo Webhook 推送一条通知。

    Args:
        api_base: Server 酱接口基址，例如 ``https://sctapi.ftqq.com``。
        sendkey: SendKey（sct 开头）。
        title: 通知标题。
        desp: 通知正文（支持 Markdown）。
        channel: 可选的推送通道名，留空走默认通道。

    Returns:
        tuple[bool, str]: (是否推送成功, 说明文本)。
    """

    if not sendkey:
        return False, "未配置 SendKey"

    url = f"{api_base.rstrip('/')}/{sendkey}.send"
    form: Dict[str, str] = {"title": title[:32], "desp": desp}
    if channel:
        form["channel"] = channel

    try:
        status, payload = await asyncio.to_thread(
            _request_json,
            url,
            method="POST",
            token="",
            timeout=_SERVERCHAN_TIMEOUT,
            form_data=form,
        )
    except Exception as exc:  # noqa: BLE001
        return False, f"推送请求异常: {exc}"

    if status != 200:
        return False, f"推送失败: HTTP {status}"

    # Server 酱成功返回 {"code":0,"message":"...","data":{...}}
    code = None
    if isinstance(payload, dict):
        code = payload.get("code")
    if code in (0, None):
        return True, "推送成功"
    message = payload.get("message") if isinstance(payload, dict) else ""
    return False, f"推送失败: code={code}, message={message}"
