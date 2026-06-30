"""经营概览 Tab：汇总本地库的商品/订单/复检数据，一眼看清经营状况。

数据全部来自本地数据库（已采集/已上架商品、已抓取订单、复检结果），
打开即时计算、100% 可靠，无需联网抓取。
"""
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel,
    QPushButton, QGroupBox, QFrame, QMessageBox,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QFont

from engine.dashboard_stats import compute_dashboard
from engine.xianyu_listings import fetch_and_store, get_latest_listing_summary
from database.db_manager import db


GLOBAL_FONT_FAMILY = "Microsoft YaHei, PingFang SC, sans-serif"


class _StatCard(QFrame):
    """单个指标卡片：大数字 + 标题 + 可选副标题。"""

    def __init__(self, title: str, color: str = "#00695c"):
        super().__init__()
        self._color = color
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet(
            f"_StatCard {{ background:#ffffff; border:1px solid #e0e0e0; "
            f"border-radius:8px; }}"
        )
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(2)
        self.value_label = QLabel("-")
        self.value_label.setFont(QFont(GLOBAL_FONT_FAMILY, 24, QFont.Weight.Bold))
        self.value_label.setStyleSheet(f"color:{color};")
        self.title_label = QLabel(title)
        self.title_label.setFont(QFont(GLOBAL_FONT_FAMILY, 12))
        self.title_label.setStyleSheet("color:#666;")
        self.sub_label = QLabel("")
        self.sub_label.setFont(QFont(GLOBAL_FONT_FAMILY, 11))
        self.sub_label.setStyleSheet("color:#999;")
        lay.addWidget(self.value_label)
        lay.addWidget(self.title_label)
        lay.addWidget(self.sub_label)

    def set_value(self, value, sub: str = ""):
        self.value_label.setText(str(value))
        self.sub_label.setText(sub)


class _SyncListingWorker(QThread):
    """后台抓取闲鱼官方在售商品并落库（只读，不下架/编辑）。"""
    progress_msg = pyqtSignal(str)
    finished = pyqtSignal(dict)

    def run(self):
        def log(msg):
            self.progress_msg.emit(msg)
        try:
            result = fetch_and_store(on_log=log)
        except Exception as e:
            result = {"ok": False, "error": str(e)}
        self.finished.emit(result or {"ok": False, "error": "未知错误"})


