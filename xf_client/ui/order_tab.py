"""订单代采 Tab：闲鱼卖出订单 → 回溯源商品 → 半自动一键代采。

工作流（安全第一）：
  1) 抓单：从闲鱼已售页只读抓取卖出订单（不下单/不支付）。
  2) 匹配：订单 → 本地商品（闲鱼商品 id / 标题）→ 买家规格 → 本地 SKU → 源 skuId / 源链接。
  3) 代采：对选中订单生成代采计划并执行——打开 1688 源商品，选规格、填数量、
     填收货地址，**停在下单确认页 / 进货车**，由人工核对后手动支付。

护栏：代采执行器从不点击「提交订单/立即支付」等不可逆按钮（见 engine.reorder_agent）。
"""
import json
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QTableWidget, QTableWidgetItem, QHeaderView,
    QMessageBox, QProgressBar, QTextEdit, QGroupBox, QCheckBox,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QFont, QColor, QBrush

from engine.order_tracker import (
    GofishproOrderTracker,
    match_order_to_product,
    match_sku_for_order,
    build_reorder_plan,
)
from engine.reorder_agent import ReorderAgent, validate_reorder_plan
from database.db_manager import db
from utils.notifier import detect_new_orders, alert_new_orders, is_voice_enabled, set_voice_enabled


GLOBAL_FONT_FAMILY = "Microsoft YaHei, PingFang SC, sans-serif"


class FetchOrdersWorker(QThread):
    """抓取闲管家(goofish.pro)卖出订单并就地匹配本地商品/源商品（只读，不下单）。"""
    progress_msg = pyqtSignal(str)
    finished = pyqtSignal(list)   # list of {order, product, plan}

    def __init__(self, products):
        super().__init__()
        self.products = products or []

    def run(self):
        def log(msg):
            self.progress_msg.emit(msg)

        tracker = GofishproOrderTracker(on_log=log)
        opened = False
        rows = []
        try:
            log("正在打开闲管家并校验登录态…")
            opened = tracker.open()
            if not opened:
                log("闲管家登录失败")
                self.finished.emit([])
                return

            orders = tracker.fetch_sold_orders()
            log(f"抓到 {len(orders)} 条已售订单，开始匹配源商品…")
            for od in orders:
                product = match_order_to_product(od, self.products)
                plan = build_reorder_plan(od, product) if product else None
                # 落库：记录订单 + 匹配状态（便于追溯）。
                try:
                    self._persist(od, product, plan)
                except Exception as e:
                    log(f"  订单落库失败：{e}")
                rows.append({"order": od, "product": product, "plan": plan})
        except Exception as e:
            log(f"抓单异常：{e}")
        finally:
            if opened:
                tracker.close()
        self.finished.emit(rows)

    def _persist(self, order, product, plan):
        match_status = "unmatched"
        source_sku_id = ""
        source_url = ""
        source_platform = ""
        if product and plan:
            source_url = plan.get("source_url", "")
            source_sku_id = plan.get("source_sku_id", "")
            source_platform = plan.get("source_platform", "")
            if plan.get("ok") and plan.get("spec_score", 0) >= 0.99:
                match_status = "matched"
            else:
                match_status = "need_review"
        db.save_order({
            "product_id": product.get("db_id") if product else None,
            "platform_order_id": order.get("platform_order_id", ""),
            "platform": "xianyu",
            "buyer_name": order.get("buyer_name", ""),
            "buyer_phone": order.get("buyer_phone", ""),
            "buyer_address": order.get("buyer_address", ""),
            "order_status": order.get("order_status", "pending"),
            "order_amount": order.get("order_amount", ""),
            "buyer_spec": order.get("buyer_spec", ""),
            "quantity": order.get("quantity", 1),
            "source_platform": source_platform,
            "source_url": source_url,
            "source_item_id": order.get("xianyu_item_id", ""),
            "source_sku_id": source_sku_id,
            "match_status": match_status,
        })


class AddressFetchWorker(QThread):
    """代采发货前进闲管家订单详情页补抓买家收货地址（列表页不含地址）。"""
    progress_msg = pyqtSignal(str)
    finished = pyqtSignal(dict)   # {name, phone, address}

    def __init__(self, order):
        super().__init__()
        self.order = order or {}

    def run(self):
        def log(msg):
            self.progress_msg.emit(msg)

        tracker = GofishproOrderTracker(on_log=log)
        opened = False
        info = {"name": "", "phone": "", "address": ""}
        try:
            log("正在打开闲管家订单详情，补抓收货地址…")
            opened = tracker.open()
            if not opened:
                log("闲管家登录失败，无法补抓收货地址")
                self.finished.emit(info)
                return
            info = tracker.fetch_order_address(self.order)
        except Exception as e:
            log(f"补抓收货地址异常：{e}")
        finally:
            if opened:
                tracker.close()
        self.finished.emit(info)


