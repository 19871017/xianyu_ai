"""多平台运营监控引擎

支持平台: 闲鱼 / 拼多多 / 京东 / 阿里巴巴(1688)

使用 DrissionPage 驱动 Chrome 访问各平台商家后台，
通过 JS 提取运营指标，保存到本地 SQLite，并生成预警。

优化要点:
- 每个平台独立持久化 Profile，首次扫码登录后续免登录
- JS 提取多层 fallback: 选择器 → body.innerText 正则
- 所有 JS try/catch 包裹，返回 JSON 字符串
- _safe_int / _safe_float 处理 "1,234" / "1.2万" / "¥99.9" 等格式
- 单个指标失败不影响其他指标
"""
import re
import json
import time
import os
from dataclasses import dataclass, field
from typing import List, Dict, Optional
from datetime import datetime

from utils.browser_config import get_chromium_options, check_browser_available


PLATFORM_DISPLAY = {
    "xianyu": "闲鱼",
    "pdd": "拼多多",
    "jd": "京东",
    "1688": "阿里巴巴",
}

PLATFORM_COLOR = {
    "xianyu": "#FF6B35",
    "pdd": "#E44B2E",
    "jd": "#CC0000",
    "1688": "#FF6600",
}

# 每个平台的持久化 Profile 目录
PROFILE_DIRS = {
    "xianyu": os.path.join(os.path.expanduser("~"), ".xf_xianyu_monitor_profile"),
    "pdd":    os.path.join(os.path.expanduser("~"), ".xf_pdd_monitor_profile"),
    "jd":     os.path.join(os.path.expanduser("~"), ".xf_jd_monitor_profile"),
    "1688":   os.path.join(os.path.expanduser("~"), ".xf_1688_monitor_profile"),
}


# ──────────────────────────────────────────────────────────────
#  数据结构
# ──────────────────────────────────────────────────────────────

@dataclass
class MonitorSnapshot:
    """标准化的平台运营快照"""
    platform: str
    timestamp: str = ""
    is_logged_in: bool = False
    active_listings: int = 0        # 在售商品数
    total_views: int = 0            # 累计浏览量
    total_wants: int = 0            # 累计收藏/想要
    total_inquiries: int = 0        # 询盘数(1688)
    pending_orders: int = 0         # 待处理订单
    completed_orders_today: int = 0 # 今日完成订单
    completed_orders_30d: int = 0   # 近30日完成订单
    revenue_today: float = 0.0      # 今日营收
    revenue_30d: float = 0.0        # 近30日营收
    alerts: List[str] = field(default_factory=list)
    raw_data: Dict = field(default_factory=dict)
    error: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def display_name(self) -> str:
        return PLATFORM_DISPLAY.get(self.platform, self.platform)

    def to_dict(self) -> dict:
        return {
            "platform": self.platform,
            "timestamp": self.timestamp,
            "is_logged_in": self.is_logged_in,
            "active_listings": self.active_listings,
            "total_views": self.total_views,
            "total_wants": self.total_wants,
            "total_inquiries": self.total_inquiries,
            "pending_orders": self.pending_orders,
            "completed_orders_today": self.completed_orders_today,
            "completed_orders_30d": self.completed_orders_30d,
            "revenue_today": self.revenue_today,
            "revenue_30d": self.revenue_30d,
            "alerts": self.alerts,
            "error": self.error,
        }


# ──────────────────────────────────────────────────────────────
#  辅助函数
# ──────────────────────────────────────────────────────────────

def _safe_int(val) -> int:
    """安全转 int，处理 '1,234' / '1.2万' / '¥99' 等格式"""
    if val is None:
        return 0
    s = str(val).replace(",", "").replace(" ", "").replace("¥", "").replace("￥", "")
    if "万" in s:
        try:
            return int(float(s.replace("万", "")) * 10000)
        except Exception:
            return 0
    try:
        m = re.search(r"[\d.]+", s)
        return int(float(m.group())) if m else 0
    except Exception:
        return 0


def _safe_float(val) -> float:
    """安全转 float，处理 '1,234.5' / '1.2万' / '¥99.9元' 等格式"""
    if val is None:
        return 0.0
    s = str(val).replace(",", "").replace("¥", "").replace("￥", "").replace("元", "").strip()
    if "万" in s:
        try:
            return float(s.replace("万", "")) * 10000
        except Exception:
            return 0.0
    try:
        m = re.search(r"[\d.]+", s)
        return float(m.group()) if m else 0.0
    except Exception:
        return 0.0


def _run_js_safe(tab, js_code: str, default: str = "{}"):
    """安全执行 JS，失败返回默认值"""
    try:
        result = tab.run_js(js_code)
        if result is None:
            return default
        return result
    except Exception:
        return default


# ──────────────────────────────────────────────────────────────
#  基类
# ──────────────────────────────────────────────────────────────

