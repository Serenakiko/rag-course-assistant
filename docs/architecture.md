# 系统架构

## 总体架构

```mermaid
flowchart LR
    B[浏览器<br/>原生 HTML/CSS/JS] -->|HTTP / SSE| F[FastAPI]
    F --> A[认证与限流]
    F --> C[对话与 RAG]
    F --> K[知识库 / 文档]
    F --> U[用户 / 统计 / 反馈]
    C --> R[RAG Service]
    R --> V[(ChromaDB<br/>向量库)]
    R --> O[Ollama<br/>qwen3:8b + bge-m3]
    A --> D[(SQLite<br/>WAL)]
    K --> D
    U --> D
    C --> D
```

## 模块职责

| 模块 | 职责 |
| --- | --- |
| `backend/routers/auth.py` | 注册、登录、JWT、限流、头像、个人信息 |
| `backend/routers/chat.py` | 会话管理、SSE 问答、点赞/点踩、管理员会话查看 |
| `backend/routers/knowledge.py` | 知识库与知识点 CRUD、可见性、改名重建向量 |
| `backend/routers/documents.py` | 文档上传、解析、列表、重新解析、删除 |
| `backend/routers/unanswered.py` | 未命中问题列表、聚类、统计、补资料后重新生成 |
| `backend/routers/recycle.py` | 知识库删除快照、30 天恢复、彻底删除 |
| `backend/routers/stats.py` | 学生工作台与管理员数据概览 |
| `backend/services/rag_service.py` | 检索、门控、提示词拼装、流式生成、引用来源 |
| `backend/services/vector_store.py` | Chroma 封装、版本化写入、按课程过滤、删除 |
| `backend/static/` | 零构建前端；`views/` 下按页面拆分 |

## 数据模型

```mermaid
erDiagram
    USER ||--o{ CONVERSATION : owns
    CONVERSATION ||--o{ MESSAGE : contains
    USER ||--o{ KNOWLEDGE_BASE : creates
    KNOWLEDGE_BASE ||--o{ KNOWLEDGE_POINT : contains
    KNOWLEDGE_BASE ||--o{ DOCUMENT : contains
    USER ||--o{ UNANSWERED_QUESTION : asks
    USER ||--o{ RECYCLE_ITEM : deletes

    USER {
        int id PK
        string account
        string username
        string role
        int token_version
    }
    CONVERSATION {
        int id PK
        int user_id FK
        string title
        int gen_seq
    }
    MESSAGE {
        int id PK
        int conversation_id FK
        string role
        text content
        text sources
        int rejected
        string feedback
    }
    KNOWLEDGE_BASE {
        int id PK
        string name
        int created_by FK
    }
    KNOWLEDGE_POINT {
        int id PK
        string title
        text content
        string course
        int is_auto
    }
    DOCUMENT {
        int id PK
        string filename
        string stored_path
        string course
        string status
        int is_seed
    }
    UNANSWERED_QUESTION {
        int id PK
        string question
        string course
        string status
        text related_points
        int created_by FK
    }
    RECYCLE_ITEM {
        int id PK
        string kind
        string name
        text payload
    }
```

## 一次问答的时序

```mermaid
sequenceDiagram
    participant U as 学生
    participant API as FastAPI
    participant DB as SQLite
    participant R as RAG Service
    participant V as Chroma
    participant L as Ollama

    U->>API: POST /api/chat/ask (SSE)
    API->>DB: 定位会话 / 保存提问 / gen_seq+1
    API->>R: generate_answer(question, history, courses)
    R->>L: 多轮指代改写（可选）
    R->>V: similarity_search(top_k, course filter)
    V-->>R: 文档片段 + 相似度
    R->>R: 距离门控
    alt 相似度低于阈值
        R-->>API: unmatched
        API->>DB: 写入未命中问题
        API-->>U: SSE unmatched
    else 通过距离门控
        opt ANSWERABILITY_CHECK
            R->>L: 判断资料是否直接回答问题
        end
        alt 不可答
            R-->>API: unmatched
            API->>DB: 写入未命中问题
        else 可答
            R->>L: 带引用上下文生成回答
            L-->>R: token 流
            R-->>API: token 流
            API-->>U: SSE token
            API->>DB: 保存回答 + sources
        end
    end
```

## 三重拒答

| 防线 | 解决的问题 | 实现位置 |
| --- | --- | --- |
| 距离门控 | 问题与课程资料明显无关 | `rag_service.generate_answer` |
| 未收录标记 | 模型自己判断资料不足 | `rag_prompts.is_unmatched_answer` |
| 可答性判定 | 资料“相近但没直接讲” | `ANSWERABILITY_CHECK` 分支 |

对应测试：`test_unmatched_gate_*`、`test_answerability_gate_blocks_similar_but_uncovered`、`test_empty_kb_short_circuits_without_llm`。

## 关键工程细节

- **SSE 流式**：`/api/chat/ask` 逐 token 推送，前端断线后可用 `regenerate` 补生成。
- **会话级并发控制**：`conversations.gen_seq` 让新提问顶掉旧流，避免两轮回答互相覆盖。
- **生成闸门**：`rag_service._llm_slots` 限制同时生成路数，排队超时返回明确错误。
- **向量版本化**：先写新版本再删旧版本，避免写入失败时把旧向量删光；检索后再做 BM25+向量混合重排。
- **回收站**：删除前落快照，恢复时重新向量化；提交失败保留 trash 原件。
