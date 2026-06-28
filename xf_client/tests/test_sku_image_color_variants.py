"""SKU 规格图色变体保留回归测试。

背景（根因）：
    1688/淘宝同款不同色的 SKU 规格图，结构相同仅配色不同。dHash 先转灰度
    计算，颜色信息丢失，5 个颜色变体的 dHash 汉明距离≈0，会被感知去重误判为
    重复，导致只保存第一张 → 闲鱼按规格值配图时其余颜色缺图。

    修复：SKU 图下载传 skip_perceptual=True，只做 MD5 字节级去重（挡完全相同
    的图），保留所有颜色变体。

本测试用 requests.get 打桩，离线验证：
    - skip_perceptual=True ：5 个色变体全部保存（修复后行为）。
    - skip_perceptual=False：色变体被感知去重，仅存 1 张（旧 bug 行为）。
"""

import io
import os
import sys
import shutil
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from PIL import Image
    _HAS_PIL = True
except Exception:
    _HAS_PIL = False


def _striped_color(color, size=(320, 320), step=20):
    """同结构（竖条纹）仅换主色：dHash 几乎一致、字节(MD5)各不同。"""
    img = Image.new("RGB", size, color)
    for x in range(0, size[0], step):
        for y in range(size[1]):
            img.putpixel((x, y), (color[0] // 2, color[1] // 2, color[2] // 2))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


_COLORS = [
    (200, 40, 40),    # 红
    (40, 160, 60),    # 绿
    (40, 80, 200),    # 蓝
    (210, 190, 40),   # 黄
    (150, 60, 190),   # 紫
]


class _FakeResp:
    def __init__(self, content):
        self.status_code = 200
        self.content = content


@unittest.skipUnless(_HAS_PIL, "需要 Pillow")
class TestSkuImageColorVariants(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sku_color_")
        # 5 个色变体的字节（结构同、颜色异）
        self.payloads = [_striped_color(c) for c in _COLORS]

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run_for(self, collector_module, collector_cls, skip_perceptual):
        # 打桩 requests.get：按调用顺序返回各色字节
        seq = list(self.payloads)
        calls = {"i": 0}

        def fake_get(url, *a, **k):
            content = seq[calls["i"] % len(seq)]
            calls["i"] += 1
            return _FakeResp(content)

        orig_get = collector_module.requests.get
        collector_module.requests.get = fake_get
        try:
            col = collector_cls()
            md5_pool = set()
            dhash_pool = []
            saved = []
            for idx in range(len(self.payloads)):
                res = col._download_and_dedup_image(
                    f"https://example.com/sku_{idx}.jpg", self.tmp, idx,
                    md5_pool=md5_pool, dhash_pool=dhash_pool,
                    skip_perceptual=skip_perceptual,
                )
                if res:
                    saved.append(res["path"])
            return saved
        finally:
            collector_module.requests.get = orig_get

    def test_alibaba_keeps_all_color_variants(self):
        from engine import alibaba_collector
        saved = self._run_for(alibaba_collector, alibaba_collector.AlibabaCollector, True)
        self.assertEqual(len(saved), 5, "skip_perceptual=True 应保留全部 5 个颜色变体")

    def test_alibaba_old_behavior_drops_variants(self):
        from engine import alibaba_collector
        saved = self._run_for(alibaba_collector, alibaba_collector.AlibabaCollector, False)
        self.assertLess(len(saved), 5, "感知去重(旧行为)会误删颜色变体")

    def test_taobao_keeps_all_color_variants(self):
        from engine import taobao_collector
        saved = self._run_for(taobao_collector, taobao_collector.TaobaoCollector, True)
        self.assertEqual(len(saved), 5, "skip_perceptual=True 应保留全部 5 个颜色变体")


if __name__ == "__main__":
    unittest.main(verbosity=2)
