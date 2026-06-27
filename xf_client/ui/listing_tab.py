"""商品上架 Tab（多渠道）

支持两个上架渠道，用户可自由选择：
  - 🐠 闲管家(goofish.pro)：第三方鱼小铺后台，需开通（多规格/深库存可能收费）。
  - 🐟 闲鱼官方(goofish.com)：闲鱼官方发布页，免费，扫码登录即可，按单价发布。

采集自各平台(1688/淘宝/京东/拼多多)的数据统一打包后，由对应渠道的上架器
自动填写发布表单。

功能:
  - 商品列表展示（含来源平台标识）
  - 上架渠道选择（闲管家 / 闲鱼官方）
  - 价格策略（加价%、降价%、固定售价）
  - 成色/库存等上架参数（闲管家专用）
  - dry-run（默认）：填完表单停在提交前，人工核对后再放开提交
  - 批量上架进度与结果
  - 单条价格管理（加价/降价/设价）
"""
import json
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QTableWidget, QTableWidgetItem, QHeaderView,
    QGroupBox, QMessageBox, QComboBox, QDoubleSpinBox,
    QProgressBar, QLineEdit, QTabWidget, QTextEdit,
    QSpinBox, QCheckBox,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QFont, QColor, QBrush

from engine.goofishpro_lister import GoofishProLister
from engine.xianyu_lister import XianyuLister
from engine.price_manager import PriceManager
from engine.product_package import ensure_full_product_package
from database.db_manager import db


GLOBAL_FONT_FAMILY = "Microsoft YaHei, PingFang SC, sans-serif"

# 采集来源平台展示（仅用于列表来源列标识，上架目标由渠道选择决定）
SOURCE_PLATFORM_DISPLAY = {
    "xianyu": "🐟 闲鱼",
    "pdd": "🛒 拼多多",
    "jd": "🏪 京东",
    "1688": "🏭 1688",
    "taobao": "🛍 淘宝",
    "goofishpro": "🐠 闲管家",
}

CONDITIONS = ["全新", "99新", "95新", "9新", "8新", "7新"]

# 上架渠道：闲管家(需开通/可能收费) 与 闲鱼官方(免费，扫码登录)
LISTING_CHANNELS = {
    "goofishpro": {"name": "🐠 闲管家", "lister": GoofishProLister,
                   "status": "listed_goofishpro", "login_hint": "闲管家"},
    "xianyu": {"name": "🐟 闲鱼官方", "lister": XianyuLister,
               "status": "listed_xianyu", "login_hint": "闲鱼"},
}


