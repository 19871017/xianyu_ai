import os

# 数据库
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./data/xf_server.db")

# JWT
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "xf-ai-secret-key-change-in-production-2026")
JWT_ALGORITHM = "HS256"
JWT_ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24  # 24h
JWT_REFRESH_TOKEN_EXPIRE_DAYS = 30

# RSA
RSA_KEY_SIZE = 2048
RSA_PRIVATE_KEY_PATH = os.getenv("RSA_PRIVATE_KEY_PATH", "keys/private_key.pem")
RSA_PUBLIC_KEY_PATH = os.getenv("RSA_PUBLIC_KEY_PATH", "keys/public_key.pem")

# 管理员
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")

# License
DEFAULT_LICENSE_DAYS = 30
MAX_DEVICES_PER_LICENSE = 3

# CORS
CORS_ORIGINS = ["*"]
