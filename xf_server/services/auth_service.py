from sqlalchemy.orm import Session
from models.user import User
from schemas.auth import UserRegister, UserLogin, Token
from utils.security import hash_password, verify_password, create_access_token, create_refresh_token
from config import ADMIN_USERNAME, ADMIN_PASSWORD
from fastapi import HTTPException


def register_user(db: Session, user_data: UserRegister):
    # 检查用户名是否存在
    existing = db.query(User).filter(User.username == user_data.username).first()
    if existing:
        raise HTTPException(status_code=400, detail="用户名已存在")

    user = User(
        username=user_data.username,
        hashed_password=hash_password(user_data.password),
        is_admin=(user_data.username == ADMIN_USERNAME),
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

    token_data = {"sub": user.username, "user_id": user.id, "is_admin": user.is_admin}
    return Token(
        access_token=create_access_token(token_data),
        refresh_token=create_refresh_token(token_data),
    )


def ensure_admin_exists(db: Session):
    """确保管理员账号存在"""
    admin = db.query(User).filter(User.username == ADMIN_USERNAME).first()
    if not admin:
        admin = User(
            username=ADMIN_USERNAME,
            hashed_password=hash_password(ADMIN_PASSWORD),
            is_admin=True,
        )
        db.add(admin)
        db.commit()
