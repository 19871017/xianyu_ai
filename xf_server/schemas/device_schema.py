from pydantic import BaseModel
from typing import Optional
from datetime import datetime


class DeviceInfo(BaseModel):
    id: int
    license_id: int
    machine_id: str
    device_name: str
    ip_address: str
    is_active: bool
    activated_at: datetime
    last_verified: datetime

    class Config:
        from_attributes = True
