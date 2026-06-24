"""多平台运营监控 Tab

功能:
- 实时展示 闲鱼/拼多多/京东/1688 四个平台的运营概况
- 平台状态卡片（已连接/未连接/错误）
- 汇总指标（在售商品/今日订单/30日营收/待处理）
- 各平台详细数据表格
- 历史趋势简报
- 预警通知
- 自动定时刷新
"""
import json
from datetime import datetime
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QTableWidget, QTableWidgetItem, QHeaderView,
    QGroupBox, QMessageBox, QComboBox, QFrame, QGridLayout,
    QTabWidget, QTextEdit, QScrollArea, QSizePolicy,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt6.QtGui import QFont, QColor, QBrush

from engine.monitor_manager import MonitorManager, MonitorSnapshot, PLATFORM_DISPLAY, PLATFORM_COLOR
from database.db_manager import db


GLOBAL_FONT_FAMILY = "Microsoft YaHei, PingFang SC, sans-serif"

PLATFORM_ICON = {
    "xianyu": "🐟",
    "pdd": "🛒",
    "jd": "🏪",
    "1688": "🏭",
}

ALL_PLATFORMS = ["xianyu", "pdd", "jd", "1688"]


class PlatformFetchWorker(QThread):
    """单平台数据采集 Worker"""
    progress = pyqtSignal(str)
    snapshot_ready = pyqtSignal(str, object)   # platform, MonitorSnapshot
    error = pyqtSignal(str, str)               # platform, error_msg

    def __init__(self, platform: str, wait_login: bool = False):
        super().__init__()
        self.platform = platform
        self.wait_login = wait_login

    def run(self):
        try:
            manager = MonitorManager(on_progress=lambda m: self.progress.emit(m))
            snap = manager.fetch_platform(
                self.platform,
                wait_login=self.wait_login,
                on_progress=lambda m: self.progress.emit(m),
            )
            manager.save_snapshot(snap)
            self.snapshot_ready.emit(self.platform, snap)
        except Exception as e:
            self.error.emit(self.platform, str(e))


class AllPlatformFetchWorker(QThread):
    """全平台数据采集 Worker（串行）"""
    progress = pyqtSignal(str)
    snapshot_ready = pyqtSignal(str, object)
    all_done = pyqtSignal()

    def __init__(self, platforms: list):
        super().__init__()
        self.platforms = platforms

    def run(self):
        manager = MonitorManager(on_progress=lambda m: self.progress.emit(m))
        for p in self.platforms:
            try:
                snap = manager.fetch_platform(p, wait_login=False, on_progress=lambda m: self.progress.emit(m))
                manager.save_snapshot(snap)
                self.snapshot_ready.emit(p, snap)
            except Exception as e:
                from engine.monitor_manager import MonitorSnapshot
                err_snap = MonitorSnapshot(platform=p, error=str(e))
                self.snapshot_ready.emit(p, err_snap)
        self.all_done.emit()


