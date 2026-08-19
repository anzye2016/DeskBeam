"""DeskBeam — desktop streaming and remote control for Windows."""

import asyncio
import base64
import ctypes
import ctypes.wintypes
import hashlib
import hmac
import io
import json
import os
import queue
import socket
import ssl
import struct as _struct
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import wave
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

try:
    import cv2
except ImportError:
    cv2 = None

try:
    import capture
except ImportError:
    capture = None

try:
    import speech
except ImportError:
    speech = None

try:
    from encoder import H264Encoder, has_idr
except ImportError:
    H264Encoder = None
    has_idr = None

try:
    from webrtc_streamer import WebRTCSession
except ImportError:
    WebRTCSession = None

try:
    from gpu_stream import GPUStreamer, FFMPEG_EXISTS as _HAS_FFMPEG, native_screen_size as _native_screen_size
    from gpu_stream import ffmpeg_ready as _ffmpeg_ready, ensure_ffmpeg as _ensure_ffmpeg
except ImportError:
    GPUStreamer = None
    _HAS_FFMPEG = False
    _native_screen_size = None
    _ffmpeg_ready = lambda: False
    _ensure_ffmpeg = lambda url=None, timeout=900: False

try:
    from gpu_stream import _dbg as _gpu_dbg
except ImportError:
    def _gpu_dbg(msg):
        pass

import websockets
from websockets.http11 import Response
from websockets.datastructures import Headers

from sendinput import press, combo, type_text, mouse_event, mouse_move_to, key_down, key_up

# ── Config ──
if getattr(sys, "frozen", False):
    SCRIPT_DIR = Path(sys.executable).parent.resolve()
else:
    SCRIPT_DIR = Path(__file__).parent.resolve()
CONFIG_FILE = SCRIPT_DIR / "config.json"

DEFAULT_CONFIG = {
    "port": 8769,
    "ssl_cert": "cert.pem",
    "ssl_key": "key.pem",
    "web_dir": "web",
    "token": "",
    "max_fps": 15,
    "max_fps_lan": 60,
    "gop": 10,
    "gop_lan": 1,
    "cq": 26,
    "cq_lan": 18,
    "preset": "p4",
    "preset_lan": "p1",
    "maxrate": "6M",
    "maxrate_lan": "40M",
    "bufsize": "12M",
    "bufsize_lan": "80M",
    "wan_downscale": False,
    "streaming": True,
    "ffmpeg_url": "",
    "wsl_asr_script": "~/scripts/asr.py",
    "asr_health_url": "http://127.0.0.1:8082/healthz",
    "asr_cooldown": 10,
    "asr_api_url": "",
    "asr_api_key": "",
    "asr_api_model": "mimo-v2.5-asr",
    "asr_api_auth": "",
    "asr_api_response_path": "choices.0.message.content",
}

_cfg = {}
try:
    if CONFIG_FILE.is_file():
        _cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8-sig"))
except Exception as _e:
    print(f"WARNING: {CONFIG_FILE} is invalid ({_e}). Using defaults.")
for k, v in DEFAULT_CONFIG.items():
    _cfg.setdefault(k, v)


def _get_int(key, default=0):
    try:
        return int(_cfg[key])
    except (KeyError, ValueError, TypeError):
        return default


def _pick_int(lan, key, key_lan, default, default_lan=None):
    if lan:
        return max(_get_int(key_lan, default_lan if default_lan is not None else default), 1)
    return max(_get_int(key, default), 1)


def _pick(lan, key, key_lan, default, default_lan=None):
    if lan:
        return _cfg.get(key_lan, default_lan if default_lan is not None else default)
    return _cfg.get(key, default)


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
TOTP_SECRET = _cfg.get("totp_secret", "").strip()
MAX_FPS = max(_get_int("max_fps", 15), 1)
GOP_SIZE = max(_get_int("gop", 10), 1)
AUDIT_LOG = SCRIPT_DIR / "audit.log"

executor = ThreadPoolExecutor(max_workers=4)
# Hold strong references to fire-and-forget asyncio tasks so the GC cannot
# cancel them mid-flight (e.g. ASR transcription after voice_end).
_BACKGROUND_TASKS = set()


def _is_lan(ip):
    return ip.startswith(("192.168.", "10.", "172.16.", "172.17.", "172.18.", "172.19.",
                          "172.20.", "172.21.", "172.22.", "172.23.", "172.24.",
                          "172.25.", "172.26.", "172.27.", "172.28.", "172.29.",
                          "172.30.", "172.31."))


def _real_ip(headers, fallback):
    """Client IP for rate-limiting/audit/LAN detection.

    WAN clients arrive via the SSH tunnel, so the socket peer is always
    127.0.0.1. Only then is the X-Real-IP header (set by nginx, which
    overwrites any client-supplied value) trusted; direct connections keep
    their socket peer address, so a spoofed header cannot bypass the login
    rate limit or fake LAN status. NOTE: nginx must set X-Real-IP; otherwise
    all WAN clients share one bucket and lock themselves out."""
    ip = headers.get("X-Real-IP", "").strip()
    if ip and fallback.startswith(("127.0.0.1", "::1")):
        return ip
    return fallback


# ── Auth helpers ──
_LOGIN_FAILS = {}
MAX_LOGIN_FAILS = 5
LOGIN_BLOCK_SEC = 86400
_SESSION_MAX_AGE = 86400
_SESSION_START = 0.0
_LOGIN_HTML = ""
try:
    p = WEB_DIR / "login.html"
    if p.is_file():
        _LOGIN_HTML = p.read_text(encoding="utf-8")
except Exception:
    pass
