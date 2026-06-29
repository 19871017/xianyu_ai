from sqlalchemy import Column, Integer, String, DateTime, Boolean, Text
from datetime import datetime
from models.database import Base


class Announcement(Base):
    """下载站公告栏条目。"""

    __tablename__ = "announcements"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(200), nullable=False)
    content = Column(Text, default="")
    # 是否发布（下架的公告不在前台展示）。
    is_published = Column(Boolean, default=True, index=True)
    # 置顶排序：值越大越靠前，相同则按时间倒序。
    pinned = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
