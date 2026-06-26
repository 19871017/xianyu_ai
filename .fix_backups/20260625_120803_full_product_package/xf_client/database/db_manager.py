import json
import sqlite3
import os
from datetime import datetime
from typing import List, Dict, Optional


DB_PATH = os.path.join(os.path.expanduser("~"), ".xf_data", "data.db")


class DatabaseManager:
    """SQLite数据库管理器 - 商品、订单、采集记录持久化"""

    def __init__(self):
        self._ensure_db_dir()
        self._init_tables()

    def _ensure_db_dir(self):
        """确保数据库目录存在"""
        db_dir = os.path.dirname(DB_PATH)
        if not os.path.exists(db_dir):
            os.makedirs(db_dir, exist_ok=True)

    def _get_conn(self):
        """获取数据库连接"""
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_tables(self):
        """初始化数据表"""
        with self._get_conn() as conn:
            # 商品表
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
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # 订单表
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
                    upstream_order_id TEXT,
                    upstream_status TEXT,
                    notes TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (product_id) REFERENCES products(id)
                )
            """)

            # 采集记录表
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

            # Cookie存储表
            conn.execute("""
                CREATE TABLE IF NOT EXISTS cookies (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    platform TEXT UNIQUE,
                    cookie_data TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            conn.commit()

    # ========== 商品操作 ==========

    def save_product(self, item: Dict) -> int:
        """保存或更新商品"""
        with self._get_conn() as conn:
            # 检查是否已存在
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
                "local_images": json.dumps(item.get("local_images", []), ensure_ascii=False),
                "attributes": json.dumps(item.get("attributes", {}), ensure_ascii=False),
                "tags": json.dumps(item.get("tags", []), ensure_ascii=False),
                "status": item.get("status", "collected"),
                "updated_at": datetime.now().isoformat(),
            }

            if existing:
                # 更新
                data["id"] = existing["id"]
                conn.execute("""
                    UPDATE products SET
                        platform=:platform,
                        ai_title=:ai_title,
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
                # 插入
                cursor = conn.execute("""
                    INSERT INTO products (
                        item_id, platform, original_title, ai_title, description,
                        original_price, new_price, price_markup_pct, wants, views,
                        seller, seller_credit, source_url, source_item_id,
                        local_images, attributes, tags, status, updated_at
                    ) VALUES (
                        :item_id, :platform, :original_title, :ai_title, :description,
                        :original_price, :new_price, :price_markup_pct, :wants, :views,
                        :seller, :seller_credit, :source_url, :source_item_id,
                        :local_images, :attributes, :tags, :status, :updated_at
                    )
                """, data)
                return cursor.lastrowid

    def get_all_products(self, status: str = None) -> List[Dict]:
        """获取所有商品"""
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
        """根据ID获取商品"""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM products WHERE id = ?",
                (product_id,)
            ).fetchone()
            return self._row_to_product(row) if row else None

    def get_product_by_item_id(self, item_id: str) -> Optional[Dict]:
        """根据item_id获取商品"""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM products WHERE item_id = ?",
                (item_id,)
            ).fetchone()
            return self._row_to_product(row) if row else None

    def update_product_status(self, product_id: int, status: str):
        """更新商品状态"""
        with self._get_conn() as conn:
            conn.execute(
                "UPDATE products SET status = ?, updated_at = ? WHERE id = ?",
                (status, datetime.now().isoformat(), product_id)
            )

    def delete_product(self, product_id: int):
        """删除商品"""
        with self._get_conn() as conn:
            conn.execute("DELETE FROM products WHERE id = ?", (product_id,))

    def _row_to_product(self, row: sqlite3.Row) -> Dict:
        """将数据库行转换为商品字典"""
        return {
            "db_id": row["id"],
            "item_id": row["item_id"],
            "platform": row["platform"],
            "original_title": row["original_title"],
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
            "local_images": json.loads(row["local_images"] or "[]"),
            "attributes": json.loads(row["attributes"] or "{}"),
            "tags": json.loads(row["tags"] or "[]"),
            "status": row["status"],
            "created_at": row["created_at"],
        }

    # ========== 订单操作 ==========

    def save_order(self, order: Dict) -> int:
        """保存订单"""
        with self._get_conn() as conn:
            cursor = conn.execute("""
                INSERT INTO orders (
                    product_id, platform_order_id, platform, buyer_name,
                    buyer_phone, buyer_address, order_status, order_amount,
                    upstream_order_id, upstream_status, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                order.get("product_id"),
                order.get("platform_order_id"),
                order.get("platform", "xianyu"),
                order.get("buyer_name", ""),
                order.get("buyer_phone", ""),
                order.get("buyer_address", ""),
                order.get("order_status", "pending"),
                order.get("order_amount", ""),
                order.get("upstream_order_id", ""),
                order.get("upstream_status", ""),
                order.get("notes", ""),
            ))
            return cursor.lastrowid

    def get_orders_by_product(self, product_id: int) -> List[Dict]:
        """获取商品关联的订单"""
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM orders WHERE product_id = ? ORDER BY created_at DESC",
                (product_id,)
            ).fetchall()
            return [dict(row) for row in rows]

    def get_all_orders(self, status: str = None) -> List[Dict]:
        """获取所有订单"""
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
        """更新订单状态"""
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
                    order_id
                ))
            else:
                conn.execute(
                    "UPDATE orders SET order_status = ?, updated_at = ? WHERE id = ?",
                    (status, datetime.now().isoformat(), order_id)
                )

    # ========== Cookie操作 ==========

    def save_cookie(self, platform: str, cookie_data: str):
        """保存Cookie"""
        with self._get_conn() as conn:
            conn.execute("""
                INSERT INTO cookies (platform, cookie_data, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(platform) DO UPDATE SET
                    cookie_data = excluded.cookie_data,
                    updated_at = excluded.updated_at
            """, (platform, cookie_data, datetime.now().isoformat()))

    def get_cookie(self, platform: str) -> Optional[str]:
        """获取Cookie"""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT cookie_data FROM cookies WHERE platform = ?",
                (platform,)
            ).fetchone()
            return row["cookie_data"] if row else None

    def delete_cookie(self, platform: str):
        """删除Cookie"""
        with self._get_conn() as conn:
            conn.execute("DELETE FROM cookies WHERE platform = ?", (platform,))


# 全局数据库实例
db = DatabaseManager()
