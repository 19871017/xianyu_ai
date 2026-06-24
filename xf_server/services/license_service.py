from sqlalchemy.orm import Session
from models.license_model import License
from models.device import Device
from schemas.license_schema import LicenseActivate, LicenseVerify, LicenseIssue, LicenseExtend
from utils.rsa_utils import sign_data, verify_signature
from utils.helpers import generate_license_key
from config import MAX_DEVICES_PER_LICENSE
from datetime import datetime, timedelta
from fastapi import HTTPException


def issue_license(db: Session, data: LicenseIssue) -> License:
    """签发新License"""
    license_key = generate_license_key()
    expires_at = datetime.utcnow() + timedelta(days=data.days)

    license_obj = License(
        license_key=license_key,
        user_id=data.user_id,
        expires_at=expires_at,
        days=data.days,
    )
    db.add(license_obj)
    db.commit()
    db.refresh(license_obj)
    return license_obj


def activate_license(db: Session, data: LicenseActivate) -> dict:
    """激活License，绑定机器码"""
    license_obj = db.query(License).filter(License.license_key == data.license_key).first()
    if not license_obj:
        raise HTTPException(status_code=404, detail="License不存在")
    if not license_obj.is_active:
        raise HTTPException(status_code=400, detail="License已被吊销")
    if license_obj.expires_at < datetime.utcnow():
        raise HTTPException(status_code=400, detail="License已过期")

    # 检查设备数限制
    if license_obj.machine_id and license_obj.machine_id != data.machine_id:
        device_count = db.query(Device).filter(
            Device.license_id == license_obj.id,
            Device.is_active == True,
        ).count()
        if device_count >= MAX_DEVICES_PER_LICENSE:
            raise HTTPException(status_code=400, detail=f"已达最大设备数限制({MAX_DEVICES_PER_LICENSE})")

    # 生成签名
    sign_data_str = f"{data.license_key}:{data.machine_id}:{license_obj.expires_at.isoformat()}"
    signature = sign_data(sign_data_str)

    # 更新License
    license_obj.machine_id = data.machine_id
    license_obj.signature = signature
    license_obj.activated_at = datetime.utcnow()
    db.commit()
    db.refresh(license_obj)

    # 记录设备
    existing_device = db.query(Device).filter(
        Device.license_id == license_obj.id,
        Device.machine_id == data.machine_id,
    ).first()
    if not existing_device:
        device = Device(
            license_id=license_obj.id,
            machine_id=data.machine_id,
        )
        db.add(device)
        db.commit()

    return {
        "license_key": license_obj.license_key,
        "machine_id": data.machine_id,
        "signature": signature,
        "expires_at": license_obj.expires_at.isoformat(),
    }


def verify_license(db: Session, data: LicenseVerify) -> dict:
    """验证License有效性"""
    license_obj = db.query(License).filter(License.license_key == data.license_key).first()
    if not license_obj:
        return {"valid": False, "reason": "License不存在"}
    if not license_obj.is_active:
        return {"valid": False, "reason": "License已被吊销"}
    if license_obj.expires_at < datetime.utcnow():
        return {"valid": False, "reason": "License已过期"}
    if license_obj.machine_id and license_obj.machine_id != data.machine_id:
        return {"valid": False, "reason": "机器码不匹配"}

    # RSA验签
    sign_data_str = f"{data.license_key}:{data.machine_id}:{license_obj.expires_at.isoformat()}"
    if license_obj.signature and not verify_signature(sign_data_str, license_obj.signature):
        return {"valid": False, "reason": "签名验证失败"}

    # 更新设备最后验证时间
    device = db.query(Device).filter(
        Device.license_id == license_obj.id,
        Device.machine_id == data.machine_id,
    ).first()
    if device:
        device.last_verified = datetime.utcnow()
        db.commit()

    return {
        "valid": True,
        "expires_at": license_obj.expires_at.isoformat(),
        "signature": license_obj.signature,
    }


def revoke_license(db: Session, license_key: str) -> dict:
    """吊销License"""
    license_obj = db.query(License).filter(License.license_key == license_key).first()
    if not license_obj:
        raise HTTPException(status_code=404, detail="License不存在")
    license_obj.is_active = False
    db.commit()
    return {"message": "License已吊销"}


def extend_license(db: Session, data: LicenseExtend) -> License:
    """续期License"""
    license_obj = db.query(License).filter(License.license_key == data.license_key).first()
    if not license_obj:
        raise HTTPException(status_code=404, detail="License不存在")

    license_obj.expires_at += timedelta(days=data.days)
    # 重新签名
    sign_data_str = f"{license_obj.license_key}:{license_obj.machine_id or ''}:{license_obj.expires_at.isoformat()}"
    license_obj.signature = sign_data(sign_data_str)
    db.commit()
    db.refresh(license_obj)
    return license_obj
