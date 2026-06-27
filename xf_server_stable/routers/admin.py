from fastapi import APIRouter, Depends, HTTPException, Header, Request
from sqlalchemy.orm import Session
from models.database import get_db
from models.user import User
from models.device import Device
from models.license_model import License
from models.audit_log import AuditLog
from utils.security import decode_token, hash_password
from services.auth_service import ensure_admin_exists
from services.audit_service import log_action
from schemas.license_schema import LicenseIssue, LicenseExtend, LicenseInfo
from services.license_service import (
    issue_license, extend_license, revoke_license,
    set_device_force_offline, unbind_device, device_is_online,
)
from config import OFFLINE_THRESHOLD_SECONDS
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin", tags=["管理后台"])


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else ""


def get_current_admin(authorization: str = Header(None), db: Session = Depends(get_db)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="未提供认证信息")
    try:
        payload = decode_token(authorization.split(" ")[1])
    except Exception as e:
        logger.error(f"Token验证失败: {e}")
        raise HTTPException(status_code=401, detail="Token无效或已过期")
    is_admin = payload.get("is_admin", False) or payload.get("role") == "admin"
    if not is_admin:
        raise HTTPException(status_code=403, detail="需要管理员权限")
    # 实时校验管理员账号仍然有效（被禁用则立即失效）
    user = db.query(User).filter(User.username == payload.get("sub")).first()
    if not user or not user.is_active or user.role != "admin":
        raise HTTPException(status_code=403, detail="管理员账号无效或已被禁用")
    return payload


@router.get("/dashboard")
def dashboard(db: Session = Depends(get_db), _=Depends(get_current_admin)):
    ensure_admin_exists(db)
    now = datetime.utcnow()
    online_cutoff = now - timedelta(seconds=OFFLINE_THRESHOLD_SECONDS)
    total_users = db.query(User).count()
    total_licenses = db.query(License).count()
    active_licenses = db.query(License).filter(License.is_active == True).count()  # noqa: E712
    expired_licenses = db.query(License).filter(License.expires_at < now).count()
    total_devices = db.query(Device).count()
    active_devices = db.query(Device).filter(Device.is_active == True).count()  # noqa: E712
    online_devices = db.query(Device).filter(
        Device.is_active == True,  # noqa: E712
        Device.force_offline == False,  # noqa: E712
        Device.last_heartbeat != None,  # noqa: E711
        Device.last_heartbeat >= online_cutoff,
    ).count()
    return {
        "total_users": total_users,
        "total_licenses": total_licenses,
        "active_licenses": active_licenses,
        "expired_licenses": expired_licenses,
        "total_devices": total_devices,
        "active_devices": active_devices,
        "online_devices": online_devices,
    }


@router.get("/users")
def list_users(db: Session = Depends(get_db), _=Depends(get_current_admin)):
    users = db.query(User).all()
    return [
        {
            "id": u.id,
            "username": u.username,
            "role": u.role,
            "is_active": u.is_active,
            "created_at": u.created_at.isoformat() if u.created_at else None,
        }
        for u in users
    ]


@router.post("/user/{user_id}/toggle")
def toggle_user(user_id: int, request: Request, db: Session = Depends(get_db),
                admin=Depends(get_current_admin)):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    if user.role == "admin":
        raise HTTPException(status_code=400, detail="不能禁用管理员账号")
    user.is_active = not user.is_active
    db.commit()
    log_action(db, "disable_user" if not user.is_active else "enable_user",
               actor=admin.get("sub", "admin"), target=user.username, ip_address=_client_ip(request))
    return {"message": "已禁用" if not user.is_active else "已启用", "is_active": user.is_active}


@router.get("/devices")
def list_devices(db: Session = Depends(get_db), _=Depends(get_current_admin)):
    devices = db.query(Device).all()
    return [
        {
            "id": d.id,
            "license_id": d.license_id,
            "machine_id": d.machine_id,
            "device_name": d.device_name,
            "ip_address": d.ip_address,
            "is_active": d.is_active,
            "force_offline": d.force_offline,
            "online": device_is_online(d),
            "activated_at": d.activated_at.isoformat() if d.activated_at else None,
            "last_verified": d.last_verified.isoformat() if d.last_verified else None,
            "last_heartbeat": d.last_heartbeat.isoformat() if d.last_heartbeat else None,
        }
        for d in devices
    ]


@router.post("/device/{device_id}/force_offline")
def force_offline(device_id: int, request: Request, db: Session = Depends(get_db),
                  admin=Depends(get_current_admin)):
    return set_device_force_offline(db, device_id, True, actor=admin.get("sub", "admin"), ip=_client_ip(request))


@router.post("/device/{device_id}/allow_online")
def allow_online(device_id: int, request: Request, db: Session = Depends(get_db),
                 admin=Depends(get_current_admin)):
    return set_device_force_offline(db, device_id, False, actor=admin.get("sub", "admin"), ip=_client_ip(request))


@router.post("/device/{device_id}/unbind")
def unbind(device_id: int, request: Request, db: Session = Depends(get_db),
           admin=Depends(get_current_admin)):
    return unbind_device(db, device_id, actor=admin.get("sub", "admin"), ip=_client_ip(request))


@router.get("/licenses")
def list_licenses(db: Session = Depends(get_db), _=Depends(get_current_admin)):
    licenses = db.query(License).all()
    return [
        {
            "id": l.id,
            "license_key": l.license_key,
            "user_id": l.user_id,
            "machine_id": l.machine_id or "",
            "max_devices": getattr(l, "max_devices", 0) or 0,
            "note": getattr(l, "note", "") or "",
            "expires_at": l.expires_at.isoformat() if l.expires_at else None,
            "is_active": l.is_active,
            "days": l.days,
            "issued_at": l.issued_at.isoformat() if l.issued_at else None,
            "activated_at": l.activated_at.isoformat() if l.activated_at else None,
        }
        for l in licenses
    ]


@router.post("/license/issue", response_model=LicenseInfo)
def admin_issue_license(data: LicenseIssue, request: Request, db: Session = Depends(get_db),
                        admin=Depends(get_current_admin)):
    return issue_license(db, data, actor=admin.get("sub", "admin"), ip=_client_ip(request))


@router.put("/license/extend", response_model=LicenseInfo)
def admin_extend_license(data: LicenseExtend, request: Request, db: Session = Depends(get_db),
                         admin=Depends(get_current_admin)):
    return extend_license(db, data, actor=admin.get("sub", "admin"), ip=_client_ip(request))


@router.post("/license/{license_key}/revoke")
def admin_revoke_license(license_key: str, request: Request, db: Session = Depends(get_db),
                         admin=Depends(get_current_admin)):
    return revoke_license(db, license_key, actor=admin.get("sub", "admin"), ip=_client_ip(request))


@router.get("/audit")
def list_audit(limit: int = 200, db: Session = Depends(get_db), _=Depends(get_current_admin)):
    limit = max(1, min(limit, 1000))
    rows = db.query(AuditLog).order_by(AuditLog.id.desc()).limit(limit).all()
    return [
        {
            "id": r.id,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "actor": r.actor,
            "action": r.action,
            "target": r.target,
            "ip_address": r.ip_address,
            "detail": r.detail,
        }
        for r in rows
    ]
