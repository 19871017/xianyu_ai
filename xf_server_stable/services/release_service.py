"""版本发布与公告的业务逻辑（下载站 + 客户端更新检测）。"""
import os
import re
import logging
from datetime import datetime
from sqlalchemy.orm import Session

from models.app_version import AppVersion
from models.announcement import Announcement
from config import BASE_DIR

logger = logging.getLogger(__name__)

# 安装包存放目录。优先级：
#   XF_DOWNLOADS_DIR（部署时指向独立数据盘）> XF_BASE_OVERRIDE/downloads（测试隔离）> BASE_DIR/downloads。
_base = os.getenv("XF_DOWNLOADS_DIR") or (
    os.path.join(os.getenv("XF_BASE_OVERRIDE"), "downloads")
    if os.getenv("XF_BASE_OVERRIDE") else os.path.join(BASE_DIR, "downloads")
)
DOWNLOADS_DIR = os.path.abspath(_base)
os.makedirs(DOWNLOADS_DIR, exist_ok=True)

ALLOWED_PLATFORMS = ("mac", "win")
# 允许上传的安装包扩展名，按平台区分，避免上传任意可执行/脚本文件。
ALLOWED_EXTS = {
    "mac": (".dmg", ".pkg", ".zip"),
    "win": (".exe", ".msi", ".zip"),
}


def _iso(dt) -> str:
    return dt.isoformat() if dt else None


def _version_tuple(ver: str):
    """把 '3.2.0' 解析成可比较的整数元组；非数字段记 0。"""
    parts = re.split(r"[._-]", str(ver or "").strip())
    out = []
    for p in parts:
        m = re.match(r"\d+", p)
        out.append(int(m.group()) if m else 0)
    return tuple(out) or (0,)


def version_to_dict(v: AppVersion) -> dict:
    return {
        "id": v.id,
        "platform": v.platform,
        "version": v.version,
        "download_url": v.download_url or "",
        "file_name": v.file_name or "",
        "file_size": v.file_size or 0,
        "release_notes": v.release_notes or "",
        "is_latest": bool(v.is_latest),
        "force_update": bool(v.force_update),
        "created_at": _iso(v.created_at),
    }


def announcement_to_dict(a: Announcement) -> dict:
    return {
        "id": a.id,
        "title": a.title,
        "content": a.content or "",
        "is_published": bool(a.is_published),
        "pinned": a.pinned or 0,
        "created_at": _iso(a.created_at),
    }


def _normalize_platform(platform: str) -> str:
    p = (platform or "").strip().lower()
    if p in ("macos", "darwin", "osx"):
        p = "mac"
    if p in ("windows", "win32", "win64"):
        p = "win"
    if p not in ALLOWED_PLATFORMS:
        raise ValueError(f"不支持的平台: {platform}（仅 mac / win）")
    return p


def _mark_latest(db: Session, platform: str, keep_id: int) -> None:
    """把该平台除 keep_id 外的版本 is_latest 全部置 False。"""
    db.query(AppVersion).filter(
        AppVersion.platform == platform,
        AppVersion.id != keep_id,
    ).update({AppVersion.is_latest: False})


def create_version(db: Session, platform: str, version: str, download_url: str = "",
                   release_notes: str = "", force_update: bool = False,
                   file_name: str = "", file_size: int = 0) -> AppVersion:
    platform = _normalize_platform(platform)
    version = (version or "").strip()
    if not version:
        raise ValueError("版本号不能为空")
    if not download_url and not file_name:
        raise ValueError("需提供下载链接或上传安装包")
    v = AppVersion(
        platform=platform,
        version=version,
        download_url=download_url or "",
        file_name=file_name or "",
        file_size=file_size or 0,
        release_notes=release_notes or "",
        is_latest=True,
        force_update=bool(force_update),
        created_at=datetime.utcnow(),
    )
    db.add(v)
    db.flush()  # 拿到 v.id
    _mark_latest(db, platform, v.id)
    db.commit()
    db.refresh(v)
    logger.info(f"新增版本: {platform} {version} (id={v.id})")
    return v


def _safe_filename(platform: str, version: str, original: str) -> str:
    """构造安全文件名：平台_版本_原名，去除路径分隔与异常字符。"""
    ext = os.path.splitext(original or "")[1].lower()
    base = f"{platform}_{version}".replace(" ", "")
    base = re.sub(r"[^A-Za-z0-9._-]", "", base)
    ext = re.sub(r"[^A-Za-z0-9.]", "", ext)
    return f"{base}{ext}"


