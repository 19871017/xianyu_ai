"""多平台运营监控引擎

支持平台: 闲鱼 / 拼多多 / 京东 / 阿里巴巴(1688)

使用 DrissionPage 驱动 Chrome 访问各平台商家后台，
通过 JS 提取运营指标，保存到本地 SQLite，并生成预警。
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


@dataclass
class MonitorSnapshot:
    """标准化的平台运营快照"""
    platform: str
    timestamp: str = ""
    is_logged_in: bool = False
    active_listings: int = 0      # 在售商品数
    total_views: int = 0           # 累计浏览量
    total_wants: int = 0           # 累计收藏/想要
    total_inquiries: int = 0       # 询盘数(1688)
    pending_orders: int = 0        # 待处理订单
    completed_orders_today: int = 0  # 今日完成订单
    completed_orders_30d: int = 0    # 近30日完成订单
    revenue_today: float = 0.0       # 今日营收
    revenue_30d: float = 0.0         # 近30日营收
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


def _safe_int(val) -> int:
    """安全转int，处理 '1,234' / '1.2万' 等格式"""
    if val is None:
        return 0
    s = str(val).replace(",", "").replace(" ", "")
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
    """安全转float"""
    if val is None:
        return 0.0
    s = str(val).replace(",", "").replace("¥", "").replace("元", "").strip()
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


class BasePlatformMonitor:
    """平台监控基类"""

    PLATFORM = ""
    DASHBOARD_URL = ""
    PROFILE_DIR_NAME = ".monitor_profile"

    def __init__(self, on_progress=None):
        self.on_progress = on_progress
        self.chromium = None
        self.tab = None

    def _log(self, msg: str):
        if self.on_progress:
            self.on_progress(msg)

    def _init_browser(self, headless: bool = False):
        ok, msg = check_browser_available()
        if not ok:
            raise Exception(f"浏览器检查失败: {msg}")
        profile_dir = os.path.join(
            os.path.expanduser("~"), f".{self.PLATFORM}_monitor_profile"
        )
        os.makedirs(profile_dir, exist_ok=True)
        co, _port = get_chromium_options(user_data_dir=profile_dir)
        if headless:
            co.set_argument("--headless=new")
        self.chromium = Chromium(co)
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

    def _is_login_page(self) -> bool:
        url = self.tab.url.lower()
        return "login" in url or "passport" in url or "sign_in" in url

    def _wait_for_login(self, timeout: int = 120) -> bool:
        self._log(f"请在浏览器中登录 {PLATFORM_DISPLAY.get(self.PLATFORM, self.PLATFORM)}...")
        for i in range(timeout):
            time.sleep(1)
            try:
                if not self._is_login_page():
                    self._log(f"✅ {PLATFORM_DISPLAY.get(self.PLATFORM)} 登录成功")
                    return True
            except Exception:
                pass
        return False

    def _extract_metrics(self) -> dict:
        """子类实现：返回原始指标 dict"""
        raise NotImplementedError

    def fetch_snapshot(self, wait_login: bool = False) -> MonitorSnapshot:
        snapshot = MonitorSnapshot(platform=self.PLATFORM)
        try:
            self._init_browser(headless=False)
            self._log(f"[{snapshot.display_name()}] 正在打开监控页面...")
            self._safe_tab().get(self.DASHBOARD_URL)
            time.sleep(4)

            if self._is_login_page():
                if wait_login:
                    logged_in = self._wait_for_login(120)
                    if not logged_in:
                        snapshot.error = "登录超时"
                        return snapshot
                    time.sleep(2)
                else:
                    snapshot.error = "需要登录"
                    snapshot.is_logged_in = False
                    return snapshot

            snapshot.is_logged_in = True
            self._log(f"[{snapshot.display_name()}] 正在提取运营数据...")

            raw = self._extract_metrics()
            snapshot.raw_data = raw

            # 解析通用字段
            snapshot.active_listings = _safe_int(raw.get("active_listings", 0))
            snapshot.total_views = _safe_int(raw.get("total_views", 0))
            snapshot.total_wants = _safe_int(raw.get("total_wants", 0))
            snapshot.total_inquiries = _safe_int(raw.get("total_inquiries", 0))
            snapshot.pending_orders = _safe_int(raw.get("pending_orders", 0))
            snapshot.completed_orders_today = _safe_int(raw.get("completed_orders_today", 0))
            snapshot.completed_orders_30d = _safe_int(raw.get("completed_orders_30d", 0))
            snapshot.revenue_today = _safe_float(raw.get("revenue_today", 0))
            snapshot.revenue_30d = _safe_float(raw.get("revenue_30d", 0))

            # 生成预警
            snapshot.alerts = self._generate_alerts(snapshot)

            self._log(
                f"[{snapshot.display_name()}] ✓ 在售:{snapshot.active_listings} "
                f"待处理订单:{snapshot.pending_orders} "
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
        if snapshot.pending_orders > 5:
            alerts.append(f"⚠️ 有 {snapshot.pending_orders} 个待处理订单！")
        if snapshot.active_listings == 0 and snapshot.is_logged_in:
            alerts.append("⚠️ 当前无在售商品，请检查商品状态")
        if snapshot.revenue_today > snapshot.revenue_30d / 30 * 2:
            alerts.append(f"🎉 今日营收是日均的2倍！(¥{snapshot.revenue_today:.0f})")
        return alerts


# ──────────────────────────────────────────────────────────────
#  闲鱼监控
# ──────────────────────────────────────────────────────────────

class XianyuMonitor(BasePlatformMonitor):
    """闲鱼商家账号运营监控"""

    PLATFORM = "xianyu"
    DASHBOARD_URL = "https://www.goofish.com/personal"

    def _extract_metrics(self) -> dict:
        metrics = {}
        # 在售商品数
        goods_count_js = """
        try {
            var els = document.querySelectorAll('[class*="goods-item"], [class*="item-cell"], [class*="item-list"] li');
            var countEl = document.querySelector('[class*="goods-count"], [class*="item-count"]');
            if (countEl) {
                var m = countEl.textContent.match(/\\d+/);
                return m ? m[0] : String(els.length);
            }
            return String(els.length);
        } catch(e) { return '0'; }
        """
        metrics["active_listings"] = self.tab.run_js(goods_count_js) or "0"

        # 访问订单页面获取订单数
        try:
            self.tab.get("https://www.goofish.com/sold")
            time.sleep(3)

            order_js = """
            try {
                var orders = document.querySelectorAll(
                    '[class*="order-item"], [class*="trade-item"], [class*="order-card"]'
                );
                var pending = 0;
                var completed = 0;
                orders.forEach(function(o) {
                    var s = o.textContent;
                    if (s.includes('待') || s.includes('处理') || s.includes('付款')) pending++;
                    if (s.includes('完成') || s.includes('成功')) completed++;
                });
                return JSON.stringify({
                    total: orders.length,
                    pending: pending,
                    completed: completed
                });
            } catch(e) { return '{"total":0,"pending":0,"completed":0}'; }
            """
            order_raw = self.tab.run_js(order_js) or '{"total":0,"pending":0,"completed":0}'
            order_data = json.loads(order_raw)
            metrics["pending_orders"] = order_data.get("pending", 0)
            metrics["completed_orders_today"] = order_data.get("completed", 0)
        except Exception:
            pass

        # 回到个人页面获取更多统计
        try:
            self.tab.get(self.DASHBOARD_URL)
            time.sleep(2)
            stats_js = """
            try {
                var res = {};
                var viewEls = document.querySelectorAll('[class*="view-count"], [class*="visit"]');
                if (viewEls.length > 0) {
                    var m = viewEls[0].textContent.match(/[\\d,万]+/);
                    res.total_views = m ? m[0] : '0';
                }
                var wantEls = document.querySelectorAll('[class*="want-count"], [class*="interest"]');
                if (wantEls.length > 0) {
                    var m2 = wantEls[0].textContent.match(/[\\d,万]+/);
                    res.total_wants = m2 ? m2[0] : '0';
                }
                return JSON.stringify(res);
            } catch(e) { return '{}'; }
            """
            stats = json.loads(self.tab.run_js(stats_js) or "{}")
            metrics.update(stats)
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
        url = self.tab.url.lower()
        return "login" in url or "passport" in url or "mms.pinduoduo.com/login" in url

    def _extract_metrics(self) -> dict:
        metrics = {}
        overview_js = """
        try {
            var res = {};
            // 昨日/今日概况 各指标
            var cards = document.querySelectorAll('[class*="data-card"], [class*="overview-item"], [class*="stat-item"]');
            cards.forEach(function(card) {
                var label = (card.querySelector('[class*="label"], [class*="title"]') || {textContent:''}).textContent.trim();
                var value = (card.querySelector('[class*="value"], [class*="num"]') || {textContent:''}).textContent.trim();
                if (!label || !value) return;
                if (label.includes('访客') || label.includes('浏览')) res.total_views = value;
                else if (label.includes('成交金额') || label.includes('营收')) res.revenue_today = value;
                else if (label.includes('成交笔数') || label.includes('订单数')) res.completed_orders_today = value;
                else if (label.includes('在售') || label.includes('商品数')) res.active_listings = value;
            });

            // 备用：从页面文字匹配
            var bodyText = document.body.innerText;
            if (!res.revenue_today) {
                var m = bodyText.match(/成交金额[：:]*\\s*([¥￥\\d,.万]+)/);
                if (m) res.revenue_today = m[1];
            }
            if (!res.total_views) {
                var m2 = bodyText.match(/访客数[：:]*\\s*([\\d,.万]+)/);
                if (m2) res.total_views = m2[1];
            }
            return JSON.stringify(res);
        } catch(e) { return '{}'; }
        """
        overview = json.loads(self.tab.run_js(overview_js) or "{}")
        metrics.update(overview)

        # 获取待处理订单
        try:
            self.tab.get("https://mms.pinduoduo.com/order/list?order_status=1")
            time.sleep(3)
            order_count_js = """
            try {
                var countEl = document.querySelector('[class*="total-count"], [class*="order-total"]');
                if (countEl) {
                    var m = countEl.textContent.match(/\\d+/);
                    return m ? m[0] : '0';
                }
                return String(document.querySelectorAll('[class*="order-item"]').length);
            } catch(e) { return '0'; }
            """
            metrics["pending_orders"] = self.tab.run_js(order_count_js) or "0"
        except Exception:
            pass

        # 商品数
        try:
            self.tab.get("https://mms.pinduoduo.com/goods/goods_list")
            time.sleep(2)
            goods_js = """
            try {
                var countEl = document.querySelector('[class*="total"], [class*="count"]');
                if (countEl) {
                    var m = countEl.textContent.match(/\\d+/);
                    return m ? m[0] : '0';
                }
                return String(document.querySelectorAll('[class*="goods-item"]').length);
            } catch(e) { return '0'; }
            """
            metrics["active_listings"] = self.tab.run_js(goods_js) or metrics.get("active_listings", "0")
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
        url = self.tab.url.lower()
        return "login" in url or "passport" in url or "sso" in url

    def _extract_metrics(self) -> dict:
        metrics = {}
        dashboard_js = """
        try {
            var res = {};
            var bodyText = document.body.innerText;

            // 今日销售额
            var m1 = bodyText.match(/今日销售额[^\\d]*([\\d,.]+)/);
            if (m1) res.revenue_today = m1[1];

            // 今日成交订单
            var m2 = bodyText.match(/今日成交订单[^\\d]*([\\d,]+)/);
            if (m2) res.completed_orders_today = m2[1];

            // 今日访客
            var m3 = bodyText.match(/今日访客[^\\d]*([\\d,.万]+)/);
            if (m3) res.total_views = m3[1];

            // 待处理订单
            var m4 = bodyText.match(/待[处发]理[^\\d]*([\\d]+)/);
            if (m4) res.pending_orders = m4[1];

            // 通过数字卡片提取
            document.querySelectorAll('[class*="data-num"], [class*="count-num"], [class*="stat-value"]').forEach(function(el, i) {
                if (i === 0 && !res.revenue_today) res.revenue_today = el.textContent.trim();
                if (i === 1 && !res.completed_orders_today) res.completed_orders_today = el.textContent.trim();
                if (i === 2 && !res.total_views) res.total_views = el.textContent.trim();
            });

            return JSON.stringify(res);
        } catch(e) { return '{}'; }
        """
        overview = json.loads(self.tab.run_js(dashboard_js) or "{}")
        metrics.update(overview)

        # 在售商品数
        try:
            self.tab.get("https://pop.jd.com/goods/goodsList.html")
            time.sleep(3)
            goods_js = """
            try {
                var countEl = document.querySelector('[class*="total-num"], [class*="record-count"]');
                if (countEl) {
                    var m = countEl.textContent.match(/\\d+/);
                    return m ? m[0] : '0';
                }
                return '0';
            } catch(e) { return '0'; }
            """
            metrics["active_listings"] = self.tab.run_js(goods_js) or "0"
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
        url = self.tab.url.lower()
        return "login" in url or "passport" in url or "member.1688.com" in url

    def _extract_metrics(self) -> dict:
        metrics = {}
        overview_js = """
        try {
            var res = {};
            var bodyText = document.body.innerText;

            // 浏览量
            var m1 = bodyText.match(/浏览量[^\\d]*([\\d,.万]+)/);
            if (m1) res.total_views = m1[1];

            // 询盘数
            var m2 = bodyText.match(/询盘[^\\d]*([\\d,]+)/);
            if (m2) res.total_inquiries = m2[1];

            // 成交
            var m3 = bodyText.match(/成交笔数[^\\d]*([\\d,]+)/);
            if (m3) res.completed_orders_30d = m3[1];

            var m4 = bodyText.match(/成交金额[^\\d¥]*([\\d,.万]+)/);
            if (m4) res.revenue_30d = m4[1];

            // 待处理
            var m5 = bodyText.match(/待发货[^\\d]*([\\d]+)/);
            if (m5) res.pending_orders = m5[1];

            // 卡片提取
            document.querySelectorAll('[class*="data-value"], [class*="stat-num"]').forEach(function(el, i) {
                var text = el.textContent.trim();
                if (i === 0 && !res.total_views) res.total_views = text;
                if (i === 1 && !res.total_inquiries) res.total_inquiries = text;
                if (i === 2 && !res.revenue_30d) res.revenue_30d = text;
            });

            return JSON.stringify(res);
        } catch(e) { return '{}'; }
        """
        overview = json.loads(self.tab.run_js(overview_js) or "{}")
        metrics.update(overview)

        # 在售产品数
        try:
            self.tab.get("https://wangpu.1688.com/product/list.htm")
            time.sleep(3)
            products_js = """
            try {
                var countEl = document.querySelector('[class*="total"], [class*="count"]');
                if (countEl) {
                    var m = countEl.textContent.match(/\\d+/);
                    return m ? m[0] : '0';
                }
                return String(document.querySelectorAll('[class*="product-item"]').length);
            } catch(e) { return '0'; }
            """
            metrics["active_listings"] = self.tab.run_js(products_js) or "0"
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
