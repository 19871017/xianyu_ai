"""定时调度 Tab：管理周期任务（源复检 / 采集 / 闲鱼擦亮）。

设计要点：
  - 任务配置落库（scheduled_tasks 表），增删改 / 启停由本 Tab 操作。
  - 到期判断复用 engine.scheduler 的纯逻辑（compute_next_run/due_tasks）。
  - 真正的「执行」由主窗口的 QTimer 周期性调用 run_due_tasks 分发，
    复用已实测的引擎（RecheckEngine / 各采集器 / XianyuRefresher），
    调度本身不直接碰浏览器。
  - 同一时刻只跑一个调度任务，避免多浏览器会话互相抢占登录态。
"""
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox,
    QComboBox, QLineEdit, QSpinBox, QTextEdit, QDialog, QFormLayout,
    QDialogButtonBox, QCheckBox,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QFont, QColor, QBrush

from datetime import datetime

from engine.scheduler import (
    compute_next_run, due_tasks, validate_task, describe_schedule,
)
from database.db_manager import db


GLOBAL_FONT_FAMILY = "Microsoft YaHei, PingFang SC, sans-serif"

TASK_TYPE_LABELS = {
    "recheck": "源商品复检",
    "collect": "链接采集",
    "polish": "闲鱼擦亮",
}
TASK_TYPE_VALUES = {v: k for k, v in TASK_TYPE_LABELS.items()}


class TaskRunWorker(QThread):
    """后台执行单个到期任务。按任务类型分发到对应引擎。"""
    progress_msg = pyqtSignal(str)
    finished_run = pyqtSignal(int, bool, str)  # task_id, ok, summary

    def __init__(self, task: dict):
        super().__init__()
        self.task = task or {}

    def run(self):
        tid = int(self.task.get("id") or 0)
        ttype = self.task.get("task_type")

        def log(msg):
            self.progress_msg.emit(str(msg))

        try:
            if ttype == "recheck":
                ok, summary = self._run_recheck(log)
            elif ttype == "collect":
                ok, summary = self._run_collect(log)
            elif ttype == "polish":
                ok, summary = self._run_polish(log)
            else:
                ok, summary = False, f"未知任务类型：{ttype}"
        except Exception as e:
            ok, summary = False, f"执行异常：{e}"
            log(summary)

        self.finished_run.emit(tid, ok, summary)

    def _run_recheck(self, log):
        from engine.source_recheck import RecheckEngine
        # 定时复检聚焦「已上架」商品：防止卖出后源头涨价/售罄/下架造成
        # 亏损或缺货。全量复检（含未上架/拼多多风控）太重，不在定时任务里跑。
        listed = ("listed_xianyu", "listed_goofishpro")
        products = [
            p for p in db.get_all_products()
            if (p.get("source_url") or "").strip()
            and (p.get("source_platform") or p.get("platform")) in ("1688", "taobao", "jd", "pdd")
            and (p.get("status") or "") in listed
        ]
        if not products:
            return True, "无已上架商品可复检（跳过）"
        engine = RecheckEngine(on_log=log)

        def on_item(done, total, row):
            try:
                db.save_recheck(row)
            except Exception:
                pass

        results = engine.recheck_products(products, on_item=on_item)
        crit = sum(1 for r in results if r.get("level") == "critical")
        warn = sum(1 for r in results if r.get("level") == "warn")
        return True, f"复检 {len(results)} 个，严重 {crit}，警告 {warn}"

    def _run_collect(self, log):
        from ui.collect_tab import COLLECTOR_CLASSES  # 复用采集器映射
        links = self.task.get("params", {}).get("links") or []
        links = [u.strip() for u in links if str(u).strip()]
        if not links:
            return True, "未配置采集链接（跳过）"

        from urllib.parse import urlparse

        def platform_of(url):
            host = (urlparse(url).hostname or "").lower()
            if "1688" in host:
                return "1688"
            if "taobao" in host or "tmall" in host:
                return "taobao"
            if "jd.com" in host or "jd.hk" in host:
                return "jd"
            if "pinduoduo" in host or "yangkeduo" in host:
                return "pdd"
            if "goofish" in host or "xianyu" in host:
                return "xianyu"
            return ""

        groups: dict[str, list] = {}
        for u in links:
            p = platform_of(u)
            if p:
                groups.setdefault(p, []).append(u)

        total_saved = 0
        for plat, urls in groups.items():
            collector_cls = COLLECTOR_CLASSES.get(plat)
            if not collector_cls:
                continue
            collector = collector_cls(on_progress=log)
            try:
                if hasattr(collector, "collect_by_links"):
                    items = collector.collect_by_links(urls) or []
                else:
                    items = []
                    for u in urls:
                        items.extend(collector.collect_by_link(u) or [])
            except Exception as e:
                log(f"  [{plat}] 采集失败：{e}")
                continue
            for it in items:
                try:
                    db.save_product(it)
                    total_saved += 1
                except Exception:
                    pass
        return True, f"采集落库 {total_saved} 个商品"

    def _run_polish(self, log):
        from engine.xianyu_refresh import XianyuRefresher
        refresher = XianyuRefresher(on_log=log)
        if not refresher.open(timeout=300):
            return False, "闲鱼登录失败，擦亮中止"
        try:
            res = refresher.refresh_all()
        finally:
            refresher.close()
        return bool(res.get("ok")) or res.get("found", 0) == 0, res.get("note", "")


