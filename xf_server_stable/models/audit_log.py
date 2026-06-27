from sqlalchemy import Column, Integer, String, DateTime, Text
from datetime import datetime
from models.database import Base


class AuditLog(Base):
    """审计日志：记录关键控制操作与客户端校验事件，便于追踪与排查。"""

    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    actor = Column(String(80), default="")        # 操作者（管理员用户名 / system / client）
    action = Column(String(60), nullable=False, index=True)  # issue/revoke/extend/force_offline/...
    target = Column(String(128), default="")      # 受影响对象（license_key / machine_id）
    ip_address = Column(String(50), default="")
    detail = Column(Text, default="")