class BasePlatformMonitor:
    """平台监控基类"""

    PLATFORM = ""
    DASHBOARD_URL = ""
    LOGIN_TIMEOUT = 120  # 扫码登录超时秒数

    def __init__(self, on_progress=None):
        self.on_progress = on_progress
        self.chromium = None
        self.tab = None

    def _log(self, msg: str):
        if self.on_progress:
            self.on_progress(msg)

    # ── 浏览器管理 ──────────────────────────────────────────

    def _init_browser(self, headless: bool = False):
        ok, msg = check_browser_available()
        if not ok:
            raise Exception(f"浏览器检查失败: {msg}")

        profile_dir = PROFILE_DIRS.get(self.PLATFORM)
        if not profile_dir:
            raise Exception(f"未配置 {self.PLATFORM} 的 Profile 目录")
        os.makedirs(profile_dir, exist_ok=True)

        co, _port = get_chromium_options(user_data_dir=profile_dir)
        if headless:
            co.set_argument("--headless=new")

        self.chromium = __import__("DrissionPage").Chromium(co)
        self.tab = self.chromium.latest_tab

    def _close_browser(self):
        if self.chromium:
            try:
                self.chromium.quit()
            except Exception:
                pass
            self.chromium = None
            self.tab = None

    def _safe_tab(self):
        try:
            _ = self.tab.url
        except Exception:
            if self.chromium:
                self.tab = self.chromium.latest_tab
        return self.tab

    # ── 登录检测 ──────────────────────────────────────────

    def _is_login_page(self) -> bool:
        """检测当前是否在登录页面"""
        try:
            url = self.tab.url.lower()
        except Exception:
            return True
        keywords = ("login", "passport", "sign_in", "sso", "account/login")
        return any(kw in url for kw in keywords)

    def _wait_for_login(self, timeout: int = None) -> bool:
        """等待用户扫码登录"""
        timeout = timeout or self.LOGIN_TIMEOUT
        display = PLATFORM_DISPLAY.get(self.PLATFORM, self.PLATFORM)
        self._log(f"请在浏览器中扫码登录 {display}（超时 {timeout}s）...")
        for i in range(timeout):
            time.sleep(1)
            try:
                if not self._is_login_page():
                    self._log(f"✅ {display} 登录成功")
                    return True
            except Exception:
                pass
        self._log(f"⏰ {display} 登录超时")
        return False

    # ── 核心流程 ──────────────────────────────────────────

    def _extract_metrics(self) -> dict:
        """子类实现：返回原始指标 dict"""
        raise NotImplementedError

    def fetch_snapshot(self, wait_login: bool = False) -> MonitorSnapshot:
        snapshot = MonitorSnapshot(platform=self.PLATFORM)
        try:
            self._init_browser(headless=False)
            display = snapshot.display_name()
            self._log(f"[{display}] 正在打开监控页面...")
            self._safe_tab().get(self.DASHBOARD_URL)
            time.sleep(4)

            # 登录检测
            if self._is_login_page():
                if wait_login:
                    logged_in = self._wait_for_login()
                    if not logged_in:
                        snapshot.error = "登录超时，请重试"
                        return snapshot
                    time.sleep(2)
                    # 登录成功后重新打开仪表盘
                    self._safe_tab().get(self.DASHBOARD_URL)
                    time.sleep(3)
                else:
                    snapshot.error = "登录失效，需重新登录"
                    snapshot.is_logged_in = False
                    snapshot.alerts.append("🔑 登录失效，需重新登录")
                    return snapshot

            snapshot.is_logged_in = True
            self._log(f"[{display}] 正在提取运营数据...")

            raw = self._extract_metrics()
            snapshot.raw_data = raw

            # 解析通用字段（每个字段独立 try，互不影响）
            for field_name in (
                "active_listings", "total_views", "total_wants",
                "total_inquiries", "pending_orders",
                "completed_orders_today", "completed_orders_30d",
            ):
                try:
                    setattr(snapshot, field_name, _safe_int(raw.get(field_name, 0)))
                except Exception:
                    pass

            for field_name in ("revenue_today", "revenue_30d"):
                try:
                    setattr(snapshot, field_name, _safe_float(raw.get(field_name, 0)))
                except Exception:
                    pass

            # 生成预警
            snapshot.alerts = self._generate_alerts(snapshot)

            self._log(
                f"[{display}] ✓ 在售:{snapshot.active_listings} "
                f"待处理:{snapshot.pending_orders} "
                f"今日营收:¥{snapshot.revenue_today:.2f}"
            )

        except Exception as e:
            snapshot.error = str(e)
            self._log(f"[{snapshot.display_name()}] ✗ 采集失败: {e}")
        finally:
            self._close_browser()

        return snapshot

    def _generate_alerts(self, snapshot: MonitorSnapshot) -> List[str]:
        """生成运营预警"""
        alerts = []

        # 登录失效
        if not snapshot.is_logged_in:
            alerts.append("🔑 登录失效，需重新登录")

        # 待处理订单 > 5: 紧急
        if snapshot.pending_orders > 5:
            alerts.append(f"🔴 紧急：有 {snapshot.pending_orders} 个待处理订单！")

        # 在售商品 = 0: 异常
        if snapshot.active_listings == 0 and snapshot.is_logged_in:
            alerts.append("⚠️ 异常：当前无在售商品，请检查商品状态")

        # 今日营收 > 日均2倍: 好消息
        daily_avg = snapshot.revenue_30d / 30 if snapshot.revenue_30d > 0 else 0
        if daily_avg > 0 and snapshot.revenue_today > daily_avg * 2:
            alerts.append(
                f"🎉 今日营收 ¥{snapshot.revenue_today:.0f} 是日均的 "
                f"{snapshot.revenue_today / daily_avg:.1f} 倍！"
            )

        # 今日营收 = 0 且有在售商品: 需关注
        if snapshot.revenue_today == 0 and snapshot.active_listings > 0 and snapshot.is_logged_in:
            alerts.append("👀 今日营收为0，但有在售商品，需关注流量情况")

        return alerts