class TaskDialog(QDialog):
    """新增/编辑定时任务对话框。"""

    def __init__(self, parent=None, task: dict | None = None):
        super().__init__(parent)
        self.setWindowTitle("定时任务")
        self.setMinimumWidth(420)
        self.task = dict(task or {})
        self._build()

    def _build(self):
        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.name_edit = QLineEdit(self.task.get("name", ""))
        form.addRow("任务名称:", self.name_edit)

        self.type_combo = QComboBox()
        for label in TASK_TYPE_LABELS.values():
            self.type_combo.addItem(label)
        cur_type = TASK_TYPE_LABELS.get(self.task.get("task_type", "recheck"))
        if cur_type:
            self.type_combo.setCurrentText(cur_type)
        self.type_combo.currentTextChanged.connect(self._on_type_changed)
        form.addRow("任务类型:", self.type_combo)

        self.trigger_combo = QComboBox()
        self.trigger_combo.addItems(["按间隔", "每天定点"])
        self.trigger_combo.setCurrentIndex(0 if self.task.get("trigger", "interval") == "interval" else 1)
        self.trigger_combo.currentIndexChanged.connect(self._on_trigger_changed)
        form.addRow("触发方式:", self.trigger_combo)

        self.interval_spin = QSpinBox()
        self.interval_spin.setRange(1, 100000)
        self.interval_spin.setValue(int(self.task.get("interval_minutes") or 360))
        self.interval_spin.setSuffix(" 分钟")
        form.addRow("运行间隔:", self.interval_spin)

        self.daily_edit = QLineEdit(self.task.get("daily_time", "09:00"))
        self.daily_edit.setPlaceholderText("HH:MM，如 09:30")
        form.addRow("每天时间:", self.daily_edit)

        self.links_edit = QTextEdit()
        self.links_edit.setPlaceholderText("每行一个商品链接（仅采集任务需要）")
        self.links_edit.setMaximumHeight(120)
        links = self.task.get("params", {}).get("links") or []
        if links:
            self.links_edit.setPlainText("\n".join(links))
        form.addRow("采集链接:", self.links_edit)

        self.enabled_cb = QCheckBox("启用该任务")
        self.enabled_cb.setChecked(bool(self.task.get("enabled", True)))
        form.addRow("", self.enabled_cb)

        layout.addLayout(form)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self._on_accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

        self._on_type_changed()
        self._on_trigger_changed()

    def _on_trigger_changed(self):
        is_interval = self.trigger_combo.currentIndex() == 0
        self.interval_spin.setVisible(is_interval)
        self.daily_edit.setVisible(not is_interval)

    def _on_type_changed(self):
        is_collect = TASK_TYPE_VALUES.get(self.type_combo.currentText()) == "collect"
        self.links_edit.setVisible(is_collect)

    def _on_accept(self):
        task = dict(self.task)
        task["name"] = self.name_edit.text().strip() or self.type_combo.currentText()
        task["task_type"] = TASK_TYPE_VALUES.get(self.type_combo.currentText(), "recheck")
        task["trigger"] = "interval" if self.trigger_combo.currentIndex() == 0 else "daily"
        task["interval_minutes"] = self.interval_spin.value()
        task["daily_time"] = self.daily_edit.text().strip() or "09:00"
        task["enabled"] = self.enabled_cb.isChecked()
        links = [l.strip() for l in self.links_edit.toPlainText().splitlines() if l.strip()]
        task["params"] = {"links": links}

        chk = validate_task(task)
        if not chk["ok"]:
            QMessageBox.warning(self, "配置有误", "\n".join(chk["reasons"]))
            return
        if task["task_type"] == "collect" and not links:
            QMessageBox.warning(self, "缺少链接", "采集任务需要至少一个商品链接。")
            return
        self.result_task = task
        self.accept()


