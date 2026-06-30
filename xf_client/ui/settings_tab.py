import json
import os
import requests
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QLineEdit, QGroupBox, QMessageBox,
    QApplication, QComboBox, QFormLayout, QSizePolicy, QCheckBox,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QCursor, QFont
from license.license_validator import LicenseValidator
from license.machine_id import get_machine_id
from database.db_manager import db
from config import SERVER_URL, AI_API_URL, AI_API_KEY, AI_MODEL


# 全局字体常量（与main_window一致）
GLOBAL_FONT_FAMILY = "Microsoft YaHei, PingFang SC, sans-serif"


def _load_env_config():
    """从 ~/.xf_env 读取AI配置"""
    env_path = os.path.join(os.path.expanduser("~"), ".xf_env")
    config = {"AI_API_URL": "", "AI_API_MODEL": "", "AI_API_KEY": ""}
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if "=" in line:
                    k, v = line.split("=", 1)
                    k = k.strip()
                    v = v.strip()
                    if k in config:
                        config[k] = v
    # 环境变量覆盖
    config["AI_API_URL"] = config["AI_API_URL"] or AI_API_URL
    config["AI_API_MODEL"] = config["AI_API_MODEL"] or AI_MODEL
    config["AI_API_KEY"] = config["AI_API_KEY"] or AI_API_KEY
    return config


class FetchModelsWorker(QThread):
    """后台拉取模型列表"""
    finished = pyqtSignal(list)
    error = pyqtSignal(str)

    def __init__(self, api_url, api_key):
        super().__init__()
        self.api_url = api_url.rstrip("/")
        self.api_key = api_key

    def run(self):
        try:
            base = self.api_url
            # 补全 /v1/models 路径
            if "/v1" not in base:
                url = f"{base}/v1/models"
            elif base.endswith("/v1"):
                url = f"{base}/models"
            else:
                url = f"{base}/models"

            resp = requests.get(
                url,
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=(10, 60),
            )
            if resp.status_code != 200:
                self.error.emit(f"请求失败 ({resp.status_code}): {resp.text[:200]}")
                return

            data = resp.json()
            models = []
            if isinstance(data, dict) and "data" in data:
                for m in data["data"]:
                    mid = m.get("id", "")
                    if mid:
                        models.append(mid)
            elif isinstance(data, list):
                for m in data:
                    mid = m.get("id", "") if isinstance(m, dict) else str(m)
                    if mid:
                        models.append(mid)

            models.sort()
            self.finished.emit(models)
        except requests.exceptions.ConnectionError:
            self.error.emit("连接失败，请检查API地址是否正确")
        except requests.exceptions.Timeout:
            self.error.emit("请求超时，请稍后重试")
        except Exception as e:
            self.error.emit(str(e))


class ClickableLabel(QLabel):
    """可点击复制的Label"""
    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self.setCursor(QCursor(Qt.CursorShape.IBeamCursor))
        self.setToolTip("点击复制")
        self._full_text = text

    def setText(self, text):
        self._full_text = text
        super().setText(text)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            clipboard = QApplication.clipboard()
            clipboard.setText(self._full_text)
            old_style = self.styleSheet()
            self.setStyleSheet("color: #2e7d32; font-weight: bold;")
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(800, lambda: self.setStyleSheet(old_style))


