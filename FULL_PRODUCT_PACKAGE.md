# 完整商品包采集/导出补丁

本补丁把采集结果统一成“商品搬家/铺货格式”，输出内容参考你上传的拼多多样例：

- `商品信息.xlsx`
- `主图_1.jpg`、`主图_2.jpg` ...
- `详情页_1.jpg`、`详情页_2.jpg` ...
- `SKU规格名_1.jpg`

## 修改内容

1. 新增 `xf_client/engine/product_package.py`
   - 统一字段：标题、货号、属性、类目、品牌、SKU、价格、库存、售后、产地、发货地、包装尺寸等。
   - 导出 Excel 和图片包。

2. 新增 `xf_client/engine/pdd_full_package.py`
   - 从拼多多 raw JSON 和当前页面 DOM 中尽量提取：
     - SKU规格/价格/库存/SKU图
     - 主图
     - 详情图
     - 商品属性
     - 7天无理由等服务信息

3. 修改 `xf_client/engine/pdd_collector.py`
   - PDD 采集结果自动增强为完整商品包。
   - 图片按主图/详情图/SKU图分组下载。

4. 修改 `xf_client/ui/collect_tab.py`
   - 所有平台采集完成后都会统一规范化。
   - 自动导出商品包到 `~/Desktop/电商数据/exports/商品包_时间戳/`。

5. 修改 `xf_client/database/db_manager.py`
   - 兼容从老数据库读取完整商品包字段。

## 使用

```bash
python apply_full_product_package_patch.py
cd xf_client
python -m compileall .
python main.py
```

采集完成后，日志里会显示：

```text
📦 商品包已导出: /Users/xxx/Desktop/电商数据/exports/商品包_2026xxxx_xxxxxx
```

## 注意

- 这个补丁不做验证码绕过、签名逆向或平台风控规避。
- 拼多多详情图/SKU图是否完整，取决于页面当前实际加载出来的数据。
- 闲鱼通常没有多 SKU，会默认生成一条“默认”规格。
- 1688 的阶梯价/SKU可以继续增强，但本补丁已经能导出统一格式。
