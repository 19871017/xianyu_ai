# 多平台电商AI助手 v3.0

> 支持 **闲鱼 / 拼多多 / 京东 / 阿里巴巴(1688)** 四平台全链路运营的桌面AI工具

---

## 🌟 功能全览

| 功能模块 | 闲鱼 🐟 | 拼多多 🛒 | 京东 🏪 | 1688 🏭 |
|---------|---------|---------|--------|--------|
| **商品采集** | ✅ 关键词/主页 | ✅ 关键词/链接 | ✅ 关键词/链接 | ✅ 关键词/链接 |
| **AI文案改写** | ✅ | ✅ | ✅ | ✅ |
| **批量上架** | ✅ | ✅ | ✅ | ✅ |
| **价格管理** | ✅ 加/降/设价 | ✅ | ✅ | ✅ |
| **运营监控** | ✅ | ✅ | ✅ | ✅ |
| **Excel导出** | ✅ | ✅ | ✅ | ✅ |
| **订单监控** | ✅ | — | — | — |

---

## 📦 项目结构

```
xianyu_ai/
├── xf_client/                       # 桌面客户端 (PyQt6)
│   ├── config.py                    # 全局配置（平台URL/AI/路径/参数）
│   ├── main.py                      # 程序入口
│   ├── requirements.txt             # Python依赖
│   ├── run_windows.bat              # Windows一键运行
│   ├── build_windows.bat            # Windows打包脚本
│   │
│   ├── engine/                      # 核心业务引擎
│   │   ├── xianyu_collector.py      # 闲鱼采集器
│   │   ├── pdd_collector.py         # 拼多多采集器
│   │   ├── jd_collector.py          # 🆕 京东采集器
│   │   ├── alibaba_collector.py     # 阿里巴巴/1688采集器
│   │   ├── ai_writer.py             # AI文案改写（OpenAI兼容）
│   │   ├── xianyu_lister.py         # 闲鱼上架器
│   │   ├── pdd_lister.py            # 🆕 拼多多上架器
│   │   ├── jd_lister.py             # 🆕 京东上架器
│   │   ├── alibaba_lister.py        # 🆕 1688上架器
│   │   ├── monitor_manager.py       # 🆕 多平台运营监控引擎
│   │   ├── price_manager.py         # 价格管理（加价/降价/设价）
│   │   ├── image_downloader.py      # 异步图片下载
│   │   └── excel_exporter.py        # Excel导出
│   │
│   ├── ui/                          # 界面层（PyQt6）
│   │   ├── main_window.py           # 🔄 主窗口（含运营监控Tab）
│   │   ├── collect_tab.py           # 🔄 采集Tab（支持4平台）
│   │   ├── listing_tab.py           # 🔄 上架Tab（支持4平台）
│   │   ├── monitor_tab.py           # 🆕 运营监控Tab
│   │   ├── copywriting_tab.py       # 文案优化Tab
│   │   ├── export_tab.py            # Excel导出Tab
│   │   ├── order_tab.py             # 订单Tab
│   │   └── settings_tab.py          # 设置Tab
│   │
│   ├── database/
│   │   └── db_manager.py            # 🔄 SQLite（新增monitor_snapshots表）
│   ├── license/                     # RSA-2048授权系统
│   └── utils/                       # 工具函数
│
└── xf_server_stable/                # 服务端（FastAPI + SQLite）
    ├── main.py
    ├── requirements.txt
    ├── deploy_stable.sh
    ├── models/ / routers/ / services/ / schemas/ / utils/
    └── admin_frontend/
```

---

## 🚀 快速开始

### 环境要求
- Python **3.10+**
- Chrome 浏览器（用于DrissionPage自动化）

### macOS / Linux

```bash
cd xf_client
pip install -r requirements.txt
python main.py
```

### Windows（推荐）

1. 安装 Python 3.10+（勾选 Add to PATH）
2. 双击 `run_windows.bat`，首次自动安装依赖

### Windows 打包 exe

```bash
build_windows.bat
# 产物在 output/多平台电商AI助手/
```

---

## ⚙️ 配置说明

### AI 接口配置（设置页面）

| 配置项 | 说明 | 示例 |
|-------|------|------|
| API URL | OpenAI兼容接口地址 | `https://api.deepseek.com` |
| API Key | 接口密钥 | `sk-xxxxxxxx` |
| 模型名称 | 任意模型 | `deepseek-chat` / `gpt-4o` |

