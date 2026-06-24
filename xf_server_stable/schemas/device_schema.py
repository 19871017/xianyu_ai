from pydantic import BaseModel


class DeviceInfo(BaseModel):
    id: int
    license_id: int
    machine_id: str
    device_name: str
    is_active: bool
