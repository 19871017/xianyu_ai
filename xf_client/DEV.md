# 闲鱼AI助手 - 开发说明文档

## 项目概述

闲鱼AI助手是一个桌面客户端工具，用于闲鱼商品的**采集→AI文案改写→批量上架→数据导出**全链路操作。基于 PyQt6 GUI 框架开发，通过 DrissionPage 驱动 Chrome 浏览器进行闲鱼网页自动化操作，集成 OpenAI 兼容格式的 AI API 实现文案自动优化。

## 技术栈

| 层级 | 技术 | 说明 |
|------|------|------|
| GUI框架 | PyQt6 ≥ 6.6 | 跨平台桌面界面 |
| 浏览器自动化 | DrissionPage ≥ 4.0 | 基于 CDP 的 Chrome 控制 |
| AI接口 | aiohttp + OpenAI兼容格式 | 支持 DeepSeek/OneAPI/NewAPI 等中转 |
| 数据导出 | openpyxl ≥ 3.1 | Excel .xlsx 格式 |
| HTTP请求 | requests ≥ 2.31 | License验证/服务器通信 |
| 授权系统 | RSA-2048 + JWT + 机器码 | 远程激活+本地离线验证 |
| 服务端 | FastAPI + SQLite | 部署在 38.71.117.111:8000 |

## 目录结构

```
xf_client/
├── main.py                      # 程序入口，加载环境变量、启动Qt应用
├── config.py                    # 全局配置：服务器地址、AI API、路径
├── requirements.txt             # Python依赖清单
├── run_windows.bat              # Windows直接运行脚本（无需打包exe）
├── build_windows.bat            # Windows PyInstaller打包脚本
├── 闲鱼AI助手.spec              # PyInstaller spec文件（macOS用）
│
├── engine/                      # 核心业务引擎
│   ├── __init__.py
│   ├── xianyu_collector.py      # 采集器（495行）- 搜索/详情页采集/图片下载
│   ├── ai_writer.py             # AI文案改写（109行）- OpenAI兼容格式
│   ├── xianyu_lister.py         # 上架器（104行）- 自动填写发布表单
│   ├── price_manager.py         # 价格管理（65行）- 批量加价/降价/设价
│   ├── image_downloader.py      # 异步图片下载（81行）- aiohttp并发下载
│   └── excel_exporter.py        # Excel导出（90行）- 带样式格式化
│
├── ui/                          # 界面层（PyQt6）
│   ├── __init__.py
│   ├── main_window.py           # 主窗口（107行）- Tab管理/状态栏/License门控
│   ├── collect_tab.py           # 采集Tab（189行）- 关键词/主页两种模式
│   ├── copywriting_tab.py       # 文案优化Tab（123行）- AI批量改写
│   ├── listing_tab.py           # 上架Tab（236行）- 价格管理+批量上架
│   ├── export_tab.py            # 导出Tab（68行）- Excel导出
│   └── settings_tab.py          # 设置Tab（262行）- License/AI配置/服务器测试
│
├── license/                     # 授权系统
│   ├── __init__.py
│   ├── machine_id.py            # 机器码生成（44行）- macOS/Windows跨平台
│   └── license_validator.py     # License验证（133行）- 远程激活+本地回退
│
└── utils/                       # 工具函数
    ├── __init__.py
    └── helpers.py               # 通用工具（28行）- 文件名清理/价格格式化
```

**代码统计**：24个Python文件，约2000行业务代码（不含依赖库）。

## 核心模块详解

### 1. 采集器 `engine/xianyu_collector.py`

**职责**：闲鱼商品数据采集，包括搜索列表、详情页解析、图片下载。

**工作流程**：
1. DrissionPage 打开 Chrome → 访问 `goofish.com/search?q=关键词`
2. 滚动页面加载更多商品 → 提取商品链接（`/item?id=xxx`）
3. 逐个访问商品详情页 → JavaScript提取标题/价格/描述/属性/图片
4. 图片URL清洗 → 下载 → MD5去重

**关键技术点**：

- **CSS选择器适配**：闲鱼页面class名带hash后缀（如`item-main-window-list-item--gXUlMEkj`），用`[class*="xxx"]`模糊匹配
- **图片URL清洗** `_clean_image_url()`：
  - 原始URL格式：`https://img.alicdn.com/bao/uploaded/xxx.heic_220x10000Q90.jpg_.webp`
  - 清洗后：`https://img.alicdn.com/bao/uploaded/xxx.heic_960x960.jpg`（高清+兼容格式）
  - 正则：`re.sub(r'_\d+x\d+.*$', '', url)` 去掉缩略图后缀
- **图片选择器**：`.ant-image-img`（高清大图区）+ `[class*="carouselItem"] img`（轮播图），只匹配`bao/uploaded`路径
- **MD5去重**：全局`seen_img_md5`集合，跨商品去重

**主要方法**：
```python
collector = XianyuCollector(on_progress=callback)
items = collector.search_by_keyword("iPhone 15", count=20)
items = collector.collect_by_homepage("https://www.goofish.com/personal?userId=xxx", count=20)
```

**返回数据结构**：
```python
{
    "item_id": "1058748579734",
    "original_title": "iPhone 15 Pro Max 256G",
    "original_price": "6999",
    "description": "95新，无划痕，带原装充电器",
    "seller_name": "数码小哥",
    "seller_credit": "3",
    "want_count": "23",
    "view_count": "456",
    "link": "https://www.goofish.com/item?id=1058748579734",
    "image_urls": ["https://img.alicdn.com/bao/uploaded/xxx.heic_960x960.jpg", ...],
    "local_images": ["/Users/xxx/Desktop/闲鱼数据/images/img_001.jpg", ...],
}
```

