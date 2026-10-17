"""一键启动入口：python run.py [--reload] [--port 8000]

先检查关键依赖，再启动 Uvicorn 服务。
浏览器访问：http://127.0.0.1:8000
"""
from __future__ import annotations

import argparse
import socket
import sys
import webbrowser
from threading import Timer

REQUIRED_PACKAGES = ["fastapi", "uvicorn", "sqlalchemy", "chromadb", "langchain", "pypdf", "jwt", "dotenv", "multipart"]


def check_dependencies() -> bool:
    missing: list[str] = []
    for pkg in REQUIRED_PACKAGES:
        try:
            __import__(pkg)
        except ImportError:
            missing.append(pkg)
    if missing:
        print("=" * 64)
        print("缺少必要依赖：" + ", ".join(missing))
        print("请先执行安装：")
        print("    pip install -r requirements.txt")
        print("=" * 64)
        return False
    return True


def _port_in_use(host: str, port: int) -> bool:
    """端口是否已被占用：开机自启时服务可能已经在跑，重复启动直接跳过。"""
    target = "127.0.0.1" if host in ("", "0.0.0.0", "::") else host
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((target, port)) == 0


def main() -> None:
    parser = argparse.ArgumentParser(description="智答系统（基于 RAG 大模型的智能问答）")
    parser.add_argument("--reload", action="store_true", help="开发模式：代码修改后自动重启")
    parser.add_argument("--port", type=int, default=None, help="服务端口（默认读取 .env 的 PORT）")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    args = parser.parse_args()

    if not check_dependencies():
        sys.exit(1)

    import uvicorn

    from backend.config import settings

    port = args.port or settings.PORT
    url = f"http://{settings.HOST}:{port}"

    if _port_in_use(settings.HOST, port):
        print(f"  端口 {port} 上已经有服务在跑，本次启动跳过。")
        return

    print()
    print("=" * 64)
    print(f"  {settings.APP_NAME} v{settings.APP_VERSION}")
    print(f"  访问地址：{url}")
    print(f"  大模型模式：{settings.LLM_PROVIDER}")
    print(f"  默认管理员：23040107 / 111111（登录后请尽快修改密码）")
    print("  按 Ctrl+C 停止服务")
    print("=" * 64)
    print()

    if not args.no_browser:
        Timer(1.5, lambda: webbrowser.open(url)).start()

    uvicorn.run(
        "backend.main:app",
        host=settings.HOST,
        port=port,
        reload=args.reload,
        log_level="info",
    )


if __name__ == "__main__":
    main()