class DashboardTab(QWidget):
    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self._setup_ui()
        self.refresh_items(None)

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(10, 10, 10, 10)

        top = QHBoxLayout()
        title = QLabel("📊 经营概览")
        title.setFont(QFont(GLOBAL_FONT_FAMILY, 16, QFont.Weight.Bold))
        top.addWidget(title)
        top.addStretch()
        self.refresh_btn = QPushButton("🔄 刷新")
        self.refresh_btn.setMinimumHeight(34)
        self.refresh_btn.setStyleSheet(
            "QPushButton { background:#00897b; color:white; border-radius:4px; "
            "padding:4px 18px; font-size:13px; }"
            "QPushButton:hover { background:#00695c; }"
        )
        self.refresh_btn.clicked.connect(lambda: self.refresh_items(None))
        top.addWidget(self.refresh_btn)

        self.sync_btn = QPushButton("🔄 同步闲鱼在售")
        self.sync_btn.setMinimumHeight(34)
        self.sync_btn.setStyleSheet(
            "QPushButton { background:#1565c0; color:white; border-radius:4px; "
            "padding:4px 18px; font-size:13px; }"
            "QPushButton:hover { background:#0d47a1; }"
            "QPushButton:disabled { background:#bbb; }"
        )
        self.sync_btn.clicked.connect(self._sync_listings)
        top.addWidget(self.sync_btn)
        layout.addLayout(top)

        # 同步状态提示行
        self.sync_status = QLabel("")
        self.sync_status.setFont(QFont(GLOBAL_FONT_FAMILY, 11))
        self.sync_status.setStyleSheet("color:#888;")
        layout.addWidget(self.sync_status)

        # ── 指标卡片区 ──
        cards = QGridLayout()
        cards.setSpacing(10)
        self.card_total = _StatCard("商品总数", "#1565c0")
        self.card_listed = _StatCard("已上架", "#2e7d32")
        self.card_multi = _StatCard("多规格商品", "#00695c")
        self.card_orders = _StatCard("订单总数", "#6a1b9a")
        self.card_revenue = _StatCard("成交额(¥)", "#ad1457")
        self.card_risk = _StatCard("风险商品", "#c62828")
        cards.addWidget(self.card_total, 0, 0)
        cards.addWidget(self.card_listed, 0, 1)
        cards.addWidget(self.card_multi, 0, 2)
        cards.addWidget(self.card_orders, 1, 0)
        cards.addWidget(self.card_revenue, 1, 1)
        cards.addWidget(self.card_risk, 1, 2)
        self.card_xianyu = _StatCard("闲鱼在售(实时)", "#00838f")
        cards.addWidget(self.card_xianyu, 2, 0)
        layout.addLayout(cards)

        # ── 明细分组区 ──
        detail = QHBoxLayout()
        detail.setSpacing(10)

        self.platform_group = QGroupBox("按来源平台")
        self.platform_group.setFont(QFont(GLOBAL_FONT_FAMILY, 12, QFont.Weight.Bold))
        self.platform_layout = QVBoxLayout(self.platform_group)
        detail.addWidget(self.platform_group)

        self.status_group = QGroupBox("按上架状态")
        self.status_group.setFont(QFont(GLOBAL_FONT_FAMILY, 12, QFont.Weight.Bold))
        self.status_layout = QVBoxLayout(self.status_group)
        detail.addWidget(self.status_group)

        self.profit_group = QGroupBox("利润与运营")
        self.profit_group.setFont(QFont(GLOBAL_FONT_FAMILY, 12, QFont.Weight.Bold))
        self.profit_layout = QVBoxLayout(self.profit_group)
        detail.addWidget(self.profit_group)

        layout.addLayout(detail)
        layout.addStretch()

    def _clear_layout(self, lay):
        while lay.count():
            item = lay.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

    def _add_rows(self, lay, rows):
        self._clear_layout(lay)
        if not rows:
            empty = QLabel("暂无数据")
            empty.setStyleSheet("color:#999; font-size:12px;")
            lay.addWidget(empty)
            return
        for text in rows:
            lb = QLabel(text)
            lb.setFont(QFont(GLOBAL_FONT_FAMILY, 12))
            lb.setStyleSheet("color:#444; padding:2px 0;")
            lay.addWidget(lb)
        lay.addStretch()

    # 主窗口刷新接口（与其它 tab 一致）。items 不使用，数据自库中取。
    def refresh_items(self, items):
        try:
            products = db.get_all_products()
        except Exception:
            products = []
        try:
            orders = db.get_all_orders()
        except Exception:
            orders = []
        try:
            rechecks = db.get_latest_rechecks(only_alert=True)
        except Exception:
            rechecks = []

        d = compute_dashboard(products, orders, rechecks)
        p, o, pf, rk = d["products"], d["orders"], d["profit"], d["risk"]

        self.card_total.set_value(p["total"], f"关注 {p['total_wants']} / 浏览 {p['total_views']}")
        self.card_listed.set_value(p["listed"], f"占 {self._pct(p['listed'], p['total'])}")
        self.card_multi.set_value(p["multi_sku"], f"占 {self._pct(p['multi_sku'], p['total'])}")
        self.card_orders.set_value(o["total"], f"匹配源头 {o['match_rate']}%")
        self.card_revenue.set_value(f"{o['revenue']:.0f}", f"{o['total']} 单累计")
        risk_total = rk["critical"] + rk["warn"]
        self.card_risk.set_value(risk_total, f"严重 {rk['critical']} / 警告 {rk['warn']}")
        # 风险卡颜色：有严重风险才标红。
        self.card_risk.value_label.setStyleSheet(
            "color:#c62828;" if rk["critical"] else ("color:#e65100;" if rk["warn"] else "color:#2e7d32;")
        )

        self._add_rows(self.platform_layout, [
            f"{r['label']}：{r['count']} 个" for r in p["by_platform"]
        ])
        self._add_rows(self.status_layout, [
            f"{r['label']}：{r['count']} 个" for r in p["by_status"]
        ])
        self._add_rows(self.profit_layout, [
            f"平均加价率：{pf['avg_markup_pct']}%",
            f"潜在毛利合计：¥{pf['total_gross_margin']:.2f}",
            f"单品均毛利：¥{pf['avg_gross_margin']:.2f}",
            f"（基于 {pf['sample']} 个已上架商品）",
        ])

        self._refresh_xianyu_card()

    def _refresh_xianyu_card(self):
        """根据最近一次在售快照刷新「闲鱼在售」卡片。"""
        try:
            snap = get_latest_listing_summary()
        except Exception:
            snap = None
        if not snap:
            self.card_xianyu.set_value("-", "点「同步闲鱼在售」获取")
            return
        ts = (snap.get("snapshot_time") or "").replace("T", " ")[:16]
        self.card_xianyu.set_value(
            snap.get("active_listings", 0),
            f"想要 {snap.get('total_wants', 0)} / 浏览 {snap.get('total_views', 0)}"
            + (f" · {ts}" if ts else ""),
        )

    def _sync_listings(self):
        if not self.main_window.is_licensed():
            QMessageBox.warning(self, "未激活", "请先在设置页面激活 License 后使用")
            return
        self.sync_btn.setEnabled(False)
        self.sync_btn.setText("同步中…")
        self.sync_status.setText("正在打开闲鱼并校验登录态…")
        self._sync_worker = _SyncListingWorker()
        self._sync_worker.progress_msg.connect(lambda m: self.sync_status.setText(m))
        self._sync_worker.finished.connect(self._on_sync_done)
        self._sync_worker.start()

    def _on_sync_done(self, result):
        self.sync_btn.setEnabled(True)
        self.sync_btn.setText("🔄 同步闲鱼在售")
        if result.get("ok"):
            self.sync_status.setText(
                f"✅ 同步完成：在售 {result.get('active_listings', 0)} 个，"
                f"想要 {result.get('total_wants', 0)}，浏览 {result.get('total_views', 0)}"
            )
            self._refresh_xianyu_card()
        else:
            err = result.get("error") or "未知错误"
            self.sync_status.setText(f"⚠️ 同步失败：{err}")
            QMessageBox.warning(self, "同步失败", f"未能同步闲鱼在售商品：\n{err}")

    @staticmethod
    def _pct(part, total):
        return f"{(part / total * 100):.0f}%" if total else "0%"