if not _LOGIN_HTML:
    _LOGIN_HTML = '<!DOCTYPE html><meta charset=utf-8><title>Login</title><form id=f><input type=password id=t placeholder=Token><button>Login</button><p id=e></p></form><script>f.onsubmit=async e=>{e.preventDefault();let r=await fetch("/login",{headers:{"X-Auth-Token":t.value}});r.redirected&&r.url.endsWith("/")?location.href="/":e.textContent=r.status==429?"Blocked 24h":"Invalid token"}</script>'


def _audit_write(line):
    try:
        with open(AUDIT_LOG, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass


def _audit(event, ip=""):
    """Append to the audit log. File I/O runs on the executor so the event
    loop never blocks on disk (or antivirus scanning the log file)."""
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {event} {ip}\n"
    try:
        executor.submit(_audit_write, line)
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
    global _SESSION_START
    if not AUTH_TOKEN:
        return True
    cookies = _parse_cookies(request.headers)
    token = cookies.get(COOKIE_NAME)
    if not token or not hmac.compare_digest(token, AUTH_TOKEN):
        return False
    if time.time() - _SESSION_START > _SESSION_MAX_AGE:
        _SESSION_START = 0.0
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


def _pem_fingerprint(pem_path):
    """从 PEM 证书文件计算 SHA-256 指纹（hex）。用于前端 MITM 校验。"""
    try:
        with open(pem_path, "r", encoding="utf-8") as f:
            text = f.read()
        b64 = ""
        in_cert = False
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("-----BEGIN CERTIFICATE-----"):
                in_cert = True
                continue
            if line.startswith("-----END CERTIFICATE-----"):
                break
            if in_cert:
                b64 += line
        der = base64.b64decode(b64)
        return hashlib.sha256(der).hexdigest()
    except Exception:
        return ""


_CERT_FINGERPRINT = ""


# ── TOTP (RFC 6238) two-factor auth ─────────────────────────────────────────
# Pure Python, no external deps. Enabled only when config "totp_secret" is set
# (base32 secret, e.g. the kind you scan into Google/Microsoft Authenticator).


def _totp_verify(secret_b32, code, window=1):
    """Verify a 6-digit TOTP code against a base32 secret (RFC 6238, HMAC-SHA1).
    Allows ±`window` time-steps of clock drift."""
    try:
        key = base64.b32decode(secret_b32.replace(" ", "").upper())
    except Exception:
        return False
    if not code or not code.isdigit() or len(code) != 6:
        return False
    n = int(code)
    t0 = int(time.time()) // 30
    for offset in range(-window, window + 1):
        counter = t0 + offset
        msg = _struct.pack(">Q", counter)
        digest = hmac.new(key, msg, hashlib.sha1).digest()
        pos = digest[-1] & 0x0F
        value = (_struct.unpack(">I", digest[pos:pos + 4])[0] & 0x7FFFFFFF) % 1000000
        if value == n:
            return True
    return False


# ── Key / mouse mapping ──
_KEY_MAP = {
    "enter": (press, "enter"),
    "esc": (press, "esc"),
    "ctrl_c": (combo, "ctrl_c"),
    "ctrl_v": (combo, "ctrl_v"),
    "backspace": (press, "backspace"),
    "ctrl_j": (combo, "ctrl_j"),
    "shift_enter": (combo, "shift_enter"),
    "tab": (press, "tab"),
    "alt_tab": (combo, "alt_tab"),
    "win": (press, "win"),
    "f5": (press, "f5"),
    "f12": (press, "f12"),
    "ctrl_s": (combo, "ctrl_s"),
    "ctrl_z": (combo, "ctrl_z"),
    "ctrl_x": (combo, "ctrl_x"),
    "ctrl_a": (combo, "ctrl_a"),
    "ctrl_f5": (combo, "ctrl_f5"),
}


def do_combo(name):
    entry = _KEY_MAP.get(name)
    if entry:
        fn, arg = entry
        try:
            fn(arg)
        except Exception:
            traceback.print_exc()


def _mouse_flags(*flags):
    """Send a sequence of MOUSEEVENTF_* flags (down+up pairs for clicks)."""
    for flag in flags:
        mouse_event(flag)


def do_mouse(cmd, dx=0, dy=0):
    global _GVX, _GVY
    if cmd == "move":
        _GVX += dx
        _GVY += dy
        if _GYRO_ON:
            ctypes.windll.user32.SetCursorPos(int(_GVX), int(_GVY))
        else:
            mouse_event(0x0001, dx, dy)
        return
    if cmd == "move_to":
        if _GYRO_ON:
            _GVX, _GVY = float(dx), float(dy)
        mouse_move_to(dx, dy)
        return
    if cmd == "scroll":
        mouse_event(0x0800, data=120 if dy > 0 else -120)


def _mouse_accumulate(dx, dy):
    """Accumulate a move delta; flushed on the mouse tick. Never blocks."""
    if dx == 0 and dy == 0:
        return
    with _MOVE_LOCK:
        _MOVE_ACC[0] += dx
        _MOVE_ACC[1] += dy


def _mouse_flush():
    """Pop accumulated deltas and apply them. Call on the mouse tick only."""
    with _MOVE_LOCK:
        dx, dy = _MOVE_ACC[0], _MOVE_ACC[1]
        _MOVE_ACC[0] = _MOVE_ACC[1] = 0.0
    if dx or dy:
        do_mouse("move", int(dx), int(dy))


async def _mouse_flush_task():
    while True:
        await asyncio.sleep(0.008)
        try:
            _mouse_flush()
        except Exception:
            traceback.print_exc()


# ── Startup helpers ──
def _write_pid():
    try:
        PID_FILE.write_text(str(os.getpid()))
    except Exception:
        pass


def _unlink_pid():
    try:
        PID_FILE.unlink(missing_ok=True)
    except Exception:
        pass


def _kill_old_instances():
    try:
        _my_pid = os.getpid()
        _script_dir = str(SCRIPT_DIR).replace("\\", "\\\\")
        # 只杀本目录的 DeskBeam 实例（按 exe 路径匹配），避免与别处同名
        # DeskBeam.exe 误杀（例如 DeskBeam2 目录）。exe 场景下命令行里含
        # exe 完整路径，pythonw 场景下含 server.py 路径，两者都覆盖。
        # 端口兜底仅杀 DeskBeam/python 进程，不误伤碰巧占用端口的其它服务。
        subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"Get-CimInstance Win32_Process | "
             f"Where-Object {{ $_.CommandLine -like '*{_script_dir}*' -and "
             f"($_.Name -like 'DeskBeam*' -or $_.Name -in @('python.exe','pythonw.exe')) -and "
             f"$_.ProcessId -ne {_my_pid} }} | "
             f"ForEach-Object {{ Stop-Process -Id $_.ProcessId -Force }}; "
             f"$c=Get-NetTCPConnection -LocalPort {PORT} -ErrorAction SilentlyContinue; "
             f"if($c){{$c|Where-Object{{$_.OwningProcess -ne {_my_pid}}}|ForEach-Object{{"
             f"$pp=Get-Process -Id $_.OwningProcess -ErrorAction SilentlyContinue;"
             f"if($pp -and ($pp.ProcessName -like 'DeskBeam*' -or $pp.ProcessName -in @('python','pythonw')))"
             f"{{Stop-Process -Id $_.OwningProcess -Force}}}}}}"],
            capture_output=True, creationflags=0x08000000, timeout=15,
        )
    except Exception:
        pass