class SchedulerTab(QWidget):
    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self.worker = None
        self._running_task_id = None
        self._setup_ui()
        self.reload_tasks()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.setContentsMargins(8, 8, 8, 8)

        tip = QLabel(
            "定时调度：让源复检、链接采集、闲鱼擦亮按计划自动运行。\n"
            "调度在软件运行期生效（关闭软件则暂停）；同一时刻只跑一个任务，"
            "避免多个浏览器会话互相抢登录态。擦亮带安全护栏，绝不下架/删除商品。"
        )
        tip.setWordWrap(True)
        tip.setFont(QFont(GLOBAL_FONT_FAMILY, 12))
        tip.setStyleSheet("color:#4527a0; background:#ede7f6; padding:8px; border-radius:4px;")
        layout.addWidget(tip)

        ctrl = QHBoxLayout()
        self.add_btn = QPushButton("➕ 新增任务")
        self.add_btn.setMinimumHeight(36)
        self.add_btn.clicked.connect(self._add_task)
        ctrl.addWidget(self.add_btn)

        self.edit_btn = QPushButton("✏️ 编辑")
        self.edit_btn.setMinimumHeight(36)
        self.edit_btn.clicked.connect(self._edit_task)
        ctrl.addWidget(self.edit_btn)

        self.toggle_btn = QPushButton("⏯ 启用/停用")
        self.toggle_btn.setMinimumHeight(36)
        self.toggle_btn.clicked.connect(self._toggle_task)
        ctrl.addWidget(self.toggle_btn)

        self.del_btn = QPushButton("🗑 删除")
        self.del_btn.setMinimumHeight(36)
        self.del_btn.clicked.connect(self._delete_task)
        ctrl.addWidget(self.del_btn)

        self.run_now_btn = QPushButton("▶️ 立即运行")
        self.run_now_btn.setMinimumHeight(36)
        self.run_now_btn.clicked.connect(self._run_selected_now)
        ctrl.addWidget(self.run_now_btn)

        ctrl.addStretch()
        layout.addLayout(ctrl)

        self.status_label = QLabel("调度就绪。")
        self.status_label.setFont(QFont(GLOBAL_FONT_FAMILY, 12, QFont.Weight.Bold))
        layout.addWidget(self.status_label)

        self.table = QTableWidget()
        self.table.setColumnCount(7)
        self.table.setHorizontalHeaderLabels(
            ["状态", "名称", "类型", "计划", "下次运行", "上次运行", "上次结果"]
        )
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        layout.addWidget(self.table)

        self.log_area = QTextEdit()
        self.log_area.setReadOnly(True)
        self.log_area.setMaximumHeight(140)
        self.log_area.setPlaceholderText("调度执行日志…")
        layout.addWidget(self.log_area)

    # ── 数据 ──
    def reload_tasks(self):
        try:
            self.tasks = db.get_scheduled_tasks()
        except Exception as e:
            self.tasks = []
            self._append_log(f"读取任务失败：{e}")
        self._fill_table()

    def _fill_table(self):
        rows = self.tasks
        self.table.setRowCount(len(rows))
        now = datetime.now()
        enabled_n = 0
        for i, t in enumerate(rows):
            enabled = bool(t.get("enabled", True))
            if enabled:
                enabled_n += 1
            st = QTableWidgetItem("启用" if enabled else "停用")
            st.setForeground(QBrush(QColor("#2e7d32" if enabled else "#999")))
            self.table.setItem(i, 0, st)
            self.table.setItem(i, 1, QTableWidgetItem(str(t.get("name", ""))))
            self.table.setItem(i, 2, QTableWidgetItem(TASK_TYPE_LABELS.get(t.get("task_type"), t.get("task_type", ""))))
            self.table.setItem(i, 3, QTableWidgetItem(describe_schedule(t)))
            try:
                nxt = compute_next_run(t, now).strftime("%m-%d %H:%M") if enabled else "—"
            except Exception:
                nxt = "—"
            self.table.setItem(i, 4, QTableWidgetItem(nxt))
            last = t.get("last_run") or "从未"
            if isinstance(last, str) and len(last) > 16:
                last = last[:16].replace("T", " ")
            self.table.setItem(i, 5, QTableWidgetItem(str(last)))
            self.table.setItem(i, 6, QTableWidgetItem(str(t.get("last_result", ""))[:60]))
        self.status_label.setText(f"共 {len(rows)} 个任务，启用 {enabled_n} 个。")

    def _selected_task(self):
        row = self.table.currentRow()
        if row < 0 or row >= len(self.tasks):
            return None
        return self.tasks[row]

    # ── 操作 ──
    def _add_task(self):
        dlg = TaskDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            db.save_scheduled_task(dlg.result_task)
            self.reload_tasks()
            self._append_log(f"已新增任务：{dlg.result_task['name']}")

    def _edit_task(self):
        t = self._selected_task()
        if not t:
            QMessageBox.information(self, "提示", "请先选中一行任务。")
            return
        dlg = TaskDialog(self, task=t)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            dlg.result_task["id"] = t["id"]
            db.save_scheduled_task(dlg.result_task)
            self.reload_tasks()
            self._append_log(f"已更新任务：{dlg.result_task['name']}")

    def _toggle_task(self):
        t = self._selected_task()
        if not t:
            QMessageBox.information(self, "提示", "请先选中一行任务。")
            return
        db.set_task_enabled(t["id"], not bool(t.get("enabled", True)))
        self.reload_tasks()

    def _delete_task(self):
        t = self._selected_task()
        if not t:
            QMessageBox.information(self, "提示", "请先选中一行任务。")
            return
        if QMessageBox.question(self, "确认删除", f"删除任务「{t.get('name')}」？") != QMessageBox.StandardButton.Yes:
            return
        db.delete_scheduled_task(t["id"])
        self.reload_tasks()

    def _run_selected_now(self):
        t = self._selected_task()
        if not t:
            QMessageBox.information(self, "提示", "请先选中一行任务。")
            return
        self._dispatch(t)

    # ── 调度执行（主窗口 QTimer 调用）──
    def run_due_tasks(self):
        """检查到期任务并执行。同一时刻只跑一个，避免会话冲突。"""
        if self.worker and self.worker.isRunning():
            return
        try:
            tasks = db.get_scheduled_tasks()
        except Exception:
            return
        due = due_tasks(tasks)
        if not due:
            return
        self._dispatch(due[0])

    def _dispatch(self, task: dict):
        if self.worker and self.worker.isRunning():
            QMessageBox.information(self, "调度忙", "已有任务在运行，请稍候。")
            return
        self._running_task_id = int(task.get("id") or 0)
        self._append_log(f"▶️ 开始执行：{task.get('name')}（{TASK_TYPE_LABELS.get(task.get('task_type'),'')}）")
        self.worker = TaskRunWorker(task)
        self.worker.progress_msg.connect(self._append_log)
        self.worker.finished_run.connect(self._on_task_finished)
        self.worker.start()

    def _on_task_finished(self, task_id, ok, summary):
        try:
            db.mark_task_run(task_id, summary)
        except Exception:
            pass
        flag = "✅" if ok else "⚠️"
        self._append_log(f"{flag} 任务完成：{summary}")
        # 采集任务可能新增商品，刷新共享数据。
        try:
            self.main_window.reload_from_db()
        except Exception:
            pass
        self.reload_tasks()

    def _append_log(self, msg):
        self.log_area.append(msg)
        sb = self.log_area.verticalScrollBar()
        sb.setValue(sb.maximum())
