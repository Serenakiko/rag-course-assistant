# 智答系统（基于 RAG 大模型）

一个可本地运行的全栈 Web 应用：**FastAPI + LangChain + ChromaDB** 构建 RAG 知识问答后端，**原生 HTML/CSS/JS** 构建前后端分离的网页界面，支持 **学生 / 管理员** 双角色，大模型接入支持 **Ollama 本地模型** 与 **OpenAI 兼容 API** 一键切换。

---

## 一、功能总览

| 角色 | 功能 |
|---|---|
| 学生 | 智能问答（流式输出、引用溯源）、会话管理、知识库浏览与**自助添加知识库/知识点**（可管理自己创建的内容）、个人中心 |
| 管理员 | 数据概览（用户/文档/知识点/会话统计与近 7 日趋势）、用户管理（角色调整/重置密码/删除）、知识库管理（课程文档上传 PDF/TXT/Markdown/CSV 后台自动解析分块入库 + 知识库与知识点全量维护）、对话管理（全部用户会话的查看与删除）、**反馈闭环**（统计 / 生成回答 / 通知学生） |

核心能力：
- **RAG 问答**：问题 → 向量检索 Top-K → 拼接上下文 → 大模型流式生成，回答附引用来源，可溯源。
- **多轮对话**：会话历史自动带入上下文，支持新建/切换/删除会话。
- **知识库双源**：上传文档分块向量化 + 用户维护的知识点（按知识库/课程组织），统一进 ChromaDB 检索。
- **多角色权限**：JWT 认证，接口级角色校验（学生仅可提问与浏览，管理员可维护知识与用户）。
- **双模型模式**：`LLM_PROVIDER=ollama | openai` 一键切换，向量模型独立配置。
- **反馈闭环**：问题与学习相关但知识库未覆盖时，双层拒答（距离门控 + 模型判定）不编造答案，并自动记录到"反馈"；管理员补充资料后可一键重新生成回答，学生在工作台查看解答。

---

## 二、系统架构

```
┌────────────────────────────────────────────────────────────┐
│                        浏览器（前端）                        │
│   index.html / app.html  +  CSS  +  JS（原生，无构建步骤）   │
│   登录注册 · 学生（智能问答/知识点库） · 管理员（数据概览/用户管理/知识库管理/对话管理） │
└──────────────┬───────────────────────────────┬────────────┘
               │ REST API + SSE 流式             │ 静态资源
┌──────────────▼───────────────────────────────▼────────────┐
│                    FastAPI 应用层 (backend)                 │
│  routers: auth / chat / knowledge / documents / users/stats │
│  services: llm / embedding / parser / chunker / vector/rag  │
└────────┬──────────────────────┬───────────────────┬────────┘
         │                      │                   │
┌────────▼────────┐   ┌─────────▼────────┐  ┌───────▼────────┐
│  SQLite (元数据) │   │ ChromaDB (向量库) │  │ LLM / Embedding │
│ 用户/会话/消息/  │   │ 文档分块 + 知识点  │  │ Ollama 或 API   │
│ 文档/知识点      │   │ 持久化存储         │  │ 流式生成        │
└─────────────────┘   └──────────────────┘  └────────────────┘
```

### RAG 回答流程

```
用户问题
   │
   ▼
向量化（Embedding）
   │
   ▼
ChromaDB 相似度检索 Top-K ──── 来源：课程文档分块 / 知识点
   │
   ▼
组装提示词（系统角色 + 参考资料 + 最近 N 轮对话历史）
   │
   ▼
大模型流式生成（SSE）──► 前端打字机渲染 + 引用来源折叠
   │
   ▼
回答与来源落库（SQLite）
```

### 目录结构

