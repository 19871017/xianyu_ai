import os

# 服务器配置
SERVER_URL = os.getenv("XF_SERVER_URL", "http://38.71.117.111:8000")

# AI API配置（兼容OpenAI格式中转）
# 支持任意OpenAI兼容API：DeepSeek、OpenAI、OneAPI、NewAPI等
AI_API_URL = os.getenv("AI_API_URL", "")
AI_API_KEY = os.getenv("AI_API_KEY", "")
AI_MODEL = os.getenv("AI_MODEL", "")

# 闲鱼
XIANYU_BASE_URL = "https://www.goofish.com"

# 导出
EXPORT_DIR = os.path.join(os.path.expanduser("~"), "Desktop", "闲鱼数据")
IMAGE_DIR = os.path.join(EXPORT_DIR, "images")

# License
LICENSE_FILE = os.path.join(os.path.expanduser("~"), ".xf_license.json")