配置存储于 `~/.xf_env`，格式：
```
AI_API_URL=https://api.deepseek.com
AI_API_MODEL=deepseek-chat
AI_API_KEY=sk-xxxxxxxx
```

### License 激活（设置页面）
1. 在设置页面输入 License Key
2. 点击「激活」，联网验证并绑定机器码
3. 激活后支持离线回退验证（7天内）

---

## 📡 多平台运营监控

运营监控Tab支持同时监控4个平台的商家账号状态：

| 指标 | 说明 |
|-----|------|
| 在售商品数 | 当前平台在架商品总数 |
| 浏览量/询盘 | 累计浏览量（1688为询盘数） |
| 待处理订单 | 需要及时处理的订单（超过5个自动预警） |
| 今日营收 | 当日成交金额 |
| 30日营收 | 近30天累计营收 |
| 运营预警 | 异常情况自动推送（待处理积压/营收异常等） |

**使用流程：**
1. 点击平台卡片的「连接账号」按钮
2. 在弹出的浏览器中完成对应平台商家后台登录（Cookie持久化，之后免登录）
3. 数据自动采集并保存到本地数据库
4. 可设置 10/30/60 分钟自动刷新

---

## 🔄 采集 → 上架 全流程

```
采集Tab
  ├── 选择来源平台（闲鱼/拼多多/京东/1688）
  ├── 关键词搜索 或 商品链接直采
  └── 图片自动下载+MD5去重
        ↓
文案优化Tab
  └── AI批量改写标题/描述/标签
        ↓
上架Tab
  ├── 选择目标上架平台（独立于来源平台）
  ├── 价格策略：加价%、降价%、固定价
  ├── 平台特有参数：库存/起订量(1688)/类目(京东)
  └── 批量上架（等待商家后台登录→自动填表→提交）
        ↓
导出Tab
  └── 导出 Excel（含来源/上架状态/价格对比）
```

---

## 🛡️ 授权系统

- **机器码**：macOS用`ioreg`、Windows用`wmic`，SHA-256前32位
- **激活**：RSA-2048签名 + JWT + 机器码绑定，存 `~/.xf_license.json`
- **验证策略**：优先远程，失败回退本地（离线模式）
- **服务端**：`38.71.117.111:8000` / FastAPI + SQLite
- **管理后台**：`/admin`（首次使用请修改默认密码）

---

## 📋 数据库表结构（SQLite）

| 表名 | 说明 |
|-----|------|
| `products` | 商品数据（含来源平台/AI改写/上架状态） |
| `orders` | 订单数据 |
| `monitor_snapshots` | **🆕** 多平台运营监控历史快照 |
| `cookies` | 平台Cookie持久化 |
| `collect_records` | 采集记录 |

数据库位置：`~/.xf_data/data.db`

---

## 🔧 已知问题与解决方案

| 问题 | 原因 | 解决方案 |
|-----|------|---------|
| DrissionPage + PyInstaller onefile打包失败 | 动态导入问题 | 改用onedir模式或直接运行 |
| 闲鱼CSS class带hash后缀 | 前端随版本变化 | 用`[class*="..."]`模糊匹配 |
| 京东/拼多多商家上架失败 | 页面结构更新 | 手动确认商家后台结构后提issue |
| 服务器Connection reset | 云安全组偶发 | License已支持本地离线验证回退 |
| SSH被fail2ban封锁 | 密码尝试过多 | 已调整为maxretry=20，bantime=300 |

---

## 📝 版本历史

| 版本 | 日期 | 更新内容 |
|-----|------|---------|
| **v3.0** | 2026-06-24 | 🆕 京东采集+上架；🆕 拼多多/1688上架器；🆕 多平台运营监控Tab；🆕 数据库monitor_snapshots表；🔄 4平台统一采集/上架UI |
| v1.1 | 2026-06-23 | AI API兼容中转URL、图片采集修复、服务器连接优化、Windows运行脚本 |
| v1.0 | 2026-06-23 | 初始版本，闲鱼采集+AI改写+上架+导出+License授权 |

---

## ⚠️ 合规提示

本工具通过自动化浏览器操作各平台网页，使用时请注意：
- 遵守各平台用户协议和反爬规则
- 控制采集频率，避免触发平台风控
- 商品上架内容需符合平台发布规范
- 本工具仅供学习和个人合法使用
