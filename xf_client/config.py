"""全局配置 - 多平台电商AI助手 v3.0"""
import os

# ──────────────────────── 服务端 ────────────────────────
SERVER_BASE_URL = os.environ.get("XF_SERVER_BASE_URL", "https://xy.lxd997.dpdns.org")
API_LICENSE_ACTIVATE = f"{SERVER_BASE_URL}/api/license/activate"
API_LICENSE_VERIFY   = f"{SERVER_BASE_URL}/api/license/verify"
API_AUTH_REGISTER    = f"{SERVER_BASE_URL}/api/auth/register"
API_AUTH_LOGIN       = f"{SERVER_BASE_URL}/api/auth/login"
API_LICENSE_HEARTBEAT = f"{SERVER_BASE_URL}/api/license/heartbeat"
# 公开更新检测：返回某平台最新版本（无需登录态）。platform=mac/win。
API_PUBLIC_LATEST    = f"{SERVER_BASE_URL}/api/public/latest"
# 下载站首页：客户端检测到新版本后，点「确定」跳转此处下载。
DOWNLOAD_SITE_URL    = os.environ.get("XF_DOWNLOAD_SITE_URL", f"{SERVER_BASE_URL}/")

# 客户端调用 activate/verify/heartbeat 必须携带的密钥（与服务端 CLIENT_API_KEY 一致）。
# 优先环境变量，便于分发时不写死在源码里。
CLIENT_API_KEY = os.environ.get("XF_CLIENT_API_KEY", "a5008d5e75e902a25cde6f3e72181d25ed9967471e8d2545540bf624a6f39626")

# 离线宽限：远程不可达时，本地最多容忍的时长（秒）。超过即判定失效。
LICENSE_OFFLINE_GRACE_SECONDS = int(os.environ.get("XF_OFFLINE_GRACE_SECONDS", str(72 * 3600)))
# 心跳间隔（秒），服务端可在 verify/activate 响应中下发覆盖。
LICENSE_HEARTBEAT_INTERVAL = int(os.environ.get("XF_HEARTBEAT_INTERVAL", "60"))

# ──────────────────────── AI 接口 ────────────────────────
AI_API_URL   = os.environ.get("AI_API_URL",   "https://api.deepseek.com")
AI_API_MODEL = os.environ.get("AI_API_MODEL", "deepseek-chat")
AI_API_KEY   = os.environ.get("AI_API_KEY",   "")

# ──────────────────────── 本地路径 ────────────────────────
BASE_DIR   = os.path.join(os.path.expanduser("~"), "Desktop", "电商数据")
IMAGE_DIR  = os.path.join(BASE_DIR, "images")
EXPORT_DIR = os.path.join(BASE_DIR, "exports")

# 确保目录存在
for _d in [BASE_DIR, IMAGE_DIR, EXPORT_DIR]:
    os.makedirs(_d, exist_ok=True)

# ──────────────────────── 各平台 URL ────────────────────────
PLATFORM_URLS = {
    # 闲鱼
    "xianyu": {
        "home":    "https://www.goofish.com/",
        "search":  "https://www.goofish.com/search?q={kw}",
        "publish": "https://www.goofish.com/publish",
        "orders":  "https://www.goofish.com/sold",
        "profile": "https://www.goofish.com/personal",
    },
    # 拼多多
    "pdd": {
        "home":    "https://mobile.yangkeduo.com/",
        "search":  "https://mobile.yangkeduo.com/search_result.html?search_key={kw}",
        "seller":  "https://mms.pinduoduo.com/",
        "publish": "https://mms.pinduoduo.com/goods/goods_commit",
        "orders":  "https://mms.pinduoduo.com/order/list",
        "goods":   "https://mms.pinduoduo.com/goods/goods_list",
        "dashboard": "https://mms.pinduoduo.com/dashboard/index",
    },
    # 京东
    "jd": {
        "home":    "https://www.jd.com/",
        "search":  "https://search.jd.com/Search?keyword={kw}&enc=utf-8",
        "item":    "https://item.jd.com/{sku_id}.html",
        "seller":  "https://pop.jd.com/",
        "publish": "https://pop.jd.com/goods/addGoods.html",
        "orders":  "https://pop.jd.com/order/orderList.html",
        "goods":   "https://pop.jd.com/goods/goodsList.html",
    },
    # 阿里巴巴/1688
    "1688": {
        "home":    "https://www.1688.com/",
        "search":  "https://s.1688.com/selloffer/offerresultfresh.htm?keywords={kw}",
        "item":    "https://detail.1688.com/offer/{offer_id}.html",
        "seller":  "https://wangpu.1688.com/",
        "publish": "https://product.1688.com/product/publishProduct.htm",
        "orders":  "https://trade.1688.com/order/orderList.htm",
        "products": "https://wangpu.1688.com/product/list.htm",
        "dashboard": "https://wangpu.1688.com/",
    },
    # 闲管家（goofish.pro）— 第三方鱼小铺/闲鱼管理后台，用于发布上品
    "goofishpro": {
        "home":      "https://goofish.pro/",
        "login":     "https://goofish.pro/login",
        "publish":   "https://goofish.pro/sale/product/add",
        "products":  "https://goofish.pro/sale/product/index",
        "orders":    "https://goofish.pro/sale/order/index",
        "dashboard": "https://goofish.pro/sale/statistics",
    },
}

# ──────────────────────── 平台显示名称 ────────────────────────
PLATFORM_DISPLAY = {
    "xianyu": "闲鱼",
    "pdd":    "拼多多",
    "jd":     "京东",
    "1688":   "阿里巴巴",
    "goofishpro": "闲管家",
}

PLATFORM_ICON = {
    "xianyu": "🐟",
    "pdd":    "🛒",
    "jd":     "🏪",
    "1688":   "🏭",
    "goofishpro": "🐠",
}

# ──────────────────────── 采集默认参数 ────────────────────────
DEFAULT_COLLECT_COUNT = 20
DEFAULT_PRICE_MARKUP_PCT = 10.0   # 默认加价 10%
DEFAULT_STOCK = 999               # 拼多多/京东默认库存
DEFAULT_MOQ = 1                   # 1688 默认起订量
REQUEST_TIMEOUT = 30              # HTTP 请求超时(秒)
BROWSER_WAIT_AFTER_NAV = 3        # 页面跳转后等待(秒)

# ──────────────────────── 版本 ────────────────────────
APP_VERSION = "3.2.1"
APP_NAME = "多平台电商AI助手"

# ──────────────────────── 兼容别名 ────────────────────────
SERVER_URL = SERVER_BASE_URL          # 旧代码引用 SERVER_URL
AI_MODEL = AI_API_MODEL                # 旧代码引用 AI_MODEL
LICENSE_FILE = os.path.join(BASE_DIR, ".xf_license.json")  # License 文件路径
