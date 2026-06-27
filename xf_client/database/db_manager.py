import json
import sqlite3
import os
from datetime import datetime
from typing import List, Dict, Optional

try:
    from engine.product_package import PACKAGE_ATTR_KEY
except Exception:
    PACKAGE_ATTR_KEY = '_full_product_package'


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

    def delete_product(self, product_id: int):
        with self._get_conn() as conn:
            conn.execute("DELETE FROM products WHERE id = ?", (product_id,))

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


# 全局数据库实例
db = DatabaseManager()
