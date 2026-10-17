# 智答系统（基于 RAG 大模型）

一个可本地运行的全栈 Web 应用：**FastAPI + SQLAlchemy + SQLite + ChromaDB** 构建 RAG 问答后端，**原生 HTML / CSS / JS** 构建无需构建步骤的网页界面，支持 **学生 / 管理员** 双角色。对话模型默认走 **Ollama `qwen3.5:4b`**，向量模型 **`bge-m3`**，也可以通过 `.env` 切到任意 OpenAI 兼容接口。

---

## 一、功能总览

| 角色 | 功能 |
|---|---|
| 学生 | 工作台（今日提问、知识点总数、我的反馈）、知识库浏览与自建（新建后可立刻上传资料、维护知识点）、智能问答（流式输出 + 引用溯源 + 会话管理）、我的反馈、个人中心 |
| 管理员 | 数据概览（用户 / 知识库 / 文档 / 今日提问 + 趋势图 + 知识库文档占比）、知识库管理（建库即传资料、知识点维护）、文档管理（检索 / 详情 / 删除）、对话历史（按问题 / 回答 / 提问人搜索）、问题反馈（生成回答 / 状态流转 / 删除）、用户管理（搜索 / 角色调整 / 重置密码 / 删除） |

核心能力：

- **RAG 问答**：问题 → 向量检索 Top-K → 距离门控 → 组装上下文 → 大模型流式生成，回答附引用来源，可点开查看原文。
- **多轮对话**：最近 6 轮自动带入上下文；当前对话有内容时新建会二次确认，历史会话保留在左侧。
- **知识库双来源**：上传文档分块向量化 + 手工维护的知识点，统一进同一个 Chroma 集合检索。
- **可见性隔离**：管理员看到全部知识库；学生只看到「管理员发布的 + 自己创建的」，检索、列表、知识点增改、文档上传共用同一套规则（`kb_scope.py`）。
- **权限与安全**：JWT 认证、接口级角色校验、登录失败限流（同一账号 + IP 10 分钟内失败 10 次锁定）、头像上传按文件头校验、Markdown 渲染先转义后解析。
- **双模型模式**：`LLM_PROVIDER=ollama | openai` 一键切换，对话与向量模型分别配置。
- **反馈闭环**：课程相关但知识库没覆盖的问题自动记录；管理员补完资料后可一键重新生成回答，学生在工作台看到结果。
- **零构建前端**：原生 JS 视图模块，服务端直接托管静态文件，改完刷新即生效。

---

## 二、系统架构

```
┌──────────────────────────────────────────────────────────────┐
│                          浏览器（前端）                       │
│  index.html / register.html / app.html + CSS + JS（无构建）   │
│  学生：工作台 / 知识库 / 问答 / 反馈 / 个人中心               │
│  管理员：概览 / 知识库 / 文档 / 问答 / 反馈 / 历史 / 用户     │
└───────────────┬───────────────────────────────┬──────────────┘
                │ REST API + SSE 流式            │ 静态资源
┌───────────────▼───────────────────────────────▼──────────────┐
│                     FastAPI 应用层（backend）                  │
│  routers: auth / chat / knowledge / documents / users / stats │
│           unanswered                                          │
│  services: llm / embedding / parser / chunker / vector / rag   │
│            kb_scope                                           │
└────────┬──────────────────────┬────────────────────┬──────────┘
         │                      │                    │
┌────────▼────────┐   ┌─────────▼────────┐  ┌────────▼─────────┐
│ SQLite（元数据） │   │ ChromaDB（向量）  │  │ Ollama / 兼容 API │
│ 用户/会话/消息/  │   │ 文档分块 + 知识点 │  │ qwen3.5:4b 对话   │
│ 文档/知识点/反馈 │   │ 持久化存储        │  │ bge-m3 向量       │
└─────────────────┘   └──────────────────┘  └──────────────────┘
```

### RAG 回答流程

