from sqlalchemy.orm import Session
from models.license_model import License
from models.device import Device
from models.user import User
from schemas.license_schema import (
    LicenseActivate, LicenseVerify, LicenseHeartbeat, LicenseIssue, LicenseExtend,
)
from utils.rsa_utils import sign_data, verify_signature
from utils.helpers import generate_license_key
from services.audit_service import log_action
from config import (
    MAX_DEVICES_PER_LICENSE, OFFLINE_THRESHOLD_SECONDS,
    REQUEST_TIMESTAMP_WINDOW_SECONDS, HEARTBEAT_INTERVAL_SECONDS,
)
import time
from datetime import datetime, timedelta
from fastapi import HTTPException
import logging

logger = logging.getLogger(__name__)


# ──────────────────────── 内部工具 ────────────────────────

def _effective_max_devices(license_obj: License) -> int:
    if getattr(license_obj, "max_devices", 0) and license_obj.max_devices > 0:
        return license_obj.max_devices
    return MAX_DEVICES_PER_LICENSE


def _check_timestamp(ts) -> bool:
    """校验客户端时间戳是否在允许窗口内（防重放/防回拨）。

    ts 为 None 时放行（兼容旧客户端），但新客户端应始终带 ts。
    """
    if ts is None:
        return True
    try:
        ts = int(ts)
    except (TypeError, ValueError):
        return False
    now = int(time.time())
    return abs(now - ts) <= REQUEST_TIMESTAMP_WINDOW_SECONDS


def _is_online(device: Device) -> bool:
    ref = device.last_heartbeat or device.last_verified
    if not ref:
        return False
    return (datetime.utcnow() - ref).total_seconds() <= OFFLINE_THRESHOLD_SECONDS


# ──────────────────────── 签发 / 续期 / 吊销 ────────────────────────

def issue_license(db: Session, data: LicenseIssue, actor: str = "admin", ip: str = "") -> License:
    """签发新License"""
    user = db.query(User).filter(User.id == data.user_id).first()
    if not user:
        raise HTTPException(status_code=400, detail=f"用户ID {data.user_id} 不存在")
    if data.days <= 0:
        raise HTTPException(status_code=400, detail="天数必须大于0")

    license_key = generate_license_key()
    expires_at = datetime.utcnow() + timedelta(days=data.days)

    license_obj = License(
        license_key=license_key,
        user_id=data.user_id,
        expires_at=expires_at,
        days=data.days,
        machine_id="",
        signature="",
        max_devices=max(0, data.max_devices),
        note=data.note or "",
    )
    db.add(license_obj)
    db.commit()
    db.refresh(license_obj)
    log_action(db, "issue", actor=actor, target=license_key, ip_address=ip,
               detail=f"user_id={data.user_id} days={data.days} max_devices={data.max_devices}")
    logger.info(f"签发License成功: key={license_key}, user_id={data.user_id}, days={data.days}")
    return license_obj


