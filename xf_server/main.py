from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from models.database import init_db
from services.auth_service import ensure_admin_exists
from models.database import SessionLocal
from routers.auth import router as auth_router
from routers.license_api import router as license_router
from routers.admin import router as admin_router
from utils.rsa_utils import ensure_keys
import os

app = FastAPI(title="闲鱼AI助手后端", version="1.0.0")

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
    return {"message": "闲鱼AI助手后端服务", "version": "1.0.0"}


# 管理后台
@app.get("/admin")
def admin_page():
    return FileResponse(os.path.join(ADMIN_FRONTEND_DIR, "index.html"))


app.mount("/admin/static", StaticFiles(directory=os.path.join(ADMIN_FRONTEND_DIR, "static")), name="admin-static")