class SettingsTab(QWidget):
    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self.machine_id = get_machine_id()
        self._ai_config = _load_env_config()
        self._models_worker = None
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        # ════════════ License激活 ════════════
        license_group = QGroupBox("🔑 License激活")
        license_group.setFont(QFont(GLOBAL_FONT_FAMILY, 13, QFont.Weight.Bold))
        license_layout = QVBoxLayout(license_group)
        license_layout.setSpacing(8)

        self.license_status = QLabel()
        self._update_license_status()
        license_layout.addWidget(self.license_status)

        # 机器码
        machine_layout = QHBoxLayout()
        machine_layout.addWidget(QLabel("本机机器码:"))
        self.machine_id_label = ClickableLabel(self.machine_id)
        self.machine_id_label.setStyleSheet(
            "color: #1565c0; font-family: 'Courier New', monospace; "
            "font-size: 15px; padding: 6px 10px; "
            "background: #e3f2fd; border-radius: 4px;"
        )
        machine_layout.addWidget(self.machine_id_label)

        self.copy_machine_btn = QPushButton("📋 复制")
        self.copy_machine_btn.clicked.connect(self._copy_machine_id)
        machine_layout.addWidget(self.copy_machine_btn)
        machine_layout.addStretch()
        license_layout.addLayout(machine_layout)

        self.copy_hint = QLabel("✅ 已复制到剪贴板")
        self.copy_hint.setStyleSheet("color: #2e7d32; font-size: 13px;")
        self.copy_hint.setVisible(False)
        license_layout.addWidget(self.copy_hint)

        # 输入激活码
        key_layout = QHBoxLayout()
        self.license_input = QLineEdit()
        self.license_input.setPlaceholderText("输入License Key")
        self.license_input.setMinimumHeight(36)
        key_layout.addWidget(self.license_input)

        self.activate_btn = QPushButton("🔑 激活")
        self.activate_btn.setMinimumHeight(36)
        self.activate_btn.clicked.connect(self._activate)
        key_layout.addWidget(self.activate_btn)
        license_layout.addLayout(key_layout)

        layout.addWidget(license_group)

        # ════════════ 服务器配置 ════════════
        server_group = QGroupBox("🌐 服务器配置")
        server_group.setFont(QFont(GLOBAL_FONT_FAMILY, 13, QFont.Weight.Bold))
        server_layout = QVBoxLayout(server_group)
        server_layout.addWidget(QLabel(f"服务器地址: {SERVER_URL}"))

        self.test_btn = QPushButton("🔌 测试连接")
        self.test_btn.setMinimumHeight(36)
        self.test_btn.clicked.connect(self._test_connection)
        server_layout.addWidget(self.test_btn)

        layout.addWidget(server_group)

        # ════════════ AI API配置 ════════════
        api_group = QGroupBox("🤖 AI API配置（兼容OpenAI格式中转）")
        api_group.setFont(QFont(GLOBAL_FONT_FAMILY, 13, QFont.Weight.Bold))
        api_layout = QVBoxLayout(api_group)
        api_layout.setSpacing(10)

        # 提示
        hint = QLabel("填写API地址和Key后，点击「拉取模型」自动获取可用模型列表")
        hint.setStyleSheet("color: #666; font-size: 13px;")
        hint.setWordWrap(True)
        api_layout.addWidget(hint)

        # API地址
        url_layout = QHBoxLayout()
        url_label = QLabel("API地址:")
        url_label.setMinimumWidth(80)
        url_layout.addWidget(url_label)
        self.api_url_input = QLineEdit()
        self.api_url_input.setPlaceholderText("如: https://api.deepseek.com 或 http://127.0.0.1:3000")
        self.api_url_input.setText(self._ai_config.get("AI_API_URL", ""))
        self.api_url_input.setMinimumHeight(36)
        url_layout.addWidget(self.api_url_input)
        api_layout.addLayout(url_layout)

        # API Key
        key_layout2 = QHBoxLayout()
        key_label2 = QLabel("API Key:")
        key_label2.setMinimumWidth(80)
        key_layout2.addWidget(key_label2)
        self.api_key_input = QLineEdit()
        self.api_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key_input.setPlaceholderText("输入API Key")
        self.api_key_input.setText(self._ai_config.get("AI_API_KEY", ""))
        self.api_key_input.setMinimumHeight(36)
        key_layout2.addWidget(self.api_key_input)
        api_layout.addLayout(key_layout2)

        # 拉取模型按钮
        fetch_layout = QHBoxLayout()
        fetch_layout.addStretch()
        self.fetch_models_btn = QPushButton("🔄 拉取模型列表")
        self.fetch_models_btn.setMinimumHeight(36)
        self.fetch_models_btn.setStyleSheet(
            "QPushButton { background: #1976D2; color: white; "
            "border-radius: 4px; padding: 6px 20px; font-size: 14px; font-weight: bold; }"
            "QPushButton:hover { background: #1565C0; }"
        )
        self.fetch_models_btn.clicked.connect(self._fetch_models)
        fetch_layout.addWidget(self.fetch_models_btn)
        fetch_layout.addStretch()
        api_layout.addLayout(fetch_layout)

        # 模型选择
        model_layout = QHBoxLayout()
        model_label = QLabel("选择模型:")
        model_label.setMinimumWidth(80)
        model_layout.addWidget(model_label)
        self.model_combo = QComboBox()
        self.model_combo.setMinimumHeight(36)
        self.model_combo.setEditable(True)
        self.model_combo.setPlaceholderText("请先拉取模型列表，或手动输入模型名")
        saved_model = self._ai_config.get("AI_API_MODEL", "")
        if saved_model:
            self.model_combo.addItem(saved_model)
            self.model_combo.setCurrentText(saved_model)
        model_layout.addWidget(self.model_combo)
        api_layout.addLayout(model_layout)

        # 保存 + 测试
        btn_layout = QHBoxLayout()
        self.save_key_btn = QPushButton("💾 保存配置")
        self.save_key_btn.setMinimumHeight(40)
        self.save_key_btn.setStyleSheet(
            "QPushButton { background: #2e7d32; color: white; "
            "border-radius: 4px; padding: 6px 24px; font-size: 14px; font-weight: bold; }"
            "QPushButton:hover { background: #1B5E20; }"
        )
        self.save_key_btn.clicked.connect(self._save_api_config)
        btn_layout.addWidget(self.save_key_btn)

        self.test_ai_btn = QPushButton("🧪 测试AI连接")
        self.test_ai_btn.setMinimumHeight(40)
        self.test_ai_btn.setStyleSheet(
            "QPushButton { background: #f57c00; color: white; "
            "border-radius: 4px; padding: 6px 24px; font-size: 14px; font-weight: bold; }"
            "QPushButton:hover { background: #E65100; }"
        )
        self.test_ai_btn.clicked.connect(self._test_ai_connection)
        btn_layout.addWidget(self.test_ai_btn)

        btn_layout.addStretch()
        api_layout.addLayout(btn_layout)

        # 状态提示
        self.ai_status_label = QLabel("")
        self.ai_status_label.setStyleSheet("font-size: 13px;")
        api_layout.addWidget(self.ai_status_label)

        layout.addWidget(api_group)

        # ════════════ 数据管理（历史数据清理）════════════
        data_group = QGroupBox("🗂 数据管理（历史数据清理）")
        data_group.setFont(QFont(GLOBAL_FONT_FAMILY, 13, QFont.Weight.Bold))
        data_layout = QVBoxLayout(data_group)
        data_layout.setSpacing(8)

        self.data_counts_label = QLabel("")
        self.data_counts_label.setStyleSheet("color: #555; font-size: 13px;")
        self.data_counts_label.setWordWrap(True)
        data_layout.addWidget(self.data_counts_label)

        self.del_local_checkbox = QCheckBox("同时删除本地关联文件（采集的图片目录，不可恢复）")
        self.del_local_checkbox.setStyleSheet("font-size: 13px;")
        data_layout.addWidget(self.del_local_checkbox)

        # 第一行：商品 / 订单 / 采集记录
        row1 = QHBoxLayout()
        self.clear_products_btn = QPushButton("🗑 清空商品")
        self.clear_products_btn.setMinimumHeight(34)
        self.clear_products_btn.clicked.connect(self._clear_products)
        row1.addWidget(self.clear_products_btn)

        self.clear_orders_btn = QPushButton("🗑 清空订单")
        self.clear_orders_btn.setMinimumHeight(34)
        self.clear_orders_btn.clicked.connect(self._clear_orders)
        row1.addWidget(self.clear_orders_btn)

        self.clear_collect_btn = QPushButton("🗑 清空采集记录")
        self.clear_collect_btn.setMinimumHeight(34)
        self.clear_collect_btn.clicked.connect(self._clear_collect_records)
        row1.addWidget(self.clear_collect_btn)
        data_layout.addLayout(row1)

        # 第二行：监控快照 / 源复检 / 定时任务
        row2 = QHBoxLayout()
        self.clear_monitor_btn = QPushButton("🗑 清空运营快照")
        self.clear_monitor_btn.setMinimumHeight(34)
        self.clear_monitor_btn.clicked.connect(self._clear_monitor)
        row2.addWidget(self.clear_monitor_btn)

        self.clear_recheck_btn = QPushButton("🗑 清空源复检")
        self.clear_recheck_btn.setMinimumHeight(34)
        self.clear_recheck_btn.clicked.connect(self._clear_rechecks)
        row2.addWidget(self.clear_recheck_btn)

        self.clear_tasks_btn = QPushButton("🗑 清空定时任务")
        self.clear_tasks_btn.setMinimumHeight(34)
        self.clear_tasks_btn.clicked.connect(self._clear_tasks)
        row2.addWidget(self.clear_tasks_btn)
        data_layout.addLayout(row2)

        # 第三行：一键清空全部
        self.clear_all_btn = QPushButton("⚠️ 清空全部历史数据")
        self.clear_all_btn.setMinimumHeight(38)
        self.clear_all_btn.setStyleSheet(
            "QPushButton { background: #c62828; color: white; "
            "border-radius: 4px; padding: 6px 24px; font-size: 14px; font-weight: bold; }"
            "QPushButton:hover { background: #b71c1c; }"
        )
        self.clear_all_btn.clicked.connect(self._clear_all)
        data_layout.addWidget(self.clear_all_btn)

        refresh_counts_layout = QHBoxLayout()
        refresh_counts_layout.addStretch()
        self.refresh_counts_btn = QPushButton("🔄 刷新统计")
        self.refresh_counts_btn.setMinimumHeight(30)
        self.refresh_counts_btn.clicked.connect(self._refresh_data_counts)
        refresh_counts_layout.addWidget(self.refresh_counts_btn)
        data_layout.addLayout(refresh_counts_layout)

        layout.addWidget(data_group)
        self._refresh_data_counts()

        layout.addStretch()

    def _copy_machine_id(self):
        clipboard = QApplication.clipboard()
        clipboard.setText(self.machine_id)
        self.copy_hint.setVisible(True)
        self.machine_id_label.setStyleSheet(
            "color: #2e7d32; font-family: 'Courier New', monospace; "
            "font-size: 15px; padding: 6px 10px; "
            "background: #e8f5e9; border-radius: 4px;"
        )
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(1500, self._reset_machine_id_style)

    def _reset_machine_id_style(self):
        self.copy_hint.setVisible(False)
        self.machine_id_label.setStyleSheet(
            "color: #1565c0; font-family: 'Courier New', monospace; "
            "font-size: 15px; padding: 6px 10px; "
            "background: #e3f2fd; border-radius: 4px;"
        )

    def _update_license_status(self):
        validator = LicenseValidator()
        result = validator.verify()
        if result.get("valid"):
            expires = result.get("expires_at", "N/A")
            if isinstance(expires, str) and len(expires) > 10:
                expires = expires[:10]
            self.license_status.setText(f"✅ License有效 | 到期: {expires}")
            self.license_status.setStyleSheet("color: #2e7d32; font-size: 15px; font-weight: bold;")
        else:
            self.license_status.setText(f"❌ License无效 | 原因: {result.get('reason', '未激活')}")
            self.license_status.setStyleSheet("color: #c62828; font-size: 15px; font-weight: bold;")

    def _activate(self):
        key = self.license_input.text().strip()
        if not key:
            QMessageBox.warning(self, "提示", "请输入License Key")
            return

        validator = LicenseValidator()
        result = validator.activate(key)
        if result.get("success"):
            QMessageBox.information(self, "成功", "License激活成功！")
            self._update_license_status()
            self.main_window.update_status()
        else:
            QMessageBox.critical(self, "失败", f"激活失败: {result.get('message', '未知错误')}")

    def _test_connection(self):
        result = self.main_window.license_validator.test_server_connection()
        if result["ok"]:
            QMessageBox.information(self, "成功", result["msg"])
        else:
            QMessageBox.critical(self, "连接失败", result["msg"])

    def _fetch_models(self):
        """从API拉取可用模型列表"""
        url = self.api_url_input.text().strip()
        key = self.api_key_input.text().strip()

        if not url:
            QMessageBox.warning(self, "提示", "请先填写API地址")
            return
        if not key:
            QMessageBox.warning(self, "提示", "请先填写API Key")
            return

        self.fetch_models_btn.setEnabled(False)
        self.fetch_models_btn.setText("⏳ 拉取中...")
        self.ai_status_label.setText("正在拉取模型列表...")

        self._models_worker = FetchModelsWorker(url, key)
        self._models_worker.finished.connect(self._on_models_fetched)
        self._models_worker.error.connect(self._on_models_error)
        self._models_worker.start()

    def _on_models_fetched(self, models):
        self.fetch_models_btn.setEnabled(True)
        self.fetch_models_btn.setText("🔄 拉取模型列表")

        if not models:
            self.ai_status_label.setText("⚠️ 未获取到模型列表，请手动输入模型名")
            return

        current = self.model_combo.currentText()
        self.model_combo.clear()
        for m in models:
            self.model_combo.addItem(m)

        # 恢复之前选中的
        if current and current in models:
            self.model_combo.setCurrentText(current)
        else:
            self.model_combo.setCurrentIndex(0)

        self.ai_status_label.setText(f"✅ 获取到 {len(models)} 个模型，请选择")
        self.ai_status_label.setStyleSheet("color: #2e7d32; font-size: 13px;")

    def _on_models_error(self, msg):
        self.fetch_models_btn.setEnabled(True)
        self.fetch_models_btn.setText("🔄 拉取模型列表")
        self.ai_status_label.setText(f"❌ 拉取失败: {msg}")
        self.ai_status_label.setStyleSheet("color: #c62828; font-size: 13px;")

    def _save_api_config(self):
        url = self.api_url_input.text().strip()
        model = self.model_combo.currentText().strip()
        key = self.api_key_input.text().strip()

        if not all([url, model, key]):
            QMessageBox.warning(self, "提示", "请填写完整的API地址、模型和Key")
            return

        env_path = os.path.join(os.path.expanduser("~"), ".xf_env")
        with open(env_path, "w", encoding="utf-8") as f:
            f.write(f"AI_API_URL={url}\n")
            f.write(f"AI_API_MODEL={model}\n")
            f.write(f"AI_API_KEY={key}\n")

        # 更新运行时配置
        import config
        config.AI_API_URL = url
        config.AI_API_KEY = key
        config.AI_MODEL = model

        self._ai_config = {"AI_API_URL": url, "AI_API_MODEL": model, "AI_API_KEY": key}
        QMessageBox.information(self, "成功", "AI配置已保存并生效！")

    def _test_ai_connection(self):
        url = self.api_url_input.text().strip()
        key = self.api_key_input.text().strip()
        model = self.model_combo.currentText().strip()

        if not all([url, key, model]):
            QMessageBox.warning(self, "提示", "先填写API地址、Key和模型，再测试")
            return

        self.test_ai_btn.setEnabled(False)
        self.test_ai_btn.setText("⏳ 测试中...")
        self.ai_status_label.setText("正在测试AI连接...")

        try:
            base = url.rstrip("/")
            if "/v1/chat/completions" not in base and "/chat/completions" not in base:
                if base.endswith("/v1"):
                    test_url = f"{base}/chat/completions"
                else:
                    test_url = f"{base}/v1/chat/completions"
            else:
                test_url = base

            resp = requests.post(test_url,
                json={"model": model, "messages": [{"role": "user", "content": "hi"}], "max_tokens": 5},
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                timeout=(10, 60),  # (连接超时, 读取超时)
            )
            if resp.status_code == 200:
                data = resp.json()
                reply = data.get("choices", [{}])[0].get("message", {}).get("content", "")[:50]
                self.ai_status_label.setText(f"✅ 连接成功！模型回复: {reply}")
                self.ai_status_label.setStyleSheet("color: #2e7d32; font-size: 13px;")
                QMessageBox.information(self, "成功", f"AI连接正常！\n模型: {model}\n回复: {reply}")
            else:
                err = resp.text[:200]
                self.ai_status_label.setText(f"❌ 连接失败 ({resp.status_code})")
                self.ai_status_label.setStyleSheet("color: #c62828; font-size: 13px;")
                QMessageBox.warning(self, "失败", f"AI连接失败 ({resp.status_code}): {err}")
        except Exception as e:
            self.ai_status_label.setText(f"❌ 连接异常: {e}")
            self.ai_status_label.setStyleSheet("color: #c62828; font-size: 13px;")
            QMessageBox.critical(self, "错误", f"连接异常: {e}")
        finally:
            self.test_ai_btn.setEnabled(True)
            self.test_ai_btn.setText("🧪 测试AI连接")

    # ════════════ 数据管理 ════════════
    _COUNT_LABELS = {
        "products": "商品",
        "orders": "订单",
        "collect_records": "采集记录",
        "monitor_snapshots": "运营快照",
        "source_rechecks": "源复检",
        "scheduled_tasks": "定时任务",
    }

    def _refresh_data_counts(self):
        try:
            counts = db.data_counts()
        except Exception as e:
            self.data_counts_label.setText(f"统计读取失败: {e}")
            return
        parts = [f"{self._COUNT_LABELS.get(k, k)}: {counts.get(k, 0)}" for k in self._COUNT_LABELS]
        self.data_counts_label.setText("当前本地数据 —— " + " | ".join(parts))

    def _confirm(self, title: str, text: str) -> bool:
        ret = QMessageBox.question(
            self, title, text,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return ret == QMessageBox.StandardButton.Yes

    def _reload_main(self):
        """清理后刷新主窗口共享数据与各 Tab。"""
        try:
            self.main_window.reload_from_db()
        except Exception as e:
            print(f"刷新主窗口失败: {e}")

    def _clear_products(self):
        remove_local = self.del_local_checkbox.isChecked()
        extra = "，并删除本地图片目录" if remove_local else ""
        if not self._confirm("确认清空商品",
                             f"将删除全部商品记录{extra}。此操作不可恢复，确定继续？"):
            return
        try:
            res = db.clear_products(remove_local=remove_local)
        except Exception as e:
            QMessageBox.critical(self, "失败", f"清空商品失败: {e}")
            return
        self._reload_main()
        self._refresh_data_counts()
        msg = f"已删除 {res.get('products', 0)} 个商品"
        if remove_local:
            msg += f"，清理本地目录 {res.get('local_dirs', 0)} 个"
        QMessageBox.information(self, "完成", msg)

    def _clear_orders(self):
        if not self._confirm("确认清空订单", "将删除全部订单记录。此操作不可恢复，确定继续？"):
            return
        try:
            n = db.clear_orders()
        except Exception as e:
            QMessageBox.critical(self, "失败", f"清空订单失败: {e}")
            return
        self._reload_main()
        self._refresh_data_counts()
        QMessageBox.information(self, "完成", f"已删除 {n} 条订单")

    def _clear_collect_records(self):
        if not self._confirm("确认清空采集记录", "将删除全部采集历史记录。此操作不可恢复，确定继续？"):
            return
        try:
            n = db.clear_collect_records()
        except Exception as e:
            QMessageBox.critical(self, "失败", f"清空采集记录失败: {e}")
            return
        self._refresh_data_counts()
        QMessageBox.information(self, "完成", f"已删除 {n} 条采集记录")

    def _clear_monitor(self):
        if not self._confirm("确认清空运营快照", "将删除全部运营监控快照。此操作不可恢复，确定继续？"):
            return
        try:
            n = db.clear_all_monitor_snapshots()
        except Exception as e:
            QMessageBox.critical(self, "失败", f"清空运营快照失败: {e}")
            return
        self._refresh_data_counts()
        QMessageBox.information(self, "完成", f"已删除 {n} 条运营快照")

    def _clear_rechecks(self):
        if not self._confirm("确认清空源复检", "将删除全部源复检记录。此操作不可恢复，确定继续？"):
            return
        try:
            n = db.clear_all_rechecks()
        except Exception as e:
            QMessageBox.critical(self, "失败", f"清空源复检失败: {e}")
            return
        self._refresh_data_counts()
        QMessageBox.information(self, "完成", f"已删除 {n} 条源复检记录")

    def _clear_tasks(self):
        if not self._confirm("确认清空定时任务", "将删除全部定时任务。此操作不可恢复，确定继续？"):
            return
        try:
            n = db.clear_scheduled_tasks()
        except Exception as e:
            QMessageBox.critical(self, "失败", f"清空定时任务失败: {e}")
            return
        try:
            self.main_window.scheduler_tab.reload_tasks()
        except Exception:
            pass
        self._refresh_data_counts()
        QMessageBox.information(self, "完成", f"已删除 {n} 个定时任务")

    def _clear_all(self):
        remove_local = self.del_local_checkbox.isChecked()
        extra = "，并删除本地图片目录" if remove_local else ""
        if not self._confirm("确认清空全部",
                             f"将删除全部历史数据（商品/订单/采集记录/运营快照/源复检/定时任务）{extra}。\n"
                             "此操作不可恢复，确定继续？"):
            return
        summary = {}
        try:
            summary["products"] = db.clear_products(remove_local=remove_local)
            summary["orders"] = db.clear_orders()
            summary["collect"] = db.clear_collect_records()
            summary["monitor"] = db.clear_all_monitor_snapshots()
            summary["recheck"] = db.clear_all_rechecks()
            summary["tasks"] = db.clear_scheduled_tasks()
        except Exception as e:
            QMessageBox.critical(self, "失败", f"清空过程中出错: {e}")
            return
        self._reload_main()
        try:
            self.main_window.scheduler_tab.reload_tasks()
        except Exception:
            pass
        self._refresh_data_counts()
        p = summary.get("products", {})
        msg = (
            f"已清空全部历史数据：\n"
            f"商品 {p.get('products', 0)}、订单 {summary.get('orders', 0)}、"
            f"采集记录 {summary.get('collect', 0)}、运营快照 {summary.get('monitor', 0)}、"
            f"源复检 {summary.get('recheck', 0)}、定时任务 {summary.get('tasks', 0)}"
        )
        if remove_local:
            msg += f"\n清理本地目录 {p.get('local_dirs', 0)} 个"
        QMessageBox.information(self, "完成", msg)

    def refresh_items(self, items):
        pass
