# 智答系统

基于检索增强生成（RAG）的课程智能问答平台。以课程资料为唯一知识来源，由本地大模型生成回答并标注引用出处；知识库未覆盖的内容拒答并记录，由教师补充资料后闭环回复。

## 功能特性

- 流式问答，回答附带引用溯源，学生可点赞/点踩反馈
- 三重拒答防线：距离门控、未收录标记、可答性判定，宁可拒答不编造
- 未命中问题自动记录，管理员按问题聚类、补资料后一键重新生成
- 知识库可见性隔离：学生只能检索公共库和自己的库，公共库对非创建者只读
- 误删可恢复：知识库删除先落快照，30 天内可恢复并重新向量化
- 双角色：学生（提问、自建知识库）与管理员（内容、用户、数据概览）

## 快速开始

### 环境要求

- Python 3.10+
- [Ollama](https://ollama.com)（本地模型推理，数据不出机器）

### 安装与启动

```bash
# 1. 拉取模型
ollama pull qwen3.5:4b    # 对话模型
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

### 默认账号

| 角色 | 账号 | 密码 |
| --- | --- | --- |
| 管理员 | `23040107` | `111111` |

> 首次登录后请立即在「个人中心」修改密码。

### 其它启动方式

```bash
python run.py --port 9000       # 指定端口
python run.py --no-browser      # 不自动打开浏览器
python run.py --reload          # 开发模式，代码修改自动重启
```

双击 `start.bat` 也可启动。

### 使用云端模型（可选）

默认使用 Ollama 本地模型。如需云端接口，在 `.env` 中设置：

```
LLM_PROVIDER=openai
OPENAI_API_KEY=你的密钥
```

支持 DeepSeek 等 OpenAI 兼容接口。

## 技术栈

FastAPI · SQLAlchemy · SQLite · ChromaDB · 原生 HTML/CSS/JS（零构建）

## 文档

- [docs/使用说明.md](docs/使用说明.md) — 学生与管理员的操作手册
- [docs/技术设计.md](docs/技术设计.md) — 架构、RAG 设计与数据库
- [docs/开发维护.md](docs/开发维护.md) — 测试、踩坑与前端规范

接口文档启动后访问 `http://127.0.0.1:8000/docs`。
