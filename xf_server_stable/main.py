from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from models.database import init_db, SessionLocal
from services.auth_service import ensure_admin_exists
from routers.auth import router as auth_router
from routers.license_api import router as license_router
from routers.admin import router as admin_router
from utils.rsa_utils import ensure_keys
import os
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

app = FastAPI(title="闲鱼AI助手后端", version="2.0.0")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 路由
app.include_router(auth_router)
app.include_router(license_router)
app.include_router(admin_router)

# 静态文件
ADMIN_FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "admin_frontend")


# 启动时初始化
init_db()
ensure_keys()
db = SessionLocal()
try:
    ensure_admin_exists(db)
finally:
    db.close()


@app.on_event("startup")
def startup():
    init_db()
    ensure_keys()
    db = SessionLocal()
    try:
        ensure_admin_exists(db)
    finally:
        db.close()


@app.get("/")
def root():
    return {"message": "闲鱼AI助手后端服务", "version": "2.0.0", "docs": "/docs"}


@app.get("/admin")
def admin_page():
    return FileResponse(os.path.join(ADMIN_FRONTEND_DIR, "index.html"))


app.mount("/admin/static", StaticFiles(directory=os.path.join(ADMIN_FRONTEND_DIR, "static")), name="admin-static")
