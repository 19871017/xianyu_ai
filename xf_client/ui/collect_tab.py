import json
import re
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QSpinBox, QGroupBox,
    QRadioButton, QButtonGroup, QProgressBar, QMessageBox,
    QTextEdit, QComboBox, QDoubleSpinBox, QFileDialog,
)
from PyQt6.QtCore import QThread, pyqtSignal, Qt
from PyQt6.QtGui import QFont

from engine.xianyu_collector import XianyuCollector
from engine.pdd_collector import PddCollector
from engine.alibaba_collector import AlibabaCollector
from engine.taobao_collector import TaobaoCollector
from engine.jd_collector import JDCollector
from engine.collect_filter import filter_items
from database.db_manager import db
from engine.product_package import ensure_full_product_package, export_products_package
from engine.link_importer import import_links


GLOBAL_FONT_FAMILY = "Microsoft YaHei, PingFang SC, sans-serif"

# 各平台采集器类映射
COLLECTOR_CLASSES = {
    "xianyu": XianyuCollector,
    "pdd": PddCollector,
    "jd": JDCollector,
    "1688": AlibabaCollector,
    "taobao": TaobaoCollector,
}

# 各平台登录页URL
LOGIN_URLS = {
    "xianyu": "https://login.taobao.com/",
    "pdd": "https://mobile.yangkeduo.com/",
    "jd": "https://passport.jd.com/new/login.aspx",
    "1688": "https://login.1688.com/member/signin.htm",
    "taobao": "https://login.taobao.com/",
}


def is_valid_url(url: str) -> bool:
    if not url:
        return False
    pattern = r'^https?://[^\s<>"{}|\\^`\[\]]+$'
    return bool(re.match(pattern, url, re.IGNORECASE))


class LoginWorker(QThread):
    """登录工作线程"""
    progress = pyqtSignal(str)
    finished_login = pyqtSignal(bool)

    def __init__(self, platform):
        super().__init__()
        self.platform = platform

    def run(self):
        try:
            cls = COLLECTOR_CLASSES.get(self.platform)
            if not cls:
                self.progress.emit(f"不支持的平台: {self.platform}")
                self.finished_login.emit(False)
                return

            def on_progress(msg):
                self.progress.emit(msg)

            collector = cls(on_progress=on_progress)
            self.progress.emit(f"正在启动 {self.platform} 登录...")
            result = collector.ensure_login(timeout=300)
            self.finished_login.emit(result)
        except Exception as e:
            self.progress.emit(f"❌ 登录失败: {e}")
            self.finished_login.emit(False)


class CheckLoginWorker(QThread):
    """检查登录状态线程"""
    finished_check = pyqtSignal(bool)

    def __init__(self, platform):
        super().__init__()
        self.platform = platform

    def run(self):
        try:
            cls = COLLECTOR_CLASSES.get(self.platform)
            if not cls:
                self.finished_check.emit(False)
                return
            collector = cls()
            result = collector.check_login_status()
            self.finished_check.emit(result)
        except Exception:
            self.finished_check.emit(False)


class BatchImportWorker(QThread):
    """批量链接导入采集：按平台分组，优先用各采集器的 collect_by_links 单会话循环。"""
    progress = pyqtSignal(str)
    finished = pyqtSignal(list)
    error = pyqtSignal(str)

    def __init__(self, links, filters=None):
        super().__init__()
        # links: [{"url","platform","item_id"}]
        self.links = links or []
        self.filters = filters or {}

    def run(self):
        def on_progress(msg):
            self.progress.emit(msg)

        try:
            # 按平台分组，保序
            groups = {}
            order = []
            for ln in self.links:
                plat = ln.get("platform")
                url = ln.get("url")
                if not plat or not url:
                    continue
                if plat not in groups:
                    groups[plat] = []
                    order.append(plat)
                groups[plat].append(url)

            all_items = []
            for plat in order:
                urls = groups[plat]
                cls = COLLECTOR_CLASSES.get(plat)
                if cls is None:
                    self.progress.emit(f"跳过不支持的平台: {plat}（{len(urls)}条）")
                    continue
                self.progress.emit(f"\n=== 开始采集 {plat}（{len(urls)} 个商品）===")
                collector = cls(on_progress=on_progress)
                try:
                    if hasattr(collector, "collect_by_links"):
                        items = collector.collect_by_links(urls)
                    else:
                        # 回退：逐条采集（每条重启浏览器）
                        items = []
                        for u in urls:
                            try:
                                got = collector.collect_by_link(u)
                                if got:
                                    items.extend(got)
                            except Exception as e:
                                self.progress.emit(f"  ✗ 采集异常 [{u}]: {e}")
                    all_items.extend(items or [])
                except Exception as e:
                    self.progress.emit(f"  ✗ {plat} 批量采集失败: {e}")

            all_items = self._apply_filters(all_items)
            self.finished.emit(all_items)
        except Exception as e:
            self.error.emit(str(e))

    def _apply_filters(self, items):
        f = self.filters or {}
        if not f:
            return items
        try:
            filtered = filter_items(
                items,
                min_price=f.get('min_price'),
                max_price=f.get('max_price'),
                min_sales=f.get('min_sales'),
                min_wants=f.get('min_wants'),
                min_views=f.get('min_views'),
                sort_by=f.get('sort_by'),
                order=f.get('order', 'desc'),
            )
            removed = len(items) - len(filtered)
            if removed > 0:
                self.progress.emit(f'按筛选条件过滤掉 {removed} 个，保留 {len(filtered)} 个')
            return filtered
        except Exception as e:
            self.progress.emit(f'筛选异常，返回原始结果: {e}')
            return items


