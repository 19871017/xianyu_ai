from pydantic import BaseModel
from typing import Optional


class VersionCreate(BaseModel):
    """管理端创建版本（外部直链方式；上传安装包走 multipart 表单接口）。"""
    platform: str            # mac / win
    version: str             # 形如 3.2.0
    download_url: str = ""   # 外部直链；上传安装包时由服务端填充
    release_notes: str = ""
    force_update: bool = False


class VersionInfo(BaseModel):
    id: int
    platform: str
    version: str
    download_url: str
    file_name: str
    file_size: int
    release_notes: str
    is_latest: bool
    force_update: bool
    created_at: Optional[str] = None


class AnnouncementCreate(BaseModel):
    title: str
    content: str = ""
    is_published: bool = True
    pinned: int = 0


class AnnouncementInfo(BaseModel):
    id: int
    title: str
    content: str
    is_published: bool
    pinned: int
    created_at: Optional[str] = None
