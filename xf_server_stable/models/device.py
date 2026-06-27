from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Boolean
from datetime import datetime
from models.database import Base


class Device(Base):
    __tablename__ = "devices"

    id = Column(Integer, primary_key=True, index=True)
    license_id = Column(Integer, ForeignKey("licenses.id"), nullable=False)
    machine_id = Column(String(128), nullable=False, index=True)
    device_name = Column(String(100), default="")
    ip_address = Column(String(50), default="")
    is_active = Column(Boolean, default=True)
    # 管理员强制下线：置 True 后客户端下次 verify/heartbeat 立即被拒。
    force_offline = Column(Boolean, default=False)
    activated_at = Column(DateTime, default=datetime.utcnow)
    last_verified = Column(DateTime, default=datetime.utcnow)
    # 最近心跳时间，用于在线状态判定。
    last_heartbeat = Column(DateTime, nullable=True)
    # 防重放：记录最近一次被接受的请求时间戳（来自客户端的 ts）。
    last_nonce_ts = Column(Integer, default=0)
    # 每台设备独立签名（绑定本机 machine_id），支持同一 License 多设备各自离线验签。
    signature = Column(String(512), default="")