### 2. AI文案改写 `engine/ai_writer.py`

**职责**：调用OpenAI兼容API改写商品文案。

**兼容性设计**：
- `_normalize_url()` 自动补全 `/v1/chat/completions` 路径
  - `https://api.deepseek.com` → `https://api.deepseek.com/v1/chat/completions`
  - `http://127.0.0.1:3000/v1` → `http://127.0.0.1:3000/v1/chat/completions`
- 支持任意模型名称：`deepseek-chat` / `gpt-4o` / `claude-3.5-sonnet` / 自定义

**Prompt设计**：系统角色为"闲鱼文案优化专家"，要求返回JSON格式（title/description/tags），基于原始信息优化而非编造。

### 3. 上架器 `engine/xianyu_lister.py`

**职责**：自动填写闲鱼发布表单并提交。

**流程**：打开 `goofish.com/publish` → 填标题 → 填描述 → 填价格 → 上传图片 → 点发布

**价格策略**：支持上架时按百分比加价（`price_markup_pct`参数）。

### 4. 价格管理 `engine/price_manager.py`

四种模式：批量加价(%)、批量降价(%)、统一设价、固定金额降价。

### 5. License授权系统

**`license/machine_id.py`**：
- macOS：`ioreg` 读取 `IOPlatformSerialNumber`
- Windows：`wmic bios get serialnumber`
- SHA-256 取前32位作为机器码

**`license/license_validator.py`**：
- **激活**：POST `/api/license/activate` → 服务器绑定机器码 → 返回签名License → 存本地 `~/.xf_license.json`
- **验证**：优先远程GET `/api/license/verify`，失败回退本地验证（检查过期时间+机器码）
- **服务器连接检测**：带缓存的`_check_server()`，5秒超时，避免每次验证都卡住
- **友好错误提示**：区分`ConnectionError`/`Timeout`/其他异常

### 6. UI层架构

**MainWindow** 持有共享数据 `collected_items`，各Tab通过 `main_window.get_items()` / `main_window.set_items()` 传递数据。

**License门控**：未激活时采集/上架/文案/导出Tab功能全部禁用，显示橙色提示条。

**异步模式**：耗时操作（采集/AI改写/上架）用 `QThread` 子线程执行，通过 `pyqtSignal` 回调更新UI。

## 服务端部署

- **服务器**：38.71.117.111:8000
- **框架**：FastAPI + SQLite
- **API端点**：
  - `GET /` — 首页（管理后台入口）
  - `GET /admin` — 管理后台（admin/admin123）
  - `GET /docs` — API文档（Swagger UI）
  - `POST /api/license/activate` — 激活License
  - `GET /api/license/verify` — 验证License
  - `POST /api/auth/register` — 用户注册
  - `POST /api/auth/login` — 用户登录

**已知问题**：云安全组偶发Connection reset（POST请求），License已支持本地离线验证回退。

## 运行方式

### macOS（开发/打包）
```bash
# 开发运行
cd xf_client
pip install -r requirements.txt
python main.py

# 打包.app
pip install pyinstaller
pyinstaller 闲鱼AI助手.spec --noconfirm
```

### Windows（直接运行，推荐）
1. 安装 Python 3.10+（勾选 Add to PATH）
2. 解压 `闲鱼AI助手_Windows版.zip` 到任意目录
3. 双击 `run_windows.bat`
4. 首次运行自动安装依赖，之后直接启动

### Windows（打包exe，可选）
1. 同上准备Python环境
2. 双击 `build_windows.bat`
3. 产物在 `output/闲鱼AI助手/` 目录

## 配置文件

| 文件 | 位置 | 说明 |
|------|------|------|
| `~/.xf_license.json` | 用户目录 | License数据（license_key/machine_id/signature/expires_at） |
| `~/.xf_env` | 用户目录 | AI API配置（AI_API_URL/AI_API_MODEL/AI_API_KEY） |

**`.xf_env` 格式**：
```
AI_API_URL=https://api.deepseek.com
AI_API_MODEL=deepseek-chat
AI_API_KEY=sk-xxxxxxxx
```

## 踩坑记录

1. **DrissionPage + PyInstaller onefile打包失败** — DrissionPage内部有动态导入，onefile模式找不到模块。改用onedir模式或直接`python main.py`运行。

2. **闲鱼CSS class带hash后缀** — 如`item-main-window-list-item--gXUlMEkj`，hash每次部署可能变化。用`[class*="item-main-window"]`模糊匹配。

3. **图片URL清洗正则错误** — 旧正则`_\d+x\d+.*?\.(jpg|jpeg|png|webp)`会匹配到`.heic`扩展名导致404。修正为`_\d+x\d+.*$`去掉所有缩略图后缀。

4. **服务器datetime时区不一致** — `datetime.now(timezone.utc)`（aware）与SQLite naive datetime比较抛TypeError。统一用`datetime.utcnow()`。

5. **SSH频繁被fail2ban封锁** — 服务器fail2ban的maxretry默认5次。已调整为maxretry=20、bantime=300。

6. **云安全组Connection reset** — 部分POST请求被云安全组reset，非服务器问题。License验证增加本地回退逻辑。

## 依赖清单

```
PyQt6>=6.6.0
DrissionPage>=4.0.0
aiohttp>=3.9.0
openpyxl>=3.1.0
requests>=2.31.0
```

Python版本要求：3.10+

## 版本历史

- **v1.0** (2026-06-23): 初始版本，采集+AI改写+上架+导出+License授权
- **v1.1** (2026-06-23): AI API兼容中转URL、图片采集修复、服务器连接优化、Windows运行脚本
