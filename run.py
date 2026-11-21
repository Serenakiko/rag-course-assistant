"""一键启动入口：python run.py [--reload] [--port 8000]

先检查关键依赖，再启动 Uvicorn 服务。
浏览器访问：http://127.0.0.1:8000
"""
from __future__ import annotations

import argparse
import json
import socket
import sys
import time
import webbrowser
from threading import Thread
from urllib.request import urlopen

from backend.seed_data import DEFAULT_ADMIN

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


def _ollama_models(base_url: str, timeout: float = 3.0) -> set[str] | None:
    """问 Ollama 要已安装的模型列表；连不上返回 None（区分「没启动」和「没装模型」）。"""
    try:
        with urlopen(f"{base_url.rstrip('/')}/api/tags", timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception:  # noqa: BLE001  连不上 / 不是 Ollama
        return None
    models = payload.get("models") or []
    return {str(item.get("name") or "").strip() for item in models if item.get("name")}


def _model_present(installed: set[str], wanted: str) -> bool:
    """Ollama 的 tag 可能带 / 不带 :latest，按「名字片段」宽松比对。"""
    wanted = wanted.strip()
    if wanted in installed:
        return True
    base = wanted.split(":", 1)[0]
    return any(name == wanted or name.split(":", 1)[0] == base for name in installed)


def check_models(skip: bool = False) -> bool:
    """启动前确认大模型 / 向量模型就绪。

    以前是等用户提问才 404 / 500，错误信息只有一行 traceback；这里提前说清楚
    缺什么、怎么装。Ollama 连不上时只警告不退出（可能用远程地址，或稍后才启动）。
    """
    from backend.config import settings

    provider = (settings.LLM_PROVIDER or "ollama").lower()
    if provider == "openai":
        if not settings.OPENAI_API_KEY:
            print("=" * 64)
            print("LLM_PROVIDER=openai 但没有配置 OPENAI_API_KEY。")
            print("请在 .env 里填写 OPENAI_API_KEY，或改用 LLM_PROVIDER=ollama。")
            print("=" * 64)
            return False
        print(f"  大模型：OpenAI 兼容接口（{settings.OPENAI_LLM_MODEL}）")
        return True

    wanted = [settings.OLLAMA_LLM_MODEL, settings.OLLAMA_EMBED_MODEL]
    installed = _ollama_models(settings.OLLAMA_BASE_URL)
    if installed is None:
        print()
        print(f"  [警告] 连不上 Ollama（{settings.OLLAMA_BASE_URL}）。")
        print("  对话和检索都会失败，请先启动 Ollama，或检查 .env 里的 OLLAMA_BASE_URL。")
        print("  已跳过模型检查继续启动（可用 --skip-model-check 关掉这条提示）。")
        return True
    missing = [name for name in wanted if not _model_present(installed, name)]
    if missing:
        print("=" * 64)
        print("缺少模型：" + "、".join(missing))
        print("请先下载：")
        for name in missing:
            print(f"    ollama pull {name}")
        print("下载完成后重新运行 python run.py")
        print("=" * 64)
        return False
    print(f"  Ollama 模型就绪：{settings.OLLAMA_LLM_MODEL} / {settings.OLLAMA_EMBED_MODEL}")
    return True


def _port_in_use(host: str, port: int) -> bool:
    """端口是否已被占用：开机自启时服务可能已经在跑，重复启动直接跳过。"""
    target = "127.0.0.1" if host in ("", "0.0.0.0", "::") else host
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((target, port)) == 0


def _is_our_service(host: str, port: int) -> bool:
    """端口占用时通过健康检查确认是否为本应用。"""
    from backend.config import settings

    target = "127.0.0.1" if host in ("", "0.0.0.0", "::") else host
    try:
        with urlopen(f"http://{target}:{port}/api/health", timeout=2.0) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception:  # noqa: BLE001
        return False
    return payload.get("app") == settings.APP_NAME


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
    # Windows 控制台/重定向文件默认用 GBK，中文日志会和 UTF-8 混在一起变成乱码。
    # 入口统一改成 UTF-8，uvicorn/logging 继承同一套 stdio。
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                pass
    parser = argparse.ArgumentParser(description="智答系统（基于 RAG 大模型的智能问答）")
    parser.add_argument("--reload", action="store_true", help="开发模式：代码修改后自动重启")
    parser.add_argument("--port", type=int, default=None, help="服务端口（默认读取 .env 的 PORT）")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    parser.add_argument(
        "--skip-model-check",
        action="store_true",
        help="跳过启动前的大模型 / 向量模型检查",
    )
    args = parser.parse_args()

    if not check_dependencies():
        sys.exit(1)

    # 模型没装 / API key 没填时，早点失败比让用户先看到首页再收到 500 更好
    if not args.skip_model_check and not check_models():
        sys.exit(1)

    import uvicorn

    from backend.config import settings

    port = args.port or settings.PORT
    browser_host = "127.0.0.1" if settings.HOST in ("", "0.0.0.0", "::") else settings.HOST
    url = f"http://{browser_host}:{port}"

    if _port_in_use(settings.HOST, port):
        if _is_our_service(settings.HOST, port):
            print(f"  端口 {port} 上已经有本系统在跑，本次启动跳过。")
            print(f"  访问地址：{url}")
            if not args.no_browser:
                webbrowser.open(url)
            return
        print(f"  端口 {port} 已被其他程序占用，请换一个端口，例如：python run.py --port 8001")
        sys.exit(1)

    print()
    print("=" * 64)
    print(f"  {settings.APP_NAME} v{settings.APP_VERSION}")
    print(f"  访问地址：{url}")
    print(f"  大模型模式：{settings.LLM_PROVIDER}")
    print(f"  默认管理员账号：{DEFAULT_ADMIN[0]}（首次登录后请立即修改密码）")
    print("  按 Ctrl+C 停止服务")
    print("=" * 64)
    print()

    if not args.no_browser:
        Thread(
            target=_open_browser_when_listening,
            args=(url, settings.HOST, port),
            daemon=True,
        ).start()

    # 会话的「新请求顶掉旧请求」靠 conversations.gen_seq 落库（见 chat._bump_gen_seq），
    # 多 worker 也能正确工作；但本机模型一次只吐一路 token，实际并发由服务端的
    # 生成闸门（rag_service 的 _SLOT_COUNT）控制，多 worker 并不会变快，反而每个
    # worker 各建一套 SQLite 连接、Chroma 句柄。默认仍单进程。
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
            print("  如果是「管理员仍在使用默认密码」：")
            print(
                f"    1) 把 .env 里的 HOST 改回 127.0.0.1，用 {DEFAULT_ADMIN[0]} 登录"
                "（初始密码见 data/admin_initial_credentials.txt）"
            )
            print("    2) 在「个人中心」改掉密码，再把 HOST 改成 0.0.0.0")
            print("    （凭据文件只有首次启动生成的初始密码，已改密后忽略即可）")
            print("=" * 64)
        raise


if __name__ == "__main__":
    main()