```
rag-course-assistant/
├── run.py                  # 一键启动入口（python run.py [--reload] [--port 8000] [--no-browser]）
├── start.bat               # Windows 双击启动
├── requirements.txt        # Python 依赖
├── .env.example            # 配置模板
├── .env                    # 本机配置（已预置 Ollama 模式）
│
├── update_knowledge_bases.py  # 知识库迁移脚本（删除旧示例知识点、创建四门课程知识库，幂等可重复运行）
├── sync_seed_documents.py     # 把 20 条种子知识点同步为「文档管理」中的文档记录（幂等，不重复入向量库）
├── rebuild_vectors.py         # 向量库重建：清空 Chroma 后从 SQLite 重新入库知识点与已就绪文档
├── kb_probe.py                # 调试探针：6 条典型问题的 Top-1 相似度分数（验证距离门控）
├── kb_calibrate.py            # 调试探针：5 类问题（强匹配/弱匹配/无关）的 Top-3 分数校准
├── kb_e2e.py                  # 端到端闭环验证：登录→建会话→三类问题 SSE→未命中记录→状态流转
│
├── backend/
│   ├── main.py             # FastAPI 入口：路由装配、静态托管、生命周期
│   ├── config.py           # 集中配置（.env 加载）
│   ├── database.py         # SQLite 引擎与会话
│   ├── models.py           # ORM：用户/文档/知识点/会话/消息/未命中反馈
│   ├── schemas.py          # Pydantic 请求响应模型
│   ├── security.py         # PBKDF2 密码哈希 + JWT
│   ├── deps.py             # 当前用户 / 角色校验依赖
│   ├── seed.py             # 初始化：默认管理员 + 四门课程知识库与 20 条示例知识点
│   ├── routers/            # auth / chat / knowledge / documents / users / stats / unanswered
│   ├── services/           # llm / embedding / document_parser / chunker / vector_store / rag_service
│   └── static/             # 前端（原生 HTML/CSS/JS，无构建步骤）
│       ├── index.html      # 登录页
│       ├── register.html   # 注册页
│       ├── app.html        # 主工作台（学生 + 管理员界面）
│       ├── css/            # common.css / auth.css / app.css
│       ├── js/             # api.js / app.js / auth.js / common.js / markdown.js + views/ 各视图
│       └── avatars/        # 用户头像
├── docs/                   # 项目文档目录（预留）
└── data/                   # 运行时生成（不入库）：app.db、chroma/、uploads/（含 avatars/）
```

---

## 三、快速启动（VSCode）

### 0. 环境要求

