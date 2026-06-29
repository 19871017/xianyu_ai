#!/bin/bash
# 闲鱼AI助手服务端 - 稳定版部署脚本
# 用法: 在服务器上执行 bash deploy_stable.sh
set -e

echo "=========================================="
echo "  闲鱼AI助手服务端 - 稳定版部署"
echo "=========================================="

# 配置
DEPLOY_DIR="/opt/xf_server"
BACKUP_DIR="/opt/xf_server_backup_$(date +%Y%m%d_%H%M%S)"
SOURCE_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "[1/8] 检查环境..."
if ! command -v python3 &> /dev/null; then
    echo "❌ python3 未安装"
    exit 1
fi
if ! command -v pip3 &> /dev/null; then
    echo "❌ pip3 未安装"
    exit 1
fi
echo "✅ python3: $(python3 --version)"

echo "[2/8] 安装依赖..."
pip3 install -q fastapi uvicorn[standard] sqlalchemy pydantic python-jose[cryptography] bcrypt cryptography python-multipart 2>&1 | tail -3

echo "[3/8] 备份旧代码..."
if [ -d "$DEPLOY_DIR" ]; then
    cp -r "$DEPLOY_DIR" "$BACKUP_DIR"
    echo "✅ 已备份到 $BACKUP_DIR"
else
    echo "⚠️  旧目录不存在，首次部署"
    mkdir -p "$DEPLOY_DIR"
fi

echo "[4/8] 部署新代码..."
# 创建目录结构
mkdir -p "$DEPLOY_DIR"/{models,schemas,services,routers,utils,admin_frontend/static,site_frontend,downloads,keys,data}

