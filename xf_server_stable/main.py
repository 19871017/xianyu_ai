from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from contextlib import asynccontextmanager
from models.database import init_db, SessionLocal
from services.auth_service import ensure_admin_exists
from routers.auth import router as auth_router
from routers.license_api import router as license_router
from routers.admin import router as admin_router
from routers.release_api import router as release_router
from utils.rsa_utils import ensure_keys
import config
import os
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("xf_server")

ADMIN_FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "admin_frontend")


def _bootstrap():
    """初始化数据库 / 密钥 / 管理员，并做启动期安全自检。"""
    init_db()
    ensure_keys()
    for w in config.validate_config():
        logger.warning("配置告警: %s", w)
    db = SessionLocal()
    try:
        ensure_admin_exists(db)
    finally:
        db.close()
    if getattr(config, "ADMIN_PASSWORD_GENERATED", False):
        logger.warning(
            "首次启动生成随机管理员密码，请登录后立即修改: 用户名=%s 密码=%s (见 keys/admin_password.txt)",
            config.ADMIN_USERNAME, config.ADMIN_PASSWORD,
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    _bootstrap()
    yield


app = FastAPI(title="闲鱼AI助手后端", version="2.1.0", lifespan=lifespan)

# CORS：默认不放开跨域（管理后台与 API 同源）；如需跨域用 CORS_ORIGINS 环境变量显式配置。
if config.CORS_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

app.include_router(auth_router)
app.include_router(license_router)
app.include_router(admin_router)
app.include_router(release_router)


@app.get("/admin")
def admin_page():
    index = os.path.join(ADMIN_FRONTEND_DIR, "index.html")
    if os.path.exists(index):
        return FileResponse(index)
    return {"message": "管理后台前端未部署"}


# 静态目录可能在某些部署下不存在，做容错挂载。
_static_dir = os.path.join(ADMIN_FRONTEND_DIR, "static")
if os.path.isdir(_static_dir):
    app.mount("/admin/static", StaticFiles(directory=_static_dir), name="admin-static")
