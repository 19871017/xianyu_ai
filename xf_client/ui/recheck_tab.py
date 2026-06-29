"""源商品复检 Tab：重采已上架商品的源商品，对比价格/库存，产出风险告警。

防亏损用途：上游 1688/淘宝等涨价、改规格、售罄时及时发现，避免卖出后
拿不到货 / 亏本发货。复检结果落库，可查看历史告警。
"""
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QTableWidget, QTableWidgetItem, QHeaderView,
    QMessageBox, QProgressBar, QTextEdit, QCheckBox, QDoubleSpinBox,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QFont, QColor, QBrush

from engine.source_recheck import RecheckEngine
from database.db_manager import db


GLOBAL_FONT_FAMILY = "Microsoft YaHei, PingFang SC, sans-serif"

LEVEL_DISPLAY = {
    "critical": ("严重", "#c62828"),
    "warn": ("警告", "#e65100"),
    "info": ("提示", "#1565c0"),
    "none": ("正常", "#2e7d32"),
}


class RecheckWorker(QThread):
    """后台复检：按源平台重采商品并对比，落库后回传结果。"""
    progress_msg = pyqtSignal(str)
    item_done = pyqtSignal(int, int, dict)
    finished = pyqtSignal(list)

    def __init__(self, products, price_up_pct=10.0):
        super().__init__()
        self.products = products or []
        self.price_up_pct = price_up_pct

    def run(self):
        def log(msg):
            self.progress_msg.emit(msg)

        def on_item(done, total, row):
            try:
                db.save_recheck(row)
            except Exception as e:
                log(f"  复检结果落库失败：{e}")
            self.item_done.emit(done, total, row)

        try:
            engine = RecheckEngine(on_log=log)
            results = engine.recheck_products(
                self.products, price_up_pct=self.price_up_pct, on_item=on_item
            )
            self.finished.emit(results)
        except Exception as e:
            log(f"复检异常：{e}")
            self.finished.emit([])


