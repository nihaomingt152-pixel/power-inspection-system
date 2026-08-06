#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
数据库连接管理模块。
支持 SQLite（默认，无需安装）和 MySQL 两种数据库后端。
"""

import os
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()

# 数据库配置（默认 SQLite，可通过 .env 切换为 MySQL）
DB_TYPE = os.getenv("DB_TYPE", "sqlite").lower()

if DB_TYPE == "mysql":
    DB_HOST = os.getenv("DB_HOST", "localhost")
    DB_PORT = os.getenv("DB_PORT", "3306")
    DB_USER = os.getenv("DB_USER", "root")
    DB_PASSWORD = os.getenv("DB_PASSWORD", "123456")
    DB_NAME = os.getenv("DB_NAME", "power_inspection")
    DATABASE_URL = (
        f"mysql+pymysql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
    )
    # MySQL 连接参数
    CONNECT_ARGS = {
        "charset": "utf8mb4",
    }
else:
    # SQLite（默认）
    DB_PATH = os.getenv("DB_PATH", "./database/power_inspection.db")
    # 确保数据库文件在项目目录下
    if not Path(DB_PATH).is_absolute():
        DB_PATH = str(Path(__file__).resolve().parent.parent / DB_PATH)
    # 确保目录存在
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    DATABASE_URL = f"sqlite:///{DB_PATH}"
    CONNECT_ARGS = {
        "check_same_thread": False,  # SQLite 需要此参数以支持多线程
    }

# 创建引擎和会话工厂
engine = create_engine(
    DATABASE_URL,
    connect_args=CONNECT_ARGS,
    echo=False,  # 生产环境关闭 SQL 日志
    pool_pre_ping=True,  # 自动检测断开连接
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# ORM 基类
Base = declarative_base()


def get_db():
    """
    获取数据库会话（用于 FastAPI 依赖注入）。
    使用完毕后自动关闭会话。
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _safe_add_column(table_name: str, column_name: str, column_type: str, default: str = None):
    """
    安全添加列：如果列已存在则跳过，否则执行 ALTER TABLE。
    用于在已有数据库上升级 schema，避免数据丢失。
    """
    import sqlalchemy
    try:
        with engine.connect() as conn:
            # 检查列是否存在（SQLite 用 PRAGMA，MySQL 用 INFORMATION_SCHEMA）
            if DB_TYPE == "mysql":
                result = conn.execute(
                    sqlalchemy.text(
                        "SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS "
                        "WHERE TABLE_NAME=:tbl AND COLUMN_NAME=:col AND TABLE_SCHEMA=DATABASE()"
                    ),
                    {"tbl": table_name, "col": column_name},
                )
                exists = result.scalar() > 0
            else:
                result = conn.execute(
                    sqlalchemy.text(f"PRAGMA table_info({table_name})")
                )
                columns = [row[1] for row in result.fetchall()]
                exists = column_name in columns

            if not exists:
                default_clause = f" DEFAULT {default}" if default is not None else ""
                conn.execute(
                    sqlalchemy.text(
                        f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}{default_clause}"
                    )
                )
                conn.commit()
                print(f"[数据库] 已添加列: {table_name}.{column_name}")
    except Exception as e:
        print(f"[数据库] 添加列 {table_name}.{column_name} 失败: {e}")


def init_db():
    """
    初始化数据库：创建所有表，然后执行增量迁移。
    应在应用启动时调用。
    """
    Base.metadata.create_all(bind=engine)

    # 增量迁移：为新版本添加新增列（Phase 6+）
    if DB_TYPE == "sqlite":
        _safe_add_column("detection_records", "gps_source", "VARCHAR(16)", "'none'")
        _safe_add_column("t_work_orders", "detection_record_id", "INTEGER", "NULL")
        _safe_add_column("t_work_orders", "review_remark", "TEXT", "NULL")
        _safe_add_column("t_work_orders", "close_remark", "TEXT", "NULL")
        _safe_add_column("t_work_orders", "ai_summary", "TEXT", "NULL")
        _safe_add_column("t_video_detections", "frame_images", "TEXT", "'[]'")
        _safe_add_column("t_video_detections", "file_md5", "VARCHAR(32)", "NULL")
        _safe_add_column("t_video_detections", "video_summary", "TEXT", "NULL")
    else:
        _safe_add_column("detection_records", "gps_source", "VARCHAR(16)", "'none'")
        _safe_add_column("t_work_orders", "detection_record_id", "INTEGER", "NULL")
        _safe_add_column("t_work_orders", "review_remark", "TEXT", "NULL")
        _safe_add_column("t_work_orders", "close_remark", "TEXT", "NULL")
        _safe_add_column("t_work_orders", "ai_summary", "JSON", "NULL")
        _safe_add_column("t_video_detections", "frame_images", "JSON", "'[]'")
        _safe_add_column("t_video_detections", "file_md5", "VARCHAR(32)", "NULL")
        _safe_add_column("t_video_detections", "video_summary", "JSON", "NULL")

    print(f"[数据库] 初始化完成，类型: {DB_TYPE}")


def get_db_session():
    """
    获取一个数据库会话（用于非 FastAPI 依赖注入场景）。
    调用者负责在适当时候关闭会话。
    """
    return SessionLocal()
