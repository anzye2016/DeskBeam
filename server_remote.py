"""DeskBeam — desktop streaming and remote control for Windows."""

import asyncio
import ctypes
import ctypes.wintypes
import json
import os
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path



import websockets
from websockets.http11 import Response
from websockets.datastructures import Headers

import keyboard

# ── Config ──
if getattr(sys, "frozen", False):
    SCRIPT_DIR = Path(sys.executable).parent.resolve()
else:
    SCRIPT_DIR = Path(__file__).parent.resolve()
CONFIG_FILE = SCRIPT_DIR / "config.json"

_defaults = {
    "port": 8769,
    "ssl_cert": "cert.pem",
    "ssl_key": "key.pem",
    "web_dir": "web",
    "token": "",
    "max_fps": 3,
    "streaming": True,
    "gop": 1,
    "wsl_asr_script": "~/scripts/asr.py",
    "asr_health_url": "http://127.0.0.1:8082/healthz",
    "asr_cooldown": 10,
}

_cfg = {}
try:
    if CONFIG_FILE.is_file():
        _cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
except Exception as _e:
    print(f"WARNING: {CONFIG_FILE} is invalid ({_e}). Using defaults.")
for k, v in _defaults.items():
    _cfg.setdefault(k, v)


def _get_int(key, default=0):
    try:
        return int(_cfg[key])
    except (KeyError, ValueError, TypeError):
        return default


HOST = "0.0.0.0"
PORT = _get_int("port", 8769)
if getattr(sys, "frozen", False):
    _bundled = Path(sys._MEIPASS) / "deskbeam_web"
    WEB_DIR = _bundled if _bundled.is_dir() else (SCRIPT_DIR / _cfg["web_dir"]).resolve()
else:
    WEB_DIR = (SCRIPT_DIR / _cfg["web_dir"]).resolve()
SSL_CERT = SCRIPT_DIR / _cfg["ssl_cert"]
SSL_KEY = SCRIPT_DIR / _cfg["ssl_key"]
PID_FILE = SCRIPT_DIR / "server.pid"
LOG_FILE = SCRIPT_DIR / "server.log"
TEMP_DIR = Path(tempfile.gettempdir()) / "deskbeam"
try:
    TEMP_DIR.mkdir(exist_ok=True)
except Exception:
    TEMP_DIR = SCRIPT_DIR / "temp"
    TEMP_DIR.mkdir(exist_ok=True)
AUTH_TOKEN = _cfg.get("token", "").strip()
COOKIE_NAME = "deskbeam_token"

AUDIT_LOG = SCRIPT_DIR / "audit.log"

executor = ThreadPoolExecutor(max_workers=2)

# ── Auth helpers ──
_LOGIN_FAILS = {}
MAX_LOGIN_FAILS = 5
LOGIN_BLOCK_SEC = 86400
_SESSION_MAX_AGE = 86400
_LOGIN_TIME = {}
_LOGIN_HTML = ""
try:
    p = WEB_DIR / "login.html"
    if p.is_file():
        _LOGIN_HTML = p.read_text(encoding="utf-8")
except Exception:
    pass
if not _LOGIN_HTML:
    _LOGIN_HTML = '<!DOCTYPE html><meta charset=utf-8><title>Login</title><form method=get action=/login><input name=token placeholder=Token><button>Login</button></form>'


