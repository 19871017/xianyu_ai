import json
import re
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QSpinBox, QGroupBox,
    QRadioButton, QButtonGroup, QProgressBar, QMessageBox,
    QTextEdit, QComboBox,
)
from PyQt6.QtCore import QThread, pyqtSignal, Qt
from PyQt6.QtGui import QFont

from engine.xianyu_collector import XianyuCollector
from engine.pdd_collector import PddCollector
from engine.alibaba_collector import AlibabaCollector
from engine.jd_collector import JDCollector
from database.db_manager import db


GLOBAL_FONT_FAMILY = "Microsoft YaHei, PingFang SC, sans-serif"


def is_valid_url(url: str) -> bool:
    if not url:
        return False
    pattern = r'^https?://[^\s<>"{}|\\^`\[\]]+$'
    return bool(re.match(pattern, url, re.IGNORECASE))


class CollectWorker(QThread):
    progress = pyqtSignal(str)
    finished = pyqtSignal(list)
    error = pyqtSignal(str)

    def __init__(self, platform, mode, value, count):
        super().__init__()
        self.platform = platform
        self.mode = mode
        self.value = value
        self.count = count

    def run(self):
        def on_progress(msg):
            self.progress.emit(msg)

        try:
            if self.platform == "xianyu":
                collector = XianyuCollector(on_progress=on_progress)
                if self.mode == "keyword":
                    items = collector.search_by_keyword(self.value, self.count)
                else:
                    items = collector.collect_by_homepage(self.value, self.count)

            elif self.platform == "pdd":
                collector = PddCollector(on_progress=on_progress)
                if self.mode == "keyword":
                    items = collector.search_by_keyword(self.value, self.count)
                else:
                    items = collector.collect_by_link(self.value)

            elif self.platform == "1688":
                collector = AlibabaCollector(on_progress=on_progress)
                if self.mode == "keyword":
                    items = collector.search_by_keyword(self.value, self.count)
                else:
                    items = collector.collect_by_link(self.value)

            elif self.platform == "jd":
                collector = JDCollector(on_progress=on_progress)
                if self.mode == "keyword":
                    items = collector.search_by_keyword(self.value, self.count)
                else:
                    items = collector.collect_by_link(self.value)

            else:
                items = []

            self.finished.emit(items)
        except Exception as e:
            self.error.emit(str(e))


# 平台提示配置
PLATFORM_HINTS = {
    "xianyu": {
        "keyword": "输入要搜索的关键词，如：iPhone 15",
        "link": "粘贴闲鱼商品链接，如：https://www.goofish.com/item?id=xxx",
        "link_label": "主页/商品链接:",
    },
    "pdd": {
        "keyword": "输入拼多多搜索关键词，如：无线耳机",
        "link": "粘贴拼多多商品链接，如：https://mobile.yangkeduo.com/goods.html?goods_id=xxx",
        "link_label": "商品链接:",
    },
    "1688": {
        "keyword": "输入1688搜索关键词，如：手机壳批发",
        "link": "粘贴1688商品链接，如：https://detail.1688.com/offer/xxx.html",
        "link_label": "商品链接:",
    },
    "jd": {
        "keyword": "输入京东搜索关键词，如：小米14",
        "link": "粘贴京东商品链接，如：https://item.jd.com/xxxxxxxxx.html",
        "link_label": "商品链接:",
    },
}


