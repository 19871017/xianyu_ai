"""全局配置 - 多平台电商AI助手 v3.0"""
import os

# ──────────────────────── 服务端 ────────────────────────
SERVER_BASE_URL = "http://38.71.117.111:8000"
API_LICENSE_ACTIVATE = f"{SERVER_BASE_URL}/api/license/activate"
API_LICENSE_VERIFY   = f"{SERVER_BASE_URL}/api/license/verify"
API_AUTH_REGISTER    = f"{SERVER_BASE_URL}/api/auth/register"
API_AUTH_LOGIN       = f"{SERVER_BASE_URL}/api/auth/login"

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
}

# ──────────────────────── 各平台监控仪表盘 URL ────────────────────────
MONITOR_DASHBOARD_URLS = {
    "xianyu": "https://www.goofish.com/personal",
    "pdd":    "https://mms.pinduoduo.com/dashboard/index",
    "jd":     "https://pop.jd.com/",
    "1688":   "https://wangpu.1688.com/",
}

# ──────────────────────── 平台显示名称 ────────────────────────
PLATFORM_DISPLAY = {
    "xianyu": "闲鱼",
    "pdd":    "拼多多",
    "jd":     "京东",
    "1688":   "阿里巴巴",
}

PLATFORM_ICON = {
    "xianyu": "🐟",
    "pdd":    "🛒",
    "jd":     "🏪",
    "1688":   "🏭",
}

# ──────────────────────── 采集默认参数 ────────────────────────
DEFAULT_COLLECT_COUNT = 20
DEFAULT_PRICE_MARKUP_PCT = 10.0   # 默认加价 10%
DEFAULT_STOCK = 999               # 拼多多/京东默认库存
DEFAULT_MOQ = 1                   # 1688 默认起订量
REQUEST_TIMEOUT = 30              # HTTP 请求超时(秒)
BROWSER_WAIT_AFTER_NAV = 3        # 页面跳转后等待(秒)

# ──────────────────────── 版本 ────────────────────────
APP_VERSION = "3.0.0"
APP_NAME = "多平台电商AI助手"

# ──────────────────────── 兼容别名 ────────────────────────
SERVER_URL = SERVER_BASE_URL          # 旧代码引用 SERVER_URL
AI_MODEL = AI_API_MODEL                # 旧代码引用 AI_MODEL
LICENSE_FILE = os.path.join(BASE_DIR, ".xf_license.json")  # License 文件路径
