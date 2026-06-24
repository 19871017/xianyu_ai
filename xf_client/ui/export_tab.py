import webbrowser
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QTableWidget, QTableWidgetItem,
    QHeaderView, QMessageBox, QFileDialog, QMenu,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from engine.excel_exporter import ExcelExporter


GLOBAL_FONT_FAMILY = "Microsoft YaHei, PingFang SC, sans-serif"


class ExportTab(QWidget):
    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        self.info_label = QLabel("请先采集商品数据")
        self.info_label.setFont(QFont(GLOBAL_FONT_FAMILY, 13))
        layout.addWidget(self.info_label)

        self.table = QTableWidget()
        self.table.setColumnCount(8)
        self.table.setHorizontalHeaderLabels(["序号", "商品ID", "原始标题", "AI标题", "价格", "想要", "浏览", "来源链接"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(7, QHeaderView.ResizeMode.Stretch)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_context_menu)
        layout.addWidget(self.table)

        btn_layout = QHBoxLayout()
        self.export_btn = QPushButton("📊 导出Excel")
        self.export_btn.setMinimumHeight(42)
        self.export_btn.setStyleSheet(
            "QPushButton { background: #2e7d32; color: white; "
            "border-radius: 4px; padding: 8px 28px; font-size: 15px; font-weight: bold; }"
            "QPushButton:hover { background: #1B5E20; }"
            "QPushButton:disabled { background: #bbb; }"
        )
        self.export_btn.clicked.connect(self._export)
        self.export_btn.setEnabled(False)
        btn_layout.addWidget(self.export_btn)
        btn_layout.addStretch()
        layout.addLayout(btn_layout)

    def refresh_items(self, items):
        self.info_label.setText(f"共 {len(items)} 个商品")
        self.export_btn.setEnabled(len(items) > 0)
        self._update_table(items)

    def _update_table(self, items):
        self.items = items
        self.table.setRowCount(len(items))
        for i, item in enumerate(items):
            self.table.setItem(i, 0, QTableWidgetItem(str(i + 1)))
            self.table.setItem(i, 1, QTableWidgetItem(item.get("item_id", "")))
            self.table.setItem(i, 2, QTableWidgetItem(item.get("original_title", "")))
            self.table.setItem(i, 3, QTableWidgetItem(item.get("ai_title", "")))
            self.table.setItem(i, 4, QTableWidgetItem(item.get("original_price", "")))
            self.table.setItem(i, 5, QTableWidgetItem(item.get("wants", "0")))
            self.table.setItem(i, 6, QTableWidgetItem(item.get("views", "0")))
            link = item.get("link", "")
            link_display = link[:50] + "..." if len(link) > 50 else link
            self.table.setItem(i, 7, QTableWidgetItem(link_display))

    def _show_context_menu(self, position):
        from PyQt6.QtWidgets import QApplication
        row = self.table.rowAt(position.y())
        if row < 0 or row >= len(getattr(self, 'items', [])):
            return
        item = self.items[row]
        link = item.get("link", "")
        menu = QMenu(self)
        if link:
            open_action = menu.addAction("🔗 打开原链接")
            open_action.triggered.connect(lambda: webbrowser.open(link))
            copy_action = menu.addAction("📋 复制链接")
            copy_action.triggered.connect(lambda: QApplication.clipboard().setText(link))
            menu.exec(self.table.viewport().mapToGlobal(position))

    def _export(self):
        if not self.main_window.is_licensed():
            QMessageBox.warning(self, "未激活", "请先在设置页面激活License后使用导出功能")
            return

        items = self.main_window.get_items()
        if not items:
            QMessageBox.warning(self, "提示", "没有数据可导出")
            return

        try:
            exporter = ExcelExporter()
            filepath = exporter.export(items)
            QMessageBox.information(self, "导出成功", f"已导出到:\n{filepath}")
        except Exception as e:
            QMessageBox.critical(self, "导出失败", str(e))
