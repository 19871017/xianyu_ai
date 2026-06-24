"""多平台商品上架 Tab

支持上架目标平台:
  - 🐟 闲鱼 (XianyuLister)
  - 🛒 拼多多 (PddLister)
  - 🏪 京东 (JDLister)
  - 🏭 阿里巴巴/1688 (AlibabaLister)

功能:
  - 商品列表展示（含来源平台标识）
  - 目标上架平台选择
  - 价格策略（加价%、固定价、降价%）
  - 平台特有参数（库存/MOQ等）
  - 批量上架进度与结果
  - 单条价格管理（加价/降价/设价）
"""
import json
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QTableWidget, QTableWidgetItem, QHeaderView,
    QGroupBox, QMessageBox, QComboBox, QDoubleSpinBox,
    QProgressBar, QLineEdit, QFrame, QTabWidget, QTextEdit,
    QSpinBox, QCheckBox, QAbstractItemView,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QFont, QColor, QBrush

from engine.xianyu_lister import XianyuLister
from engine.price_manager import PriceManager
from database.db_manager import db


GLOBAL_FONT_FAMILY = "Microsoft YaHei, PingFang SC, sans-serif"

PLATFORM_DISPLAY = {
    "xianyu": "🐟 闲鱼",
    "pdd": "🛒 拼多多",
    "jd": "🏪 京东",
    "1688": "🏭 1688",
}

TARGET_PLATFORMS = [
    ("🐟 闲鱼", "xianyu"),
    ("🛒 拼多多", "pdd"),
    ("🏪 京东", "jd"),
    ("🏭 阿里巴巴(1688)", "1688"),
]


class ListingWorker(QThread):
    """上架 Worker — 根据 target_platform 选择对应 Lister"""
    progress_msg = pyqtSignal(str)
    item_done = pyqtSignal(int, bool, str)   # index, success, error
    finished = pyqtSignal(list)              # results list

    def __init__(self, items, target_platform, price_mode,
                 price_value, stock=999, moq=1, category=""):
        super().__init__()
        self.items = items
        self.target_platform = target_platform
        self.price_mode = price_mode      # "markup" | "fixed" | "markdown"
        self.price_value = price_value    # float
        self.stock = stock
        self.moq = moq
        self.category = category
        self.results = []

    def run(self):
        def on_progress(msg):
            self.progress_msg.emit(msg)

        target = self.target_platform

        for i, item in enumerate(self.items):
            # 计算最终价格
            base_price = 0.0
            try:
                base_price = float(
                    str(item.get("new_price") or item.get("original_price", "0"))
                    .replace(",", "")
                )
            except Exception:
                pass

            if self.price_mode == "markup":
                final_price = f"{base_price * (1 + self.price_value / 100):.2f}" if base_price else None
                markup_pct = self.price_value
            elif self.price_mode == "markdown":
                final_price = f"{max(0.01, base_price * (1 - self.price_value / 100)):.2f}" if base_price else None
                markup_pct = -self.price_value
            else:  # fixed
                final_price = f"{self.price_value:.2f}" if self.price_value > 0 else None
                markup_pct = 0

            on_progress(f"[{i + 1}/{len(self.items)}] 正在上架到 {PLATFORM_DISPLAY.get(target, target)}: "
                        f"{(item.get('ai_title') or item.get('original_title', ''))[:30]}...")

            try:
                if target == "xianyu":
                    lister = XianyuLister(on_progress=on_progress)
                    result = lister.list_item(
                        item,
                        price=final_price,
                        price_markup_pct=markup_pct,
                        wait_login=(i == 0),
                    )

                elif target == "pdd":
                    from engine.pdd_lister import PddLister
                    lister = PddLister(on_progress=on_progress)
                    result = lister.list_item(
                        item,
                        price=final_price,
                        price_markup_pct=markup_pct,
                        stock=self.stock,
                        wait_login=(i == 0),
                    )

                elif target == "jd":
                    from engine.jd_lister import JDLister
                    lister = JDLister(on_progress=on_progress)
                    result = lister.list_item(
                        item,
                        price=final_price,
                        price_markup_pct=markup_pct,
                        stock=self.stock,
                        category=self.category,
                        wait_login=(i == 0),
                    )

                elif target == "1688":
                    from engine.alibaba_lister import AlibabaLister
                    lister = AlibabaLister(on_progress=on_progress)
                    result = lister.list_item(
                        item,
                        price=final_price,
                        price_markup_pct=markup_pct,
                        stock=self.stock,
                        moq=self.moq,
                        wait_login=(i == 0),
                    )

                else:
                    result = {"success": False, "error": f"不支持的上架平台: {target}"}

                success = result.get("success", False)
                error = result.get("error", "")
                self.results.append({**item, "list_result": result})
                self.item_done.emit(i, success, error)

                # 更新数据库状态
                if success and item.get("db_id"):
                    db.update_product_status(
                        item["db_id"],
                        f"listed_{target}"
                    )

            except Exception as e:
                error_msg = str(e)
                on_progress(f"  ✗ 上架失败: {error_msg[:80]}")
                self.results.append({**item, "list_result": {"success": False, "error": error_msg}})
                self.item_done.emit(i, False, error_msg)

        self.finished.emit(self.results)


