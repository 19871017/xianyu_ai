import sys
import os

# 加载环境变量
env_path = os.path.join(os.path.expanduser("~"), ".xf_env")
if os.path.exists(env_path):
    with open(env_path, "r") as f:
        for line in f:
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                key, value = line.split("=", 1)
                os.environ[key.strip()] = value.strip()

from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QIcon
from ui.main_window import MainWindow
from utils.helpers import app_icon_path
from ui.update_prompt import UpdatePromptManager


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    icon_path = app_icon_path()
    if icon_path:
        app.setWindowIcon(QIcon(icon_path))

    # 授权门禁：由 MainWindow 各功能页的 is_licensed()（在线/离线强制验签）
    # 与 engine 层能力令牌守卫（license.capability_guard）共同把关；
    # 破解本地授权文件也拿不到服务端签名令牌，核心功能仍不可用。
    window = MainWindow()
    window.show()

    # 启动后异步检测新版本，有更新则弹窗引导到下载站（不阻塞启动）。
    window._update_prompt = UpdatePromptManager(window)
    window._update_prompt.start()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
