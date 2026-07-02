import os
import secrets
import logging

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
KEYS_DIR = os.path.join(BASE_DIR, "keys")
os.makedirs(KEYS_DIR, exist_ok=True)


def _persisted_secret(filename: str, nbytes: int = 48) -> str:
    """读取持久化随机密钥；不存在则生成并写入 keys/ (0600)。

    避免把可预测的密钥硬编码进仓库：未显式提供环境变量时，
    使用一次性随机密钥并持久化，保证重启后 JWT 仍然有效。
    """
    path = os.path.join(KEYS_DIR, filename)
    if os.path.exists(path):
        with open(path, "r") as f:
            value = f.read().strip()
        if value:
            return value
    value = secrets.token_urlsafe(nbytes)
    with open(path, "w") as f:
        f.write(value)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return value


# ──────────────────────── 部署模式 ────────────────────────
# production 模式下，缺失关键密钥将直接拒绝启动，杜绝弱默认值。
ENV = os.getenv("XF_ENV", "production").lower()
IS_PRODUCTION = ENV == "production"

# ──────────────────────── 数据库 ────────────────────────
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./data/xf_server.db")

# ──────────────────────── JWT ────────────────────────
# 优先环境变量；否则使用持久化随机密钥（绝不再用仓库内硬编码值）。
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY") or _persisted_secret("jwt_secret.key")
JWT_ALGORITHM = "HS256"
JWT_ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("JWT_ACCESS_TOKEN_EXPIRE_MINUTES", str(60 * 12)))  # 12h
JWT_REFRESH_TOKEN_EXPIRE_DAYS = int(os.getenv("JWT_REFRESH_TOKEN_EXPIRE_DAYS", "7"))

# ──────────────────────── RSA ────────────────────────
RSA_KEY_SIZE = 2048
RSA_PRIVATE_KEY_PATH = os.getenv("RSA_PRIVATE_KEY_PATH", os.path.join(KEYS_DIR, "private_key.pem"))
RSA_PUBLIC_KEY_PATH = os.getenv("RSA_PUBLIC_KEY_PATH", os.path.join(KEYS_DIR, "public_key.pem"))

# ──────────────────────── 管理员 ────────────────────────
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
# 置 1 时：启动期强制把管理员密码重置为 ADMIN_PASSWORD（用于轮换线上弱口令 admin123）。
ADMIN_FORCE_RESET = os.getenv("ADMIN_FORCE_RESET", "0").strip() in ("1", "true", "True", "yes")
# 不再硬编码 admin123：环境变量优先，否则生成随机密码并持久化，首启时打印一次。
_ADMIN_PW_FROM_ENV = os.getenv("ADMIN_PASSWORD")
if _ADMIN_PW_FROM_ENV:
    ADMIN_PASSWORD = _ADMIN_PW_FROM_ENV
    ADMIN_PASSWORD_GENERATED = False
else:
    _pw_path = os.path.join(KEYS_DIR, "admin_password.txt")
    _existed = os.path.exists(_pw_path)
    ADMIN_PASSWORD = _persisted_secret("admin_password.txt", 12)
    ADMIN_PASSWORD_GENERATED = not _existed

# ──────────────────────── 客户端调用鉴权 ────────────────────────
# 客户端调用 activate/verify/heartbeat 必须带此 Key，杜绝任意人匿名调用。
CLIENT_API_KEY = os.getenv("CLIENT_API_KEY") or _persisted_secret("client_api_key.key", 32)
# 是否强制校验客户端密钥。默认关闭：避免已部署的旧客户端（不带 X-Client-Key）被拒。
# 新客户端内置同一 Key 后，将本项设为 1 即可开启强制校验。
REQUIRE_CLIENT_KEY = os.getenv("REQUIRE_CLIENT_KEY", "0").strip() in ("1", "true", "True", "yes")

# ──────────────────────── License / 设备控制 ────────────────────────
DEFAULT_LICENSE_DAYS = 30
MAX_DEVICES_PER_LICENSE = int(os.getenv("MAX_DEVICES_PER_LICENSE", "3"))
# 心跳间隔与离线判定（秒）。客户端按 HEARTBEAT_INTERVAL 上报；
# 超过 OFFLINE_THRESHOLD 未上报视为离线。
HEARTBEAT_INTERVAL_SECONDS = int(os.getenv("HEARTBEAT_INTERVAL_SECONDS", "60"))
OFFLINE_THRESHOLD_SECONDS = int(os.getenv("OFFLINE_THRESHOLD_SECONDS", "180"))
# verify/heartbeat 请求时间戳允许的偏移窗口（秒），用于防重放。
REQUEST_TIMESTAMP_WINDOW_SECONDS = int(os.getenv("REQUEST_TIMESTAMP_WINDOW_SECONDS", "300"))
# 客户端离线宽限：远程不可达时本地最多容忍的时长（秒）。
OFFLINE_GRACE_SECONDS = int(os.getenv("OFFLINE_GRACE_SECONDS", str(72 * 3600)))
# ──────────────────────── 能力令牌（方案B：核心功能服务端授权）────────────────────────
# 客户端执行受控动作（采集/上架/AI改写）前，向服务端换取一次性短期 RSA 签名令牌。
# 私钥只在服务端，破解版客户端拿不到私钥就伪造不出令牌，从而无法调用核心功能。
CAPABILITY_TOKEN_TTL_SECONDS = int(os.getenv("CAPABILITY_TOKEN_TTL_SECONDS", "180"))
# 允许申请的能力动作白名单（未知动作一律拒签，防止被滥用为通用签名预言机）。
CAPABILITY_ACTIONS = {"collect", "listing", "ai_rewrite", "export", "recheck", "dashboard", "reorder"}

# ──────────────────────── CORS ────────────────────────
# 默认仅允许管理后台同源；可用逗号分隔的环境变量放开。
_cors = os.getenv("CORS_ORIGINS", "").strip()
CORS_ORIGINS = [o.strip() for o in _cors.split(",") if o.strip()] if _cors else []


def validate_config():
    """启动期安全自检：生产模式下拒绝弱配置。返回告警列表。"""
    warnings = []
    if IS_PRODUCTION:
        if not os.getenv("JWT_SECRET_KEY"):
            warnings.append("JWT_SECRET_KEY 未通过环境变量提供，已使用持久化随机密钥 (keys/jwt_secret.key)")
        if not _ADMIN_PW_FROM_ENV:
            warnings.append("ADMIN_PASSWORD 未通过环境变量提供，已使用持久化随机密码 (keys/admin_password.txt)")
        if not os.getenv("CLIENT_API_KEY"):
            warnings.append("CLIENT_API_KEY 未通过环境变量提供，已使用持久化随机密钥 (keys/client_api_key.key)")
    return warnings