class ReorderWorker(QThread):
    """对单条订单执行半自动代采（停在确认页，绝不支付）。"""
    progress_msg = pyqtSignal(str)
    finished = pyqtSignal(dict)

    def __init__(self, plan):
        super().__init__()
        self.plan = plan

    def run(self):
        def log(msg):
            self.progress_msg.emit(msg)

        agent = ReorderAgent(on_log=log)
        opened = False
        out = {"ok": False, "error": ""}
        try:
            log("正在打开 1688 并校验登录态…")
            opened = agent.open()
            if not opened:
                out["error"] = "1688 登录失败"
                self.finished.emit(out)
                return
            out = agent.run_reorder(self.plan)
        except Exception as e:
            out = {"ok": False, "error": str(e)}
        finally:
            # 代采停在确认页，浏览器保持打开供人工核对支付，不关闭。
            pass
        self.finished.emit(out)


class OrderTab(QWidget):
    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self.rows = []           # [{order, product, plan}]
        self.worker = None
        self.reorder_worker = None
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.setContentsMargins(8, 8, 8, 8)

        tip = QLabel(
            "卖出订单回溯代采：从闲管家抓取卖出订单 → 匹配采集来源 → 半自动到 1688 下单。\n"
            "⚠️ 代采只会停在「下单确认页 / 进货车」，绝不自动支付，请人工核对后手动付款。"
        )
        tip.setWordWrap(True)
        tip.setFont(QFont(GLOBAL_FONT_FAMILY, 12))
        tip.setStyleSheet("color:#b26a00; background:#fff8e1; padding:8px; border-radius:4px;")
        layout.addWidget(tip)

        btn_row = QHBoxLayout()
        self.fetch_btn = QPushButton("🔄 抓取闲管家卖出订单")
        self.fetch_btn.setMinimumHeight(40)
        self.fetch_btn.setStyleSheet(
            "QPushButton { background:#1565c0; color:white; border-radius:4px; "
            "padding:6px 20px; font-size:14px; font-weight:bold; }"
            "QPushButton:hover { background:#0d47a1; }"
            "QPushButton:disabled { background:#bbb; }"
        )
        self.fetch_btn.clicked.connect(self._fetch_orders)
        btn_row.addWidget(self.fetch_btn)

        self.reorder_btn = QPushButton("🛒 对选中订单代采（停在确认页）")
        self.reorder_btn.setMinimumHeight(40)
        self.reorder_btn.setStyleSheet(
            "QPushButton { background:#2e7d32; color:white; border-radius:4px; "
            "padding:6px 20px; font-size:14px; font-weight:bold; }"
            "QPushButton:hover { background:#1b5e20; }"
            "QPushButton:disabled { background:#bbb; }"
        )
        self.reorder_btn.clicked.connect(self._reorder_selected)
        self.reorder_btn.setEnabled(False)
        btn_row.addWidget(self.reorder_btn)
        btn_row.addStretch()
        self.voice_cb = QCheckBox("🔊 新订单语音提醒")
        self.voice_cb.setChecked(is_voice_enabled())
        self.voice_cb.setFont(QFont(GLOBAL_FONT_FAMILY, 12))
        self.voice_cb.setToolTip("抓单后若发现新订单，弹系统通知并语音播报")
        self.voice_cb.toggled.connect(self._on_voice_toggled)
        btn_row.addWidget(self.voice_cb)
        layout.addLayout(btn_row)

        self.table = QTableWidget()
        self.table.setColumnCount(8)
        self.table.setHorizontalHeaderLabels(
            ["选择", "买家", "标题", "买家规格", "金额", "源平台", "源skuId", "匹配状态"]
        )
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.cellClicked.connect(self._on_row_clicked)
        layout.addWidget(self.table)

        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)

        self.log_area = QTextEdit()
        self.log_area.setReadOnly(True)
        self.log_area.setMaximumHeight(140)
        self.log_area.setFont(QFont(GLOBAL_FONT_FAMILY, 12))
        layout.addWidget(self.log_area)

        self._selected_row = -1

    def _on_voice_toggled(self, checked):
        set_voice_enabled(checked)

    # ── 数据接口 ──
    def refresh_items(self, items):
        # 商品列表由主窗口维护；订单 tab 抓单时实时从 DB 取最新商品。
        pass

    # ── 抓单 ──
    def _fetch_orders(self):
        products = []
        try:
            products = db.get_all_products()
        except Exception:
            products = self.main_window.collected_items or []
        if not products:
            QMessageBox.warning(self, "提示", "本地没有采集的商品，无法回溯源商品。请先采集并上架。")
            return

        self.fetch_btn.setEnabled(False)
        self.reorder_btn.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0)
        self.log_area.clear()

        self.worker = FetchOrdersWorker(products)
        self.worker.progress_msg.connect(self._append_log)
        self.worker.finished.connect(self._on_fetch_done)
        self.worker.start()

    def _on_fetch_done(self, rows: list):
        self.rows = rows or []
        self.progress_bar.setVisible(False)
        self.progress_bar.setRange(0, 100)
        self.fetch_btn.setEnabled(True)
        self._fill_table()
        self._append_log(f"\n✅ 完成：{len(self.rows)} 条订单已匹配并落库。")
        if not self.rows:
            self._append_log("未抓到订单（可能闲鱼已售页改版或当前无卖出）。")
        else:
            try:
                orders = [r.get("order") for r in self.rows if isinstance(r, dict)]
                new_count = detect_new_orders(orders)
                if new_count > 0:
                    self._append_log(f"🔔 检测到 {new_count} 个新订单，已语音提醒。")
                    alert_new_orders(new_count)
            except Exception as e:
                self._append_log(f"新订单提醒异常：{e}")

    def _fill_table(self):
        self.table.setRowCount(len(self.rows))
        for i, row in enumerate(self.rows):
            od = row["order"]
            plan = row.get("plan")
            cb = QTableWidgetItem()
            cb.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            cb.setCheckState(Qt.CheckState.Unchecked)
            self.table.setItem(i, 0, cb)
            self.table.setItem(i, 1, QTableWidgetItem(od.get("buyer_name", "")))
            self.table.setItem(i, 2, QTableWidgetItem(od.get("title", "")))
            self.table.setItem(i, 3, QTableWidgetItem(od.get("buyer_spec", "")))
            self.table.setItem(i, 4, QTableWidgetItem(str(od.get("order_amount", ""))))
            src_plat = plan.get("source_platform", "") if plan else ""
            src_sku = plan.get("source_sku_id", "") if plan else ""
            self.table.setItem(i, 5, QTableWidgetItem(src_plat))
            self.table.setItem(i, 6, QTableWidgetItem(src_sku))

            status, color = self._status_label(row)
            st_item = QTableWidgetItem(status)
            st_item.setForeground(QBrush(QColor(color)))
            self.table.setItem(i, 7, st_item)
        self.reorder_btn.setEnabled(len(self.rows) > 0)

    def _status_label(self, row):
        product = row.get("product")
        plan = row.get("plan")
        if not product:
            return "未匹配商品", "#c62828"
        if not plan or not plan.get("ok"):
            return "缺源链接", "#c62828"
        score = plan.get("spec_score", 0)
        if score >= 0.99:
            return "可代采", "#2e7d32"
        return "规格待核对", "#b26a00"

    def _on_row_clicked(self, row, col):
        self._selected_row = row

    def _checked_rows(self):
        out = []
        for i in range(self.table.rowCount()):
            cb = self.table.item(i, 0)
            if cb and cb.checkState() == Qt.CheckState.Checked:
                out.append(i)
        return out

    # ── 代采 ──
    def _reorder_selected(self):
        checked = self._checked_rows()
        if not checked:
            QMessageBox.warning(self, "提示", "请勾选要代采的订单（建议一次一单，便于核对）。")
            return
        if len(checked) > 1:
            QMessageBox.information(
                self, "提示",
                "为安全起见，代采一次只处理一条订单。已选中多条，将只处理第一条。"
            )
        idx = checked[0]
        row = self.rows[idx]
        plan = row.get("plan")
        if not plan:
            QMessageBox.warning(self, "无法代采", "该订单未匹配到源商品，无法代采。")
            return

        od = row["order"]
        # 闲管家订单列表页不含收货地址；代采校验又把地址/收货人当硬门槛。
        # 若计划里地址为空且能定位订单详情，先进详情页补抓一次再校验。
        ship = plan.get("ship_to") or {}
        need_addr = not (ship.get("address") or "").strip()
        can_fetch = bool((od.get("detail_url") or "").strip() or (od.get("platform_order_id") or "").strip())
        if need_addr and can_fetch:
            self._pending_reorder = {"idx": idx, "plan": plan, "row": row}
            self.reorder_btn.setEnabled(False)
            self.fetch_btn.setEnabled(False)
            self.progress_bar.setVisible(True)
            self.progress_bar.setRange(0, 0)
            self._append_log("收货地址缺失，先进闲管家订单详情补抓…")
            self.addr_worker = AddressFetchWorker(od)
            self.addr_worker.progress_msg.connect(self._append_log)
            self.addr_worker.finished.connect(self._on_address_fetched)
            self.addr_worker.start()
            return

        self._confirm_and_launch_reorder(idx, plan, row)

    def _on_address_fetched(self, info: dict):
        self.progress_bar.setVisible(False)
        self.progress_bar.setRange(0, 100)
        self.reorder_btn.setEnabled(True)
        self.fetch_btn.setEnabled(True)
        pending = getattr(self, "_pending_reorder", None)
        self._pending_reorder = None
        if not pending:
            return
        idx, plan, row = pending["idx"], pending["plan"], pending["row"]
        info = info or {}
        addr = (info.get("address") or "").strip()
        if addr:
            ship = plan.setdefault("ship_to", {})
            ship["address"] = addr
            if info.get("name"):
                ship["name"] = info["name"]
            if info.get("phone"):
                ship["phone"] = info["phone"]
            # 同步回订单与本地库，便于追溯。
            od = row["order"]
            od["buyer_address"] = addr
            if info.get("name"):
                od["buyer_name"] = od.get("buyer_name") or info["name"]
            if info.get("phone"):
                od["buyer_phone"] = info["phone"]
            try:
                db.save_order({
                    "platform_order_id": od.get("platform_order_id", ""),
                    "platform": "xianyu",
                    "buyer_name": od.get("buyer_name", ""),
                    "buyer_phone": od.get("buyer_phone", ""),
                    "buyer_address": addr,
                })
            except Exception as e:
                self._append_log(f"收货地址落库失败：{e}")
        else:
            self._append_log("未能补抓到收货地址，请到浏览器人工核对后再代采。")
        self._confirm_and_launch_reorder(idx, plan, row)

    def _confirm_and_launch_reorder(self, idx, plan, row):
        check = validate_reorder_plan(plan)
        if not check["ok"]:
            QMessageBox.warning(
                self, "代采前校验未通过",
                "原因：\n- " + "\n- ".join(check["reasons"]) +
                "\n\n请在浏览器中人工处理该订单。"
            )
            return

        od = row["order"]
        msg = (
            f"即将到 1688 半自动代采：\n\n"
            f"买家：{od.get('buyer_name','')}\n"
            f"规格：{od.get('buyer_spec','') or '（单规格）'}\n"
            f"数量：{check['quantity']}\n"
            f"源链接：{check['offer_url']}\n\n"
            "软件会自动选规格、填数量、填收货地址，"
            "并停在「下单确认页 / 进货车」。\n"
            "⚠️ 不会自动支付，请你核对后手动付款。\n\n确认继续？"
        )
        if QMessageBox.question(self, "确认代采", msg) != QMessageBox.StandardButton.Yes:
            return

        self.reorder_btn.setEnabled(False)
        self.fetch_btn.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0)

        self.reorder_worker = ReorderWorker(plan)
        self.reorder_worker.progress_msg.connect(self._append_log)
        self.reorder_worker.finished.connect(self._on_reorder_done)
        self.reorder_worker.start()

    def _on_reorder_done(self, out: dict):
        self.progress_bar.setVisible(False)
        self.progress_bar.setRange(0, 100)
        self.reorder_btn.setEnabled(True)
        self.fetch_btn.setEnabled(True)
        if out.get("ok"):
            stage = out.get("stage", "")
            specs = "、".join(out.get("selected_specs", []))
            self._append_log(
                f"\n✅ 代采已停在确认页（stage={stage}）。已选规格：{specs or '（单规格）'}。"
                "\n请到 1688 浏览器窗口核对规格/数量/地址后手动支付。"
            )
            QMessageBox.information(
                self, "代采已就绪",
                "已在 1688 完成选规格/填数量，并停在下单确认页。\n"
                "请到浏览器核对后手动支付（软件不会自动付款）。"
            )
        else:
            self._append_log(f"\n❌ 代采未完成：{out.get('error','')}")
            QMessageBox.warning(self, "代采未完成", out.get("error", "未知错误"))

    def _append_log(self, msg: str):
        self.log_area.append(msg)
        sb = self.log_area.verticalScrollBar()
        sb.setValue(sb.maximum())
