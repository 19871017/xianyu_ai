from pydantic import BaseModel
from typing import Optional
from datetime import datetime


class LicenseActivate(BaseModel):
    license_key: str
    machine_id: str


class LicenseVerify(BaseModel):
    license_key: str
    machine_id: str


class LicenseIssue(BaseModel):
    user_id: int
    days: int = 30


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
    created_at: datetime
    activated_at: Optional[datetime] = None

    class Config:
        from_attributes = True
