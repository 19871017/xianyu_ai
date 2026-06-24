# 闲鱼AI助手

商品采集 → AI文案改写 → 批量上架 → 数据导出，全链路自动化工具。

支持平台：闲鱼、拼多多、1688。

## 功能一览

| 模块 | 功能 | 说明 |
|------|------|------|
| 🔍 采集 | 多平台商品采集 | 闲鱼/拼多多/1688，关键词搜索或链接直采 |
| ✍️ AI改写 | 文案自动优化 | 兼容OpenAI/DeepSeek/任意中转API |
| 📦 上架 | 批量发布闲鱼 | 百分比加价、自动填表发布 |
| 💰 价格管理 | 批量调价 | 加价/降价/统一设价/固定金额降价 |
| 📊 导出 | Excel导出 | 带格式化样式，含图片路径 |
| 📋 订单管理 | 订单监控 | 扫码登录、自动刷新、统计卡片 |
| 🔑 授权 | License系统 | RSA-2048签名 + 机器码绑定 + 离线回退 |

## 技术栈

- **客户端**：PyQt6 + DrissionPage + SQLite
- **服务端**：FastAPI + SQLite + RSA-2048
- **AI**：OpenAI兼容格式（支持DeepSeek/OneAPI/NewAPI等中转）

## 项目结构

```
xianyu_ai/
├── xf_client/              # 客户端
│   ├── main.py             # 入口
│   ├── config.py           # 全局配置
│   ├── engine/             # 核心引擎（采集/AI/上架/导出）
│   ├── ui/                 # PyQt6界面层
│   ├── license/            # 授权系统
│   ├── utils/              # 工具函数
│   ├── database/           # SQLite数据持久化
│   ├── requirements.txt    # 依赖
│   ├── 闲鱼AI助手.spec      # PyInstaller打包配置
│   ├── build_windows.bat   # Windows打包脚本
│   ├── run_windows.bat     # Windows直接运行脚本
│   ├── README.md           # 客户端说明
│   └── DEV.md              # 开发文档（详细架构说明）
├── xf_server/              # 服务端（原版）
├── xf_server_stable/       # 服务端（稳定版，已部署）
│   ├── main.py             # FastAPI入口
│   ├── models/             # 数据模型
│   ├── routers/            # API路由
│   ├── services/           # 业务逻辑
│   ├── schemas/            # Pydantic模型
│   ├── utils/              # 工具（RSA/JWT/安全）
│   ├── admin_frontend/     # 管理后台前端
│   ├── deploy_stable.sh    # 部署脚本
│   └── requirements.txt    # 依赖
└── fix_server.sh           # 服务器修复脚本
```

## 快速开始

### 客户端

```bash
cd xf_client
pip install -r requirements.txt
python main.py
```

Windows用户可直接双击 `run_windows.bat`。

### 服务端

```bash
cd xf_server_stable
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000
```

管理后台：`http://localhost:8000/admin`（admin/admin123）

API文档：`http://localhost:8000/docs`

## 详细文档

- [客户端开发文档](xf_client/DEV.md) — 完整架构说明、模块详解、踩坑记录
- [客户端使用说明](xf_client/README.md) — 安装运行打包

## 环境要求

- Python 3.10+
- Google Chrome 浏览器
- macOS / Windows / Linux

## 依赖

```
PyQt6>=6.6.0
DrissionPage>=4.0.0
aiohttp>=3.9.0
openpyxl>=3.1.0
requests>=2.31.0
fastapi>=0.104.0
uvicorn>=0.24.0
python-jose>=3.3.0
passlib>=1.7.4
bcrypt>=4.0.0
python-multipart>=0.0.6
pydantic>=2.5.0
```

## License

 proprietary - 仅供授权用户使用