# ──────────────────────────────────────────────────────────────
#  闲鱼监控
# ──────────────────────────────────────────────────────────────

class XianyuMonitor(BasePlatformMonitor):
    """闲鱼商家账号运营监控"""

    PLATFORM = "xianyu"
    DASHBOARD_URL = "https://www.goofish.com/personal"

    def _is_login_page(self) -> bool:
        try:
            url = self.tab.url.lower()
        except Exception:
            return True
        return any(kw in url for kw in ("login", "passport", "sign_in", "my.goofish.com/login"))

    def _extract_metrics(self) -> dict:
        metrics = {}

        # ── 在售商品数 + 浏览/想要 ──────────────────────────
        goods_and_stats_js = r"""
        try {
            var res = {};

            // 在售商品：尝试多种选择器
            var goodsSelectors = [
                '[class*="goods-item"]',
                '[class*="item-cell"]',
                '[class*="item-card"]',
                '[class*="product-item"]',
                '[class*="feeds-item"]',
                '[class*="card-wrap"]'
            ];
            var goodsCount = 0;
            for (var i = 0; i < goodsSelectors.length; i++) {
                var els = document.querySelectorAll(goodsSelectors[i]);
                if (els.length > 0) {
                    goodsCount = els.length;
                    break;
                }
            }

            // 从统计区域提取商品数
            var countEls = document.querySelectorAll(
                '[class*="goods-count"], [class*="item-count"], [class*="tab-count"], [class*="count-num"]'
            );
            countEls.forEach(function(el) {
                var text = el.textContent.trim();
                var m = text.match(/(\d[\d,]*)/);
                if (m && goodsCount === 0) {
                    // 如果文本包含"在售"或"商品"关键词
                    var parent = el.parentElement ? el.parentElement.textContent : '';
                    if (parent.includes('在售') || parent.includes('商品') || parent.includes('宝贝')) {
                        goodsCount = parseInt(m[1].replace(/,/g, '')) || goodsCount;
                    }
                }
            });
            res.active_listings = goodsCount;

            // 浏览/想要：从统计区域提取
            var bodyText = document.body.innerText;
            var viewMatch = bodyText.match(/(?:浏览|访客|曝光)[^\d]*([\d,.万]+)/);
            if (viewMatch) res.total_views = viewMatch[1];
            var wantMatch = bodyText.match(/(?:想要|收藏|喜欢|兴趣)[^\d]*([\d,.万]+)/);
            if (wantMatch) res.total_wants = wantMatch[1];

            // 备用：从统计元素提取
            var statEls = document.querySelectorAll(
                '[class*="stat"], [class*="data"], [class*="count"], [class*="meta"]'
            );
            statEls.forEach(function(el) {
                var text = el.textContent.trim();
                if (!res.total_views) {
                    var m = text.match(/(?:浏览|访客|曝光)[^\d]*([\d,.万]+)/);
                    if (m) res.total_views = m[1];
                }
                if (!res.total_wants) {
                    var m2 = text.match(/(?:想要|收藏|喜欢|兴趣)[^\d]*([\d,.万]+)/);
                    if (m2) res.total_wants = m2[1];
                }
            });

            return JSON.stringify(res);
        } catch(e) {
            return JSON.stringify({error: e.message});
        }
        """
        try:
            raw = _run_js_safe(self.tab, goods_and_stats_js)
            metrics.update(json.loads(raw))
        except Exception:
            pass

        # ── 订单数据：访问 /sold 页面 ──────────────────────
        try:
            self.tab.get("https://www.goofish.com/sold")
            time.sleep(3)

            order_js = r"""
            try {
                var res = {total: 0, pending: 0, completed: 0};

                // 尝试多种选择器
                var orderSelectors = [
                    '[class*="order-item"]',
                    '[class*="trade-item"]',
                    '[class*="order-card"]',
                    '[class*="order-wrap"]',
                    '[class*="trade-card"]'
                ];
                var orders = [];
                for (var i = 0; i < orderSelectors.length; i++) {
                    orders = document.querySelectorAll(orderSelectors[i]);
                    if (orders.length > 0) break;
                }

                res.total = orders.length;
                orders.forEach(function(o) {
                    var s = o.textContent;
                    if (s.indexOf('待') >= 0 || s.indexOf('处理') >= 0 || s.indexOf('付款') >= 0 || s.indexOf('发货') >= 0) {
                        res.pending++;
                    }
                    if (s.indexOf('完成') >= 0 || s.indexOf('成功') >= 0 || s.indexOf('收货') >= 0) {
                        res.completed++;
                    }
                });

                // 备用：从页面文字提取
                var bodyText = document.body.innerText;
                if (res.pending === 0) {
                    var m = bodyText.match(/待[发处]理[^\d]*(\d+)/);
                    if (m) res.pending = parseInt(m[1]) || 0;
                }
                if (res.total === 0) {
                    var m2 = bodyText.match(/共[^\d]*(\d+)[^\d]*笔/);
                    if (m2) res.total = parseInt(m2[1]) || 0;
                }

                return JSON.stringify(res);
            } catch(e) {
                return JSON.stringify({total: 0, pending: 0, completed: 0});
            }
            """
            order_raw = _run_js_safe(self.tab, order_js, '{"total":0,"pending":0,"completed":0}')
            order_data = json.loads(order_raw)
            metrics["pending_orders"] = order_data.get("pending", 0)
            metrics["completed_orders_today"] = order_data.get("completed", 0)
        except Exception:
            pass

        # ── 回到个人页提取更多统计 ──────────────────────────
        try:
            self.tab.get(self.DASHBOARD_URL)
            time.sleep(2)
            extra_js = r"""
            try {
                var res = {};
                var bodyText = document.body.innerText;

                if (!res.total_views) {
                    var m = bodyText.match(/(?:浏览|访客|曝光)[^\d]*([\d,.万]+)/);
                    if (m) res.total_views = m[1];
                }
                if (!res.total_wants) {
                    var m2 = bodyText.match(/(?:想要|收藏|喜欢|兴趣)[^\d]*([\d,.万]+)/);
                    if (m2) res.total_wants = m2[1];
                }

                // 尝试从数字标签提取
                var numEls = document.querySelectorAll('[class*="num"], [class*="count"], [class*="value"]');
                var nums = [];
                numEls.forEach(function(el) {
                    var t = el.textContent.trim();
                    var m = t.match(/^([\d,.万]+)$/);
                    if (m) nums.push(m[1]);
                });
                if (nums.length >= 1 && !res.total_views) res.total_views = nums[0];
                if (nums.length >= 2 && !res.total_wants) res.total_wants = nums[1];

                return JSON.stringify(res);
            } catch(e) { return '{}'; }
            """
            extra = json.loads(_run_js_safe(self.tab, extra_js))
            # 只补充尚未提取到的字段
            for k, v in extra.items():
                if k not in metrics or not metrics[k]:
                    metrics[k] = v
        except Exception:
            pass

        return metrics


