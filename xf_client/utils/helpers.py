import os
import re
import json
from datetime import datetime


def ensure_dir(path: str):
    os.makedirs(path, exist_ok=True)


def sanitize_filename(name: str) -> str:
    """清理文件名中的非法字符"""
    return re.sub(r'[\\/:*?"<>|]', '_', name)[:100]


def format_price(price_str: str) -> str:
    """标准化价格格式"""
    if not price_str:
        return "0"
    price_str = re.sub(r'[^\d.]', '', str(price_str))
    try:
        return f"{float(price_str):.2f}"
    except ValueError:
        return "0"


def timestamp_str() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")
