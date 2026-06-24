from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.orm import Session
from models.database import get_db
from models.user import User
from utils.security import decode_token, create_access_token, create_refresh_token
from schemas.auth import UserRegister, UserLogin, Token, TokenRefresh
from services.auth_service import register_user, login_user

router = APIRouter(prefix="/api/auth", tags=["认证"])


@router.post("/register")
def register(user_data: UserRegister, db: Session = Depends(get_db)):
    user = register_user(db, user_data)
    return {"message": "注册成功", "user_id": user.id}


@router.post("/login", response_model=Token)
def login(user_data: UserLogin, db: Session = Depends(get_db)):
    return login_user(db, user_data)


@router.post("/refresh", response_model=Token)
def refresh_token(data: TokenRefresh):
    try:
        payload = decode_token(data.refresh_token)
        if payload.get("type") != "refresh":
            raise HTTPException(status_code=401, detail="无效的refresh token")
        token_data = {
            "sub": payload["sub"],
            "user_id": payload["user_id"],
            "is_admin": payload.get("is_admin", False),
        }
        return Token(
            access_token=create_access_token(token_data),
            refresh_token=create_refresh_token(token_data),
        )
    except Exception:
        raise HTTPException(status_code=401, detail="Token无效或已过期")