# ──────────────────────────────────────────────────────────────
#  拼多多监控
# ──────────────────────────────────────────────────────────────

class PddSellerMonitor(BasePlatformMonitor):
    """拼多多商家后台运营监控"""

    PLATFORM = "pdd"
    DASHBOARD_URL = "https://mms.pinduoduo.com/dashboard/index"

    def _is_login_page(self) -> bool:
        try:
            url = self.tab.url.lower()
        except Exception:
            return True
        return any(kw in url for kw in ("login", "passport", "mms.pinduoduo.com/login"))

    def _extract_metrics(self) -> dict:
        metrics = {}

        # ── 仪表盘概览数据 ──────────────────────────────────
        overview_js = r"""
        try {
            var res = {};

            // 方案1: 从数据卡片提取
            var cardSelectors = [
                '[class*="data-card"]',
                '[class*="overview-item"]',
                '[class*="stat"]',
                '[class*="metric"]',
                '[class*="summary"]'
            ];
            for (var si = 0; si < cardSelectors.length; si++) {
                var cards = document.querySelectorAll(cardSelectors[si]);
                if (cards.length === 0) continue;
                cards.forEach(function(card) {
                    var labelEl = card.querySelector('[class*="label"], [class*="title"], [class*="name"]');
                    var valueEl = card.querySelector('[class*="value"], [class*="num"], [class*="amount"]');
                    if (!labelEl || !valueEl) return;
                    var label = labelEl.textContent.trim();
                    var value = valueEl.textContent.trim();
                    if (!label || !value) return;

                    if (label.indexOf('成交金额') >= 0 || label.indexOf('营业额') >= 0 || label.indexOf('营收') >= 0) {
                        if (!res.revenue_today) res.revenue_today = value;
                    }
                    if (label.indexOf('访客') >= 0 || label.indexOf('浏览') >= 0) {
                        if (!res.total_views) res.total_views = value;
                    }
                    if (label.indexOf('成交笔数') >= 0 || label.indexOf('订单') >= 0) {
                        if (!res.completed_orders_today) res.completed_orders_today = value;
                    }
                    if (label.indexOf('待发货') >= 0 || label.indexOf('待处理') >= 0) {
                        if (!res.pending_orders) res.pending_orders = value;
                    }
                    if (label.indexOf('在售') >= 0 || label.indexOf('商品') >= 0) {
                        if (!res.active_listings) res.active_listings = value;
                    }
                });
            }

            // 方案2: 从 body.innerText 正则匹配
            var bodyText = document.body.innerText;

            if (!res.revenue_today) {
                var m1 = bodyText.match(/成交金额[：:]*\s*([¥￥]?\s*[\d,.万]+)/);
                if (m1) res.revenue_today = m1[1].trim();
            }
            if (!res.total_views) {
                var m2 = bodyText.match(/访客数[：:]*\s*([\d,.万]+)/);
                if (m2) res.total_views = m2[1];
            }
            if (!res.completed_orders_today) {
                var m3 = bodyText.match(/成交笔数[：:]*\s*([\d,]+)/);
                if (m3) res.completed_orders_today = m3[1];
            }
            if (!res.pending_orders) {
                var m4 = bodyText.match(/待发货[：:]*\s*(\d+)/);
                if (m4) res.pending_orders = m4[1];
            }
            if (!res.active_listings) {
                var m5 = bodyText.match(/在售商品[：:]*\s*(\d+)/);
                if (m5) res.active_listings = m5[1];
            }

            return JSON.stringify(res);
        } catch(e) {
            return JSON.stringify({error: e.message});
        }
        """
        try:
            raw = _run_js_safe(self.tab, overview_js)
            metrics.update(json.loads(raw))
        except Exception:
            pass

        # ── 待处理订单：访问订单列表 ──────────────────────
        try:
            self.tab.get("https://mms.pinduoduo.com/order/list?order_status=1")
            time.sleep(3)
            order_js = r"""
            try {
                var res = {pending: 0, total: 0};

                // 从计数元素提取
                var countEls = document.querySelectorAll(
                    '[class*="total-count"], [class*="order-total"], [class*="count-num"], [class*="badge"]'
                );
                for (var i = 0; i < countEls.length; i++) {
                    var m = countEls[i].textContent.match(/(\d+)/);
                    if (m) {
                        res.pending = parseInt(m[1]);
                        break;
                    }
                }

                // 计算订单卡片数量
                var orderEls = document.querySelectorAll(
                    '[class*="order-item"], [class*="order-card"], [class*="order-row"]'
                );
                res.total = orderEls.length;
                if (res.pending === 0 && res.total > 0) {
                    res.pending = res.total;
                }

                // 备用：正则
                if (res.pending === 0) {
                    var m = document.body.innerText.match(/共\s*(\d+)\s*[条笔个]/);
                    if (m) res.pending = parseInt(m[1]);
                }

                return JSON.stringify(res);
            } catch(e) {
                return JSON.stringify({pending: 0, total: 0});
            }
            """
            order_raw = _run_js_safe(self.tab, order_js, '{"pending":0,"total":0}')
            order_data = json.loads(order_raw)
            if order_data.get("pending", 0):
                metrics["pending_orders"] = order_data["pending"]
        except Exception:
            pass

        # ── 在售商品：访问商品列表 ──────────────────────────
        try:
            self.tab.get("https://mms.pinduoduo.com/goods/goods_list")
            time.sleep(3)
            goods_js = r"""
            try {
                var res = {active_listings: 0};

                // 从计数元素提取
                var countEls = document.querySelectorAll(
                    '[class*="total"], [class*="count"], [class*="goods-count"]'
                );
                for (var i = 0; i < countEls.length; i++) {
                    var text = countEls[i].textContent.trim();
                    var m = text.match(/(\d[\d,]*)/);
                    if (m) {
                        var num = parseInt(m[1].replace(/,/g, ''));
                        if (num > 0 && num < 100000) {
                            res.active_listings = num;
                            break;
                        }
                    }
                }

                // 计算商品卡片数量
                if (res.active_listings === 0) {
                    var goodsEls = document.querySelectorAll(
                        '[class*="goods-item"], [class*="goods-card"], [class*="product-item"]'
                    );
                    res.active_listings = goodsEls.length;
                }

                // 备用：正则
                if (res.active_listings === 0) {
                    var m = document.body.innerText.match(/共\s*(\d+)\s*[条个]/);
                    if (m) res.active_listings = parseInt(m[1]);
                }

                return JSON.stringify(res);
            } catch(e) {
                return JSON.stringify({active_listings: 0});
            }
            """
            goods_raw = _run_js_safe(self.tab, goods_js, '{"active_listings":0}')
            goods_data = json.loads(goods_raw)
            if goods_data.get("active_listings", 0):
                metrics["active_listings"] = goods_data["active_listings"]
        except Exception:
            pass

        return metrics


