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

分发包必须走**加密打包**（核心模块 Cython 编译为原生扩展 .pyd/.so，
杜绝明文源码与验签公钥被替换）：

```bash
python secure_build.py            # 测试 + Cython 加密编译 + 打包（唯一正式出包方式）
python build.py --test            # 仅跑单元测试（不产出分发包）
```

> ⚠️ 严禁用普通 PyInstaller 直接打包（`pyinstaller 闲鱼AI助手.spec`）分发。
> 普通打包会把 engine/license/config 以明文源码塞进包内，验签公钥可被一行
> 替换，导致授权被绕过。普通 `build.py` 的打包路径已封禁，仅保留测试。
> 分发包启动即校验核心模块是否为原生扩展，明文包会 fail-closed 拒用核心功能。

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
