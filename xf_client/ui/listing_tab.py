import webbrowser
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QLineEdit, QSpinBox, QTableWidget,
    QTableWidgetItem, QHeaderView, QMessageBox,
    QGroupBox, QDoubleSpinBox, QMenu,
)
from PyQt6.QtCore import QThread, pyqtSignal, Qt
from PyQt6.QtGui import QFont

from engine.xianyu_lister import XianyuLister
from engine.price_manager import PriceManager


GLOBAL_FONT_FAMILY = "Microsoft YaHei, PingFang SC, sans-serif"


class ListWorker(QThread):
    progress = pyqtSignal(int, int)
    finished = pyqtSignal(list)
    error = pyqtSignal(str)

    def __init__(self, items, price, price_markup_pct, stock, location, delay):
        super().__init__()
        self.items = items
        self.price = price
        self.price_markup_pct = price_markup_pct
        self.stock = stock
        self.location = location
        self.delay = delay
        self.lister = XianyuLister()

    def run(self):
        try:
            results = []
            total = len(self.items)
            for i, item in enumerate(self.items):
                result = self.lister.list_item(
                    item, self.price, self.price_markup_pct,
                    self.stock, self.location
                )
                results.append(result)
                self.progress.emit(i + 1, total)
            self.finished.emit(results)
        except Exception as e:
            self.error.emit(str(e))