def _audit(event, ip=""):
    try:
        with open(AUDIT_LOG, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {event} {ip}\n")
    except Exception:
        pass

def _get_lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
    except Exception:
        ip = "127.0.0.1"
    finally:
        s.close()
    return ip


LAN_IP = _get_lan_ip()


def _parse_cookies(headers):
    cookies = {}
    cookie_header = headers.get("Cookie", "")
    if not cookie_header:
        return cookies
    for part in cookie_header.split(";"):
        part = part.strip()
        if "=" in part:
            k, v = part.split("=", 1)
            cookies[k.strip()] = v.strip()
    return cookies


def _check_auth(request):
    if not AUTH_TOKEN:
        return True
    cookies = _parse_cookies(request.headers)
    token = cookies.get(COOKIE_NAME)
    if token != AUTH_TOKEN:
        return False
    login_time = _LOGIN_TIME.get(token, 0)
    if time.time() - login_time > _SESSION_MAX_AGE:
        _LOGIN_TIME.pop(token, None)
        return False
    return True


def _login_allowed(ip):
    now = time.time()
    if ip in _LOGIN_FAILS:
        count, first, blocked = _LOGIN_FAILS[ip]
        if blocked and now < blocked:
            return False
        if now - first > LOGIN_BLOCK_SEC:
            del _LOGIN_FAILS[ip]
    if len(_LOGIN_FAILS) > 1000:
        _LOGIN_FAILS.clear()
    return True


def _login_fail(ip):
    now = time.time()
    if ip in _LOGIN_FAILS:
        count, first, blocked = _LOGIN_FAILS[ip]
        if blocked and now < blocked:
            return
        count += 1
    else:
        count, first = 1, now
    blocked = now + LOGIN_BLOCK_SEC if count >= MAX_LOGIN_FAILS else 0
    _LOGIN_FAILS[ip] = (count, first, blocked)


def _login_ok(ip):
    _LOGIN_FAILS.pop(ip, None)





# ── Voice ASR ──
WSL_ASR = _cfg["wsl_asr_script"]
ASR_URL = _cfg["asr_health_url"]
_asr_ready = False
_asr_last_check = 0
_asr_lock = threading.Lock()
_ASR_COOLDOWN = _get_int("asr_cooldown", 10)


def _wsl(cmd, timeout=120):
    full = ["wsl.exe"] + (cmd if isinstance(cmd, list) else cmd.split())
    try:
        r = subprocess.run(full, capture_output=True, timeout=timeout,
                           creationflags=subprocess.CREATE_NO_WINDOW)
        return r.returncode, r.stdout.decode("utf-8", errors="replace").strip(), r.stderr.decode("utf-8", errors="replace").strip()
    except subprocess.TimeoutExpired:
        return -1, "", "timeout"


def _wav_to_wsl_path(p):
    p = str(p)
    if len(p) >= 2 and p[1] == ":":
        return f"/mnt/{p[0].lower()}{p[2:].replace(chr(92), '/')}"
    return p


def _expand_wsl_path(p):
    p = str(p)
    if p.startswith("~"):
        rc, home, _ = _wsl("echo $HOME", timeout=5)
        if rc == 0 and home:
            p = home + p[1:]
    return p


def _ensure_asr():
    global _asr_ready, _asr_last_check
    now = time.time()
    if _asr_ready:
        return True
    if now - _asr_last_check < _ASR_COOLDOWN:
        return False
    with _asr_lock:
        if _asr_ready:
            return True
        _asr_last_check = now
    rc, out, _ = _wsl(f"curl -s {ASR_URL}", timeout=5)
    if "ok" in out:
        with _asr_lock:
            _asr_ready = True
        return True
    return False


def _transcribe(wav_path):
    if not _ensure_asr():
        return "[ASR not available]"

    wsl_path = _wav_to_wsl_path(wav_path)
    script = _expand_wsl_path(WSL_ASR)
    rc, out, err = _wsl(["python3", script, wsl_path], timeout=600)
    if rc != 0:
        with _asr_lock:
            _asr_ready = False
            _asr_last_check = 0
        return ""

    if err:
        print(f"  WSL stderr: {err[:200]}")

    lines = []
    for line in out.split("\n"):
        line = line.strip()
        if not line:
            continue
        if any(kw in line for kw in ("处理中", "编码", "音频:", "耗时:", "字幕", "处理音频")):
            continue
        lines.append(line)
    return " ".join(lines).strip()
_KEY_MAP = {
    "enter": lambda: keyboard.press_and_release("enter"),
    "esc": lambda: keyboard.press_and_release("esc"),
    "ctrl_c": lambda: keyboard.send("ctrl+c"),
    "ctrl_v": lambda: keyboard.send("ctrl+v"),
    "backspace": lambda: keyboard.press_and_release("backspace"),
    "ctrl_j": lambda: keyboard.send("ctrl+j"),
    "shift_enter": lambda: keyboard.send("shift+enter"),
    "tab": lambda: keyboard.press_and_release("tab"),
    "alt_tab": lambda: keyboard.send("alt+tab"),
    "win": lambda: keyboard.send("win"),
    "f5": lambda: keyboard.send("f5"),
    "ctrl_s": lambda: keyboard.send("ctrl+s"),
    "ctrl_z": lambda: keyboard.send("ctrl+z"),
    "ctrl_x": lambda: keyboard.send("ctrl+x"),
    "ctrl_a": lambda: keyboard.send("ctrl+a"),
    "ctrl_f5": lambda: keyboard.send("ctrl+f5"),
}


def do_combo(name):
    fn = _KEY_MAP.get(name)
    if fn:
        try:
            fn()
        except Exception:
            traceback.print_exc()


def _mouse(flags, dx=0, dy=0, data=0):
    try:
        ctypes.windll.user32.mouse_event(flags, dx, dy, data, 0)
    except Exception:
        traceback.print_exc()


def do_mouse(cmd, dx=0, dy=0):
    if cmd == "move":
        _mouse(0x0001, dx, dy)
    elif cmd == "click":
        _mouse(0x0002); _mouse(0x0004)
    elif cmd == "down":
        _mouse(0x0002)
    elif cmd == "up":
        _mouse(0x0004)
    elif cmd == "right":
        _mouse(0x0008); _mouse(0x0010)
    elif cmd == "middle":
        _mouse(0x0020); _mouse(0x0040)
    elif cmd == "scroll":
        delta = 120 if dy > 0 else -120
        _mouse(0x0800, data=delta)


# ── HTTP handler ──
async def http_handler(connection, request):
    path = request.path

    if path == "/ws":
        if not _check_auth(request):
            return Response(403, "Forbidden", Headers({}), b"Forbidden")
        return None

    if path.startswith("/login"):
        ip = connection.remote_address[0] if connection.remote_address else "0.0.0.0"
        token_param = ""
        if "?" in path:
            qs = path.split("?", 1)[1]
            for part in qs.split("&"):
                if part.startswith("token="):
                    token_param = part.split("=", 1)[1]
                    break
        if token_param:
            if not _login_allowed(ip):
                return Response(429, "Too Many Requests", Headers({"Content-Type": "text/html; charset=utf-8"}), b"<h1>Blocked for 24h</h1>")
            if token_param == AUTH_TOKEN:
                _login_ok(ip)
                _LOGIN_TIME[AUTH_TOKEN] = time.time()
                _audit("LOGIN OK", ip)
                cookie = f"{COOKIE_NAME}={AUTH_TOKEN}; Path=/; Max-Age={_SESSION_MAX_AGE}; HttpOnly; SameSite=Strict"
                return Response(302, "Found", Headers({"Location": "/", "Set-Cookie": cookie}), b"")
            _login_fail(ip)
            error = _LOGIN_HTML.replace("</body>", '<p style="color:#E61919;text-align:center">Invalid token</p></body>')
            return Response(200, "OK", Headers({"Content-Type": "text/html; charset=utf-8"}), error.encode("utf-8"))
        return Response(200, "OK", Headers({"Content-Type": "text/html; charset=utf-8"}), _LOGIN_HTML.encode("utf-8"))

    if path == "/logout":
        ip = connection.remote_address[0] if connection.remote_address else "0.0.0.0"
        _audit("LOGOUT", ip)
        _LOGIN_TIME.pop(AUTH_TOKEN, None)
        cookie = f"{COOKIE_NAME}=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict"
        return Response(302, "Found", Headers({"Location": "/login", "Set-Cookie": cookie}), b"")

    if path == "/shutdown":
        _audit("SHUTDOWN", connection.remote_address[0] if connection.remote_address else "")
        _server.close()
        return Response(200, "OK", Headers({"Content-Type": "text/plain"}), b"Server shutting down...")

    if AUTH_TOKEN and not _check_auth(request):
        return Response(302, "Found", Headers({"Location": "/login"}), b"")

    if path == "/" or path == "":
        path = "/index.html"
    file_path = (WEB_DIR / path.lstrip("/")).resolve()
    if file_path.is_file():
        try:
            file_path.relative_to(WEB_DIR.resolve())
        except ValueError:
            return Response(404, "Not Found", Headers({}), b"Not Found")
        suffix_map = {
            ".html": "text/html; charset=utf-8",
            ".js": "application/javascript",
            ".css": "text/css",
            ".jpg": "image/jpeg",
            ".png": "image/png",
        }
        content_type = suffix_map.get(file_path.suffix, "application/octet-stream")
        body = file_path.read_bytes()
        return Response(200, "OK", Headers({"Content-Type": content_type}), body)
    return Response(404, "Not Found", Headers({}), b"Not Found")


# ── WebSocket handler ──
async def ws_handler(websocket):
    ip = websocket.remote_address[0] if websocket.remote_address else ""
    _audit("WS CONNECT", ip)
    await websocket.send(json.dumps({"type": "hello", "streaming": False}))
    loop = asyncio.get_running_loop()

    try:
        async for message in websocket:
            if isinstance(message, bytes):
                wav_path = TEMP_DIR / f"rec_{os.getpid()}_{time.time_ns()}.wav"
                try:
                    wav_path.write_bytes(message)
                except OSError:
                    continue
                async def _transcribe_async(path):
                    t = await loop.run_in_executor(executor, _transcribe, path)
                    try:
                        path.unlink(missing_ok=True)
                    except Exception:
                        pass
                    if t:
                        await loop.run_in_executor(executor, keyboard.write, t)
                asyncio.create_task(_transcribe_async(wav_path))
                continue

            if isinstance(message, str):
                try:
                    msg = json.loads(message)
                except json.JSONDecodeError:
                    continue

                cmd = msg.get("type", "")

                if cmd == "type_text":
                    text = msg.get("text", "")
                    if text:
                        print(f"  type: {text}")
                        await loop.run_in_executor(executor, keyboard.write, text)
                elif cmd in _KEY_MAP:
                    await loop.run_in_executor(executor, do_combo, cmd)
                elif cmd == "mouse_move":
                    dx, dy = msg.get("dx", 0), msg.get("dy", 0)
                    await loop.run_in_executor(executor, do_mouse, "move", dx, dy)
                elif cmd == "mouse_click":
                    await loop.run_in_executor(executor, do_mouse, "click")
                elif cmd == "mouse_down":
                    await loop.run_in_executor(executor, do_mouse, "down")
                elif cmd == "mouse_up":
                    await loop.run_in_executor(executor, do_mouse, "up")
                elif cmd == "mouse_right":
                    await loop.run_in_executor(executor, do_mouse, "right")
                elif cmd == "mouse_middle":
                    await loop.run_in_executor(executor, do_mouse, "middle")
                elif cmd == "scroll_up":
                    await loop.run_in_executor(executor, do_mouse, "scroll", 0, 1)
                elif cmd == "scroll_down":
                    await loop.run_in_executor(executor, do_mouse, "scroll", 0, -1)
    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        _audit("WS DISCONNECT", ip)
        print(f"WS disconnected: {websocket.remote_address}")


# ── Main ──
async def main():
    print("Remote-only mode.")
    if SSL_CERT.is_file() and SSL_KEY.is_file():
        ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ssl_context.load_cert_chain(SSL_CERT, SSL_KEY)
        proto = "https"
    else:
        ssl_context = None
        proto = "http"

    if not WEB_DIR.is_dir():
        print(f"ERROR: web directory not found: {WEB_DIR}")
        print("  The web/ directory contains the browser UI files and must be present.")
        sys.exit(1)

    global _server
    _server = await websockets.serve(
        ws_handler,
        HOST,
        PORT,
        ssl=ssl_context,
        process_request=http_handler,
        ping_interval=30,
        ping_timeout=10,
    )
    print(f"Ready.  {proto}://{LAN_IP}:{PORT}")
    await _server.wait_closed()


if __name__ == "__main__":
    try:
        PID_FILE.write_text(str(os.getpid()))
    except Exception:
        pass

    # Kill old instances before starting
    try:
        import subprocess as _sp
        _my_pid = os.getpid()
        _sp.run(
            ["powershell", "-NoProfile", "-Command",
             f"Get-Process DeskBeamRemote -ErrorAction SilentlyContinue | Where-Object {{ $_.Id -ne {_my_pid} }} | Stop-Process -Force"],
            capture_output=True, creationflags=0x08000000, timeout=10,
        )
        _script_dir = str(SCRIPT_DIR).replace("\\", "\\\\")
        _sp.run(
            ["powershell", "-NoProfile", "-Command",
             f"Get-CimInstance Win32_Process -Filter \"name='pythonw.exe'\" | "
             f"Where-Object {{ $_.CommandLine -like '*{_script_dir}*' -and $_.ProcessId -ne {_my_pid} }} | "
             f"ForEach-Object {{ Stop-Process -Id $_.ProcessId -Force }}"],
            capture_output=True, creationflags=0x08000000, timeout=10,
        )
    except Exception:
        pass

    MAX_LOG = 256 * 1024
    try:
        if LOG_FILE.is_file() and LOG_FILE.stat().st_size > MAX_LOG:
            lines = LOG_FILE.read_text(encoding="utf-8").splitlines()
            LOG_FILE.write_text("\n".join(lines[-len(lines) // 2 :]) + "\n", encoding="utf-8")
    except Exception:
        pass

    try:
        log_fh = open(LOG_FILE, "a", encoding="utf-8")
        sys.stdout = sys.stderr = log_fh
    except Exception:
        log_fh = None

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    except Exception:
        traceback.print_exc()
    finally:
        try:
            PID_FILE.unlink(missing_ok=True)
        except Exception:
            pass
        if log_fh:
            log_fh.close()
