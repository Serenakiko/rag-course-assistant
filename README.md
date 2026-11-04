<div align="center">

# 智答 · 课程智能问答平台

**基于 RAG 的课程智能问答系统** — 以课程资料为唯一知识来源，本地大模型生成回答并标注引用出处，知识库未覆盖的内容拒答不编造。

[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110%2B-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![LangChain](https://img.shields.io/badge/LangChain-1.x-1C3C3C?logo=langchain&logoColor=white)](https://www.langchain.com/)
[![ChromaDB](https://img.shields.io/badge/ChromaDB-0.4%2B-FFD700)](https://www.trychroma.com/)
[![License](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

</div>

---

## 这是什么

高校课程答疑长期靠课堂提问、课程群与教师邮箱，普遍存在三个痛点：**重复问题多**（同一知识点每学期反复被问）、**答疑不及时**（非工作时间无人回应）、**口径不一致**（不同助教给出矛盾解释）。

智答系统把课程资料作为唯一知识来源，让大模型「只回答课程内的问题」，形成「提问 → 答疑 → 补资料 → 再答疑」的闭环。核心原则：**宁可拒答，绝不编造**。

## ✨ 功能特性

- **流式问答 + 引用溯源** — 回答附引用来源，可点开查看原文片段
- **三重拒答防线** — 距离门控、未收录标记、可答性判定，知识库没覆盖就明确拒答
- **反馈闭环** — 未命中问题自动记录，管理员按问题聚类、补资料后一键重新生成回答
- **知识库可见性隔离** — 学生只能检索公共库和自己的库，公共库对非创建者只读
- **误删可恢复** — 删除先落快照进回收站，30 天内可恢复并重新向量化
- **双角色权限** — 学生（提问、自建知识库）与管理员（内容、用户、数据概览）

## 🚀 快速开始

### 环境要求

| 依赖 | 版本 | 说明 |
| --- | --- | --- |
| Python | 3.10+ | 3.11 / 3.12 均可 |
| [Ollama](https://ollama.com) | 最新 | 本地模型推理，数据不出机器 |

### 安装与启动

```bash
# 1. 拉取模型
ollama pull qwen3:8b      # 对话模型
ollama pull bge-m3        # 向量模型

# 2. 安装依赖
python -m venv venv
venv\Scripts\activate      # macOS/Linux: source venv/bin/activate
pip install -r requirements.txt

# 3. 配置（复制模板，至少填 JWT_SECRET）
cp .env.example .env

# 4. 启动（自动打开 http://127.0.0.1:8000）
python run.py
```

> Windows 用户也可直接双击 `start.bat`（一键启动，用项目内 venv）。

### 默认账号

| 角色 | 账号 | 密码 |
| --- | --- | --- |
| 管理员 | `23040107` | `111111` |

> ⚠️ 首次登录后请立即在「个人中心」修改密码。

### 其它启动方式

```bash
python run.py --port 9000       # 指定端口
python run.py --no-browser      # 不自动打开浏览器
python run.py --reload          # 开发模式，代码修改自动重启
```

### 使用云端模型（可选）

默认使用 Ollama 本地模型。如需云端接口，在 `.env` 中设置：

```env
LLM_PROVIDER=openai
OPENAI_API_KEY=你的密钥
```

支持 DeepSeek 等 OpenAI 兼容接口，对话与向量模型可分别指定来源。

## 🧰 技术栈

| 层 | 技术 | 用途 |
| --- | --- | --- |
| Web 框架 | FastAPI + Uvicorn | REST + SSE 流式，自动生成 OpenAPI 文档 |
| ORM | SQLAlchemy 2.x | 类型化 `Mapped` 声明 |
| 元数据库 | SQLite（WAL） | 单机零部署，读写并发不阻塞 |
| 向量库 | ChromaDB | 嵌入式持久化，metadata 过滤按课程隔离 |
| 模型胶水 | LangChain（core/chroma/ollama/openai） | 统一 Ollama 与 OpenAI 接口，切换模型只改 `.env` |
| 本地推理 | Ollama | 数据不出口，8B 模型 16G 内存可跑 |
| 认证 | PyJWT + PBKDF2 | 标准库实现，免 bcrypt 编译依赖 |
| 前端 | 原生 HTML/CSS/JS | 零构建、断网可用 |

## 📂 项目结构

```
rag-course-assistant/
├── backend/                # 后端应用
│   ├── routers/            # API 路由（auth/chat/knowledge/documents/users/stats/unanswered/recycle）
│   ├── services/           # 业务服务（llm/embedding/parser/chunker/vector_store/rag_service/...）
│   ├── static/             # 前端静态资源（零构建 HTML/CSS/JS）
│   ├── config.py           # 配置加载（.env）
│   ├── models.py           # SQLAlchemy 模型
│   └── security.py         # PBKDF2 哈希 + JWT
├── alembic/                # 数据库版本化迁移
├── data/                   # 运行时数据（SQLite / Chroma / 上传文件）
├── tests/                  # pytest 用例 + node 前端回归
├── .tools/                 # 一次性运维脚本（种子数据 / 向量重建 / 阈值标定）
├── evaluate.py             # 拒答阈值标定
├── run.py                  # 启动入口
└── requirements.txt        # 依赖清单
```

## 📚 使用说明

接口文档启动后访问 <http://127.0.0.1:8000/docs>（OpenAPI 自动生成）。

- **学生端**：登录注册 → 智能问答（流式回答、引用溯源、点赞/点踩）→ 自建知识库、上传文档 → 我的反馈
- **管理员端**：数据概览 → 知识库与文档管理 → 问题反馈（聚类、补资料、重新生成回答）→ 用户管理 → 回收站

## 🧪 测试

```bash
python -m pytest              # 全部自动化用例（含 2 条 node 前端回归）
python -m ruff check .        # 静态检查
```

测试分四层：后端单元（pytest，模型用 fake）、前端纯函数（node 回归）、端到端（`.tools/kb_e2e.py`）、性能与安全。阈值标定用 `python evaluate.py`，基于 `data/golden.json` 标注集复现。

## 🔧 常见问题

- **页面显示「127.0.0.1 拒绝了连接请求」** — 服务还没启动完（冷启动约 15s），等日志出现监听信息再刷新；若报错，常见原因是 `JWT_SECRET` 未配置。
- **问答报「模型服务还没准备好」** — Ollama 未启动或无对应模型，`ollama list` 确认后 `ollama pull`。
- **回答总被拒答 / 检索异常** — 先停后端运行 `python .tools/rebuild_vectors.py` 重建向量，再 `python .tools/kb_probe.py` 看距离分布。换过向量模型必须重新标定阈值。
- **误删知识库** — 管理员登录进回收站，30 天内可恢复。
- **清空所有数据** — 删除 `data/app.db`、`data/chroma/`、`data/trash/` 后重启。

## 📄 License

[MIT](LICENSE)
