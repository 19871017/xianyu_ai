import json
import sqlite3
import os
from datetime import datetime
from typing import List, Dict, Optional

try:
    from engine.product_package import PACKAGE_ATTR_KEY
except Exception:
    PACKAGE_ATTR_KEY = '_full_product_package'


import shutil


def _iter_local_image_dirs(product: Dict) -> set:
    """从商品记录中收集“本软件托管”的本地图片目录（用于级联删除）。

    安全约束：只返回路径中包含 ``images`` 这一层的“按商品归档”目录
    （形如 ``.../电商数据/images/{item_id}/``）。用户自行导入的素材目录
    （如 ``~/Downloads/xxx``）不含 images 归档层，不会被纳入，避免误删。
    """
    dirs = set()

    def _consider(path: str):
        if not path or not isinstance(path, str):
            return
        norm = os.path.normpath(path)
        parts = norm.split(os.sep)
        if "images" not in parts:
            return
        idx = parts.index("images")
        # images 之后必须还有一层“商品目录”才纳入（避免删除整个 images 根）
        if idx + 1 >= len(parts):
            return
        item_dir = os.sep.join(parts[: idx + 2])
        dirs.add(item_dir)

    for img in (product.get("local_images") or []):
        if isinstance(img, str):
            _consider(os.path.dirname(img))
    for key in ("image_dir", "source_dir"):
        val = product.get(key)
        if isinstance(val, str):
            _consider(val)
    # 商品包内嵌字段
    attrs = product.get("attributes")
    if isinstance(attrs, dict):
        pkg = attrs.get(PACKAGE_ATTR_KEY)
        if isinstance(pkg, dict):
            for key in ("image_dir", "source_dir"):
                val = pkg.get(key)
                if isinstance(val, str):
                    _consider(val)
    return dirs


def _safe_remove_dirs(dirs) -> int:
    """删除给定目录集合，返回成功删除的数量。仅删除存在的目录，异常不抛出。"""
    removed = 0
    for d in dirs:
        try:
            if d and os.path.isdir(d):
                shutil.rmtree(d)
                removed += 1
        except Exception:
            pass
    return removed


DB_PATH = os.path.join(os.path.expanduser("~"), ".xf_data", "data.db")


