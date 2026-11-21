"""课程 / 班级 / 学期路由。

组织关系：学期 → 课程 → 教学班 → 学生成员，课程可显式绑定一个知识库。

权限：
  - 结构维护（学期/课程/班级/成员/绑定知识库）仅管理员
  - 学生只能读：我的课程、课程下的班级（若在本班）
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user, require_role
from ..models import (
    ClassGroup,
    ClassMember,
    Course,
    KnowledgeBase,
    Semester,
    User,
)
from ..schemas import (
    ClassCreateRequest,
    ClassMemberAddRequest,
    ClassUpdateRequest,
    CourseCreateRequest,
    CourseUpdateRequest,
    SemesterCreateRequest,
    SemesterUpdateRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter()

_admin = require_role("admin")


def _semester_out(row: Semester, course_count: int = 0) -> dict:
    return {
        "id": row.id,
        "name": row.name,
        "start_date": row.start_date,
        "end_date": row.end_date,
        "is_active": bool(row.is_active),
        "course_count": course_count,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def _course_out(row: Course, class_count: int = 0, base_name: str = "", semester_name: str = "") -> dict:
    return {
        "id": row.id,
        "code": row.code,
        "name": row.name,
        "semester_id": row.semester_id,
        "semester_name": semester_name,
        "knowledge_base_id": row.knowledge_base_id,
        "knowledge_base_name": base_name,
        "description": row.description,
        "class_count": class_count,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def _class_out(row: ClassGroup, course_name: str = "", member_count: int = 0) -> dict:
    return {
        "id": row.id,
        "name": row.name,
        "course_id": row.course_id,
        "course_name": course_name,
        "teacher_id": row.teacher_id,
        "member_count": member_count,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


# ---------------- 学期 ----------------


@router.get("/semesters", summary="学期列表")
def list_semesters(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    counts = dict(
        db.execute(
            select(Course.semester_id, func.count(Course.id)).group_by(Course.semester_id)
        ).all()
    )
    rows = db.scalars(select(Semester).order_by(Semester.id.desc())).all()
    return {"items": [_semester_out(r, counts.get(r.id, 0)) for r in rows]}


@router.post("/semesters", summary="新建学期（管理员）", status_code=201)
def create_semester(
    payload: SemesterCreateRequest,
    db: Session = Depends(get_db),
    _: User = Depends(_admin),
):
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="学期名称不能为空")
    row = Semester(
        name=name,
        start_date=payload.start_date.strip()[:10],
        end_date=payload.end_date.strip()[:10],
        is_active=1 if payload.is_active else 0,
    )
    if row.is_active:
        # 同时只允许一个当前学期，避免「我的课程」出现歧义
        db.query(Semester).filter(Semester.is_active == 1).update({"is_active": 0})
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="学期名称已存在")
    db.refresh(row)
    return _semester_out(row)


@router.put("/semesters/{semester_id}", summary="编辑学期（管理员）")
def update_semester(
    semester_id: int,
    payload: SemesterUpdateRequest,
    db: Session = Depends(get_db),
    _: User = Depends(_admin),
):
    row = db.get(Semester, semester_id)
    if row is None:
        raise HTTPException(status_code=404, detail="学期不存在")
    # 只有请求里真的带上来的字段才改，没带的保持原值（部分更新语义）
    fields = payload.model_fields_set
    if "name" in fields:
        name = (payload.name or "").strip()
        if not name:
            raise HTTPException(status_code=422, detail="学期名称不能为空")
        row.name = name
    if "start_date" in fields:
        row.start_date = (payload.start_date or "").strip()[:10]
    if "end_date" in fields:
        row.end_date = (payload.end_date or "").strip()[:10]
    if "is_active" in fields:
        row.is_active = 1 if payload.is_active else 0
        if row.is_active:
            db.query(Semester).filter(
                Semester.is_active == 1, Semester.id != row.id
            ).update({"is_active": 0})
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="学期名称已存在")
    db.refresh(row)
    return _semester_out(row)


@router.delete("/semesters/{semester_id}", summary="删除学期（管理员）")
def delete_semester(
    semester_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(_admin),
):
    row = db.get(Semester, semester_id)
    if row is None:
        raise HTTPException(status_code=404, detail="学期不存在")
    if db.scalar(select(func.count(Course.id)).where(Course.semester_id == semester_id)):
        raise HTTPException(status_code=409, detail="该学期下仍有课程，请先移动或删除课程")
    db.delete(row)
    db.commit()
    return {"ok": True}


# ---------------- 课程 ----------------


@router.get("/courses", summary="课程列表")
def list_courses(
    semester_id: int | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    stmt = select(Course).order_by(Course.id.desc())
    if semester_id:
        stmt = stmt.where(Course.semester_id == semester_id)
    rows = db.scalars(stmt).all()
    class_counts = dict(
        db.execute(
            select(ClassGroup.course_id, func.count(ClassGroup.id)).group_by(ClassGroup.course_id)
        ).all()
    )
    base_names = dict(db.execute(select(KnowledgeBase.id, KnowledgeBase.name)).all())
    semester_names = dict(db.execute(select(Semester.id, Semester.name)).all())
    return {
        "items": [
            _course_out(
                r,
                class_counts.get(r.id, 0),
                base_names.get(r.knowledge_base_id, ""),
                semester_names.get(r.semester_id, ""),
            )
            for r in rows
        ]
    }


@router.post("/courses", summary="新建课程（管理员）", status_code=201)
def create_course(
    payload: CourseCreateRequest,
    db: Session = Depends(get_db),
    _: User = Depends(_admin),
):
    code = payload.code.strip()
    name = payload.name.strip()
    if not code or not name:
        raise HTTPException(status_code=422, detail="课程编号与名称不能为空")
    semester_id = payload.semester_id
    if semester_id is not None and db.get(Semester, semester_id) is None:
        raise HTTPException(status_code=422, detail="学期不存在")
    base_id = payload.knowledge_base_id
    if base_id is not None and db.get(KnowledgeBase, base_id) is None:
        raise HTTPException(status_code=422, detail="知识库不存在")
    row = Course(
        code=code,
        name=name,
        semester_id=semester_id,
        knowledge_base_id=base_id,
        description=payload.description.strip(),
    )
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="课程编号已存在")
    db.refresh(row)
    return _course_out(row)


@router.put("/courses/{course_id}", summary="编辑课程（管理员）")
def update_course(
    course_id: int,
    payload: CourseUpdateRequest,
    db: Session = Depends(get_db),
    _: User = Depends(_admin),
):
    row = db.get(Course, course_id)
    if row is None:
        raise HTTPException(status_code=404, detail="课程不存在")
    fields = payload.model_fields_set
    if "code" in fields:
        code = (payload.code or "").strip()
        if not code:
            raise HTTPException(status_code=422, detail="课程编号不能为空")
        row.code = code
    if "name" in fields:
        name = (payload.name or "").strip()
        if not name:
            raise HTTPException(status_code=422, detail="课程名称不能为空")
        row.name = name
    if "semester_id" in fields:
        value = payload.semester_id
        if value is not None and db.get(Semester, value) is None:
            raise HTTPException(status_code=422, detail="学期不存在")
        row.semester_id = value
    if "knowledge_base_id" in fields:
        value = payload.knowledge_base_id
        if value is not None and db.get(KnowledgeBase, value) is None:
            raise HTTPException(status_code=422, detail="知识库不存在")
        row.knowledge_base_id = value
    if "description" in fields:
        row.description = (payload.description or "").strip()
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="课程编号已存在")
    db.refresh(row)
    return _course_out(row)


@router.delete("/courses/{course_id}", summary="删除课程（管理员）")
def delete_course(
    course_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(_admin),
):
    row = db.get(Course, course_id)
    if row is None:
        raise HTTPException(status_code=404, detail="课程不存在")
    db.delete(row)
    db.commit()
    return {"ok": True}


# ---------------- 教学班 ----------------


@router.get("/courses/{course_id}/classes", summary="课程下的班级")
def list_classes(
    course_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if db.get(Course, course_id) is None:
        raise HTTPException(status_code=404, detail="课程不存在")
    rows = db.scalars(
        select(ClassGroup).where(ClassGroup.course_id == course_id).order_by(ClassGroup.id)
    ).all()
    if user.role != "admin":
        mine = set(
            db.scalars(select(ClassMember.class_id).where(ClassMember.user_id == user.id)).all()
        )
        rows = [r for r in rows if r.id in mine]
    counts = dict(
        db.execute(
            select(ClassMember.class_id, func.count(ClassMember.id)).group_by(ClassMember.class_id)
        ).all()
    )
    course = db.get(Course, course_id)
    course_name = course.name if course else ""
    return {"items": [_class_out(r, course_name=course_name, member_count=counts.get(r.id, 0)) for r in rows]}


@router.post("/courses/{course_id}/classes", summary="新建班级（管理员）", status_code=201)
def create_class(
    course_id: int,
    payload: ClassCreateRequest,
    db: Session = Depends(get_db),
    _: User = Depends(_admin),
):
    course = db.get(Course, course_id)
    if course is None:
        raise HTTPException(status_code=404, detail="课程不存在")
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="班级名称不能为空")
    teacher_id = payload.teacher_id
    if teacher_id is not None and db.get(User, teacher_id) is None:
        raise HTTPException(status_code=422, detail="教师不存在")
    row = ClassGroup(name=name, course_id=course_id, teacher_id=teacher_id)
    db.add(row)
    db.commit()
    db.refresh(row)
    return _class_out(row, course_name=course.name)


@router.put("/classes/{class_id}", summary="编辑班级（管理员）")
def update_class(
    class_id: int,
    payload: ClassUpdateRequest,
    db: Session = Depends(get_db),
    _: User = Depends(_admin),
):
    row = db.get(ClassGroup, class_id)
    if row is None:
        raise HTTPException(status_code=404, detail="班级不存在")
    fields = payload.model_fields_set
    if "name" in fields:
        name = (payload.name or "").strip()
        if not name:
            raise HTTPException(status_code=422, detail="班级名称不能为空")
        row.name = name
    if "teacher_id" in fields:
        value = payload.teacher_id
        if value is not None and db.get(User, value) is None:
            raise HTTPException(status_code=422, detail="教师不存在")
        row.teacher_id = value
    db.commit()
    db.refresh(row)
    return _class_out(row)


@router.delete("/classes/{class_id}", summary="删除班级（管理员）")
def delete_class(
    class_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(_admin),
):
    row = db.get(ClassGroup, class_id)
    if row is None:
        raise HTTPException(status_code=404, detail="班级不存在")
    db.delete(row)
    db.commit()
    return {"ok": True}


@router.get("/classes/{class_id}/members", summary="班级成员（管理员或本班学生）")
def list_members(
    class_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    row = db.get(ClassGroup, class_id)
    if row is None:
        raise HTTPException(status_code=404, detail="班级不存在")
    if user.role != "admin":
        mine = db.scalar(
            select(ClassMember.id).where(
                ClassMember.class_id == class_id, ClassMember.user_id == user.id
            )
        )
        if not mine:
            raise HTTPException(status_code=403, detail="无权查看该班级")
    rows = db.execute(
        select(ClassMember, User)
        .join(User, User.id == ClassMember.user_id)
        .where(ClassMember.class_id == class_id)
        .order_by(ClassMember.id)
    ).all()
    return {
        "items": [
            {
                "user_id": u.id,
                "account": u.account,
                "username": u.username,
                "role": u.role,
                "joined_at": m.created_at.isoformat() if m.created_at else None,
            }
            for m, u in rows
        ]
    }


@router.post("/classes/{class_id}/members", summary="添加学生到班级（管理员）")
def add_member(
    class_id: int,
    payload: ClassMemberAddRequest,
    db: Session = Depends(get_db),
    _: User = Depends(_admin),
):
    if db.get(ClassGroup, class_id) is None:
        raise HTTPException(status_code=404, detail="班级不存在")
    account = (payload.account or "").strip()
    if payload.user_id is not None:
        target = db.get(User, payload.user_id)
    elif account:
        target = db.scalar(select(User).where(User.account == account))
    else:
        raise HTTPException(status_code=422, detail="请提供 account 或 user_id")
    if target is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    if target.role != "student":
        raise HTTPException(status_code=422, detail="只能把学生加入班级")
    exists = db.scalar(
        select(ClassMember.id).where(
            ClassMember.class_id == class_id, ClassMember.user_id == target.id
        )
    )
    if exists:
        return {"ok": True, "added": False}
    db.add(ClassMember(class_id=class_id, user_id=target.id))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
    return {"ok": True, "added": True, "user_id": target.id}


@router.delete("/classes/{class_id}/members/{member_user_id}", summary="移出班级（管理员）")
def remove_member(
    class_id: int,
    member_user_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(_admin),
):
    row = db.scalar(
        select(ClassMember).where(
            ClassMember.class_id == class_id, ClassMember.user_id == member_user_id
        )
    )
    if row is None:
        raise HTTPException(status_code=404, detail="该学生不在班级中")
    db.delete(row)
    db.commit()
    return {"ok": True}


# ---------------- 我的课程（学生） ----------------


@router.get("/my/courses", summary="我所在班级对应的课程")
def my_courses(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    rows = db.execute(
        select(ClassGroup, Course)
        .join(Course, Course.id == ClassGroup.course_id)
        .join(ClassMember, ClassMember.class_id == ClassGroup.id)
        .where(ClassMember.user_id == user.id)
        .order_by(Course.id, ClassGroup.id)
    ).all()
    bases = {
        b.id: b
        for b in db.scalars(
            select(KnowledgeBase).where(
                KnowledgeBase.id.in_({c.knowledge_base_id for _, c in rows if c.knowledge_base_id})
            )
        ).all()
    }
    semesters = dict(db.execute(select(Semester.id, Semester.name)).all())
    grouped: dict[int, dict] = {}
    for cls, course in rows:
        item = grouped.setdefault(
            course.id,
            {
                "id": course.id,
                "code": course.code,
                "name": course.name,
                "description": course.description,
                "semester_name": semesters.get(course.semester_id, ""),
                "knowledge_base": (
                    {"id": bases[course.knowledge_base_id].id,
                     "name": bases[course.knowledge_base_id].name}
                    if course.knowledge_base_id in bases
                    else None
                ),
                "classes": [],
            },
        )
        item["classes"].append({"id": cls.id, "name": cls.name})
    return {"items": list(grouped.values())}
