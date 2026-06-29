"""商品持久化 回归测试：根治「调价/改文案保存后被旧 package 覆盖回退」。

SKU 等数据存于 attributes 内的 _full_product_package blob，读取时会用
package 覆盖顶层字段。若 save_product 不按顶层最新值重建 package，调价后
重新加载就会回退到旧价。本测试用临时 DB 隔离，验证修复持续生效。
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestProductPersist(unittest.TestCase):
    def setUp(self):
        # 用独立临时 DB，避免污染真实库。
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        import database.db_manager as dbm
        self.dbm = dbm
        self._orig_path = dbm.DB_PATH
        dbm.DB_PATH = self.tmp.name
        self.db = dbm.DatabaseManager()

    def tearDown(self):
        self.dbm.DB_PATH = self._orig_path
        try:
            os.unlink(self.tmp.name)
        except Exception:
            pass

    def _make_item(self):
        return {
            "item_id": "test_persist_1",
            "platform": "1688",
            "title": "测试商品",
            "original_title": "测试商品",
            "original_price": "9.9",
            "new_price": "9.9",
            "source_url": "https://detail.1688.com/offer/1.html",
            "sku_list": [
                {"spec1": "红", "spec2": "S", "price": 9.9, "stock": 10},
                {"spec1": "蓝", "spec2": "M", "price": 19.8, "stock": 5},
            ],
            "status": "collected",
        }

    def _prices(self, item):
        return sorted({float(s.get("price")) for s in item.get("sku_list") or []})

    def test_price_edit_persists(self):
        item = self._make_item()
        self.db.save_product(item)

        loaded = self.db.get_all_products()[0]
        self.assertEqual(self._prices(loaded), [9.9, 19.8])

        # 调价：把 SKU 价抬高并改顶层售价，保存后重载不应回退。
        for s in loaded["sku_list"]:
            s["price"] = round(float(s["price"]) * 2, 2)
        loaded["new_price"] = "19.80"
        self.db.save_product(loaded)

        reloaded = self.db.get_all_products()[0]
        self.assertEqual(self._prices(reloaded), [19.8, 39.6])
        self.assertEqual(str(reloaded.get("new_price")), "19.80")

    def test_title_edit_persists(self):
        item = self._make_item()
        self.db.save_product(item)
        loaded = self.db.get_all_products()[0]
        loaded["title"] = "改过的标题"
        loaded["ai_title"] = "改过的标题"
        self.db.save_product(loaded)
        reloaded = self.db.get_all_products()[0]
        self.assertEqual(reloaded.get("title"), "改过的标题")

    def test_status_only_save_keeps_skus(self):
        # 仅改状态保存（不带新 sku_list），SKU 不应丢失。
        item = self._make_item()
        self.db.save_product(item)
        loaded = self.db.get_all_products()[0]
        self.db.update_product_status(loaded["db_id"], "listed_xianyu")
        reloaded = self.db.get_all_products()[0]
        self.assertEqual(self._prices(reloaded), [9.9, 19.8])
        self.assertEqual(reloaded.get("status"), "listed_xianyu")


    def test_edit_dialog_flow_persists(self):
        # 端到端：模拟编辑弹窗提交路径 apply_product_edits -> save_product -> 重载。
        from engine.product_package import apply_product_edits
        item = self._make_item()
        self.db.save_product(item)
        loaded = self.db.get_all_products()[0]

        edits = {
            "title": "旗舰耳机 黑白双色",
            "description": "全新正品，多规格可选。",
            "new_price": "29.9",
            "sku_edits": [{"index": 0, "price": "21.5", "stock": "7"}],
        }
        edited = apply_product_edits(loaded, edits)
        for key in ("db_id", "item_id", "status", "platform"):
            if key not in edited and key in loaded:
                edited[key] = loaded[key]
        self.db.save_product(edited)

        reloaded = self.db.get_all_products()[0]
        self.assertEqual(reloaded.get("title"), "旗舰耳机 黑白双色")
        self.assertEqual(reloaded.get("description"), "全新正品，多规格可选。")
        self.assertEqual(str(reloaded.get("new_price")), "29.9")
        prices = {round(float(s["price"]), 2) for s in reloaded["sku_list"]}
        self.assertIn(21.5, prices)
        sku0 = next(s for s in reloaded["sku_list"] if round(float(s["price"]), 2) == 21.5)
        self.assertEqual(int(sku0.get("stock")), 7)


if __name__ == "__main__":
    unittest.main(verbosity=2)