class CollectTab(QWidget):
    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self.worker = None
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        # ── 平台选择 ──
        platform_group = QGroupBox("采集平台")
        platform_group.setFont(QFont(GLOBAL_FONT_FAMILY, 13, QFont.Weight.Bold))
        platform_layout = QHBoxLayout(platform_group)

        platform_layout.addWidget(QLabel("选择采集平台:"))
        self.platform_combo = QComboBox()
        self.platform_combo.setMinimumHeight(36)
        self.platform_combo.setFont(QFont(GLOBAL_FONT_FAMILY, 13))
        self.platform_combo.addItem("🐟 闲鱼", "xianyu")
        self.platform_combo.addItem("🛒 拼多多", "pdd")
        self.platform_combo.addItem("🏪 京东", "jd")
        self.platform_combo.addItem("🏭 阿里巴巴(1688)", "1688")
        self.platform_combo.currentIndexChanged.connect(self._on_platform_changed)
        platform_layout.addWidget(self.platform_combo)
        platform_layout.addStretch()
        layout.addWidget(platform_group)

        # ── 采集模式 ──
        mode_group = QGroupBox("采集模式")
        mode_group.setFont(QFont(GLOBAL_FONT_FAMILY, 13, QFont.Weight.Bold))
        mode_layout = QVBoxLayout(mode_group)
        mode_layout.setSpacing(8)

        self.keyword_radio = QRadioButton("🔍 关键词搜索采集")
        self.homepage_radio = QRadioButton("🔗 商品链接直采")
        self.keyword_radio.setChecked(True)

        self.mode_btn_group = QButtonGroup(self)
        self.mode_btn_group.addButton(self.keyword_radio, 0)
        self.mode_btn_group.addButton(self.homepage_radio, 1)
        self.mode_btn_group.idToggled.connect(self._on_mode_changed)

        mode_layout.addWidget(self.keyword_radio)
        mode_layout.addWidget(self.homepage_radio)
        layout.addWidget(mode_group)

        # ── 采集参数 ──
        param_group = QGroupBox("采集参数")
        param_group.setFont(QFont(GLOBAL_FONT_FAMILY, 13, QFont.Weight.Bold))
        param_layout = QVBoxLayout(param_group)
        param_layout.setSpacing(8)

        self.input_label = QLabel("关键词:")
        self.input_field = QLineEdit()
        self.input_field.setPlaceholderText("输入要搜索的关键词，如：iPhone 15")
        self.input_field.setMinimumHeight(36)
        param_layout.addWidget(self.input_label)
        param_layout.addWidget(self.input_field)

        count_layout = QHBoxLayout()
        count_layout.addWidget(QLabel("采集数量:"))
        self.count_spin = QSpinBox()
        self.count_spin.setRange(1, 200)
        self.count_spin.setValue(20)
        self.count_spin.setMinimumHeight(36)
        count_layout.addWidget(self.count_spin)
        count_layout.addStretch()
        param_layout.addLayout(count_layout)

        layout.addWidget(param_group)

        # ── 日志区 ──
        log_group = QGroupBox("采集日志")
        log_group.setFont(QFont(GLOBAL_FONT_FAMILY, 13, QFont.Weight.Bold))
        log_layout = QVBoxLayout(log_group)
        self.log_area = QTextEdit()
        self.log_area.setReadOnly(True)
        self.log_area.setMaximumHeight(200)
        self.log_area.setFont(QFont(GLOBAL_FONT_FAMILY, 12))
        log_layout.addWidget(self.log_area)
        layout.addWidget(log_group)

        # ── 进度 ──
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setVisible(False)
        self.progress_bar.setMinimumHeight(28)
        layout.addWidget(self.progress_bar)

        # ── 按钮 ──
        btn_layout = QHBoxLayout()
        self.start_btn = QPushButton("🚀 开始采集")
        self.start_btn.setMinimumHeight(42)
        self.start_btn.setStyleSheet(
            "QPushButton { background: #1976D2; color: white; "
            "border-radius: 4px; padding: 8px 28px; font-size: 15px; font-weight: bold; }"
            "QPushButton:hover { background: #1565C0; }"
            "QPushButton:disabled { background: #bbb; }"
        )
        self.start_btn.clicked.connect(self._start_collect)

        self.cancel_btn = QPushButton("⏹ 取消")
        self.cancel_btn.setMinimumHeight(42)
        self.cancel_btn.setStyleSheet(
            "QPushButton { background: #e53935; color: white; "
            "border-radius: 4px; padding: 8px 28px; font-size: 15px; font-weight: bold; }"
            "QPushButton:hover { background: #C62828; }"
        )
        self.cancel_btn.clicked.connect(self._cancel)
        self.cancel_btn.setVisible(False)

        btn_layout.addWidget(self.start_btn)
        btn_layout.addWidget(self.cancel_btn)
        btn_layout.addStretch()
        layout.addLayout(btn_layout)

        # ── 统计 ──
        self.stats_label = QLabel("")
        self.stats_label.setStyleSheet("color: #666; font-size: 13px; padding: 4px;")
        layout.addWidget(self.stats_label)

        layout.addStretch()

    def _on_platform_changed(self):
        platform = self.platform_combo.currentData()
        hints = PLATFORM_HINTS.get(platform, PLATFORM_HINTS["xianyu"])
        mode = "keyword" if self.keyword_radio.isChecked() else "link"
        self.input_field.setPlaceholderText(hints[mode])

    def _on_mode_changed(self, btn_id, checked):
        if not checked:
            return
        platform = self.platform_combo.currentData()
        hints = PLATFORM_HINTS.get(platform, PLATFORM_HINTS["xianyu"])
        if btn_id == 0:
            self.input_label.setText("关键词:")
            self.input_field.setPlaceholderText(hints["keyword"])
        else:
            self.input_label.setText(hints.get("link_label", "商品链接:"))
            self.input_field.setPlaceholderText(hints["link"])

    def _start_collect(self):
        if not self.main_window.is_licensed():
            QMessageBox.warning(self, "未激活", "请先在设置页面激活License后使用采集功能")
            return

        platform = self.platform_combo.currentData()
        mode = "keyword" if self.keyword_radio.isChecked() else "homepage"
        value = self.input_field.text().strip()

        if not value:
            QMessageBox.warning(self, "提示", "请输入关键词或商品链接")
            return

        if mode == "homepage" and not is_valid_url(value):
            platform_examples = {
                "xianyu": "https://www.goofish.com/item?id=xxx",
                "pdd": "https://mobile.yangkeduo.com/goods.html?goods_id=xxx",
                "jd": "https://item.jd.com/xxxxxxxxx.html",
                "1688": "https://detail.1688.com/offer/xxx.html",
            }
            example = platform_examples.get(platform, "https://...")
            QMessageBox.warning(
                self, "无效的URL",
                f"请输入有效的商品链接\n\n格式示例:\n{example}"
            )
            return

        count = self.count_spin.value()

        self.start_btn.setEnabled(False)
        self.cancel_btn.setVisible(True)
        self.progress_bar.setVisible(True)
        self.log_area.clear()
        self._append_log(f"开始采集 [{self.platform_combo.currentText()}]...")

        self.worker = CollectWorker(platform, mode, value, count)
        self.worker.progress.connect(self._on_progress)
        self.worker.finished.connect(self._on_finished)
        self.worker.error.connect(self._on_error)
        self.worker.start()

    def _cancel(self):
        if self.worker and self.worker.isRunning():
            self.worker.terminate()
        self._reset_ui()
        self._append_log("采集已取消")

    def _on_progress(self, msg):
        self._append_log(msg)

    def _on_finished(self, items):
        for item in items:
            try:
                db_id = db.save_product(item)
                item["db_id"] = db_id
            except Exception as e:
                print(f"保存商品失败: {e}")

        self.main_window.set_items(items)
        self._reset_ui()
        total_imgs = sum(len(it.get("local_images", [])) for it in items)
        self._append_log(f"\n✅ 采集完成！共 {len(items)} 个商品，{total_imgs} 张图片（已MD5去重）")
        self._append_log(f"💾 数据已保存到本地数据库，关闭软件不会丢失")
        QMessageBox.information(
            self, "完成",
            f"采集完成，共 {len(items)} 个商品，{total_imgs} 张图片\n数据已自动保存"
        )

    def _on_error(self, msg):
        self._reset_ui()
        self._append_log(f"❌ 采集失败: {msg}")
        QMessageBox.critical(self, "错误", f"采集失败: {msg}")

    def _append_log(self, msg):
        self.log_area.append(msg)
        scrollbar = self.log_area.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def _reset_ui(self):
        self.start_btn.setEnabled(True)
        self.cancel_btn.setVisible(False)
        self.progress_bar.setVisible(False)
        self.worker = None

    def refresh_items(self, items):
        """刷新统计信息"""
        platform_map = {"xianyu": "闲鱼", "pdd": "拼多多", "jd": "京东", "1688": "1688"}
        platform_counts = {}
        for item in items:
            p = item.get("platform", "xianyu")
            label = platform_map.get(p, p)
            platform_counts[label] = platform_counts.get(label, 0) + 1

        stats = " | ".join([f"{k}: {v}个" for k, v in platform_counts.items()])
        self.stats_label.setText(f"📊 当前数据: {stats}" if stats else "📊 暂无采集数据")