- Windows / macOS / Linux，**Python 3.10+**（本机 3.11/3.12 均可）
- [Ollama](https://ollama.com/)（本地模型模式需要）
- VSCode + Python 扩展

### 1. 安装 Ollama 并拉取模型（本地模式）

```bash
# 安装 Ollama 后，在终端执行：
ollama pull qwen2.5:7b    # 对话模型（回答生成）
ollama pull bge-m3        # 向量模型（中文语义检索效果好）
```

> 也可用其他模型：如 `qwen2.5:3b`（更轻量）、`bge-large-zh-v1.5`（中文向量）。修改 `.env` 中的 `OLLAMA_LLM_MODEL` / `OLLAMA_EMBED_MODEL` 即可。
> 若不使用本地模型，可跳过本步，改用 OpenAI 兼容 API（见第 4 节）。

### 2. 打开项目并安装依赖

用 VSCode 打开 `D:\rag-course-assistant` 文件夹，在终端执行：

```bash
pip install -r requirements.txt
```

> 建议先创建虚拟环境：`python -m venv venv`，激活后（Windows: `venv\Scripts\activate`）再安装。

### 3. 启动系统

```bash
python run.py
```

启动成功后自动打开浏览器：**http://127.0.0.1:8000**

或直接双击 `start.bat`。

### 4. 切换 OpenAI 兼容 API 模式（可选）

编辑 `.env`：

```ini
LLM_PROVIDER=openai
OPENAI_API_BASE=https://api.deepseek.com/v1
OPENAI_API_KEY=sk-你的密钥
OPENAI_LLM_MODEL=deepseek-chat
OPENAI_EMBED_MODEL=text-embedding-3-small
```

保存后重启 `python run.py` 即可，无需改任何代码。

### 5. 默认账号

| 账号 | 密码 | 角色 |
|---|---|---|
| 23040107 | 111111 | 管理员（登录后请立即在「个人中心」改密） |
| （注册） | - | 学生自行注册 |

---

## 四、使用指南

1. **登录/注册**：首页选择登录或注册；注册身份固定为「学生」，管理员由已有管理员在「用户管理」中调整角色。
2. **智能问答**：输入问题 → Enter 发送；回答实时流式输出，点击「📎 参考了 N 份资料」可查看引用来源；左侧可新建/切换/删除会话。
3. **知识库**：以课程为单位的知识库卡片总览，点击进入查看知识点；所有登录用户均可**添加知识库 / 知识点**，创建者本人或管理员可编辑、删除（知识点自动进入向量索引）。
4. **知识库管理**（管理员）：拖拽上传课程资料，系统自动解析、分块、向量入库；解析状态实时刷新，可查看分块详情。
5. **数据概览 / 用户管理 / 对话管理**（管理员）：查看用户/文档/知识点/会话统计与近 7 日趋势；调整角色（学生/管理员）、重置密码、删除用户；查看与删除全部用户的问答会话。

---

## 五、API 接口一览

| 方法 | 路径 | 说明 | 权限 |
|---|---|---|---|
| POST | /api/auth/register | 注册（学生） | 公开 |
| POST | /api/auth/login | 登录，返回 JWT | 公开 |
| GET | /api/auth/me | 当前用户信息 | 登录 |
| POST | /api/auth/change-password | 修改密码 | 登录 |
| PUT | /api/auth/profile | 更新姓名 | 登录 |
| GET | /api/chat/sessions | 会话列表 | 登录 |
| POST | /api/chat/ask | 流式问答（SSE） | 登录 |
| GET/PATCH/DELETE | /api/chat/sessions/{id} | 会话消息/重命名/删除 | 登录 |
| GET | /api/knowledge/bases | 知识库列表（含知识点数量/创建者） | 登录 |
| POST/PUT/DELETE | /api/knowledge/bases(/{id}) | 知识库增删改（删除级联其下知识点） | 登录（创建者/管理员可改删） |
| GET/POST/PUT/DELETE | /api/knowledge/points | 知识点增删改查 | 查=登录，写=登录（创建者/管理员可改删） |
| GET | /api/knowledge/courses | 课程名称列表 | 登录 |
| POST | /api/documents/upload | 上传文档（后台解析） | 管理员 |
| GET/DELETE | /api/documents(/{id}) | 文档列表/详情/删除 | 管理员 |
| GET | /api/admin/users | 用户列表 | 管理员 |
| PATCH/DELETE | /api/admin/users/{id} | 修改/删除用户 | 管理员 |
| GET | /api/stats/admin/stats | 系统统计 | 管理员 |
| GET | /api/chat/admin/conversations | 全部会话列表（含用户名/消息数） | 管理员 |
| GET/DELETE | /api/chat/admin/conversations/{id} | 会话消息查看/删除 | 管理员 |
| GET | /api/llm/status | 大模型连接状态探测 | 公开 |
| GET | /api/admin/unanswered | 反馈列表（可按状态过滤） | 管理员 |
| GET | /api/admin/unanswered/stats | 反馈统计（总数/待处理/已处理/今日/课程分布） | 管理员 |
| POST | /api/admin/unanswered/{id}/regenerate | 补充资料后重新生成回答（成功置为已解决） | 管理员 |
| PATCH/DELETE | /api/admin/unanswered/{id} | 标记状态 / 删除记录 | 管理员 |
| GET | /api/chat/unanswered/mine | 我的已解答问题（学生工作台通知） | 登录 |

接口文档：启动后访问 **http://127.0.0.1:8000/docs**（Swagger UI）。

---

## 六、常见问题（FAQ）

**Q1：问答返回「回答生成失败：model xxx not found」**
Ollama 未拉取对应模型。执行 `ollama pull qwen2.5:7b` 和 `ollama pull bge-m3`，重启服务。

**Q2：头部状态显示「无法连接 Ollama」**
确认 Ollama 已启动（托盘图标存在）。测试：浏览器访问 `http://localhost:11434`。

**Q3：上传 PDF 显示「解析失败」**
可能是扫描件（无文本层），需 OCR 处理后再上传；或改用 TXT/Markdown 格式。

**Q4：示例知识点没有被创建**
首次启动时若 Ollama 未就绪，示例知识点会自动回滚（避免库不一致）。启动 Ollama 后删除 `data/app.db` 再重启即可重新初始化。

**Q5：如何清空所有数据？**
停止服务后删除 `data/` 下的 `app.db` 和 `chroma/` 目录，重启即可回到初始状态。

**Q6：端口被占用？**
修改 `.env` 中的 `PORT`，或启动时指定：`python run.py --port 9000`。

**Q7：回答总被拒答 / 向量库疑似损坏怎么办？**
先停掉后端，运行 `python rebuild_vectors.py`：它会清空 `data/chroma/`，然后从 SQLite 里把全部知识点和已就绪文档重新分块、向量化入库。完成后重启 `python run.py`。
若想先看距离门控是否过严，可在后端运行时另开终端执行 `python kb_probe.py`，观察「库里有答案」与「库里没答案」两类问题的相似度分数差，再据此调整 `.env` 中的检索阈值（如需）。

---

## 七、运维与调试脚本

根目录下的脚本均为一次性运维/验证工具，**不参与系统运行时**，按需手动执行：

| 脚本 | 用途 | 何时使用 |
|---|---|---|
| `python update_knowledge_bases.py` | 迁移：删除旧示例知识点，重建四门课程知识库（幂等） | 知识库结构升级后 |
| `python sync_seed_documents.py` | 把 20 条种子知识点在「文档管理」页同步为对应文档记录（幂等，**不重复入向量库**） | 首次部署后、「文档数」与「知识点数」对不上时 |
| `python rebuild_vectors.py` | 清空 Chroma，从 SQLite 重建全部向量索引 | 向量库损坏、换了向量模型、检索结果异常时（先停后端） |
| `python kb_probe.py` | 打印 6 条典型问题的 Top-1 相似度分数 | 调试距离门控/拒答阈值 |
| `python kb_calibrate.py` | 打印 5 类问题（强匹配/弱匹配/语义近但库无/完全无关/宽泛）的 Top-3 分数 | 校准拒答策略边界 |
| `python kb_e2e.py` | 端到端跑通：登录 → 建会话 → 三类问题 SSE 流式回答 → 检查未命中反馈记录 → 状态流转 | 改完 RAG 链路后做回归验证（需后端已启动） |

---

## 八、技术要点（毕设说明）

- **RAG 核心**：`backend/services/rag_service.py` —— 检索增强生成完整链路，LCEL 流式管道。
- **文档处理**：`document_parser.py`（PDF/TXT/MD/CSV）+ `chunker.py`（递归字符分块，中文标点感知，块重叠 120 字符）。
- **向量库**：`vector_store.py` —— ChromaDB 持久化，文档与知识点双源同一集合，按元数据过滤删除。
- **双模型抽象**：`llm_service.py` / `embedding_service.py` —— 工厂模式，配置驱动，零代码切换。
- **流式协议**：SSE（`text/event-stream`），前端 `fetch + ReadableStream` 解析，支持中途停止。
- **安全**：PBKDF2-SHA256 密码哈希、JWT 无状态认证、接口角色校验、HTML 转义防 XSS。