class CollectWorker(QThread):
    progress = pyqtSignal(str)
    finished = pyqtSignal(list)
    error = pyqtSignal(str)

    def __init__(self, platform, mode, value, count, filters=None):
        super().__init__()
        self.platform = platform
        self.mode = mode
        self.value = value
        self.count = count
        self.filters = filters or {}

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

            elif self.platform == "taobao":
                collector = TaobaoCollector(on_progress=on_progress)
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

            items = self._apply_filters(items)
            self.finished.emit(items)
        except Exception as e:
            self.error.emit(str(e))

    def _apply_filters(self, items):
        f = self.filters or {}
        if not f:
            return items
        try:
            filtered = filter_items(
                items,
                min_price=f.get('min_price'),
                max_price=f.get('max_price'),
                min_sales=f.get('min_sales'),
                min_wants=f.get('min_wants'),
                min_views=f.get('min_views'),
                sort_by=f.get('sort_by'),
                order=f.get('order', 'desc'),
            )
            removed = len(items) - len(filtered)
            if removed > 0:
                self.progress.emit(f'按筛选条件过滤掉 {removed} 个，保留 {len(filtered)} 个')
            return filtered
        except Exception as e:
            self.progress.emit(f'筛选异常，返回原始结果: {e}')
            return items


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
    "taobao": {
        "keyword": "输入淘宝/天猫搜索关键词，如：蓝牙耳机",
        "link": "粘贴淘宝/天猫商品链接，如：https://item.taobao.com/item.htm?id=xxx",
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
        self.platform_combo.addItem("🛍 淘宝/天猫", "taobao")
        self.platform_combo.currentIndexChanged.connect(self._on_platform_changed)
        platform_layout.addWidget(self.platform_combo)

        # 登录状态标签
        self.login_status_label = QLabel("🔘 未检查")
        self.login_status_label.setStyleSheet("color: #999; font-size: 13px; padding: 2px 8px;")
        platform_layout.addWidget(self.login_status_label)

        # 登录按钮
        self.login_btn = QPushButton("🔐 登录账号")
        self.login_btn.setMinimumHeight(36)
        self.login_btn.setStyleSheet(
            "QPushButton { background: #FF9800; color: white; "
            "border-radius: 4px; padding: 6px 16px; font-size: 13px; }"
            "QPushButton:hover { background: #F57C00; }"
            "QPushButton:disabled { background: #bbb; }"
        )
        self.login_btn.clicked.connect(self._do_login)
        platform_layout.addWidget(self.login_btn)

        # 检查登录状态按钮
        self.check_login_btn = QPushButton("🔄 检查状态")
        self.check_login_btn.setMinimumHeight(36)
        self.check_login_btn.setStyleSheet(
            "QPushButton { background: #607D8B; color: white; "
            "border-radius: 4px; padding: 6px 16px; font-size: 13px; }"
            "QPushButton:hover { background: #455A64; }"
            "QPushButton:disabled { background: #bbb; }"
        )
        self.check_login_btn.clicked.connect(self._check_login)
        platform_layout.addWidget(self.check_login_btn)

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

        # ── 筛选与排序 ──
        from PyQt6.QtWidgets import QDoubleSpinBox
        price_layout = QHBoxLayout()
        price_layout.addWidget(QLabel('价格区间:'))
        self.min_price_spin = QDoubleSpinBox()
        self.min_price_spin.setRange(0, 999999)
        self.min_price_spin.setDecimals(2)
        self.min_price_spin.setPrefix('¥')
        self.min_price_spin.setMinimumHeight(32)
        self.min_price_spin.setSpecialValueText('不限')
        price_layout.addWidget(self.min_price_spin)
        price_layout.addWidget(QLabel('—'))
        self.max_price_spin = QDoubleSpinBox()
        self.max_price_spin.setRange(0, 999999)
        self.max_price_spin.setDecimals(2)
        self.max_price_spin.setPrefix('¥')
        self.max_price_spin.setMinimumHeight(32)
        self.max_price_spin.setSpecialValueText('不限')
        price_layout.addWidget(self.max_price_spin)
        price_layout.addStretch()
        param_layout.addLayout(price_layout)

        hot_layout = QHBoxLayout()
        hot_layout.addWidget(QLabel('最低销量:'))
        self.min_sales_spin = QSpinBox()
        self.min_sales_spin.setRange(0, 9999999)
        self.min_sales_spin.setMinimumHeight(32)
        self.min_sales_spin.setSpecialValueText('不限')
        hot_layout.addWidget(self.min_sales_spin)
        hot_layout.addWidget(QLabel('最低想要/热度:'))
        self.min_wants_spin = QSpinBox()
        self.min_wants_spin.setRange(0, 9999999)
        self.min_wants_spin.setMinimumHeight(32)
        self.min_wants_spin.setSpecialValueText('不限')
        hot_layout.addWidget(self.min_wants_spin)
        hot_layout.addWidget(QLabel('最低浏览量:'))
        self.min_views_spin = QSpinBox()
        self.min_views_spin.setRange(0, 99999999)
        self.min_views_spin.setMinimumHeight(32)
        self.min_views_spin.setSpecialValueText('不限')
        hot_layout.addWidget(self.min_views_spin)
        hot_layout.addStretch()
        param_layout.addLayout(hot_layout)

        sort_layout = QHBoxLayout()
        sort_layout.addWidget(QLabel('排序:'))
        self.sort_combo = QComboBox()
        self.sort_combo.addItem('不排序', '')
        self.sort_combo.addItem('价格', 'price')
        self.sort_combo.addItem('销量', 'sales')
        self.sort_combo.addItem('想要/热度', 'wants')
        self.sort_combo.addItem('浏览量', 'views')
        self.sort_combo.setMinimumHeight(32)
        sort_layout.addWidget(self.sort_combo)
        self.order_combo = QComboBox()
        self.order_combo.addItem('从高到低', 'desc')
        self.order_combo.addItem('从低到高', 'asc')
        self.order_combo.setMinimumHeight(32)
        sort_layout.addWidget(self.order_combo)
        sort_layout.addStretch()
        param_layout.addLayout(sort_layout)

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

        # 导入链接文件批量采集（配合 1688 采购助手插件等选品导出）
        self.import_btn = QPushButton("📂 导入链接文件")
        self.import_btn.setMinimumHeight(42)
        self.import_btn.setStyleSheet(
            "QPushButton { background: #00897B; color: white; "
            "border-radius: 4px; padding: 8px 20px; font-size: 14px; font-weight: bold; }"
            "QPushButton:hover { background: #00695C; }"
            "QPushButton:disabled { background: #bbb; }"
        )
        self.import_btn.setToolTip(
            "从选品/导出文件(Excel/CSV/JSON/TXT)中提取 1688/淘宝/京东/拼多多商品链接并批量采集"
        )
        self.import_btn.clicked.connect(self._import_links_collect)

        btn_layout.addWidget(self.start_btn)
        btn_layout.addWidget(self.cancel_btn)
        btn_layout.addWidget(self.import_btn)
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
        # 重置登录状态
        self.login_status_label.setText("🔘 未检查")
        self.login_status_label.setStyleSheet("color: #999; font-size: 13px; padding: 2px 8px;")

    def _do_login(self):
        """启动登录流程"""
        if not self.main_window.is_licensed():
            QMessageBox.warning(self, "未激活", "请先在设置页面激活License后使用")
            return

        platform = self.platform_combo.currentData()
        platform_name = self.platform_combo.currentText()

        self.login_btn.setEnabled(False)
        self.check_login_btn.setEnabled(False)
        self.start_btn.setEnabled(False)
        self.login_status_label.setText("⏳ 登录中...")
        self.login_status_label.setStyleSheet("color: #FF9800; font-size: 13px; padding: 2px 8px;")
        self._append_log(f"正在启动 {platform_name} 登录流程...")

        self.login_worker = LoginWorker(platform)
        self.login_worker.progress.connect(self._on_login_progress)
        self.login_worker.finished_login.connect(self._on_login_finished)
        self.login_worker.start()

    def _check_login(self):
        """检查当前平台登录状态"""
        platform = self.platform_combo.currentData()
        self.check_login_btn.setEnabled(False)
        self.login_status_label.setText("⏳ 检查中...")
        self.login_status_label.setStyleSheet("color: #FF9800; font-size: 13px; padding: 2px 8px;")

        self.check_worker = CheckLoginWorker(platform)
        self.check_worker.finished_check.connect(self._on_check_login_finished)
        self.check_worker.start()

    def _on_login_progress(self, msg):
        self._append_log(msg)

    def _on_login_finished(self, success):
        self.login_btn.setEnabled(True)
        self.check_login_btn.setEnabled(True)
        self.start_btn.setEnabled(True)

        if success:
            self.login_status_label.setText("✅ 已登录")
            self.login_status_label.setStyleSheet("color: #4CAF50; font-size: 13px; padding: 2px 8px; font-weight: bold;")
            self._append_log("✅ 登录成功！现在可以开始采集了")
            QMessageBox.information(self, "登录成功", "登录成功！登录态已保存。\n现在可以开始采集了。")
        else:
            self.login_status_label.setText("❌ 未登录")
            self.login_status_label.setStyleSheet("color: #f44336; font-size: 13px; padding: 2px 8px;")
            self._append_log("❌ 登录失败或超时")
            QMessageBox.warning(self, "登录失败", "登录失败或超时，请重试。")

    def _on_check_login_finished(self, logged_in):
        self.check_login_btn.setEnabled(True)
        if logged_in:
            self.login_status_label.setText("✅ 已登录")
            self.login_status_label.setStyleSheet("color: #4CAF50; font-size: 13px; padding: 2px 8px; font-weight: bold;")
            self._append_log("✅ 当前平台已登录")
        else:
            self.login_status_label.setText("❌ 未登录")
            self.login_status_label.setStyleSheet("color: #f44336; font-size: 13px; padding: 2px 8px;")
            self._append_log("⚠️ 当前平台未登录，请先点击'登录账号'")

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

    def _collect_filters(self) -> dict:
        """读取筛选控件的值，组装成 filter_items 的参数（仅含已启用项）。"""
        f = {}
        mn = self.min_price_spin.value()
        mx = self.max_price_spin.value()
        if mn > 0:
            f["min_price"] = float(mn)
        if mx > 0:
            f["max_price"] = float(mx)
        if self.min_sales_spin.value() > 0:
            f["min_sales"] = float(self.min_sales_spin.value())
        if self.min_wants_spin.value() > 0:
            f["min_wants"] = float(self.min_wants_spin.value())
        if self.min_views_spin.value() > 0:
            f["min_views"] = float(self.min_views_spin.value())
        sort_by = self.sort_combo.currentData()
        if sort_by:
            f["sort_by"] = sort_by
            f["order"] = "asc" if self.order_combo.currentData() == "asc" else "desc"
        return f

    def _filters_desc(self, f: dict) -> str:
        parts = []
        if "min_price" in f or "max_price" in f:
            lo = f.get("min_price", 0)
            hi = f.get("max_price", "∞")
            parts.append(f"价格 {lo}~{hi}")
        if "min_sales" in f:
            parts.append(f"销量≥{int(f['min_sales'])}")
        if "min_wants" in f:
            parts.append(f"想要≥{int(f['min_wants'])}")
        if "min_views" in f:
            parts.append(f"浏览≥{int(f['min_views'])}")
        if f.get("sort_by"):
            label = {"price": "价格", "sales": "销量", "wants": "想要", "views": "浏览"}.get(f["sort_by"], f["sort_by"])
            arrow = "↑" if f.get("order") == "asc" else "↓"
            parts.append(f"按{label}{arrow}排序")
        return " | ".join(parts) if parts else "无"

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
                "taobao": "https://item.taobao.com/item.htm?id=xxx",
            }
            example = platform_examples.get(platform, "https://...")
            QMessageBox.warning(
                self, "无效的URL",
                f"请输入有效的商品链接\n\n格式示例:\n{example}"
            )
            return

        count = self.count_spin.value()
        filters = self._collect_filters()

        self.start_btn.setEnabled(False)
        self.import_btn.setEnabled(False)
        self.cancel_btn.setVisible(True)
        self.progress_bar.setVisible(True)
        self.log_area.clear()
        self._append_log(f"开始采集 [{self.platform_combo.currentText()}]...")
        if filters:
            self._append_log(f"筛选条件: {self._filters_desc(filters)}")

        self.worker = CollectWorker(platform, mode, value, count, filters)
        self.worker.progress.connect(self._on_progress)
        self.worker.finished.connect(self._on_finished)
        self.worker.error.connect(self._on_error)
        self.worker.start()

    def _import_links_collect(self):
        """从选品/导出文件提取商品链接并批量采集（方案一：插件选品→导入采集）。"""
        if not self.main_window.is_licensed():
            QMessageBox.warning(self, "未激活", "请先在设置页面激活License后使用采集功能")
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "选择选品/导出文件",
            "",
            "选品文件 (*.xlsx *.xlsm *.xls *.csv *.json *.txt);;所有文件 (*.*)",
        )
        if not path:
            return
        try:
            links = import_links(path)
        except Exception as e:
            QMessageBox.critical(self, "导入失败", f"解析文件失败:\n{e}")
            return
        if not links:
            QMessageBox.warning(
                self, "未找到链接",
                "文件中未提取到受支持平台(1688/淘宝/京东/拼多多)的商品链接。",
            )
            return

        # 平台分布概览
        dist = {}
        for ln in links:
            dist[ln["platform"]] = dist.get(ln["platform"], 0) + 1
        plat_label = {"1688": "1688", "taobao": "淘宝/天猫", "jd": "京东", "pdd": "拼多多"}
        desc = " | ".join(f"{plat_label.get(k, k)}: {v}个" for k, v in dist.items())
        ret = QMessageBox.question(
            self, "确认导入采集",
            f"共提取到 {len(links)} 个商品链接\n{desc}\n\n是否开始批量采集？\n"
            "(拼多多详情页可能触发风控验证，建议以 1688/淘宝为主)",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if ret != QMessageBox.StandardButton.Yes:
            return

        filters = self._collect_filters()
        self.start_btn.setEnabled(False)
        self.import_btn.setEnabled(False)
        self.cancel_btn.setVisible(True)
        self.progress_bar.setVisible(True)
        self.log_area.clear()
        self._append_log(f"📂 已导入 {len(links)} 个链接：{desc}")
        if filters:
            self._append_log(f"筛选条件: {self._filters_desc(filters)}")

        self.worker = BatchImportWorker(links, filters)
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
        normalized_items = []
        for item in items:
            try:
                item = ensure_full_product_package(item)
                db_id = db.save_product(item)
                item["db_id"] = db_id
                normalized_items.append(item)
            except Exception as e:
                print(f"保存商品失败: {e}")

        export_dir = ""
        if normalized_items:
            try:
                export_dir = export_products_package(normalized_items)
            except Exception as e:
                self._append_log(f"⚠ 商品包导出失败: {e}")

        self.main_window.set_items(normalized_items)
        self._reset_ui()
        total_imgs = sum(len(it.get("local_images", [])) for it in normalized_items)
        total_skus = sum(len(it.get("sku_list", [])) for it in normalized_items)
        self._append_log(f"\n✅ 采集完成！共 {len(normalized_items)} 个商品，{total_skus} 个SKU，{total_imgs} 张图片（已MD5去重）")
        self._append_log(f"💾 数据已保存到本地数据库，关闭软件不会丢失")
        if export_dir:
            self._append_log(f"📦 商品包已导出: {export_dir}")
        QMessageBox.information(
            self, "完成",
            f"采集完成，共 {len(normalized_items)} 个商品，{total_skus} 个SKU，{total_imgs} 张图片\n数据已自动保存"
            + (f"\n\n商品包已导出:\n{export_dir}" if export_dir else "")
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
        self.import_btn.setEnabled(True)
        self.cancel_btn.setVisible(False)
        self.progress_bar.setVisible(False)
        self.worker = None

    def refresh_items(self, items):
        """刷新统计信息"""
        platform_map = {"xianyu": "闲鱼", "pdd": "拼多多", "jd": "京东", "1688": "1688", "taobao": "淘宝/天猫"}
        platform_counts = {}
        for item in items:
            p = item.get("platform", "xianyu")
            label = platform_map.get(p, p)
            platform_counts[label] = platform_counts.get(label, 0) + 1

        stats = " | ".join([f"{k}: {v}个" for k, v in platform_counts.items()])
        self.stats_label.setText(f"📊 当前数据: {stats}" if stats else "📊 暂无采集数据")
