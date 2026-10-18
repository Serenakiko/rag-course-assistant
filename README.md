# 智答系统（基于 RAG 大模型）

一个可本地运行的全栈 Web 应用：**FastAPI + SQLAlchemy + SQLite + ChromaDB** 构建 RAG 问答后端，**原生 HTML / CSS / JS** 构建无需构建步骤的网页界面，支持 **学生 / 管理员** 双角色。对话模型默认走 **Ollama `qwen3.5:4b`**，向量模型 **`bge-m3`**，也可以通过 `.env` 切到任意 OpenAI 兼容接口。

---

## 一、功能总览

侧边栏按「角色 → 分组」组织，学生与管理员各有一套入口：

| 角色 | 菜单 | 功能 |
|---|---|---|
| 学生 | 首页 | 欢迎区 + 四张统计卡（我的会话 / 提问消息 / 我的文档 / 知识库，可点击跳转）+ 核心功能入口 + 我的反馈列表；**整页一屏放下，不用向下拉** |
| 学生 | 智能问答 | 流式回答、引用溯源、会话列表（最近 20 条 + 服务端标题搜索 + 重命名 / 删除）、停止生成（生成中不能并发提问）、中断会话回来自动续答 |
| 学生 | 对话历史 | 自己全部会话，服务端分页 + 标题搜索、查看问答内容、删除 |
| 学生 | 我的反馈 | 待处理 / 已解决分区，统计卡 + Tab 切换，新回答标「新回答」，点开才标已读；**三个 Tab 都有底部分页（每页 10 条，可切 20 条/页）** |
| 学生 | 知识库 | 卡片总览（按名称 / 简介搜索，固定每页 12 条 + 底部分页，不显示「第 x/y 页」、也没有每页条数下拉）+ 新建知识库；进入库内维护知识点、上传文档 |
| 学生 | 我的文档 | 自己上传的文档：服务端搜索 + 分页（只显示「共 N 条」，没有「第 x/y 页」）、**查看文件内容**（分块或原文）、失败可重新解析、删除、直接上传（选目标知识库） |
| 学生 | 个人中心 | 查看 / 编辑资料、更换头像、修改密码 |
| 管理员 | 数据概览 | 用户 / 知识库 / 文档 / 今日提问四张卡（可点击进对应页面）、近 7 日提问趋势、知识库文档占比环形图、最近 4 条未命中问题（点击跳转问题反馈）；**整页一屏放下，不用向下拉** |
| 管理员 | 知识库与文档 | 一个页面两个 tab：**知识库**（表格 + 服务端分页：描述 / 文档数 / 分块数 / 创建者，编辑弹窗内可直接传文档，删除进回收站）、**全部文档**（服务端搜索 + 分页 / **查看文件内容** / 重新解析 / 删除） |
| 管理员 | 智能问答 | 与学生同款的问答界面，用于自测 |
| 管理员 | 问题反馈 | 统计 + 全部 / 处理中 / 已解决 / **按问题聚类** 四个 tab，生成回答、状态流转、删除；四个 tab 都有分页 |
| 管理员 | 对话历史 | 全部会话（标题 / 消息数 / 提问者），按标题或提问者搜索，进详情看完整问答，可删除 |
| 管理员 | 用户管理 | 搜索、新增用户、角色调整、重置密码、删除（级联清理其会话与消息） |
| 管理员 | 回收站 | 删除过的知识库快照，按名称 / 删除人搜索，显示剩余可恢复天数（30 天），恢复或彻底删除 |
| 两者 | 个人中心 | 同上 |

核心能力：

- **RAG 问答**：问题 → 向量检索 Top-K → 距离门控 → 组装上下文 → 大模型流式生成，回答附引用来源，可点开查看原文；生成中按钮变「停止生成」，这一轮没结束前不能发新问题（防两轮并发、上一问丢答案）。
- **多轮对话**：最近 6 轮、总长度 6000 字以内自动带入上下文；当前对话有内容时新建会二次确认；被中断的会话重新打开会自动补生成（后端不重复记录提问）。
- **知识库双来源**：上传文档分块向量化 + 手工维护的知识点，统一进同一个 Chroma 集合检索。
- **可见性隔离**：管理员看到全部知识库；学生只看到「管理员发布的 + 自己创建的」，检索、列表、知识点增改、文档上传、反馈重生成共用同一套规则（`kb_scope.py`）。
- **回收站**：删除知识库前先落快照（库信息 + 知识点正文 + 文档源文件移到 `data/trash/<id>/`），30 天内可一键恢复，恢复时重建知识点与分块并重新向量化，重名自动加「（恢复N）」后缀；启动时顺手清理过期快照。
- **问题聚类**：把「红黑树怎么插入」和「红黑树的插入过程」这类换说法的问题并成一组，按人数排序，优先用向量余弦（默认 0.78），向量模型不可用时退回中文二元组 Jaccard（默认 0.55），问题向量带缓存表。
- **反馈闭环**：课程相关但知识库没覆盖的问题自动记录（同一学生同一问题只留一条）；管理员补完资料后可一键重新生成回答，学生在首页和「我的反馈」看到结果，未读高亮、点开即读。
- **权限与安全**：JWT 认证、接口级角色校验、登录失败限流（同一账号 + IP 10 分钟内失败 10 次锁定）、注册限流（同一 IP 10 分钟 5 次）、头像上传按文件头 + 大小校验、Markdown 渲染先转义后解析、CORS 只放行本机来源。
- **双模型模式**：`LLM_PROVIDER=ollama | openai` 一键切换，对话与向量模型可分别指向不同来源（`EMBEDDING_PROVIDER`）。
- **超时与失败保护**：单次回答上限 180 秒（`LLM_TIMEOUT`），超时 / 模型报错 / 手动停止都不会写成正式回答（只留一条「这条提问没有生成出回答」的提示，且不标中断，重新打开不会自动重跑）；文档解析失败可「重新解析」，不必删掉重传。
- **单轮串行**：同一会话在生成期间不能发新问题（前端 `activeSeq` 占位 + 后端 `_gen_seq` 兜底），避免两轮并行把上一问的答案顶掉。
- **并发闸门**：同时最多 3 路生成（`_llm_slots()`，流式问答和管理员「生成回答」共用同一个闸门），其余请求排队期间每 10 秒收一个心跳保活——本机模型一次只吐一路 token，不限并发时排队的请求会一起撞 180 秒超时。排队最多等 120 秒（`_QUEUE_MAX_WAIT`）：学生那条回「当前提问人数较多」，管理员那条回 `busy`（前端的 fetch 没有超时，不能无限期挂着），都不会让人一直转到前端总超时才看到一句指错方向的「生成超时」。同一会话里被新提问顶掉的旧请求，会在排队期间和拿到槽位后各查一次序号，不再占一路模型白跑。
- **分页统一**：所有列表页都是服务端分页（`limit` / `offset` + 总数），底部条由 `Common.renderPagination` 统一渲染：管理员端一律不显示「第 x/y 页」，学生端按页面传 `hidePageInfo`；正文滚动条统一收成 8px 细条 + 透明轨道（内容再多也不冒那条灰底，但溢出时会露出一根浅色把手当提示）。
- **默认管理员保护**：`23040107 / 111111` 只在本机监听时可用；把 `HOST` 放开到 `0.0.0.0` 且密码没改时服务直接拒绝启动。
- **零构建前端**：原生 JS 视图模块，服务端直接托管静态文件，没有打包步骤；改完要提 `app.html` 里的 `?v=` 再刷新（见开发记录 17）；≤640px 自动切抽屉导航。分页条这块有 node 纯函数回归（`tests/frontend_pagination.mjs`）。

