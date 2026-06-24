from sqlalchemy.orm import Session
from models.device import Device
from fastapi import HTTPException


def get_devices_by_license(db: Session, license_id: int):
    return db.query(Device).filter(Device.license_id == license_id).all()


def unbind_device(db: Session, device_id: int):
    device = db.query(Device).filter(Device.id == device_id).first()
    if not device:
        raise HTTPException(status_code=404, detail="设备不存在")
    device.is_active = False
    db.commit()
    return {"message": "设备已解绑"}
