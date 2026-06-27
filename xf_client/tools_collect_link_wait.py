"""用 collect_by_link 采集单链接（含验证码人工等待 + 图片去重）。
用法: python tools_collect_link_wait.py "1688链接"
结果 SKU 写入 dump_1688_skus.json。
"""
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from engine.alibaba_collector import AlibabaCollector

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "dump_1688_skus.json")


def main():
    if len(sys.argv) < 2:
        print("用法: python tools_collect_link_wait.py \"1688链接\""); return
    url = sys.argv[1]
    c = AlibabaCollector(on_progress=lambda m: print("  ", m, flush=True))
    items = c.collect_by_link(url)
    if not items:
        print(">> 未采到商品（可能验证码超时或页面异常）"); return
    it = items[0]
    skus = it.get("sku_list") or []
    print(f"\n>> 标题: {it.get('title','')[:60]}")
    print(f">> 主图(去重后): {len(it.get('local_images') or [])} 张")
    print(f">> SKU 数量: {len(skus)}")
    dims = set()
    for s in skus:
        dims.update((s.get('sku_attrs') or {}).keys())
    print(f">> SKU 维度: {sorted(d for d in dims if d)}")
    for s in skus[:12]:
        print(f"   - {s.get('spec1')} / {s.get('spec2')}  ¥{s.get('price')}  库存{s.get('stock')}")
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(skus, f, ensure_ascii=False, indent=2)
    print(f">> SKU 明细已存: {OUT}")


if __name__ == "__main__":
    main()
