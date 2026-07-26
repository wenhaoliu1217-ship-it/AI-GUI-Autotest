"""使用标准库托管本地演示站。"""

from __future__ import annotations

import contextlib
import socket
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args) -> None:  # noqa: A002
        return


class DemoServer:
    """可作为上下文管理器启动/停止演示站。"""

    def __init__(self, host: str = "127.0.0.1", port: int = 8765) -> None:
        self.host = host
        self.port = port
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def start(self) -> "DemoServer":
        site_dir = Path(__file__).with_name("site")

        def handler(*args, **kwargs):
            return _QuietHandler(*args, directory=str(site_dir), **kwargs)

        self._server = ThreadingHTTPServer((self.host, self.port), handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server.server_close()
        if self._thread:
            self._thread.join(timeout=3)

    def __enter__(self) -> "DemoServer":
        return self.start()

    def __exit__(self, *_args) -> None:
        self.stop()


def find_available_port() -> int:
    with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
