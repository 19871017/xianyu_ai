from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session
from models.database import get_db
from schemas.license_schema import (
    LicenseActivate, LicenseVerify, LicenseHeartbeat,
    LicenseIssue, LicenseExtend, LicenseInfo,
)
from services.license_service import (
    activate_license, verify_license, heartbeat, revoke_license,
    issue_license, extend_license,
)
from routers.admin import get_current_admin
from config import CLIENT_API_KEY, REQUIRE_CLIENT_KEY
import hmac

router = APIRouter(prefix="/api/license", tags=["License"])


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else ""


def require_client_key(x_client_key: str = Header(None)):
    """客户端调用 activate/verify/heartbeat 的密钥校验。

    REQUIRE_CLIENT_KEY=0（默认）时：兼容旧客户端——不带 Key 放行，
    但若带了 Key 则必须正确（防止错误配置静默通过）。
    REQUIRE_CLIENT_KEY=1 时：强制要求正确 Key。
    """
    if not REQUIRE_CLIENT_KEY and not x_client_key:
        return True
    if not x_client_key or not hmac.compare_digest(x_client_key, CLIENT_API_KEY):
        raise HTTPException(status_code=401, detail="客户端密钥无效")
    return True


@router.post("/activate")
def activate(data: LicenseActivate, request: Request, db: Session = Depends(get_db),
             _=Depends(require_client_key)):
    return activate_license(db, data, ip=_client_ip(request))


@router.get("/verify")
def verify(license_key: str, machine_id: str, request: Request, ts: int = None,
           db: Session = Depends(get_db), _=Depends(require_client_key)):
    data = LicenseVerify(license_key=license_key, machine_id=machine_id, ts=ts)
    return verify_license(db, data, ip=_client_ip(request))


@router.post("/heartbeat")
def do_heartbeat(data: LicenseHeartbeat, request: Request, db: Session = Depends(get_db),
                 _=Depends(require_client_key)):
    return heartbeat(db, data, ip=_client_ip(request))


@router.post("/revoke")
def revoke(license_key: str, request: Request, db: Session = Depends(get_db),
           admin=Depends(get_current_admin)):
    return revoke_license(db, license_key, actor=admin.get("sub", "admin"), ip=_client_ip(request))


@router.post("/issue", response_model=LicenseInfo)
def issue(data: LicenseIssue, request: Request, db: Session = Depends(get_db),
          admin=Depends(get_current_admin)):
    return issue_license(db, data, actor=admin.get("sub", "admin"), ip=_client_ip(request))


@router.put("/extend", response_model=LicenseInfo)
def extend(data: LicenseExtend, request: Request, db: Session = Depends(get_db),
           admin=Depends(get_current_admin)):
    return extend_license(db, data, actor=admin.get("sub", "admin"), ip=_client_ip(request))
