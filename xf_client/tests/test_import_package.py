"""商品包导入（import_product_package）单元测试。

构造临时商品包目录（含 商品信息.xlsx + 图片），验证 export 的逆操作：
多规格、价格、库存、属性、SKU 图匹配，以及两种目录布局（扁平 / 子目录）。
"""

import os
import sys
import shutil
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from openpyxl import Workbook

from engine.product_package import (
    EXPORT_HEADERS,
    import_product_package,
    _parse_attr_text,
    _norm_header,
)


def _png_bytes() -> bytes:
    # 1x1 透明 PNG，足够当作有效图片文件占位。
    import base64
    return base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
    )


def _write_img(path: str):
    with open(path, "wb") as f:
        f.write(_png_bytes())


def _build_xlsx(path: str, rows: list[list]):
    wb = Workbook()
    ws = wb.active
    ws.title = "商品信息"
    ws.append(EXPORT_HEADERS)
    for row in rows:
        ws.append(row)
    wb.save(path)


def _row(title="", attr="", spec1="", spec2="", price="", stock="",
         origin="", ship_from=""):
    # 按 EXPORT_HEADERS 顺序构造一行，缺省补空。
    full = [""] * len(EXPORT_HEADERS)
    idx = {name.lstrip("*＊"): i for i, name in enumerate(EXPORT_HEADERS)}
    full[idx["标题"]] = title
    full[idx["商品属性"]] = attr
    full[idx["规格1"]] = spec1
    full[idx["规格2"]] = spec2
    full[idx["价格"]] = price
    full[idx["库存"]] = stock
    full[idx["产地"]] = origin
    full[idx["发货地"]] = ship_from
    return full


class TestParseAttrText(unittest.TestCase):
    def test_basic(self):
        attrs = _parse_attr_text("材质:聚丙烯；风格:中式；")
        self.assertEqual(attrs["材质"], "聚丙烯")
        self.assertEqual(attrs["风格"], "中式")

    def test_chinese_colon_and_empty(self):
        attrs = _parse_attr_text("产地：中国大陆;")
        self.assertEqual(attrs["产地"], "中国大陆")

    def test_empty(self):
        self.assertEqual(_parse_attr_text(""), {})
        self.assertEqual(_parse_attr_text(None), {})


class TestNormHeader(unittest.TestCase):
    def test_strips_star_and_spaces(self):
        self.assertEqual(_norm_header("*标题"), "标题")
        self.assertEqual(_norm_header(" 价格 "), "价格")
        self.assertEqual(_norm_header("商家SKU"), "商家sku")


class TestImportSubdirLayout(unittest.TestCase):
    """子目录布局：主图/ 详情图/ SKU图/。"""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="pkg_sub_")
        os.makedirs(os.path.join(self.dir, "主图"))
        os.makedirs(os.path.join(self.dir, "详情图"))
        os.makedirs(os.path.join(self.dir, "SKU图"))
        for i in (1, 2, 3):
            _write_img(os.path.join(self.dir, "主图", f"主图_{i}.jpeg"))
        for i in (1, 2):
            _write_img(os.path.join(self.dir, "详情图", f"详情页_{i}.jpeg"))
        _write_img(os.path.join(self.dir, "SKU图", "【红色】2个装_1.jpeg"))
        _write_img(os.path.join(self.dir, "SKU图", "【红色】1个装_1.jpeg"))
        _build_xlsx(os.path.join(self.dir, "商品信息.xlsx"), [
            _row(title="福字磁吸贴马年冰箱贴摆件", attr="材质:聚丙烯；产地:中国大陆；",
                 spec1="【红色】2个装", price=15.6, stock=1000,
                 origin="中国大陆", ship_from="浙江省"),
            _row(spec1="【红色】1个装", price=9.89, stock=500),
        ])

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_title_and_attrs_inherited(self):
        item = import_product_package(self.dir)
        self.assertEqual(item["title"], "福字磁吸贴马年冰箱贴摆件")
        self.assertEqual(item["origin"], "中国大陆")
        self.assertEqual(item["ship_from"], "浙江省")
        self.assertEqual(item["attributes"]["材质"], "聚丙烯")

    def test_multi_spec_prices(self):
        item = import_product_package(self.dir)
        skus = item["sku_list"]
        self.assertEqual(len(skus), 2)
        self.assertEqual(skus[0]["spec1"], "【红色】2个装")
        self.assertEqual(skus[0]["price"], 15.6)
        self.assertEqual(skus[0]["stock"], 1000)
        self.assertEqual(skus[1]["spec1"], "【红色】1个装")
        self.assertEqual(skus[1]["price"], 9.89)
        self.assertEqual(skus[1]["stock"], 500)

    def test_lowest_price(self):
        item = import_product_package(self.dir)
        self.assertEqual(item["price"], 9.89)

    def test_images_collected(self):
        item = import_product_package(self.dir)
        self.assertEqual(len(item["main_images"]), 3)
        self.assertEqual(len(item["detail_images"]), 2)

    def test_sku_image_matched(self):
        item = import_product_package(self.dir)
        for sku in item["sku_list"]:
            self.assertTrue(sku.get("sku_image"), f"{sku['spec1']} 应匹配到 SKU 图")
            self.assertTrue(os.path.exists(sku["sku_image"]))

    def test_category_keyword_not_garbage(self):
        item = import_product_package(self.dir)
        # 摆件 属于家居装饰类品类词，应命中而非取标题末尾乱词
        self.assertEqual(item["category_keyword"], "摆件")


class TestImportFlatLayout(unittest.TestCase):
    """扁平布局：图片直接在根目录。"""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="pkg_flat_")
        for i in (1, 2):
            _write_img(os.path.join(self.dir, f"主图_{i}.jpeg"))
        _write_img(os.path.join(self.dir, "详情页_1.jpeg"))
        _write_img(os.path.join(self.dir, "蓝色_1.jpeg"))
        _build_xlsx(os.path.join(self.dir, "商品信息.xlsx"), [
            _row(title="纯色卫衣", spec1="蓝色", price=49, stock=200),
        ])

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_flat_images_and_sku(self):
        item = import_product_package(self.dir)
        self.assertEqual(item["title"], "纯色卫衣")
        self.assertEqual(len(item["main_images"]), 2)
        self.assertEqual(len(item["detail_images"]), 1)
        self.assertEqual(len(item["sku_list"]), 1)
        self.assertTrue(item["sku_list"][0]["sku_image"])
        self.assertEqual(item["category_keyword"], "卫衣")


class TestImportErrors(unittest.TestCase):
    def test_missing_dir(self):
        with self.assertRaises(FileNotFoundError):
            import_product_package("/no/such/dir/xyz")

    def test_no_xlsx(self):
        d = tempfile.mkdtemp(prefix="pkg_empty_")
        try:
            with self.assertRaises(FileNotFoundError):
                import_product_package(d)
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