---

## 二、系统架构

```
┌──────────────────────────────────────────────────────────────┐
│                          浏览器（前端）                       │
│  index.html / register.html / app.html + CSS + JS（无构建）   │
│  学生：首页 / 问答 / 历史 / 反馈 / 知识库 / 我的文档 / 账号    │
│  管理员：概览 / 知识库与文档 / 问答 / 反馈 / 历史 / 用户 / 回收站│
└───────────────┬───────────────────────────────┬──────────────┘
                │ REST API + SSE 流式            │ 静态资源
┌───────────────▼───────────────────────────────▼──────────────┐
│                     FastAPI 应用层（backend）                  │
│  routers: auth / chat / knowledge / documents / users / stats │
│           unanswered / recycle                                │
│  services: llm / embedding / parser / chunker / vector / rag   │
│            kb_scope / recycle                                 │
└────────┬──────────────────────┬────────────────────┬──────────┘
         │                      │                    │
┌────────▼────────┐   ┌─────────▼────────┐  ┌────────▼─────────┐
│ SQLite（元数据） │   │ ChromaDB（向量）  │  │ Ollama / 兼容 API │
│ 用户/会话/消息/  │   │ 文档分块 + 知识点 │  │ qwen3.5:4b 对话   │
│ 文档/知识点/反馈 │   │ 持久化存储        │  │ bge-m3 向量       │
│ 回收站快照       │   └──────────────────┘  └──────────────────┘
└─────────────────┘
         │
┌────────▼─────────┐
│ data/trash/      │ 删除知识库时被移走的源文件（30 天后清理）
└──────────────────┘
```

### RAG 回答流程

```
用户问题
   │
   ▼
按可见范围确定课程（管理员：全部；学生：可见库）→ 向量化
   │
   ▼
ChromaDB 相似度检索 Top-K ──── 来源：课程文档分块 / 知识点
   │
   ├─ 最相似距离 > 0.85 ──► 判定「知识库未收录」，记录反馈
   │                        （是否与课程相关：关键词优先，课程词和「考研/面试」
   │                          这类词同时命中时问模型一次，含糊的宁可多问一遍；
   │                          「这门课讲了什么」走概览模式，不做未收录判定）
   ▼
组装提示词（系统规则 + 参考资料 + 最近 6 轮 / 6000 字内历史）
   │
   ▼
排队闸门（同时最多 3 路生成，其余每 10 秒发心跳；排队超 120 秒直接回「人多」）
   │
   ▼
大模型流式生成（SSE）──► 前端逐字渲染 + 引用来源
   │
   ├─ 超过 LLM_TIMEOUT（180s）──► 停止并提示重试，不写成已回答
   ▼
回答 + 引用来源 + 是否中断（interrupted）落库（SQLite）
```

超时、模型报错、一个字都没吐出来就结束这几种情况都不写正式回答，只落一条「（这条提问没有生成出回答，可能被中断了，重新发一次试试。）」的提示，且不标 `interrupted` —— 所以重新打开会话不会自动重跑，避免反复失败。

### 目录结构

```
rag-course-assistant/
├── run.py                    # 启动入口（--reload / --port / --no-browser；端口占用时自动跳过）
├── start.bat                 # Windows 双击启动
├── requirements.txt
├── .env.example              # 配置模板
├── .env                      # 本机配置（不入库）
│
├── backend/
│   ├── main.py               # 路由装配、静态托管（带 no-cache）、启动迁移、过期回收站清理
│   ├── config.py             # 集中配置（.env 加载、JWT_SECRET 校验）
│   ├── database.py           # SQLite 引擎、外键约束、增量迁移
│   ├── models.py             # ORM：用户/文档/知识点/会话/消息/反馈/回收站/问题向量
│   ├── schemas.py            # 请求响应模型与字段校验
│   ├── security.py           # PBKDF2 密码哈希 + JWT
│   ├── deps.py               # 当前用户 / 角色依赖
│   ├── seed.py               # 默认管理员 + 四门课程知识库与示例知识点
│   ├── routers/              # auth / chat / knowledge / documents / users / stats / unanswered / recycle
│   ├── services/             # llm_service / embedding_service / document_parser
│   │                         # chunker / vector_store / rag_service / kb_scope / recycle
│   └── static/
│       ├── index.html        # 登录页
│       ├── register.html     # 注册页
│       ├── app.html          # 主界面（学生 + 管理员）
│       ├── css/              # common.css / auth.css / app.css
│       ├── js/               # api.js / app.js / auth.js / common.js / markdown.js
│       │   └── views/        # workbench / knowledge / chat / feedback / profile
│       │                     # admin（概览 / 用户 / 知识库表格 / 对话历史）
│       │                     # admin-feedback（问题反馈，独立模块）
│       │                     # documents / history / content / recycle
│       ├── img/              # 品牌图标、登录页背景
│       └── avatars/          # 用户上传头像（运行时生成，不入库）
├── tests/
│   ├── test_core.py          # pytest 单元测试（不依赖网络与正式数据）
│   └── frontend_pagination.mjs  # 前端分页条纯函数回归（node 跑，pytest 会顺带执行）
├── data/                     # 运行时生成（不入库）：app.db、chroma/、uploads/、trash/
└── .tools/                   # 一次性运维/调试脚本（入库）+ 本地日志（不入库）
    ├── update_knowledge_bases.py
    ├── sync_seed_documents.py
    ├── rebuild_vectors.py
    ├── seed_demo.py
    ├── kb_probe.py
    ├── kb_calibrate.py
    ├── kb_e2e.py
    ├── make_demo_avatar.py
    └── verify_avatar.py
```

---

## 三、快速启动

### 0. 环境要求

