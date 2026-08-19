#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
电力输电线路智能检测分析预警系统 v3.0 - Phase 2 全功能版。
新增: 用户认证、工单管理、GIS 地图、兜底检测、GPU 监控、数据仪表盘。
"""

import os, sys, uuid, base64, logging, tempfile, threading, json, hashlib, time
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, List

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from fastapi import (
    FastAPI, File, UploadFile, Form, Query, HTTPException, Depends, Request, Body,
)
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, RedirectResponse
from fastapi.encoders import jsonable_encoder
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv()

from database.db_connection import init_db, get_db, get_db_session
from database.models import DetectionRecord, VideoTask, User, WorkOrder
from services.detection_service import (
    get_detector, process_single_image, process_video_file, predict_frame_base64,
)
from services.auth_service import (
    authenticate, create_user, init_default_users, get_current_user,
    require_login, create_session, SESSION_COOKIE_NAME, SESSION_MAX_AGE,
)
from services.work_order_service import (
    create_work_order, accept_order, submit_review, approve_order,
    reject_order, reject_order_worker, reassign_order, list_work_orders,
    get_order_detail, get_repairmen, get_order_stats, delete_order, get_order_logs,
)
from services.fallback_service import FallbackDetector
from services.monitor_service import get_gpu_info

# ---- 日志 ----
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(PROJECT_ROOT / "app.log", encoding="utf-8", mode="a"),
    ],
)
logger = logging.getLogger("app")


def _json_resp(data):
    """统一 JSON 响应封装：jsonable_encoder 确保 datetime 等类型可序列化。

    历史教训：JSONResponse 的 content 直接经 json.dumps 序列化，datetime 会报
    "Object of type datetime is not JSON serializable"，必须先转成可序列化类型。
    """
    return JSONResponse(content=jsonable_encoder(data))

# ---- FastAPI 应用 ----
app = FastAPI(title="电力输电线路智能检测分析预警系统 v3.0", version="3.0.0", docs_url="/docs")

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])
app.add_middleware(
    SessionMiddleware,
    secret_key=os.getenv("SECRET_KEY", "power-inspection-secret-key-change-me"),
    session_cookie=SESSION_COOKIE_NAME,
    max_age=SESSION_MAX_AGE,
)

STATIC_DIR = PROJECT_ROOT / "static"
UPLOADS_DIR = STATIC_DIR / "uploads"
TEMPLATES_DIR = PROJECT_ROOT / "templates"
REPORTS_DIR = PROJECT_ROOT / "reports"

for d in [UPLOADS_DIR, REPORTS_DIR, UPLOADS_DIR / "annotated",
          STATIC_DIR / "outputs", UPLOADS_DIR / "temp", UPLOADS_DIR / "repair"]:
    d.mkdir(parents=True, exist_ok=True)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "5000"))


# ---- 生命周期 ----
@app.on_event("startup")
async def startup():
    logger.info("=" * 50)
    logger.info("系统 v3.0 启动中...")
    try:
        init_db()
        # 初始化默认用户
        db = get_db_session()
        init_default_users(db)
        db.close()
    except Exception as e:
        logger.error(f"DB 初始化失败: {e}")
    try:
        get_detector()
        logger.info("YOLO 模型加载成功")
    except Exception as e:
        logger.error(f"YOLO 模型加载失败: {e}")
    logger.info(f"监听 http://{HOST}:{PORT}")
    logger.info("=" * 50)


# ---- 页面路由 ----

def _render_template(filename: str) -> HTMLResponse:
    path = TEMPLATES_DIR / filename
    if not path.exists():
        return HTMLResponse(f"<h2>{filename} 未创建</h2>")
    with open(path, encoding="utf-8") as f:
        return HTMLResponse(f.read())


@app.get("/", response_class=HTMLResponse)
async def index(request: Request, db=Depends(get_db)):
    """主页：Admin 可访问，Worker 重定向到仪表盘。"""
    user = _try_get_user(request, db)
    if user and user.role == "repairman":
        return RedirectResponse(url="/dashboard", status_code=302)
    return _render_template("index.html")

@app.get("/login", response_class=HTMLResponse)
async def login_page(): return _render_template("login.html")

@app.get("/register", response_class=HTMLResponse)
async def register_page(): return _render_template("login.html")

@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page(): return _render_template("dashboard.html")

@app.get("/orders", response_class=HTMLResponse)
async def orders_page(): return _render_template("orders.html")

@app.get("/map", response_class=HTMLResponse)
async def map_page(): return _render_template("map.html")

@app.get("/video/play/{task_id}", response_class=HTMLResponse)
async def video_player_page(task_id: str): return _render_template("video_player.html")


# ============================================================
# API: 认证
# ============================================================

@app.post("/api/auth/login")
def api_login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db=Depends(get_db),
):
    user = authenticate(db, username, password)
    if not user:
        raise HTTPException(401, "用户名或密码错误")
    token = create_session(user.id)
    resp = _json_resp({"success": True, "data": user.to_dict()})
    resp.set_cookie(SESSION_COOKIE_NAME, token, max_age=SESSION_MAX_AGE,
                    httponly=True, samesite="lax")
    return resp


@app.post("/api/auth/register")
def api_register(
    username: str = Form(...),
    password: str = Form(...),
    role: str = Form("inspector"),
    full_name: str = Form(""),
    db=Depends(get_db),
):
    if not username or not password:
        raise HTTPException(400, "用户名和密码不能为空")
    if role not in ("inspector", "repairman"):
        raise HTTPException(400, "角色必须为 inspector 或 repairman")
    try:
        user = create_user(db, username, password, role, full_name or username)
        return _json_resp({"success": True, "data": user.to_dict()})
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/auth/logout")
def api_logout():
    resp = _json_resp({"success": True})
    resp.delete_cookie(SESSION_COOKIE_NAME)
    return resp


@app.get("/api/auth/me")
def api_me(request: Request, db=Depends(get_db)):
    user = get_current_user(request, db)
    if not user:
        return _json_resp({"success": True, "data": None})
    return _json_resp({"success": True, "data": user.to_dict()})


# ---- 登录检查辅助 ----

def _try_get_user(request: Request, db) -> Optional[User]:
    """尝试获取用户，不强制要求登录。"""
    return get_current_user(request, db)


def _require_inspector_or_admin(request: Request, db=Depends(get_db)) -> User:
    """要求运维/管理员角色，否则返回 403。"""
    user = require_login(request, db)
    if user.role not in ("inspector", "admin"):
        raise HTTPException(403, "仅运维人员可执行此操作")
    return user


# ============================================================
# API: 图片检测（保留 Phase 1 + 兜底检测增强）
# ============================================================

ALLOWED_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}


def _save_upload_temp(file: UploadFile) -> str:
    suffix = Path(file.filename).suffix.lower()
    fd, tmp = tempfile.mkstemp(suffix=suffix, prefix="upload_")
    os.close(fd)
    with open(tmp, "wb") as f:
        f.write(file.file.read())
    return tmp


@app.post("/api/upload/image")
def api_upload_image(
    file: UploadFile = File(...),
    call_ai: bool = Form(True),
    call_fallback: bool = Form(True),
    mobile: bool = Form(False),
    user: User = Depends(_require_inspector_or_admin),
):
    file_ext = Path(file.filename).suffix.lower()
    if file_ext not in ALLOWED_IMAGE_EXTS:
        raise HTTPException(400, f"不支持的格式: {file_ext}")
    tmp_path = None
    try:
        tmp_path = _save_upload_temp(file)
        # 1. YOLO 检测 + AI 分析（是否调用 AI 由 call_ai 决定）
        result = process_single_image(
            image_path=tmp_path, save_annotated=True, call_ai=call_ai,
        )

        # 2. 兜底异物检测（独立于 AI 分析，仅受 call_fallback 控制）
        #    无论 call_ai 是否开启，只要 call_fallback 开启且配置了 API Key 就执行
        fallback_result = None
        if call_fallback and os.getenv("DASHSCOPE_API_KEY"):
            try:
                fb = FallbackDetector()
                fb_result, _ = fb.check(tmp_path)
                fallback_result = {
                    "description": fb_result.get("description", ""),
                    "is_abnormal": bool(fb_result.get("is_abnormal", False)),
                    "confidence": fb_result.get("confidence", "low"),
                }
                # 无论是否异常都保存完整结果，供历史详情展示；未勾选兜底时保持不写入
                db2 = get_db_session()
                try:
                    rec = db2.query(DetectionRecord).filter(
                        DetectionRecord.id == result.get("record_id")
                    ).first()
                    if rec:
                        rec.fallback_result = fallback_result
                        rec.has_abnormal = bool(fallback_result["is_abnormal"])
                        rec.abnormal_desc = fallback_result["description"]
                        db2.commit()
                finally:
                    db2.close()
            except Exception as e:
                logger.warning(f"兜底检测异常: {e}")

        # 3. 组装返回（fallback_result 结构化 + 兼容旧字段）
        has_abnormal = bool(fallback_result and fallback_result["is_abnormal"])
        result["fallback_result"] = fallback_result
        result["has_abnormal"] = has_abnormal
        result["abnormal_desc"] = fallback_result["description"] if fallback_result else ""
        # 语音播报标识（依赖 AI 分析的严重等级）
        ai = result.get("ai_analysis") or {}
        sev = ai.get("severity", "一般")
        result["speech_alert"] = sev in ("严重", "紧急")

        # 移动端精简返回：图片 URL 指向缩略图，避免公网加载大图（AI 分析文本保持完整）
        if mobile and result.get("annotated_thumb_path"):
            result["annotated_image_path"] = result.pop("annotated_thumb_path")

        return _json_resp({"success": True, "data": result, "message": "检测完成"})
    except Exception as e:
        logger.error(f"图片处理失败: {e}", exc_info=True)
        raise HTTPException(500, f"处理失败: {str(e)}")
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try: os.unlink(tmp_path)
            except OSError: pass


@app.post("/api/upload/batch")
def api_upload_batch(
    files: List[UploadFile] = File(...),
    call_ai: bool = Form(True),
    user: User = Depends(_require_inspector_or_admin),
):
    if not files:
        raise HTTPException(400, "请至少上传一个文件")
    results, errors = [], []
    for file in files:
        tmp_path = None
        try:
            tmp_path = _save_upload_temp(file)
            result = process_single_image(
                image_path=tmp_path, save_annotated=True, call_ai=call_ai,
            )
            ai = result.get("ai_analysis") or {}
            result["speech_alert"] = ai.get("severity", "一般") in ("严重", "紧急")
            result["original_filename"] = file.filename
            results.append(result)
        except Exception as e:
            errors.append({"filename": file.filename, "error": str(e)})
        finally:
            if tmp_path and os.path.exists(tmp_path):
                try: os.unlink(tmp_path)
                except OSError: pass
    return _json_resp({
        "success": len(errors) == 0, "total": len(files),
        "processed": len(results), "errors": errors, "data": results,
    })


# ============================================================
# API: 视频分析（保留 Phase 1）
# ============================================================

ALLOWED_VIDEO_EXTS = {".mp4", ".avi", ".mov", ".mkv", ".wmv", ".flv", ".webm"}
MAX_VIDEO_SIZE = int(os.getenv("MAX_VIDEO_SIZE", str(500 * 1024 * 1024)))  # 500MB 上限
_video_progress: dict = {}
_video_progress_lock = threading.Lock()


def _update_progress(task_id: str, processed: int, total: int, start_time: float):
    """更新进度并估算剩余时间（优化2：进度可视化）。"""
    with _video_progress_lock:
        elapsed = time.time() - start_time
        avg_time = elapsed / processed if processed > 0 else 0
        remaining = avg_time * (total - processed) if total > 0 else 0
        _video_progress[task_id] = {
            "status": "processing",
            "processed": processed,
            "total": total,
            "elapsed_seconds": int(elapsed),
            "estimated_remaining": int(remaining),
            "progress_percent": int((processed / total) * 100) if total > 0 else 0,
        }


def _run_video_processing(task_id: str, video_path: str, call_ai: bool, fallback_interval: int = 5, user_id: int = None, file_md5: str = None):
    db = get_db_session()
    start_time = time.time()
    try:
        with _video_progress_lock:
            _video_progress[task_id] = {"status": "processing", "processed": 0, "total": 0}
        result = process_video_file(
            video_path=video_path, db=db, task_id=task_id, call_ai=call_ai, user_id=user_id,
            file_md5=file_md5,
            progress_callback=lambda p, t: _update_progress(task_id, p, t, start_time),
        )
        with _video_progress_lock:
            _video_progress[task_id] = {
                "status": "completed", "processed": result["total_frames"],
                "total": result["total_frames"], "output_video": result["output_video"],
                "ai_reports": result["ai_reports"], "stats": result["stats"],
                "video_summary": result.get("video_summary"),
                "progress_percent": 100, "estimated_remaining": 0, "elapsed_seconds": int(time.time() - start_time),
            }
    except Exception as e:
        logger.error(f"视频异常 [{task_id}]: {e}", exc_info=True)
        with _video_progress_lock:
            _video_progress[task_id] = {"status": "failed", "error": str(e)}
        try:
            t = db.query(VideoTask).filter(VideoTask.task_id == task_id).first()
            if t: t.status = "failed"; db.commit()
        except: pass
    finally:
        db.close()
        try:
            if os.path.exists(video_path): os.unlink(video_path)
        except OSError: pass


@app.post("/api/upload/video")
def api_upload_video(file: UploadFile = File(...), call_ai: bool = Form(True),
                     fallback_interval: int = Form(5),
                     user: User = Depends(_require_inspector_or_admin)):
    file_ext = Path(file.filename).suffix.lower()
    if file_ext not in ALLOWED_VIDEO_EXTS:
        raise HTTPException(400, f"不支持的格式: {file_ext}")
    fd, tmp = tempfile.mkstemp(suffix=file_ext, prefix="video_")
    os.close(fd)
    with open(tmp, "wb") as f:
        f.write(file.file.read())

    # 文件大小校验（优化1 后端兜底：移动端未压缩时拒绝超大文件）
    if os.path.getsize(tmp) > MAX_VIDEO_SIZE:
        try: os.unlink(tmp)
        except OSError: pass
        raise HTTPException(400, "视频文件过大，请压缩后重试（限制 500MB）")

    # 计算文件 MD5（流式，不占用内存；优化7 重复上传缓存）
    hash_md5 = hashlib.md5()
    with open(tmp, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            hash_md5.update(chunk)
    file_md5 = hash_md5.hexdigest()

    # 查询缓存：相同视频已分析过则直接返回历史结果
    db = get_db_session()
    try:
        from database.models import VideoDetectionRecord as VDR
        cached = db.query(VDR).filter(VDR.file_md5 == file_md5).first()
        if cached and cached.status == "completed":
            # 从 frames_data 重建 AI 报告（保持时间轴展示）
            cached_ai_reports = []
            for fd in (cached.frames_data or []):
                ai = fd.get("ai_analysis")
                if ai:
                    cached_ai_reports.append({
                        "timestamp": fd.get("timestamp", 0),
                        "frame_index": fd.get("frame_index", 0),
                        "description": ai.get("description", "")[:300],
                        "severity": ai.get("severity", "未知"),
                        "cause": ai.get("cause", ""),
                        "suggestion": ai.get("suggestion", ""),
                    })
            cached_ai_reports.sort(key=lambda x: x["frame_index"])
            return _json_resp({"success": True, "data": {
                "task_id": cached.task_id,
                "from_cache": True,
                "record_id": cached.id,
                "status": "completed",
                "progress_percent": 100,
                "processed_frames": cached.total_frames,
                "total_frames": cached.total_frames,
                "output_video": cached.output_video_path,
                "ai_reports": cached_ai_reports,
                "video_summary": cached.video_summary,
                "stats": {"total_frames": cached.total_frames, "defect_count": cached.defect_count,
                          "severity": cached.severity, "from_cache": True},
            }})
    finally:
        db.close()

    task_id = uuid.uuid4().hex
    db = get_db_session()
    try:
        vt = VideoTask(task_id=task_id, original_filename=file.filename,
                       status="pending", total_frames=0, processed_frames=0)
        db.add(vt); db.commit()
    except Exception as e:
        db.rollback(); raise HTTPException(500, str(e))
    finally: db.close()
    threading.Thread(target=_run_video_processing,
                     args=(task_id, tmp, call_ai, fallback_interval, user.id, file_md5), daemon=True).start()
    return _json_resp({"success": True, "data": {"task_id": task_id, "status": "pending"}})


@app.get("/api/video/progress/{task_id}")
def api_video_progress(task_id: str):
    with _video_progress_lock:
        mem = _video_progress.get(task_id)
    db = get_db_session()
    try:
        task = db.query(VideoTask).filter(VideoTask.task_id == task_id).first()
        if not task: raise HTTPException(404, "任务不存在")
        data = task.to_dict()
        if mem: data.update(mem)
        return _json_resp({"success": True, "data": data})
    finally: db.close()


# ---- 视频检测记录 API（Phase 7）----

def _delete_video_record(db, record):
    """删除视频检测记录：清理帧图片/关键帧图片并删除关联的 VideoTask。"""
    frame_images = record.frame_images or []
    keyframe_images = record.keyframe_images or []
    all_images = frame_images + [kf for kf in keyframe_images
                                 if not any(fi.get("image_path") == kf.get("image_path") for fi in frame_images)]
    deleted_files = 0

    # 删除物理图片文件
    for img in all_images:
        img_path = img.get("image_path") if isinstance(img, dict) else img
        if not img_path:
            continue
        # 兼容绝对/相对路径
        fp = Path(img_path)
        if not fp.is_absolute():
            fp = PROJECT_ROOT / fp
        if fp.exists() and fp.is_file():
            try:
                fp.unlink()
                deleted_files += 1
            except OSError as e:
                logger.warning(f"删除帧图片失败: {fp}, {e}")

    # 删除关联的 VideoTask（如有）
    try:
        vt = db.query(VideoTask).filter(VideoTask.task_id == record.task_id).first()
        if vt:
            db.delete(vt)
    except Exception as e:
        logger.warning(f"删除 VideoTask 失败: {e}")

    db.delete(record)
    db.commit()
    return deleted_files


@app.get("/api/video/records/{record_id}")
def api_video_record_detail(record_id: int, request: Request, db=Depends(get_db)):
    """获取视频检测记录详情（含完整 frames_data）。"""
    require_login(request, db)
    from database.models import VideoDetectionRecord as VDR
    r = db.query(VDR).filter(VDR.id == record_id).first()
    if not r:
        raise HTTPException(404, "视频记录不存在")
    return _json_resp({"success": True, "data": r.to_dict()})


@app.delete("/api/video/records/{record_id}")
def api_video_record_delete(record_id: int, request: Request, db=Depends(get_db)):
    """Admin 删除视频检测记录及其关联的帧图片文件。"""
    user = require_login(request, db)
    if user.role not in ("inspector", "admin"):
        raise HTTPException(403, "仅运维人员可删除视频记录")
    from database.models import VideoDetectionRecord as VDR

    r = db.query(VDR).filter(VDR.id == record_id).first()
    if not r:
        raise HTTPException(404, "视频记录不存在")

    deleted_files = _delete_video_record(db, r)
    logger.info(f"视频记录 #{record_id} 已删除（含 {deleted_files} 张帧图片）by user {user.id}")

    return _json_resp({"success": True, "data": {
        "id": record_id,
        "deleted_images": deleted_files,
    }, "message": f"视频记录已删除，清理了 {deleted_files} 张帧图片"})


@app.get("/api/video/records/{record_id}/frame/{frame_index}")
def api_video_record_frame(record_id: int, frame_index: int, request: Request, db=Depends(get_db)):
    """获取单帧详情（含 AI 分析和图片路径）。"""
    require_login(request, db)
    from database.models import VideoDetectionRecord as VDR
    r = db.query(VDR).filter(VDR.id == record_id).first()
    if not r or not r.frames_data:
        raise HTTPException(404, "视频记录或帧数据不存在")

    frame = None
    for f in r.frames_data:
        if f.get("frame_index") == frame_index:
            frame = f
            break
    if not frame:
        raise HTTPException(404, f"帧 #{frame_index} 不存在")

    # 查找对应的帧图片（优先 frame_images，降级 keyframe_images 兼容旧数据）
    image_url = None
    frame_images = r.frame_images or []
    if frame_images:
        for fi in frame_images:
            if fi.get("frame_index") == frame_index:
                image_url = "/" + fi["image_path"]
                break
    if not image_url and r.keyframe_images:
        for kf in r.keyframe_images:
            if kf.get("frame_index") == frame_index:
                image_url = "/" + kf["image_path"]
                break

    # Phase 29: 推断帧等级（兼容旧数据：has_defect → 严重，否则正常）
    original_severity = frame.get("original_severity") or ("严重" if frame.get("has_defect") else "正常")
    severity_override = frame.get("severity_override")

    return _json_resp({"success": True, "data": {
        **frame,
        "original_severity": original_severity,
        "severity_override": severity_override,
        "effective_severity": severity_override or original_severity,
        "severity_modified_at": frame.get("severity_modified_at"),
        "severity_modified_by": frame.get("severity_modified_by"),
        "severity_modified_by_name": frame.get("severity_modified_by_name"),
        "image_url": image_url,
        "total_frames": r.total_frames,
        "output_video_path": r.output_video_path,
    }})


@app.put("/api/video/records/{record_id}/frame/{frame_index}/severity")
def api_video_record_frame_severity(record_id: int, frame_index: int,
                                    severity: str = Body(..., embed=True),
                                    request: Request = None,
                                    db=Depends(get_db)):
    """手动修改单帧预警等级（Phase 29，仅运维/管理员）。

    更新 frames_data 中对应帧的 severity_override 及修改记录，
    前端据此可对任意帧（含 YOLO 判定为正常者）生成工单。
    """
    user = require_login(request, db)
    if user.role not in ("inspector", "admin"):
        raise HTTPException(403, "仅运维人员可修改预警等级")
    if severity not in ("正常", "一般", "严重", "紧急"):
        raise HTTPException(400, "等级必须为: 正常/一般/严重/紧急")

    from database.models import VideoDetectionRecord as VDR
    r = db.query(VDR).filter(VDR.id == record_id).first()
    if not r or not r.frames_data:
        raise HTTPException(404, "视频记录或帧数据不存在")
    frame = None
    for f in r.frames_data:
        if f.get("frame_index") == frame_index:
            frame = f
            break
    if not frame:
        raise HTTPException(404, f"帧 #{frame_index} 不存在")

    # 记录原始等级（首次修改时按 YOLO 结果推断）
    if not frame.get("original_severity"):
        frame["original_severity"] = "严重" if frame.get("has_defect") else "正常"

    frame["severity_override"] = severity
    frame["severity_modified_at"] = datetime.now().isoformat()
    frame["severity_modified_by"] = user.id
    frame["severity_modified_by_name"] = user.full_name or user.username

    # JSON 列显式标记变更（SQLAlchemy 对可变形列表不自动追踪）
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(r, "frames_data")
    db.commit()

    return _json_resp({"success": True, "data": {
        "frame_index": frame_index,
        "original_severity": frame["original_severity"],
        "severity_override": severity,
        "effective_severity": severity,
        "severity_modified_at": frame["severity_modified_at"],
        "severity_modified_by": frame["severity_modified_by"],
        "severity_modified_by_name": frame["severity_modified_by_name"],
    }})


@app.get("/api/video/records/{record_id}/keyframes")
def api_video_record_keyframes(record_id: int, request: Request, db=Depends(get_db)):
    """获取关键帧列表（用于网格展示）。"""
    require_login(request, db)
    from database.models import VideoDetectionRecord as VDR
    r = db.query(VDR).filter(VDR.id == record_id).first()
    if not r:
        raise HTTPException(404, "视频记录不存在")

    keyframes = r.keyframe_images or []
    # 补充无图片帧的基本信息
    result = []
    for kf in keyframes:
        result.append({
            "frame_index": kf["frame_index"],
            "timestamp": kf.get("timestamp", 0),
            "image_url": "/" + kf["image_path"] if kf.get("image_path") else None,
            "has_defect": kf.get("has_defect", False),
            "severity": kf.get("severity", "一般"),
        })

    return _json_resp({"success": True, "data": {
        "keyframes": result,
        "total_frames": r.total_frames,
        "output_video_path": r.output_video_path,
    }})


@app.get("/api/export/video/{filename:path}")
def api_export_video(filename: str):
    fp = STATIC_DIR / "outputs" / Path(filename).name
    if not fp.exists(): raise HTTPException(404, "视频不存在")
    return FileResponse(str(fp), media_type="video/mp4", filename=Path(filename).name)


# ============================================================
# API: 摄像头帧预测（保留 Phase 1）
# ============================================================

@app.post("/api/predict_frame")
def api_predict_frame(payload: dict = Body(...),
                      user: User = Depends(_require_inspector_or_admin)):
    b64 = payload.get("image", "")
    if not b64: raise HTTPException(400, "缺少 image 字段")
    try:
        result = predict_frame_base64(b64)
        return _json_resp({"success": True, "data": result})
    except Exception as e:
        raise HTTPException(500, str(e))


# ============================================================
# API: 工单管理（Phase 2 新增）
# ============================================================

@app.post("/api/orders")
def api_create_order(
    request: Request,
    title: str = Form(...),
    description: str = Form(""),
    severity: str = Form("一般"),
    assigned_to: int = Form(...),
    original_image_path: str = Form(""),
    annotated_image_path: str = Form(""),
    gps_lat: float = Form(None),
    gps_lng: float = Form(None),
    detection_record_id: int = Form(None),
    ai_summary: str = Form(""),
    db=Depends(get_db),
):
    user = require_login(request, db)
    if user.role not in ("inspector", "admin"):
        raise HTTPException(403, "仅运维人员可创建工单")
    # 前端以 JSON 字符串提交 AI 摘要（图片记录 = ai_analysis，视频帧 = video_summary）
    ai_summary_dict = None
    if ai_summary:
        try:
            ai_summary_dict = json.loads(ai_summary)
        except (ValueError, TypeError):
            logger.warning(f"ai_summary 解析失败: {ai_summary[:100]}")
    try:
        order = create_work_order(
            db, title=title, description=description, severity=severity,
            created_by=user.id, assigned_to=assigned_to,
            original_image_path=original_image_path,
            annotated_image_path=annotated_image_path,
            gps_lat=gps_lat, gps_lng=gps_lng,
            detection_record_id=detection_record_id,
            ai_summary=ai_summary_dict,
        )
        return _json_resp({"success": True, "data": order.to_dict()})
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/orders")
def api_list_orders(
    request: Request,
    status: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, le=100),
    db=Depends(get_db),
):
    user = require_login(request, db)
    result = list_work_orders(db, user, status_filter=status, page=page, page_size=page_size)
    return _json_resp({"success": True, "data": result})


@app.get("/api/orders/{order_id}")
def api_order_detail(order_id: int, request: Request, db=Depends(get_db)):
    require_login(request, db)
    try:
        order = get_order_detail(db, order_id)
        # 兼容旧工单：关联了检测记录但未存图片/AI摘要时自动补（Worker 端工单详情显示）
        if order.detection_record_id and (not order.annotated_image_path or not order.ai_summary):
            from database.models import DetectionRecord
            det = db.query(DetectionRecord).filter(DetectionRecord.id == order.detection_record_id).first()
            if det:
                if not order.annotated_image_path and det.annotated_image_path:
                    order.annotated_image_path = det.annotated_image_path
                if not order.ai_summary and det.ai_analysis:
                    order.ai_summary = det.ai_analysis
        return _json_resp({"success": True, "data": order.to_dict()})
    except ValueError as e:
        raise HTTPException(404, str(e))


@app.post("/api/orders/{order_id}/accept")
def api_accept_order(order_id: int, request: Request, db=Depends(get_db)):
    user = require_login(request, db)
    try:
        order = accept_order(db, order_id, user.id)
        return _json_resp({"success": True, "data": order.to_dict()})
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/orders/{order_id}/submit-review")
def api_submit_review(
    order_id: int, request: Request,
    repair_image: Optional[UploadFile] = File(None),
    review_remark: str = Form(""),
    db=Depends(get_db),
):
    user = require_login(request, db)
    repair_path = None
    if repair_image:
        repair_dir = UPLOADS_DIR / "repair"
        repair_dir.mkdir(exist_ok=True)
        fn = f"repair_{order_id}_{uuid.uuid4().hex[:8]}_{repair_image.filename}"
        repair_path = str(repair_dir / fn)
        with open(repair_path, "wb") as f:
            f.write(repair_image.file.read())
    try:
        order = submit_review(db, order_id, user.id, repair_path,
                              review_remark=review_remark or None)
        return _json_resp({"success": True, "data": order.to_dict()})
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/orders/{order_id}/approve")
def api_approve_order(
    order_id: int, request: Request,
    close_remark: str = Form(""),
    db=Depends(get_db),
):
    """确认闭环（可附带闭环备注，用于导出报告）。"""
    user = require_login(request, db)
    try:
        order = approve_order(db, order_id, user.id, close_remark=close_remark)
        return _json_resp({"success": True, "data": order.to_dict()})
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/orders/{order_id}/reject")
def api_reject_order(
    order_id: int, request: Request,
    reason: str = Form(""),
    db=Depends(get_db),
):
    user = require_login(request, db)
    try:
        order = reject_order(db, order_id, user.id, reason or "需要重新检修")
        return _json_resp({"success": True, "data": order.to_dict()})
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/orders/{order_id}/reassign")
def api_reassign_order(
    order_id: int, request: Request,
    payload: dict = Body(...),
    db=Depends(get_db),
):
    """Admin 重新派发已驳回的工单（JSON body）。"""
    user = require_login(request, db)
    if user.role not in ("inspector", "admin"):
        raise HTTPException(403, "仅运维人员可重新派发工单")
    try:
        order = reassign_order(
            db, order_id=order_id, user_id=user.id,
            assigned_to=payload.get("assigned_to"),
            title=payload.get("title"),
            description=payload.get("description"),
            severity=payload.get("severity"),
        )
        return _json_resp({"success": True, "data": order.to_dict()})
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/orders/{order_id}/reject-worker")
def api_reject_order_worker(
    order_id: int, request: Request,
    reason: str = Form(...),
    remark: str = Form(""),
    db=Depends(get_db),
):
    """Worker 驳回工单（仅 processing 状态）。"""
    user = require_login(request, db)
    # 校验：选择"其他"时补充说明必填
    if reason == "其他" and not remark.strip():
        raise HTTPException(400, "选择「其他」时请填写补充说明")
    try:
        order = reject_order_worker(db, order_id, user.id, reason, remark)
        return _json_resp({"success": True, "data": order.to_dict()})
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.delete("/api/orders/{order_id}/delete")
def api_delete_order(order_id: int, request: Request, db=Depends(get_db)):
    """Admin 硬删除工单。"""
    user = require_login(request, db)
    if user.role not in ("inspector", "admin"):
        raise HTTPException(403, "仅运维人员可删除工单")
    try:
        info = delete_order(db, order_id, user.id)
        return _json_resp({"success": True, "data": info,
                             "message": f"工单 #{order_id} 已删除"})
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/orders/batch-delete")
def api_batch_delete_orders(request: Request, payload: dict = Body(...), db=Depends(get_db)):
    """批量硬删除工单，支持部分失败。"""
    user = require_login(request, db)
    if user.role not in ("inspector", "admin"):
        raise HTTPException(403, "仅运维人员可删除工单")

    order_ids = payload.get("ids") or []
    if not isinstance(order_ids, list) or not order_ids:
        raise HTTPException(400, "请至少选择一个工单")

    deleted = 0
    failed = []
    for order_id in order_ids:
        try:
            delete_order(db, order_id, user.id)
            deleted += 1
        except ValueError as e:
            failed.append({"id": order_id, "reason": str(e)})
        except Exception as e:
            db.rollback()
            failed.append({"id": order_id, "reason": str(e)})
            logger.error(f"批量删除工单失败: id={order_id}, {e}")

    logger.info(f"批量删除工单: 成功 {deleted} 条，失败 {len(failed)} 条 by user {user.id}")
    return _json_resp({"success": True, "data": {
        "deleted": deleted,
        "failed": failed,
    }, "message": f"已删除 {deleted} 个工单"})


@app.get("/api/orders/{order_id}/logs")
def api_order_logs(order_id: int, request: Request, db=Depends(get_db)):
    """获取工单操作日志。"""
    require_login(request, db)
    logs = get_order_logs(db, order_id)
    return _json_resp({"success": True, "data": logs})


@app.get("/api/orders/{order_id}/export-report")
def api_export_order_report(order_id: int, request: Request, db=Depends(get_db)):
    """
    导出工单闭环报告（Word 文档，Phase 7.2）。
    仅 closed（已闭环）状态的工单允许导出。
    """
    require_login(request, db)
    try:
        order = get_order_detail(db, order_id)
    except ValueError as e:
        raise HTTPException(404, str(e))

    if order.status != "closed":
        raise HTTPException(400, "仅已闭环状态的工单可导出报告")

    try:
        from services.report_service import export_work_order_report
        path = export_work_order_report(order, db, REPORTS_DIR)
        return FileResponse(
            path=path,
            filename=Path(path).name,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
    except Exception as e:
        logger.error(f"工单 #{order_id} 报告导出失败: {e}")
        raise HTTPException(500, f"报告生成失败: {e}")


@app.get("/api/repairmen")
def api_repairmen(db=Depends(get_db)):
    return _json_resp({"success": True, "data": get_repairmen(db)})


# ============================================================
# API: 仪表盘数据
# ============================================================

@app.get("/api/dashboard/stats")
def api_dashboard_stats(
    request: Request,
    days: int = Query(30, description="统计最近 N 天"),
    db=Depends(get_db),
):
    user = _try_get_user(request, db)
    since = datetime.now() - timedelta(days=days)

    # Worker 数据隔离：获取该用户被指派的检测记录 ID 列表
    worker_record_ids = None
    if user and user.role == "repairman":
        worker_order_records = db.query(WorkOrder.detection_record_id).filter(
            WorkOrder.assigned_to == user.id,
            WorkOrder.detection_record_id.isnot(None),
        ).all()
        worker_record_ids = [r[0] for r in worker_order_records if r[0] is not None]

    # 每日缺陷数
    from sqlalchemy import func
    daily_query = db.query(
        func.date(DetectionRecord.created_at).label("date"),
        func.count(DetectionRecord.id).label("count"),
    ).filter(
        DetectionRecord.created_at >= since,
        DetectionRecord.defect_count > 0,
    )
    if worker_record_ids is not None:
        if worker_record_ids:
            daily_query = daily_query.filter(DetectionRecord.id.in_(worker_record_ids))
        else:
            daily_query = daily_query.filter(DetectionRecord.id == -1)  # 无数据
    daily = daily_query.group_by("date").order_by("date").all()
    trend = [{"date": str(d), "count": c} for d, c in daily]

    # 类别分布
    records_query = db.query(DetectionRecord).filter(
        DetectionRecord.created_at >= since,
    )
    if worker_record_ids is not None:
        if worker_record_ids:
            records_query = records_query.filter(DetectionRecord.id.in_(worker_record_ids))
        else:
            records_query = records_query.filter(DetectionRecord.id == -1)
    all_records = records_query.all()
    cat_count = {}
    for r in all_records:
        dets = r.yolo_detections or []
        for det in dets:
            nm = det.get("class_name", "unknown")
            cat_count[nm] = cat_count.get(nm, 0) + 1

    # 工单统计（Worker 仅统计自己的工单）
    if user and user.role == "repairman":
        order_stats = get_order_stats(db, user_id=user.id)
    else:
        order_stats = get_order_stats(db)

    # 总览
    total_query = db.query(DetectionRecord).filter(
        DetectionRecord.created_at >= since
    )
    if worker_record_ids is not None:
        if worker_record_ids:
            total_query = total_query.filter(DetectionRecord.id.in_(worker_record_ids))
        else:
            total_query = total_query.filter(DetectionRecord.id == -1)
    total_detections = total_query.count()

    return _json_resp({"success": True, "data": {
        "total_detections": total_detections,
        "trend": trend,
        "category_distribution": cat_count,
        "order_stats": order_stats,
        "period_days": days,
    }})


# ============================================================
# API: 地图数据
# ============================================================

@app.get("/api/map/records")
def api_map_records(request: Request, db=Depends(get_db)):
    user = _try_get_user(request, db)

    # Worker 数据隔离：仅返回自己工单对应的检测记录 GPS
    if user and user.role == "repairman":
        # 获取该 worker 的工单关联的 detection_record_id 列表
        order_records = db.query(WorkOrder.detection_record_id).filter(
            WorkOrder.assigned_to == user.id,
            WorkOrder.detection_record_id.isnot(None),
            WorkOrder.status.in_(["processing", "pending_review", "closed"]),
        ).all()
        record_ids = [r[0] for r in order_records if r[0] is not None]
        if record_ids:
            records = db.query(DetectionRecord).filter(
                DetectionRecord.id.in_(record_ids),
                DetectionRecord.gps_lat.isnot(None),
                DetectionRecord.gps_lng.isnot(None),
            ).order_by(DetectionRecord.created_at.desc()).limit(500).all()
        else:
            records = []
    else:
        records = db.query(DetectionRecord).filter(
            DetectionRecord.gps_lat.isnot(None),
            DetectionRecord.gps_lng.isnot(None),
        ).order_by(DetectionRecord.created_at.desc()).limit(500).all()

    markers = []
    for r in records:
        ai = r.ai_analysis or {}
        markers.append({
            "id": r.id,
            "lat": r.gps_lat or r.gps_latitude,
            "lng": r.gps_lng or r.gps_longitude,
            "severity": ai.get("severity", "一般"),
            "class_name": (
                (r.yolo_detections or [{}])[0].get("class_name", "未知")
                if r.yolo_detections else "未知"
            ),
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "annotated_image_path": r.annotated_image_path or "",
        })

    # ---- 未闭环工单标记（Phase 29：显示未完成闭环工单的具体位置）----
    # 工单状态: pending/processing/pending_review/rejected 均为未闭环；closed 为已闭环
    order_q = db.query(WorkOrder).filter(
        WorkOrder.gps_lat.isnot(None),
        WorkOrder.gps_lng.isnot(None),
        WorkOrder.status != "closed",
    )
    if user and user.role == "repairman":
        order_q = order_q.filter(WorkOrder.assigned_to == user.id)
    orders = order_q.order_by(WorkOrder.created_at.desc()).limit(500).all()

    order_status_text = {
        "pending": "待派发", "processing": "处理中",
        "pending_review": "待复检", "rejected": "已驳回",
    }
    order_markers = []
    for o in orders:
        order_markers.append({
            "id": o.id,
            "type": "order",
            "lat": o.gps_lat,
            "lng": o.gps_lng,
            "title": o.title,
            "severity": o.severity,
            "status": o.status,
            "status_text": order_status_text.get(o.status, o.status),
            "assignee": o.assignee.full_name if o.assignee else None,
            "created_at": o.created_at.isoformat() if o.created_at else None,
            "annotated_image_path": o.annotated_image_path or "",
        })

    return _json_resp({"success": True, "data": {
        "records": markers,
        "orders": order_markers,
    }})


# ============================================================
# API: GPU 系统状态
# ============================================================

@app.get("/api/system/status")
def api_system_status():
    import torch
    gpu = get_gpu_info()
    try:
        detector = get_detector()
        model_ok, model_path = True, detector.model_path
    except:
        model_ok, model_path = False, None
    return _json_resp({"success": True, "data": {
        "gpu": gpu,
        "model_loaded": model_ok,
        "model_path": model_path,
        "dashscope_available": bool(os.getenv("DASHSCOPE_API_KEY")),
        "cuda_available": torch.cuda.is_available(),
    }})


# ============================================================
# 历史记录 & 导出 & 预览（保留 Phase 1）
# ============================================================

def _delete_image_record(db, record):
    """删除图片检测记录：清理标注图与缩略图，并解除工单关联（方案B）。"""
    deleted_image = False
    # 删除标注图片（物理文件，仅 annotated，保留原始图）
    if record.annotated_image_path:
        try:
            img_path = Path(record.annotated_image_path)
            if img_path.exists() and img_path.is_file():
                img_path.unlink()
                deleted_image = True
                logger.info(f"删除标注图片: {img_path}")
            # 顺带删除移动端缩略图（Phase 8：与标注图同名的 _thumb.jpg，防止残留）
            thumb_path = img_path.with_name(img_path.stem + "_thumb.jpg")
            if thumb_path.exists() and thumb_path.is_file():
                thumb_path.unlink()
                logger.info(f"删除缩略图: {thumb_path}")
        except Exception as e:
            logger.warning(f"删除标注图片失败: {e}")

    # 解除工单关联（方案B：仅置 NULL，保留工单记录）
    db.query(WorkOrder).filter(
        WorkOrder.detection_record_id == record.id
    ).update({"detection_record_id": None})

    db.delete(record)
    db.commit()
    return deleted_image


@app.get("/api/history")
def api_history(
    page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
    source_type: Optional[str] = Query(None), alert_only: bool = Query(False),
    db=Depends(get_db),
):
    """混合查询：detection_records（图片）+ t_video_detections（视频）。"""
    from database.models import VideoDetectionRecord as VDR

    # 分别查询两张表
    all_records = []

    # 图片记录
    if not source_type or source_type == "image":
        img_query = db.query(DetectionRecord)
        if alert_only:
            img_query = img_query.filter(DetectionRecord.alert_triggered == True)
        img_records = img_query.order_by(DetectionRecord.created_at.desc()).all()
        for r in img_records:
            d = r.to_dict()
            d["record_type"] = "image"
            all_records.append(d)

    # 视频记录（Phase 7 新增）
    if not source_type or source_type == "video":
        vid_query = db.query(VDR).filter(VDR.status == "completed")
        if alert_only:
            vid_query = vid_query.filter(VDR.has_alert == True)
        vid_records = vid_query.order_by(VDR.created_at.desc()).all()
        for r in vid_records:
            d = r.to_dict()
            all_records.append(d)

    # 按 created_at 降序排序
    all_records.sort(key=lambda x: x.get("created_at") or "", reverse=True)

    # 分页
    total = len(all_records)
    total_pages = max(1, (total + page_size - 1) // page_size)
    start = (page - 1) * page_size
    end = start + page_size
    records = all_records[start:end]

    return _json_resp({"success": True, "data": {
        "total": total, "page": page, "page_size": page_size,
        "total_pages": total_pages,
        "records": records,
    }})


@app.get("/api/history/{record_id}")
def api_history_detail(record_id: int, db=Depends(get_db)):
    r = db.query(DetectionRecord).filter(DetectionRecord.id == record_id).first()
    if not r: raise HTTPException(404, "记录不存在")
    return _json_resp({"success": True, "data": r.to_dict()})


@app.delete("/api/history/{record_id}")
def api_delete_record(record_id: int, request: Request, db=Depends(get_db)):
    """Admin 删除图片检测记录：删除标注图片物理文件 + 解除工单关联（方案B）。"""
    user = require_login(request, db)
    if user.role not in ("inspector", "admin"):
        raise HTTPException(403, "仅运维人员可删除记录")

    record = db.query(DetectionRecord).filter(DetectionRecord.id == record_id).first()
    if not record:
        raise HTTPException(404, "记录不存在")

    deleted_image = _delete_image_record(db, record)
    logger.info(f"检测记录 #{record_id} 已删除 by user {user.id}（标注图片已删: {deleted_image}）")

    return _json_resp({"success": True, "data": {
        "id": record_id,
        "annotated_image_deleted": deleted_image,
    }, "message": "记录已删除"})


@app.post("/api/history/batch-delete")
def api_batch_delete_history(request: Request, payload: dict = Body(...), db=Depends(get_db)):
    """批量删除历史检测记录（图片/视频混合），支持部分失败。"""
    user = require_login(request, db)
    if user.role not in ("inspector", "admin"):
        raise HTTPException(403, "仅运维人员可删除记录")

    items = payload.get("items") or []
    if not isinstance(items, list) or not items:
        raise HTTPException(400, "请至少选择一条记录")

    from database.models import VideoDetectionRecord as VDR
    deleted = 0
    deleted_images = 0
    failed = []

    for item in items:
        record_id = item.get("id")
        record_type = item.get("record_type") or "image"
        try:
            if record_type == "video":
                record = db.query(VDR).filter(VDR.id == record_id).first()
                if not record:
                    failed.append({"id": record_id, "record_type": record_type, "reason": "记录不存在"})
                    continue
                deleted_images += _delete_video_record(db, record)
            elif record_type == "image":
                record = db.query(DetectionRecord).filter(DetectionRecord.id == record_id).first()
                if not record:
                    failed.append({"id": record_id, "record_type": record_type, "reason": "记录不存在"})
                    continue
                _delete_image_record(db, record)
            else:
                failed.append({"id": record_id, "record_type": record_type, "reason": "不支持的记录类型"})
                continue
            deleted += 1
        except Exception as e:
            db.rollback()
            failed.append({"id": record_id, "record_type": record_type, "reason": str(e)})
            logger.error(f"批量删除历史记录失败: id={record_id} type={record_type}, {e}")

    logger.info(f"批量删除历史记录: 成功 {deleted} 条，失败 {len(failed)} 条 by user {user.id}")
    return _json_resp({"success": True, "data": {
        "deleted": deleted,
        "deleted_images": deleted_images,
        "failed": failed,
    }, "message": f"已删除 {deleted} 条记录"})


@app.post("/api/history/{record_id}/gps")
def api_update_record_gps(
    record_id: int,
    request: Request,
    gps_lat: float = Form(...),
    gps_lng: float = Form(...),
    db=Depends(get_db),
):
    """手动更新检测记录的 GPS 坐标（仅运维/管理员）。"""
    user = require_login(request, db)
    if user.role not in ("inspector", "admin"):
        raise HTTPException(403, "仅运维人员可更新 GPS 坐标")
    r = db.query(DetectionRecord).filter(DetectionRecord.id == record_id).first()
    if not r:
        raise HTTPException(404, "记录不存在")
    r.gps_lat = gps_lat
    r.gps_lng = gps_lng
    r.gps_source = "manual"
    db.commit()
    logger.info(f"GPS 手动更新: 记录 #{record_id} → ({gps_lat}, {gps_lng})")
    return _json_resp({"success": True, "data": r.to_dict()})


@app.get("/api/export/{record_id}")
def api_export_report(record_id: int, db=Depends(get_db)):
    from services.report_service import export_word_report
    r = db.query(DetectionRecord).filter(DetectionRecord.id == record_id).first()
    if not r: raise HTTPException(404, "记录不存在")
    try:
        path = export_word_report(r, REPORTS_DIR)
        return FileResponse(path=path, filename=Path(path).name,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    except Exception as e:
        raise HTTPException(500, str(e))


@app.get("/api/preview/{filename:path}")
async def api_preview(filename: str):
    for d in [UPLOADS_DIR, STATIC_DIR / "outputs", UPLOADS_DIR / "repair"]:
        fp = d / filename
        if fp.exists(): return FileResponse(str(fp))
    raise HTTPException(404, f"文件不存在: {filename}")


@app.get("/api/status")
async def api_status():
    try:
        d = get_detector()
        m_ok, mp = True, d.model_path
    except:
        m_ok, mp = False, None
    return _json_resp({"success": True, "data": {
        "model_loaded": m_ok, "model_path": mp,
        "dashscope_available": bool(os.getenv("DASHSCOPE_API_KEY")),
        "cuda_available": True, "gpu_name": "GPU",
        "server_time": datetime.now().isoformat(),
    }})


# ============================================================
# 启动入口
# ============================================================

if __name__ == "__main__":
    import uvicorn
    print(); print("=" * 60)
    print("  电力输电线路智能检测分析预警系统 v3.0")
    print(f"  http://localhost:{PORT}  |  /docs  |  /dashboard  |  /map  |  /orders")
    print("=" * 60); print()
    uvicorn.run("app:app", host=HOST, port=PORT, reload=False, log_level="info")