def save_uploaded_package(platform: str, version: str, original_name: str, data: bytes) -> dict:
    """保存上传的安装包到 downloads 目录，返回 {file_name, file_size, download_url}。"""
    platform = _normalize_platform(platform)
    ext = os.path.splitext(original_name or "")[1].lower()
    if ext not in ALLOWED_EXTS.get(platform, ()):
        raise ValueError(f"{platform} 平台不允许的文件类型: {ext or '无扩展名'}")
    fname = _safe_filename(platform, version, original_name)
    path = os.path.join(DOWNLOADS_DIR, fname)
    # 防路径穿越：最终路径必须仍在 downloads 目录内。
    if os.path.commonpath([os.path.abspath(path), DOWNLOADS_DIR]) != DOWNLOADS_DIR:
        raise ValueError("非法文件名")
    with open(path, "wb") as f:
        f.write(data)
    return {
        "file_name": fname,
        "file_size": len(data),
        "download_url": f"/downloads/{fname}",
    }


def list_versions(db: Session, platform: str = None, only_latest: bool = False):
    q = db.query(AppVersion)
    if platform:
        q = q.filter(AppVersion.platform == _normalize_platform(platform))
    if only_latest:
        q = q.filter(AppVersion.is_latest == True)  # noqa: E712
    rows = q.order_by(AppVersion.platform.asc(), AppVersion.id.desc()).all()
    return [version_to_dict(v) for v in rows]


def get_latest(db: Session, platform: str) -> dict:
    platform = _normalize_platform(platform)
    v = db.query(AppVersion).filter(
        AppVersion.platform == platform,
        AppVersion.is_latest == True,  # noqa: E712
    ).order_by(AppVersion.id.desc()).first()
    return version_to_dict(v) if v else None


def delete_version(db: Session, version_id: int) -> bool:
    v = db.query(AppVersion).filter(AppVersion.id == version_id).first()
    if not v:
        return False
    platform = v.platform
    was_latest = v.is_latest
    # 删除关联的本地安装包（外部直链不处理）。
    if v.file_name:
        fp = os.path.join(DOWNLOADS_DIR, v.file_name)
        try:
            if os.path.isfile(fp) and os.path.commonpath([os.path.abspath(fp), DOWNLOADS_DIR]) == DOWNLOADS_DIR:
                os.remove(fp)
        except Exception as e:
            logger.warning(f"删除安装包文件失败: {fp} err={e}")
    db.delete(v)
    db.flush()
    # 若删的是最新版，把同平台最近一条重新标记为最新。
    if was_latest:
        nxt = db.query(AppVersion).filter(AppVersion.platform == platform).order_by(AppVersion.id.desc()).first()
        if nxt:
            nxt.is_latest = True
    db.commit()
    return True


def create_announcement(db: Session, title: str, content: str = "",
                        is_published: bool = True, pinned: int = 0) -> Announcement:
    title = (title or "").strip()
    if not title:
        raise ValueError("公告标题不能为空")
    a = Announcement(
        title=title,
        content=content or "",
        is_published=bool(is_published),
        pinned=int(pinned or 0),
        created_at=datetime.utcnow(),
    )
    db.add(a)
    db.commit()
    db.refresh(a)
    return a


def list_announcements(db: Session, only_published: bool = False):
    q = db.query(Announcement)
    if only_published:
        q = q.filter(Announcement.is_published == True)  # noqa: E712
    rows = q.order_by(Announcement.pinned.desc(), Announcement.id.desc()).all()
    return [announcement_to_dict(a) for a in rows]


def update_announcement(db: Session, ann_id: int, **fields) -> dict:
    a = db.query(Announcement).filter(Announcement.id == ann_id).first()
    if not a:
        return None
    if "title" in fields and fields["title"] is not None:
        a.title = fields["title"].strip() or a.title
    if "content" in fields and fields["content"] is not None:
        a.content = fields["content"]
    if "is_published" in fields and fields["is_published"] is not None:
        a.is_published = bool(fields["is_published"])
    if "pinned" in fields and fields["pinned"] is not None:
        a.pinned = int(fields["pinned"])
    db.commit()
    db.refresh(a)
    return announcement_to_dict(a)


def delete_announcement(db: Session, ann_id: int) -> bool:
    a = db.query(Announcement).filter(Announcement.id == ann_id).first()
    if not a:
        return False
    db.delete(a)
    db.commit()
    return True
