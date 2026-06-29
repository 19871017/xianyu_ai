"""客户端更新提示：启动后异步检测新版本，发现后在主线程弹窗。

设计：
- 检测在 QThread 后台进行，不阻塞启动与主界面渲染。
- 仅在确实有新版本时弹窗；网络不可达/无新版静默跳过。
- 用户点「确定」打开下载站；强制更新仅在文案上强调，不阻断使用。
"""
from __future__ import annotations

import logging

from PyQt6.QtCore import QThread, pyqtSignal, QObject
from PyQt6.QtWidgets import QMessageBox
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtCore import QUrl

from engine.update_checker import check_update
from config import APP_VERSION

logger = logging.getLogger(__name__)


class _UpdateWorker(QThread):
    """后台检测最新版本，结果通过信号回主线程。"""
    done = pyqtSignal(dict)

    def run(self):
        try:
            res = check_update(APP_VERSION)
        except Exception as e:  # 检测失败不打扰用户
            logger.info(f"更新检测异常（忽略）: {e}")
            res = {"has_update": False}
        self.done.emit(res)


class UpdatePromptManager(QObject):
    """挂在主窗口上的更新提示管理器（持有 worker 生命周期）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._worker = None

    def start(self):
        self._worker = _UpdateWorker()
        self._worker.done.connect(self._on_done)
        self._worker.start()

    def _on_done(self, res: dict):
        if not res or not res.get("has_update"):
            return
        ver = res.get("version") or ""
        notes = (res.get("notes") or "").strip()
        url = res.get("download_url") or ""
        force = res.get("force_update")
        title = "发现新版本"
        body = f"检测到新版本 v{ver}（当前 v{APP_VERSION}）。"
        if force:
            body += "\n\n这是一次重要更新，建议立即升级。"
        if notes:
            body += f"\n\n更新内容：\n{notes[:500]}"
        body += "\n\n点击「确定」前往下载页面获取新版本。"
        box = QMessageBox()
        box.setWindowTitle(title)
        box.setIcon(QMessageBox.Icon.Information)
        box.setText(body)
        box.setStandardButtons(QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel)
        box.button(QMessageBox.StandardButton.Ok).setText("确定")
        box.button(QMessageBox.StandardButton.Cancel).setText("稍后")
        if box.exec() == QMessageBox.StandardButton.Ok and url:
            QDesktopServices.openUrl(QUrl(url))
