import json
import os
import time
import requests
from datetime import datetime
from config import (
    SERVER_URL, LICENSE_FILE, CLIENT_API_KEY,
    API_LICENSE_ACTIVATE, API_LICENSE_VERIFY, API_LICENSE_HEARTBEAT,
    API_LICENSE_CAPABILITY, LICENSE_OFFLINE_GRACE_SECONDS,
)
from license.machine_id import get_machine_id
from license.signature import verify_license_signature, verify_capability_token


class LicenseValidator:
    """License 校验器。

    设计原则（避免认证形同虚设）：
    - 所有服务端调用都带 X-Client-Key 与 ts（时间戳，防重放）。
    - 服务端明确返回 invalid 时，立即判失效，不再回退本地。
    - 仅当服务端不可达时才走本地回退，且受离线宽限窗口限制。
    - 本地缓存记录最近一次"在线校验通过"的时间，用于离线宽限判定。
    """

    def __init__(self):
        self.machine_id = get_machine_id()
        self.license_data = self._load_local()
        self._server_ok = None
        # 能力令牌短期缓存：{action: (token, expire_ts)}，减少每次动作都打网络。
        self._cap_cache = {}

    # ──────────────────────── 本地存储 ────────────────────────
    def _load_local(self) -> dict:
        if os.path.exists(LICENSE_FILE):
            try:
                with open(LICENSE_FILE, "r") as f:
                    return json.load(f)
            except Exception:
                return {}
        return {}

    def _save_local(self, data: dict):
        os.makedirs(os.path.dirname(LICENSE_FILE), exist_ok=True)
        with open(LICENSE_FILE, "w") as f:
            json.dump(data, f, indent=2)

    def _headers(self) -> dict:
        h = {}
        if CLIENT_API_KEY:
            h["X-Client-Key"] = CLIENT_API_KEY
        return h

    # ──────────────────────── 服务器探测 ────────────────────────
    def _check_server(self) -> bool:
        if self._server_ok is not None:
            return self._server_ok
        try:
            resp = requests.get(f"{SERVER_URL}/", timeout=5)
            self._server_ok = (resp.status_code == 200)
        except Exception:
            self._server_ok = False
        return self._server_ok

    # ──────────────────────── 激活 ────────────────────────
    def activate(self, license_key: str) -> dict:
        try:
            resp = requests.post(
                API_LICENSE_ACTIVATE,
                json={
                    "license_key": license_key,
                    "machine_id": self.machine_id,
                    "device_name": self._device_name(),
                },
                headers=self._headers(),
                timeout=15,
            )
            if resp.status_code == 200:
                data = resp.json()
                data["machine_id"] = self.machine_id
                # 强制验签：私钥只在服务端，伪造服务器/响应无法通过
                if not verify_license_signature(
                    data.get("license_key", ""), self.machine_id,
                    data.get("expires_at", ""), data.get("signature", ""),
                ):
                    return {"success": False, "message": "激活响应签名校验失败（服务器不可信）"}
                data["last_online_verify"] = int(time.time())
                self._save_local(data)
                self.license_data = data
                self._server_ok = True
                return {"success": True, "data": data}
            try:
                detail = resp.json().get("detail", "激活失败")
            except Exception:
                detail = f"HTTP {resp.status_code}"
            return {"success": False, "message": detail}
        except requests.exceptions.ConnectionError:
            return {"success": False, "message": f"无法连接服务器 ({SERVER_URL})，请检查网络或服务器是否启动"}
        except requests.exceptions.Timeout:
            return {"success": False, "message": f"服务器响应超时 ({SERVER_URL})"}
        except Exception as e:
            return {"success": False, "message": f"激活失败: {e}"}

    # ──────────────────────── 校验 ────────────────────────
    def verify(self) -> dict:
        if not self.license_data or not self.license_data.get("license_key"):
            return {"valid": False, "reason": "未激活"}

        # 1) 优先远程校验
        if self._check_server():
            try:
                resp = requests.get(
                    API_LICENSE_VERIFY,
                    params={
                        "license_key": self.license_data.get("license_key", ""),
                        "machine_id": self.machine_id,
                        "ts": int(time.time()),
                    },
                    headers=self._headers(),
                    timeout=10,
                )
                if resp.status_code == 200:
                    result = resp.json()
                    if result.get("valid"):
                        # 在线通过也必须验签：假服务器返回 valid:true 但给不出有效签名
                        exp = result.get("expires_at") or self.license_data.get("expires_at", "")
                        sig = result.get("signature") or self.license_data.get("signature", "")
                        if not verify_license_signature(
                            self.license_data.get("license_key", ""),
                            self.machine_id, exp, sig,
                        ):
                            return {"valid": False, "reason": "授权签名校验失败（服务器不可信）", "source": "online"}
                        # 刷新在线校验时间戳与到期时间
                        self.license_data["last_online_verify"] = int(time.time())
                        if result.get("expires_at"):
                            self.license_data["expires_at"] = result["expires_at"]
                        if result.get("signature"):
                            self.license_data["signature"] = result["signature"]
                        self._save_local(self.license_data)
                        return {"valid": True, "expires_at": result.get("expires_at"), "source": "online"}
                    # 服务端明确判失效 → 立即失效，不回退本地
                    return {"valid": False, "reason": result.get("reason", "服务端校验未通过"), "source": "online"}
                if resp.status_code == 401:
                    return {"valid": False, "reason": "客户端密钥无效或缺失", "source": "online"}
            except Exception:
                pass  # 网络异常 → 走离线回退

        # 2) 离线回退（仅服务器不可达时）
        return self._verify_offline()

    def _verify_offline(self) -> dict:
        license_key = self.license_data.get("license_key", "")
        expires_at = self.license_data.get("expires_at", "")
        if not license_key or not expires_at:
            return {"valid": False, "reason": "本地License数据不完整，请联网激活"}

        # 机器码绑定
        if self.license_data.get("machine_id") != self.machine_id:
            return {"valid": False, "reason": "机器码不匹配"}

        # 到期检查
        try:
            exp_str = expires_at.replace("Z", "+00:00")
            if datetime.fromisoformat(exp_str) < datetime.utcnow():
                return {"valid": False, "reason": "License已过期"}
        except Exception:
            pass

        # 离线宽限：距上次在线校验不能超过宽限窗口
        last_online = self.license_data.get("last_online_verify", 0)
        if not last_online:
            return {"valid": False, "reason": "尚未完成在线校验，请联网激活"}
        offline_for = int(time.time()) - int(last_online)
        if offline_for > LICENSE_OFFLINE_GRACE_SECONDS:
            hours = LICENSE_OFFLINE_GRACE_SECONDS // 3600
            return {"valid": False, "reason": f"离线超过 {hours} 小时，请联网重新校验"}

        # 离线也必须验签：防止伪造本地 license 文件绕过
        if not verify_license_signature(
            license_key, self.machine_id, expires_at,
            self.license_data.get("signature", ""),
        ):
            return {"valid": False, "reason": "本地授权签名无效，请联网重新激活"}

        return {"valid": True, "expires_at": expires_at, "source": "offline"}

    # ──────────────────────── 心跳 ────────────────────────
    def heartbeat(self) -> dict:
        """上报心跳；返回服务端指令（continue/logout/deactivate/reject）。"""
        if not self.license_data.get("license_key"):
            return {"ok": False, "action": "deactivate", "reason": "未激活"}
        try:
            resp = requests.post(
                API_LICENSE_HEARTBEAT,
                json={
                    "license_key": self.license_data.get("license_key", ""),
                    "machine_id": self.machine_id,
                    "ts": int(time.time()),
                },
                headers=self._headers(),
                timeout=8,
            )
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code == 401:
                return {"ok": False, "action": "reject", "reason": "客户端密钥无效"}
            return {"ok": False, "action": "continue", "reason": f"HTTP {resp.status_code}"}
        except Exception:
            # 网络异常不强制下线，交由 verify 的离线宽限处理
            return {"ok": True, "action": "continue", "reason": "offline"}

    # ──────────────────────── 能力令牌（方案B） ────────────────────────
    def acquire_capability(self, action: str) -> dict:
        """执行受控动作前换取服务端短期签名令牌，并本地验签。

        破解版客户端拿不到服务端私钥，无法伪造令牌；因此即便本地授权文件
        被伪造、UI 显示"已激活"，核心功能仍会因拿不到有效令牌而被拒。

        返回 {"ok": bool, "reason": str}。ok=True 才允许执行动作。
        """
        # 短期缓存命中（还剩足够余量）即复用，减少每次动作都打网络。
        cached = self._cap_cache.get(action)
        if cached:
            token, expire_ts = cached
            if expire_ts - int(time.time()) > 30 and verify_capability_token(
                action, self.machine_id, expire_ts, token
            ):
                return {"ok": True, "reason": "cached"}

        license_key = (self.license_data or {}).get("license_key", "")
        if not license_key:
            return {"ok": False, "reason": "未激活"}
        try:
            resp = requests.post(
                API_LICENSE_CAPABILITY,
                json={
                    "license_key": license_key,
                    "machine_id": self.machine_id,
                    "action": action,
                    "ts": int(time.time()),
                },
                headers=self._headers(),
                timeout=10,
            )
        except Exception:
            return {"ok": False, "reason": "无法连接授权服务器，核心功能需联网授权"}
        if resp.status_code == 401:
            return {"ok": False, "reason": "客户端密钥无效"}
        if resp.status_code != 200:
            return {"ok": False, "reason": f"授权服务异常 (HTTP {resp.status_code})"}
        try:
            data = resp.json()
        except Exception:
            return {"ok": False, "reason": "授权响应无法解析"}
        if not data.get("ok"):
            return {"ok": False, "reason": data.get("reason", "授权被拒绝")}

        token = data.get("token", "")
        expire_ts = data.get("expire_ts", 0)
        # 关键：用内嵌公钥验签，假服务器给不出有效签名即判失败。
        if not verify_capability_token(action, self.machine_id, expire_ts, token):
            return {"ok": False, "reason": "能力令牌签名无效（服务器不可信）"}
        self._cap_cache[action] = (token, int(expire_ts))
        return {"ok": True, "reason": "online"}

    # ──────────────────────── 杂项 ────────────────────────
    def _device_name(self) -> str:
        try:
            import platform
            return platform.node() or ""
        except Exception:
            return ""

    def is_activated(self) -> bool:
        return self.license_data.get("license_key") is not None

    def get_license_info(self) -> dict:
        return self.license_data

    def test_server_connection(self) -> dict:
        try:
            resp = requests.get(f"{SERVER_URL}/", timeout=8)
            if resp.status_code == 200:
                self._server_ok = True
                return {"ok": True, "msg": "服务器连接正常"}
            return {"ok": False, "msg": f"服务器返回异常 (HTTP {resp.status_code})"}
        except requests.exceptions.ConnectionError:
            return {"ok": False, "msg": f"无法连接到 {SERVER_URL}，请检查:\n1. 网络是否正常\n2. 服务器是否运行\n3. 防火墙/安全组是否放行端口"}
        except requests.exceptions.Timeout:
            return {"ok": False, "msg": f"连接超时 ({SERVER_URL})，服务器可能未启动或网络延迟高"}
        except Exception as e:
            return {"ok": False, "msg": f"连接异常: {e}"}
