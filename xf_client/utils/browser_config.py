"""浏览器配置工具 - 自动检测并配置Chrome路径，处理端口冲突"""
import os
import sys
import subprocess
import socket


def _is_port_in_use(port: int) -> bool:
    """检查端口是否被占用"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(('127.0.0.1', port)) == 0


def _find_free_port(start: int = 9222, max_tries: int = 50) -> int:
    """从start开始找一个空闲端口"""
    for i in range(max_tries):
        port = start + i
        if not _is_port_in_use(port):
            return port
    raise RuntimeError(f"无法找到空闲端口 ({start}-{start + max_tries - 1})")


def _kill_stale_chrome(port: int = None):
    """清理残留的DrissionPage/Chrome调试进程
    
    打包后app异常退出时，Chrome进程可能残留并占用调试端口，
    导致下次启动时报 "连接被拒绝" 错误。
    """
    import signal
    
    if sys.platform == "darwin":
        # macOS: 用lsof找到占用指定端口的进程
        cmd = ["lsof", "-t", "-i", f":{port}"] if port else None
        
        if cmd:
            try:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
                pids = result.stdout.strip().split('\n')
                for pid in pids:
                    pid = pid.strip()
                    if pid and pid.isdigit():
                        try:
                            os.kill(int(pid), signal.SIGKILL)
                            print(f"[browser] 已清理残留Chrome进程 PID={pid}")
                        except (ProcessLookupError, PermissionError):
                            pass
            except Exception:
                pass

    elif sys.platform == "win32":
        # Windows: 用netstat找PID，再taskkill
        try:
            result = subprocess.run(
                ["netstat", "-ano"],
                capture_output=True, text=True, timeout=10
            )
            port_str = str(port) if port else ""
            for line in result.stdout.splitlines():
                if f"127.0.0.1:{port_str}" in line or f"localhost:{port_str}" in line:
                    parts = line.split()
                    if len(parts) >= 5:
                        pid = parts[-1]
                        if pid.isdigit():
                            subprocess.run(
                                ["taskkill", "/F", "/PID", pid],
                                capture_output=True, timeout=5
                            )
        except Exception:
            pass


def get_chrome_path() -> str:
    """自动检测Chrome浏览器路径"""
    
    if sys.platform == "darwin":  # macOS
        possible_paths = [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Chrome.app/Contents/MacOS/Chrome",
            os.path.expanduser("~/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
        ]
        try:
            result = subprocess.run(["which", "google-chrome"], capture_output=True, text=True)
            if result.returncode == 0 and result.stdout.strip():
                possible_paths.insert(0, result.stdout.strip())
        except:
            pass
            
    elif sys.platform == "win32":  # Windows
        possible_paths = [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
        ]
        try:
            result = subprocess.run(["where", "chrome"], capture_output=True, text=True)
            if result.returncode == 0 and result.stdout.strip():
                lines = result.stdout.strip().split('\n')
                if lines:
                    possible_paths.insert(0, lines[0].strip())
        except:
            pass
            
    else:  # Linux
        possible_paths = [
            "/usr/bin/google-chrome",
            "/usr/bin/google-chrome-stable",
            "/usr/bin/chromium",
            "/usr/bin/chromium-browser",
        ]
        try:
            result = subprocess.run(["which", "google-chrome"], capture_output=True, text=True)
            if result.returncode == 0 and result.stdout.strip():
                possible_paths.insert(0, result.stdout.strip())
        except:
            pass
    
    for path in possible_paths:
        if path and os.path.exists(path):
            return path
    
    return None


def get_chromium_options(user_data_dir=None, headless=False, auto_port=True):
    """获取配置好的ChromiumOptions（自动处理端口冲突）
    
    Args:
        user_data_dir: 用户数据目录，登录时需要持久化Cookie
        headless: 是否无头模式
        auto_port: 是否自动处理端口冲突（默认True）
        
    Returns:
        (ChromiumOptions, 实际使用的端口号)
    """
    from DrissionPage import ChromiumOptions
    
    co = ChromiumOptions()
    
    # 设置Chrome路径
    chrome_path = get_chrome_path()
    if chrome_path:
        co.set_paths(browser_path=chrome_path)
    
    # 设置用户数据目录
    if user_data_dir:
        co.set_user_data_path(user_data_dir)
    else:
        import tempfile
        default_dir = os.path.join(tempfile.gettempdir(), 'xf_chrome_data')
        os.makedirs(default_dir, exist_ok=True)
        co.set_user_data_path(default_dir)
    
    # 确定调试端口：自动找空闲端口
    if auto_port:
        # 先尝试清理9222端口的残留进程
        _kill_stale_chrome(9222)
        import time
        time.sleep(1)  # 等待端口释放
        
        port = _find_free_port(9222)
    else:
        port = 9222
    
    co.set_argument(f'--remote-debugging-port={port}')
    
    # 通用参数
    co.set_argument('--no-sandbox')
    co.set_argument('--disable-blink-features=AutomationControlled')
    co.set_argument('--disable-gpu')
    co.set_argument('--window-size=1920,1080')
    co.set_argument('--disable-dev-shm-usage')
    
    # 无头模式
    if headless:
        co.headless(True)
    
    return co, port


# ====== 兼容旧调用的接口 ======
# 以下函数保持向后兼容，内部调用新接口

def get_chromium_options_compat(user_data_dir=None, headless=False, port=9222):
    """兼容旧代码的接口 - 返回 (co, port) 元组"""
    return get_chromium_options(user_data_dir=user_data_dir, headless=headless, auto_port=True)


def check_browser_available() -> tuple[bool, str]:
    """检查浏览器是否可用，返回(是否可用, 错误信息)"""
    chrome_path = get_chrome_path()
    if not chrome_path:
        return False, "未找到Chrome浏览器，请安装Google Chrome"
    if not os.path.exists(chrome_path):
        return False, f"Chrome路径不存在: {chrome_path}"
    return True, chrome_path