```
用户问题
   │
   ▼
按可见范围确定课程 → 向量化（Embedding）
   │
   ▼
ChromaDB 相似度检索 Top-K ──── 来源：课程文档分块 / 知识点
   │
   ├─ 最相似距离 > 0.85 ──► 判定「知识库未收录」，不调用模型，记录反馈
   │
   ▼
组装提示词（系统规则 + 参考资料 + 最近 6 轮对话）
   │
   ▼
大模型流式生成（SSE）──► 前端逐字渲染 + 引用来源
   │
   ▼
回答、来源、耗时落库（SQLite）
```

### 目录结构

```
rag-course-assistant/
├── run.py                    # 启动入口（--reload / --port / --no-browser；端口占用时自动跳过）
├── start.bat                 # Windows 双击启动
├── autostart_silent.vbs      # 登录后静默启动（隐藏窗口，输出写 .tools/server-start.log）
├── requirements.txt
├── .env.example              # 配置模板
├── .env                      # 本机配置（不入库）
│
├── backend/
│   ├── main.py               # 路由装配、静态托管、启动迁移
│   ├── config.py             # 集中配置（.env 加载、JWT_SECRET 校验）
│   ├── database.py           # SQLite 引擎、外键约束、增量迁移
│   ├── models.py             # ORM：用户/文档/知识点/会话/消息/反馈
│   ├── schemas.py            # 请求响应模型与字段校验
│   ├── security.py           # PBKDF2 密码哈希 + JWT
│   ├── deps.py               # 当前用户 / 角色依赖
│   ├── seed.py               # 默认管理员 + 四门课程知识库与示例知识点
│   ├── routers/              # auth / chat / knowledge / documents / users / stats / unanswered
│   ├── services/             # llm / embedding / document_parser / chunker / vector_store / rag_service / kb_scope
│   └── static/
│       ├── index.html        # 登录页
│       ├── register.html     # 注册页
│       ├── app.html          # 主界面（学生 + 管理员）
│       ├── css/              # common.css / auth.css / app.css
│       ├── js/               # api.js / app.js / auth.js / common.js / markdown.js
│       │   └── views/        # workbench / knowledge / chat / feedback / profile / admin / documents / history
│       ├── img/              # 品牌图标、登录页背景
│       └── avatars/          # 用户上传头像（运行时生成，不入库）
├── data/                     # 运行时生成（不入库）：app.db、chroma/、uploads/
└── .tools/                   # 一次性运维/调试脚本（入库）+ 本地日志（不入库）
    ├── update_knowledge_bases.py
    ├── sync_seed_documents.py
    ├── rebuild_vectors.py
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

启动成功后自动打开 **http://127.0.0.1:8000**。也可以双击 `start.bat`，或 `python run.py --no-browser` 不自动开浏览器。

如果希望登录 Windows 后自动在后台启动：把 `autostart_silent.vbs` 建一个快捷方式放进「启动」文件夹（`shell:startup`），脚本会用隐藏窗口运行 `python run.py --no-browser`。服务已经在跑时再次启动会自动跳过，不会重复占端口。

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

---

## 四、使用指南

### 学生

1. **登录 / 注册**：账号必须是 8 位数字，规则前后端一致；密码至少 6 位，两项不符会逐项弹窗提示。
2. **工作台**：显示今日提问、已入库知识点、我的反馈（待处理 / 已解决）；没有反馈时也会保留卡片并给出说明。
3. **智能问答**：Enter 发送、Shift+Enter 换行；回答流式输出，点「参考了 N 份资料」展开引用原文。左侧「+ 新对话」在当前对话有内容时会先确认，空对话则直接提示。
4. **知识库**：卡片总览支持按「名称 / 简介」搜索；新建知识库后会**直接弹出上传资料**的窗口；进入某个库可维护知识点、上传文档。
5. **我的反馈**：查看提问被记录后的处理状态与管理员补充的回答。
6. **个人中心**：默认只读，点「编辑资料」后输入框才可编辑，同时出现「取消 / 保存」；头像下方有「更换头像」入口。

### 管理员

1. **数据概览**：用户 / 知识库 / 文档 / 今日提问四项统计，近 7 日提问趋势，知识库文档占比环形图（中心显示知识点总数，图例带数量与百分比，空知识库不显示）。
2. **知识库管理**：新建后立即上传资料；列表支持按「名称 / 描述 / 创建者」搜索；每行可上传资料、编辑、删除。
3. **文档管理**：只负责查看与清理。支持按「文件名 / 知识库 / 类型」搜索，可查看分块详情、删除文档。**上传入口只在知识库页面**。
4. **对话历史**：按「问题 / 回答 / 提问人」搜索，服务端分页，点「详情」查看完整问答。
5. **问题反馈**：查看未命中问题，补充资料后可重新生成回答（按提问学生的可见范围检索），支持标记状态与删除。
6. **用户管理**：按「账号 / 用户名 / 角色」搜索；可调整角色、重置密码、删除用户（删除会级联清理其会话与消息）。

---

## 五、API 接口一览

| 方法 | 路径 | 说明 | 权限 |
|---|---|---|---|
| POST | /api/auth/register | 注册（固定学生角色） | 公开 |
| POST | /api/auth/login | 登录，返回 JWT（失败限流） | 公开 |
| GET | /api/auth/me | 当前用户 | 登录 |
| PUT | /api/auth/profile | 更新用户名 / 头像 | 登录 |
| POST | /api/auth/avatar | 上传头像（校验文件头） | 登录 |
| POST | /api/auth/change-password | 修改密码 | 登录 |
| GET | /api/chat/sessions | 我的会话列表 | 登录 |
| POST | /api/chat/sessions | 新建会话 | 登录 |
| GET / PATCH / DELETE | /api/chat/sessions/{id} | 会话消息 / 重命名 / 删除 | 登录 |
| POST | /api/chat/ask | 流式问答（SSE） | 登录 |
| GET | /api/chat/unanswered/mine | 我的反馈 | 登录 |
| POST | /api/chat/unanswered/{id}/read | 标记反馈已读 | 登录 |
| GET | /api/knowledge/bases | 知识库列表（按可见性过滤） | 登录 |
| POST / PUT / DELETE | /api/knowledge/bases(/{id}) | 知识库增删改 | 登录（学生仅自己创建的） |
| GET / POST / PUT / DELETE | /api/knowledge/points | 知识点增删改查（写入校验目标库可见性） | 登录 |
| GET | /api/knowledge/courses | 可见课程名列表 | 登录 |
| GET | /api/knowledge/stats/count | 知识点总数 | 登录 |
| POST | /api/documents/upload | 上传文档（后台解析分块） | 登录（课程须可见） |
| GET | /api/documents | 文档列表 | 管理员 |
| GET / DELETE | /api/documents/{id} | 文档详情（分块预览）/ 删除 | 管理员 |
| GET | /api/admin/users | 用户列表 | 管理员 |
| POST | /api/admin/users | 新增用户 | 管理员 |
| PATCH / DELETE | /api/admin/users/{id} | 修改 / 删除用户 | 管理员 |
| GET | /api/stats/me | 我的统计（工作台） | 登录 |
| GET | /api/stats/admin/stats | 系统统计（含趋势与占比） | 管理员 |
| GET | /api/chat/admin/conversations | 全部会话（分页） | 管理员 |
| GET | /api/chat/admin/conversations/{id}/messages | 会话消息 | 管理员 |
| DELETE | /api/chat/admin/conversations/{id} | 删除会话 | 管理员 |
| GET | /api/chat/admin/messages | 问答对（`q` + `field=question\|answer\|user`，分页） | 管理员 |
| GET | /api/admin/unanswered | 反馈列表（状态过滤） | 管理员 |
| GET | /api/admin/unanswered/stats | 反馈统计 | 管理员 |
| POST | /api/admin/unanswered/{id}/regenerate | 重新生成回答 | 管理员 |
| PATCH / DELETE | /api/admin/unanswered/{id} | 标记状态 / 删除 | 管理员 |
| GET | /api/llm/status | 模型连接状态（前端不再展示，供排查） | 登录 |
| GET | /api/health | 健康检查 | 公开 |

接口文档：启动后访问 **http://127.0.0.1:8000/docs**。

---

## 六、常见问题

**Q1：页面显示「127.0.0.1 拒绝了连接请求」**
服务还没启动完。冷启动要一分钟左右（加载 Chroma 和依赖），等日志出现监听信息再刷新。若一直如此，检查终端报错，常见原因是 `JWT_SECRET` 未配置。

**Q2：问答报「模型服务还没准备好」**
说明 Ollama 里没有对应模型，或 Ollama 未启动。执行 `ollama list` 确认有 `qwen3.5:4b` 和 `bge-m3`；没有就 `ollama pull`。具体原因在浏览器控制台（`[llm]` 开头）和服务端日志里。

**Q3：Ollama 明明装了模型，`ollama list` 却是空的**
Ollama 桌面端的「模型存储位置」指向了空目录。打开 Ollama 设置检查该路径，模型文件通常位于 `C:\Users\<你>\.ollama\models`；改回正确目录后重启 Ollama。

**Q4：上传 PDF 提示解析失败**
多为扫描件（没有文本层），需要先 OCR；或者改用 TXT / Markdown。

**Q5：示例知识点没有创建**
首次启动时如果 Ollama 未就绪，种子知识点会回滚。启动 Ollama 后删除 `data/app.db` 再重启即可重新初始化。

**Q6：如何清空所有数据**
停止服务，删除 `data/app.db` 与 `data/chroma/`，重启回到初始状态。

**Q7：端口被占用**
`run.py` 检测到端口已占用会直接跳过启动（防止开机自启重复起进程）。确实要换端口：`python run.py --port 9000` 或改 `.env` 的 `PORT`。

**Q8：回答总被拒答 / 检索结果异常**
先停后端，运行 `python .tools/rebuild_vectors.py` 重建向量；再运行 `python .tools/kb_probe.py` 看距离分布是否仍在 0.85 两侧。换过向量模型就必须重新标定阈值。

---

## 七、运维与调试脚本

`.tools/` 下的脚本均为一次性工具，不参与运行时，请在项目根目录执行：

| 脚本 | 用途 | 何时使用 |
|---|---|---|
| `python .tools/update_knowledge_bases.py` | 重建四门课程知识库（幂等） | 知识库结构升级后 |
| `python .tools/sync_seed_documents.py` | 种子知识点同步为文档记录（不重复入向量库） | 文档数与知识点数对不上时 |
| `python .tools/rebuild_vectors.py` | 清空 Chroma，全部重建 | 换向量模型 / 索引损坏（先停后端） |
| `python .tools/kb_probe.py` | 典型问题 Top-1 距离 | 调拒答阈值 |
| `python .tools/kb_calibrate.py` | 多类问题 Top-3 距离 | 标定阈值边界 |
| `python .tools/kb_e2e.py` | 登录 → 问答 → 反馈 → 状态流转 | 改完 RAG 链路做回归 |

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

**12. 还没解决的**

- 检索和入库在 `async` 路由里是同步调用，会阻塞事件循环；单机自用无感，并发上来是问题，正确做法是 `asyncio.to_thread` 包一层。
- 多轮历史按条数截断（最近 6 轮），没按 token 算预算。
- 没有 pytest，回归靠手动跑 `.tools/kb_e2e.py`。

---

## 九、代码位置速查

- **RAG 核心**：`backend/services/rag_service.py` —— 检索、门控、提示词、流式生成。
- **可见性规则**：`backend/services/kb_scope.py` —— 列表、检索、写入、上传共用同一套判断，别再在路由里各写一份。
- **文档处理**：`document_parser.py`（PDF / TXT / MD / CSV）+ `chunker.py`（递归分块，中文标点感知）。
- **向量库**：`vector_store.py` —— Chroma 持久化，文档与知识点同集合，按元数据过滤增删。
- **模型切换**：`llm_service.py` / `embedding_service.py` —— 按配置返回 Ollama 或 OpenAI 兼容实现。
- **认证**：`security.py`（PBKDF2-SHA256 + JWT）、`deps.py`（角色依赖）、`routers/auth.py`（登录限流、头像校验）。
- **流式协议**：SSE（`text/event-stream`），前端 `fetch + ReadableStream` 解析，支持中途停止。
- **前端结构**：`static/js/views/` 每个页面一个模块，统一的弹窗 / Toast / 搜索按钮在 `common.js`。