class ListingWorker(QThread):
    """上架 Worker — 按所选渠道打开一次浏览器，逐个填表（默认 dry-run）。"""
    progress_msg = pyqtSignal(str)
    item_done = pyqtSignal(int, bool, str)   # index, success, error
    finished = pyqtSignal(list)              # results list

    def __init__(self, items, price_mode, price_value,
                 stock=1, condition="全新", dry_run=True, channel="goofishpro"):
        super().__init__()
        self.items = items
        self.channel = channel
        self.price_mode = price_mode      # "markup" | "markdown" | "fixed"
        self.price_value = price_value    # float
        self.stock = stock
        self.condition = condition
        self.dry_run = dry_run
        self.results = []

    def _final_price(self, item) -> float:
        base = 0.0
        try:
            base = float(
                str(item.get("new_price") or item.get("price")
                    or item.get("original_price", "0")).replace(",", "").replace("¥", "")
            )
        except Exception:
            pass
        if self.price_mode == "markup":
            return round(base * (1 + self.price_value / 100), 2) if base else 0.0
        if self.price_mode == "markdown":
            return round(max(0.01, base * (1 - self.price_value / 100)), 2) if base else 0.0
        # fixed
        return round(self.price_value, 2) if self.price_value > 0 else base

    def run(self):
        def on_progress(msg):
            self.progress_msg.emit(msg)

        ch = LISTING_CHANNELS.get(self.channel, LISTING_CHANNELS["goofishpro"])
        ch_name = ch["name"]
        hint = ch["login_hint"]
        lister = ch["lister"](on_log=on_progress)
        opened = False
        try:
            on_progress(f"正在打开{hint}并校验登录态...")
            opened = lister.open()
            if not opened:
                for i in range(len(self.items)):
                    self.item_done.emit(i, False, f"{hint}登录失败")
                self.finished.emit([])
                return

            for i, item in enumerate(self.items):
                # 统一补齐为上架格式
                pkg = ensure_full_product_package(dict(item))
                price = self._final_price(item)
                pkg["price"] = price
                pkg["stock"] = pkg.get("stock") or self.stock
                pkg["condition"] = self.condition

                title = pkg.get("title") or pkg.get("original_title") or ""
                on_progress(f"[{i + 1}/{len(self.items)}] 上架到{ch_name}: {title[:30]}...")

                try:
                    result = lister.fill_product(pkg, dry_run=self.dry_run)
                    success = result.get("ok", False)
                    error = result.get("error", "")
                    self.results.append({**item, "list_result": result})
                    self.item_done.emit(i, success, error)
                    if success and not self.dry_run and item.get("db_id"):
                        db.update_product_status(item["db_id"], ch["status"])
                except Exception as e:
                    error_msg = str(e)
                    on_progress(f"  ✗ 上架失败: {error_msg[:80]}")
                    self.results.append({**item, "list_result": {"ok": False, "error": error_msg}})
                    self.item_done.emit(i, False, error_msg)
        finally:
            if opened:
                if self.dry_run:
                    on_progress(f"dry-run 完成：浏览器保持打开，请在{hint}核对后手动提交。")
                else:
                    lister.close()
        self.finished.emit(self.results)


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

        # ── 上架渠道选择 ──
        ch_row = QHBoxLayout()
        ch_label = QLabel("上架渠道:")
        ch_label.setStyleSheet("font-size: 13px; font-weight: bold; color: #00695c;")
        ch_row.addWidget(ch_label)
        self.channel_combo = QComboBox()
        self.channel_combo.setMinimumHeight(32)
        self.channel_combo.setMinimumWidth(180)
        for key, meta in LISTING_CHANNELS.items():
            self.channel_combo.addItem(meta["name"], key)
        self.channel_combo.currentIndexChanged.connect(self._on_channel_changed)
        ch_row.addWidget(self.channel_combo)
        self.channel_hint = QLabel("")
        self.channel_hint.setStyleSheet("color: #888; font-size: 12px; margin-left: 8px;")
        self.channel_hint.setWordWrap(True)
        ch_row.addWidget(self.channel_hint, 1)
        layout.addLayout(ch_row)

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
        self.table.setColumnWidth(5, 90)
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
        config_tabs.addTab(self._build_listing_config(), "📦 上架配置")
        config_tabs.addTab(self._build_price_config(), "💰 价格管理")
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

        # 触发一次初始渠道提示
        self._on_channel_changed()

    def _build_listing_config(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(8)

        # 价格策略
        row1 = QHBoxLayout()
        row1.addWidget(QLabel("价格策略:"))
        self.price_mode_combo = QComboBox()
        self.price_mode_combo.setMinimumHeight(34)
        self.price_mode_combo.addItem("加价 (%)", "markup")
        self.price_mode_combo.addItem("降价 (%)", "markdown")
        self.price_mode_combo.addItem("固定售价 (¥)", "fixed")
        row1.addWidget(self.price_mode_combo)

        self.price_spin = QDoubleSpinBox()
        self.price_spin.setRange(0, 99999)
        self.price_spin.setValue(10)
        self.price_spin.setSuffix(" %")
        self.price_spin.setDecimals(1)
        self.price_spin.setMinimumHeight(34)
        self.price_mode_combo.currentIndexChanged.connect(self._on_price_mode_changed)
        row1.addWidget(self.price_spin)
        row1.addStretch()
        layout.addLayout(row1)

        # 闲管家上架参数
        row2 = QHBoxLayout()
        row2.addWidget(QLabel("成色:"))
        self.condition_combo = QComboBox()
        self.condition_combo.setMinimumHeight(34)
        for c in CONDITIONS:
            self.condition_combo.addItem(c, c)
        row2.addWidget(self.condition_combo)
        row2.addSpacing(16)

        row2.addWidget(QLabel("默认库存:"))
        self.stock_spin = QSpinBox()
        self.stock_spin.setRange(1, 99999)
        self.stock_spin.setValue(1)
        self.stock_spin.setMinimumHeight(34)
        row2.addWidget(self.stock_spin)
        row2.addSpacing(16)

        self.dry_run_cb = QCheckBox("仅填写不提交（dry-run，推荐）")
        self.dry_run_cb.setChecked(True)
        row2.addWidget(self.dry_run_cb)
        row2.addStretch()
        layout.addLayout(row2)

        # 操作按钮
        btn_row = QHBoxLayout()
        self.list_btn = QPushButton("🚀 上架到闲管家（选中商品）")
        self.list_btn.setMinimumHeight(40)
        self.list_btn.setStyleSheet(
            "QPushButton { background: #00897b; color: white; border-radius: 4px; "
            "padding: 6px 24px; font-size: 14px; font-weight: bold; }"
            "QPushButton:hover { background: #00796b; }"
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

    def _on_channel_changed(self):
        key = self.channel_combo.currentData() or "goofishpro"
        if key == "goofishpro":
            self.channel_hint.setText(
                "闲管家(goofish.pro)：需开通鱼小铺，多规格/深库存可能收费。"
            )
            self.list_btn.setText("🚀 上架到闲管家（选中商品）")
            if hasattr(self, "condition_combo"):
                self.condition_combo.setEnabled(True)
            if hasattr(self, "stock_spin"):
                self.stock_spin.setEnabled(True)
        else:
            self.channel_hint.setText(
                "闲鱼官方(goofish.com)：免费发布，扫码登录即可；按单价发布，无成色字段。"
            )
            self.list_btn.setText("🚀 发布到闲鱼官方（选中商品）")
            if hasattr(self, "condition_combo"):
                self.condition_combo.setEnabled(False)
            if hasattr(self, "stock_spin"):
                self.stock_spin.setEnabled(False)

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

        price_mode = self.price_mode_combo.currentData()
        price_value = self.price_spin.value()
        stock = self.stock_spin.value()
        condition = self.condition_combo.currentData()
        dry_run = self.dry_run_cb.isChecked()
        channel = self.channel_combo.currentData() or "goofishpro"
        ch_name = LISTING_CHANNELS.get(channel, LISTING_CHANNELS["goofishpro"])["name"]

        msg = (
            f"即将上架 {len(selected)} 个商品到 {ch_name}\n\n"
            f"价格策略: {self.price_mode_combo.currentText()} {price_value}\n"
            f"成色: {condition}\n默认库存: {stock}\n"
            f"模式: {'dry-run（仅填写不提交）' if dry_run else '⚠️ 直接提交上架'}\n\n"
            "请确认继续？"
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
            selected, price_mode, price_value,
            stock=stock, condition=condition, dry_run=dry_run, channel=channel,
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
        pct = int(self._list_done / max(1, self._list_total) * 100)
        self.progress_bar.setValue(pct)

        row = index
        if row < self.table.rowCount():
            status_item = QTableWidgetItem("✅ 已填写" if success else f"❌ {error[:15]}")
            status_item.setForeground(
                QBrush(QColor("#2e7d32")) if success else QBrush(QColor("#c62828"))
            )
            self.table.setItem(row, 5, status_item)

    def _on_listing_finished(self, results: list):
        self._reset_list_ui()
        self._append_log(
            f"\n✅ 完成！成功 {self._list_success}/{self._list_total} 个"
        )
        QMessageBox.information(
            self, "完成",
            f"上架处理完成！\n成功: {self._list_success} 个\n"
            f"失败: {self._list_total - self._list_success} 个\n\n"
            "dry-run 模式下请到对应渠道的浏览器窗口核对后手动提交/发布。"
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
            updated_ids = {item.get("item_id"): item for item in updated}
            for i, item in enumerate(self.items):
                if item.get("item_id") in updated_ids:
                    self.items[i] = updated_ids[item["item_id"]]
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
        self.items = items
        self.table.setRowCount(len(items))

        for i, item in enumerate(items):
            cb = QCheckBox()
            cb.stateChanged.connect(self._update_selected_count)
            self.table.setCellWidget(i, 0, cb)

            platform = item.get("platform", "")
            platform_label = SOURCE_PLATFORM_DISPLAY.get(platform, platform)
            self.table.setItem(i, 1, QTableWidgetItem(platform_label))

            title = item.get("ai_title") or item.get("original_title") or item.get("title", "")
            self.table.setItem(i, 2, QTableWidgetItem(title[:60]))

            self.table.setItem(i, 3, QTableWidgetItem(str(item.get("original_price", ""))))

            new_price = item.get("new_price") or item.get("price") or item.get("original_price", "")
            self.table.setItem(i, 4, QTableWidgetItem(str(new_price)))

            status = item.get("status", "collected")
            status_map = {
                "collected": "待处理",
                "listed_goofishpro": "✅闲管家",
                "listed_xianyu": "✅闲鱼",
            }
            self.table.setItem(i, 5, QTableWidgetItem(status_map.get(status, status)))

            edit_btn = QPushButton("详情")
            edit_btn.setMinimumHeight(28)
            edit_btn.setStyleSheet(
                "QPushButton { background: #00897b; color: white; "
                "border-radius: 3px; padding: 2px 8px; font-size: 11px; }"
                "QPushButton:hover { background: #00796b; }"
            )
            edit_btn.clicked.connect(lambda checked, idx=i: self._show_detail(idx))
            self.table.setCellWidget(i, 6, edit_btn)

        self._update_selected_count()

    def _show_detail(self, index: int):
        if index >= len(self.items):
            return
        item = self.items[index]
        sku_list = item.get("sku_list") or []
        info = (
            f"商品ID: {item.get('item_id', '')}\n"
            f"来源平台: {SOURCE_PLATFORM_DISPLAY.get(item.get('platform'), item.get('platform', ''))}\n"
            f"原始标题: {item.get('original_title') or item.get('title', '')}\n"
            f"AI 标题: {item.get('ai_title', '（未改写）')}\n"
            f"原价: ¥{item.get('original_price', '')}\n"
            f"上架价: ¥{item.get('new_price') or item.get('price', '')}\n"
            f"图片数: {len(item.get('local_images', []) or item.get('main_images', []))}\n"
            f"SKU 数: {len(sku_list)}\n"
            f"状态: {item.get('status', '')}\n"
            f"来源链接: {item.get('source_url', '')}"
        )
        QMessageBox.information(self, "商品详情", info)
