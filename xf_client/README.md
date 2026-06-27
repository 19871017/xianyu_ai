# 闲鱼AI助手

## 功能
- 🔍 关键词搜索采集闲鱼商品
- 🏠 主页链接采集
- ✍️ AI文案优化改写（DeepSeek/OpenAI）
- 📦 批量上架闲鱼
- 💰 批量降价管理
- 📊 Excel导出
- 🔑 License授权管理

## 安装

```bash
pip install -r requirements.txt
```

## 运行

```bash
python main.py
```

## 测试 + 打包

一键流程（先跑全套单元测试，通过后才打包；测试失败自动中止）：

```bash
python build.py            # 测试 + 打包
python build.py --test     # 只跑测试
python build.py --no-test  # 跳过测试直接打包（不推荐）
```

平台产物：
- macOS: `dist/闲鱼AI助手.app` 与 `dist/闲鱼AI助手/`
- Windows: 运行 `build_windows.bat`，产物在 `dist/闲鱼AI助手/闲鱼AI助手.exe`

单独运行测试：

```bash
python -m unittest discover -s tests -v
```

## 采集筛选

采集页支持按以下条件过滤与排序采集结果：
- 价格区间（最低价 / 最高价）
- 最低销量、最低想要/热度、最低浏览量
- 排序字段（价格 / 销量 / 想要 / 浏览量）与升降序

## 配置

- 服务器地址在 `config.py` 中修改
- AI API Key 在设置页面配置
- License Key 在设置页面激活
