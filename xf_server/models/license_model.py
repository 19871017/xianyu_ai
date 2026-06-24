from sqlalchemy import Column, Integer, String, DateTime, Boolean
from datetime import datetime
from models.database import Base


class License(Base):
    __tablename__ = "licenses"

    id = Column(Integer, primary_key=True, index=True)
    license_key = Column(String(64), unique=True, index=True, nullable=False)
    user_id = Column(Integer, nullable=False)
    machine_id = Column(String(128), default="")
    signature = Column(String(512), default="")
    expires_at = Column(DateTime, nullable=False)
    is_active = Column(Boolean, default=True)
    days = Column(Integer, default=30)
    created_at = Column(DateTime, default=datetime.utcnow)
    activated_at = Column(DateTime, nullable=True)
