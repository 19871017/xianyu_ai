"""经营概览 Tab：汇总本地库的商品/订单/复检数据，一眼看清经营状况。

数据全部来自本地数据库（已采集/已上架商品、已抓取订单、复检结果），
打开即时计算、100% 可靠，无需联网抓取。
"""
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel,
    QPushButton, QGroupBox, QFrame,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont

from engine.dashboard_stats import compute_dashboard
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
        layout.addLayout(top)

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

    @staticmethod
    def _pct(part, total):
        return f"{(part / total * 100):.0f}%" if total else "0%"
