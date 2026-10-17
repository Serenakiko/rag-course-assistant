# -*- coding: utf-8 -*-
"""头像接口端到端验证脚本（仅用标准库）。"""
import json
import os
import urllib.request
import uuid
from pathlib import Path

BASE = "http://127.0.0.1:8000"
BASE_DIR = Path(__file__).resolve().parent.parent
TEST_USER = os.getenv("RAG_TEST_USER", "")
TEST_PASSWORD = os.getenv("RAG_TEST_PASSWORD", "")


def http(method, path, token=None, data=None, raw=None, ctype=None):
    headers = {}
    body = None
    if token:
        headers["Authorization"] = "Bearer " + token
    if data is not None:
        body = json.dumps(data).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if raw is not None:
        body = raw
        if ctype:
            headers["Content-Type"] = ctype
    req = urllib.request.Request(BASE + path, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def multipart(token, file_path):
    boundary = "----test" + uuid.uuid4().hex
    with open(file_path, "rb") as f:
        fdata = f.read()
    parts = []
    parts.append(f"--{boundary}\r\n".encode())
    parts.append(b'Content-Disposition: form-data; name="file"; filename="avatar.png"\r\n')
    parts.append(b"Content-Type: image/png\r\n\r\n")
    parts.append(fdata)
    parts.append(b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    body = b"".join(parts)
    return http("POST", "/api/auth/avatar", token=token, raw=body,
                ctype=f"multipart/form-data; boundary={boundary}")


# 1. 登录
if not TEST_USER or not TEST_PASSWORD:
    raise SystemExit("请先设置 RAG_TEST_USER / RAG_TEST_PASSWORD 环境变量")
status, content = http(
    "POST",
    "/api/auth/login",
    data={"username": TEST_USER, "password": TEST_PASSWORD},
)
assert status == 200, f"login failed: {status} {content}"
login = json.loads(content)
token = login["token"]
print(f"[1] 登录 OK，avatar 字段存在: {login['user'].get('avatar')!r}")

# 2. 上传头像
status, content = multipart(token, str(BASE_DIR / "data" / "demo_avatar.png"))
assert status == 200, f"upload failed: {status} {content}"
user = json.loads(content)
avatar = user["avatar"]
print(f"[2] 上传 OK -> {avatar}")

# 3. 静态访问头像
status, content = http("GET", avatar)
assert status == 200, f"image access failed: {status}"
print(f"[3] 静态访问头像 OK（HTTP {status}，{len(content)} 字节）")

# 4. 恢复默认头像，并确认旧文件已被删除
old_file = BASE_DIR / "backend" / avatar.lstrip("/")
status, content = http("PUT", "/api/auth/profile", token=token, data={"avatar": ""})
assert status == 200, f"remove failed: {status} {content}"
user = json.loads(content)
assert not old_file.exists(), f"旧头像文件还在：{old_file}"
print(f"[4] 移除 OK -> avatar={user['avatar']!r}，旧文件已删除")

print("ALL PASS")