class RecheckTab(QWidget):
    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self.worker = None
        self._setup_ui()
        self._load_history()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.setContentsMargins(8, 8, 8, 8)

        tip = QLabel(
            "源商品复检：重采已上架商品的上游源商品，对比采集时的价格/库存，"
            "及时发现涨价、售罄、规格下架等风险，防止卖出后亏本或缺货。\n"
            "仅复检有源平台与源链接的商品（1688/淘宝/京东/拼多多）。"
        )
        tip.setWordWrap(True)
        tip.setFont(QFont(GLOBAL_FONT_FAMILY, 12))
        tip.setStyleSheet("color:#00695c; background:#e0f2f1; padding:8px; border-radius:4px;")
        layout.addWidget(tip)

        ctrl = QHBoxLayout()
        self.recheck_btn = QPushButton("🔍 复检全部已上架商品")
        self.recheck_btn.setMinimumHeight(40)
        self.recheck_btn.setStyleSheet(
            "QPushButton { background:#00897b; color:white; border-radius:4px; "
            "padding:6px 20px; font-size:14px; font-weight:bold; }"
            "QPushButton:hover { background:#00695c; }"
            "QPushButton:disabled { background:#bbb; }"
        )
        self.recheck_btn.clicked.connect(self._start_recheck)
        ctrl.addWidget(self.recheck_btn)

        ctrl.addWidget(QLabel("涨价告警阈值:"))
        self.price_up_spin = QDoubleSpinBox()
        self.price_up_spin.setRange(1, 100)
        self.price_up_spin.setValue(10)
        self.price_up_spin.setSuffix(" %")
        self.price_up_spin.setMinimumHeight(32)
        ctrl.addWidget(self.price_up_spin)

        self.only_alert_cb = QCheckBox("只看有风险的")
        self.only_alert_cb.stateChanged.connect(self._load_history)
        ctrl.addWidget(self.only_alert_cb)
        ctrl.addStretch()
        layout.addLayout(ctrl)

        self.summary_label = QLabel("")
        self.summary_label.setFont(QFont(GLOBAL_FONT_FAMILY, 12, QFont.Weight.Bold))
        layout.addWidget(self.summary_label)

        self.table = QTableWidget()
        self.table.setColumnCount(7)
        self.table.setHorizontalHeaderLabels(
            ["风险", "商品", "源平台", "闲鱼售价", "采集价", "当前源价", "风险说明"]
        )
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(6, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(0, 56)
        self.table.setColumnWidth(2, 70)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self.table)

        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        self.progress_bar.setMinimumHeight(22)
        layout.addWidget(self.progress_bar)

        self.log_area = QTextEdit()
        self.log_area.setReadOnly(True)
        self.log_area.setMaximumHeight(120)
        self.log_area.setFont(QFont(GLOBAL_FONT_FAMILY, 12))
        layout.addWidget(self.log_area)

    # 主窗口刷新接口（与其它 tab 一致；复检数据自管，不需外部 items）。
    def refresh_items(self, items):
        pass

    def _start_recheck(self):
        if not self.main_window.is_licensed():
            QMessageBox.warning(self, "未激活", "请先激活License后使用复检功能")
            return
        try:
            products = db.get_all_products()
        except Exception:
            products = self.main_window.collected_items or []
        # 仅复检已上架的商品（有闲鱼售价、源链接才有意义）。
        targets = [
            p for p in products
            if (p.get("source_url") or "").strip()
            and (p.get("source_platform") or p.get("platform")) in ("1688", "taobao", "jd", "pdd")
        ]
        if not targets:
            QMessageBox.information(
                self, "无可复检商品",
                "没有带源链接的商品。请先采集（1688/淘宝/京东/拼多多）并上架后再复检。"
            )
            return

        msg = (
            f"将重采 {len(targets)} 个商品的上游源商品并对比价格/库存。\n\n"
            "复检会按源平台打开浏览器逐个重采，耗时与商品数相关，"
            "且依赖各平台登录态。确认开始？"
        )
        if QMessageBox.question(self, "确认复检", msg) != QMessageBox.StandardButton.Yes:
            return

        self.recheck_btn.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, len(targets))
        self.progress_bar.setValue(0)
        self.log_area.clear()
        self._append_log(f"开始复检 {len(targets)} 个商品…")

        self.worker = RecheckWorker(targets, price_up_pct=self.price_up_spin.value())
        self.worker.progress_msg.connect(self._append_log)
        self.worker.item_done.connect(self._on_item_done)
        self.worker.finished.connect(self._on_finished)
        self.worker.start()

    def _on_item_done(self, done, total, row):
        self.progress_bar.setValue(done)
        lv = row.get("level", "none")
        if lv != "none":
            self._append_log(f"  [{done}/{total}] {LEVEL_DISPLAY.get(lv, ('?',''))[0]}：{row.get('summary','')}")

    def _on_finished(self, results):
        self.progress_bar.setVisible(False)
        self.recheck_btn.setEnabled(True)
        crit = sum(1 for r in results if r.get("level") == "critical")
        warn = sum(1 for r in results if r.get("level") == "warn")
        self._append_log(
            f"\n✅ 复检完成：共 {len(results)} 个，严重 {crit} 个，警告 {warn} 个。"
        )
        if crit:
            QMessageBox.warning(
                self, "发现严重风险",
                f"复检发现 {crit} 个严重风险商品（亏本/售罄/下架），请尽快处理。"
            )
        self._load_history()

    def _load_history(self):
        try:
            rows = db.get_latest_rechecks(only_alert=self.only_alert_cb.isChecked())
        except Exception:
            rows = []
        self._fill_table(rows)
        crit = sum(1 for r in rows if r.get("level") == "critical")
        warn = sum(1 for r in rows if r.get("level") == "warn")
        if rows:
            self.summary_label.setText(
                f"最近复检：{len(rows)} 个商品　|　🔴 严重 {crit}　🟠 警告 {warn}"
            )
            self.summary_label.setStyleSheet(
                "color:#c62828;" if crit else ("color:#e65100;" if warn else "color:#2e7d32;")
            )
        else:
            self.summary_label.setText("暂无复检记录。点击「复检全部已上架商品」开始。")
            self.summary_label.setStyleSheet("color:#888;")

    def _fill_table(self, rows):
        self.table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            lv = r.get("level", "none")
            label, color = LEVEL_DISPLAY.get(lv, ("?", "#888"))
            lv_item = QTableWidgetItem(label)
            lv_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            lv_item.setForeground(QBrush(QColor(color)))
            self.table.setItem(i, 0, lv_item)
            self.table.setItem(i, 1, QTableWidgetItem(str(r.get("title", ""))[:50]))
            self.table.setItem(i, 2, QTableWidgetItem(str(r.get("platform", ""))))
            self.table.setItem(i, 3, QTableWidgetItem(f"¥{float(r.get('listing_price') or 0):.2f}"))
            self.table.setItem(i, 4, QTableWidgetItem(f"¥{float(r.get('old_min_price') or 0):.2f}"))
            new_p = QTableWidgetItem(f"¥{float(r.get('new_min_price') or 0):.2f}")
            if lv == "critical":
                new_p.setForeground(QBrush(QColor("#c62828")))
            self.table.setItem(i, 5, new_p)
            self.table.setItem(i, 6, QTableWidgetItem(str(r.get("summary", ""))))

    def _append_log(self, msg):
        self.log_area.append(msg)
        sb = self.log_area.verticalScrollBar()
        sb.setValue(sb.maximum())
