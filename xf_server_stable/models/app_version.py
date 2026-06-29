from sqlalchemy import Column, Integer, String, DateTime, Boolean, Text
from datetime import datetime
from models.database import Base


class AppVersion(Base):
    """客户端软件版本：供下载站展示与客户端更新检测。"""

    __tablename__ = "app_versions"

    id = Column(Integer, primary_key=True, index=True)
    # 平台：mac / win。同一平台可有多条历史版本，最新一条由 is_latest 标记。
    platform = Column(String(10), nullable=False, index=True)
    # 版本号，语义化形如 3.2.0；与客户端 APP_VERSION 比较决定是否提示更新。
    version = Column(String(32), nullable=False)
    # 下载地址：上传安装包时为本服务器相对路径（/downloads/xxx），
    # 也可直接填外部直链（如对象存储 / 网盘直链）。
    download_url = Column(String(512), default="")
    # 上传安装包的原始文件名与大小（字节），外部直链时可为空/0。
    file_name = Column(String(255), default="")
    file_size = Column(Integer, default=0)
    # 更新说明（多行）。
    release_notes = Column(Text, default="")
    # 是否为该平台当前最新版（同平台同一时刻仅一条为 True）。
    is_latest = Column(Boolean, default=True, index=True)
    # 是否强制更新：客户端检测到后提示不可跳过（仅提示语气，不阻断）。
    force_update = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
