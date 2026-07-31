#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
用户认证与权限管理服务（修复版）。
修复: 使用 CryptContext 统一 bcrypt 哈希；72 字节密码截断保护。
"""

import os
import logging
from datetime import datetime
from typing import Optional

import bcrypt
from itsdangerous import URLSafeTimedSerializer
from fastapi import Request, HTTPException
from sqlalchemy.orm import Session
from dotenv import load_dotenv

from database.models import User

load_dotenv()
logger = logging.getLogger(__name__)

# ---- bcrypt 配置（直接使用 bcrypt 库，避免 passlib 兼容性问题）----
BCRYPT_ROUNDS = 12
# bcrypt 5.0+ 严格限制 72 字节，超过需手动截断
MAX_PASSWORD_BYTES = 72

# ---- Session 密钥 ----
SECRET_KEY = os.getenv("SECRET_KEY", "power-inspection-secret-key-change-me")
serializer = URLSafeTimedSerializer(SECRET_KEY)

SESSION_COOKIE_NAME = "session_id"
SESSION_MAX_AGE = 86400  # 24 小时

# ---- 角色权限 ----
ROLE_PERMISSIONS = {
    "inspector": ["create_order", "view_all_orders", "review_order", "view_all_data", "create_alert"],
    "repairman": ["view_my_orders", "accept_order", "submit_review"],
    "admin": ["create_order", "view_all_orders", "review_order", "view_all_data",
               "create_alert", "manage_users", "system_config"],
}


def hash_password(password: str) -> str:
    """使用 bcrypt 哈希密码（自动截断至 72 字节）。"""
    pwd_bytes = password.encode("utf-8")
    # bcrypt 5.0+ 严格要求密码 ≤ 72 字节
    if len(pwd_bytes) > MAX_PASSWORD_BYTES:
        pwd_bytes = pwd_bytes[:MAX_PASSWORD_BYTES]
    salt = bcrypt.gensalt(rounds=BCRYPT_ROUNDS)
    return bcrypt.hashpw(pwd_bytes, salt).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    """验证密码（与 hash_password 保持相同的 72 字节截断逻辑）。"""
    pwd_bytes = password.encode("utf-8")
    if len(pwd_bytes) > MAX_PASSWORD_BYTES:
        pwd_bytes = pwd_bytes[:MAX_PASSWORD_BYTES]
    try:
        return bcrypt.checkpw(pwd_bytes, password_hash.encode("utf-8"))
    except (ValueError, TypeError, AttributeError):
        logger.warning("密码哈希格式无效")
        return False


# ---- Session 管理 ----

def create_session(user_id: int) -> str:
    """创建签名 session token。"""
    return serializer.dumps({"user_id": user_id, "created_at": datetime.now().isoformat()})


def decode_session(token: str) -> Optional[dict]:
    """解析并验证 session token。"""
    try:
        return serializer.loads(token, max_age=SESSION_MAX_AGE)
    except Exception:
        return None


# ---- 用户管理 ----

def create_user(db: Session, username: str, password: str, role: str,
                full_name: str = None) -> User:
    """创建新用户。"""
    if len(password) < 4:
        raise ValueError("密码至少需要 4 个字符")

    existing = db.query(User).filter(User.username == username).first()
    if existing:
        raise ValueError(f"用户名已存在: {username}")
    if role not in ("inspector", "repairman", "admin"):
        raise ValueError(f"无效角色: {role}")

    user = User(
        username=username,
        password_hash=hash_password(password),
        role=role,
        full_name=full_name or username,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    logger.info(f"新用户注册: {username} ({role})")
    return user


def authenticate(db: Session, username: str, password: str) -> Optional[User]:
    """验证用户名密码，返回 User 或 None。"""
    user = db.query(User).filter(User.username == username).first()
    if not user:
        logger.info(f"登录失败: 用户不存在 {username}")
        return None
    if not verify_password(password, user.password_hash):
        logger.info(f"登录失败: 密码错误 {username}")
        return None
    logger.info(f"登录成功: {username} ({user.role})")
    return user


def init_default_users(db: Session):
    """
    初始化默认账户。
    如果用户已存在但密码哈希格式不对（旧数据库残留），则更新密码哈希。
    """
    defaults = [
        ("admin", "123456", "inspector", "运维管理员"),
        ("worker", "123456", "repairman", "检修人员"),
    ]
    for username, pwd, role, name in defaults:
        existing = db.query(User).filter(User.username == username).first()
        if existing:
            # 验证现有密码是否可用（用相同密码测试）
            if verify_password(pwd, existing.password_hash):
                logger.info(f"预置账户正常: {username}/{role}")
            else:
                # 密码哈希不匹配，更新
                existing.password_hash = hash_password(pwd)
                existing.role = role
                existing.full_name = name
                db.commit()
                logger.warning(f"已修复预置账户密码: {username}")
        else:
            # 创建新账户
            user = User(
                username=username,
                password_hash=hash_password(pwd),
                role=role,
                full_name=name,
            )
            db.add(user)
            db.commit()
            db.refresh(user)
            logger.info(f"已创建预置账户: {username}/{role}")


# ---- FastAPI 依赖注入 ----

def get_current_user(request: Request, db: Session) -> Optional[User]:
    """从 Cookie 中解析当前登录用户。"""
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        return None
    data = decode_session(token)
    if not data:
        return None
    return db.query(User).filter(User.id == data["user_id"]).first()


def require_login(request: Request, db: Session) -> User:
    """强制要求登录，否则抛出 401。"""
    user = get_current_user(request, db)
    if not user:
        raise HTTPException(status_code=401, detail="请先登录")
    return user


def require_role(*roles: str):
    """返回一个依赖函数，限制只有特定角色可访问。"""
    def checker(request: Request, db: Session) -> User:
        user = require_login(request, db)
        if user.role not in roles:
            raise HTTPException(status_code=403, detail="权限不足")
        return user
    return checker


def has_permission(user: User, permission: str) -> bool:
    """检查用户是否拥有特定权限。"""
    return permission in ROLE_PERMISSIONS.get(user.role, [])
