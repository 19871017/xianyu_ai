"""商品编辑弹窗：在上架前直接改文案/价格/各 SKU 价格库存。

只编辑用户可改字段，提交时走 engine.product_package.apply_product_edits
统一合并并补齐为完整商品包，再由调用方落库 + 刷新。
"""
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel,
    QLineEdit, QTextEdit, QPushButton, QTableWidget, QTableWidgetItem,
    QHeaderView, QGroupBox, QDoubleSpinBox, QSpinBox, QMessageBox,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont

from engine.product_package import apply_product_edits


GLOBAL_FONT_FAMILY = "Microsoft YaHei, PingFang SC, sans-serif"


class ProductEditDialog(QDialog):
    """编辑单个商品的文案与 SKU 价格/库存。"""

    def __init__(self, item: dict, parent=None):
        super().__init__(parent)
        self.item = item or {}
        self._sku_widgets = []   # [(price_spin, stock_spin)]
        self.result_item = None
        self.setWindowTitle("编辑商品")
        self.setMinimumSize(640, 640)
        self.setFont(QFont(GLOBAL_FONT_FAMILY, 12))
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        # ── 文案区 ──
        form_group = QGroupBox("文案信息")
        form = QFormLayout(form_group)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self.title_edit = QLineEdit(self.item.get("title")
                                    or self.item.get("ai_title")
                                    or self.item.get("original_title") or "")
        form.addRow("标题:", self.title_edit)

        self.short_title_edit = QLineEdit(self.item.get("short_title") or "")
        form.addRow("短标题:", self.short_title_edit)

        self.category_edit = QLineEdit(self.item.get("category_keyword")
                                       or self.item.get("category") or "")
        form.addRow("品类词:", self.category_edit)

        self.brand_edit = QLineEdit(self.item.get("brand") or "")
        form.addRow("品牌:", self.brand_edit)

        tags = self.item.get("tags") or []
        if isinstance(tags, list):
            tags = ", ".join(str(t) for t in tags)
        self.tags_edit = QLineEdit(str(tags))
        self.tags_edit.setPlaceholderText("逗号分隔，如：复古, 全新, 包邮")
        form.addRow("标签:", self.tags_edit)

        self.desc_edit = QTextEdit(self.item.get("description") or "")
        self.desc_edit.setMinimumHeight(120)
        form.addRow("描述:", self.desc_edit)

        # 统一售价（留空表示不改，按各 SKU 价格上架）。
        self.price_spin = QDoubleSpinBox()
        self.price_spin.setRange(0, 9_999_999)
        self.price_spin.setDecimals(2)
        self.price_spin.setSpecialValueText("（不统一改价）")
        try:
            cur_price = float(self.item.get("new_price") or self.item.get("price") or 0)
        except Exception:
            cur_price = 0.0
        self.price_spin.setValue(cur_price)
        form.addRow("统一售价:", self.price_spin)

        layout.addWidget(form_group)

        # ── SKU 区 ──
        sku_list = self.item.get("sku_list") or []
        if sku_list:
            sku_group = QGroupBox(f"SKU 价格/库存（{len(sku_list)} 个规格）")
            sku_layout = QVBoxLayout(sku_group)
            self.sku_table = QTableWidget()
            self.sku_table.setColumnCount(4)
            self.sku_table.setHorizontalHeaderLabels(["规格1", "规格2", "价格", "库存"])
            self.sku_table.horizontalHeader().setSectionResizeMode(
                0, QHeaderView.ResizeMode.Stretch)
            self.sku_table.horizontalHeader().setSectionResizeMode(
                1, QHeaderView.ResizeMode.Stretch)
            self.sku_table.setRowCount(len(sku_list))
            for i, sku in enumerate(sku_list):
                s1 = QTableWidgetItem(str(sku.get("spec1", "")))
                s1.setFlags(s1.flags() & ~Qt.ItemFlag.ItemIsEditable)
                s2 = QTableWidgetItem(str(sku.get("spec2", "")))
                s2.setFlags(s2.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.sku_table.setItem(i, 0, s1)
                self.sku_table.setItem(i, 1, s2)

                p_spin = QDoubleSpinBox()
                p_spin.setRange(0, 9_999_999)
                p_spin.setDecimals(2)
                try:
                    p_spin.setValue(float(sku.get("price") or 0))
                except Exception:
                    p_spin.setValue(0.0)
                self.sku_table.setCellWidget(i, 2, p_spin)

                st_spin = QSpinBox()
                st_spin.setRange(0, 9_999_999)
                try:
                    st_spin.setValue(int(float(sku.get("stock") or 0)))
                except Exception:
                    st_spin.setValue(0)
                self.sku_table.setCellWidget(i, 3, st_spin)

                self._sku_widgets.append((p_spin, st_spin))
            sku_layout.addWidget(self.sku_table)
            layout.addWidget(sku_group)

        # ── 按钮 ──
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel_btn = QPushButton("取消")
        cancel_btn.setMinimumHeight(36)
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)
        save_btn = QPushButton("保存")
        save_btn.setMinimumHeight(36)
        save_btn.setStyleSheet(
            "QPushButton { background:#2e7d32; color:white; border-radius:4px; "
            "padding:6px 24px; font-weight:bold; }"
            "QPushButton:hover { background:#1b5e20; }"
        )
        save_btn.clicked.connect(self._on_save)
        btn_row.addWidget(save_btn)
        layout.addLayout(btn_row)

    def _on_save(self):
        edits = {
            "title": self.title_edit.text().strip(),
            "short_title": self.short_title_edit.text().strip(),
            "category_keyword": self.category_edit.text().strip(),
            "brand": self.brand_edit.text().strip(),
            "tags": self.tags_edit.text().strip(),
            "description": self.desc_edit.toPlainText().strip(),
        }
        if self.price_spin.value() > 0:
            edits["new_price"] = self.price_spin.value()

        sku_edits = []
        for idx, (p_spin, st_spin) in enumerate(self._sku_widgets):
            sku_edits.append({
                "index": idx,
                "price": p_spin.value(),
                "stock": st_spin.value(),
            })
        if sku_edits:
            edits["sku_edits"] = sku_edits

        try:
            self.result_item = apply_product_edits(self.item, edits)
        except Exception as e:
            QMessageBox.critical(self, "保存失败", f"合并编辑失败：\n{e}")
            return
        self.accept()
