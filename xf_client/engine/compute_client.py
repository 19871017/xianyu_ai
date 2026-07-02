"""服务端计算客户端（防逆向样板）：核心算法在云端，客户端只负责取数、送数、收结果。

与 license_validator 的关系：
- 复用其 machine_id 与已激活的 license_key，用它们换取短期「计算令牌」(compute JWT)。
- 令牌本地缓存至过期前，减少每次计算都打网络。

为什么这样能防逆向：
- 选品打分算法（profit_score）不再随客户端分发，逆向 exe 也拿不到实现。
- 客户端此模块只有网络调用与数据搬运，没有任何值钱逻辑，泄漏无价值。
"""
from __future__ import annotations

import time
from typing import Any

import requests

from config import (
    CLIENT_API_KEY, API_COMPUTE_TOKEN, API_COMPUTE_PROFIT_SCORE,
)


class ComputeError(RuntimeError):
    """服务端计算调用失败：未授权 / 未联网 / 服务异常。"""


class ComputeClient:
    """调用服务端计算接口。持有一个 LicenseValidator 以复用 machine_id/license。"""

    def __init__(self, validator):
        self._validator = validator
        # 计算令牌缓存：(token, expire_ts)。
        self._token = None
        self._token_exp = 0

    def _headers(self) -> dict:
        h = {}
        if CLIENT_API_KEY:
            h["X-Client-Key"] = CLIENT_API_KEY
        return h

    def _license_key(self) -> str:
        return (self._validator.get_license_info() or {}).get("license_key", "")

    def _ensure_token(self) -> str:
        """取一个有效的计算令牌（本地缓存至到期前 30 秒复用）。"""
        now = int(time.time())
        if self._token and self._token_exp - now > 30:
            return self._token

        license_key = self._license_key()
        if not license_key:
            raise ComputeError("未激活，无法调用服务端计算")
        try:
            resp = requests.post(
                API_COMPUTE_TOKEN,
                json={
                    "license_key": license_key,
                    "machine_id": self._validator.machine_id,
                    "ts": now,
                },
                headers=self._headers(),
                timeout=10,
            )
        except Exception:
            raise ComputeError("无法连接服务器，选品打分需联网")
        if resp.status_code == 401:
            raise ComputeError("客户端密钥无效")
        if resp.status_code == 403:
            try:
                reason = resp.json().get("detail", "授权无效")
            except Exception:
                reason = "授权无效"
            raise ComputeError(reason)
        if resp.status_code != 200:
            raise ComputeError(f"计算令牌服务异常 (HTTP {resp.status_code})")
        try:
            data = resp.json()
        except Exception:
            raise ComputeError("计算令牌响应无法解析")
        self._token = data.get("access_token", "")
        self._token_exp = now + int(data.get("expires_in", 0))
        if not self._token:
            raise ComputeError("计算令牌为空")
        return self._token

    def rank_products(
        self,
        products: list[dict[str, Any]],
        *,
        shipping_cost: float = 5.0,
        platform_fee_pct: float = 0.6,
        extra_cost: float = 0.0,
        target_markup_pct: float = 0.0,
    ) -> list[dict[str, Any]]:
        """调用服务端选品打分，返回已排序结果。失败抛 ComputeError。"""
        token = self._ensure_token()
        headers = self._headers()
        headers["Authorization"] = f"Bearer {token}"
        try:
            resp = requests.post(
                API_COMPUTE_PROFIT_SCORE,
                json={
                    "products": products,
                    "shipping_cost": shipping_cost,
                    "platform_fee_pct": platform_fee_pct,
                    "extra_cost": extra_cost,
                    "target_markup_pct": target_markup_pct,
                },
                headers=headers,
                timeout=30,
            )
        except Exception:
            raise ComputeError("无法连接服务器，选品打分需联网")
        if resp.status_code == 401:
            # 令牌可能过期：清缓存，让下次重新换取。
            self._token = None
            self._token_exp = 0
            raise ComputeError("计算令牌无效或已过期，请重试")
        if resp.status_code != 200:
            raise ComputeError(f"选品打分服务异常 (HTTP {resp.status_code})")
        try:
            data = resp.json()
        except Exception:
            raise ComputeError("选品打分响应无法解析")
        return data.get("ranked", [])
