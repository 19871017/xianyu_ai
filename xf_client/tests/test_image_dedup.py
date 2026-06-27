"""图片感知去重单测（dHash + 汉明距离，纯 Pillow）。

注意: dHash 基于亮度梯度结构、对颜色不敏感, 故"不同图"用例须用不同的
空间结构(不同图案), 而非仅换颜色。
"""

import io
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.image_dedup import dhash, hamming, is_near_duplicate

try:
    from PIL import Image
    _HAS_PIL = True
except Exception:
    _HAS_PIL = False


def _striped(color, size=(300, 300), step=20, fmt="JPEG", quality=90):
    """竖条纹图：用于测试同结构换尺寸/重压缩仍判重。"""
    img = Image.new("RGB", size, color)
    for x in range(0, size[0], step):
        for y in range(size[1]):
            img.putpixel((x, y), (color[0] // 2, color[1] // 2, color[2] // 2))
    buf = io.BytesIO()
    img.save(buf, format=fmt, quality=quality)
    return buf.getvalue()


def _gradient(size=(300, 300)):
    """对角渐变图：与条纹图结构完全不同。"""
    img = Image.new("RGB", size)
    for x in range(size[0]):
        for y in range(size[1]):
            v = (x + y) % 256
            img.putpixel((x, y), (v, v, v))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


@unittest.skipUnless(_HAS_PIL, "需要 Pillow")
class TestImageDedup(unittest.TestCase):
    def test_same_image_resized_is_near_dup(self):
        a = _striped((180, 60, 200), size=(300, 300))
        b = _striped((180, 60, 200), size=(150, 150))  # 换尺寸
        ha, hb = dhash(a), dhash(b)
        self.assertIsNotNone(ha)
        self.assertLessEqual(hamming(ha, hb), 5)
        self.assertTrue(is_near_duplicate(hb, [ha]))

    def test_same_image_recompressed_is_near_dup(self):
        a = _striped((40, 160, 90), quality=95)
        b = _striped((40, 160, 90), quality=30)  # 重压缩
        self.assertTrue(is_near_duplicate(dhash(b), [dhash(a)]))

    def test_different_structure_not_dup(self):
        a = _striped((120, 120, 120))   # 条纹
        b = _gradient()                  # 渐变
        self.assertGreater(hamming(dhash(a), dhash(b)), 5)
        self.assertFalse(is_near_duplicate(dhash(b), [dhash(a)]))

    def test_none_hash_not_dup(self):
        self.assertFalse(is_near_duplicate(None, [123]))
        self.assertIsNone(dhash(b""))

    def test_hamming_basic(self):
        self.assertEqual(hamming(0b1010, 0b1000), 1)
        self.assertEqual(hamming(0, 0), 0)


@unittest.skipUnless(_HAS_PIL, "需要 Pillow")
class TestValidProductImage(unittest.TestCase):
    def test_valid_large_image(self):
        from utils.image_dedup import is_valid_product_image
        data = _striped((180, 60, 200), size=(800, 800))
        self.assertTrue(is_valid_product_image(data))

    def test_svg_icon_rejected(self):
        from utils.image_dedup import is_valid_product_image
        svg = b'<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24"></svg>'
        self.assertFalse(is_valid_product_image(svg))

    def test_tiny_image_rejected(self):
        from utils.image_dedup import is_valid_product_image
        data = _striped((120, 120, 120), size=(32, 32))
        self.assertFalse(is_valid_product_image(data))

    def test_empty_bytes_rejected(self):
        from utils.image_dedup import is_valid_product_image
        self.assertFalse(is_valid_product_image(b""))


if __name__ == "__main__":
    unittest.main(verbosity=2)
