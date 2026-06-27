class PriceManager:
    """批量价格管理 - 支持降价和加价"""

    def __init__(self):
        self.items = []

    def set_items(self, items: list):
        self.items = items

    def batch_reduce_price(self, items: list, reduce_percent: float = 10) -> list:
        """批量降价（百分比）"""
        results = []
        for item in items:
            try:
                original_price = self._parse_price(item)
                new_price = original_price * (1 - reduce_percent / 100)
                new_price = max(0.01, new_price)
                item["new_price"] = f"{new_price:.2f}"
                results.append(item)
            except (ValueError, TypeError):
                item["new_price"] = item.get("original_price", "0")
                results.append(item)
        return results

    def batch_markup_price(self, items: list, markup_percent: float = 10) -> list:
        """批量加价（百分比）"""
        results = []
        for item in items:
            try:
                original_price = self._parse_price(item)
                new_price = original_price * (1 + markup_percent / 100)
                item["new_price"] = f"{new_price:.2f}"
                results.append(item)
            except (ValueError, TypeError):
                item["new_price"] = item.get("original_price", "0")
                results.append(item)
        return results

    def batch_set_price(self, items: list, price: float) -> list:
        """批量统一设价"""
        for item in items:
            item["new_price"] = f"{price:.2f}"
        return items

    def batch_reduce_fixed(self, items: list, reduce_amount: float) -> list:
        """批量固定金额降价"""
        results = []
        for item in items:
            try:
                original_price = self._parse_price(item)
                new_price = max(0.01, original_price - reduce_amount)
                item["new_price"] = f"{new_price:.2f}"
                results.append(item)
            except (ValueError, TypeError):
                item["new_price"] = item.get("original_price", "0")
                results.append(item)
        return results

    def _parse_price(self, item: dict) -> float:
        """从item中解析价格"""
        # 优先用 price 字段（float），fallback到 original_price（str）
        if item.get("price"):
            return float(item["price"])
        price_str = item.get("original_price", "0").replace("¥", "").replace("￥", "").replace(",", "").replace("，", "").strip()
        return float(price_str)

    def batch_adjust(self, items: list, mode: str, value: float, on_progress=None) -> list:
        """统一调价入口：按 mode 分发到具体策略。

        mode:
          - markup_pct    加价百分比
          - markdown_pct  降价百分比
          - set_price     统一设价
          - fixed_reduce  固定金额降价
        """
        if mode == "markup_pct":
            result = self.batch_markup_price(items, value)
        elif mode == "markdown_pct":
            result = self.batch_reduce_price(items, value)
        elif mode == "set_price":
            result = self.batch_set_price(items, value)
        elif mode == "fixed_reduce":
            result = self.batch_reduce_fixed(items, value)
        else:
            result = items
        if on_progress:
            on_progress(f"已对 {len(result)} 个商品完成调价（{mode}={value}）")
        return result