class ListingTab(QWidget):
    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self.worker = None
        self.items = []
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        # 价格设置
        price_group = QGroupBox("💰 价格设置")
        price_group.setFont(QFont(GLOBAL_FONT_FAMILY, 13, QFont.Weight.Bold))
        price_layout = QVBoxLayout(price_group)
        price_layout.setSpacing(8)

        # 加价百分比
        markup_layout = QHBoxLayout()
        markup_layout.addWidget(QLabel("加价百分比(%):"))
        self.markup_percent = QDoubleSpinBox()
        self.markup_percent.setRange(0, 500)
        self.markup_percent.setValue(10)
        self.markup_percent.setSingleStep(5)
        self.markup_percent.setSuffix(" %")
        self.markup_percent.setMinimumHeight(36)
        markup_layout.addWidget(self.markup_percent)

        self.markup_btn = QPushButton("📈 批量加价")
        self.markup_btn.setMinimumHeight(36)
        self.markup_btn.clicked.connect(self._batch_markup)
        markup_layout.addWidget(self.markup_btn)
        markup_layout.addStretch()
        price_layout.addLayout(markup_layout)

        # 降价百分比
        reduce_layout = QHBoxLayout()
        reduce_layout.addWidget(QLabel("降价百分比(%):"))
        self.reduce_percent = QDoubleSpinBox()
        self.reduce_percent.setRange(1, 90)
        self.reduce_percent.setValue(10)
        self.reduce_percent.setSingleStep(5)
        self.reduce_percent.setSuffix(" %")
        self.reduce_percent.setMinimumHeight(36)
        reduce_layout.addWidget(self.reduce_percent)

        self.reduce_btn = QPushButton("💰 批量降价")
        self.reduce_btn.setMinimumHeight(36)
        self.reduce_btn.clicked.connect(self._batch_reduce)
        reduce_layout.addWidget(self.reduce_btn)
        reduce_layout.addStretch()
        price_layout.addLayout(reduce_layout)

        # 统一价格
        fixed_layout = QHBoxLayout()
        fixed_layout.addWidget(QLabel("统一价格:"))
        self.price_input = QDoubleSpinBox()
        self.price_input.setRange(0.01, 99999)
        self.price_input.setDecimals(2)
        self.price_input.setSpecialValueText("不使用")
        self.price_input.setMinimumHeight(36)
        fixed_layout.addWidget(self.price_input)

        self.fixed_btn = QPushButton("💲 统一设价")
        self.fixed_btn.setMinimumHeight(36)
        self.fixed_btn.clicked.connect(self._batch_fixed)
        fixed_layout.addWidget(self.fixed_btn)
        fixed_layout.addStretch()
        price_layout.addLayout(fixed_layout)

        layout.addWidget(price_group)

        # 上架设置
        settings_group = QGroupBox("📦 上架设置")
        settings_group.setFont(QFont(GLOBAL_FONT_FAMILY, 13, QFont.Weight.Bold))
        settings_layout = QVBoxLayout(settings_group)
        settings_layout.setSpacing(8)

        list_markup_layout = QHBoxLayout()
        list_markup_layout.addWidget(QLabel("上架加价(%):"))
        self.list_markup_percent = QDoubleSpinBox()
        self.list_markup_percent.setRange(0, 500)
        self.list_markup_percent.setValue(0)
        self.list_markup_percent.setSingleStep(5)
        self.list_markup_percent.setSuffix(" %")
        self.list_markup_percent.setMinimumHeight(36)
        list_markup_layout.addWidget(self.list_markup_percent)
        list_markup_layout.addStretch()
        settings_layout.addLayout(list_markup_layout)

        stock_layout = QHBoxLayout()
        stock_layout.addWidget(QLabel("库存:"))
        self.stock_input = QSpinBox()
        self.stock_input.setRange(1, 999)
        self.stock_input.setValue(1)
        self.stock_input.setMinimumHeight(36)
        stock_layout.addWidget(self.stock_input)
        stock_layout.addStretch()
        settings_layout.addLayout(stock_layout)

        loc_layout = QHBoxLayout()
        loc_layout.addWidget(QLabel("发货地:"))
        self.location_input = QLineEdit("全国")
        self.location_input.setMinimumHeight(36)
        loc_layout.addWidget(self.location_input)
        loc_layout.addStretch()
        settings_layout.addLayout(loc_layout)

        layout.addWidget(settings_group)

        # 商品列表
        self.table = QTableWidget()
        self.table.setColumnCount(8)
        self.table.setHorizontalHeaderLabels(["序号", "标题", "原价", "新价", "想要", "浏览", "来源", "状态"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_context_menu)
        self.table.cellDoubleClicked.connect(self._on_cell_double_clicked)
        layout.addWidget(self.table)

        # 按钮
        btn_layout = QHBoxLayout()
        self.list_btn = QPushButton("📦 批量上架")
        self.list_btn.setMinimumHeight(42)
        self.list_btn.setStyleSheet(
            "QPushButton { background: #1976D2; color: white; "
            "border-radius: 4px; padding: 8px 28px; font-size: 15px; font-weight: bold; }"
            "QPushButton:hover { background: #1565C0; }"
            "QPushButton:disabled { background: #bbb; }"
        )
        self.list_btn.clicked.connect(self._start_list)
        self.list_btn.setEnabled(False)
        btn_layout.addWidget(self.list_btn)
        btn_layout.addStretch()
        layout.addLayout(btn_layout)

    def refresh_items(self, items):
        self.list_btn.setEnabled(len(items) > 0)
        self._update_table(items)

    def _update_table(self, items):
        self.items = items
        self.table.setRowCount(len(items))
        for i, item in enumerate(items):
            self.table.setItem(i, 0, QTableWidgetItem(str(i + 1)))
            self.table.setItem(i, 1, QTableWidgetItem(item.get("ai_title", item.get("original_title", ""))))
            self.table.setItem(i, 2, QTableWidgetItem(f"¥{item.get('price', item.get('original_price', '0'))}"))
            new_price = item.get("new_price", "")
            self.table.setItem(i, 3, QTableWidgetItem(f"¥{new_price}" if new_price else ""))
            self.table.setItem(i, 4, QTableWidgetItem(item.get("wants", "0")))
            self.table.setItem(i, 5, QTableWidgetItem(item.get("views", "0")))
            link = item.get("link", "")
            link_display = "🔗 点击打开" if link else "-"
            link_item = QTableWidgetItem(link_display)
            link_item.setData(Qt.ItemDataRole.UserRole, link)
            link_item.setToolTip(link if link else "无来源链接")
            self.table.setItem(i, 6, link_item)
            self.table.setItem(i, 7, QTableWidgetItem("待上架"))

    def _show_context_menu(self, position):
        from PyQt6.QtWidgets import QApplication
        row = self.table.rowAt(position.y())
        if row < 0 or row >= len(self.items):
            return

        item = self.items[row]
        link = item.get("link", "")

        menu = QMenu(self)
        if link:
            open_action = menu.addAction("🔗 打开原链接")
            open_action.triggered.connect(lambda: webbrowser.open(link))
            copy_action = menu.addAction("📋 复制链接")
            copy_action.triggered.connect(lambda: QApplication.clipboard().setText(link))
            menu.addSeparator()
        view_action = menu.addAction("👁 查看详情")
        view_action.triggered.connect(lambda: self._show_item_detail(item))
        menu.exec(self.table.viewport().mapToGlobal(position))

    def _on_cell_double_clicked(self, row, column):
        if row < 0 or row >= len(self.items):
            return
        item = self.items[row]
        link = item.get("link", "")
        if link and column in [1, 6]:
            webbrowser.open(link)

    def _show_item_detail(self, item):
        title = item.get("ai_title") or item.get("original_title", "")
        link = item.get("link", "无")
        desc = item.get("description", "")[:200]
        seller = item.get("seller", "未知")

        msg = f"""<b>{title}</b><br><br>
<b>来源链接:</b> <a href='{link}'>{link}</a><br>
<b>卖家:</b> {seller}<br><br>
<b>描述:</b><br>{desc}..."""

        box = QMessageBox(self)
        box.setWindowTitle("商品详情")
        box.setTextFormat(Qt.TextFormat.RichText)
        box.setText(msg)
        box.setStandardButtons(QMessageBox.StandardButton.Ok)
        box.exec()

    def _batch_markup(self):
        items = self.main_window.get_items()
        if not items:
            QMessageBox.warning(self, "提示", "没有商品数据")
            return
        pm = PriceManager()
        items = pm.batch_markup_price(items, self.markup_percent.value())
        self.main_window.set_items(items)

    def _batch_reduce(self):
        items = self.main_window.get_items()
        if not items:
            QMessageBox.warning(self, "提示", "没有商品数据")
            return
        pm = PriceManager()
        items = pm.batch_reduce_price(items, self.reduce_percent.value())
        self.main_window.set_items(items)

    def _batch_fixed(self):
        items = self.main_window.get_items()
        if not items:
            QMessageBox.warning(self, "提示", "没有商品数据")
            return
        pm = PriceManager()
        items = pm.batch_set_price(items, self.price_input.value())
        self.main_window.set_items(items)

    def _start_list(self):
        if not self.main_window.is_licensed():
            QMessageBox.warning(self, "未激活", "请先在设置页面激活License后使用上架功能")
            return

        items = self.main_window.get_items()
        if not items:
            QMessageBox.warning(self, "提示", "没有商品数据")
            return

        price = str(self.price_input.value()) if self.price_input.value() > 0.01 else None
        markup_pct = self.list_markup_percent.value()

        self.worker = ListWorker(
            items, price, markup_pct,
            str(self.stock_input.value()),
            self.location_input.text(),
            5,
        )
        self.worker.progress.connect(self._on_progress)
        self.worker.finished.connect(self._on_finished)
        self.worker.error.connect(self._on_error)
        self.list_btn.setEnabled(False)
        self.worker.start()

    def _on_progress(self, current, total):
        self.main_window.statusBar().showMessage(f"上架中: {current}/{total}")

    def _on_finished(self, results):
        self.list_btn.setEnabled(True)
        success = sum(1 for r in results if r.get("success"))

        # 更新表格状态列
        for i, result in enumerate(results):
            if i < self.table.rowCount():
                if result.get("success"):
                    status = "✅ 上架成功"
                else:
                    err = result.get("error", "失败")[:20]
                    status = f"❌ {err}"
                self.table.setItem(i, 7, QTableWidgetItem(status))

        QMessageBox.information(self, "完成", f"上架完成: 成功 {success}, 失败 {len(results) - success}")

    def _on_error(self, msg):
        self.list_btn.setEnabled(True)
        QMessageBox.critical(self, "错误", f"上架失败: {msg}")