def activate_license(db: Session, data: LicenseActivate, ip: str = "") -> dict:
    """激活License，绑定机器码"""
    license_obj = db.query(License).filter(License.license_key == data.license_key).first()
    if not license_obj:
        raise HTTPException(status_code=404, detail="License不存在")
    if not license_obj.is_active:
        raise HTTPException(status_code=400, detail="License已被吊销")

    now = datetime.utcnow()
    if license_obj.expires_at < now:
        raise HTTPException(status_code=400, detail="License已过期")

    # 设备数限制：统计该 License 下已激活且未被强制下线的不同设备
    max_devices = _effective_max_devices(license_obj)
    existing_device = db.query(Device).filter(
        Device.license_id == license_obj.id,
        Device.machine_id == data.machine_id,
    ).first()
    if not existing_device:
        active_count = db.query(Device).filter(
            Device.license_id == license_obj.id,
            Device.is_active == True,  # noqa: E712
        ).count()
        if active_count >= max_devices:
            raise HTTPException(status_code=400, detail=f"已达最大设备数限制({max_devices})")

    # 绑定主机器码（首次激活时记录），并按"本机 machine_id"签名
    if not license_obj.machine_id:
        license_obj.machine_id = data.machine_id
    sign_data_str = f"{data.license_key}:{data.machine_id}:{license_obj.expires_at.isoformat()}"
    try:
        signature = sign_data(sign_data_str)
    except Exception as e:
        logger.error(f"签名生成失败: {e}")
        raise HTTPException(status_code=500, detail="签名生成失败")

    license_obj.activated_at = now

    # 记录/复活设备：签名存储在设备行（每台设备独立签名，互不覆盖）
    if existing_device:
        existing_device.is_active = True
        existing_device.force_offline = False
        existing_device.activated_at = now
        existing_device.signature = signature
        if data.device_name:
            existing_device.device_name = data.device_name
        if ip:
            existing_device.ip_address = ip
    else:
        device = Device(
            license_id=license_obj.id,
            machine_id=data.machine_id,
            device_name=data.device_name or "",
            ip_address=ip,
            activated_at=now,
            signature=signature,
        )
        db.add(device)
    db.commit()
    db.refresh(license_obj)

    log_action(db, "activate", actor="client", target=data.machine_id, ip_address=ip,
               detail=f"license={data.license_key}")

    return {
        "license_key": license_obj.license_key,
        "machine_id": data.machine_id,
        "signature": signature,
        "expires_at": license_obj.expires_at.isoformat(),
        "heartbeat_interval": HEARTBEAT_INTERVAL_SECONDS,
    }


def verify_license(db: Session, data: LicenseVerify, ip: str = "") -> dict:
    """验证License有效性（含设备级强制下线 / 防重放）"""
    if not _check_timestamp(data.ts):
        return {"valid": False, "reason": "请求时间戳无效（可能重放或时钟偏差）"}

    license_obj = db.query(License).filter(License.license_key == data.license_key).first()
    if not license_obj:
        return {"valid": False, "reason": "License不存在"}
    if not license_obj.is_active:
        return {"valid": False, "reason": "License已被吊销"}

    now = datetime.utcnow()
    if license_obj.expires_at < now:
        return {"valid": False, "reason": "License已过期"}

    # 校验用户是否被禁用
    user = db.query(User).filter(User.id == license_obj.user_id).first()
    if user and not user.is_active:
        return {"valid": False, "reason": "账号已被禁用"}

    device = db.query(Device).filter(
        Device.license_id == license_obj.id,
        Device.machine_id == data.machine_id,
    ).first()
    if not device:
        return {"valid": False, "reason": "设备未激活，请重新激活"}
    if device.force_offline:
        return {"valid": False, "reason": "该设备已被管理员强制下线"}
    if not device.is_active:
        return {"valid": False, "reason": "设备已被解绑"}

    # 防重放：ts 必须严格大于上次接受的 ts（带窗口校验后）
    if data.ts is not None:
        if device.last_nonce_ts and int(data.ts) < device.last_nonce_ts:
            return {"valid": False, "reason": "请求时间戳过期（防重放）"}
        device.last_nonce_ts = int(data.ts)

    # RSA验签（按本设备签名；为空则兼容旧数据）
    device_sig = getattr(device, "signature", "") or ""
    if device_sig:
        sign_data_str = f"{data.license_key}:{data.machine_id}:{license_obj.expires_at.isoformat()}"
        if not verify_signature(sign_data_str, device_sig):
            return {"valid": False, "reason": "签名验证失败"}

    device.last_verified = now
    if ip:
        device.ip_address = ip
    db.commit()

    return {
        "valid": True,
        "expires_at": license_obj.expires_at.isoformat(),
        "signature": device_sig,
        "heartbeat_interval": HEARTBEAT_INTERVAL_SECONDS,
    }