class PlatformCard(QFrame):
    """单平台状态卡片"""
    connect_clicked = pyqtSignal(str)  # platform

    def __init__(self, platform: str, parent=None):
        super().__init__(parent)
        self.platform = platform
        self._setup_ui()
        self.setMinimumWidth(200)
        self.setMaximumWidth(280)

    def _setup_ui(self):
        self.setFrameStyle(QFrame.Shape.StyledPanel)
        self.setStyleSheet("""
            QFrame {
                border: 1px solid #ddd;
                border-radius: 8px;
                background: #fff;
                padding: 4px;
            }
        """)
        layout = QVBoxLayout(self)
        layout.setSpacing(4)
        layout.setContentsMargins(10, 10, 10, 10)

        # 平台标题行
        title_row = QHBoxLayout()
        icon = PLATFORM_ICON.get(self.platform, "📦")
        name = PLATFORM_DISPLAY.get(self.platform, self.platform)
        self.title_label = QLabel(f"{icon} {name}")
        self.title_label.setFont(QFont(GLOBAL_FONT_FAMILY, 13, QFont.Weight.Bold))
        title_row.addWidget(self.title_label)

        # 状态指示
        self.status_dot = QLabel("⚪")
        self.status_dot.setFont(QFont(GLOBAL_FONT_FAMILY, 14))
        title_row.addStretch()
        title_row.addWidget(self.status_dot)
        layout.addLayout(title_row)

        # 指标
        self.listings_label = QLabel("在售: -")
        self.listings_label.setStyleSheet("color: #444; font-size: 12px;")
        layout.addWidget(self.listings_label)

        self.orders_label = QLabel("待处理: -")
        self.orders_label.setStyleSheet("color: #444; font-size: 12px;")
        layout.addWidget(self.orders_label)

        self.revenue_label = QLabel("今日营收: -")
        self.revenue_label.setStyleSheet("color: #444; font-size: 12px;")
        layout.addWidget(self.revenue_label)

        self.last_update_label = QLabel("未检测")
        self.last_update_label.setStyleSheet("color: #999; font-size: 11px;")
        layout.addWidget(self.last_update_label)

        # 连接按钮
        self.connect_btn = QPushButton("🔗 连接账号")
        self.connect_btn.setMinimumHeight(30)
        self.connect_btn.setStyleSheet(
            "QPushButton { background: #1976D2; color: white; border-radius: 4px; "
            "padding: 4px 12px; font-size: 12px; font-weight: bold; }"
            "QPushButton:hover { background: #1565C0; }"
            "QPushButton:disabled { background: #bbb; }"
        )
        self.connect_btn.clicked.connect(lambda: self.connect_clicked.emit(self.platform))
        layout.addWidget(self.connect_btn)

    def update_snapshot(self, snap: MonitorSnapshot):
        """用快照数据更新卡片显示"""
        if snap.error and not snap.is_logged_in:
            if "登录" in snap.error or "需要" in snap.error:
                self.status_dot.setText("🔴")
                self.connect_btn.setText("🔗 未登录，点击连接")
                self.connect_btn.setEnabled(True)
            else:
                self.status_dot.setText("🟡")
                self.connect_btn.setText(f"⚠ {snap.error[:15]}")
        elif snap.is_logged_in:
            self.status_dot.setText("🟢")
            self.connect_btn.setText("🔄 刷新")
            self.connect_btn.setEnabled(True)
        else:
            self.status_dot.setText("⚪")
            self.connect_btn.setText("🔗 连接账号")
            self.connect_btn.setEnabled(True)

        self.listings_label.setText(f"在售商品: {snap.active_listings}")
        self.orders_label.setText(
            f"待处理: {snap.pending_orders}"
            + (" ⚠️" if snap.pending_orders > 0 else "")
        )
        self.revenue_label.setText(f"今日营收: ¥{snap.revenue_today:.0f}")
        self.last_update_label.setText(f"更新: {snap.timestamp[11:16] if snap.timestamp else '-'}")

    def set_loading(self, loading: bool):
        self.connect_btn.setEnabled(not loading)
        if loading:
            self.status_dot.setText("🔵")
            self.connect_btn.setText("采集中...")


