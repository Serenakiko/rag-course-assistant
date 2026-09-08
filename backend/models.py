"""SQLAlchemy ORM 模型定义。"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class User(Base):
    """用户：学生 / 管理员。"""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(160))
    real_name: Mapped[str] = mapped_column(String(64), default="")
    avatar: Mapped[str] = mapped_column(String(255), default="")  # emoji:😀 / /static/avatars/x.png
    role: Mapped[str] = mapped_column(String(16), default="student", index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )

    conversations: Mapped[list["Conversation"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class Document(Base):
    """上传的课程文档（PDF / TXT / Markdown / CSV）。"""

    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    filename: Mapped[str] = mapped_column(String(255))
    file_type: Mapped[str] = mapped_column(String(16))  # pdf / txt / md / csv
    file_size: Mapped[int] = mapped_column(Integer, default=0)
    stored_path: Mapped[str] = mapped_column(String(512), default="")
    status: Mapped[str] = mapped_column(String(16), default="processing")
    # processing / ready / failed
    course: Mapped[str] = mapped_column(String(128), default="", index=True)  # 所属知识库/课程名
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str] = mapped_column(Text, default="")
    uploaded_by: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )

    uploader: Mapped["User"] = relationship()


class KnowledgeBase(Base):
    """知识库（按课程组织，知识点通过 course 字段关联到知识库名称）。"""

    __tablename__ = "knowledge_bases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    creator: Mapped["User"] = relationship()


class KnowledgePoint(Base):
    """用户维护的知识点（同样进入向量库参与检索，归属某知识库/课程）。"""

    __tablename__ = "knowledge_points"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(255))
    content: Mapped[str] = mapped_column(Text)
    course: Mapped[str] = mapped_column(String(128), default="", index=True)
    tags: Mapped[str] = mapped_column(String(255), default="")  # 逗号分隔
    created_by: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    creator: Mapped["User"] = relationship()

    @property
    def creator_name(self) -> str:
        """创建者显示名（未关联用户时返回空）。"""
        if self.creator is None:
            return ""
        return self.creator.real_name or self.creator.username or ""


class UnansweredQuestion(Base):
    """知识库未覆盖的问题记录（供管理员查看、补充资料并生成回答）。"""

    __tablename__ = "unanswered_questions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    question: Mapped[str] = mapped_column(Text)
    course: Mapped[str] = mapped_column(String(128), default="", index=True)  # 推测的关联课程，可为空
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)  # pending / resolved
    # 拒绝回答时知识库中已有的相近资料标题（JSON 数组），用于引导提问者换问法
    related_points: Mapped[str] = mapped_column(Text, default="[]")
    # 管理员补充资料后重新生成的回答；非空即视为已解决
    answer: Mapped[str] = mapped_column(Text, default="")
    answered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # 学生端"已读"标记：学生查看已解决答案后写入（NULL=未读），驱动未读角标
    read_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_by: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    creator: Mapped["User"] = relationship()

    @property
    def creator_name(self) -> str:
        """提问者显示名（未关联用户时返回空）。"""
        if self.creator is None:
            return ""
        return self.creator.real_name or self.creator.username or ""


class Conversation(Base):
    """一次问答会话（一个会话内可连续多轮追问）。"""

    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(255), default="新会话")
    # 最近一次提问使用的知识库（JSON 数组），用于历史会话按知识库筛选
    knowledge_bases: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )

    user: Mapped["User"] = relationship(back_populates="conversations")
    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan", order_by="Message.id"
    )


class Message(Base):
    """会话内的一条消息（user / assistant），assistant 消息携带引用来源 JSON。"""

    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(16))  # user / assistant
    content: Mapped[str] = mapped_column(Text)
    sources: Mapped[str] = mapped_column(Text, default="[]")  # JSON 数组
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )

    conversation: Mapped["Conversation"] = relationship(back_populates="messages")
