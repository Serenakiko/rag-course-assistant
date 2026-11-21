"""SQLAlchemy ORM 模型定义。"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_vector_key() -> str:
    return "kb_" + uuid4().hex


class Base(DeclarativeBase):
    pass


class User(Base):
    """用户：学生 / 管理员。"""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(160))
    # 改密/降权时自增，旧的 JWT 立即失效（token 里带 ver 声明）
    token_version: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # 首次随机密码登录后，除查看本人和改密外，其余 API 一律要求先改密
    must_change_password: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0"
    )
    # 用户名要求唯一（应用层会预检，这里用唯一索引兜底并发场景）
    username: Mapped[str] = mapped_column(String(64), default="", unique=True)
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
    # 内容指纹：同一知识库内阻止换名重传和轻微改动的近似文档重复入库
    content_hash: Mapped[str] = mapped_column(String(64), default="", index=True)
    content_simhash: Mapped[str] = mapped_column(String(20), default="")
    content_length: Mapped[int] = mapped_column(Integer, default=0)
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
    # 不可复用的向量身份：删除后同名重建也不会重新捡起旧向量
    vector_key: Mapped[str] = mapped_column(
        String(64), unique=True, index=True, default=new_vector_key
    )
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


class KnowledgeBaseTombstone(Base):
    """知识库删除墓碑：阻止旧 course 名下的历史向量重新进入检索。"""

    __tablename__ = "knowledge_base_tombstones"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    knowledge_base_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    course_name: Mapped[str] = mapped_column(String(128), index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )


class VectorCleanupTask(Base):
    """持久化向量清理任务：进程重启后继续清理残留向量。"""

    __tablename__ = "vector_cleanup_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(
        String(24), default="knowledge_base", server_default="knowledge_base", index=True
    )
    knowledge_base_id: Mapped[str] = mapped_column(
        String(64), default="", server_default="", index=True
    )
    object_id: Mapped[str] = mapped_column(String(64), default="", server_default="")
    course_name: Mapped[str] = mapped_column(
        String(128), default="", server_default="", index=True
    )
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class KnowledgePoint(Base):
    """用户维护的知识点（同样进入向量库参与检索，归属某知识库/课程）。"""

    __tablename__ = "knowledge_points"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(255))
    content: Mapped[str] = mapped_column(Text)
    course: Mapped[str] = mapped_column(String(128), default="", index=True)
    tags: Mapped[str] = mapped_column(String(255), default="")  # 逗号分隔
    # 文档上传自动生成的知识点：只作展示，重建向量时跳过
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
    # 最后修改者（可能与创建者不同）
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
        return self.creator.username or self.creator.account or ""

    @property
    def updater_name(self) -> str:
        """最后修改人显示名（没改过时为空）。"""
        if self.updater is None:
            return ""
        return self.updater.username or self.updater.account or ""


class UnansweredQuestion(Base):
    """知识库未覆盖的问题记录（供管理员查看、补充资料并生成回答）。"""

    __tablename__ = "unanswered_questions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # 展示编号：删除后重排成 1..n，主键 id 不动
    display_no: Mapped[int] = mapped_column(Integer, default=0, index=True)
    question: Mapped[str] = mapped_column(Text)
    course: Mapped[str] = mapped_column(String(128), default="", index=True)  # 推测的关联课程，可为空
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)  # pending / resolved
    # 拒绝回答时知识库中已有的相近资料标题（JSON 数组），用于引导提问者换问法
    related_points: Mapped[str] = mapped_column(Text, default="[]")
    # 管理员补充资料后重新生成的回答；非空即视为已解决
    answer: Mapped[str] = mapped_column(Text, default="")
    # 该未命中问题来自哪次会话/哪条学生消息（NULL=历史数据或无法回溯）：
    # 管理员重新生成后，答案要写回这条消息所在会话，学生才能在原对话里看到
    conversation_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True, index=True
    )
    message_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("messages.id", ondelete="SET NULL"), nullable=True
    )
    answered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # 学生端“已读”标记：学生查看已解决答案后写入（NULL=未读），驱动未读角标
    read_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # 管理员端“已读”标记（NULL=未读），与学生端 read_at 独立
    admin_read_at: Mapped[datetime | None] = mapped_column(
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
        return self.creator.username or self.creator.account or ""


class Conversation(Base):
    """一次问答会话（一个会话内可连续多轮追问）。"""

    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(255), default="新会话")
    # 生成序号：新提问自增，用于同会话新提问顶掉旧流
    gen_seq: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0"
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
    # 这一轮是否拒答（知识库未覆盖）：1=拒答，0=正常回答。
    # 统计拒答率直接读这个字段，而不是拿中文文案 LIKE（门控拒答与模型拒答的措辞不同，
    # LIKE 会漏掉最主要的一类拒答，导致 hit_rate 系统性虚高）。
    rejected: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # 学生对这条回答的评价："" 未评 / "up" 有帮助 / "down" 没帮助（质量反馈信号）
    feedback: Mapped[str] = mapped_column(String(8), default="", server_default="")
    # 点「没帮助」时可填的原因（短文本），用于管理员定位差答/进入未命中队列
    feedback_reason: Mapped[str] = mapped_column(Text, default="", server_default="")
    # 本轮结果：answered / rejected / empty_kb / failed / interrupted
    outcome: Mapped[str] = mapped_column(String(16), default="", server_default="")
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
        return self.deleter.username or self.deleter.account or ""


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


class RevokedToken(Base):
    """已登出的 JWT 的 jti 黑名单。

    JWT 无状态，登出只删 Cookie 并不能让已泄露的 token 失效；这里记下 jti 到
    过期时间，鉴权时命中即拒绝。过期条目在登出/登录时顺手清理。
    """

    __tablename__ = "revoked_tokens"

    jti: Mapped[str] = mapped_column(String(64), primary_key=True)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )


class Semester(Base):
    """学期：课程的归档单位（如「2026 秋季学期」）。"""

    __tablename__ = "semesters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    start_date: Mapped[str] = mapped_column(String(10), default="")  # YYYY-MM-DD
    end_date: Mapped[str] = mapped_column(String(10), default="")
    is_active: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )


class Course(Base):
    """课程：一个学期下的一门课，可绑定一个知识库。"""

    __tablename__ = "courses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128), index=True)
    semester_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("semesters.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # 绑定的知识库（同名课程靠这个字段显式关联，不再依赖名称字符串匹配）
    knowledge_base_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("knowledge_bases.id", ondelete="SET NULL"),
        nullable=True, index=True,
    )
    description: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )


class ClassGroup(Base):
    """教学班：课程下的一次开课，学生通过成员表加入。"""

    __tablename__ = "class_groups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), index=True)
    course_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("courses.id", ondelete="CASCADE"), index=True
    )
    teacher_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )


class ClassMember(Base):
    """班级成员：唯一约束保证同一人不会重复入班。"""

    __tablename__ = "class_members"
    __table_args__ = (
        UniqueConstraint("class_id", "user_id", name="uq_class_member"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    class_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("class_groups.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )


class Notification(Base):
    """站内通知：管理员补答 / 反馈处理结果 / 手动运营消息。

    每个用户只看自己的通知；read_at 为 NULL 表示未读，驱动顶部铃铛角标。
    link 存前端路由（如 ``#/feedback``），点击通知时前端据此跳转。
    """

    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # system（手动运营）/ answer_ready（补答完成）/ feedback_reply（反馈有回复）
    kind: Mapped[str] = mapped_column(String(32), default="system", index=True)
    title: Mapped[str] = mapped_column(String(200), default="")
    body: Mapped[str] = mapped_column(Text, default="")
    link: Mapped[str] = mapped_column(String(300), default="")
    read_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )


class ShareLink(Base):
    """匿名只读分享链接：会话或知识库，带有效期。

    只存目标，不存内容快照：读取时按 kind/target_id 现查，便于随内容更新，过期即失效。
    token 由 secrets.token_urlsafe(32) 生成（43 字符），unique index 防碰撞。
    """

    __tablename__ = "share_links"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    token: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    kind: Mapped[str] = mapped_column(String(24), index=True)  # conversation / knowledge_base
    target_id: Mapped[int] = mapped_column(Integer, index=True)
    created_by: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), index=True
    )
    revoked: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="0"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )

    creator: Mapped["User | None"] = relationship()
