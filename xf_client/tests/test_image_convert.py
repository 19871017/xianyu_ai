"""图片转码 回归测试：闲鱼只接受 jpg/jpeg/png，webp/heic 须转码后再上传。

覆盖 to_uploadable_image 纯逻辑：安全格式原样返回、webp 转 jpg、缺文件返回
None。用临时图片隔离，不依赖网络/浏览器。
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.xianyu_lister import to_uploadable_image, XIANYU_UPLOAD_EXTS


class TestToUploadableImage(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _make_img(self, name, fmt):
        from PIL import Image
        p = os.path.join(self.tmpdir, name)
        Image.new("RGB", (64, 64), (200, 100, 50)).save(p, fmt)
        return p

    def test_jpg_returned_as_is(self):
        p = self._make_img("a.jpg", "JPEG")
        self.assertEqual(to_uploadable_image(p), p)

    def test_png_returned_as_is(self):
        p = self._make_img("a.png", "PNG")
        self.assertEqual(to_uploadable_image(p), p)

    def test_webp_converted_to_jpg(self):
        p = self._make_img("a.webp", "WEBP")
        out = to_uploadable_image(p)
        self.assertIsNotNone(out)
        self.assertTrue(out.lower().endswith(".jpg"))
        self.assertTrue(os.path.isfile(out))
        # 转出的文件可被 Pillow 当 JPEG 读回。
        from PIL import Image
        with Image.open(out) as img:
            self.assertEqual(img.format, "JPEG")

    def test_missing_file_returns_none(self):
        self.assertIsNone(to_uploadable_image(os.path.join(self.tmpdir, "nope.webp")))
        self.assertIsNone(to_uploadable_image(""))

    def test_safe_exts_constant(self):
        self.assertIn(".jpg", XIANYU_UPLOAD_EXTS)
        self.assertNotIn(".webp", XIANYU_UPLOAD_EXTS)


if __name__ == "__main__":
    unittest.main(verbosity=2)
