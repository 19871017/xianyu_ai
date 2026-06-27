"""图片感知去重：dHash + 汉明距离（纯 Pillow 实现，无 numpy/imagehash 依赖）。

为什么需要：
    字节级 MD5 只能挡“完全相同”的图。电商同款图常被换尺寸、重新压缩、
    转码（jpg/webp），导致字节不同但肉眼一致，上架时易被平台判为重复。
    dHash 对缩放/轻微压缩稳定，能挡掉这类“近似重复”。
"""
from __future__ import annotations

import io

try:
    from PIL import Image
except Exception:  # pragma: no cover - 无 Pillow 时降级为不做感知去重
    Image = None


def dhash(image_bytes: bytes, hash_size: int = 8) -> int | None:
    """计算图片 dHash，返回 hash_size*hash_size 位整数；失败返回 None。

    原理：缩放成 (hash_size+1) x hash_size 的灰度图，逐行比较相邻像素亮度，
    左>右记 1 否则记 0。对缩放和轻度压缩不敏感，适合判“同款图”。
    """
    if Image is None or not image_bytes:
        return None
    try:
        img = Image.open(io.BytesIO(image_bytes)).convert("L").resize(
            (hash_size + 1, hash_size), Image.LANCZOS
        )
    except Exception:
        return None
    px = img.load()
    bits = 0
    idx = 0
    for row in range(hash_size):
        for col in range(hash_size):
            bits |= (1 << idx) if px[col, row] > px[col + 1, row] else 0
            idx += 1
    return bits


def hamming(a: int, b: int) -> int:
    """两个哈希的汉明距离（不同比特位数）。"""
    return bin(a ^ b).count("1")


def is_near_duplicate(h: int | None, seen, threshold: int = 5) -> bool:
    """h 与 seen 中任一哈希的汉明距离 <= threshold 则视为近似重复。

    threshold=5：8x8 dHash 共 64 位，距离 0~5 经验上为同图/同款的安全阈值，
    既能挡换尺寸/重压缩，又不会误杀不同图（不同图通常 >20）。
    """
    if h is None:
        return False
    for s in seen:
        if hamming(h, s) <= threshold:
            return True
    return False


def is_valid_product_image(image_bytes: bytes, min_size: int = 200) -> bool:
    """判断是否为有效商品图（排除 SVG 图标/占位图/超小 UI 元素）。

    为什么需要：
        1688 详情页里混着界面用的 SVG 图标（如 15x8、24x24 的箭头/占位符），
        字节数可能超过几百字节绕过"小文件"过滤，但 Pillow 无法解码（SVG 非位图），
        或解码后尺寸极小。这类图当成商品主图上架会污染商品。

    规则：
        - Pillow 不可用时降级为 True（不误杀，交由上层其它过滤）。
        - 无法解码（SVG/损坏）→ False。
        - 任一边小于 min_size（默认 200px）→ False。

    Args:
        image_bytes: 图片原始字节。
        min_size: 最小边长阈值（像素），商品图通常 >=800，取 200 留足余量。

    Returns:
        True 为有效商品位图，False 为应丢弃。
    """
    if Image is None:
        return True
    if not image_bytes or len(image_bytes) < 500:
        return False
    try:
        img = Image.open(io.BytesIO(image_bytes))
        width, height = img.size
    except Exception:
        return False
    if width < min_size or height < min_size:
        return False
    return True
