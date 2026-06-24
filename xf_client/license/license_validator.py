import json
import os
import requests
from config import SERVER_URL, LICENSE_FILE
from license.machine_id import get_machine_id


class LicenseValidator:
    def __init__(self):
        self.machine_id = get_machine_id()
        self.license_data = self._load_local()
        self._server_ok = None  # None=未检测, True=可用, False=不可用

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

    def _check_server(self) -> bool:
        """快速检测服务器是否可达（带缓存）"""
        if self._server_ok is not None:
            return self._server_ok
        try:
            resp = requests.get(f"{SERVER_URL}/", timeout=5)
            self._server_ok = (resp.status_code == 200)
            return self._server_ok
        except Exception:
            self._server_ok = False
            return False

    def activate(self, license_key: str) -> dict:
        """远程激活License"""
        try:
            resp = requests.post(
                f"{SERVER_URL}/api/license/activate",
                json={"license_key": license_key, "machine_id": self.machine_id},
                timeout=15,
            )
            if resp.status_code == 200:
                data = resp.json()
                self._save_local(data)
                self.license_data = data
                self._server_ok = True
                return {"success": True, "data": data}
            else:
                try:
                    detail = resp.json().get("detail", "激活失败")
                except Exception:
                    detail = f"HTTP {resp.status_code}"
                return {"success": False, "message": detail}
        except requests.exceptions.ConnectionError as e:
            return {"success": False, "message": f"无法连接服务器 ({SERVER_URL})，请检查网络或服务器是否启动"}
        except requests.exceptions.Timeout:
            return {"success": False, "message": f"服务器响应超时 ({SERVER_URL})"}
        except Exception as e:
            return {"success": False, "message": f"激活失败: {e}"}

    def verify(self) -> dict:
        """验证License有效性（优先远程，回退本地）"""
        if not self.license_data:
            return {"valid": False, "reason": "未激活"}

        # 远程验证（仅当服务器上次检测可用时才尝试）
        if self._check_server():
            try:
                resp = requests.get(
                    f"{SERVER_URL}/api/license/verify",
                    params={
                        "license_key": self.license_data.get("license_key", ""),
                        "machine_id": self.machine_id,
                    },
                    timeout=10,
                )
                if resp.status_code == 200:
                    result = resp.json()
                    if result.get("valid"):
                        return {"valid": True, "expires_at": result.get("expires_at")}
            except Exception:
                pass  # 远程失败，走本地回退

        # 本地回退验证
        license_key = self.license_data.get("license_key", "")
        signature = self.license_data.get("signature", "")
        expires_at = self.license_data.get("expires_at", "")

        if not license_key or not signature or not expires_at:
            return {"valid": False, "reason": "本地License数据不完整"}

        # 时间检查
        from datetime import datetime
        try:
            exp_str = expires_at.replace("Z", "+00:00")
            if datetime.fromisoformat(exp_str) < datetime.utcnow():
                return {"valid": False, "reason": "License已过期"}
        except Exception:
            pass

        # 机器码检查
        if self.license_data.get("machine_id") != self.machine_id:
            return {"valid": False, "reason": "机器码不匹配"}

        return {"valid": True, "expires_at": expires_at}

    def is_activated(self) -> bool:
        return self.license_data.get("license_key") is not None

    def get_license_info(self) -> dict:
        return self.license_data

    def test_server_connection(self) -> dict:
        """测试服务器连接（供UI调用）"""
        try:
            resp = requests.get(f"{SERVER_URL}/", timeout=8)
            if resp.status_code == 200:
                self._server_ok = True
                return {"ok": True, "msg": "服务器连接正常"}
            else:
                return {"ok": False, "msg": f"服务器返回异常 (HTTP {resp.status_code})"}
        except requests.exceptions.ConnectionError:
            return {"ok": False, "msg": f"无法连接到 {SERVER_URL}，请检查:\n1. 网络是否正常\n2. 服务器是否运行\n3. 防火墙/安全组是否放行端口"}
        except requests.exceptions.Timeout:
            return {"ok": False, "msg": f"连接超时 ({SERVER_URL})，服务器可能未启动或网络延迟高"}
        except Exception as e:
            return {"ok": False, "msg": f"连接异常: {e}"}