class DatabaseManager:
    """SQLite数据库管理器 - 商品/订单/监控快照/Cookie 持久化"""

    def __init__(self):
        self._ensure_db_dir()
        self._init_tables()

    def _ensure_db_dir(self):
        db_dir = os.path.dirname(DB_PATH)
        if not os.path.exists(db_dir):
            os.makedirs(db_dir, exist_ok=True)

    def _get_conn(self):
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_tables(self):
        with self._get_conn() as conn:
            # ── 商品表 ──
            conn.execute("""
                CREATE TABLE IF NOT EXISTS products (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    item_id TEXT UNIQUE,
                    platform TEXT DEFAULT 'xianyu',
                    original_title TEXT,
                    ai_title TEXT,
                    description TEXT,
                    original_price TEXT,
                    new_price TEXT,
                    price_markup_pct REAL DEFAULT 0,
                    wants TEXT DEFAULT '0',
                    views TEXT DEFAULT '0',
                    seller TEXT,
                    seller_credit TEXT,
                    source_url TEXT,
                    source_item_id TEXT,
                    local_images TEXT,
                    attributes TEXT,
                    tags TEXT,
                    status TEXT DEFAULT 'collected',
                    xianyu_item_id TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # ── 订单表 ──
            conn.execute("""
                CREATE TABLE IF NOT EXISTS orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    product_id INTEGER,
                    platform_order_id TEXT,
                    platform TEXT DEFAULT 'xianyu',
                    buyer_name TEXT,
                    buyer_phone TEXT,
                    buyer_address TEXT,
                    order_status TEXT DEFAULT 'pending',
                    order_amount TEXT,
                    buyer_spec TEXT,
                    quantity INTEGER DEFAULT 1,
                    source_platform TEXT,
                    source_url TEXT,
                    source_item_id TEXT,
                    source_sku_id TEXT,
                    match_status TEXT DEFAULT 'unmatched',
                    upstream_order_id TEXT,
                    upstream_status TEXT,
                    notes TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (product_id) REFERENCES products(id)
                )
            """)

            # ── 采集记录表 ──
            conn.execute("""
                CREATE TABLE IF NOT EXISTS collect_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    platform TEXT,
                    keyword TEXT,
                    source_url TEXT,
                    item_count INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # ── Cookie 存储表 ──
            conn.execute("""
                CREATE TABLE IF NOT EXISTS cookies (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    platform TEXT UNIQUE,
                    cookie_data TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # ── 【新增】运营监控快照表 ──
            conn.execute("""
                CREATE TABLE IF NOT EXISTS monitor_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    platform TEXT NOT NULL,
                    snapshot_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    is_logged_in INTEGER DEFAULT 0,
                    active_listings INTEGER DEFAULT 0,
                    total_views INTEGER DEFAULT 0,
                    total_wants INTEGER DEFAULT 0,
                    total_inquiries INTEGER DEFAULT 0,
                    pending_orders INTEGER DEFAULT 0,
                    completed_orders_today INTEGER DEFAULT 0,
                    completed_orders_30d INTEGER DEFAULT 0,
                    revenue_today REAL DEFAULT 0.0,
                    revenue_30d REAL DEFAULT 0.0,
                    alerts TEXT DEFAULT '[]',
                    raw_data TEXT DEFAULT '{}',
                    error TEXT DEFAULT ''
                )
            """)
            # ── 源商品复检结果表 ──
            conn.execute("""
                CREATE TABLE IF NOT EXISTS source_rechecks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    product_id INTEGER,
                    title TEXT,
                    platform TEXT,
                    source_url TEXT,
                    listing_price REAL DEFAULT 0,
                    old_min_price REAL DEFAULT 0,
                    new_min_price REAL DEFAULT 0,
                    level TEXT DEFAULT 'none',
                    summary TEXT DEFAULT '',
                    alerts TEXT DEFAULT '[]',
                    checked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # ── 定时任务表 ──
            conn.execute("""
                CREATE TABLE IF NOT EXISTS scheduled_tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT,
                    task_type TEXT,
                    trigger TEXT DEFAULT 'interval',
                    interval_minutes INTEGER DEFAULT 60,
                    daily_time TEXT DEFAULT '09:00',
                    params TEXT DEFAULT '{}',
                    enabled INTEGER DEFAULT 1,
                    last_run TIMESTAMP,
                    last_result TEXT DEFAULT '',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # 给老数据库做 migration（字段不存在时 ALTER）
            self._migrate_monitor_table(conn)
            self._migrate_trace_columns(conn)

            conn.commit()

    def _migrate_monitor_table(self, conn):
        """老数据库兼容：monitor_snapshots 若缺少新字段则 ALTER 补充"""
        try:
            existing = {
                row[1]
                for row in conn.execute("PRAGMA table_info(monitor_snapshots)")
            }
            new_cols = {
                "total_inquiries": "INTEGER DEFAULT 0",
                "completed_orders_30d": "INTEGER DEFAULT 0",
                "revenue_30d": "REAL DEFAULT 0.0",
                "raw_data": "TEXT DEFAULT '{}'",
                "error": "TEXT DEFAULT ''",
            }
            for col, definition in new_cols.items():
                if col not in existing:
                    conn.execute(
                        f"ALTER TABLE monitor_snapshots ADD COLUMN {col} {definition}"
                    )
        except Exception:
            pass  # 不阻塞启动

    def _migrate_trace_columns(self, conn):
        """老数据库兼容：products / orders 补齐来源追溯与规格字段。"""
        try:
            prod_cols = {
                row[1] for row in conn.execute("PRAGMA table_info(products)")
            }
            if "xianyu_item_id" not in prod_cols:
                conn.execute("ALTER TABLE products ADD COLUMN xianyu_item_id TEXT")

            order_cols = {
                row[1] for row in conn.execute("PRAGMA table_info(orders)")
            }
            new_order_cols = {
                "buyer_spec": "TEXT",
                "quantity": "INTEGER DEFAULT 1",
                "source_platform": "TEXT",
                "source_url": "TEXT",
                "source_item_id": "TEXT",
                "source_sku_id": "TEXT",
                "match_status": "TEXT DEFAULT 'unmatched'",
            }
            for col, definition in new_order_cols.items():
                if col not in order_cols:
                    conn.execute(
                        f"ALTER TABLE orders ADD COLUMN {col} {definition}"
                    )
        except Exception:
            pass  # 不阻塞启动

    # ═══════════════════ 商品操作 ═══════════════════

    def save_product(self, item: Dict) -> int:
        # 用顶层最新字段(如调价后的 sku_list)重建 attributes 内的
        # _full_product_package，避免读取时旧 package 覆盖回退（根治调价/
        # 改文案保存后被旧值覆盖）。仅在带 sku_list 或既有 package 时触发，
        # 不影响仅状态字段的保存；规整失败不阻断保存。
        try:
            from engine.product_package import ensure_full_product_package
            _attrs = item.get("attributes")
            if item.get("sku_list") is not None or (
                isinstance(_attrs, dict) and PACKAGE_ATTR_KEY in _attrs
            ):
                item = ensure_full_product_package(dict(item))
        except Exception:
            pass
        with self._get_conn() as conn:
            existing = conn.execute(
                "SELECT id FROM products WHERE item_id = ?",
                (item.get("item_id"),)
            ).fetchone()

            data = {
                "item_id": item.get("item_id", ""),
                "platform": item.get("platform", "xianyu"),
                "original_title": item.get("original_title", ""),
                "ai_title": item.get("ai_title", ""),
                "description": item.get("description", ""),
                "original_price": str(item.get("original_price", "")),
                "new_price": str(item.get("new_price", "")),
                "price_markup_pct": item.get("price_markup_pct", 0),
                "wants": str(item.get("wants", "0")),
                "views": str(item.get("views", "0")),
                "seller": item.get("seller", ""),
                "seller_credit": item.get("seller_credit", ""),
                "source_url": item.get("source_url", item.get("link", "")),
                "source_item_id": item.get("source_item_id", ""),
                "xianyu_item_id": item.get("xianyu_item_id", ""),
                "local_images": json.dumps(item.get("local_images", []), ensure_ascii=False),
                "attributes": json.dumps(item.get("attributes", {}), ensure_ascii=False),
                "tags": json.dumps(item.get("tags", []), ensure_ascii=False),
                "status": item.get("status", "collected"),
                "updated_at": datetime.now().isoformat(),
            }

            if existing:
                data["id"] = existing["id"]
                conn.execute("""
                    UPDATE products SET
                        platform=:platform,
                        original_title=:original_title,
                        ai_title=:ai_title,
                        description=:description,
                        source_url=:source_url,
                        source_item_id=:source_item_id,
                        xianyu_item_id=:xianyu_item_id,
                        new_price=:new_price,
                        price_markup_pct=:price_markup_pct,
                        local_images=:local_images,
                        attributes=:attributes,
                        tags=:tags,
                        status=:status,
                        updated_at=:updated_at
                    WHERE id=:id
                """, data)
                return existing["id"]
            else:
                cursor = conn.execute("""
                    INSERT INTO products (
                        item_id, platform, original_title, ai_title, description,
                        original_price, new_price, price_markup_pct, wants, views,
                        seller, seller_credit, source_url, source_item_id, xianyu_item_id,
                        local_images, attributes, tags, status, updated_at
                    ) VALUES (
                        :item_id, :platform, :original_title, :ai_title, :description,
                        :original_price, :new_price, :price_markup_pct, :wants, :views,
                        :seller, :seller_credit, :source_url, :source_item_id, :xianyu_item_id,
                        :local_images, :attributes, :tags, :status, :updated_at
                    )
                """, data)
                return cursor.lastrowid

    def get_all_products(self, status: str = None) -> List[Dict]:
        with self._get_conn() as conn:
            if status:
                rows = conn.execute(
                    "SELECT * FROM products WHERE status = ? ORDER BY created_at DESC",
                    (status,)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM products ORDER BY created_at DESC"
                ).fetchall()
            return [self._row_to_product(row) for row in rows]

    def get_product_by_id(self, product_id: int) -> Optional[Dict]:
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM products WHERE id = ?", (product_id,)
            ).fetchone()
            return self._row_to_product(row) if row else None

    def get_product_by_item_id(self, item_id: str) -> Optional[Dict]:
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM products WHERE item_id = ?", (item_id,)
            ).fetchone()
            return self._row_to_product(row) if row else None

    def update_product_status(self, product_id: int, status: str):
        with self._get_conn() as conn:
            conn.execute(
                "UPDATE products SET status = ?, updated_at = ? WHERE id = ?",
                (status, datetime.now().isoformat(), product_id)
            )

    def set_xianyu_item_id(self, product_id: int, xianyu_item_id: str):
        """发布到闲鱼成功后回写闲鱼商品 id（用于卖出订单回溯到本地商品）。"""
        if not xianyu_item_id:
            return
        with self._get_conn() as conn:
            conn.execute(
                "UPDATE products SET xianyu_item_id = ?, updated_at = ? WHERE id = ?",
                (str(xianyu_item_id), datetime.now().isoformat(), product_id),
            )

    def delete_product(self, product_id: int, remove_local: bool = False) -> int:
        """删除商品记录。remove_local=True 时同时删除该商品托管的本地图片目录。

        返回删除的本地目录数量（remove_local=False 时恒为 0）。
        """
        local_dirs = set()
        if remove_local:
            product = self.get_product_by_id(product_id)
            if product:
                local_dirs = _iter_local_image_dirs(product)
        with self._get_conn() as conn:
            conn.execute("DELETE FROM products WHERE id = ?", (product_id,))
        return _safe_remove_dirs(local_dirs) if remove_local else 0

    def _row_to_product(self, row: sqlite3.Row) -> Dict:
        attrs = json.loads(row["attributes"] or "{}")
        product = {
            "db_id": row["id"],
            "item_id": row["item_id"],
            "platform": row["platform"],
            "original_title": row["original_title"],
            "title": row["ai_title"] or row["original_title"],
            "ai_title": row["ai_title"] or row["original_title"],
            "description": row["description"],
            "original_price": row["original_price"],
            "new_price": row["new_price"] or row["original_price"],
            "price_markup_pct": row["price_markup_pct"],
            "wants": row["wants"],
            "views": row["views"],
            "seller": row["seller"],
            "seller_credit": row["seller_credit"],
            "source_url": row["source_url"],
            "source_item_id": row["source_item_id"],
            "xianyu_item_id": ((row["xianyu_item_id"] if "xianyu_item_id" in row.keys() else "") or ""),
            "local_images": json.loads(row["local_images"] or "[]"),
            "attributes": attrs,
            "tags": json.loads(row["tags"] or "[]"),
            "status": row["status"],
            "created_at": row["created_at"],
        }
        package = attrs.get(PACKAGE_ATTR_KEY) if isinstance(attrs, dict) else None
        if isinstance(package, dict):
            product.update(package)
            product["attributes"] = attrs
        return product

    # ═══════════════════ 订单操作 ═══════════════════

    def save_order(self, order: Dict) -> int:
        """保存订单。platform_order_id 已存在则更新，否则插入（幂等抓单）。"""
        with self._get_conn() as conn:
            poid = order.get("platform_order_id") or ""
            existing = None
            if poid:
                existing = conn.execute(
                    "SELECT id FROM orders WHERE platform_order_id = ?", (poid,)
                ).fetchone()
            data = (
                order.get("product_id"),
                poid,
                order.get("platform", "xianyu"),
                order.get("buyer_name", ""),
                order.get("buyer_phone", ""),
                order.get("buyer_address", ""),
                order.get("order_status", "pending"),
                order.get("order_amount", ""),
                order.get("buyer_spec", ""),
                int(order.get("quantity") or 1),
                order.get("source_platform", ""),
                order.get("source_url", ""),
                order.get("source_item_id", ""),
                order.get("source_sku_id", ""),
                order.get("match_status", "unmatched"),
                order.get("upstream_order_id", ""),
                order.get("upstream_status", ""),
                order.get("notes", ""),
            )
            if existing:
                conn.execute("""
                    UPDATE orders SET
                        product_id=?, platform=?, buyer_name=?, buyer_phone=?,
                        buyer_address=?, order_status=?, order_amount=?, buyer_spec=?,
                        quantity=?, source_platform=?, source_url=?, source_item_id=?,
                        source_sku_id=?, match_status=?, upstream_order_id=?,
                        upstream_status=?, notes=?, updated_at=?
                    WHERE id=?
                """, (
                    order.get("product_id"),
                    order.get("platform", "xianyu"),
                    order.get("buyer_name", ""),
                    order.get("buyer_phone", ""),
                    order.get("buyer_address", ""),
                    order.get("order_status", "pending"),
                    order.get("order_amount", ""),
                    order.get("buyer_spec", ""),
                    int(order.get("quantity") or 1),
                    order.get("source_platform", ""),
                    order.get("source_url", ""),
                    order.get("source_item_id", ""),
                    order.get("source_sku_id", ""),
                    order.get("match_status", "unmatched"),
                    order.get("upstream_order_id", ""),
                    order.get("upstream_status", ""),
                    order.get("notes", ""),
                    datetime.now().isoformat(),
                    existing["id"],
                ))
                return existing["id"]
            cursor = conn.execute("""
                INSERT INTO orders (
                    product_id, platform_order_id, platform, buyer_name,
                    buyer_phone, buyer_address, order_status, order_amount,
                    buyer_spec, quantity, source_platform, source_url,
                    source_item_id, source_sku_id, match_status,
                    upstream_order_id, upstream_status, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, data)
            return cursor.lastrowid

    def get_all_orders(self, status: str = None) -> List[Dict]:
        with self._get_conn() as conn:
            if status:
                rows = conn.execute(
                    "SELECT * FROM orders WHERE order_status = ? ORDER BY created_at DESC",
                    (status,)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM orders ORDER BY created_at DESC"
                ).fetchall()
            return [dict(row) for row in rows]

    def update_order_status(self, order_id: int, status: str, upstream_info: Dict = None):
        with self._get_conn() as conn:
            if upstream_info:
                conn.execute("""
                    UPDATE orders SET
                        order_status = ?,
                        upstream_order_id = ?,
                        upstream_status = ?,
                        updated_at = ?
                    WHERE id = ?
                """, (
                    status,
                    upstream_info.get("upstream_order_id", ""),
                    upstream_info.get("upstream_status", ""),
                    datetime.now().isoformat(),
                    order_id,
                ))
            else:
                conn.execute(
                    "UPDATE orders SET order_status = ?, updated_at = ? WHERE id = ?",
                    (status, datetime.now().isoformat(), order_id)
                )

    # ═══════════════════ 【新增】运营监控快照 ═══════════════════

    def save_monitor_snapshot(self, data: Dict):
        """保存平台运营监控快照"""
        with self._get_conn() as conn:
            conn.execute("""
                INSERT INTO monitor_snapshots (
                    platform, snapshot_time, is_logged_in, active_listings,
                    total_views, total_wants, total_inquiries,
                    pending_orders, completed_orders_today, completed_orders_30d,
                    revenue_today, revenue_30d, alerts, raw_data, error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                data.get("platform", ""),
                data.get("timestamp", datetime.now().isoformat()),
                1 if data.get("is_logged_in") else 0,
                data.get("active_listings", 0),
                data.get("total_views", 0),
                data.get("total_wants", 0),
                data.get("total_inquiries", 0),
                data.get("pending_orders", 0),
                data.get("completed_orders_today", 0),
                data.get("completed_orders_30d", 0),
                data.get("revenue_today", 0.0),
                data.get("revenue_30d", 0.0),
                json.dumps(data.get("alerts", []), ensure_ascii=False),
                json.dumps(data.get("raw_data", {}), ensure_ascii=False),
                data.get("error", ""),
            ))

    def get_monitor_snapshots(self, platform: str, days: int = 7) -> List[Dict]:
        """获取某平台最近 N 天的监控快照（最新在前）"""
        with self._get_conn() as conn:
            rows = conn.execute("""
                SELECT * FROM monitor_snapshots
                WHERE platform = ?
                  AND snapshot_time >= datetime('now', ? || ' days')
                ORDER BY snapshot_time DESC
                LIMIT 200
            """, (platform, f"-{days}")).fetchall()
            result = []
            for row in rows:
                d = dict(row)
                try:
                    d["alerts"] = json.loads(d.get("alerts") or "[]")
                except Exception:
                    d["alerts"] = []
                result.append(d)
            return result

    def get_latest_monitor_snapshot(self, platform: str) -> Optional[Dict]:
        """获取某平台最新的一条快照"""
        rows = self.get_monitor_snapshots(platform, days=30)
        return rows[0] if rows else None

    def clear_monitor_snapshots(self, platform: str = None, before_days: int = 90):
        """清理旧的监控快照"""
        with self._get_conn() as conn:
            if platform:
                conn.execute("""
                    DELETE FROM monitor_snapshots
                    WHERE platform = ?
                      AND snapshot_time < datetime('now', ? || ' days')
                """, (platform, f"-{before_days}"))
            else:
                conn.execute("""
                    DELETE FROM monitor_snapshots
                    WHERE snapshot_time < datetime('now', ? || ' days')
                """, (f"-{before_days}",))

    # ═══════════════════ Cookie 操作 ═══════════════════

    def save_cookie(self, platform: str, cookie_data: str):
        with self._get_conn() as conn:
            conn.execute("""
                INSERT INTO cookies (platform, cookie_data, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(platform) DO UPDATE SET
                    cookie_data = excluded.cookie_data,
                    updated_at = excluded.updated_at
            """, (platform, cookie_data, datetime.now().isoformat()))

    def get_cookie(self, platform: str) -> Optional[str]:
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT cookie_data FROM cookies WHERE platform = ?", (platform,)
            ).fetchone()
            return row["cookie_data"] if row else None

    def delete_cookie(self, platform: str):
        with self._get_conn() as conn:
            conn.execute("DELETE FROM cookies WHERE platform = ?", (platform,))

    # ═══════════════════ 定时任务调度 ═══════════════════

    def save_scheduled_task(self, task: Dict) -> int:
        """新增或更新一条定时任务。含 id 则更新，否则新增。"""
        import json as _json
        params_json = _json.dumps(task.get("params", {}), ensure_ascii=False)
        with self._get_conn() as conn:
            if task.get("id"):
                conn.execute("""
                    UPDATE scheduled_tasks SET
                        name = ?, task_type = ?, trigger = ?,
                        interval_minutes = ?, daily_time = ?, params = ?,
                        enabled = ?
                    WHERE id = ?
                """, (
                    task.get("name", ""),
                    task.get("task_type", ""),
                    task.get("trigger", "interval"),
                    int(task.get("interval_minutes") or 60),
                    task.get("daily_time", "09:00"),
                    params_json,
                    1 if task.get("enabled", True) else 0,
                    task["id"],
                ))
                return task["id"]
            cur = conn.execute("""
                INSERT INTO scheduled_tasks
                    (name, task_type, trigger, interval_minutes, daily_time,
                     params, enabled)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                task.get("name", ""),
                task.get("task_type", ""),
                task.get("trigger", "interval"),
                int(task.get("interval_minutes") or 60),
                task.get("daily_time", "09:00"),
                params_json,
                1 if task.get("enabled", True) else 0,
            ))
            return cur.lastrowid

    def get_scheduled_tasks(self) -> List[Dict]:
        """取全部定时任务（含 params 反序列化、enabled 转 bool）。"""
        import json as _json
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM scheduled_tasks ORDER BY id"
            ).fetchall()
            out = []
            for row in rows:
                d = dict(row)
                try:
                    d["params"] = _json.loads(d.get("params") or "{}")
                except Exception:
                    d["params"] = {}
                d["enabled"] = bool(d.get("enabled"))
                out.append(d)
            return out

    def set_task_enabled(self, task_id: int, enabled: bool):
        with self._get_conn() as conn:
            conn.execute(
                "UPDATE scheduled_tasks SET enabled = ? WHERE id = ?",
                (1 if enabled else 0, task_id),
            )

    def mark_task_run(self, task_id: int, result: str = ""):
        """标记任务已运行：更新 last_run 与 last_result。"""
        with self._get_conn() as conn:
            conn.execute(
                "UPDATE scheduled_tasks SET last_run = ?, last_result = ? WHERE id = ?",
                (datetime.now().isoformat(), str(result)[:500], task_id),
            )

    def delete_scheduled_task(self, task_id: int):
        with self._get_conn() as conn:
            conn.execute("DELETE FROM scheduled_tasks WHERE id = ?", (task_id,))

    # ═══════════════════ 源商品复检 ═══════════════════

    def save_recheck(self, row: Dict) -> int:
        """保存一条源商品复检结果。row 来自 RecheckEngine.recheck_products。"""
        import json as _json
        with self._get_conn() as conn:
            cur = conn.execute("""
                INSERT INTO source_rechecks
                    (product_id, title, platform, source_url, listing_price,
                     old_min_price, new_min_price, level, summary, alerts, checked_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                row.get("db_id"),
                row.get("title", ""),
                row.get("platform", ""),
                row.get("source_url", ""),
                float(row.get("listing_price") or 0),
                float(row.get("old_min_price") or 0),
                float(row.get("new_min_price") or 0),
                row.get("level", "none"),
                row.get("summary", ""),
                _json.dumps(row.get("alerts", []), ensure_ascii=False),
                datetime.now().isoformat(),
            ))
            return cur.lastrowid

    def get_latest_rechecks(self, only_alert: bool = False) -> List[Dict]:
        """取每个商品最近一次复检结果。only_alert=True 仅返回有告警(level!=none)的。"""
        import json as _json
        with self._get_conn() as conn:
            rows = conn.execute("""
                SELECT r.* FROM source_rechecks r
                JOIN (
                    SELECT product_id, MAX(checked_at) AS mx
                    FROM source_rechecks GROUP BY product_id
                ) t ON r.product_id = t.product_id AND r.checked_at = t.mx
                ORDER BY
                    CASE r.level WHEN 'critical' THEN 3 WHEN 'warn' THEN 2
                                 WHEN 'info' THEN 1 ELSE 0 END DESC,
                    r.checked_at DESC
            """).fetchall()
            out = []
            for row in rows:
                d = dict(row)
                try:
                    d["alerts"] = _json.loads(d.get("alerts") or "[]")
                except Exception:
                    d["alerts"] = []
                if only_alert and d.get("level", "none") == "none":
                    continue
                out.append(d)
            return out

    def clear_rechecks(self, before_days: int = 90):
        """清理过期复检记录（默认保留 90 天）。"""
        with self._get_conn() as conn:
            conn.execute(
                "DELETE FROM source_rechecks WHERE checked_at < datetime('now', ?)",
                (f'-{int(before_days)} days',),
            )

    # ═══════════════════ 历史数据删除 / 统计 ═══════════════════

    def delete_products(self, product_ids: list, remove_local: bool = False) -> dict:
        """批量删除商品。remove_local=True 时级联删除托管的本地图片目录。

        返回 {"products": 删除条数, "local_dirs": 删除本地目录数}。
        """
        ids = [int(i) for i in (product_ids or []) if i is not None]
        if not ids:
            return {"products": 0, "local_dirs": 0}
        local_dirs = set()
        if remove_local:
            for pid in ids:
                product = self.get_product_by_id(pid)
                if product:
                    local_dirs |= _iter_local_image_dirs(product)
        with self._get_conn() as conn:
            qmarks = ",".join("?" * len(ids))
            cur = conn.execute(
                f"DELETE FROM products WHERE id IN ({qmarks})", ids
            )
            deleted = cur.rowcount
        removed = _safe_remove_dirs(local_dirs) if remove_local else 0
        return {"products": deleted, "local_dirs": removed}

    def clear_products(self, remove_local: bool = False) -> dict:
        """清空全部商品。remove_local=True 时级联删除托管的本地图片目录。"""
        local_dirs = set()
        deleted = 0
        with self._get_conn() as conn:
            if remove_local:
                rows = conn.execute("SELECT * FROM products").fetchall()
                for row in rows:
                    local_dirs |= _iter_local_image_dirs(self._row_to_product(row))
            cur = conn.execute("DELETE FROM products")
            deleted = cur.rowcount
        removed = _safe_remove_dirs(local_dirs) if remove_local else 0
        return {"products": deleted, "local_dirs": removed}

    def clear_orders(self) -> int:
        with self._get_conn() as conn:
            cur = conn.execute("DELETE FROM orders")
            return cur.rowcount

    def clear_collect_records(self) -> int:
        with self._get_conn() as conn:
            cur = conn.execute("DELETE FROM collect_records")
            return cur.rowcount

    def clear_all_monitor_snapshots(self, platform: str = None) -> int:
        with self._get_conn() as conn:
            if platform:
                cur = conn.execute(
                    "DELETE FROM monitor_snapshots WHERE platform = ?", (platform,)
                )
            else:
                cur = conn.execute("DELETE FROM monitor_snapshots")
            return cur.rowcount

    def clear_all_rechecks(self) -> int:
        with self._get_conn() as conn:
            cur = conn.execute("DELETE FROM source_rechecks")
            return cur.rowcount

    def clear_scheduled_tasks(self) -> int:
        with self._get_conn() as conn:
            cur = conn.execute("DELETE FROM scheduled_tasks")
            return cur.rowcount

    def data_counts(self) -> dict:
        """各历史数据表的当前条数，用于“数据管理”界面展示。"""
        tables = {
            "products": "products",
            "orders": "orders",
            "collect_records": "collect_records",
            "monitor_snapshots": "monitor_snapshots",
            "source_rechecks": "source_rechecks",
            "scheduled_tasks": "scheduled_tasks",
        }
        out = {}
        with self._get_conn() as conn:
            for key, tbl in tables.items():
                try:
                    out[key] = conn.execute(
                        f"SELECT COUNT(*) AS c FROM {tbl}"
                    ).fetchone()["c"]
                except Exception:
                    out[key] = 0
        return out



# 全局数据库实例
db = DatabaseManager()
