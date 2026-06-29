"""版本发布 / 公告 / 下载站 / 客户端更新检测 集成测试（TestClient, hermetic）。

覆盖：公开端点(无需鉴权)、外链建版本、上传安装包、文件下载与路径穿越防护、
最新版自动切换、删除版本回退最新标记、公告增删改查与发布过滤、管理端鉴权、
客户端更新检测 latest 接口、下载站首页可访问。
"""
import os
import tempfile
import importlib
import sys
import unittest


def _fresh_app(tmpdir):
    os.environ["XF_ENV"] = "production"
    os.environ["DATABASE_URL"] = f"sqlite:///{tmpdir}/test.db"
    os.environ["JWT_SECRET_KEY"] = "unit-test-secret-DO-NOT-USE"
    os.environ["ADMIN_PASSWORD"] = "testadminpw"
    os.environ["CLIENT_API_KEY"] = "testclientkey"
    os.environ["REQUIRE_CLIENT_KEY"] = "1"
    os.environ["RSA_PRIVATE_KEY_PATH"] = f"{tmpdir}/private_key.pem"
    os.environ["RSA_PUBLIC_KEY_PATH"] = f"{tmpdir}/public_key.pem"
    # downloads 目录隔离到临时目录，避免污染仓库。
    os.environ["XF_BASE_OVERRIDE"] = tmpdir
    for m in list(sys.modules):
        if m.split(".")[0] in {"config", "models", "services", "routers", "utils", "main", "schemas"}:
            del sys.modules[m]
    main = importlib.import_module("main")
    main._bootstrap()
    from starlette.testclient import TestClient
    return TestClient(main.app)


class ReleaseFlowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.client = _fresh_app(cls.tmp)
        r = cls.client.post("/api/auth/login", json={"username": "admin", "password": "testadminpw"})
        cls.token = r.json()["access_token"]
        cls.auth = {"Authorization": f"Bearer {cls.token}"}

    # ── 公开端点（无需鉴权）──
    def test_01_public_empty_initially(self):
        c = self.client
        self.assertEqual(c.get("/api/public/versions").json(), {"mac": None, "win": None})
        self.assertEqual(c.get("/api/public/announcements").json(), [])
        self.assertEqual(c.get("/api/public/latest?platform=mac").json()["latest"], None)

    def test_02_site_index_served(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("闲鱼AI助手", r.text)

    # ── 版本：外链方式 ──
    def test_03_create_version_external_url(self):
        r = self.client.post("/api/admin/version", headers=self.auth, json={
            "platform": "mac", "version": "3.2.0",
            "download_url": "https://example.com/app.dmg",
            "release_notes": "首个版本", "force_update": False,
        })
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["platform"], "mac")
        self.assertTrue(r.json()["is_latest"])
        # 公开 latest 能取到
        self.assertEqual(self.client.get("/api/public/latest?platform=mac").json()["latest"]["version"], "3.2.0")

    def test_04_latest_switches_on_new_version(self):
        self.client.post("/api/admin/version", headers=self.auth, json={
            "platform": "mac", "version": "3.3.0", "download_url": "https://example.com/app2.dmg",
        })
        latest = self.client.get("/api/public/latest?platform=mac").json()["latest"]
        self.assertEqual(latest["version"], "3.3.0")
        # 旧版本不再是 latest
        versions = self.client.get("/api/admin/versions", headers=self.auth).json()
        mac = [v for v in versions if v["platform"] == "mac"]
        latest_flags = [v for v in mac if v["is_latest"]]
        self.assertEqual(len(latest_flags), 1)
        self.assertEqual(latest_flags[0]["version"], "3.3.0")

    # ── 版本：上传安装包 ──
    def test_05_upload_package_and_download(self):
        files = {"file": ("setup.zip", b"PKGDATA123", "application/zip")}
        data = {"platform": "win", "version": "3.2.0", "release_notes": "win首发", "force_update": "false"}
        r = self.client.post("/api/admin/version/upload", headers=self.auth, data=data, files=files)
        self.assertEqual(r.status_code, 200, r.text)
        url = r.json()["download_url"]
        self.assertTrue(url.startswith("/downloads/"))
        # 文件可下载，内容一致
        d = self.client.get(url)
        self.assertEqual(d.status_code, 200)
        self.assertEqual(d.content, b"PKGDATA123")

    def test_06_upload_rejects_bad_ext(self):
        files = {"file": ("evil.sh", b"#!/bin/sh", "text/plain")}
        data = {"platform": "win", "version": "9.9.9"}
        r = self.client.post("/api/admin/version/upload", headers=self.auth, data=data, files=files)
        self.assertEqual(r.status_code, 400)

    def test_07_download_path_traversal_blocked(self):
        r = self.client.get("/downloads/..%2f..%2fconfig.py")
        self.assertEqual(r.status_code, 404)

    # ── 客户端更新检测 ──
    def test_08_client_update_check(self):
        latest = self.client.get("/api/public/latest?platform=win").json()["latest"]
        self.assertEqual(latest["version"], "3.2.0")
        self.assertIn("download_url", latest)

    def test_09_invalid_platform_400(self):
        r = self.client.get("/api/public/latest?platform=linux")
        self.assertEqual(r.status_code, 400)

    # ── 删除版本回退最新标记 ──
    def test_10_delete_version_restores_latest(self):
        versions = self.client.get("/api/admin/versions", headers=self.auth).json()
        mac = sorted([v for v in versions if v["platform"] == "mac"], key=lambda x: x["id"])
        newest = mac[-1]  # 3.3.0
        r = self.client.delete(f"/api/admin/version/{newest['id']}", headers=self.auth)
        self.assertEqual(r.status_code, 200)
        # 删掉最新后，前一版本重新成为 latest
        latest = self.client.get("/api/public/latest?platform=mac").json()["latest"]
        self.assertEqual(latest["version"], "3.2.0")

    # ── 公告 ──
    def test_11_announcement_crud_and_publish_filter(self):
        c, auth = self.client, self.auth
        r = c.post("/api/admin/announcement", headers=auth, json={
            "title": "欢迎", "content": "正式上线", "is_published": True, "pinned": 10,
        })
        self.assertEqual(r.status_code, 200)
        aid = r.json()["id"]
        # 未发布的公告不出现在公开列表
        c.post("/api/admin/announcement", headers=auth, json={
            "title": "草稿", "content": "未发布", "is_published": False,
        })
        pub = c.get("/api/public/announcements").json()
        titles = [a["title"] for a in pub]
        self.assertIn("欢迎", titles)
        self.assertNotIn("草稿", titles)
        # 更新为下架后从公开列表消失
        c.put(f"/api/admin/announcement/{aid}", headers=auth, json={
            "title": "欢迎", "content": "正式上线", "is_published": False, "pinned": 10,
        })
        self.assertNotIn("欢迎", [a["title"] for a in c.get("/api/public/announcements").json()])
        # 删除
        self.assertEqual(c.delete(f"/api/admin/announcement/{aid}", headers=auth).status_code, 200)

    # ── 鉴权 ──
    def test_12_admin_endpoints_require_token(self):
        self.assertEqual(self.client.get("/api/admin/versions").status_code, 401)
        self.assertEqual(self.client.post("/api/admin/version", json={"platform": "mac", "version": "x", "download_url": "u"}).status_code, 401)
        self.assertEqual(self.client.get("/api/admin/announcements").status_code, 401)


if __name__ == "__main__":
    unittest.main(verbosity=2)