- Windows / macOS / Linux，**Python 3.10+**（本机 3.11）
- [Ollama](https://ollama.com/)（本地模型模式需要）
- 首次启动会加载向量模型，**冷启动约 60 秒**，期间访问会提示连接被拒绝

### 1. 安装并拉取模型

```bash
ollama pull qwen3.5:4b    # 对话模型
ollama pull bge-m3        # 向量模型（中文语义检索）
```

> 4B 模型在 16G 内存的机器上够用，首次回答约 20–30 秒（含模型加载），热态 10–15 秒。想更快可换 `qwen3.5:2b`，改 `.env` 的 `OLLAMA_LLM_MODEL` 即可。
> qwen3.5 默认会先"思考"再回答，代码里已显式关闭思考（`ChatOllama(reasoning=False)`），否则回答正文会被思考过程挤空。

### 2. 安装依赖

```bash
pip install -r requirements.txt
```

建议先建虚拟环境：`python -m venv venv`，激活后（Windows: `venv\Scripts\activate`）再安装。

### 3. 启动

```bash
python run.py
```

启动成功后自动打开 **http://127.0.0.1:8000**（是**等端口真的开始监听**才开，启动失败不会甩一个打不开的页面给你）。也可以双击 `start.bat`，或 `python run.py --no-browser` 不自动开浏览器。

仓库**不自带开机自启**，也不带任何自启脚本：服务只在你手动 `python run.py` / 双击 `start.bat` 时启动。真想要登录自启，就自己写个隐藏窗口的启动脚本（`wscript` + `.vbs` 或计划任务），输出重定向到 `.tools\server-start.log`，再放进「启动」文件夹（`shell:startup`）。**排查自启残留要看两处**：`shell:startup` 和 `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`，只删一处，另一处照样会把它拉起来。

既然没有自启，遇到端口占用就别绕圈：

```powershell
# 8000 被谁占着？
Get-NetTCPConnection -LocalPort 8000 -State Listen |
  ForEach-Object { Get-CimInstance Win32_Process -Filter "ProcessId=$($_.OwningProcess)" } |
  Select-Object ProcessId, CommandLine
# 停掉它
Get-NetTCPConnection -LocalPort 8000 -State Listen | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }
```

> 想让它在登录后自己起来，就自己写个隐藏窗口的启动脚本（`wscript` + `.vbs` 或计划任务），输出重定向到 `.tools\server-start.log`，再放进 `shell:startup`。**排查问题先看两处**：`shell:startup` 文件夹和 `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`——只删一处，另一处照样会把它拉起来。

### 4. 切换到 OpenAI 兼容 API（可选）

```ini
LLM_PROVIDER=openai
OPENAI_API_BASE=https://api.deepseek.com/v1
OPENAI_API_KEY=sk-你的密钥
OPENAI_LLM_MODEL=deepseek-chat
OPENAI_EMBED_MODEL=text-embedding-3-small
```

保存后重启即可。**换向量模型后必须重新标定 `UNMATCHED_DISTANCE`**，并运行 `python .tools/rebuild_vectors.py`。

### 5. 默认账号

| 账号 | 密码 | 角色 |
|---|---|---|
| 23040107 | 111111 | 管理员（登录后请在个人中心修改） |
| （注册） | - | 学生自行注册，账号固定 8 位数字 |

> `JWT_SECRET` 没有默认值。缺失、过短或仍是模板占位值时服务会拒绝启动，按提示生成一个 64 位随机串写入 `.env` 即可。
> 管理员仍用 `111111` 时：默认只监听 `127.0.0.1` 的情况下启动日志打印一次提醒（不改你的密码）；一旦把 `HOST` 改成 `0.0.0.0`，服务会**直接拒绝启动**——已知密码 + 局域网可达就是后门，按报错提示先改密码再放开监听（见开发记录 28）。

### 6. 配置项一览

全部来自项目根目录的 `.env`（模板 `.env.example`），**改完重启生效**。除 `JWT_SECRET` 外都有默认值，不写也能跑。

| 键 | 默认值 | 说明 |
|---|---|---|
| `HOST` / `PORT` | `127.0.0.1` / `8000` | 监听地址与端口；`HOST` 不是本机回环地址、管理员又还在用 `111111` 时拒绝启动 |
| `JWT_SECRET` | 无（必填） | 少于 32 位或仍是模板占位值会直接拒绝启动 |
| `JWT_EXPIRE_HOURS` | `72` | 登录令牌有效期 |
| `UTC_OFFSET_HOURS` | `8` | 统计口径时区（「今日」「近 7 日」按这个算）；`.env.example` 里没列，需要时手动加 |
| `LLM_PROVIDER` | `ollama` | `ollama` / `openai` |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | 本地模型服务地址 |
| `OLLAMA_LLM_MODEL` | `qwen3.5:4b` | 对话模型 |
| `OLLAMA_EMBED_MODEL` | `bge-m3` | 向量模型 |
| `OLLAMA_TEMPERATURE` | `0.3` | 采样温度 |
| `OPENAI_API_BASE` | `https://api.deepseek.com/v1` | 兼容 API 地址 |
| `OPENAI_API_KEY` | 空 | 兼容 API 密钥 |
| `OPENAI_LLM_MODEL` / `OPENAI_EMBED_MODEL` | `deepseek-chat` / `text-embedding-3-small` | 兼容 API 的模型名 |
| `OPENAI_TEMPERATURE` | `0.3` | 采样温度 |
| `EMBEDDING_PROVIDER` | 空（跟随 `LLM_PROVIDER`） | 向量模型单独指到另一家 |
| `CHUNK_SIZE` / `CHUNK_OVERLAP` | `800` / `120` | 文档分块长度与重叠字数 |
| `TOP_K` | `5` | 检索返回条数 |
| `COLLECTION_NAME` | `course_knowledge` | Chroma 集合名（改名等于换库，要重建向量） |
| `LLM_TIMEOUT` | `180` | 单次回答超时（秒） |
| `UNMATCHED_DISTANCE` | `0.85` | 拒答阈值（L2 距离）；换向量模型必须重新标定 |
| `ANSWERABILITY_CHECK` | `false` | 检索后二次可答性判定，默认关，原因见「开发记录 8」 |

写死在 `backend/config.py`、不开放给 `.env` 的：`HISTORY_LIMIT=6`（拼进提示词的历史轮数）、`HISTORY_CHAR_BUDGET=6000`（历史文本总长度上限）、`ROLES`。

其它散在各自模块里的上限（改的时候记得去对应文件）：

| 项 | 值 | 位置 |
|---|---|---|
| 登录失败限流 | 同账号 + IP 10 分钟 10 次（`_LOGIN_WINDOW/_LOGIN_MAX_FAILS`） | `routers/auth.py` |
| 登录失败限流（IP 维度） | 同一 IP 10 分钟 30 次（`_IP_MAX_FAILS`），防止换账号名逐个试 | `routers/auth.py` |
| 注册限流 | 同 IP 10 分钟 5 次（`_REG_WINDOW/_REG_MAX`） | `routers/auth.py` |
| 头像大小 | 2MB，按文件头校验真实类型 | `routers/auth.py` |
| 单文档大小 | 20MB | `routers/documents.py` |
| 回收站保留 | 30 天（`RETENTION_DAYS`） | `services/recycle.py` |
| 侧边栏会话条数 | 20（`SIDEBAR_LIMIT`），搜索结果最多 50 | `static/js/views/chat.js` |
| 同时生成路数 | 3（`_SLOT_COUNT`，闸门由 `_llm_slots()` 按事件循环提供）；排队期间每 10 秒发一次心跳（`_QUEUE_PING_SECONDS`），最多等 120 秒（`_QUEUE_MAX_WAIT`） | `services/rag_service.py` |
| 前端流式超时 | 空闲 200 秒（必须大于 `LLM_TIMEOUT`）、总时长 320 秒（= 排队 120 + 生成 180 + 余量） | `static/js/views/chat.js` |
| 列表每页条数 | 默认 10，可切 20；知识库卡片固定 12（不给下拉） | 各 `static/js/views/*.js` |
| 列表接口单页上限 | 普通列表 100，管理端列表 200，聚类一次最多 500 | `routers/*.py` |
| 抽屉导航断点 | ≤640px | `static/css/app.css` |

---

## 四、使用指南

### 学生

1. **登录 / 注册**：账号必须是 8 位数字，规则前后端一致；密码至少 6 位，两项不符会逐项弹窗提示。
2. **首页**：顶部四张统计卡（我的会话、提问消息、我的文档、知识库）都能点进去；下面是我的反馈列表，没有记录时也会给出说明。整页固定一屏，反馈多了在列表内部滚动，不会把页面顶出滚动条。
3. **智能问答**：Enter 发送、Shift+Enter 换行；回答流式输出，生成中发送按钮变「停止生成」，此时按 Enter 也不会发新问题；想连着问就等这一轮答完或先点「停止生成」；点「参考了 N 份资料」展开引用原文。左侧会话可重命名、删除；会话多了侧边栏只取**最近 20 条**（服务端分页，不再把全部会话拉下来），上面的搜索框按标题搜索（服务端过滤，输入后 250ms 自动查），底部有「查看全部 N 条 ›」直接进「对话历史」页；正在看的旧会话被挤出前 20 条时会钉在最上面。「+ 新对话」在当前对话有内容时会先确认，空对话直接提示。关掉页面再回来，没生成完的那一轮会自动续上。
4. **知识库**：卡片总览支持按「名称 / 简介」搜索；进入某个库后，知识点卡片显示的是截断正文，**点卡片可弹窗看完整内容**；新建知识库后会**直接弹出上传资料**的窗口；进入某个库可上传文档、维护知识点。**能看见不等于能写**：知识库、知识点、文档的增改删只限创建者本人或管理员，公共库对其他人是只读的（否则任何人都能往全班共用库里塞错误内容，别人的回答会引用它）。
5. **我的文档**：只显示自己上传的文档，搜索走服务端（回车或点「搜索」），列表按 10 / 20 条分页；点「查看」能看文件内容（有向量分块的按分块列，没有分块的直接读源文件原文），状态为「失败」的可点「重新解析」，源文件还在就不用重传。正在解析的文档在任何一页都会让列表自动轮询刷新。
6. **我的反馈**：待处理 / 已解决分区，**每页 10 条（可在 10 / 20 之间切换）**，三个 Tab 底部都有分页条（共 N 条 + 每页条数 + ‹ 当前页 ›，页码永远只出现一个，点左右箭头才换页；不显示「第 x/y 页」）；上方统计卡按全量计数，整页一屏放下。管理员补充资料并生成回答后，条目会标「新回答」，点开这条才标为已读。
7. **个人中心**：默认只读，点「编辑资料」后输入框才可编辑，同时出现「取消 / 保存」；点侧边栏头像可直接换头像。退出登录点一下弹确认，**长按 0.65 秒直接退出**。

### 管理员

1. **数据概览**：四张统计卡点击可进对应页面，下面接近 7 日提问趋势、知识库文档占比环形图（中心显示知识点总数，图例带数量与百分比，空知识库不显示）和最新反馈。整页自适应一屏，图表随窗口高度收缩，反馈列表放不下时内部滚动，不需要向下拉。
2. **知识库与文档**：知识库 tab 是表格视图，可按「名称 / 描述 / 创建者」搜索，编辑弹窗里能顺手传文档（pdf / txt / md / csv，单个 20MB 内），删除后进回收站；全部文档 tab 负责查看与清理，支持按「文件名 / 知识库 / 类型」搜索、点「查看」读文件内容、重新解析失败文档。两张表都是服务端分页（默认 10 条/页，可切 20），搜索也是服务端过滤。
3. **问题反馈**：全部 / 处理中 / 已解决 / **按问题聚类**，四个 tab 共用一套每页条数，默认**每页 10 条**（可在 10 / 20 之间切换），底部固定分页样式完全一致；管理员端所有列表页的分页条都只显示「共 N 条」，不带「第 x/y 页」。聚类页把换说法的问题并组、按人数排序，直接告诉你「哪类问题没人答上来」；补资料后点「生成回答」，命中则置为已解决并重置学生端未读。
4. **对话历史**：按「会话标题 / 提问者」搜索，服务端分页，点详情看完整问答，可删除会话。学生端同样走服务端分页 + 标题搜索。
5. **用户管理**：按「账号 / 用户名 / 角色」搜索；可新增、调整角色、重置密码、删除用户（删除会级联清理其会话与消息）。
6. **回收站**：列出被删知识库的快照，显示知识点数、文档数、删除人和剩余天数；「恢复」会重建知识点、搬回源文件并重新生成向量，重名自动加「（恢复N）」；「彻底删除」会连同归档文件一起清掉，不可恢复。

---

## 五、API 接口一览

| 方法 | 路径 | 说明 | 权限 |
|---|---|---|---|
| POST | /api/auth/register | 注册（固定学生角色，IP 限流） | 公开 |
| POST | /api/auth/login | 登录，返回 JWT（失败限流） | 公开 |
| GET | /api/auth/me | 当前用户 | 登录 |
| PUT | /api/auth/profile | 更新用户名 / 头像 | 登录 |
| POST | /api/auth/avatar | 上传头像（校验文件头与大小） | 登录 |
| POST | /api/auth/change-password | 修改密码 | 登录 |
| GET | /api/chat/sessions | 我的会话列表，返回 `{total, items}`（`q` 标题搜索、`limit` / `offset` 分页、`order=asc` 从旧到新） | 登录 |
| POST | /api/chat/sessions | 新建会话 | 登录 |
| GET / PATCH / DELETE | /api/chat/sessions/{id} | 会话消息 / 重命名 / 删除 | 登录 |
| POST | /api/chat/ask | 流式问答（SSE，`regenerate` 表示重生成；同一时间最多 3 路生成，其余排队等心跳，排满 120 秒回 `error` 事件） | 登录 |
| GET | /api/chat/unanswered/mine | 我的反馈（`status` 只能是 `pending` / `resolved`，否则 400；`limit` / `offset` 分页，按提交时间倒序，返回 `{total, pending, resolved, items}`） | 登录 |
| POST | /api/chat/unanswered/{id}/read | 标记反馈已读 | 登录 |
| GET | /api/knowledge/bases | 知识库列表（按可见性过滤，含文档数 / 知识点数，下拉用） | 登录 |
| GET | /api/knowledge/bases/page | 知识库分页 + 搜索（`keyword` + `field=name\|description\|creator`） | 登录 |
| POST / PUT / DELETE | /api/knowledge/bases(/{id}) | 知识库增删改（删除先落回收站快照） | 登录（删除限创建者 / 管理员） |
| GET | /api/knowledge/points/page | 知识点分页 + 搜索（`course` + `keyword`） | 登录 |
| POST / PUT / DELETE | /api/knowledge/points(/{id}) | 知识点增删改（写入校验目标库可见性） | 登录 |
| POST | /api/documents/upload | 上传文档（后台解析分块） | 登录（课程须可见） |
| GET | /api/documents | 文档列表（`keyword` + `field=filename\|course\|type` + 分页，搜索词里的 `%` `_` 按普通字符处理；返回 `{total, processing, items}`，`processing` 按可见范围全量统计、不受搜索影响；管理员全部 / 学生仅自己上传） | 登录 |
| POST | /api/documents/{id}/reparse | 重新解析失败的文档 | 管理员或上传者 |
| GET / DELETE | /api/documents/{id} | 文档详情：`chunks` 是向量分块，没有分块时 `preview` 返回源文件原文 / 删除（连同向量） | 管理员或上传者 |
| GET | /api/admin/users | 用户列表（`q` + `field` + 分页） | 管理员 |
| GET | /api/admin/users/options | 用户下拉选项 | 管理员 |
| POST | /api/admin/users | 新增用户 | 管理员 |
| PATCH / DELETE | /api/admin/users/{id} | 修改 / 删除用户 | 管理员 |
| GET | /api/stats/me | 我的统计（首页） | 登录 |
| GET | /api/stats/admin/stats | 系统统计（含趋势与占比） | 管理员 |
| GET | /api/chat/admin/conversations | 全部会话（分页） | 管理员 |
| GET | /api/chat/admin/conversations/{id}/messages | 会话消息 | 管理员 |
| DELETE | /api/chat/admin/conversations/{id} | 删除会话 | 管理员 |
| GET | /api/admin/unanswered | 反馈列表（状态过滤 + `limit` / `offset`） | 管理员 |
| GET | /api/admin/unanswered/clusters | 反馈聚类（`status` + `threshold`） | 管理员 |
| GET | /api/admin/unanswered/stats | 反馈统计（含按课程分布） | 管理员 |
| POST | /api/admin/unanswered/{id}/regenerate | 重新生成回答（限定提问者可见范围） | 管理员 |
| PATCH / DELETE | /api/admin/unanswered/{id} | 标记状态 / 删除 | 管理员 |
| GET | /api/admin/recycle | 回收站列表（`q` + `field=name\|user`） | 管理员 |
| POST | /api/admin/recycle/{id}/restore | 恢复知识库（重新向量化） | 管理员 |
| DELETE | /api/admin/recycle/{id} | 彻底删除快照与归档文件 | 管理员 |
| GET | /api/health | 健康检查 | 公开 |

接口文档：启动后访问 **http://127.0.0.1:8000/docs**。

---

## 六、常见问题

**Q1：页面显示「127.0.0.1 拒绝了连接请求」**
服务还没启动完。冷启动要一分钟左右（加载 Chroma 和依赖），等日志出现监听信息再刷新。若一直如此，检查终端报错，常见原因是 `JWT_SECRET` 未配置。

**Q2：问答报「模型服务还没准备好」**
说明 Ollama 里没有对应模型，或 Ollama 未启动。执行 `ollama list` 确认有 `qwen3.5:4b` 和 `bge-m3`；没有就 `ollama pull`。具体原因在浏览器控制台（`[llm]` 开头）和服务端日志里。

**Q3：Ollama 明明装了模型，`ollama list` 却是空的**
Ollama 桌面端的「模型位置」指向了空目录。打开 Ollama 设置看这个路径（本机是 `D:\Ollama\models`，默认安装则在 `C:\Users\<你>\.ollama\models`），改回真实目录后重启 Ollama。

**Q4：上传 PDF 提示解析失败**
多为扫描件（没有文本层），需要先 OCR；或者改用 TXT / Markdown。如果文件本身没问题只是当时模型没就绪，直接在「全部文档 / 我的文档」里点「重新解析」，不用重传。

**Q5：示例知识点没有创建**
首次启动时如果 Ollama 未就绪，种子知识点会回滚。启动 Ollama 后删除 `data/app.db` 再重启即可重新初始化。

**Q6：如何清空所有数据**
停止服务，删除 `data/app.db`、`data/chroma/`、`data/trash/`，重启回到初始状态。

**Q7：端口被占用**
`run.py` 检测到端口已占用会直接跳过启动（防止开机自启重复起进程）。确实要换端口：`python run.py --port 9000` 或改 `.env` 的 `PORT`。

**Q8：回答总被拒答 / 检索结果异常**
先停后端，运行 `python .tools/rebuild_vectors.py` 重建向量；再运行 `python .tools/kb_probe.py` 看距离分布是否仍在 0.85 两侧。换过向量模型就必须重新标定阈值。

**Q9：误删了知识库怎么找回**
用管理员登录 → 回收站。30 天内都能恢复，恢复会重建知识点与文档分块并重新向量化；超过保留期的会在下次启动时被自动清掉。

**Q10：为什么回收站里没有某个知识点 / 文档**
回收站只收「整个知识库被删除」的快照。单独删知识点、删文档不落快照，删了就没了。

**Q11：聚类结果和预期不一样**
默认用向量余弦 0.78 分组；如果向量模型没就绪会退回中文二元组相似度（0.55），粒度会粗一些。需要更松/更紧可传 `threshold`（向量 0.5–0.98，二元组 0.2–0.95）。

**Q12：回答中途卡住 / 提示超时**
单次生成上限 `LLM_TIMEOUT=180` 秒，超时会停止并提示重试，这一轮不会写入正式回答，只会留一条「这条提问没有生成出回答」的提示。首轮回答慢通常是模型在加载，第二次同样的问题会快很多。

**Q13：能不能一边等回答一边问下一个问题**
不能，这是故意的。同一会话里两轮生成并行时，后一轮会在后端把前一轮顶掉（`_gen_seq` 机制），前一轮就只剩一个永远转圈的气泡、库里也只留下提问没有答案。所以生成没结束时发送按钮只负责「停止生成」，Enter 也发不出新问题；要连问就先点停止。（前端用 `activeSeq` 从点击那一刻就占位，连建连阶段都挡住。）

**Q14：把 `HOST` 改成 `0.0.0.0` 后启动直接报错退出**
这是故意的。管理员还是默认密码 `111111` 时不允许对局域网开放（登录限流只挡暴力破解，挡不住已知密码）。按报错三步走：`HOST` 改回 `127.0.0.1` 启动 → 用 `23040107 / 111111` 登录并在「个人中心」改密码 → 再把 `HOST` 改成 `0.0.0.0`。

**Q15：几个人同时提问，后面的人一直显示「生成中」，然后报「当前提问人数较多」**
本机模型一次只能吐一路 token，所以后端限制同时 3 路生成（`rag_service._llm_slots()`），其余请求排队；排队期间每 10 秒有一个心跳保活，不会中途被前端空闲超时（200 秒）掐掉。管理员点「生成回答」走的是同一个闸门，排不上队会直接回「后台正忙」，不会让按钮一直转。
排队最多等 120 秒（`_QUEUE_MAX_WAIT`）：超过就直接回「当前提问人数较多，请稍后再发一次」，这一轮不生成（会留一条「这条提问没有生成出回答」）。`LLM_TIMEOUT=180` 只管模型自己那一段，**不含排队时间**，所以别再看到「生成超时」就以为模型挂了——排队过久是另一个提示。想彻底不等就换更快或支持并发的模型服务。

**Q16：会话记录太多怎么办**
侧边栏只渲染最近 20 条（`SIDEBAR_LIMIT`），不会再一长串摞下来：
- 找具体某次对话：用侧边栏上面的搜索框，按标题搜索（服务端过滤，防抖 250ms）；
- 更多 / 管理：点底部「查看全部 N 条 ›」进「对话历史」页，那里有分页、搜索，每条都能查看或删除；
- 正在看的旧会话如果被挤出前 20 条，会自动钉在列表最上面，不会“消失”。

---

## 七、运维与调试脚本

`.tools/` 下的脚本均为一次性工具，不参与运行时，请在项目根目录执行：

| 脚本 | 用途 | 何时使用 |
|---|---|---|
| `python .tools/update_knowledge_bases.py` | 重建四门课程知识库（幂等） | 知识库结构升级后 |
| `python .tools/sync_seed_documents.py` | 种子知识点同步为文档记录（不重复入向量库） | 文档数与知识点数对不上时 |
| `python .tools/rebuild_vectors.py` | 清空 Chroma，全部重建 | 换向量模型 / 索引损坏（先停后端） |
| `python .tools/seed_demo.py` | 造演示数据（用户 / 知识库 / 知识点 / 文档 / 会话 / 反馈），加 `--no-vectors` 只写库不调 embedding | 演示、截图、压测前 |
| `python .tools/kb_probe.py` | 典型问题 Top-1 距离 | 调拒答阈值 |
| `python .tools/kb_calibrate.py` | 多类问题 Top-3 距离 | 标定阈值边界 |
| `python .tools/kb_e2e.py` | 登录 → 问答 → 反馈 → 状态流转 | 改完 RAG 链路做回归 |
| `python .tools/make_demo_avatar.py` | 生成演示头像图（需 PIL） | 演示、截图前 |
| `python .tools/verify_avatar.py` | 登录后上传头像，校验落盘与访问路径 | 改头像逻辑后回归 |

> 运行 `kb_e2e.py` / `verify_avatar.py` 前先设置 `RAG_TEST_USER` 与 `RAG_TEST_PASSWORD` 环境变量，脚本不再写死默认密码。
> 单元测试：`python -m pytest -q`（`pytest` 已列在 `requirements.txt`；`tests/test_core.py` 覆盖可见性与写入守卫、未命中判定、头像路径、限流清理，以及几处踩过的坑：历史消息带花括号时的提示词装配、迟到回答不落库、解析失败不联动删文档、会话列表分页与搜索、重新生成失败退回待处理、LIKE 通配符转义。不依赖网络）
>
> 前端也有一条纯函数回归：`node tests/frontend_pagination.mjs`（分页条这块所有列表页共用，「管理员端又冒出第 x/y 页」「每页条数下拉不见了」这类回退不用开浏览器就能挡住）。它被 `pytest` 顺带跑一遍，本机没有 `node` 时自动跳过。

---

## 八、开发记录与踩坑

这一节记真正卡过的地方，以及最后为什么这么定。

**1. 为什么是 Chroma + SQLite 两套存储**

知识点要能改能删、要按创建者鉴权、要知道谁改的，这些在向量库里做很别扭。最后 SQLite 存元数据，Chroma 只存文本和检索字段，两边靠 `doc_id` / `point_id` 关联。代价是增删要两边各来一次，所以 `vector_store.py` 的删除都按元数据过滤，没有按 id 删。

**2. 分块参数是试出来的**

块太短会把知识点拦腰截断，检索回来是半句话。最后定成 800 字、重叠 120；`chunker.py` 除了按段落和换行切，还排了中文标点的优先级，避免整段没有句号的课件被硬切。

**3. 拒答这一层是被模型逼出来的**

最早没有距离门控，模型拿自己的知识开始编。最典型的是问「红黑树」，库里只有二叉树，它照样把红黑树的性质讲了一遍。

所以加了 `UNMATCHED_DISTANCE`：最相似资料的距离超过阈值就直接判「知识库没收录」，拒答文案由系统拼。阈值是拿 18 条问题实测标出来的：

| 分组 | 最低 | 最高 |
|---|---|---|
| 库里有答案（栈、TCP、进程线程、死锁、快排、哈希表等） | 0.4631 | 0.7749 |
| 库无但话题沾边（红黑树、B+ 树、AVL、协程等） | 0.9133 | 1.0523 |
| 完全无关（天气、考公） | 1.1882 | 1.2134 |

中间 **0.78 ~ 0.91 的空带**就是阈值该待的位置，取 **0.85**。最初拍脑袋的 1.10 落在「该拒答」档里，那 6 个沾边问题一个都没拦住。

**4. 阈值和向量模型绑死**

同一批问题换向量模型后分数整体平移，阈值会把所有问题都拒掉。所以阈值放在 `.env` 里，换模型必须重新标定并重建向量。

**5. 拒答把反馈列表搞脏了**

「今天天气怎么样」「考公怎么选岗」也会进反馈，管理员一屏全是垃圾。所以拒答前先过 `is_course_related()`：明显课程术语直接相关，明显无关的直接不记录，剩下的交给模型二分类。

**6. 「这门课讲了什么」被门控误伤**

这类问题和资料的字面相似度很低，但它恰恰最该答。所以单独做了 `OVERVIEW_PROMPT`：不做未收录判定，只汇总介绍检索到的资料。

**7. 阈值 0.85 的两处代价**

宽泛问题（「帮我总结这门课的核心框架」）实测 1.09，会被判未覆盖；话题混搭的问题（「红黑树和二叉查找树有什么区别」）仍可能拿到 0.73 的距离被放行，然后模型照着二叉树编红黑树。

**8. 第三层防线在 3B 上不成立（默认关闭）**

曾尝试在检索后再问一次「资料里有没有直接讲这个问题」。当时用 qwen2.5:3b 试了 6 种提示词：要么恒定回答「没有」（连 Top-1 就是答案的「什么是哈希表」都被判未收录），要么在明明有依据时也说「无」。只给一句话的极简资料判定正确，给 3 段真实检索结果就失灵。所以 `ANSWERABILITY_CHECK=false`，代码保留。要启用，建议把这一步单独指给 7B 以上的模型。

**9. qwen3.5 的思考模式会吃空回答**

换成 qwen3.5:4b 后，第一次实测返回的 `response` 是空字符串——模型把预算全花在 `thinking` 里了。解决方式是 `ChatOllama(reasoning=False)`；同时把 `num_ctx` 显式设成 8192，否则多轮对话加资料很容易把系统规则挤出 4096 的默认窗口。

**10. Ollama 升级后模型"消失"**

桌面端某次升级把模型目录解析到了 `D:\` 根目录，`ollama list` 空空如也，日志里是 `total blobs: 0`。模型其实都在 `C:\Users\<你>\.ollama\models`。结论：Ollama 的模型目录以桌面端设置为准，不是环境变量；升级后先看设置里的存储位置。

**11. 拉模型卡在 IPv6**

Ollama 下载器连 Cloudflare R2 时走 IPv6，某些网络环境下会一直 `SynSent`。最后用 `curl -4` 手动下载层文件 + 校验 SHA256 + 写回 manifest 才装上。遇到「pulling manifest」长时间不动，先怀疑 IPv6。

**12. 回收站用快照，不做软删除**

一开始想在知识库表上加 `deleted_at` 做软删除，问题是删除知识库本来就要清向量、删文档、搬源文件，软删除会把这些「要不要真删」的分支渗进检索、列表、统计所有查询里。最后改成独立的 `RecycleItem` 快照表：删之前存一份库信息 + 知识点正文 + 文档元信息，源文件移到 `data/trash/<id>/`，主表照常真删。恢复时按快照重建并重新向量化，库里没有的东西不会「借尸还魂」。代价是恢复要重跑 embedding，几秒量级，能接受。

**13. 未命中问题曾经按主键重排，误删过记录**

最早删一条反馈就把其余记录的 id 前移，前端或脚本手里的旧编号立刻指向别人，按编号删就打偏了。现在主键一律不动，只重排展示用 `display_no`。

**14. 聚类不能只靠字面匹配**

中文不分词，按字面相似度合并会漏掉「红黑树怎么插入」和「红黑树的插入过程」这种换说法。所以优先用 bge-m3 余弦（0.78），向量不可用时才退回字符二元组 Jaccard（0.55）——宁可分组粗一点，也不要因为模型挂了就整页空白。问题向量单独存一张缓存表，同一句话只算一次。

**15. 失败不能写成「已解决」**

管理员点「生成回答」时，超时或空回答曾经也会清状态，结果学生端显示已解决但内容是空的。现在 `generate_answer` 明确返回 `error` 字段，超时 / 空回答保持待处理、不覆盖已有答案，并提示「检查模型服务后再试」。

**16. 知识库和文档本来是两个菜单**

补资料时要在「知识库管理」和「文档管理」之间来回跳，管理员反馈很烦。现在合并成一个「知识库与文档」页，两个 tab 各管一摊，上传入口收进编辑弹窗，旧路由（`kb` / `documents`）仍能落到对应 tab。

**17. 改了前端却不生效：静态资源被缓存**

`app.html` 里每个 js/css 都带 `?v=NN`。改了文件不提版本号，浏览器会继续用缓存里的旧代码——排查半天以为是逻辑没改对（并发提问那次就是）。现在 `main.py` 给 `/static` 统一加了 `Cache-Control: no-cache`（StaticFiles 带 ETag，命中就走 304，不费流量），但**改 js/css 仍然要同步提 `?v=`**：已经打开过的页面不会主动回源，而且版本号本身能标记“这次改过”。

**18. 「已解决」的反馈重新生成失败后变成空白答案**

管理员在「已解决」tab 点「重新生成」，如果知识库里仍然没收录，`regenerate` 的 `else` 分支只清了 `q.answer`，`status` 还留在 `resolved`：学生端看到「已解决 + 新回答」角标、点开是空白，管理员之前写好的答案也一起没了。现在 `else` 分支同时把 `status` 打回 `pending`、`answered_at` 清空——「还在补资料」和「已经答上了」不能共用同一个状态。

**19. 学生端反馈只取最新 50 条，统计数字跟着少算**

`/api/chat/unanswered/mine` 原来写死 `.limit(50)`，前端按 10 条/页只能翻 5 页，第 51 条以后学生永远看不到；「待处理 / 已解决」两个数字还是从这 50 条里 `filter().length` 算出来的，跟管理员端总数对不上。现在改成服务端分页，两个计数单独按全量查。改返回形状时还漏了一处：首页「我的反馈」卡片（`views/workbench.js`）仍把响应当数组 `.slice()`，一渲染就抛 `TypeError`、被 catch 成「反馈加载失败」。接口从数组改成 `{total, items}` 这类对象时，务必把所有调用方一起扫一遍（这次就是漏了第二处）。

**20. 聊天页整页能上下滑，输入框被顶出屏幕**

`.chat-layout` 之前写死 `height: calc(100vh - 108px)`，但实际可用高度是 `100vh - 60(header) - 24 - 76(上下内边距) = 100vh - 160`，多算 52px 就把 `.main-content` 撑出了滚动条。现在改成 `height: 100%` 跟随父容器 + `min-height: 0` 允许被压缩，只有 `.chat-messages` 自己滚；聊天页容器的 76px 下内边距（本来是给列表页固定分页条留的）也改成了四周对称。

**21. 滚动条**：`common.css` 里原来是 `* { scrollbar-width: none }` + `*::-webkit-scrollbar { width: 0; height: 0 }`，全站都不画滚动条，页面右侧不会冒灰条、排版也不会因为滚动条出现而横向抖一下。**但这等于把「这页还能往下」的唯一视觉线索一起藏了**——鼠标用户经常以为到底了。现在改成 8px 细条 + 透明轨道（`scrollbar-color: #d8d4cb transparent`）：不溢出时什么都不显示，真溢出时露出一根浅色把手当提示。

顺带记一笔配色：页面底色 `#f6f5f2` 和消息区 `#f7f6f3` 只差一个色阶，卡片原来的 `--shadow`（`0 1px 2px rgba(...,.05)`）几乎等于没有，看着就是“卡片和背景糊成一片”。现在卡片给了 1px 边框 + `--shadow-lg`，聊天页容器 `.main-content:has(.chat-layout)` 底色压到 `#e9e4db`，卡片横向居中、最多 1280px（靠 `:has()` 只作用于这一页，别的页面一点没动）。

**22. 同一会话允许两轮并发 = 上一问丢答案**

`abortController` 是 `await API.streamAsk()` 之后才赋值的，可按钮那时已经变成「停止生成」——这段建连空窗期点按钮走的是「发送」分支，于是第二轮发起来了。后端 `_gen_seq` 发现同会话有新请求会把旧一轮作废（不落库），旧流的回调又被序号检查挡掉、连收尾都跳过：库里留下一条没答案的提问，界面上那个气泡永远转圈。现在 `streamAsk` 同步返回 `{ controller, done }`（建连阶段也能 abort，错误统一走 `onError`），`send()` 开头就用 `activeSeq` 同步占位，并发提问在点击那一刻就被挡住。

**23. 上一轮回答里有花括号，下一轮就报「回答生成失败」**

多轮历史原来是 `("user", "文本")` 元组直接塞给 `ChatPromptTemplate.from_messages()`，LangChain 会把每条消息当模板解析：上一轮回答里出现 `if (x) { ... }` 或 JSON，格式化时就 `KeyError: ' '`，用户看到的是「回答生成失败：' '」——报错信息完全指不到真正原因。实测第一轮问「C 的 if 语句怎么写」、第二轮必挂。现在历史与学生问题都改成 `HumanMessage` / `AIMessage` 对象（内容不再参与模板解析），系统提示仍保留 `{context}` 占位。`tests/test_core.py` 里加了回归测试。

**24. Chroma 的 where 平铺两个键会直接抛错**

文档详情的分块是这么查的：`collection.get(where={"source_type": "document", "doc_id": str(doc.id)})`。Chroma 只认「一个操作符」，两个键平铺会抛 `ValueError: Expected where to have exactly one operator`；外面正好套了 `try/except` 兜成空列表，于是文档详情永远显示「没有分块」——前端还以为是自己没调对。多条件必须写 `{"$and": [...]}`（`vector_store.delete_document` 早就这么写了，这里是漏了一处）。顺带补了原文兜底：种子文档（`.tools/sync_seed_documents.py` 生成）本来就只有记录没有分块，现在没分块时直接读源文件返回 `preview`。

**25. 一个问题打一次模型，并发不限 = 全批超时**

每个人提问都会打一次模型（未命中还要多打一次「是否课程相关」）。本机 3B 模型一次只吐一路 token，30 个人同时点发送时，排在后面的请求等不到第一个 token，180 秒后整批超时——学生看到的是「回答生成超时」，而这恰恰是最需要它的时候。现在 `stream_answer` 外面套了一层信号量（`_llm_slots()`，默认 `_SLOT_COUNT = 3`）：同时最多 3 路生成，排队的请求每 10 秒收一个 `ping` 保活（前端按「收到数据」重置空闲计时；客户端空闲超时给到 200 秒，必须大于后端的 180 秒，否则模型憋着不出 token 的 120 秒里前端先跑掉），槽位在 `finally` 里释放，里层生成器 `aclose()`——客户端断开时模型流不会吊在那儿。
第一批改完还有两个洞：① 排队没有上限，排队时间又不计入 `LLM_TIMEOUT`，排久了学生是在前端总超时（`totalTimeout`）被掐掉的，看到的却是「回答生成超时」，方向全错——现在排队满 120 秒（`_QUEUE_MAX_WAIT`）就回一条明确的 `error`，前端总超时也放宽到 320 秒（120 排队 + 180 生成 + 余量）；② 非流式的 `generate_answer`（管理员「生成回答」）没进闸门，补完资料点一下就是第 4 路——现在它和流式共用 `_llm_slots()`（`_generate_answer_locked` 是真身），并且最多等 `_QUEUE_MAX_WAIT` 秒就回 `busy`：管理员那条是普通 fetch，没有超时，无限期挂着只会让人以为界面卡死；③ 模块级的单个 `Semaphore` 一旦排过队就跟那个事件循环绑死，换个 loop 再用会抛「bound to a different event loop」——pytest 每个 `asyncio.run()` 都是新循环，所以改成 `_llm_slots()` 按 loop 惰性取；④ 同一会话两个请求时，被顶掉的那个仍会抢到槽位把答案生成完再丢弃——现在 `stream_answer` 收一个 `still_wanted` 回调，排队期间和拿到槽位后各查一次。

**26. admin.js 一个文件塞了五个页面**

1231 行里混着数据概览、用户管理、知识库表格、对话历史、问题反馈，改「问题反馈每页几条」这种小事也要在四个模块之间翻。现在问题反馈整体拆成 `views/admin-feedback.js`（独立 IIFE，只依赖全局 `Common/API/Markdown/App`），`AdminView.render(container, "unanswered")` 委托过去，admin.js 降到 969 行。

**27. 一堆「回答范围」代码，前端从来没调过**

`AskRequest.knowledge_bases`、会话里的 `knowledge_bases` / `last_knowledge_bases`、`list_sessions` 的 `kb` 筛选、`_kb_list` / `_kb_from_conv`：后端一直支持「这次只在某几个知识库里检索」，但界面上没有任何入口，`streamAsk` 永远发空数组。更糟的是那段 403 校验写在「保存用户提问」**之后**——越权请求会先落一条提问再返回 403，会话里多出一条永远没有回答的记录，下次打开还会自动补生成。既然没人用，索性连功能一起删（模型里那两列保留并标成遗留列：老库这两列是 NOT NULL 且没服务器端默认值，删字段会让 INSERT 直接失败）。顺带删掉两个前端从不调用的端点 `GET /api/knowledge/points`、`GET /api/knowledge/courses`。

**28. 默认管理员 + 放开 HOST = 后门**

`23040107 / 111111` 是本地课设的取舍，登录限流只拦暴力破解、拦不住已知密码。默认 `HOST=127.0.0.1` 时它出不了本机；但只要为了「让同学用手机访问」把 `HOST` 改成 `0.0.0.0`，这就是个公开的管理员入口。现在启动时检查：`HOST` 不是回环地址、管理员又还在用 `111111`，直接拒绝启动，并在报错里写清「先 127.0.0.1 登录改密 → 再放开 HOST」。

**29. 还没解决的**

- 文档解析（PDF/TXT 分块 + 入库向量）还是同步跑在后台线程里，一次传十几个大文件会排队；检索已经用 `asyncio.to_thread` 挪出事件循环了。
- 多轮历史是「6 轮 + 6000 字」双上限，没按 token 算预算，换模型上下文变长后这套常数要重看。
- 聚类是贪心合并，长尾问题多时分组顺序会受输入顺序影响。
- 端到端回归仍靠 `.tools/kb_e2e.py`，没接进 CI；前端除分页条的 node 回归外，其余交互还是靠人眼看。
- 「回答范围（只查某几个知识库）」整套后端逻辑已经删了，真要做就得在聊天页加多选入口，并重新设计会话侧的存储。

---

## 九、代码位置速查

- **RAG 核心**：`backend/services/rag_service.py` —— 检索、门控、概览模式、提示词、流式生成、超时控制。
- **可见性规则**：`backend/services/kb_scope.py` —— 列表、检索、写入、上传、反馈重生成共用同一套判断，别再在路由里各写一份。
- **回收站**：`backend/services/recycle.py`（快照 / 归档 / 过期清理）+ `backend/routers/recycle.py`（列表 / 恢复 / 彻底删除）。
- **文档处理**：`document_parser.py`（PDF / TXT / MD / CSV）+ `chunker.py`（递归分块，中文标点感知）。
- **向量库**：`vector_store.py` —— Chroma 持久化，文档与知识点同集合，按元数据过滤增删。
- **问题聚类**：`backend/routers/unanswered.py` 的 `cluster_unanswered` + `QuestionVector` 缓存表（写入走 upsert，两个标签页同时点聚类不会撞唯一索引）。
- **并发闸门**：`rag_service.stream_answer` 负责排队、心跳、120 秒排队上限和「被顶掉就别跑了」的判断，真身是 `_stream_answer_locked`；非流式的 `generate_answer` 是同一套闸门的另一入口（真身 `_generate_answer_locked`），排不上队回 `error: busy`。闸门本身由 `_llm_slots()` 按事件循环提供，不要改回模块级单例。
- **搜索转义**：`services/like.py` 的 `escape_like` / `contains` —— 所有关键词搜索都走它，`%` `_` 按普通字符匹配，别再写 `f"%{kw}%"`。
- **模型切换**：`llm_service.py` / `embedding_service.py` —— 按配置返回 Ollama 或 OpenAI 兼容实现。
- **认证**：`security.py`（PBKDF2-SHA256 + JWT）、`deps.py`（角色依赖）、`routers/auth.py`（登录 / 注册限流、头像校验）。
- **流式协议**：SSE（`text/event-stream`），前端 `fetch + ReadableStream` 解析，支持中途停止；`routers/chat.py` 用生成序号防止旧流覆盖新回答。
- **前端结构**：`static/js/views/` 每个页面一个模块，管理员的「知识库与文档」在 `content.js` 里拼 `admin.js` + `documents.js`，问题反馈单独在 `admin-feedback.js`；统一的弹窗 / Toast / 搜索按钮 / 分页条在 `common.js`。
- **列表分页**：`Common.renderPagination`（`opts.hidePageInfo` 关掉「第 x/y 页」，管理员角色默认就关）+ 各页自己的 `limit` / `offset` 状态；回归用例在 `tests/frontend_pagination.mjs`。
- **学生反馈 / 文档列表**：`routers/chat.py` 的 `my_unanswered`（返回 `{total, pending, resolved, items}`）、`routers/documents.py` 的 `list_documents`（返回 `{total, processing, items}`，`processing` 决定前端要不要轮询；它按可见范围全量统计，不吃 `keyword`，否则搜着别的词就以为解析完了）。
- **导航与路由**：`static/js/app.js` 的 `NAV` / `TITLES` / `navigate()`，新增页面在这里登记；≤640px 抽屉逻辑也在本文件。
- **静态资源缓存**：`main.py` 里的 `static_no_cache` 中间件（`/static` 统一 `no-cache`）+ `app.html` 里的 `?v=NN`，**改 js/css 两边都要管**（见开发记录 17）。
- **会话列表渲染**：`views/chat.js` 的 `fetchSessionPage()` / `renderSessionList()` / `SIDEBAR_LIMIT`（侧边栏取最近 20 条 + 服务端搜索 + 查看全部入口）；`views/history.js` 走同一接口做服务端分页。`GET /api/chat/sessions` 返回 `{total, items}`，不再全量下发。