class PriceWorker(QThread):
    """批量价格调整 Worker"""
    progress_msg = pyqtSignal(str)
    finished = pyqtSignal(list)

    def __init__(self, items, mode, value):
        super().__init__()
        self.items = items
        self.mode = mode
        self.value = value

    def run(self):
        pm = PriceManager()
        updated = pm.batch_adjust(
            self.items,
            mode=self.mode,
            value=self.value,
            on_progress=lambda m: self.progress_msg.emit(m),
        )
        self.finished.emit(updated)


class ListingTab(QWidget):
    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self.items = []
        self.worker = None
        self._setup_ui()

    # ──────────────────────── UI 构建 ────────────────────────

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.setContentsMargins(8, 8, 8, 8)

        # ── 商品列表 ──
        table_group = QGroupBox("商品列表")
        table_group.setFont(QFont(GLOBAL_FONT_FAMILY, 13, QFont.Weight.Bold))
        table_layout = QVBoxLayout(table_group)

        self.table = QTableWidget()
        self.table.setColumnCount(7)
        self.table.setHorizontalHeaderLabels([
            "✓", "来源平台", "商品名称", "原价", "上架价", "状态", "操作"
        ])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(0, 36)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(1, 90)
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(5, 80)
        self.table.horizontalHeader().setSectionResizeMode(6, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(6, 70)
        self.table.setFont(QFont(GLOBAL_FONT_FAMILY, 12))
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table_layout.addWidget(self.table)

        # 全选/取消 行
        sel_layout = QHBoxLayout()
        self.select_all_btn = QPushButton("全选")
        self.select_all_btn.setMinimumHeight(30)
        self.select_all_btn.clicked.connect(self._select_all)
        sel_layout.addWidget(self.select_all_btn)
        self.deselect_btn = QPushButton("取消全选")
        self.deselect_btn.setMinimumHeight(30)
        self.deselect_btn.clicked.connect(self._deselect_all)
        sel_layout.addWidget(self.deselect_btn)
        self.selected_count_label = QLabel("已选: 0")
        self.selected_count_label.setStyleSheet("color: #555; font-size: 12px; margin-left: 8px;")
        sel_layout.addWidget(self.selected_count_label)
        sel_layout.addStretch()
        table_layout.addLayout(sel_layout)
        layout.addWidget(table_group)

        # ── 配置区 Tab ──
        config_tabs = QTabWidget()
        config_tabs.setFont(QFont(GLOBAL_FONT_FAMILY, 12))
        config_tabs.setMaximumHeight(230)

        # Tab1: 上架配置
        list_config = self._build_listing_config()
        config_tabs.addTab(list_config, "📦 上架配置")

        # Tab2: 价格管理
        price_config = self._build_price_config()
        config_tabs.addTab(price_config, "💰 价格管理")

        layout.addWidget(config_tabs)

        # ── 进度 & 日志 ──
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setVisible(False)
        self.progress_bar.setMinimumHeight(24)
        layout.addWidget(self.progress_bar)

        self.log_area = QTextEdit()
        self.log_area.setReadOnly(True)
        self.log_area.setMaximumHeight(120)
        self.log_area.setFont(QFont(GLOBAL_FONT_FAMILY, 12))
        layout.addWidget(self.log_area)

    def _build_listing_config(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(8)

        # 目标上架平台
        row1 = QHBoxLayout()
        row1.addWidget(QLabel("上架到:"))
        self.target_platform_combo = QComboBox()
        self.target_platform_combo.setMinimumHeight(34)
        self.target_platform_combo.setFont(QFont(GLOBAL_FONT_FAMILY, 13))
        for label, val in TARGET_PLATFORMS:
            self.target_platform_combo.addItem(label, val)
        self.target_platform_combo.currentIndexChanged.connect(self._on_target_platform_changed)
        row1.addWidget(self.target_platform_combo)
        row1.addSpacing(16)

        # 价格模式
        row1.addWidget(QLabel("价格策略:"))
        self.price_mode_combo = QComboBox()
        self.price_mode_combo.setMinimumHeight(34)
        self.price_mode_combo.addItem("加价 (%)", "markup")
        self.price_mode_combo.addItem("降价 (%)", "markdown")
        self.price_mode_combo.addItem("固定售价 (¥)", "fixed")
        row1.addWidget(self.price_mode_combo)

        self.price_spin = QDoubleSpinBox()
        self.price_spin.setRange(0, 9999)
        self.price_spin.setValue(10)
        self.price_spin.setSuffix(" %")
        self.price_spin.setDecimals(1)
        self.price_spin.setMinimumHeight(34)
        self.price_mode_combo.currentIndexChanged.connect(self._on_price_mode_changed)
        row1.addWidget(self.price_spin)
        row1.addStretch()
        layout.addLayout(row1)

        # 平台特有参数行
        row2 = QHBoxLayout()
        self.stock_label = QLabel("库存:")
        self.stock_spin = QSpinBox()
        self.stock_spin.setRange(1, 99999)
        self.stock_spin.setValue(999)
        self.stock_spin.setMinimumHeight(34)
        row2.addWidget(self.stock_label)
        row2.addWidget(self.stock_spin)

        self.moq_label = QLabel("起订量(1688):")
        self.moq_spin = QSpinBox()
        self.moq_spin.setRange(1, 9999)
        self.moq_spin.setValue(1)
        self.moq_spin.setMinimumHeight(34)
        self.moq_label.setVisible(False)
        self.moq_spin.setVisible(False)
        row2.addSpacing(12)
        row2.addWidget(self.moq_label)
        row2.addWidget(self.moq_spin)

        self.category_label = QLabel("商品类目(京东/可选):")
        self.category_input = QLineEdit()
        self.category_input.setPlaceholderText("如：手机")
        self.category_input.setMinimumHeight(34)
        self.category_input.setMaximumWidth(120)
        self.category_label.setVisible(False)
        self.category_input.setVisible(False)
        row2.addSpacing(12)
        row2.addWidget(self.category_label)
        row2.addWidget(self.category_input)
        row2.addStretch()
        layout.addLayout(row2)

        # 操作按钮
        btn_row = QHBoxLayout()
        self.list_btn = QPushButton("🚀 批量上架（选中商品）")
        self.list_btn.setMinimumHeight(40)
        self.list_btn.setStyleSheet(
            "QPushButton { background: #1976D2; color: white; border-radius: 4px; "
            "padding: 6px 24px; font-size: 14px; font-weight: bold; }"
            "QPushButton:hover { background: #1565C0; }"
            "QPushButton:disabled { background: #bbb; }"
        )
        self.list_btn.clicked.connect(self._start_listing)

        self.cancel_list_btn = QPushButton("⏹ 停止")
        self.cancel_list_btn.setMinimumHeight(40)
        self.cancel_list_btn.setStyleSheet(
            "QPushButton { background: #e53935; color: white; border-radius: 4px; "
            "padding: 6px 20px; font-size: 14px; }"
        )
        self.cancel_list_btn.clicked.connect(self._cancel_listing)
        self.cancel_list_btn.setVisible(False)

        btn_row.addWidget(self.list_btn)
        btn_row.addWidget(self.cancel_list_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        layout.addStretch()
        return widget

    def _build_price_config(self) -> QWidget:
        """价格管理面板"""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(8)

        row = QHBoxLayout()
        row.addWidget(QLabel("调价方式:"))
        self.pm_mode_combo = QComboBox()
        self.pm_mode_combo.setMinimumHeight(34)
        self.pm_mode_combo.addItem("加价 (%)", "markup_pct")
        self.pm_mode_combo.addItem("降价 (%)", "markdown_pct")
        self.pm_mode_combo.addItem("统一设价 (¥)", "set_price")
        self.pm_mode_combo.addItem("固定金额降价 (¥)", "fixed_reduce")
        row.addWidget(self.pm_mode_combo)

        self.pm_value_spin = QDoubleSpinBox()
        self.pm_value_spin.setRange(0, 99999)
        self.pm_value_spin.setValue(10)
        self.pm_value_spin.setSuffix(" %")
        self.pm_value_spin.setDecimals(1)
        self.pm_value_spin.setMinimumHeight(34)
        self.pm_mode_combo.currentIndexChanged.connect(self._on_pm_mode_changed)
        row.addWidget(self.pm_value_spin)

        self.pm_apply_btn = QPushButton("💰 批量调价（选中商品）")
        self.pm_apply_btn.setMinimumHeight(34)
        self.pm_apply_btn.setStyleSheet(
            "QPushButton { background: #388E3C; color: white; border-radius: 4px; "
            "padding: 4px 20px; font-size: 13px; font-weight: bold; }"
            "QPushButton:hover { background: #2E7D32; }"
        )
        self.pm_apply_btn.clicked.connect(self._apply_price)
        row.addWidget(self.pm_apply_btn)
        row.addStretch()
        layout.addLayout(row)

        layout.addStretch()
        return widget

    # ──────────────────────── 事件处理 ────────────────────────

    def _on_target_platform_changed(self):
        """切换目标上架平台时显示/隐藏平台特有参数"""
        platform = self.target_platform_combo.currentData()
        self.moq_label.setVisible(platform == "1688")
        self.moq_spin.setVisible(platform == "1688")
        self.category_label.setVisible(platform == "jd")
        self.category_input.setVisible(platform == "jd")
        # 闲鱼不需要库存输入
        self.stock_label.setVisible(platform != "xianyu")
        self.stock_spin.setVisible(platform != "xianyu")

    def _on_price_mode_changed(self):
        mode = self.price_mode_combo.currentData()
        if mode == "fixed":
            self.price_spin.setSuffix(" ¥")
            self.price_spin.setValue(0)
        else:
            self.price_spin.setSuffix(" %")
            self.price_spin.setValue(10)

    def _on_pm_mode_changed(self):
        mode = self.pm_mode_combo.currentData()
        if mode in ("set_price", "fixed_reduce"):
            self.pm_value_spin.setSuffix(" ¥")
        else:
            self.pm_value_spin.setSuffix(" %")

    def _select_all(self):
        for row in range(self.table.rowCount()):
            cb = self.table.cellWidget(row, 0)
            if cb:
                cb.setChecked(True)
        self._update_selected_count()

    def _deselect_all(self):
        for row in range(self.table.rowCount()):
            cb = self.table.cellWidget(row, 0)
            if cb:
                cb.setChecked(False)
        self._update_selected_count()

    def _update_selected_count(self):
        count = sum(
            1 for row in range(self.table.rowCount())
            if self.table.cellWidget(row, 0) and self.table.cellWidget(row, 0).isChecked()
        )
        self.selected_count_label.setText(f"已选: {count}")

    def _get_selected_items(self) -> list:
        selected = []
        for row in range(self.table.rowCount()):
            cb = self.table.cellWidget(row, 0)
            if cb and cb.isChecked():
                if row < len(self.items):
                    selected.append(self.items[row])
        return selected

    # ──────────────────────── 上架逻辑 ────────────────────────

    def _start_listing(self):
        if not self.main_window.is_licensed():
            QMessageBox.warning(self, "未激活", "请先激活License后使用上架功能")
            return

        selected = self._get_selected_items()
        if not selected:
            QMessageBox.warning(self, "提示", "请先勾选要上架的商品")
            return

        target = self.target_platform_combo.currentData()
        target_name = self.target_platform_combo.currentText()
        price_mode = self.price_mode_combo.currentData()
        price_value = self.price_spin.value()
        stock = self.stock_spin.value()
        moq = self.moq_spin.value()
        category = self.category_input.text().strip()

        msg = (
            f"即将上架 {len(selected)} 个商品到 {target_name}\n\n"
            f"价格策略: {self.price_mode_combo.currentText()} {price_value}\n"
            + (f"库存: {stock}\n" if target != "xianyu" else "")
            + (f"起订量: {moq}\n" if target == "1688" else "")
            + (f"类目: {category}\n" if category else "")
            + "\n请确认继续？"
        )
        reply = QMessageBox.question(self, "确认上架", msg)
        if reply != QMessageBox.StandardButton.Yes:
            return

        self.list_btn.setEnabled(False)
        self.cancel_list_btn.setVisible(True)
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        self.log_area.clear()

        self._list_total = len(selected)
        self._list_done = 0
        self._list_success = 0

        self.worker = ListingWorker(
            selected, target, price_mode, price_value,
            stock=stock, moq=moq, category=category
        )
        self.worker.progress_msg.connect(self._on_list_progress)
        self.worker.item_done.connect(self._on_item_done)
        self.worker.finished.connect(self._on_listing_finished)
        self.worker.start()

    def _cancel_listing(self):
        if self.worker and self.worker.isRunning():
            self.worker.terminate()
        self._reset_list_ui()
        self._append_log("⏹ 上架已中止")

    def _on_list_progress(self, msg: str):
        self._append_log(msg)

    def _on_item_done(self, index: int, success: bool, error: str):
        self._list_done += 1
        if success:
            self._list_success += 1
        pct = int(self._list_done / self._list_total * 100)
        self.progress_bar.setValue(pct)

        # 更新表格状态列
        row = index
        if row < self.table.rowCount():
            status_item = QTableWidgetItem("✅ 成功" if success else f"❌ {error[:15]}")
            status_item.setForeground(
                QBrush(QColor("#2e7d32")) if success else QBrush(QColor("#c62828"))
            )
            self.table.setItem(row, 5, status_item)

    def _on_listing_finished(self, results: list):
        self._reset_list_ui()
        self._append_log(
            f"\n✅ 上架完成！成功 {self._list_success}/{self._list_total} 个"
        )
        QMessageBox.information(
            self, "完成",
            f"上架完成！\n成功: {self._list_success} 个\n失败: {self._list_total - self._list_success} 个"
        )

    def _reset_list_ui(self):
        self.list_btn.setEnabled(True)
        self.cancel_list_btn.setVisible(False)
        self.progress_bar.setVisible(False)
        self.worker = None

    # ──────────────────────── 价格管理 ────────────────────────

    def _apply_price(self):
        selected = self._get_selected_items()
        if not selected:
            QMessageBox.warning(self, "提示", "请先勾选要调价的商品")
            return

        mode = self.pm_mode_combo.currentData()
        value = self.pm_value_spin.value()

        try:
            pm = PriceManager()
            updated = pm.batch_adjust(selected, mode=mode, value=value)
            # 同步回主数据
            updated_ids = {item.get("item_id"): item for item in updated}
            for i, item in enumerate(self.items):
                if item.get("item_id") in updated_ids:
                    self.items[i] = updated_ids[item["item_id"]]
                    # 持久化
                    if item.get("db_id"):
                        db.save_product(self.items[i])
            self.refresh_items(self.items)
            self._append_log(f"✅ 已对 {len(selected)} 个商品完成调价")
        except Exception as e:
            QMessageBox.critical(self, "错误", f"调价失败: {e}")

    # ──────────────────────── 工具方法 ────────────────────────

    def _append_log(self, msg: str):
        self.log_area.append(msg)
        sb = self.log_area.verticalScrollBar()
        sb.setValue(sb.maximum())

    def refresh_items(self, items: list):
        """刷新商品列表"""
        self.items = items
        self.table.setRowCount(len(items))

        for i, item in enumerate(items):
            # 勾选框
            cb = QCheckBox()
            cb.stateChanged.connect(self._update_selected_count)
            self.table.setCellWidget(i, 0, cb)

            # 来源平台
            platform = item.get("platform", "xianyu")
            platform_label = PLATFORM_DISPLAY.get(platform, platform)
            platform_item = QTableWidgetItem(platform_label)
            self.table.setItem(i, 1, platform_item)

            # 商品名称（优先 AI 标题）
            title = item.get("ai_title") or item.get("original_title") or item.get("title", "")
            self.table.setItem(i, 2, QTableWidgetItem(title[:60]))

            # 原价
            self.table.setItem(i, 3, QTableWidgetItem(str(item.get("original_price", ""))))

            # 上架价
            new_price = item.get("new_price") or item.get("original_price", "")
            self.table.setItem(i, 4, QTableWidgetItem(str(new_price)))

            # 状态
            status = item.get("status", "collected")
            status_map = {
                "collected": "待处理",
                "listed_xianyu": "✅闲鱼",
                "listed_pdd": "✅拼多多",
                "listed_jd": "✅京东",
                "listed_1688": "✅1688",
            }
            status_text = status_map.get(status, status)
            self.table.setItem(i, 5, QTableWidgetItem(status_text))

            # 操作按钮
            edit_btn = QPushButton("详情")
            edit_btn.setMinimumHeight(28)
            edit_btn.setStyleSheet(
                "QPushButton { background: #1976D2; color: white; "
                "border-radius: 3px; padding: 2px 8px; font-size: 11px; }"
                "QPushButton:hover { background: #1565C0; }"
            )
            edit_btn.clicked.connect(lambda checked, idx=i: self._show_detail(idx))
            self.table.setCellWidget(i, 6, edit_btn)

        self._update_selected_count()

    def _show_detail(self, index: int):
        """显示商品详情（只读）"""
        if index >= len(self.items):
            return
        item = self.items[index]
        info = (
            f"商品ID: {item.get('item_id', '')}\n"
            f"来源平台: {PLATFORM_DISPLAY.get(item.get('platform'), item.get('platform', ''))}\n"
            f"原始标题: {item.get('original_title', '')}\n"
            f"AI 标题: {item.get('ai_title', '（未改写）')}\n"
            f"原价: ¥{item.get('original_price', '')}\n"
            f"上架价: ¥{item.get('new_price', '')}\n"
            f"图片数: {len(item.get('local_images', []))}\n"
            f"状态: {item.get('status', '')}\n"
            f"来源链接: {item.get('source_url', '')}"
        )
        QMessageBox.information(self, "商品详情", info)
