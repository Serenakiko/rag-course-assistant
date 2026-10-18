"""Pydantic 请求 / 响应模型（v2 风格）。"""
from __future__ import annotations

from datetime import datetime
import json

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ---------- 认证 ----------
class RegisterRequest(BaseModel):
    # 账号固定 8 位数字（前端同规则）
    username: str = Field(min_length=8, max_length=8, pattern=r"^\d{8}$")
    password: str = Field(min_length=6, max_length=64)
    real_name: str = Field(default="", max_length=32)
    role: str = Field(default="student", pattern=r"^student$")


class LoginRequest(BaseModel):
    username: str
    password: str


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    real_name: str
    avatar: str = ""
    role: str
    created_at: datetime


class LoginResponse(BaseModel):
    token: str
    user: UserOut


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str = Field(min_length=6, max_length=64)


class UpdateProfileRequest(BaseModel):
    username: str | None = Field(default=None, pattern=r"^\d{8}$")
    real_name: str | None = Field(default=None, max_length=32)
    avatar: str | None = Field(default=None, max_length=512)


# ---------- 对话 ----------
class CreateConversationRequest(BaseModel):
    title: str = Field(default="", max_length=255)


class ConversationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    created_at: datetime
    # 会话里的消息条数：学生端「对话历史」直接显示，不用点进去才知道
    message_count: int = 0


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    conversation_id: int
    role: str
    content: str
    sources: list = Field(default_factory=list)
    interrupted: bool = False
    created_at: datetime

    @field_validator("sources", mode="before")
    @classmethod
    def _decode_sources(cls, v: object) -> list:
        """库里存的是 JSON 字符串，出参统一成数组，前端可以直接当数组用。"""
        if isinstance(v, str):
            try:
                parsed = json.loads(v or "[]")
            except ValueError:
                return []
            return parsed if isinstance(parsed, list) else []
        return v if isinstance(v, list) else []


class AskRequest(BaseModel):
    conversation_id: int | None = None  # None 表示新建会话
    message: str = Field(min_length=1, max_length=8000)
    # 重新生成模式：不重复保存提问，并删除会话中上一条助手回答（供前端“重新生成”刷新键使用）
    regenerate: bool = False


class RenameConversationRequest(BaseModel):
    title: str = Field(min_length=1, max_length=255)


# ---------- 知识点 ----------
class KnowledgePointIn(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    content: str = Field(min_length=1)
    course: str = Field(default="", max_length=128)
    tags: str = Field(default="", max_length=255)


class KnowledgePointOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    content: str
    course: str
    tags: str
    created_by: int | None
    created_at: datetime
    updated_at: datetime
    updated_by: int | None = None
    creator_name: str = ""
    updater_name: str = ""


# ---------- 知识库 ----------
class KnowledgeBaseIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: str = Field(default="", max_length=1000)


class KnowledgeBaseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str
    created_by: int | None
    created_at: datetime
    updated_at: datetime


class KnowledgeBaseSummary(KnowledgeBaseOut):
    """知识库列表项：附带知识点 / 文档 / 分块数量与创建者姓名。"""

    point_count: int = 0
    document_count: int = 0   # 该库下的文档条数
    chunk_count: int = 0      # 该库所有文档的分块总数
    creator_name: str = ""


# ---------- 文档 ----------
class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    filename: str
    file_type: str
    file_size: int
    status: str
    course: str = ""
    chunk_count: int
    error: str
    uploaded_by: int | None
    created_at: datetime


class DocumentDetailOut(DocumentOut):
    chunks: list[dict] = []   # 向量库里的分块（真上传的文档才有）
    preview: str = ""         # 分块为空时回退：直接读原文件的正文（种子文档只有记录没有分块）


# ---------- 用户管理 ----------
class AdminUserUpdate(BaseModel):
    role: str | None = Field(default=None, pattern=r"^(student|admin)$")
    real_name: str | None = Field(default=None, max_length=32)
    new_password: str | None = Field(default=None, min_length=6, max_length=64)


# ---------- 统计 ----------
class StatsOut(BaseModel):
    users: int
    students: int
    admins: int
    knowledge_bases: int = 0          # 知识库数量
    documents: int
    documents_ready: int
    knowledge_points: int
    conversations: int
    messages: int
    today_questions: int = 0          # 今日提问数（user 消息条数）
    daily_questions: list[dict]        # [{date, count}] 近 7 天每日提问数
    kb_doc_distribution: list[dict] = []  # [{name, count}] 知识库文档占比
    unanswered_pending: int = 0        # 待处理反馈数
    unanswered_resolved: int = 0       # 已解答反馈数
    recent_unanswered: list[dict] = [] # [{id, question, status, created_at, answered_at}] 最近反馈
