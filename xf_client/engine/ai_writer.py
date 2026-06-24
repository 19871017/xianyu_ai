import json
import os
import sys
import requests
from config import AI_API_URL, AI_API_KEY, AI_MODEL


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
        self._load_runtime_config()
        self.api_url = (self._runtime_url or AI_API_URL).rstrip("/")
        self.api_key = self._runtime_key or AI_API_KEY
        self.model = self._runtime_model or AI_MODEL

        if not self._is_configured():
            return {
                "success": False,
                "error": "未配置AI API，请在设置页面填写API地址、Key并选择模型"
            }

        prompt = f"""你是一个闲鱼商品文案优化专家。请根据以下原始信息，生成吸引人的闲鱼商品文案。

要求：
1. 标题：简洁有力，包含关键词，20字以内
2. 描述：详细、真实、有吸引力，突出卖点
3. 适合闲鱼平台的口语化风格
4. 不要编造参数，基于提供的信息优化

原始标题：{title}
原始描述：{description}
价格：{price}

请以JSON格式返回：
{{"title":"优化后的标题","description":"优化后的描述","tags":["标签1","标签2","标签3"]}}"""

        try:
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            }
            payload = {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": "你是闲鱼文案优化专家，只返回JSON格式结果。"},
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
