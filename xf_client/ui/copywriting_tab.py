from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QProgressBar, QMessageBox,
    QTableWidget, QTableWidgetItem, QHeaderView,
)
from PyQt6.QtCore import QThread, pyqtSignal, Qt
from PyQt6.QtGui import QFont

from engine.ai_writer import AIWriter


GLOBAL_FONT_FAMILY = "Microsoft YaHei, PingFang SC, sans-serif"


class RewriteWorker(QThread):
    progress = pyqtSignal(int, int)
    finished = pyqtSignal(list)
    error = pyqtSignal(str)

    def __init__(self, items):
        super().__init__()
        self.items = items

    def run(self):
        try:
            writer = AIWriter()
            if not writer._is_configured():
                self.error.emit("未配置AI API，请先在设置页面填写API地址、Key并选择模型")
                return

            results = []
            total = len(self.items)
            for i, item in enumerate(self.items):
                result = writer.rewrite(
                    item.get("original_title", ""),
                    item.get("description", ""),
                    item.get("original_price", ""),
                )
                if result.get("success"):
                    # 保留原始描述以便追溯（仅首次改写时记录）。
                    if not item.get("original_description"):
                        item["original_description"] = item.get("description", "")
                    item["ai_title"] = result.get("title", item.get("original_title", ""))
                    item["ai_description"] = result.get("description", "")
                    item["ai_tags"] = result.get("tags", [])
                    # ★关键：把改写结果写进下游真正使用的字段，
                    #   否则 AI 标题/描述/标签不会入库也不会上架。
                    item["title"] = item["ai_title"]
                    if item["ai_description"]:
                        item["description"] = item["ai_description"]
                    if item["ai_tags"]:
                        item["tags"] = item["ai_tags"]
                else:
                    item["ai_title"] = item.get("original_title", "")
                    item["ai_description"] = f"[改写失败] {result.get('error', '')}"
                    item["ai_tags"] = []
                results.append(item)
                self.progress.emit(i + 1, total)

            self.finished.emit(results)
        except Exception as e:
            self.error.emit(str(e))


class CopywritingTab(QWidget):
    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self.worker = None
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        # 顶部信息
        self.info_label = QLabel("请先采集商品数据")
        self.info_label.setFont(QFont(GLOBAL_FONT_FAMILY, 13))
        layout.addWidget(self.info_label)

        # 进度
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        self.progress_bar.setMinimumHeight(28)
        layout.addWidget(self.progress_bar)

        # 表格
        self.table = QTableWidget()
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(["序号", "原始标题", "AI优化标题", "AI描述", "标签"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self.table)

        # 按钮
        btn_layout = QHBoxLayout()
        self.rewrite_btn = QPushButton("✍️ 一键AI改写")
        self.rewrite_btn.setMinimumHeight(40)
        self.rewrite_btn.setStyleSheet(
            "QPushButton { background: #1976D2; color: white; "
            "border-radius: 4px; padding: 8px 28px; font-size: 15px; font-weight: bold; }"
            "QPushButton:hover { background: #1565C0; }"
            "QPushButton:disabled { background: #bbb; }"
        )
        self.rewrite_btn.clicked.connect(self._start_rewrite)
        self.rewrite_btn.setEnabled(False)
        btn_layout.addWidget(self.rewrite_btn)
        btn_layout.addStretch()
        layout.addLayout(btn_layout)

    def refresh_items(self, items):
        self.info_label.setText(f"共 {len(items)} 个商品待改写")
        self.rewrite_btn.setEnabled(len(items) > 0)
        self._update_table(items)

    def _update_table(self, items):
        self.table.setRowCount(len(items))
        for i, item in enumerate(items):
            self.table.setItem(i, 0, QTableWidgetItem(str(i + 1)))
            self.table.setItem(i, 1, QTableWidgetItem(item.get("original_title", "")))
            self.table.setItem(i, 2, QTableWidgetItem(item.get("ai_title", "")))
            self.table.setItem(i, 3, QTableWidgetItem(item.get("ai_description", "")[:100]))
            self.table.setItem(i, 4, QTableWidgetItem(", ".join(item.get("ai_tags", []))))

    def _start_rewrite(self):
        if not self.main_window.is_licensed():
            QMessageBox.warning(self, "未激活", "请先在设置页面激活License后使用文案优化功能")
            return

        items = self.main_window.get_items()
        if not items:
            QMessageBox.warning(self, "提示", "没有商品数据")
            return

        self.rewrite_btn.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, len(items))

        self.worker = RewriteWorker(items)
        self.worker.progress.connect(self._on_progress)
        self.worker.finished.connect(self._on_finished)
        self.worker.error.connect(self._on_error)
        self.worker.start()

    def _on_progress(self, current, total):
        self.progress_bar.setValue(current)
        self.info_label.setText(f"正在改写: {current}/{total}")

    def _on_finished(self, items):
        # 持久化改写结果，避免重启丢失（set_items 本身不落库）。
        from database.db_manager import db
        saved = 0
        for it in items:
            try:
                if it.get("db_id"):
                    db.save_product(it)
                    saved += 1
            except Exception as e:
                print(f"保存改写结果失败: {e}")
        self.main_window.set_items(items)
        self.progress_bar.setVisible(False)
        self.info_label.setText(f"改写完成，共 {len(items)} 个商品")
        self.rewrite_btn.setEnabled(True)
        QMessageBox.information(self, "完成", f"改写完成，共 {len(items)} 个商品（已保存 {saved} 个）")

    def _on_error(self, msg):
        self.progress_bar.setVisible(False)
        self.info_label.setText(f"改写失败: {msg}")
        self.rewrite_btn.setEnabled(True)
        QMessageBox.critical(self, "错误", f"改写失败: {msg}")