def heartbeat(db: Session, data: LicenseHeartbeat, ip: str = "") -> dict:
    """客户端心跳：维持在线状态，并即时反映吊销/强制下线。"""
    if not _check_timestamp(data.ts):
        return {"ok": False, "action": "reject", "reason": "时间戳无效"}

    license_obj = db.query(License).filter(License.license_key == data.license_key).first()
    if not license_obj:
        return {"ok": False, "action": "deactivate", "reason": "License不存在"}
    if not license_obj.is_active:
        return {"ok": False, "action": "deactivate", "reason": "License已被吊销"}
    if license_obj.expires_at < datetime.utcnow():
        return {"ok": False, "action": "deactivate", "reason": "License已过期"}

    device = db.query(Device).filter(
        Device.license_id == license_obj.id,
        Device.machine_id == data.machine_id,
    ).first()
    if not device or not device.is_active:
        return {"ok": False, "action": "deactivate", "reason": "设备未激活或已解绑"}
    if device.force_offline:
        return {"ok": False, "action": "logout", "reason": "已被管理员强制下线"}

    if data.ts is not None:
        device.last_nonce_ts = int(data.ts)
    device.last_heartbeat = datetime.utcnow()
    if ip:
        device.ip_address = ip
    db.commit()
    return {"ok": True, "action": "continue", "interval": HEARTBEAT_INTERVAL_SECONDS}


def revoke_license(db: Session, license_key: str, actor: str = "admin", ip: str = "") -> dict:
    """吊销License（连带停用其所有设备）"""
    license_obj = db.query(License).filter(License.license_key == license_key).first()
    if not license_obj:
        raise HTTPException(status_code=404, detail="License不存在")
    license_obj.is_active = False
    license_obj.revoked_at = datetime.utcnow()
    db.query(Device).filter(Device.license_id == license_obj.id).update(
        {Device.is_active: False, Device.force_offline: True}
    )
    db.commit()
    log_action(db, "revoke", actor=actor, target=license_key, ip_address=ip)
    return {"message": "License已吊销，关联设备已强制下线"}


def extend_license(db: Session, data: LicenseExtend, actor: str = "admin", ip: str = "") -> License:
    """续期License"""
    license_obj = db.query(License).filter(License.license_key == data.license_key).first()
    if not license_obj:
        raise HTTPException(status_code=404, detail="License不存在")

    base = license_obj.expires_at if license_obj.expires_at > datetime.utcnow() else datetime.utcnow()
    license_obj.expires_at = base + timedelta(days=data.days)
    # 到期时间变了，所有已绑定设备需按新到期时间重新签名
    devices = db.query(Device).filter(Device.license_id == license_obj.id).all()
    for dev in devices:
        sign_data_str = f"{license_obj.license_key}:{dev.machine_id}:{license_obj.expires_at.isoformat()}"
        try:
            dev.signature = sign_data(sign_data_str)
        except Exception as e:
            logger.error(f"续期重新签名失败 machine={dev.machine_id}: {e}")
    db.commit()
    db.refresh(license_obj)
    log_action(db, "extend", actor=actor, target=data.license_key, ip_address=ip,
               detail=f"+{data.days}d -> {license_obj.expires_at.isoformat()}")
    return license_obj


# ──────────────────────── 设备控制 ────────────────────────

def set_device_force_offline(db: Session, device_id: int, value: bool,
                             actor: str = "admin", ip: str = "") -> dict:
    device = db.query(Device).filter(Device.id == device_id).first()
    if not device:
        raise HTTPException(status_code=404, detail="设备不存在")
    device.force_offline = value
    if value:
        device.last_heartbeat = None
    db.commit()
    log_action(db, "force_offline" if value else "allow_online",
               actor=actor, target=device.machine_id, ip_address=ip)
    return {"message": "已强制下线" if value else "已允许上线"}


def unbind_device(db: Session, device_id: int, actor: str = "admin", ip: str = "") -> dict:
    device = db.query(Device).filter(Device.id == device_id).first()
    if not device:
        raise HTTPException(status_code=404, detail="设备不存在")
    device.is_active = False
    device.force_offline = True
    db.commit()
    log_action(db, "unbind_device", actor=actor, target=device.machine_id, ip_address=ip)
    return {"message": "设备已解绑"}


def device_is_online(device: Device) -> bool:
    return _is_online(device)