# ──────────────────────────────────────────────────────────────
#  京东监控
# ──────────────────────────────────────────────────────────────

class JdSellerMonitor(BasePlatformMonitor):
    """京东商家后台运营监控"""

    PLATFORM = "jd"
    DASHBOARD_URL = "https://pop.jd.com/"

    def _is_login_page(self) -> bool:
        try:
            url = self.tab.url.lower()
        except Exception:
            return True
        return any(kw in url for kw in ("login", "passport", "sso", "pop.jd.com/login"))

    def _extract_metrics(self) -> dict:
        metrics = {}

        # ── 仪表盘概览数据 ──────────────────────────────────
        dashboard_js = r"""
        try {
            var res = {};
            var bodyText = document.body.innerText;

            // 方案1: 从数字卡片提取
            var numSelectors = [
                '[class*="data-num"]',
                '[class*="count-num"]',
                '[class*="stat-value"]',
                '[class*="amount"]',
                '[class*="metric-value"]'
            ];
            for (var si = 0; si < numSelectors.length; si++) {
                var els = document.querySelectorAll(numSelectors[si]);
                if (els.length === 0) continue;
                els.forEach(function(el, idx) {
                    var text = el.textContent.trim();
                    var parent = el.parentElement ? el.parentElement.textContent : '';
                    if (parent.indexOf('销售额') >= 0 || parent.indexOf('营收') >= 0) {
                        if (!res.revenue_today) res.revenue_today = text;
                    } else if (parent.indexOf('订单') >= 0 || parent.indexOf('成交') >= 0) {
                        if (!res.completed_orders_today) res.completed_orders_today = text;
                    } else if (parent.indexOf('访客') >= 0 || parent.indexOf('浏览') >= 0) {
                        if (!res.total_views) res.total_views = text;
                    } else if (parent.indexOf('待') >= 0) {
                        if (!res.pending_orders) res.pending_orders = text;
                    }
                });
            }

            // 方案2: 正则匹配
            if (!res.revenue_today) {
                var m1 = bodyText.match(/今日销售额[：:]*\s*[¥￥]?\s*([\d,.]+)/);
                if (m1) res.revenue_today = m1[1];
            }
            if (!res.completed_orders_today) {
                var m2 = bodyText.match(/今日成交订单[：:]*\s*([\d,]+)/);
                if (m2) res.completed_orders_today = m2[1];
            }
            if (!res.total_views) {
                var m3 = bodyText.match(/今日访客[：:]*\s*([\d,.万]+)/);
                if (m3) res.total_views = m3[1];
            }
            if (!res.pending_orders) {
                var m4 = bodyText.match(/待[处发]理[：:]*\s*(\d+)/);
                if (m4) res.pending_orders = m4[1];
            }

            return JSON.stringify(res);
        } catch(e) {
            return JSON.stringify({error: e.message});
        }
        """
        try:
            raw = _run_js_safe(self.tab, dashboard_js)
            metrics.update(json.loads(raw))
        except Exception:
            pass

        # ── 待处理订单 ──────────────────────────────────────
        try:
            self.tab.get("https://pop.jd.com/order/orderList.html")
            time.sleep(3)
            order_js = r"""
            try {
                var res = {pending: 0, total: 0};

                // 从计数元素提取
                var countEls = document.querySelectorAll(
                    '[class*="total-num"], [class*="record-count"], [class*="count"]'
                );
                for (var i = 0; i < countEls.length; i++) {
                    var m = countEls[i].textContent.match(/(\d+)/);
                    if (m) {
                        res.pending = parseInt(m[1]);
                        break;
                    }
                }

                // 计算订单行数
                var orderEls = document.querySelectorAll(
                    '[class*="order-item"], [class*="order-row"], [class*="order-tr"]'
                );
                res.total = orderEls.length;
                if (res.pending === 0 && res.total > 0) {
                    res.pending = res.total;
                }

                // 备用：正则
                if (res.pending === 0) {
                    var m = document.body.innerText.match(/共\s*(\d+)\s*[条笔]/);
                    if (m) res.pending = parseInt(m[1]);
                }

                return JSON.stringify(res);
            } catch(e) {
                return JSON.stringify({pending: 0, total: 0});
            }
            """
            order_raw = _run_js_safe(self.tab, order_js, '{"pending":0,"total":0}')
            order_data = json.loads(order_raw)
            if order_data.get("pending", 0):
                metrics["pending_orders"] = order_data["pending"]
        except Exception:
            pass

        # ── 在售商品 ────────────────────────────────────────
        try:
            self.tab.get("https://pop.jd.com/goods/goodsList.html")
            time.sleep(3)
            goods_js = r"""
            try {
                var res = {active_listings: 0};

                var countEls = document.querySelectorAll(
                    '[class*="total-num"], [class*="record-count"], [class*="goods-count"]'
                );
                for (var i = 0; i < countEls.length; i++) {
                    var text = countEls[i].textContent.trim();
                    var m = text.match(/(\d[\d,]*)/);
                    if (m) {
                        var num = parseInt(m[1].replace(/,/g, ''));
                        if (num > 0 && num < 100000) {
                            res.active_listings = num;
                            break;
                        }
                    }
                }

                if (res.active_listings === 0) {
                    var goodsEls = document.querySelectorAll(
                        '[class*="goods-item"], [class*="goods-row"], [class*="product-item"]'
                    );
                    res.active_listings = goodsEls.length;
                }

                if (res.active_listings === 0) {
                    var m = document.body.innerText.match(/共\s*(\d+)\s*[条个]/);
                    if (m) res.active_listings = parseInt(m[1]);
                }

                return JSON.stringify(res);
            } catch(e) {
                return JSON.stringify({active_listings: 0});
            }
            """
            goods_raw = _run_js_safe(self.tab, goods_js, '{"active_listings":0}')
            goods_data = json.loads(goods_raw)
            if goods_data.get("active_listings", 0):
                metrics["active_listings"] = goods_data["active_listings"]
        except Exception:
            pass

        return metrics


