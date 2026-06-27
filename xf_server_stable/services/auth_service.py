from sqlalchemy.orm import Session
from models.user import User
from schemas.auth import UserRegister, UserLogin, Token
from utils.security import hash_password, verify_password, create_access_token, create_refresh_token
from config import ADMIN_USERNAME, ADMIN_PASSWORD, ADMIN_FORCE_RESET, _ADMIN_PW_FROM_ENV
from fastapi import HTTPException


def register_user(db: Session, user_data: UserRegister):
    existing = db.query(User).filter(User.username == user_data.username).first()
    if existing:
        raise HTTPException(status_code=400, detail="用户名已存在")

    role = "admin" if user_data.username == ADMIN_USERNAME else "user"
    user = User(
        username=user_data.username,
        hashed_password=hash_password(user_data.password),
        role=role,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def login_user(db: Session, user_data: UserLogin) -> Token:
    user = db.query(User).filter(User.username == user_data.username).first()
    if not user or not verify_password(user_data.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="账号已被禁用")

    is_admin = user.role == "admin"
    token_data = {"sub": user.username, "user_id": user.id, "is_admin": is_admin}
    return Token(
        access_token=create_access_token(token_data),
        refresh_token=create_refresh_token(token_data),
    )


def ensure_admin_exists(db: Session):
    """确保管理员账号存在；可选地重置线上弱密码。

    当 ADMIN_FORCE_PASSWORD_RESET=1 且通过环境变量提供了 ADMIN_PASSWORD 时，
    把现有 admin 密码重置为该值（修复历史 admin123 弱口令）。
    """
    admin = db.query(User).filter(User.username == ADMIN_USERNAME).first()
    if not admin:
        admin = User(
            username=ADMIN_USERNAME,
            hashed_password=hash_password(ADMIN_PASSWORD),
            role="admin",
        )
        db.add(admin)
        db.commit()
        return
    # 既有账号：按需重置密码 / 修正角色与启用状态
    changed = False
    if ADMIN_FORCE_RESET and _ADMIN_PW_FROM_ENV:
        admin.hashed_password = hash_password(ADMIN_PASSWORD)
        changed = True
    if admin.role != "admin":
        admin.role = "admin"
        changed = True
    if not admin.is_active:
        admin.is_active = True
        changed = True
    if changed:
        db.commit()
