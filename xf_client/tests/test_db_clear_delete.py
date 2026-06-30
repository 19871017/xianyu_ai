"""数据删除/清空与本地目录级联删除的安全性测试。

重点验证：
- 仅删除“本软件托管”的 images/{item_id} 目录；
- 不触碰用户自行导入的素材目录（路径中不含 images 归档层）。
"""
import os
import sys
import json
import shutil
import sqlite3
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database.db_manager as dbm


class DBClearDeleteTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp, "data.db")
        self._orig_db_path = dbm.DB_PATH
        dbm.DB_PATH = self.db_path
        self.db = dbm.DatabaseManager()

    def tearDown(self):
        dbm.DB_PATH = self._orig_db_path
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _managed_item(self, item_id):
        """构造一个带托管图片目录的商品（images/{item_id}/...）。"""
        item_dir = os.path.join(self.tmp, "电商数据", "images", item_id)
        os.makedirs(item_dir, exist_ok=True)
        img = os.path.join(item_dir, "img_000.webp")
        with open(img, "wb") as f:
            f.write(b"x")
        return {
            "item_id": item_id,
            "platform": "1688",
            "original_title": "t",
            "local_images": [img],
        }, item_dir

    def test_iter_dirs_skips_import_sources(self):
        # 用户导入目录（不含 images 归档层）不应被纳入删除集合
        product = {
            "item_id": "imp1",
            "local_images": ["/Users/x/Downloads/902106 2/a.jpg"],
            "image_dir": "/Users/x/Downloads/902106 2",
        }
        dirs = dbm._iter_local_image_dirs(product)
        self.assertEqual(dirs, set())

    def test_iter_dirs_picks_managed(self):
        product, item_dir = self._managed_item("1688_123")
        dirs = dbm._iter_local_image_dirs(product)
        self.assertIn(os.path.normpath(item_dir), {os.path.normpath(d) for d in dirs})

    def test_iter_dirs_never_returns_images_root(self):
        # 直接传 images 根（无商品子目录）不应被纳入，避免清空整个 images
        product = {"item_id": "x", "image_dir": os.path.join(self.tmp, "电商数据", "images")}
        dirs = dbm._iter_local_image_dirs(product)
        self.assertEqual(dirs, set())

    def test_delete_product_removes_local(self):
        product, item_dir = self._managed_item("1688_del")
        pid = self.db.save_product(product)
        self.assertTrue(os.path.isdir(item_dir))
        removed = self.db.delete_product(pid, remove_local=True)
        self.assertEqual(removed, 1)
        self.assertFalse(os.path.isdir(item_dir))
        self.assertIsNone(self.db.get_product_by_id(pid))

    def test_delete_product_keeps_local_when_flag_off(self):
        product, item_dir = self._managed_item("1688_keep")
        pid = self.db.save_product(product)
        removed = self.db.delete_product(pid, remove_local=False)
        self.assertEqual(removed, 0)
        self.assertTrue(os.path.isdir(item_dir))

    def test_clear_products_removes_all_and_local(self):
        p1, d1 = self._managed_item("1688_a")
        p2, d2 = self._managed_item("1688_b")
        self.db.save_product(p1)
        self.db.save_product(p2)
        res = self.db.clear_products(remove_local=True)
        self.assertEqual(res["products"], 2)
        self.assertEqual(res["local_dirs"], 2)
        self.assertFalse(os.path.isdir(d1))
        self.assertFalse(os.path.isdir(d2))
        self.assertEqual(len(self.db.get_all_products()), 0)

    def test_delete_products_bulk(self):
        ids = []
        dirs = []
        for i in range(3):
            p, d = self._managed_item(f"1688_{i}")
            ids.append(self.db.save_product(p))
            dirs.append(d)
        res = self.db.delete_products(ids[:2], remove_local=True)
        self.assertEqual(res["products"], 2)
        self.assertEqual(res["local_dirs"], 2)
        self.assertFalse(os.path.isdir(dirs[0]))
        self.assertFalse(os.path.isdir(dirs[1]))
        self.assertTrue(os.path.isdir(dirs[2]))

    def test_data_counts(self):
        p, _ = self._managed_item("1688_c")
        self.db.save_product(p)
        counts = self.db.data_counts()
        self.assertEqual(counts["products"], 1)
        self.assertIn("orders", counts)
        self.assertIn("scheduled_tasks", counts)

    def test_clear_orders_and_records(self):
        self.db.save_order({"product_id": None, "platform": "xianyu", "buyer_name": "b"})
        self.assertEqual(self.db.clear_orders(), 1)
        self.assertEqual(len(self.db.get_all_orders()), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
