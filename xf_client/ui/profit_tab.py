"""选品打分 Tab：对本地库商品做利润测算 + 综合打分排序，辅助上架决策。

打分算法在服务端执行（engine.compute_client 调用云端接口），客户端不持有算法
源码，逆向本程序也拿不到实现。数据来自本地库，需联网 + 有效授权才能测算。
可调成本参数（运费 / 平台费率 / 其它成本）实时重算，亏损商品标红预警。
"""
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QDoubleSpinBox,
    QCheckBox, QComboBox,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QColor, QBrush

from engine.compute_client import ComputeClient, ComputeError
from database.db_manager import db


GLOBAL_FONT_FAMILY = "Microsoft YaHei, PingFang SC, sans-serif"

GRADE_COLOR = {
    "A": "#2e7d32",
    "B": "#1565c0",
    "C": "#e65100",
    "D": "#c62828",
}


class ProfitTab(QWidget):
    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self.ranked = []
        self._compute = ComputeClient(main_window.license_validator)
        self._setup_ui()
        self.refresh_items(None)

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.setContentsMargins(8, 8, 8, 8)

        tip = QLabel(
            "选品打分：对已采集商品做利润测算（净利润 = 售价 − 源价 − 运费 − 平台费）"
            "并综合净利率/需求热度/加价空间/多规格/库存打分（0-100，A/B/C/D 分级），"
            "辅助决定先上哪些。亏损商品标红预警。\n"
            "源价取 SKU 最低价，售价取设定的闲鱼售价；未设价的商品按「目标加价率」推算售价做潜力评估。"
        )
        tip.setWordWrap(True)
        tip.setFont(QFont(GLOBAL_FONT_FAMILY, 12))
        tip.setStyleSheet("color:#4527a0; background:#ede7f6; padding:8px; border-radius:4px;")
        layout.addWidget(tip)

        ctrl = QHBoxLayout()
        ctrl.addWidget(QLabel("默认运费:"))
        self.ship_spin = QDoubleSpinBox()
        self.ship_spin.setRange(0, 1000)
        self.ship_spin.setValue(5.0)
        self.ship_spin.setPrefix("¥ ")
        self.ship_spin.setMinimumHeight(30)
        self.ship_spin.valueChanged.connect(self.refresh_items)
        ctrl.addWidget(self.ship_spin)

        ctrl.addWidget(QLabel("平台费率:"))
        self.fee_spin = QDoubleSpinBox()
        self.fee_spin.setRange(0, 30)
        self.fee_spin.setValue(0.6)
        self.fee_spin.setSuffix(" %")
        self.fee_spin.setMinimumHeight(30)
        self.fee_spin.valueChanged.connect(self.refresh_items)
        ctrl.addWidget(self.fee_spin)

        ctrl.addWidget(QLabel("其它成本:"))
        self.extra_spin = QDoubleSpinBox()
        self.extra_spin.setRange(0, 1000)
        self.extra_spin.setValue(0.0)
        self.extra_spin.setPrefix("¥ ")
        self.extra_spin.setMinimumHeight(30)
        self.extra_spin.valueChanged.connect(self.refresh_items)
        ctrl.addWidget(self.extra_spin)

        ctrl.addWidget(QLabel("目标加价率:"))
        self.target_spin = QDoubleSpinBox()
        self.target_spin.setRange(0, 500)
        self.target_spin.setValue(50.0)
        self.target_spin.setSuffix(" %")
        self.target_spin.setMinimumHeight(30)
        self.target_spin.setToolTip("未设价(售价=源价)的商品按此加价率推算售价做潜力评估；设0则不推算")
        self.target_spin.valueChanged.connect(self.refresh_items)
        ctrl.addWidget(self.target_spin)

        ctrl.addWidget(QLabel("筛选:"))
        self.filter_combo = QComboBox()
        self.filter_combo.addItems(["全部", "仅 A/B 优选", "仅亏损预警"])
        self.filter_combo.currentIndexChanged.connect(self._fill_table)
        self.filter_combo.setMinimumHeight(30)
        ctrl.addWidget(self.filter_combo)

        self.refresh_btn = QPushButton("🔄 重新测算")
        self.refresh_btn.setMinimumHeight(32)
        self.refresh_btn.setStyleSheet(
            "QPushButton { background:#5e35b1; color:white; border-radius:4px; "
            "padding:4px 16px; font-size:13px; }"
            "QPushButton:hover { background:#4527a0; }"
        )
        self.refresh_btn.clicked.connect(lambda: self.refresh_items(None))
        ctrl.addWidget(self.refresh_btn)
        ctrl.addStretch()
        layout.addLayout(ctrl)

        self.summary_label = QLabel("")
        self.summary_label.setFont(QFont(GLOBAL_FONT_FAMILY, 12, QFont.Weight.Bold))
        layout.addWidget(self.summary_label)

        self.table = QTableWidget()
        self.table.setColumnCount(9)
        self.table.setHorizontalHeaderLabels(
            ["评分", "等级", "商品", "源价", "售价", "净利润", "净利率", "需求", "说明"]
        )
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        layout.addWidget(self.table)

    # 主窗口刷新接口；items 不使用，数据自库中取，打分由服务端计算。
    def refresh_items(self, items=None):
        try:
            products = db.get_all_products()
        except Exception:
            products = []
        if not products:
            self.ranked = []
            self._fill_table()
            return
        try:
            self.ranked = self._compute.rank_products(
                products,
                shipping_cost=self.ship_spin.value(),
                platform_fee_pct=self.fee_spin.value(),
                extra_cost=self.extra_spin.value(),
                target_markup_pct=self.target_spin.value(),
            )
        except ComputeError as e:
            self.ranked = []
            self._show_compute_error(str(e))
            self._fill_table()
            return
        self._fill_table()

    def _show_compute_error(self, msg: str):
        self.summary_label.setText(f"⚠️ 选品打分需联网授权：{msg}")
        self.summary_label.setStyleSheet("color:#c62828;")

    def _visible_rows(self):
        mode = self.filter_combo.currentIndex()
        if mode == 1:
            return [r for r in self.ranked if r["grade"] in ("A", "B")]
        if mode == 2:
            return [r for r in self.ranked if not r["profit"]["profitable"]]
        return self.ranked

    def _fill_table(self):
        rows = self._visible_rows()
        self.table.setRowCount(len(rows))
        loss = sum(1 for r in self.ranked if not r["profit"]["profitable"])
        a_cnt = sum(1 for r in self.ranked if r["grade"] == "A")
        b_cnt = sum(1 for r in self.ranked if r["grade"] == "B")
        for i, r in enumerate(rows):
            p = r["product"]
            pf = r["profit"]
            sig = r["signals"]

            score_item = QTableWidgetItem(f"{r['score']:.0f}")
            score_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(i, 0, score_item)

            grade_item = QTableWidgetItem(r["grade"])
            grade_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            grade_item.setForeground(QBrush(QColor(GRADE_COLOR.get(r["grade"], "#666"))))
            grade_item.setFont(QFont(GLOBAL_FONT_FAMILY, 12, QFont.Weight.Bold))
            self.table.setItem(i, 1, grade_item)

            title = p.get("ai_title") or p.get("title") or p.get("original_title") or "(无标题)"
            self.table.setItem(i, 2, QTableWidgetItem(str(title)[:40]))
            self.table.setItem(i, 3, QTableWidgetItem(f"¥{pf['source_price']:.2f}"))
            self.table.setItem(i, 4, QTableWidgetItem(f"¥{pf['sell_price']:.2f}"))

            np_item = QTableWidgetItem(f"¥{pf['net_profit']:.2f}")
            if not pf["profitable"]:
                np_item.setForeground(QBrush(QColor("#c62828")))
            self.table.setItem(i, 5, np_item)
            self.table.setItem(i, 6, QTableWidgetItem(f"{pf['net_margin_pct']:.1f}%"))

            want = sig.get("wants")
            self.table.setItem(i, 7, QTableWidgetItem("-" if want is None else str(want)))
            self.table.setItem(i, 8, QTableWidgetItem("；".join(r["reasons"])))

        self.summary_label.setText(
            f"共 {len(self.ranked)} 个商品　|　🟢 A {a_cnt}　🔵 B {b_cnt}　🔴 亏损预警 {loss}"
            f"　|　当前显示 {len(rows)} 个"
        )
        self.summary_label.setStyleSheet("color:#c62828;" if loss else "color:#2e7d32;")
