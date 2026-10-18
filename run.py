"""一键启动入口：python run.py [--reload] [--port 8000]

先检查关键依赖，再启动 Uvicorn 服务。
浏览器访问：http://127.0.0.1:8000
"""
from __future__ import annotations

import argparse
import socket
import sys
import time
import webbrowser
from threading import Thread

# 用代码里真正 import 的模块名：requirements.txt 装的是 langchain-core / -chroma，
# 没有 langchain 这个总包，之前拿它自检会让干净的虚拟环境直接启动不了
REQUIRED_PACKAGES = [
    "fastapi", "uvicorn", "sqlalchemy", "chromadb",
    "langchain_core", "langchain_chroma", "langchain_ollama", "langchain_openai",
    "pypdf", "jwt", "dotenv", "multipart",
]


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


def _open_browser_when_listening(url: str, host: str, port: int, timeout: float = 30.0) -> None:
    """等端口真的开始监听再打开浏览器。

    以前是固定 1.5 秒后直接开：模型还没加载完、或者启动直接失败（默认管理员密码
    没改又把 HOST 放开到 0.0.0.0），用户先看到一个打不开的页面，还得回头翻报错。
    服务起不来的话，这个线程自己超时退出，不会开出一个废页面。
    """
    target = "127.0.0.1" if host in ("", "0.0.0.0", "::") else host
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.3)
            if sock.connect_ex((target, port)) == 0:
                webbrowser.open(url)
                return
        time.sleep(0.4)


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
    print(f"  默认管理员账号：23040107（首次登录后请立即修改密码）")
    print("  按 Ctrl+C 停止服务")
    print("=" * 64)
    print()

    if not args.no_browser:
        Thread(
            target=_open_browser_when_listening,
            args=(url, settings.HOST, port),
            daemon=True,
        ).start()

    # 必须是单进程：会话的「新请求顶掉旧请求」靠进程内字典（chat._gen_seq），
    # 多 worker 时每个 worker 各记一份，会退化成两条答案、顺序错乱。
    try:
        uvicorn.run(
            "backend.main:app",
            host=settings.HOST,
            port=port,
            reload=args.reload,
            log_level="info",
        )
    except SystemExit as exc:
        # 启动失败（典型：默认管理员密码没改又把 HOST 放开到 0.0.0.0）时，
        # uvicorn 打印完原因就 sys.exit(3)。如果服务是被隐藏窗口拉起来的
        # （计划任务 / 自己的自启脚本），那段报错没人看得见，这里补一条能照着做的提示。
        if exc.code:
            print()
            print("=" * 64)
            print("  服务没能启动，具体原因见上面的报错。")
            print("  如果是「管理员仍在使用默认密码 111111」：")
            print("    1) 把 .env 里的 HOST 改回 127.0.0.1，启动并登录  23040107 / 111111")
            print("    2) 在「个人中心」改掉密码，再把 HOST 改成 0.0.0.0")
            print("=" * 64)
        raise


if __name__ == "__main__":
    main()
