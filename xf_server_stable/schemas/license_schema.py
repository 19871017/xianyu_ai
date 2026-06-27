from pydantic import BaseModel
from typing import Optional
from datetime import datetime


class LicenseActivate(BaseModel):
    license_key: str
    machine_id: str
    device_name: Optional[str] = ""


class LicenseVerify(BaseModel):
    license_key: str
    machine_id: str
    # 防重放：客户端请求时间戳（unix 秒）。可选以兼容旧客户端。
    ts: Optional[int] = None


class LicenseHeartbeat(BaseModel):
    license_key: str
    machine_id: str
    ts: Optional[int] = None


class LicenseIssue(BaseModel):
    user_id: int
    days: int = 30
    max_devices: int = 0
    note: str = ""


class LicenseExtend(BaseModel):
    license_key: str
    days: int = 30


class LicenseInfo(BaseModel):
    id: int
    license_key: str
    user_id: int
    machine_id: str
    expires_at: datetime
    is_active: bool
    days: int
    max_devices: int = 0
    note: str = ""
    issued_at: datetime
    activated_at: Optional[datetime] = None

    class Config:
        from_attributes = True
