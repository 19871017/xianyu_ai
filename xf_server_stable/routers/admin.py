from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.orm import Session
from models.database import get_db
from models.user import User
from models.device import Device
from models.license_model import License
from utils.security import decode_token
from services.auth_service import ensure_admin_exists
from schemas.license_schema import LicenseIssue, LicenseExtend, LicenseInfo
from services.license_service import issue_license, extend_license
from datetime import datetime
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin", tags=["管理后台"])


def get_current_admin(authorization: str = Header(None), db: Session = Depends(get_db)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="未提供认证信息")
    try:
        payload = decode_token(authorization.split(" ")[1])
        # 兼容两种token：is_admin(bool) 或 role==admin
        is_admin = payload.get("is_admin", False) or payload.get("role") == "admin"
        if not is_admin:
            raise HTTPException(status_code=403, detail="需要管理员权限")
        return payload
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Token验证失败: {e}")
        raise HTTPException(status_code=401, detail="Token无效")


@router.get("/dashboard")
def dashboard(db: Session = Depends(get_db), _=Depends(get_current_admin)):
    ensure_admin_exists(db)
    total_users = db.query(User).count()
    total_licenses = db.query(License).count()
    active_licenses = db.query(License).filter(License.is_active == True).count()
    expired_licenses = db.query(License).filter(License.expires_at < datetime.utcnow()).count()
    total_devices = db.query(Device).count()
    active_devices = db.query(Device).filter(Device.is_active == True).count()
    return {
        "total_users": total_users,
        "total_licenses": total_licenses,
        "active_licenses": active_licenses,
        "expired_licenses": expired_licenses,
        "total_devices": total_devices,
        "active_devices": active_devices,
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


@router.get("/devices")
def list_devices(db: Session = Depends(get_db), _=Depends(get_current_admin)):
    devices = db.query(Device).all()
    return [
        {
            "id": d.id,
            "license_id": d.license_id,
            "machine_id": d.machine_id,
            "device_name": d.device_name,
            "is_active": d.is_active,
            "activated_at": d.activated_at.isoformat() if d.activated_at else None,
            "last_verified": d.last_verified.isoformat() if d.last_verified else None,
        }
        for d in devices
    ]


@router.get("/licenses")
def list_licenses(db: Session = Depends(get_db), _=Depends(get_current_admin)):
    licenses = db.query(License).all()
    return [
        {
            "id": l.id,
            "license_key": l.license_key,
            "user_id": l.user_id,
            "machine_id": l.machine_id or "",
            "expires_at": l.expires_at.isoformat() if l.expires_at else None,
            "is_active": l.is_active,
            "days": l.days,
            "issued_at": l.issued_at.isoformat() if l.issued_at else None,
            "activated_at": l.activated_at.isoformat() if l.activated_at else None,
        }
        for l in licenses
    ]


@router.post("/license/issue", response_model=LicenseInfo)
def admin_issue_license(data: LicenseIssue, db: Session = Depends(get_db), _=Depends(get_current_admin)):
    return issue_license(db, data)


@router.put("/license/extend", response_model=LicenseInfo)
def admin_extend_license(data: LicenseExtend, db: Session = Depends(get_db), _=Depends(get_current_admin)):
    return extend_license(db, data)