# ──────────────────────────────────────────────────────────────
#  阿里巴巴/1688监控
# ──────────────────────────────────────────────────────────────

class AlibabaSellerMonitor(BasePlatformMonitor):
    """1688商家后台运营监控"""

    PLATFORM = "1688"
    DASHBOARD_URL = "https://wangpu.1688.com/"

    def _is_login_page(self) -> bool:
        try:
            url = self.tab.url.lower()
        except Exception:
            return True
        return any(
            kw in url
            for kw in ("login", "passport", "member.1688.com")
        )

    def _extract_metrics(self) -> dict:
        metrics = {}

        # ── 仪表盘概览数据 ──────────────────────────────────
        overview_js = r"""
        try {
            var res = {};

            // 方案1: 从数据卡片提取
            var cardSelectors = [
                '[class*="data-value"]',
                '[class*="stat"]',
                '[class*="metric"]',
                '[class*="overview"]',
                '[class*="summary"]'
            ];
            for (var si = 0; si < cardSelectors.length; si++) {
                var cards = document.querySelectorAll(cardSelectors[si]);
                if (cards.length === 0) continue;
                cards.forEach(function(card) {
                    var labelEl = card.querySelector('[class*="label"], [class*="title"], [class*="name"]');
                    var valueEl = card.querySelector('[class*="value"], [class*="num"], [class*="amount"]');
                    if (!labelEl || !valueEl) return;
                    var label = labelEl.textContent.trim();
                    var value = valueEl.textContent.trim();
                    if (!label || !value) return;

                    if (label.indexOf('浏览') >= 0) {
                        if (!res.total_views) res.total_views = value;
                    }
                    if (label.indexOf('询盘') >= 0) {
                        if (!res.total_inquiries) res.total_inquiries = value;
                    }
                    if (label.indexOf('成交金额') >= 0 || label.indexOf('营业额') >= 0) {
                        if (!res.revenue_30d) res.revenue_30d = value;
                    }
                    if (label.indexOf('待发货') >= 0 || label.indexOf('待处理') >= 0) {
                        if (!res.pending_orders) res.pending_orders = value;
                    }
                });
            }

            // 方案2: 正则匹配
            var bodyText = document.body.innerText;

            if (!res.total_views) {
                var m1 = bodyText.match(/浏览量[：:]*\s*([\d,.万]+)/);
                if (m1) res.total_views = m1[1];
            }
            if (!res.total_inquiries) {
                var m2 = bodyText.match(/询盘[：:]*\s*([\d,]+)/);
                if (m2) res.total_inquiries = m2[1];
            }
            if (!res.revenue_30d) {
                var m3 = bodyText.match(/成交金额[：:]*\s*[¥￥]?\s*([\d,.万]+)/);
                if (m3) res.revenue_30d = m3[1];
            }
            if (!res.pending_orders) {
                var m4 = bodyText.match(/待发货[：:]*\s*(\d+)/);
                if (m4) res.pending_orders = m4[1];
            }
            if (!res.completed_orders_30d) {
                var m5 = bodyText.match(/成交笔数[：:]*\s*([\d,]+)/);
                if (m5) res.completed_orders_30d = m5[1];
            }

            return JSON.stringify(res);
        } catch(e) {
            return JSON.stringify({error: e.message});
        }
        """
        try:
            raw = _run_js_safe(self.tab, overview_js)
            metrics.update(json.loads(raw))
        except Exception:
            pass

        # ── 待处理订单 ──────────────────────────────────────
        try:
            self.tab.get("https://wangpu.1688.com/order/orderList.htm")
            time.sleep(3)
            order_js = r"""
            try {
                var res = {pending: 0, total: 0};

                var countEls = document.querySelectorAll(
                    '[class*="total"], [class*="count"], [class*="badge"]'
                );
                for (var i = 0; i < countEls.length; i++) {
                    var m = countEls[i].textContent.match(/(\d+)/);
                    if (m) {
                        res.pending = parseInt(m[1]);
                        break;
                    }
                }

                var orderEls = document.querySelectorAll(
                    '[class*="order-item"], [class*="order-row"], [class*="order-tr"]'
                );
                res.total = orderEls.length;
                if (res.pending === 0 && res.total > 0) {
                    res.pending = res.total;
                }

                if (res.pending === 0) {
                    var m = document.body.innerText.match(/共\s*(\d+)\s*[条笔]/);
                    if (m) res.pending = parseInt(m[1]);
                }

                return JSON.stringify(res);
            } catch(e) {
                return JSON.stringify({pending: 0, total: 0});
            }
            """
            order_raw = _run_js_safe(self.tab, order_js, '{"pending":0,"total":0}')
            order_data = json.loads(order_raw)
            if order_data.get("pending", 0):
                metrics["pending_orders"] = order_data["pending"]
        except Exception:
            pass

        # ── 在售商品 ────────────────────────────────────────
        try:
            self.tab.get("https://wangpu.1688.com/product/list.htm")
            time.sleep(3)
            products_js = r"""
            try {
                var res = {active_listings: 0};

                var countEls = document.querySelectorAll(
                    '[class*="total"], [class*="count"], [class*="product-count"]'
                );
                for (var i = 0; i < countEls.length; i++) {
                    var text = countEls[i].textContent.trim();
                    var m = text.match(/(\d[\d,]*)/);
                    if (m) {
                        var num = parseInt(m[1].replace(/,/g, ''));
                        if (num > 0 && num < 100000) {
                            res.active_listings = num;
                            break;
                        }
                    }
                }

                if (res.active_listings === 0) {
                    var productEls = document.querySelectorAll(
                        '[class*="product-item"], [class*="goods-item"], [class*="product-card"]'
                    );
                    res.active_listings = productEls.length;
                }

                if (res.active_listings === 0) {
                    var m = document.body.innerText.match(/共\s*(\d+)\s*[条个]/);
                    if (m) res.active_listings = parseInt(m[1]);
                }

                return JSON.stringify(res);
            } catch(e) {
                return JSON.stringify({active_listings: 0});
            }
            """
            products_raw = _run_js_safe(self.tab, products_js, '{"active_listings":0}')
            products_data = json.loads(products_raw)
            if products_data.get("active_listings", 0):
                metrics["active_listings"] = products_data["active_listings"]
        except Exception:
            pass

        return metrics


