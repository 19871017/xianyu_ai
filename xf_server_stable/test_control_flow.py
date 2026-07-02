"""服务端控制功能集成测试（TestClient，hermetic）。

覆盖：客户端密钥鉴权、伪造管理员token拒绝、设备数限制、
强制下线即时生效、吊销连带下线、防重放、心跳、审计日志、用户禁用。
"""
import os
import tempfile
import time
import importlib
import sys
import unittest


def _fresh_app(tmpdir):
    # 隔离环境：独立DB与keys，固定密钥便于断言
    os.environ["XF_ENV"] = "production"
    os.environ["DATABASE_URL"] = f"sqlite:///{tmpdir}/test.db"
    os.environ["JWT_SECRET_KEY"] = "unit-test-secret-DO-NOT-USE-IN-PROD"
    os.environ["ADMIN_PASSWORD"] = "testadminpw"
    os.environ["CLIENT_API_KEY"] = "testclientkey"
    os.environ["REQUIRE_CLIENT_KEY"] = "1"
    os.environ["RSA_PRIVATE_KEY_PATH"] = f"{tmpdir}/private_key.pem"
    os.environ["RSA_PUBLIC_KEY_PATH"] = f"{tmpdir}/public_key.pem"
    os.environ["OFFLINE_THRESHOLD_SECONDS"] = "180"
    # 清理已加载模块，确保 config 重新读取环境变量
    for m in list(sys.modules):
        if m.split(".")[0] in {"config", "models", "services", "routers", "utils", "main", "schemas"}:
            del sys.modules[m]
    main = importlib.import_module("main")
    main._bootstrap()  # lifespan 不在非 with 模式触发，手动初始化
    from starlette.testclient import TestClient
    return TestClient(main.app)


class ControlFlowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.client = _fresh_app(cls.tmp)
        c = cls.client
        r = c.post("/api/auth/login", json={"username": "admin", "password": "testadminpw"})
        assert r.status_code == 200, r.text
        cls.token = r.json()["access_token"]
        cls.auth = {"Authorization": f"Bearer {cls.token}"}
        cls.ckey = {"X-Client-Key": "testclientkey"}

    def test_01_forged_admin_token_rejected(self):
        from jose import jwt
        forged = jwt.encode(
            {"sub": "admin", "user_id": 1, "is_admin": True, "type": "access"},
            "xf-ai-secret-key-change-in-production-2026", algorithm="HS256",
        )
        r = self.client.get("/api/admin/dashboard", headers={"Authorization": f"Bearer {forged}"})
        self.assertEqual(r.status_code, 401)

    def test_02_admin_no_token_rejected(self):
        self.assertEqual(self.client.get("/api/admin/dashboard").status_code, 401)

    def test_03_client_key_required(self):
        r = self.client.post("/api/license/activate",
                             json={"license_key": "x", "machine_id": "m"})
        self.assertEqual(r.status_code, 401)

    def test_04_issue_activate_verify(self):
        self.client.post("/api/auth/register", json={"username": "u1", "password": "p1"})
        users = self.client.get("/api/admin/users", headers=self.auth).json()
        uid = [u["id"] for u in users if u["username"] == "u1"][0]
        r = self.client.post("/api/admin/license/issue", headers=self.auth,
                             json={"user_id": uid, "days": 30, "max_devices": 2, "note": "t"})
        self.assertEqual(r.status_code, 200, r.text)
        ControlFlowTest.lic = r.json()["license_key"]

        r = self.client.post("/api/license/activate", headers=self.ckey,
                             json={"license_key": self.lic, "machine_id": "MACHINE-A", "device_name": "MacA"})
        self.assertEqual(r.status_code, 200, r.text)
        ts = int(time.time())
        r = self.client.get(f"/api/license/verify?license_key={self.lic}&machine_id=MACHINE-A&ts={ts}",
                            headers=self.ckey)
        self.assertTrue(r.json().get("valid"), r.text)

    def test_04c_capability_token_issued_and_verifiable(self):
        # 方案B：受控动作换取短期签名令牌；用服务端公钥验签，锁定 payload 格式。
        ts = int(time.time())
        r = self.client.post("/api/license/capability", headers=self.ckey,
                             json={"license_key": self.lic, "machine_id": "MACHINE-A",
                                   "action": "collect", "ts": ts})
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertTrue(body.get("ok"), body)
        self.assertGreater(body.get("expire_ts", 0), ts)
        # 用服务端公钥验签：payload 必须是 cap:action:machine_id:expire_ts。
        from utils.rsa_utils import verify_signature
        payload = f"cap:collect:MACHINE-A:{body['expire_ts']}"
        self.assertTrue(verify_signature(payload, body["token"]))
        # 篡改动作后同一令牌验签应失败。
        self.assertFalse(verify_signature(
            f"cap:listing:MACHINE-A:{body['expire_ts']}", body["token"]))

    def test_04d_capability_unknown_action_rejected(self):
        ts = int(time.time())
        r = self.client.post("/api/license/capability", headers=self.ckey,
                             json={"license_key": self.lic, "machine_id": "MACHINE-A",
                                   "action": "hack_everything", "ts": ts})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertFalse(r.json().get("ok"))

    def test_05_device_limit(self):
        self.client.post("/api/license/activate", headers=self.ckey,
                        json={"license_key": self.lic, "machine_id": "MACHINE-B"})
        r = self.client.post("/api/license/activate", headers=self.ckey,
                            json={"license_key": self.lic, "machine_id": "MACHINE-C"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("最大设备数", r.json()["detail"])

    def test_06_force_offline_takes_effect(self):
        devices = self.client.get("/api/admin/devices", headers=self.auth).json()
        dev_a = [d for d in devices if d["machine_id"] == "MACHINE-A"][0]
        r = self.client.post(f"/api/admin/device/{dev_a['id']}/force_offline", headers=self.auth)
        self.assertEqual(r.status_code, 200, r.text)
        ts = int(time.time())
        r = self.client.get(f"/api/license/verify?license_key={self.lic}&machine_id=MACHINE-A&ts={ts}",
                            headers=self.ckey)
        self.assertFalse(r.json().get("valid"))
        self.assertIn("强制下线", r.json()["reason"])
        # heartbeat 也应被拒
        r = self.client.post("/api/license/heartbeat", headers=self.ckey,
                            json={"license_key": self.lic, "machine_id": "MACHINE-A", "ts": ts})
        self.assertEqual(r.json()["action"], "logout")
        # 允许上线后恢复
        self.client.post(f"/api/admin/device/{dev_a['id']}/allow_online", headers=self.auth)
        ts = int(time.time())
        r = self.client.get(f"/api/license/verify?license_key={self.lic}&machine_id=MACHINE-A&ts={ts}",
                            headers=self.ckey)
        self.assertTrue(r.json().get("valid"))

    def test_07_replay_protection(self):
        # 旧时间戳 (超窗口) 应被拒
        old = int(time.time()) - 100000
        r = self.client.get(f"/api/license/verify?license_key={self.lic}&machine_id=MACHINE-A&ts={old}",
                            headers=self.ckey)
        self.assertFalse(r.json().get("valid"))

    def test_08_revoke_cascades(self):
        r = self.client.post(f"/api/admin/license/{self.lic}/revoke", headers=self.auth)
        self.assertEqual(r.status_code, 200, r.text)
        ts = int(time.time())
        r = self.client.get(f"/api/license/verify?license_key={self.lic}&machine_id=MACHINE-A&ts={ts}",
                            headers=self.ckey)
        self.assertFalse(r.json().get("valid"))
        self.assertIn("吊销", r.json()["reason"])

    def test_09_audit_log_written(self):
        rows = self.client.get("/api/admin/audit", headers=self.auth).json()
        actions = {r["action"] for r in rows}
        self.assertTrue({"issue", "activate", "force_offline", "revoke"} <= actions, actions)

    def test_10_user_disable_blocks_verify(self):
        # 新用户+license+设备，禁用用户后 verify 失败
        self.client.post("/api/auth/register", json={"username": "u2", "password": "p2"})
        users = self.client.get("/api/admin/users", headers=self.auth).json()
        uid = [u["id"] for u in users if u["username"] == "u2"][0]
        lic = self.client.post("/api/admin/license/issue", headers=self.auth,
                              json={"user_id": uid, "days": 30}).json()["license_key"]
        self.client.post("/api/license/activate", headers=self.ckey,
                        json={"license_key": lic, "machine_id": "MACHINE-U2"})
        r = self.client.post(f"/api/admin/user/{uid}/toggle", headers=self.auth)
        self.assertFalse(r.json()["is_active"])
        ts = int(time.time())
        r = self.client.get(f"/api/license/verify?license_key={lic}&machine_id=MACHINE-U2&ts={ts}",
                            headers=self.ckey)
        self.assertFalse(r.json().get("valid"))
        self.assertIn("禁用", r.json()["reason"])


if __name__ == "__main__":
    unittest.main(verbosity=2)


class AdminPasswordRotationTest(unittest.TestCase):
    def test_force_reset_rotates_admin_password(self):
        import tempfile, importlib, sys, os
        tmp = tempfile.mkdtemp()
        # 第一次启动：admin 密码 = oldpw
        os.environ.update({
            "XF_ENV": "production",
            "DATABASE_URL": f"sqlite:///{tmp}/t.db",
            "JWT_SECRET_KEY": "k", "ADMIN_PASSWORD": "oldpw",
            "CLIENT_API_KEY": "c",
            "RSA_PRIVATE_KEY_PATH": f"{tmp}/priv.pem",
            "RSA_PUBLIC_KEY_PATH": f"{tmp}/pub.pem",
            "ADMIN_FORCE_RESET": "0",
        })
        for m in list(sys.modules):
            if m.split(".")[0] in {"config","models","services","routers","utils","main","schemas"}:
                del sys.modules[m]
        main = importlib.import_module("main")
        main._bootstrap()
        from starlette.testclient import TestClient
        c = TestClient(main.app)
        self.assertEqual(c.post("/api/auth/login", json={"username":"admin","password":"oldpw"}).status_code, 200)

        # 第二次启动：开启强制重置，新密码 = newpw
        os.environ["ADMIN_PASSWORD"] = "newpw"
        os.environ["ADMIN_FORCE_RESET"] = "1"
        for m in list(sys.modules):
            if m.split(".")[0] in {"config","models","services","routers","utils","main","schemas"}:
                del sys.modules[m]
        main = importlib.import_module("main")
        main._bootstrap()
        c = TestClient(main.app)
        self.assertEqual(c.post("/api/auth/login", json={"username":"admin","password":"newpw"}).status_code, 200)
        self.assertEqual(c.post("/api/auth/login", json={"username":"admin","password":"oldpw"}).status_code, 401)
