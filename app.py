#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
电力输电线路智能检测分析预警系统 v3.0 - Phase 2 全功能版。
新增: 用户认证、工单管理、GIS 地图、兜底检测、GPU 监控、数据仪表盘。
"""

import os, sys, uuid, base64, logging, tempfile, threading, json
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, List

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from fastapi import (
    FastAPI, File, UploadFile, Form, Query, HTTPException, Depends, Request, Body,
)
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, RedirectResponse
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
    resp = JSONResponse({"success": True, "data": user.to_dict()})
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
        return JSONResponse({"success": True, "data": user.to_dict()})
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/auth/logout")
def api_logout():
    resp = JSONResponse({"success": True})
    resp.delete_cookie(SESSION_COOKIE_NAME)
    return resp


@app.get("/api/auth/me")
def api_me(request: Request, db=Depends(get_db)):
    user = get_current_user(request, db)
    if not user:
        return JSONResponse({"success": True, "data": None})
    return JSONResponse({"success": True, "data": user.to_dict()})


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
    user: User = Depends(_require_inspector_or_admin),
):
    file_ext = Path(file.filename).suffix.lower()
    if file_ext not in ALLOWED_IMAGE_EXTS:
        raise HTTPException(400, f"不支持的格式: {file_ext}")
    tmp_path = None
    try:
        tmp_path = _save_upload_temp(file)
        result = process_single_image(
            image_path=tmp_path, save_annotated=True, call_ai=call_ai,
        )

        # 兜底检测（异步，不阻塞返回）
        has_abnormal = False
        abnormal_desc = ""
        if call_fallback and os.getenv("DASHSCOPE_API_KEY"):
            try:
                fb = FallbackDetector()
                fb_result, _ = fb.check(tmp_path)
                has_abnormal = fb_result.get("abnormal", False)
                abnormal_desc = fb_result.get("description", "")
                if has_abnormal:
                    # 更新数据库记录
                    db2 = get_db_session()
                    rec = db2.query(DetectionRecord).filter(
                        DetectionRecord.id == result.get("record_id")
                    ).first()
                    if rec:
                        rec.has_abnormal = has_abnormal
                        rec.abnormal_desc = abnormal_desc
                        db2.commit()
                    db2.close()
            except Exception as e:
                logger.warning(f"兜底检测异常: {e}")

        result["has_abnormal"] = has_abnormal
        result["abnormal_desc"] = abnormal_desc
        # 语音播报标识
        ai = result.get("ai_analysis") or {}
        sev = ai.get("severity", "一般")
        result["speech_alert"] = sev in ("严重", "紧急")

        return JSONResponse({"success": True, "data": result, "message": "检测完成"})
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
    return JSONResponse({
        "success": len(errors) == 0, "total": len(files),
        "processed": len(results), "errors": errors, "data": results,
    })


# ============================================================
# API: 视频分析（保留 Phase 1）
# ============================================================

ALLOWED_VIDEO_EXTS = {".mp4", ".avi", ".mov", ".mkv", ".wmv", ".flv", ".webm"}
_video_progress: dict = {}
_video_progress_lock = threading.Lock()


def _run_video_processing(task_id: str, video_path: str, call_ai: bool, fallback_interval: int = 5, user_id: int = None):
    db = get_db_session()
    try:
        with _video_progress_lock:
            _video_progress[task_id] = {"status": "processing", "processed": 0, "total": 0}
        result = process_video_file(
            video_path=video_path, db=db, task_id=task_id, call_ai=call_ai, user_id=user_id,
        )
        with _video_progress_lock:
            _video_progress[task_id] = {
                "status": "completed", "processed": result["total_frames"],
                "total": result["total_frames"], "output_video": result["output_video"],
                "ai_reports": result["ai_reports"], "stats": result["stats"],
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
                     args=(task_id, tmp, call_ai, fallback_interval, user.id), daemon=True).start()
    return JSONResponse({"success": True, "data": {"task_id": task_id, "status": "pending"}})


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
        return JSONResponse({"success": True, "data": data})
    finally: db.close()


# ---- 视频检测记录 API（Phase 7）----

@app.get("/api/video/records/{record_id}")
def api_video_record_detail(record_id: int, request: Request, db=Depends(get_db)):
    """获取视频检测记录详情（含完整 frames_data）。"""
    require_login(request, db)
    from database.models import VideoDetectionRecord as VDR
    r = db.query(VDR).filter(VDR.id == record_id).first()
    if not r:
        raise HTTPException(404, "视频记录不存在")
    return JSONResponse({"success": True, "data": r.to_dict()})


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

    # 统计帧图片数量（用于确认提示）
    frame_images = r.frame_images or []
    keyframe_images = r.keyframe_images or []
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
        vt = db.query(VideoTask).filter(VideoTask.task_id == r.task_id).first()
        if vt:
            db.delete(vt)
    except Exception as e:
        logger.warning(f"删除 VideoTask 失败: {e}")

    db.delete(r)
    db.commit()
    logger.info(f"视频记录 #{record_id} 已删除（含 {deleted_files} 张帧图片）by user {user.id}")

    return JSONResponse({"success": True, "data": {
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

    return JSONResponse({"success": True, "data": {
        **frame,
        "image_url": image_url,
        "total_frames": r.total_frames,
        "output_video_path": r.output_video_path,
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

    return JSONResponse({"success": True, "data": {
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
        return JSONResponse({"success": True, "data": result})
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
    db=Depends(get_db),
):
    user = require_login(request, db)
    if user.role not in ("inspector", "admin"):
        raise HTTPException(403, "仅运维人员可创建工单")
    try:
        order = create_work_order(
            db, title=title, description=description, severity=severity,
            created_by=user.id, assigned_to=assigned_to,
            original_image_path=original_image_path,
            annotated_image_path=annotated_image_path,
            gps_lat=gps_lat, gps_lng=gps_lng,
            detection_record_id=detection_record_id,
        )
        return JSONResponse({"success": True, "data": order.to_dict()})
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
    return JSONResponse({"success": True, "data": result})


@app.get("/api/orders/{order_id}")
def api_order_detail(order_id: int, request: Request, db=Depends(get_db)):
    require_login(request, db)
    try:
        order = get_order_detail(db, order_id)
        return JSONResponse({"success": True, "data": order.to_dict()})
    except ValueError as e:
        raise HTTPException(404, str(e))


@app.post("/api/orders/{order_id}/accept")
def api_accept_order(order_id: int, request: Request, db=Depends(get_db)):
    user = require_login(request, db)
    try:
        order = accept_order(db, order_id, user.id)
        return JSONResponse({"success": True, "data": order.to_dict()})
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
        return JSONResponse({"success": True, "data": order.to_dict()})
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
        return JSONResponse({"success": True, "data": order.to_dict()})
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
        return JSONResponse({"success": True, "data": order.to_dict()})
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
        return JSONResponse({"success": True, "data": order.to_dict()})
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
        return JSONResponse({"success": True, "data": order.to_dict()})
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
        return JSONResponse({"success": True, "data": info,
                             "message": f"工单 #{order_id} 已删除"})
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/orders/{order_id}/logs")
def api_order_logs(order_id: int, request: Request, db=Depends(get_db)):
    """获取工单操作日志。"""
    require_login(request, db)
    logs = get_order_logs(db, order_id)
    return JSONResponse({"success": True, "data": logs})


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
    return JSONResponse({"success": True, "data": get_repairmen(db)})


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

    return JSONResponse({"success": True, "data": {
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
            "thumbnail": (
                r.annotated_image_path.replace("\\", "/").split("/static/uploads/")[-1]
                if r.annotated_image_path else ""
            ),
        })
    return JSONResponse({"success": True, "data": markers})


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
    return JSONResponse({"success": True, "data": {
        "gpu": gpu,
        "model_loaded": model_ok,
        "model_path": model_path,
        "dashscope_available": bool(os.getenv("DASHSCOPE_API_KEY")),
        "cuda_available": torch.cuda.is_available(),
    }})


# ============================================================
# 历史记录 & 导出 & 预览（保留 Phase 1）
# ============================================================

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

    return JSONResponse({"success": True, "data": {
        "total": total, "page": page, "page_size": page_size,
        "total_pages": total_pages,
        "records": records,
    }})


@app.get("/api/history/{record_id}")
def api_history_detail(record_id: int, db=Depends(get_db)):
    r = db.query(DetectionRecord).filter(DetectionRecord.id == record_id).first()
    if not r: raise HTTPException(404, "记录不存在")
    return JSONResponse({"success": True, "data": r.to_dict()})


@app.delete("/api/history/{record_id}")
def api_delete_record(record_id: int, request: Request, db=Depends(get_db)):
    """Admin 删除图片检测记录：删除标注图片物理文件 + 解除工单关联（方案B）。"""
    user = require_login(request, db)
    if user.role not in ("inspector", "admin"):
        raise HTTPException(403, "仅运维人员可删除记录")

    record = db.query(DetectionRecord).filter(DetectionRecord.id == record_id).first()
    if not record:
        raise HTTPException(404, "记录不存在")

    deleted_image = False
    # 删除标注图片（物理文件，仅 annotated，保留原始图）
    if record.annotated_image_path:
        try:
            img_path = Path(record.annotated_image_path)
            if img_path.exists() and img_path.is_file():
                img_path.unlink()
                deleted_image = True
                logger.info(f"删除标注图片: {img_path}")
        except Exception as e:
            logger.warning(f"删除标注图片失败: {e}")

    # 解除工单关联（方案B：仅置 NULL，保留工单记录）
    db.query(WorkOrder).filter(
        WorkOrder.detection_record_id == record_id
    ).update({"detection_record_id": None})

    # 删除记录
    db.delete(record)
    db.commit()
    logger.info(f"检测记录 #{record_id} 已删除 by user {user.id}（标注图片已删: {deleted_image}）")

    return JSONResponse({"success": True, "data": {
        "id": record_id,
        "annotated_image_deleted": deleted_image,
    }, "message": "记录已删除"})


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
    return JSONResponse({"success": True, "data": r.to_dict()})


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
    return JSONResponse({"success": True, "data": {
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
