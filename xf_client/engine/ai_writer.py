import json
import os
import sys
import requests
from config import AI_API_URL, AI_API_KEY, AI_MODEL
from license.capability_guard import require_capability, CapabilityError


def _get_cert_path():
    """获取SSL证书路径（兼容PyInstaller打包）"""
    if getattr(sys, 'frozen', False):
        # PyInstaller打包后的路径
        base_path = sys._MEIPASS
        cert_path = os.path.join(base_path, 'certifi', 'cacert.pem')
        if os.path.exists(cert_path):
            return cert_path
    # 开发环境
    try:
        import certifi
        return certifi.where()
    except:
        return None


class AIWriter:
    """AI文案改写 - 兼容OpenAI格式API（DeepSeek/OpenAI/OneAPI/NewAPI等中转）"""

    def __init__(self, api_url: str = None, api_key: str = None, model: str = None):
        self._load_runtime_config()
        self.api_url = (api_url or self._runtime_url or AI_API_URL).rstrip("/")
        self.api_key = api_key or self._runtime_key or AI_API_KEY
        self.model = model or self._runtime_model or AI_MODEL
        self._cert_path = _get_cert_path()

    def _load_runtime_config(self):
        """从 ~/.xf_env 读取运行时配置"""
        self._runtime_url = ""
        self._runtime_key = ""
        self._runtime_model = ""
        env_path = os.path.join(os.path.expanduser("~"), ".xf_env")
        if os.path.exists(env_path):
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if "=" in line:
                        k, v = line.split("=", 1)
                        k = k.strip()
                        v = v.strip()
                        if k == "AI_API_URL":
                            self._runtime_url = v
                        elif k == "AI_API_KEY":
                            self._runtime_key = v
                        elif k == "AI_API_MODEL":
                            self._runtime_model = v

    def _is_configured(self) -> bool:
        return bool(self.api_url and self.api_key and self.model)

    def _normalize_url(self, url: str) -> str:
        """自动补全OpenAI兼容的chat completions路径"""
        base = url.rstrip("/")
        if "/v1/chat/completions" in base or "/chat/completions" in base:
            return base
        if base.endswith("/v1"):
            return f"{base}/chat/completions"
        return f"{base}/v1/chat/completions"

    def rewrite(self, title: str, description: str, price: str = "") -> dict:
        """同步改写（在QThread中直接调用）"""
        try:
            require_capability("ai_rewrite")
        except CapabilityError as _ce:
            return {"success": False, "error": f"未获授权: {_ce}"}
        self._load_runtime_config()
        self.api_url = (self._runtime_url or AI_API_URL).rstrip("/")
        self.api_key = self._runtime_key or AI_API_KEY
        self.model = self._runtime_model or AI_MODEL

        if not self._is_configured():
            return {
                "success": False,
                "error": "未配置AI API，请在设置页面填写API地址、Key并选择模型"
            }

        prompt = f"""你是闲鱼商品文案优化专家。请把下面的原始商品信息，改写成适合闲鱼平台的「全新现货」销售文案。

商品定位：全新正品现货，非二手、非闲置、非个人在用转手。文案口吻必须是商家卖新货，严禁出现"自用""闲置""在用""转手""九成新""用了一段时间"等二手措辞。

标题要求（最重要）：
1. 20 字以内，简洁通顺，像一句正常的中文短语，不要堆砌关键词。
2. 突出最核心的品类 + 1~2 个卖点（如材质/适用/款式），不要把多个机型、多个颜色、多个尺寸罗列进标题。
3. 不要出现错别字、繁体字、无意义符号或重复词。

描述要求：
1. 真实、自然、有购买欲，分 2~4 句口语化短句，可适当换行。
2. 突出卖点（品质/适用场景/规格丰富等），不要编造原始信息里没有的参数。
3. 强调"全新现货、多规格可选、支持挑选型号/颜色"等卖点（若适用）。

通用要求：
- 严禁使用任何 emoji 表情符号（闲鱼描述含 emoji 会发布失败）。
- tags 给 3~5 个精准关键词，便于搜索。

原始标题：{title}
原始描述：{description}
价格：{price}

只返回 JSON，不要任何解释：
{{"title":"优化后的标题","description":"优化后的描述","tags":["标签1","标签2","标签3"]}}"""

        try:
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            }
            payload = {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": "你是闲鱼全新现货卖家的文案专家。所有商品均为全新现货，绝不用「在用/闲置/二手/转手」等口吻。只返回JSON格式结果，不输出任何emoji。"},
                    {"role": "user", "content": prompt}
                ],
                "temperature": 0.7,
                "max_tokens": 1000,
            }

            url = self._normalize_url(self.api_url)

            # 请求参数 - 增加超时时间 (connect=30, read=120)
            request_kwargs = {
                "json": payload,
                "headers": headers,
                "timeout": (30, 120),  # (连接超时, 读取超时)
            }
            # 如果有证书路径，使用它
            if self._cert_path:
                request_kwargs["verify"] = self._cert_path

            resp = requests.post(url, **request_kwargs)

            if resp.status_code != 200:
                return {"success": False, "error": f"API错误 {resp.status_code}: {resp.text[:200]}"}

            data = resp.json()
            content = data["choices"][0]["message"]["content"]

            # 清理可能的markdown代码块
            content = content.strip()
            if content.startswith("```"):
                content = content.split("\n", 1)[-1] if "\n" in content else content[3:]
            if content.endswith("```"):
                content = content[:-3]
            content = content.strip()

            result = json.loads(content)
            return {
                "success": True,
                "title": result.get("title", title),
                "description": result.get("description", description),
                "tags": result.get("tags", []),
            }
        except requests.exceptions.SSLError as e:
            return {"success": False, "error": f"SSL证书错误: {e}"}
        except requests.exceptions.ConnectionError as e:
            return {"success": False, "error": f"网络连接失败: {e}"}
        except requests.exceptions.Timeout as e:
            return {"success": False, "error": f"请求超时({e})，API响应太慢，请更换中转站或稍后重试"}
        except (json.JSONDecodeError, KeyError, IndexError) as e:
            return {"success": False, "error": f"AI响应解析失败: {e}"}
        except Exception as e:
            return {"success": False, "error": str(e)}