# ──────────────────────────────────────────────────────────────
#  监控管理器（统一入口）
# ──────────────────────────────────────────────────────────────

class MonitorManager:
    """多平台运营监控管理器"""

    MONITOR_CLASSES: Dict[str, type] = {
        "xianyu": XianyuMonitor,
        "pdd": PddSellerMonitor,
        "jd": JdSellerMonitor,
        "1688": AlibabaSellerMonitor,
    }

    def __init__(self, on_progress=None):
        self.on_progress = on_progress

    def _log(self, msg: str):
        if self.on_progress:
            self.on_progress(msg)

    def fetch_platform(
        self,
        platform: str,
        wait_login: bool = False,
        on_progress=None,
    ) -> MonitorSnapshot:
        """采集单个平台的运营快照"""
        cls = self.MONITOR_CLASSES.get(platform)
        if not cls:
            snap = MonitorSnapshot(platform=platform)
            snap.error = f"不支持的平台: {platform}"
            return snap

        monitor = cls(on_progress=on_progress or self.on_progress)
        return monitor.fetch_snapshot(wait_login=wait_login)

    def fetch_all(
        self,
        platforms: Optional[List[str]] = None,
        wait_login: bool = False,
        on_progress=None,
    ) -> Dict[str, MonitorSnapshot]:
        """采集所有（或指定）平台的运营快照（串行执行）"""
        if platforms is None:
            platforms = list(self.MONITOR_CLASSES.keys())

        results = {}
        for p in platforms:
            self._log(f"开始采集 {PLATFORM_DISPLAY.get(p, p)} 数据...")
            snap = self.fetch_platform(p, wait_login=wait_login, on_progress=on_progress)
            results[p] = snap
            self.save_snapshot(snap)

        return results

    def login_platform(self, platform: str, on_progress=None) -> MonitorSnapshot:
        """打开浏览器让用户扫码登录指定平台"""
        return self.fetch_platform(platform, wait_login=True, on_progress=on_progress)

    def check_login_status(self, platform: str) -> MonitorSnapshot:
        """检查指定平台的登录状态（不触发登录流程）"""
        return self.fetch_platform(platform, wait_login=False)

    def save_snapshot(self, snapshot: MonitorSnapshot):
        """保存快照到数据库"""
        try:
            from database.db_manager import db
            db.save_monitor_snapshot(snapshot.to_dict())
        except Exception as e:
            self._log(f"保存监控快照失败: {e}")

    def get_history(
        self,
        platform: str,
        days: int = 7,
    ) -> List[Dict]:
        """获取历史快照"""
        try:
            from database.db_manager import db
            return db.get_monitor_snapshots(platform, days)
        except Exception:
            return []

    def get_summary(self, snapshots: Dict[str, MonitorSnapshot]) -> Dict:
        """汇总所有平台指标"""
        total_listings = sum(s.active_listings for s in snapshots.values())
        total_views = sum(s.total_views for s in snapshots.values())
        total_pending = sum(s.pending_orders for s in snapshots.values())
        total_revenue_today = sum(s.revenue_today for s in snapshots.values())
        total_revenue_30d = sum(s.revenue_30d for s in snapshots.values())
        all_alerts = []
        for s in snapshots.values():
            all_alerts.extend(s.alerts)

        return {
            "total_listings": total_listings,
            "total_views": total_views,
            "total_pending_orders": total_pending,
            "total_revenue_today": total_revenue_today,
            "total_revenue_30d": total_revenue_30d,
            "all_alerts": all_alerts,
            "platforms_connected": sum(1 for s in snapshots.values() if s.is_logged_in),
            "platforms_total": len(snapshots),
        }
