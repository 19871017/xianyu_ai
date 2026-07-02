"""服务端计算接口（选品打分样板）——核心算法只在云端，客户端不持有源码。

鉴权链路（两段式）：
  1) POST /api/compute/token
       客户端用 license_key + machine_id 换取短期「计算令牌」(compute JWT)。
       复用现有 verify_license 全套校验（吊销/过期/设备/强制下线/防重放）。
       仍要求 X-Client-Key，杜绝匿名换取。
  2) POST /api/compute/profit_score
       客户端带 Bearer <compute JWT> 调用；服务端跑 profit_score 算法返回结果。
       令牌短时效（默认 10 分钟），过期需重新换取，降低令牌泄漏的重放价值。

为什么能防逆向：算法源码不在客户端，逆向客户端也拿不到 profit_score 实现；
破解者只能黑盒调接口，且受 X-Client-Key + 短时效 JWT + 后续可加的限流约束。
"""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from jose import jwt, JWTError
from sqlalchemy.orm import Session

from models.database import get_db
from routers.license_api import require_client_key, _client_ip
from schemas.compute_schema import (
    ComputeTokenRequest, ComputeToken,
    ProfitScoreRequest, ProfitScoreResponse,
)
from schemas.license_schema import LicenseVerify
from services.license_service import verify_license
from services.profit_score import rank_products
from config import JWT_SECRET_KEY, JWT_ALGORITHM

router = APIRouter(prefix="/api/compute", tags=["Compute"])

# 计算令牌时效（秒）：短时效以降低泄漏重放价值。
COMPUTE_TOKEN_TTL = 600
_COMPUTE_TOKEN_TYPE = "compute"


def _issue_compute_token(license_key: str, machine_id: str) -> str:
    now = int(time.time())
    payload = {
        "sub": license_key,
        "machine_id": machine_id,
        "type": _COMPUTE_TOKEN_TYPE,
        "iat": now,
        "exp": now + COMPUTE_TOKEN_TTL,
    }
    return jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


def require_compute_token(authorization: str = Header(None)) -> dict:
    """校验 Bearer 计算令牌；失败抛 401。返回 payload。"""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="缺少计算令牌")
    token = authorization.split(" ", 1)[1].strip()
    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
    except JWTError:
        raise HTTPException(status_code=401, detail="计算令牌无效或已过期")
    if payload.get("type") != _COMPUTE_TOKEN_TYPE:
        raise HTTPException(status_code=401, detail="令牌类型不匹配")
    return payload


@router.post("/token", response_model=ComputeToken)
def get_compute_token(data: ComputeTokenRequest, request: Request,
                      db: Session = Depends(get_db),
                      _=Depends(require_client_key)):
    """用 license 换取短期计算令牌（复用 verify_license 全套校验）。"""
    result = verify_license(
        db,
        LicenseVerify(license_key=data.license_key, machine_id=data.machine_id, ts=data.ts),
        ip=_client_ip(request),
    )
    if not result.get("valid"):
        raise HTTPException(status_code=403, detail=result.get("reason", "授权无效"))
    token = _issue_compute_token(data.license_key, data.machine_id)
    return ComputeToken(access_token=token, expires_in=COMPUTE_TOKEN_TTL)


@router.post("/profit_score", response_model=ProfitScoreResponse)
def compute_profit_score(data: ProfitScoreRequest,
                         payload=Depends(require_compute_token)):
    """选品打分：算法在服务端执行，客户端只收结果。"""
    ranked = rank_products(
        data.products,
        shipping_cost=data.shipping_cost,
        platform_fee_pct=data.platform_fee_pct,
        extra_cost=data.extra_cost,
        target_markup_pct=data.target_markup_pct,
    )
    return ProfitScoreResponse(ranked=ranked, count=len(ranked))