class SummaryCard(QFrame):
    """汇总统计卡片"""
    def __init__(self, title: str, value: str = "0", color: str = "#1976D2"):
        super().__init__()
        self.setFrameStyle(QFrame.Shape.StyledPanel)
        self.setStyleSheet(f"""
            QFrame {{
                border: 1px solid {color}44;
                border-radius: 8px;
                background: {color}11;
                padding: 4px;
            }}
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(2)

        self.title_lbl = QLabel(title)
        self.title_lbl.setStyleSheet("color: #555; font-size: 12px;")
        layout.addWidget(self.title_lbl)

        self.value_lbl = QLabel(value)
        self.value_lbl.setStyleSheet(f"color: {color}; font-size: 22px; font-weight: bold;")
        layout.addWidget(self.value_lbl)

    def set_value(self, v: str):
        self.value_lbl.setText(str(v))


class MonitorTab(QWidget):
    """多平台运营监控 Tab"""

    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self.workers: dict = {}                        # platform -> worker
        self.snapshots: dict[str, MonitorSnapshot] = {}  # platform -> latest snap
        self._auto_timer = None
        self._setup_ui()
        self._load_last_snapshots()

    # ──────────────────────── UI 构建 ────────────────────────

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(8, 8, 8, 8)

        # ── 平台卡片行 ──
        cards_scroll = QScrollArea()
        cards_scroll.setWidgetResizable(True)
        cards_scroll.setFixedHeight(190)
        cards_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        cards_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        cards_scroll.setStyleSheet("QScrollArea { border: none; }")

        cards_widget = QWidget()
        cards_layout = QHBoxLayout(cards_widget)
        cards_layout.setSpacing(12)
        cards_layout.setContentsMargins(0, 0, 0, 0)

        self.platform_cards: dict[str, PlatformCard] = {}
        for p in ALL_PLATFORMS:
            card = PlatformCard(p)
            card.connect_clicked.connect(self._on_connect_platform)
            self.platform_cards[p] = card
            cards_layout.addWidget(card)
        cards_layout.addStretch()
        cards_scroll.setWidget(cards_widget)
        layout.addWidget(cards_scroll)

        # ── 汇总指标行 ──
        summary_frame = QFrame()
        summary_frame.setFrameStyle(QFrame.Shape.StyledPanel)
        summary_frame.setStyleSheet("QFrame { background: #f9f9f9; border-radius: 6px; border: none; }")
        summary_layout = QHBoxLayout(summary_frame)
        summary_layout.setContentsMargins(8, 6, 8, 6)
        summary_layout.setSpacing(10)

        self.sum_listings = SummaryCard("📦 总在售商品", "0", "#1976D2")
        self.sum_pending = SummaryCard("⏳ 待处理订单", "0", "#e65100")
        self.sum_revenue_today = SummaryCard("💰 今日总营收", "¥0", "#2e7d32")
        self.sum_revenue_30d = SummaryCard("📈 30日总营收", "¥0", "#7b1fa2")
        self.sum_alerts = SummaryCard("⚠️ 当前预警", "0", "#c62828")
        for c in [self.sum_listings, self.sum_pending, self.sum_revenue_today,
                  self.sum_revenue_30d, self.sum_alerts]:
            summary_layout.addWidget(c)
        layout.addWidget(summary_frame)

        # ── 控制栏 ──
        ctrl_layout = QHBoxLayout()
        self.refresh_all_btn = QPushButton("🔄 刷新所有平台")
        self.refresh_all_btn.setMinimumHeight(36)
        self.refresh_all_btn.setStyleSheet(
            "QPushButton { background: #1976D2; color: white; border-radius: 4px; "
            "padding: 6px 20px; font-size: 14px; font-weight: bold; }"
            "QPushButton:hover { background: #1565C0; }"
            "QPushButton:disabled { background: #bbb; }"
        )
        self.refresh_all_btn.clicked.connect(self._refresh_all_platforms)
        ctrl_layout.addWidget(self.refresh_all_btn)

        ctrl_layout.addWidget(QLabel("自动刷新:"))
        self.auto_refresh_combo = QComboBox()
        self.auto_refresh_combo.setMinimumHeight(36)
        self.auto_refresh_combo.addItems(["关闭", "每10分钟", "每30分钟", "每60分钟"])
        self.auto_refresh_combo.currentTextChanged.connect(self._on_auto_refresh_changed)
        ctrl_layout.addWidget(self.auto_refresh_combo)

        ctrl_layout.addStretch()
        self.status_label = QLabel("💡 点击「连接账号」登录各平台，或点击「刷新所有平台」更新数据")
        self.status_label.setStyleSheet("color: #555; font-size: 12px;")
        ctrl_layout.addWidget(self.status_label)
        layout.addLayout(ctrl_layout)

        # ── 详细数据 Tab Widget ──
        self.detail_tabs = QTabWidget()
        self.detail_tabs.setFont(QFont(GLOBAL_FONT_FAMILY, 12))

        # Tab1: 平台概览表格
        self.overview_table = self._build_overview_table()
        self.detail_tabs.addTab(self.overview_table, "📊 平台概览")

        # Tab2: 预警面板
        self.alert_panel = self._build_alert_panel()
        self.detail_tabs.addTab(self.alert_panel, "⚠️ 运营预警")

        # Tab3: 历史记录
        self.history_widget = self._build_history_widget()
        self.detail_tabs.addTab(self.history_widget, "📋 历史记录")

        layout.addWidget(self.detail_tabs)

    def _build_overview_table(self) -> QTableWidget:
        table = QTableWidget()
        table.setColumnCount(9)
        table.setHorizontalHeaderLabels([
            "平台", "状态", "在售商品", "浏览量", "收藏/询盘",
            "待处理订单", "今日成交", "今日营收", "30日营收"
        ])
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        table.setAlternatingRowColors(True)
        table.setFont(QFont(GLOBAL_FONT_FAMILY, 12))
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.setRowCount(len(ALL_PLATFORMS))
        for i, p in enumerate(ALL_PLATFORMS):
            icon = PLATFORM_ICON.get(p, "")
            name = PLATFORM_DISPLAY.get(p, p)
            table.setItem(i, 0, QTableWidgetItem(f"{icon} {name}"))
            table.setItem(i, 1, QTableWidgetItem("⚪ 未检测"))
            for col in range(2, 9):
                table.setItem(i, col, QTableWidgetItem("-"))
        return table

    def _build_alert_panel(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(4, 4, 4, 4)
        self.alert_text = QTextEdit()
        self.alert_text.setReadOnly(True)
        self.alert_text.setFont(QFont(GLOBAL_FONT_FAMILY, 12))
        self.alert_text.setPlaceholderText("暂无预警信息\n\n运营预警将在此处显示，包括：\n• 待处理订单过多\n• 库存不足\n• 今日营收异常波动\n• 账号登录状态异常")
        layout.addWidget(self.alert_text)
        return widget

    def _build_history_widget(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(4, 4, 4, 4)

        # 平台选择
        sel_layout = QHBoxLayout()
        sel_layout.addWidget(QLabel("查看平台:"))
        self.history_platform_combo = QComboBox()
        self.history_platform_combo.setMinimumHeight(32)
        for p in ALL_PLATFORMS:
            icon = PLATFORM_ICON.get(p, "")
            name = PLATFORM_DISPLAY.get(p, p)
            self.history_platform_combo.addItem(f"{icon} {name}", p)
        self.history_platform_combo.currentIndexChanged.connect(self._load_history)
        sel_layout.addWidget(self.history_platform_combo)

        load_btn = QPushButton("📅 加载历史")
        load_btn.setMinimumHeight(32)
        load_btn.clicked.connect(self._load_history)
        sel_layout.addWidget(load_btn)
        sel_layout.addStretch()
        layout.addLayout(sel_layout)

        self.history_table = QTableWidget()
        self.history_table.setColumnCount(7)
        self.history_table.setHorizontalHeaderLabels([
            "时间", "在售商品", "浏览量", "待处理订单",
            "今日成交", "今日营收", "30日营收"
        ])
        self.history_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.history_table.setAlternatingRowColors(True)
        self.history_table.setFont(QFont(GLOBAL_FONT_FAMILY, 12))
        self.history_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self.history_table)
        return widget

    # ──────────────────────── 业务逻辑 ────────────────────────

    def _load_last_snapshots(self):
        """从数据库加载最近一次快照"""
        try:
            for p in ALL_PLATFORMS:
                records = db.get_monitor_snapshots(p, days=1)
                if records:
                    r = records[0]
                    snap = MonitorSnapshot(
                        platform=p,
                        timestamp=str(r.get("snapshot_time", "")),
                        is_logged_in=bool(r.get("is_logged_in", False)),
                        active_listings=int(r.get("active_listings", 0)),
                        total_views=int(r.get("total_views", 0)),
                        total_wants=int(r.get("total_wants", 0)),
                        pending_orders=int(r.get("pending_orders", 0)),
                        completed_orders_today=int(r.get("completed_orders_today", 0)),
                        revenue_today=float(r.get("revenue_today", 0)),
                        revenue_30d=float(r.get("revenue_30d", 0)),
                        alerts=json.loads(r.get("alerts", "[]")),
                    )
                    self.snapshots[p] = snap
                    self._update_platform_card(p, snap)
                    self._update_overview_row(p, snap)
        except Exception as e:
            print(f"加载历史快照失败: {e}")
        self._update_summary()

    def _on_connect_platform(self, platform: str):
        """点击平台卡片的连接按钮"""
        if not self.main_window.is_licensed():
            QMessageBox.warning(self, "未激活", "请先激活License后使用监控功能")
            return

        # 如果已有快照且已登录，则刷新；否则等待登录
        snap = self.snapshots.get(platform)
        wait_login = (snap is None) or (not snap.is_logged_in)

        card = self.platform_cards.get(platform)
        if card:
            card.set_loading(True)

        worker = PlatformFetchWorker(platform, wait_login=wait_login)
        worker.progress.connect(self._on_fetch_progress)
        worker.snapshot_ready.connect(self._on_snapshot_ready)
        worker.error.connect(self._on_fetch_error)
        self.workers[platform] = worker
        worker.start()

        self.status_label.setText(
            f"正在采集 {PLATFORM_DISPLAY.get(platform)} 数据..."
            + (" (等待登录)" if wait_login else "")
        )

    def _refresh_all_platforms(self):
        """刷新所有平台（已连接的）"""
        if not self.main_window.is_licensed():
            QMessageBox.warning(self, "未激活", "请先激活License后使用监控功能")
            return

        # 只刷新已登录的平台
        connected = [p for p, s in self.snapshots.items() if s.is_logged_in]
        if not connected:
            connected = ALL_PLATFORMS  # 如果都未连接，全部尝试

        self.refresh_all_btn.setEnabled(False)
        self.refresh_all_btn.setText("刷新中...")
        self.status_label.setText("正在刷新所有平台数据...")

        for p in ALL_PLATFORMS:
            card = self.platform_cards.get(p)
            if card and p in connected:
                card.set_loading(True)

        worker = AllPlatformFetchWorker(connected)
        worker.progress.connect(self._on_fetch_progress)
        worker.snapshot_ready.connect(self._on_snapshot_ready)
        worker.all_done.connect(self._on_all_done)
        self.workers["__all__"] = worker
        worker.start()

    def _on_fetch_progress(self, msg: str):
        self.status_label.setText(msg)

    def _on_snapshot_ready(self, platform: str, snap):
        """收到某平台的快照"""
        self.snapshots[platform] = snap
        self._update_platform_card(platform, snap)
        self._update_overview_row(platform, snap)
        self._update_summary()
        self._update_alerts()

        card = self.platform_cards.get(platform)
        if card:
            card.set_loading(False)
            card.update_snapshot(snap)

    def _on_fetch_error(self, platform: str, error: str):
        self.status_label.setText(f"❌ {PLATFORM_DISPLAY.get(platform)} 采集失败: {error[:50]}")
        card = self.platform_cards.get(platform)
        if card:
            card.set_loading(False)

    def _on_all_done(self):
        self.refresh_all_btn.setEnabled(True)
        self.refresh_all_btn.setText("🔄 刷新所有平台")
        self.status_label.setText(f"✅ 全平台数据刷新完成  {datetime.now().strftime('%H:%M:%S')}")

    def _update_platform_card(self, platform: str, snap: MonitorSnapshot):
        card = self.platform_cards.get(platform)
        if card:
            card.update_snapshot(snap)

    def _update_overview_row(self, platform: str, snap: MonitorSnapshot):
        """更新概览表格对应行"""
        row = ALL_PLATFORMS.index(platform) if platform in ALL_PLATFORMS else -1
        if row < 0:
            return

        if snap.is_logged_in:
            status_text = "🟢 已连接"
            status_color = QColor("#2e7d32")
        elif snap.error:
            status_text = f"🔴 {snap.error[:12]}"
            status_color = QColor("#c62828")
        else:
            status_text = "⚪ 未检测"
            status_color = QColor("#666")

        self.overview_table.setItem(row, 1, QTableWidgetItem(status_text))
        self.overview_table.item(row, 1).setForeground(QBrush(status_color))

        values = [
            str(snap.active_listings),
            str(snap.total_views),
            str(snap.total_wants or snap.total_inquiries),
            str(snap.pending_orders),
            str(snap.completed_orders_today),
            f"¥{snap.revenue_today:.2f}",
            f"¥{snap.revenue_30d:.2f}",
        ]
        for col_offset, val in enumerate(values):
            self.overview_table.setItem(row, col_offset + 2, QTableWidgetItem(val))

        # 待处理订单 > 0 高亮
        if snap.pending_orders > 0:
            item = self.overview_table.item(row, 5)
            if item:
                item.setForeground(QBrush(QColor("#e65100")))
                item.setFont(QFont(GLOBAL_FONT_FAMILY, 12, QFont.Weight.Bold))

    def _update_summary(self):
        """更新汇总指标卡片"""
        total_listings = sum(s.active_listings for s in self.snapshots.values())
        total_pending = sum(s.pending_orders for s in self.snapshots.values())
        total_rev_today = sum(s.revenue_today for s in self.snapshots.values())
        total_rev_30d = sum(s.revenue_30d for s in self.snapshots.values())
        total_alerts = sum(len(s.alerts) for s in self.snapshots.values())

        self.sum_listings.set_value(str(total_listings))
        self.sum_pending.set_value(str(total_pending))
        self.sum_revenue_today.set_value(f"¥{total_rev_today:.0f}")
        self.sum_revenue_30d.set_value(f"¥{total_rev_30d:.0f}")
        self.sum_alerts.set_value(str(total_alerts))

    def _update_alerts(self):
        """更新预警面板"""
        all_alerts = []
        for p, s in self.snapshots.items():
            for alert in s.alerts:
                name = PLATFORM_DISPLAY.get(p, p)
                icon = PLATFORM_ICON.get(p, "")
                all_alerts.append(f"[{icon}{name}] {alert}")

        if all_alerts:
            self.alert_text.setHtml(
                "<br>".join(
                    f'<span style="font-size:13px; color:#c62828;">{a}</span>'
                    for a in all_alerts
                )
            )
            # 更新 Tab 标题
            self.detail_tabs.setTabText(1, f"⚠️ 运营预警 ({len(all_alerts)})")
        else:
            self.alert_text.setPlainText("✅ 当前无运营预警")
            self.detail_tabs.setTabText(1, "⚠️ 运营预警")

    def _load_history(self):
        """加载历史记录"""
        platform = self.history_platform_combo.currentData()
        try:
            records = db.get_monitor_snapshots(platform, days=30)
            self.history_table.setRowCount(len(records))
            for i, r in enumerate(records):
                ts = str(r.get("snapshot_time", ""))[:16]
                self.history_table.setItem(i, 0, QTableWidgetItem(ts))
                self.history_table.setItem(i, 1, QTableWidgetItem(str(r.get("active_listings", 0))))
                self.history_table.setItem(i, 2, QTableWidgetItem(str(r.get("total_views", 0))))
                self.history_table.setItem(i, 3, QTableWidgetItem(str(r.get("pending_orders", 0))))
                self.history_table.setItem(i, 4, QTableWidgetItem(str(r.get("completed_orders_today", 0))))
                self.history_table.setItem(i, 5, QTableWidgetItem(f"¥{float(r.get('revenue_today', 0)):.2f}"))
                self.history_table.setItem(i, 6, QTableWidgetItem(f"¥{float(r.get('revenue_30d', 0)):.2f}"))
        except Exception as e:
            QMessageBox.warning(self, "错误", f"加载历史记录失败: {e}")

    def _on_auto_refresh_changed(self, text: str):
        """自动刷新设置"""
        if self._auto_timer:
            self._auto_timer.stop()
            self._auto_timer = None

        intervals = {
            "关闭": 0,
            "每10分钟": 600_000,
            "每30分钟": 1_800_000,
            "每60分钟": 3_600_000,
        }
        ms = intervals.get(text, 0)
        if ms > 0:
            self._auto_timer = QTimer()
            self._auto_timer.timeout.connect(self._refresh_all_platforms)
            self._auto_timer.start(ms)
            self.status_label.setText(f"✅ 自动刷新已开启: {text}")
        else:
            self.status_label.setText("自动刷新已关闭")

    def refresh_data(self):
        """被 MainWindow 调用的刷新接口"""
        self._load_last_snapshots()

    def closeEvent(self, event):
        if self._auto_timer:
            self._auto_timer.stop()
        for w in self.workers.values():
            if w and w.isRunning():
                w.terminate()
        event.accept()