def _truncate_log(max_bytes=256 * 1024):
    try:
        if LOG_FILE.is_file() and LOG_FILE.stat().st_size > max_bytes:
            lines = LOG_FILE.read_text(encoding="utf-8").splitlines()
            LOG_FILE.write_text("\n".join(lines[-len(lines) // 2:]) + "\n", encoding="utf-8")
    except Exception:
        pass


def _redirect_log():
    try:
        log_fh = open(LOG_FILE, "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stderr = log_fh
        return log_fh
    except Exception:
        return None


# ── Gyro virtual cursor ──
# In gyro mode the virtual position accumulates every delta even past screen
# edges; the real cursor is SetCursorPos-clamped to the edge. Returning the
# phone to its origin brings the accumulated offset to zero, so the cursor
# returns to its start instead of losing the off-screen movement.
_GYRO_ON = False
_gyro_owner = None  # the cmd connection that last enabled gyro mode
_GVX = 0.0
_GVY = 0.0

# Mouse-move accumulator: gyro/touchpad send up to 60-120 Hz moves; rather than
# awaiting each on the command loop (which stalls all other keys), deltas are
# accumulated here and flushed on a fixed tick by _mouse_flush_task.
_MOVE_ACC = [0.0, 0.0]
_MOVE_LOCK = threading.Lock()

_GPU_READY = _HAS_FFMPEG and GPUStreamer is not None
_STREAMING = _cfg.get("streaming", True) and (_GPU_READY or (capture is not None and capture.HAS_DXCAM and capture.HAS_AV))
_GPU_START_RETRIES = 2
_GPU_START_RETRY_DELAY = 1.5

if _native_screen_size:
    _NATIVE_W, _NATIVE_H = _native_screen_size()
else:
    _NATIVE_W, _NATIVE_H = 1920, 1080

_SOFT_WIDTH = max(_get_int("soft_width", 1920), 320)
_SOFT_HEIGHT = max(_get_int("soft_height", 1080), 240)
_SOFT_FPS = max(_get_int("soft_fps", 15), 5)
_WAN_W = _get_int("wan_width", 0)
_WAN_H = _get_int("wan_height", 0)


# ── HTTP handler ──
async def http_handler(connection, request):
    global _SESSION_START
    path = request.path

    if path == "/fingerprint":
        # 返回自身证书指纹（SHA-256 hex）。前端登录后记录，每次访问校验；
        # MITM 伪服务器返回它自己的指纹，与记录的指纹比对即可识别劫持。
        return Response(200, "OK", Headers({"Content-Type": "text/plain; charset=utf-8"}), _CERT_FINGERPRINT.encode("utf-8"))

    if path == "/cert":
        # 下载自身证书供手机等设备安装为受信 CA（application/x-x509-ca-cert
        # 让 Android 直接弹出证书安装），免去每次访问的证书警告。
        if not SSL_CERT.is_file():
            return Response(404, "Not Found", Headers({}), b"Not Found")
        return Response(200, "OK", Headers({
            "Content-Type": "application/x-x509-ca-cert",
            "Content-Disposition": 'attachment; filename="cert.crt"',
            "Cache-Control": "no-store",
        }), SSL_CERT.read_bytes())

    if path == "/ws":
        if not _check_auth(request):
            return Response(403, "Forbidden", Headers({}), b"Forbidden")
        return None

    if path == "/ws_cmd":
        if not _check_auth(request):
            return Response(403, "Forbidden", Headers({}), b"Forbidden")
        connection.is_cmd = True
        return None

    if path.startswith("/login"):
        ip = _real_ip(request.headers, connection.remote_address[0] if connection.remote_address else "0.0.0.0")
        token_param = request.headers.get("X-Auth-Token", "")
        totp_code = request.headers.get("X-Totp-Code", "").strip()
        if token_param:
            if not _login_allowed(ip):
                return Response(429, "Too Many Requests", Headers({"Content-Type": "text/html; charset=utf-8"}), b"<h1>Blocked for 24h</h1>")
            if not hmac.compare_digest(token_param, AUTH_TOKEN):
                _login_fail(ip)
                error = _LOGIN_HTML.replace("</body>", '<p style="color:#E61919;text-align:center">Invalid token</p></body>')
                return Response(200, "OK", Headers({"Content-Type": "text/html; charset=utf-8"}), error.encode("utf-8"))
            # Token OK. If TOTP is enabled, require the 6-digit code too.
            if TOTP_SECRET:
                if not totp_code:
                    _audit("LOGIN TOTP NEEDED", ip)
                    return Response(426, "Upgrade Required", Headers({"Content-Type": "text/html; charset=utf-8"}), b"totp_required")
                if not _totp_verify(TOTP_SECRET, totp_code):
                    _login_fail(ip)
                    _audit("LOGIN TOTP FAIL", ip)
                    error = _LOGIN_HTML.replace("</body>", '<p style="color:#E61919;text-align:center">Invalid code</p></body>')
                    return Response(200, "OK", Headers({"Content-Type": "text/html; charset=utf-8"}), error.encode("utf-8"))
            _login_ok(ip)
            _SESSION_START = time.time()
            _audit("LOGIN OK", ip)
            cookie = f"{COOKIE_NAME}={AUTH_TOKEN}; Path=/; Max-Age={_SESSION_MAX_AGE}; HttpOnly; SameSite=Strict"
            return Response(302, "Found", Headers({"Location": "/", "Set-Cookie": cookie}), b"")
        return Response(200, "OK", Headers({"Content-Type": "text/html; charset=utf-8"}), _LOGIN_HTML.replace("__TOTP__", "1" if TOTP_SECRET else "0").encode("utf-8"))

    if path == "/logout":
        ip = _real_ip(request.headers, connection.remote_address[0] if connection.remote_address else "0.0.0.0")
        _audit("LOGOUT", ip)
        _SESSION_START = 0.0
        cookie = f"{COOKIE_NAME}=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict"
        return Response(302, "Found", Headers({"Location": "/login", "Set-Cookie": cookie}), b"")

    if path == "/shutdown":
        if not _check_auth(request):
            return Response(403, "Forbidden", Headers({}), b"Forbidden")
        _audit("SHUTDOWN", _real_ip(request.headers, connection.remote_address[0] if connection.remote_address else ""))
        try:
            PID_FILE.unlink(missing_ok=True)
        except Exception:
            pass
        _server.close()
        return Response(200, "OK", Headers({"Content-Type": "text/plain"}), b"Server shutting down...")

    if AUTH_TOKEN and not _check_auth(request):
        return Response(302, "Found", Headers({"Location": "/login"}), b"")

    if path == "/" or path == "":
        path = "/index.html"
    # Path traversal guard: relative_to() rejects any real "../" escape.
    # URL-encoded forms (..%2f, %2e%2e) are NOT a bypass because websockets
    # does not URL-decode request.path (verified in its docs), so "%2f" is
    # treated as a literal filename character, not a path separator.
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
        headers = Headers({
            "Content-Type": content_type,
            "Cache-Control": "no-cache",
        })
        return Response(200, "OK", headers, body)
    return Response(404, "Not Found", Headers({}), b"Not Found")


# ── WebSocket handlers ──
def _frame_packet(is_idr, seq, data):
    """Wrap one encoded frame as: 1 byte IDR flag + 4 byte monotonic frame
    sequence (big-endian) + H.264 data. The client uses the sequence number
    with its own local clock to measure end-to-end latency accumulation
    (comparing two machines' clocks directly would drift over time)."""
    return (b"\x01" if is_idr else b"\x00") + (seq & 0xFFFFFFFF).to_bytes(4, "big") + data


# Simple mouse commands from the frontend -> action. Most map to a fixed
# MOUSEEVENTF_* flag sequence; scroll entries carry the direction in dy.
_MOUSE_CMDS = {
    "mouse_click": lambda: _mouse_flags(0x0002, 0x0004),
    "mouse_double_click": lambda: _mouse_flags(0x0002, 0x0004, 0x0002, 0x0004),
    "mouse_down": lambda: _mouse_flags(0x0002),
    "mouse_up": lambda: _mouse_flags(0x0004),
    "mouse_right": lambda: _mouse_flags(0x0008, 0x0010),
    "mouse_right_down": lambda: _mouse_flags(0x0008),
    "mouse_right_up": lambda: _mouse_flags(0x0010),
    "mouse_middle": lambda: _mouse_flags(0x0020, 0x0040),
    "mouse_middle_down": lambda: _mouse_flags(0x0020),
    "mouse_middle_up": lambda: _mouse_flags(0x0040),
    "scroll_up": lambda: do_mouse("scroll", 0, 1),
    "scroll_down": lambda: do_mouse("scroll", 0, -1),
}


async def _exec_cmd(msg):
    cmd = msg.get("type", "")
    try:
        if cmd == "type_text":
            text = msg.get("text", "")[:2000]
            if text:
                print(f"  type: {text}")
                await asyncio.get_running_loop().run_in_executor(executor, type_text, text)
        elif cmd in _KEY_MAP:
            do_combo(cmd)
        elif cmd == "key_press":
            k = msg.get("key", "")
            if k:
                press(k)
        elif cmd == "key_down":
            k = msg.get("key", "")
            if k:
                key_down(k)
        elif cmd == "key_up":
            k = msg.get("key", "")
            if k:
                key_up(k)
        elif cmd == "set_gyro":
            global _GYRO_ON, _GVX, _GVY, _gyro_owner
            on = bool(msg.get("on", False))
            if on:
                pt = ctypes.wintypes.POINT()
                ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
                _GVX, _GVY = float(pt.x), float(pt.y)
            _GYRO_ON = on
            _gyro_owner = websocket if on else None
        elif cmd == "gyro_calib":
            w = ctypes.windll.user32.GetSystemMetrics(0)
            h = ctypes.windll.user32.GetSystemMetrics(1)
            cx, cy = w // 2, h // 2
            if _GYRO_ON:
                _GVX, _GVY = float(cx), float(cy)
            ctypes.windll.user32.SetCursorPos(cx, cy)
        elif cmd == "mouse_move":
            dx, dy = msg.get("dx", 0), msg.get("dy", 0)
            _mouse_accumulate(dx, dy)
        elif cmd == "mouse_click_at":
            x, y = msg.get("x", 0), msg.get("y", 0)
            do_mouse("move_to", x, y)
            _mouse_flags(0x0002, 0x0004)
        elif cmd == "mouse_move_to":
            x, y = msg.get("x", 0), msg.get("y", 0)
            do_mouse("move_to", x, y)
        elif cmd in _MOUSE_CMDS:
            _MOUSE_CMDS[cmd]()
    except Exception:
        traceback.print_exc()



async def ws_dispatch(websocket):
    if getattr(websocket, "is_cmd", False):
        await ws_cmd_handler(websocket)
    else:
        await ws_handler(websocket)


async def ws_cmd_handler(websocket):
    """Command channel: control commands + voice. Separated from video so
    control messages are never queued behind video frames."""
    ip = _real_ip(websocket.request.headers, websocket.remote_address[0] if websocket.remote_address else "")
    _audit("WS CMD CONNECT", ip)
    lan = _is_lan(ip)
    await websocket.send(json.dumps({
        "type": "hello",
        "streaming": _STREAMING,
        "iceServers": [],
        "lan": lan,
    }))
    loop = asyncio.get_running_loop()
    voice_pcm = None

    try:
        async for message in websocket:
            if isinstance(message, bytes):
                if len(message) > 44:
                    if voice_pcm is None:
                        voice_pcm = io.BytesIO()
                    voice_pcm.write(message[44:])
                continue

            if isinstance(message, str):
                try:
                    msg = json.loads(message)
                except json.JSONDecodeError:
                    continue

                if msg.get("type") == "voice_end":
                    if voice_pcm:
                        pcm = voice_pcm.getvalue()
                        voice_pcm = None
                        if pcm:
                            # Unique file per recording (concurrent sessions no
                            # longer clobber each other; exe dir stays read-only
                            # safe). Removed after successful transcription.
                            wav_path = None
                            try:
                                fd, name = tempfile.mkstemp(suffix=".wav", dir=TEMP_DIR)
                                wav_path = Path(name)
                                with os.fdopen(fd, "wb") as fh:
                                    with wave.open(fh, "wb") as w:
                                        w.setnchannels(1)
                                        w.setsampwidth(2)
                                        w.setframerate(16000)
                                        w.writeframes(pcm)
                            except Exception:
                                wav_path = None
                            if wav_path is None:
                                continue
                            async def _transcribe_full(path):
                                done = False
                                try:
                                    t = await loop.run_in_executor(speech.EXECUTOR, speech.transcribe, path)
                                    if t:
                                        await loop.run_in_executor(executor, type_text, t[:2000])
                                        done = True
                                    else:
                                        print(f"  ASR failed, audio saved: {path}")
                                finally:
                                    if done:
                                        try:
                                            path.unlink(missing_ok=True)
                                        except Exception:
                                            pass
                            _task = asyncio.create_task(_transcribe_full(wav_path))
                            _BACKGROUND_TASKS.add(_task)
                            _task.add_done_callback(_BACKGROUND_TASKS.discard)
                    continue
                else:
                    await _exec_cmd(msg)
    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        _audit("WS CMD DISCONNECT", ip)
        print(f"WS cmd disconnected: {websocket.remote_address}")
        # Only the connection that owns gyro mode resets it; a second
        # client disconnecting must not kill the active gyro session.
        global _GYRO_ON, _gyro_owner
        if _gyro_owner is websocket:
            _GYRO_ON = False
            _gyro_owner = None


def _scale_rate(s, factor):
    """Scale a rate string like '40M' by a numeric factor, rounding to int."""
    s = str(s).strip().upper()
    if s.endswith('M'):
        return str(max(1, int(int(s[:-1]) * factor))) + "M"
    if s.endswith('K'):
        return str(max(1, int(int(s[:-1]) * factor))) + "K"
    return str(max(1, int(int(s) * factor)))


class _VideoSession:
    """Per-connection video-channel state shared by the sender pipelines."""

    def __init__(self, websocket, lan, fps, gop, cq, preset, maxrate, bufsize, ice_servers):
        self.ws = websocket
        self.loop = asyncio.get_running_loop()
        self.lan = lan
        self.fps = fps
        self.gop = gop
        self.cq = cq
        self.preset = preset
        self.maxrate = maxrate
        self.bufsize = bufsize
        self.ice_servers = ice_servers
        self.running = True
        self.streaming = False
        self.webrtc = None
        self.frame_seq = 0
        # Dynamic bitrate: tier 0 = full speed, 1 = congested, 2 = severe.
        # Sender loops watch _bitrate_restart and restart the encoder when set.
        self._bitrate_tier = 0
        self._bitrate_restart = asyncio.Event()
        self._base_cq = cq
        self._base_maxrate = maxrate
        self._base_bufsize = bufsize

    def effective_params(self):
        """Return (cq, maxrate, bufsize) scaled for the current bitrate tier."""
        cq = self._base_cq
        maxrate = self._base_maxrate
        bufsize = self._base_bufsize
        if self._bitrate_tier == 1:
            maxrate = _scale_rate(maxrate, 0.6)
            bufsize = _scale_rate(bufsize, 0.6)
            cq = min(cq + 3, 51)
        elif self._bitrate_tier == 2:
            maxrate = _scale_rate(maxrate, 0.3)
            bufsize = _scale_rate(bufsize, 0.3)
            cq = min(cq + 6, 51)
        return cq, maxrate, bufsize

    async def send_config(self, width, height, raw_w, raw_h, fps, enc=""):
        await self.ws.send(json.dumps({
            "type": "screen_config",
            "codec": "avc1.42001F",
            "width": width,
            "height": height,
            "raw_width": raw_w,
            "raw_height": raw_h,
            "fps": fps,
            "enc": enc,
        }))


def _soft_clamp(fps, gop):
    """Clamp fps/gop for CPU-bound software pipelines (PyAV soft encode,
    WebRTC software track). Identity when a hardware encoder is available.
    The ffmpeg GPU pipeline is never clamped here — capture.IS_SOFT only
    reflects PyAV's capability, not ffmpeg's, and GPUStreamer downscales
    itself when it ends up on libx264."""
    if capture is not None and capture.IS_SOFT:
        fps = min(fps, _SOFT_FPS)
        gop = min(gop, fps)
    return fps, gop


async def _webrtc_timeout(sess):
    await asyncio.sleep(30)
    if sess.webrtc:
        state = sess.webrtc._pc.iceConnectionState
        if state not in ("connected", "completed"):
            await sess.webrtc.close()
            sess.webrtc = None
            print(f"  WebRTC timeout (state={state})")


async def _legacy_sender(sess):
    """Pipeline: capture thread -> encode thread -> sender paced by absolute schedule."""
    fps, gop = _soft_clamp(sess.fps, sess.gop)
    interval = 1.0 / fps
    lan = sess.lan
    stop = threading.Event()
    raw_q = queue.Queue(maxsize=2)
    out_q = queue.Queue(maxsize=2)
    encoder = None

    def _drop_oldest(q):
        try:
            q.get_nowait()
        except queue.Empty:
            pass

    def capture_worker():
        cap_dt = interval * 0.95
        last_cap = 0.0
        last_pos = None
        while not stop.is_set():
            if sess.streaming and _STREAMING and not sess.webrtc:
                try:
                    _pt = ctypes.wintypes.POINT()
                    ctypes.windll.user32.GetCursorPos(ctypes.byref(_pt))
                    _pos = (_pt.x, _pt.y)
                    if _pos != last_pos:
                        last_pos = _pos
                        cap_dt = interval * 0.95
                    else:
                        cap_dt = 0.05
                except Exception:
                    cap_dt = interval * 0.95
                now = time.monotonic()
                if now < last_cap:
                    time.sleep(last_cap - now)
                    continue
                try:
                    raw, _, _ = capture.capture_screen_raw()
                    if raw is not None:
                        if raw_q.full():
                            _drop_oldest(raw_q)
                        raw_q.put(raw)
                except Exception:
                    pass
                last_cap = time.monotonic() + cap_dt
            else:
                time.sleep(0.5)

    def encode_worker():
        nonlocal encoder
        while not stop.is_set():
            if not (sess.streaming and _STREAMING and not sess.webrtc):
                time.sleep(0.5)
                continue
            try:
                raw = raw_q.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                if encoder is None:
                    h, w = raw.shape[:2]
                    if not lan and _WAN_W and cv2 is not None:
                        ew, eh = _WAN_W, _WAN_H
                    elif capture is not None and capture.IS_SOFT and cv2 is not None:
                        ew, eh = _SOFT_WIDTH, _SOFT_HEIGHT
                    else:
                        ew, eh = w, h
                    cq, maxrate, bufsize = sess.effective_params()
                    encoder = H264Encoder(ew, eh, fps=fps, gop=gop, cq=cq,
                                          maxrate=maxrate, bufsize=bufsize,
                                          preset=sess.preset)
                    while not out_q.empty():
                        _drop_oldest(out_q)
                    out_q.put(("config", ew, eh, w, h, encoder.enc_name))
                frame = raw
                if cv2 is not None and (raw.shape[1], raw.shape[0]) != (ew, eh):
                    frame = cv2.resize(raw, (ew, eh))
                h264 = encoder.encode(frame)
                if h264:
                    if out_q.full():
                        _drop_oldest(out_q)
                    out_q.put(("data", has_idr(h264), h264))
            except Exception:
                traceback.print_exc()
                if encoder is not None:
                    try:
                        encoder.close()
                    except Exception:
                        pass
                    encoder = None

    threading.Thread(target=capture_worker, daemon=True).start()
    threading.Thread(target=encode_worker, daemon=True).start()

    try:
        next_send = time.monotonic()
        while sess.running:
            if sess.streaming and _STREAMING and not sess.webrtc:
                # Bitrate tier change: restart encoder with adjusted params
                if sess._bitrate_restart.is_set():
                    sess._bitrate_restart.clear()
                    if encoder is not None:
                        encoder.close()
                        encoder = None
                    continue
                try:
                    item = out_q.get_nowait()
                except queue.Empty:
                    await asyncio.sleep(0.005)
                    continue
                now = time.monotonic()
                if next_send > now:
                    await asyncio.sleep(next_send - now)
                if item[0] == "config":
                    await sess.send_config(item[1], item[2], item[3], item[4], fps, item[5])
                else:
                    await sess.ws.send(_frame_packet(item[1], sess.frame_seq, item[2]))
                    sess.frame_seq += 1
                next_send += interval
            else:
                if encoder is not None:
                    encoder.close()
                    encoder = None
                next_send = time.monotonic()
                await asyncio.sleep(0.5)
    except websockets.exceptions.ConnectionClosed:
        return
    finally:
        stop.set()
        if encoder is not None:
            try:
                encoder.close()
            except Exception:
                pass


async def _gpu_sender(sess):
    """Pure-GPU pipeline: ffmpeg ddagrab (DXGI) -> NVENC. The CPU only
    reads the encoded H.264 access units from ffmpeg's stdout."""
    streamer = None
    config_sent = False
    closing = threading.Event()

    def start_streamer():
        nonlocal streamer, config_sent
        if streamer is not None:
            return True
        ew, eh = _NATIVE_W, _NATIVE_H
        cap = None
        if not sess.lan and _cfg.get("wan_downscale", False):
            # ddagrab video_size crops a region; to show the whole desktop
            # at a lower resolution, capture at native size and downscale.
            tw = _WAN_W or _SOFT_WIDTH
            th = _WAN_H or _SOFT_HEIGHT
            ew, eh = min(tw, _NATIVE_W), min(th, _NATIVE_H)
            if ew != _NATIVE_W or eh != _NATIVE_H:
                cap = (_NATIVE_W, _NATIVE_H)
        cq, maxrate, bufsize = sess.effective_params()
        s = GPUStreamer(ew, eh, fps=sess.fps, gop=sess.gop, cq=cq,
                        preset=sess.preset, maxrate=maxrate, bufsize=bufsize,
                        capture_w=cap[0] if cap else ew,
                        capture_h=cap[1] if cap else eh)
        # Register before first_frame: this runs on the executor thread,
        # and a disconnect while it is starting would cancel the coroutine
        # with streamer still None — leaking the ffmpeg process (which
        # keeps holding DXGI). Registering early lets the finally close it.
        streamer = s
        ok = False
        try:
            ok = s.first_frame(timeout=2.0)
        finally:
            if closing.is_set():
                s.close()
        if ok:
            config_sent = False
            return True
        s.close()
        streamer = None
        return False

    try:
        while sess.running:
            if sess.streaming and _STREAMING and not sess.webrtc:
                # Bitrate tier change: restart encoder with adjusted params
                if sess._bitrate_restart.is_set():
                    sess._bitrate_restart.clear()
                    if streamer:
                        _gpu_dbg(f"bitrate adapt: tier {sess._bitrate_tier}, restarting")
                        streamer.close()
                        streamer = None
                    continue
                if streamer is None:
                    # DXGI access can be transiently lost (lock screen, UAC,
                    # session switch); retry a few times before giving up
                    # and falling back to the legacy (green-crosshair) path.
                    ok = False
                    for attempt in range(_GPU_START_RETRIES):
                        ok = await sess.loop.run_in_executor(executor, start_streamer)
                        _gpu_dbg(f"start_streamer attempt {attempt + 1} -> {ok}")
                        if ok:
                            break
                        await asyncio.sleep(_GPU_START_RETRY_DELAY)
                    if not ok:
                        await _fallback_to_legacy(sess, "failed to start")
                        return
                    continue
                if not streamer.alive():
                    _gpu_dbg(f"streamer dead (rc={streamer._proc.returncode})")
                    streamer.close()
                    streamer = None
                    await _fallback_to_legacy(sess, "died")
                    return
                # block until the next encoded frame arrives (the ffmpeg
                # cadence paces delivery; no artificial timing that could
                # burst or stall on the event loop)
                item = await sess.loop.run_in_executor(
                    executor, lambda: streamer.read(0.5))
                if item is None:
                    await asyncio.sleep(0.005)
                    continue
                is_idr, data = item
                if not config_sent:
                    config_sent = True
                    await sess.send_config(streamer.width, streamer.height,
                                           _NATIVE_W, _NATIVE_H, streamer.fps,
                                           streamer._encoder)
                await sess.ws.send(_frame_packet(is_idr, sess.frame_seq, data))
                sess.frame_seq += 1
            else:
                if streamer:
                    streamer.close()
                    streamer = None
                await asyncio.sleep(0.5)
    except websockets.exceptions.ConnectionClosed:
        return
    except Exception as e:
        traceback.print_exc()
        _gpu_dbg(f"gpu sender exception: {e!r}")
    finally:
        closing.set()
        if streamer:
            streamer.close()
            streamer = None


async def _fallback_to_legacy(sess, reason):
    # GPU capture lost (UAC / lock screen / session switch).  The old dxcam
    # legacy pipeline is native-crash-prone here (BEX64 / 0xc0000409 on some
    # GPU configs — Q470) and cannot capture the secure desktop anyway, so we
    # do NOT switch to it.  Instead this connection simply stops streaming:
    # the client's frame-stall watchdog reconnects and retries the GPU path
    # until capture becomes available again.
    _gpu_dbg(f"fallback to legacy ({reason})")
    print(f"  GPU streamer {reason} — pausing stream; client will reconnect")
    await asyncio.sleep(1)


async def ws_handler(websocket):
    """Video channel: H.264 frames + WebRTC negotiation."""
    ip = _real_ip(websocket.request.headers, websocket.remote_address[0] if websocket.remote_address else "")
    _audit("WS CONNECT", ip)
    lan = _is_lan(ip)
    sess = _VideoSession(
        websocket, lan,
        _pick_int(lan, "max_fps", "max_fps_lan", MAX_FPS),
        _pick_int(lan, "gop", "gop_lan", GOP_SIZE),
        _pick_int(lan, "cq", "cq_lan", 26, 18),
        _pick(lan, "preset", "preset_lan", "p4", "p1"),
        _pick(lan, "maxrate", "maxrate_lan", "6M", "40M"),
        _pick(lan, "bufsize", "bufsize_lan", "6M", "40M"),
        [],
    )
    _webrtc_timeout_task = None

    # _ffmpeg_ready() re-checks the file so a just-downloaded ffmpeg is
    # picked up. Every connection re-evaluates the GPU path: when ffmpeg is
    # present we always try it (a transient UAC/lock loss falls back to
    # pausing, never to the crash-prone dxcam pipeline); when ffmpeg is
    # absent the pure-soft legacy pipeline runs from the start.
    async def screen_sender():
        if _ffmpeg_ready() and GPUStreamer is not None:
            _gpu_dbg("sender: GPU path selected")
            await _gpu_sender(sess)
        else:
            _gpu_dbg("sender: legacy path selected")
            await _legacy_sender(sess)

    sender_task = asyncio.create_task(screen_sender())

    try:
        async for message in websocket:
            if not isinstance(message, str):
                continue
            try:
                msg = json.loads(message)
            except json.JSONDecodeError:
                continue

            cmd = msg.get("type", "")

            if cmd == "set_mode":
                if not msg.get("screen", False):
                    if sess.webrtc:
                        await sess.webrtc.close()
                        sess.webrtc = None
                sess.streaming = msg.get("screen", False)
                if sess.streaming and WebRTCSession and msg.get("format") == "webrtc":
                    async def _webrtc_send(data):
                        try:
                            await websocket.send(data)
                        except Exception:
                            pass
                    async def _dc_handler(msg_str):
                        try:
                            await _exec_cmd(json.loads(msg_str))
                        except Exception:
                            pass
                    s = WebRTCSession(_webrtc_send, _dc_handler, sess.ice_servers)
                    track_fps, _ = _soft_clamp(sess.fps, sess.gop)
                    s.add_track(capture.capture_screen_raw, track_fps)
                    offer = await s.create_offer()
                    sess.webrtc = s
                    await websocket.send(json.dumps({
                        "type": "webrtc_offer",
                        "sdp": offer.sdp,
                        "sdp_type": offer.type,
                    }))
                    _webrtc_timeout_task = asyncio.create_task(_webrtc_timeout(sess))
            elif cmd == "webrtc_answer":
                if sess.webrtc:
                    await sess.webrtc.handle_answer(msg["sdp"], msg.get("sdp_type", "answer"))
            elif cmd == "webrtc_ice":
                if sess.webrtc:
                    await sess.webrtc.add_ice(msg["candidate"])
            elif cmd == "bitrate_adapt":
                tier = max(0, min(2, int(msg.get("tier", 0))))
                if tier != sess._bitrate_tier:
                    print(f"  bitrate adapt: tier {sess._bitrate_tier} -> {tier}")
                    sess._bitrate_tier = tier
                    sess._bitrate_restart.set()
    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        _audit("WS DISCONNECT", ip)
        sess.running = False
        sender_task.cancel()
        if sess.webrtc:
            asyncio.ensure_future(sess.webrtc.close())
        if _webrtc_timeout_task:
            _webrtc_timeout_task.cancel()
        try:
            await sender_task
        except asyncio.CancelledError:
            pass
        print(f"WS disconnected: {websocket.remote_address}")


# ── Main ──
async def main():
    global _CERT_FINGERPRINT
    if sys.platform == "win32":
        try:
            ctypes.windll.winmm.timeBeginPeriod(1)
            ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x00008000)
        except Exception:
            pass

    if speech is not None:
        speech.init(_cfg)

    if not _STREAMING:
        print("Streaming unavailable — running remote-only mode.")
        print("  Install for streaming: pip install av numpy dxcam")

    if SSL_CERT.is_file() and SSL_KEY.is_file():
        ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ssl_context.load_cert_chain(SSL_CERT, SSL_KEY)
        # 自身证书指纹：前端登录后记录，每次访问校验。若连接到的服务器证书
        # 指纹与记录的指纹不同，说明可能被中间人（ARP 欺骗 + 伪证书）劫持。
        # 直接从 PEM 文件解析（不依赖 get_certificate 的版本差异）。
        _CERT_FINGERPRINT = _pem_fingerprint(SSL_CERT)
        proto = "https"
    else:
        ssl_context = None
        _CERT_FINGERPRINT = ""
        proto = "http"

    if not WEB_DIR.is_dir():
        print(f"ERROR: web directory not found: {WEB_DIR}")
        print("  The web/ directory contains the browser UI files and must be present.")
        sys.exit(1)

    asyncio.create_task(_mouse_flush_task())

    # Auto-download ffmpeg in the background if it is missing and a URL is
    # configured (config "ffmpeg_url").  The server starts immediately; the
    # GPU pipeline is selected dynamically once ffmpeg is available.
    _ffmpeg_url = str(_cfg.get("ffmpeg_url", "") or "").strip()
    if _STREAMING and _ffmpeg_url and not _ffmpeg_ready():
        threading.Thread(target=lambda: _ensure_ffmpeg(_ffmpeg_url), daemon=True).start()

    global _server
    _server = await websockets.serve(
        ws_dispatch,
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
    _write_pid()
    _kill_old_instances()
    _truncate_log()
    log_fh = _redirect_log()
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    except Exception:
        traceback.print_exc()
    finally:
        _unlink_pid()
        if log_fh:
            log_fh.close()
