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
    # 改密/降权时自增，旧的 JWT 立即失效（token 里带 ver 声明）
    token_version: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
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
    # 由 .tools/sync_seed_documents.py 生成的展示记录：内容已作为知识点入过库，rebuild 时跳过
    is_seed: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
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
    # 文档上传自动生成的知识点：只作展示，重建向量时跳过，避免与文档分块重复。
    # 不再靠“标题和文档同名”来猜，避免误伤同名的手工知识点。
    is_auto: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_by: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    # 最后一次改内容的人：创建者与修改者可能不是同一个人，改了要能追到人
    updated_by: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    creator: Mapped["User"] = relationship(foreign_keys="KnowledgePoint.created_by")
    updater: Mapped["User | None"] = relationship(foreign_keys="KnowledgePoint.updated_by")

    @property
    def creator_name(self) -> str:
        """创建者显示名（未关联用户时返回空）。"""
        if self.creator is None:
            return ""
        return self.creator.real_name or self.creator.username or ""

    @property
    def updater_name(self) -> str:
        """最后修改人显示名（没改过时为空）。"""
        if self.updater is None:
            return ""
        return self.updater.real_name or self.updater.username or ""


class UnansweredQuestion(Base):
    """知识库未覆盖的问题记录（供管理员查看、补充资料并生成回答）。"""

    __tablename__ = "unanswered_questions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # 展示编号：删除后可以重排成 1..n，主键 id 永不变动。
    # 以前直接重排主键，外部拿着旧编号去操作就会打偏（删错记录）。
    display_no: Mapped[int] = mapped_column(Integer, default=0, index=True)
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
    # 学生端“已读”标记：学生查看已解决答案后写入（NULL=未读），驱动未读角标
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
    # 遗留列：以前用于「这次只在这几个知识库里检索」，前端从来没做过入口，
    # 逻辑已从 /api/chat/ask 里删掉。列保留是为了不破坏已有数据库（NOT NULL
    # 没服务器端默认值，删字段会让老库的 INSERT 直接失败）。新代码不再读写。
    knowledge_bases: Mapped[str] = mapped_column(Text, default="[]")
    # 遗留列，同上（补生成曾按它限定范围），现在不再使用
    last_knowledge_bases: Mapped[str] = mapped_column(
        Text, default="[]", server_default="[]"
    )
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
    # 生成中断（客户端断开 / 超时 / 报错）时保存的半截回答：1=中断，0=完整
    interrupted: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )

    conversation: Mapped["Conversation"] = relationship(back_populates="messages")


class RecycleItem(Base):
    """回收站：删除知识库前把内容快照存一份，30 天内可恢复。

    只存元信息与知识点正文，向量在恢复时重新入库；文档源文件移到
    data/trash/<id>/ 下，恢复时搬回上传目录。
    """

    __tablename__ = "recycle_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(16), default="base")
    name: Mapped[str] = mapped_column(String(128), default="")
    payload: Mapped[str] = mapped_column(Text, default="{}")
    deleted_by: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    deleted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )

    deleter: Mapped["User | None"] = relationship()

    @property
    def deleter_name(self) -> str:
        if self.deleter is None:
            return ""
        return self.deleter.real_name or self.deleter.username or ""


class QuestionVector(Base):
    """未命中问题的向量缓存：聚类时复用，同一句话只算一次。

    只在管理员打开「按问题聚类」时按需生成，不进检索，不参与问答。
    """

    __tablename__ = "question_vectors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    text_hash: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    text: Mapped[str] = mapped_column(Text)
    vector: Mapped[str] = mapped_column(Text)  # JSON 数组
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
