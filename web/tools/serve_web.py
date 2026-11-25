"""预览外壳：一个只读的本地静态服务器。

    python web/tools/serve_web.py            # 默认 8000，自动找空闲端口
    python web/tools/serve_web.py --port 8080 --no-open

只依赖标准库：不装框架、不起 watcher、不写文件。
它是"壳"的工具，所以放在 web/ 下——scripts/ 归后端。
"""

from __future__ import annotations

import argparse
import functools
import http.server
import socket
import socketserver
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class Handler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self) -> None:  # noqa: D102 - 开发期别让浏览器缓存骗人
        self.send_header("Cache-Control", "no-store, max-age=0")
        super().end_headers()

    def log_message(self, fmt: str, *args: object) -> None:  # noqa: D102 - 安静一点
        if self.path.endswith((".html", "/")) or self.path.endswith(".css"):
            super().log_message(fmt, *args)


def free_port(preferred: int) -> int:
    for port in range(preferred, preferred + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise SystemExit(f"从 {preferred} 起连续 20 个端口都被占用了，用 --port 换一个区间")


def main() -> None:
    parser = argparse.ArgumentParser(description="预览 web/ 外壳")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--no-open", action="store_true", help="不自动打开浏览器")
    args = parser.parse_args()

    port = free_port(args.port)
    url = f"http://127.0.0.1:{port}/web/"
    handler = functools.partial(Handler, directory=str(ROOT))

    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("127.0.0.1", port), handler) as httpd:
        print(f"外壳跑在 {url}")
        print("Ctrl+C 停止")
        if not args.no_open:
            webbrowser.open(url)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n已停止")


if __name__ == "__main__":
    main()
