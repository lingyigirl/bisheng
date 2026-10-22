"""接入指南的地址来源：运维配置优先，探测值兜底。

后端进程**无法可靠知道**"对接方该用哪个地址"——反向代理、API 网关与 HTTPS 终止
都在本进程之外，`Host` 头经过 Vite/nginx 改写后也不再可信。所以这里只提供两件事：

- `configured`：运维在 `open_api.public_base_url` 配的权威值；
- `detected`：本机出口 IP + 监听端口，仅在没配置时给开发/直连拓扑兜底。

前端据此决定默认显示哪个地址（配置 > 管理员已存 > 探测/控制台源），并在界面上
标注来源；探测值永远不覆盖运维配置。
"""

import socket

from fastapi import Request


def primary_ipv4() -> str | None:
    """本机默认路由网卡地址；取不到（或只有回环）时返回 None。

    先做一次 UDP connect 让内核选路——UDP 不握手，不会真的发包；无默认路由时
    回退到主机名解析。两种方式都可能失败，按"取不到"处理，由调用方回退控制台源。
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            ip = sock.getsockname()[0]
        if ip and not ip.startswith("127."):
            return ip
    except OSError:
        pass
    try:
        ip = socket.gethostbyname(socket.gethostname())
    except OSError:
        return None
    return None if not ip or ip.startswith("127.") else ip


def request_listen_port(request: Request | None) -> int | None:
    """ASGI 监听端口。

    代理场景下 `Host` 是代理的地址，但 `scope["server"]` 仍是本机 uvicorn 监听的
    端口——这正是探测值要拼的端口。Unix socket / 测试桩下取不到时返回 None。
    """
    if request is None or not hasattr(request, "scope"):
        return None
    server = request.scope.get("server")
    if server and len(server) >= 2 and server[1]:
        return int(server[1])
    return None


def detect_local_base_url(ip: str | None, port: int | None) -> str | None:
    """拼探测值；IP 或端口任一缺失即放弃（宁可不给，也不给半截地址）。"""
    if not ip or not port:
        return None
    return f"http://{ip}:{port}"


def resolve_base_url_sources(configured: str, request: Request | None) -> dict:
    """返回给管理端的两个候选项；`configured` 为空串表示运维未配置。"""
    return {
        "configured": configured or "",
        "detected": detect_local_base_url(primary_ipv4(), request_listen_port(request)),
    }
