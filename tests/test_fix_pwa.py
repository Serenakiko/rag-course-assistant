"""F8：PWA 外壳（manifest / service worker / 图标）与移动端接入的静态回归。

不启动浏览器：只验证 FastAPI StaticFiles 能不能把文件按正确类型吐出来、manifest 结构
是否合法、三张页面是否都挂了 manifest 与 SW 注册——真实安装、离线、手机端表现仍需人工验。
"""
from __future__ import annotations

import json
import re
import struct
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient

STATIC_DIR = Path(__file__).resolve().parents[1] / "backend" / "static"
JS_CONTENT_TYPES = ("application/javascript", "text/javascript")


def _png_size(path: Path) -> tuple[int, int]:
    """读 PNG 头里的 IHDR，避免为一条测试引入 Pillow。"""
    head = path.read_bytes()[:33]
    assert head[:8] == b"\x89PNG\r\n\x1a\n", f"{path.name} 不是合法 PNG"
    assert head[12:16] == b"IHDR", f"{path.name} 缺少 IHDR"
    width, height = struct.unpack(">II", head[16:24])
    return width, height


@pytest.fixture(scope="module")
def client():
    app = FastAPI()
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
    with TestClient(app) as test_client:
        yield test_client


def test_manifest_served_and_parseable(client):
    response = client.get("/static/manifest.webmanifest")
    assert response.status_code == 200

    data = json.loads(response.text)
    assert data["name"] == "智答系统"
    assert data["short_name"] == "智答"
    assert data["start_url"] == "/static/app.html"
    assert data["scope"] == "/"
    assert data["display"] == "standalone"
    assert data["theme_color"] == "#17505e"
    assert data["background_color"] == "#f6f5f2"

    icons = data["icons"]
    assert len(icons) >= 4
    sizes = {icon["sizes"] for icon in icons}
    assert {"192x192", "512x512"} <= sizes
    purposes = " ".join(icon.get("purpose", "") for icon in icons)
    assert "any" in purposes
    assert "maskable" in purposes


def test_manifest_icons_exist_with_correct_dimensions(client):
    data = json.loads(client.get("/static/manifest.webmanifest").text)
    for icon in data["icons"]:
        # src 形如 /static/icons/icon-192.png
        rel = icon["src"].split("/static/", 1)[1]
        path = STATIC_DIR / rel
        assert path.is_file(), f"manifest 声明的图标不存在：{icon['src']}"
        expected = int(icon["sizes"].split("x")[0])
        assert _png_size(path) == (expected, expected)


def test_service_worker_served_as_javascript(client):
    response = client.get("/static/sw.js")
    assert response.status_code == 200
    content_type = response.headers["content-type"].split(";")[0].strip()
    assert content_type in JS_CONTENT_TYPES, content_type

    body = response.text
    # 版本号会随缓存策略升级（v1 → v2 …），这里只要求存在且是 v 版本串
    assert re.search(r'const CACHE = "zhida-v\d+"', body), body[:200]
    assert "self.skipWaiting()" in body
    assert "clients.claim()" in body
    # 接口与 SSE 必须直连：fetch 监听里对 /api/ 直接 return，不缓存不代理
    assert 'url.pathname.startsWith("/api/")' in body
    assert "caches.delete" in body  # activate 清旧缓存


@pytest.mark.parametrize("page", ["index.html", "app.html", "register.html"])
def test_pages_register_manifest_and_service_worker(client, page):
    response = client.get(f"/static/{page}")
    assert response.status_code == 200
    html = response.text

    assert '<link rel="manifest" href="/static/manifest.webmanifest">' in html
    assert '<meta name="theme-color" content="#17505e">' in html
    # 注册逻辑已从内联脚本移进 sw-boot.js（CSP 要求 script-src 'self'），
    # 因此页面只需要引用它，真正的 register 调用在文件里校验。
    assert '<script src="/static/js/sw-boot.js' in html
    boot = (STATIC_DIR / "js" / "sw-boot.js").read_text(encoding="utf-8")
    assert "navigator.serviceWorker" in boot
    assert '.register("/static/sw.js"' in boot


def test_offline_fallback_page_is_precached():
    """SW 的离线回退页必须真实存在，且被列进 App shell 预缓存列表。"""
    assert (STATIC_DIR / "offline.html").is_file()
    sw = (STATIC_DIR / "sw.js").read_text(encoding="utf-8")
    assert '"/static/offline.html"' in sw
    assert '"/static/app.html"' in sw


def test_mobile_drawer_styles_present():
    """移动端补充样式落在 768 断点内，且不出现桌面端被劫持的迹象。"""
    css = (STATIC_DIR / "css" / "app.css").read_text(encoding="utf-8")
    assert "@media (max-width: 768px)" in css
    assert "body.nav-open .sidebar" in css
    assert ".nav-toggle" in css
    assert "env(safe-area-inset-bottom)" in css
