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
from ui.main_window import MainWindow
from license.license_validator import LicenseValidator


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    # License检查
    validator = LicenseValidator()
    result = validator.verify()

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