# 复制所有Python文件
cp "$SOURCE_DIR"/config.py "$DEPLOY_DIR/"
cp "$SOURCE_DIR"/main.py "$DEPLOY_DIR/"
cp "$SOURCE_DIR"/requirements.txt "$DEPLOY_DIR/"
cp "$SOURCE_DIR"/models/*.py "$DEPLOY_DIR/models/"
cp "$SOURCE_DIR"/schemas/*.py "$DEPLOY_DIR/schemas/"
cp "$SOURCE_DIR"/services/*.py "$DEPLOY_DIR/services/"
cp "$SOURCE_DIR"/routers/*.py "$DEPLOY_DIR/routers/"
cp "$SOURCE_DIR"/utils/*.py "$DEPLOY_DIR/utils/"
cp "$SOURCE_DIR"/admin_frontend/index.html "$DEPLOY_DIR/admin_frontend/"
cp "$SOURCE_DIR"/site_frontend/index.html "$DEPLOY_DIR/site_frontend/"

# 保留旧的RSA密钥和数据库
if [ -f "$BACKUP_DIR/keys/private_key.pem" ]; then
    cp "$BACKUP_DIR/keys/private_key.pem" "$DEPLOY_DIR/keys/"
    cp "$BACKUP_DIR/keys/public_key.pem" "$DEPLOY_DIR/keys/"
    echo "✅ 保留RSA密钥"
fi
if [ -f "$BACKUP_DIR/data/xf_server.db" ]; then
    cp "$BACKUP_DIR/data/xf_server.db" "$DEPLOY_DIR/data/"
    echo "✅ 保留数据库"
fi
# 保留用户上传的安装包（downloads 目录），升级不丢历史版本文件。
if [ -d "$BACKUP_DIR/downloads" ]; then
    cp -r "$BACKUP_DIR/downloads/." "$DEPLOY_DIR/downloads/" 2>/dev/null || true
    echo "✅ 保留安装包(downloads)"
fi

echo "[5/8] 数据库迁移..."
cd "$DEPLOY_DIR"
python3 << 'MIGRATE_EOF'
import sqlite3
import os

DB_PATH = "data/xf_server.db"
if not os.path.exists(DB_PATH):
    print("  数据库不存在，将在启动时自动创建")
else:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # 检查users表是否有role字段
    cursor.execute("PRAGMA table_info(users)")
    cols = [row[1] for row in cursor.fetchall()]
    if "role" not in cols:
        print("  添加users.role字段...")
        cursor.execute("ALTER TABLE users ADD COLUMN role VARCHAR(20) DEFAULT 'user'")
        # 把is_admin=1的用户设为admin
        if "is_admin" in cols:
            cursor.execute("UPDATE users SET role='admin' WHERE is_admin=1")
    if "is_admin" in cols and "is_active" not in cols:
        cursor.execute("ALTER TABLE users ADD COLUMN is_active BOOLEAN DEFAULT 1")
    
    # 检查licenses表
    cursor.execute("PRAGMA table_info(licenses)")
    cols = [row[1] for row in cursor.fetchall()]
    if "issued_at" not in cols and "created_at" in cols:
        print("  重命名licenses.created_at → issued_at...")
        cursor.execute("ALTER TABLE licenses RENAME COLUMN created_at TO issued_at")
    elif "issued_at" not in cols and "created_at" not in cols:
        print("  添加licenses.issued_at字段...")
        cursor.execute("ALTER TABLE licenses ADD COLUMN issued_at DATETIME")
    if "days" not in cols:
        print("  添加licenses.days字段...")
        cursor.execute("ALTER TABLE licenses ADD COLUMN days INTEGER DEFAULT 30")
    if "signature" not in cols:
        print("  添加licenses.signature字段...")
        cursor.execute("ALTER TABLE licenses ADD COLUMN signature VARCHAR(512) DEFAULT ''")
    if "activated_at" not in cols:
        print("  添加licenses.activated_at字段...")
        cursor.execute("ALTER TABLE licenses ADD COLUMN activated_at DATETIME")
    
    conn.commit()
    conn.close()
    print("  ✅ 数据库迁移完成")
MIGRATE_EOF

echo "[6/8] 停止旧服务..."
# 停止旧的uvicorn进程
pkill -f "uvicorn.*main:app" 2>/dev/null || true
sleep 2
echo "✅ 旧服务已停止"

echo "[7/8] 启动新服务..."
cd "$DEPLOY_DIR"
# 加载安全配置（密钥/管理员密码等）。建议在 /opt/xf_server/.env 中提供：
#   XF_ENV=production
#   JWT_SECRET_KEY=...(随机长串)
#   ADMIN_PASSWORD=...(强密码)   ADMIN_FORCE_RESET=1  # 首次轮换线上弱口令后可去掉
#   CLIENT_API_KEY=...(随机长串) REQUIRE_CLIENT_KEY=1 # 客户端全部升级后再开启
if [ -f "$DEPLOY_DIR/.env" ]; then
    set -a; . "$DEPLOY_DIR/.env"; set +a
    echo "✅ 已加载 .env 安全配置"
else
    echo "⚠️  未发现 $DEPLOY_DIR/.env，将使用持久化随机密钥(见 keys/)。建议创建 .env 显式配置密钥。"
fi
nohup python3 -m uvicorn main:app --host 0.0.0.0 --port 8000 --workers 1 > /var/log/xf_server.log 2>&1 &
sleep 3

# 检查是否启动成功
if curl -s http://127.0.0.1:8000/ | grep -q "闲鱼AI助手"; then
    echo "✅ 服务启动成功"
else
    echo "⚠️  服务可能未正常启动，检查日志:"
    tail -20 /var/log/xf_server.log
fi

echo "[8/8] 验证..."
echo ""
echo "  API地址: http://$(hostname -I | awk '{print $1}'):8000/"
echo "  管理后台: http://$(hostname -I | awk '{print $1}'):8000/admin"
echo "  API文档: http://$(hostname -I | awk '{print $1}'):8000/docs"
echo "  管理员: admin / (见 .env 的 ADMIN_PASSWORD，或 keys/admin_password.txt)"
echo ""
echo "  日志: tail -f /var/log/xf_server.log"
echo ""
echo "=========================================="
echo "  部署完成！"
echo "=========================================="
