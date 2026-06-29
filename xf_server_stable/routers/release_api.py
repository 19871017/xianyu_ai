"""版本发布 / 公告 / 下载站 接口。

- 公开端点（无需鉴权）：供前台下载站与客户端更新检测使用。
- 管理端点（复用管理员 token）：上传安装包、发布版本、管理公告。
- 下载站页面：GET /  返回下载站 HTML（公告 + Mac/Win 下载）。
"""
import os
import logging
from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File, Form
from fastapi.responses import FileResponse, HTMLResponse
from sqlalchemy.orm import Session

from models.database import get_db
from routers.admin import get_current_admin, _client_ip
from services.audit_service import log_action
from services import release_service as rs
from schemas.release_schema import VersionCreate, AnnouncementCreate

logger = logging.getLogger(__name__)

# 单个安装包上传大小上限（字节），默认 500MB，防止超大文件打满磁盘。
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", str(500 * 1024 * 1024)))

SITE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "site_frontend")

router = APIRouter(tags=["下载站/版本"])


# ──────────────────────── 公开端点 ────────────────────────
@router.get("/api/public/latest")
def public_latest(platform: str, db: Session = Depends(get_db)):
    """客户端更新检测：返回某平台最新版本信息（无最新版时 latest 为 null）。"""
    try:
        latest = rs.get_latest(db, platform)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"platform": platform, "latest": latest}


@router.get("/api/public/versions")
def public_versions(db: Session = Depends(get_db)):
    """前台下载站：两个平台各自的最新版本。"""
    return {
        "mac": rs.get_latest(db, "mac"),
        "win": rs.get_latest(db, "win"),
    }


@router.get("/api/public/announcements")
def public_announcements(db: Session = Depends(get_db)):
    """前台下载站：已发布的公告列表。"""
    return rs.list_announcements(db, only_published=True)


@router.get("/downloads/{file_name}")
def download_file(file_name: str, db: Session = Depends(get_db)):
    """提供本地安装包下载（带文件名安全校验，防路径穿越）。"""
    base = rs.DOWNLOADS_DIR
    path = os.path.abspath(os.path.join(base, file_name))
    if os.path.commonpath([path, base]) != base or not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="文件不存在")
    return FileResponse(path, filename=file_name, media_type="application/octet-stream")


# ──────────────────────── 管理端点 ────────────────────────
@router.get("/api/admin/versions")
def admin_list_versions(db: Session = Depends(get_db), _=Depends(get_current_admin)):
    return rs.list_versions(db)


@router.post("/api/admin/version")
def admin_create_version(data: VersionCreate, request: Request,
                         db: Session = Depends(get_db), admin=Depends(get_current_admin)):
    """以外部直链方式登记一个版本。"""
    try:
        v = rs.create_version(
            db, platform=data.platform, version=data.version,
            download_url=data.download_url, release_notes=data.release_notes,
            force_update=data.force_update,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    log_action(db, "create_version", actor=admin.get("sub", "admin"),
               target=f"{v.platform} {v.version}", ip_address=_client_ip(request))
    return rs.version_to_dict(v)


@router.post("/api/admin/version/upload")
async def admin_upload_version(request: Request,
                               platform: str = Form(...),
                               version: str = Form(...),
                               release_notes: str = Form(""),
                               force_update: bool = Form(False),
                               file: UploadFile = File(...),
                               db: Session = Depends(get_db),
                               admin=Depends(get_current_admin)):
    """上传安装包并登记为某平台的最新版本。"""
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="上传文件为空")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail=f"文件超过上限 {MAX_UPLOAD_BYTES // (1024*1024)}MB")
    try:
        saved = rs.save_uploaded_package(platform, version, file.filename, data)
        v = rs.create_version(
            db, platform=platform, version=version,
            download_url=saved["download_url"], release_notes=release_notes,
            force_update=force_update, file_name=saved["file_name"],
            file_size=saved["file_size"],
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    log_action(db, "upload_version", actor=admin.get("sub", "admin"),
               target=f"{v.platform} {v.version}", ip_address=_client_ip(request),
               detail=f"{saved['file_name']} {saved['file_size']}B")
    return rs.version_to_dict(v)


@router.delete("/api/admin/version/{version_id}")
def admin_delete_version(version_id: int, request: Request,
                         db: Session = Depends(get_db), admin=Depends(get_current_admin)):
    ok = rs.delete_version(db, version_id)
    if not ok:
        raise HTTPException(status_code=404, detail="版本不存在")
    log_action(db, "delete_version", actor=admin.get("sub", "admin"),
               target=str(version_id), ip_address=_client_ip(request))
    return {"message": "已删除"}


@router.get("/api/admin/announcements")
def admin_list_announcements(db: Session = Depends(get_db), _=Depends(get_current_admin)):
    return rs.list_announcements(db)


@router.post("/api/admin/announcement")
def admin_create_announcement(data: AnnouncementCreate, request: Request,
                              db: Session = Depends(get_db), admin=Depends(get_current_admin)):
    try:
        a = rs.create_announcement(db, title=data.title, content=data.content,
                                   is_published=data.is_published, pinned=data.pinned)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    log_action(db, "create_announcement", actor=admin.get("sub", "admin"),
               target=a.title[:60], ip_address=_client_ip(request))
    return rs.announcement_to_dict(a)


@router.put("/api/admin/announcement/{ann_id}")
def admin_update_announcement(ann_id: int, data: AnnouncementCreate, request: Request,
                              db: Session = Depends(get_db), admin=Depends(get_current_admin)):
    res = rs.update_announcement(db, ann_id, title=data.title, content=data.content,
                                 is_published=data.is_published, pinned=data.pinned)
    if res is None:
        raise HTTPException(status_code=404, detail="公告不存在")
    log_action(db, "update_announcement", actor=admin.get("sub", "admin"),
               target=str(ann_id), ip_address=_client_ip(request))
    return res


@router.delete("/api/admin/announcement/{ann_id}")
def admin_delete_announcement(ann_id: int, request: Request,
                              db: Session = Depends(get_db), admin=Depends(get_current_admin)):
    ok = rs.delete_announcement(db, ann_id)
    if not ok:
        raise HTTPException(status_code=404, detail="公告不存在")
    log_action(db, "delete_announcement", actor=admin.get("sub", "admin"),
               target=str(ann_id), ip_address=_client_ip(request))
    return {"message": "已删除"}


# ──────────────────────── 下载站页面 ────────────────────────
@router.get("/", response_class=HTMLResponse)
def site_index():
    """前台下载站首页（公告 + Mac/Win 下载）。"""
    index = os.path.join(SITE_DIR, "index.html")
    if os.path.isfile(index):
        with open(index, "r", encoding="utf-8") as f:
            return HTMLResponse(f.read())
    return HTMLResponse("<h1>闲鱼AI助手</h1><p>下载站页面未部署</p>")
