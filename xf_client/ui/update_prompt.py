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
        if force:
            self._prompt_force(ver, notes, url)
        else:
            self._prompt_optional(ver, notes, url)

    def _prompt_optional(self, ver, notes, url):
        """普通更新：可「稍后」，不阻断使用。"""
        body = f"检测到新版本 v{ver}（当前 v{APP_VERSION}）。"
        if notes:
            body += f"\n\n更新内容：\n{notes[:500]}"
        body += "\n\n点击「确定」前往下载页面获取新版本。"
        box = QMessageBox()
        box.setWindowTitle("发现新版本")
        box.setIcon(QMessageBox.Icon.Information)
        box.setText(body)
        box.setStandardButtons(QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel)
        box.button(QMessageBox.StandardButton.Ok).setText("确定")
        box.button(QMessageBox.StandardButton.Cancel).setText("稍后")
        if box.exec() == QMessageBox.StandardButton.Ok and url:
            QDesktopServices.openUrl(QUrl(url))

    def _prompt_force(self, ver, notes, url):
        """强制更新：旧版本停止使用。仅「去下载」按钮，关闭即退出程序。"""
        body = (
            f"检测到重要更新 v{ver}（当前 v{APP_VERSION}）。\n\n"
            "当前版本已停止支持，必须升级后才能继续使用。"
        )
        if notes:
            body += f"\n\n更新内容：\n{notes[:500]}"
        body += "\n\n点击「去下载」前往下载页面获取新版本，程序将退出。"
        box = QMessageBox()
        box.setWindowTitle("需要更新")
        box.setIcon(QMessageBox.Icon.Warning)
        box.setText(body)
        # 仅一个按钮；移除窗口关闭按钮也无法绕过——无论如何都会退出。
        box.setStandardButtons(QMessageBox.StandardButton.Ok)
        box.button(QMessageBox.StandardButton.Ok).setText("去下载")
        box.exec()
        if url:
            QDesktopServices.openUrl(QUrl(url))
        self._force_quit()

    @staticmethod
    def _force_quit():
        """强制退出应用：旧版本不允许继续运行。"""
        try:
            from PyQt6.QtWidgets import QApplication
            app = QApplication.instance()
            if app is not None:
                app.quit()
        except Exception:
            pass
        import os
        os._exit(0)
