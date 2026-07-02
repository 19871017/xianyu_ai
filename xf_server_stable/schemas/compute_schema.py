"""服务端计算接口的请求/响应模型（选品打分样板）。

设计意图（防逆向核心）：
- 选品打分算法（profit_score）只存在于服务端，客户端不再持有源码。
- 客户端先用 license+machine_id 换取短期「计算令牌」(compute JWT)，
  再带该令牌调用 /api/compute/profit_score。算法在云端跑，客户端只收结果。
"""
from __future__ import annotations

from typing import Any, Optional
from pydantic import BaseModel


class ComputeTokenRequest(BaseModel):
    """用 license 换取短期计算令牌（复用现有 license 校验，不新增账号体系）。"""
    license_key: str
    machine_id: str
    ts: Optional[int] = None


class ComputeToken(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int  # 秒


class ProfitScoreRequest(BaseModel):
    """选品打分请求：客户端把本地采集到的原始商品数据传上来，服务端算分。"""
    products: list[dict[str, Any]]
    shipping_cost: float = 5.0
    platform_fee_pct: float = 0.6
    extra_cost: float = 0.0
    target_markup_pct: float = 0.0


class ProfitScoreResponse(BaseModel):
    ranked: list[dict[str, Any]]
    count: int
