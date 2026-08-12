"""DeskBeam — desktop streaming and remote control for Windows."""

import asyncio
import ctypes
import ctypes.wintypes
import hmac
import io
import json
import os
import queue
import socket
import ssl
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
MAX_FPS = max(_get_int("max_fps", 15), 1)
GOP_SIZE = max(_get_int("gop", 10), 1)
AUDIT_LOG = SCRIPT_DIR / "audit.log"

executor = ThreadPoolExecutor(max_workers=4)


def _is_lan(ip):
    return ip.startswith(("192.168.", "10.", "172.16.", "172.17.", "172.18.", "172.19.",
                          "172.20.", "172.21.", "172.22.", "172.23.", "172.24.",
                          "172.25.", "172.26.", "172.27.", "172.28.", "172.29.",
                          "172.30.", "172.31."))


def _real_ip(headers, fallback):
    """Client IP for rate-limiting/audit/LAN detection.

    WAN clients arrive via the SSH tunnel, so the socket peer is always
    127.0.0.1. Prefer the X-Real-IP header set by nginx (which overwrites any
    client-supplied value) and fall back to the socket peer for direct LAN
    access. Trusting the header is safe because only the tunnel peer can reach
    this server. NOTE: nginx must set X-Real-IP; otherwise all WAN clients
    share one bucket and lock themselves out (or bypass it)."""
    ip = headers.get("X-Real-IP", "").strip()
    return ip if ip else fallback


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


# Mouse event sequences for the simple do_mouse() commands.  Each value is the
# MOUSEEVENTF_* flags to send in order (down+up pairs for clicks).
_MOUSE_EVENTS = {
    "click": (0x0002, 0x0004),
    "double_click": (0x0002, 0x0004, 0x0002, 0x0004),
    "down": (0x0002,),
    "up": (0x0004,),
    "right": (0x0008, 0x0010),
    "right_down": (0x0008,),
    "right_up": (0x0010,),
    "middle": (0x0020, 0x0040),
    "middle_down": (0x0020,),
    "middle_up": (0x0040,),
}


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
    if cmd in _MOUSE_EVENTS:
        for flag in _MOUSE_EVENTS[cmd]:
            mouse_event(flag)
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
        subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"Get-Process DeskBeam -ErrorAction SilentlyContinue | Where-Object {{ $_.Id -ne {_my_pid} }} | Stop-Process -Force"],
            capture_output=True, creationflags=0x08000000, timeout=10,
        )
        _script_dir = str(SCRIPT_DIR).replace("\\", "\\\\")
        subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"Get-CimInstance Win32_Process -Filter \"name='pythonw.exe'\" | "
             f"Where-Object {{ $_.CommandLine -like '*{_script_dir}*' -and $_.ProcessId -ne {_my_pid} }} | "
             f"ForEach-Object {{ Stop-Process -Id $_.ProcessId -Force }}"],
            capture_output=True, creationflags=0x08000000, timeout=10,
        )
        subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"$p=Get-NetTCPConnection -LocalPort {PORT} -ErrorAction SilentlyContinue;"
             f"if($p){{$p|Where-Object{{$_.OwningProcess -ne {_my_pid}}}|"
             f"ForEach-Object{{Stop-Process -Id $_.OwningProcess -Force}}}}"],
            capture_output=True, creationflags=0x08000000, timeout=10,
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
_GVX = 0.0
_GVY = 0.0

# Mouse-move accumulator: gyro/touchpad send up to 60-120 Hz moves; rather than
# awaiting each on the command loop (which stalls all other keys), deltas are
# accumulated here and flushed on a fixed tick by _mouse_flush_task.
_MOVE_ACC = [0.0, 0.0]
_MOVE_LOCK = threading.Lock()

_GPU_READY = _HAS_FFMPEG and GPUStreamer is not None
_STREAMING = _cfg.get("streaming", True) and (_GPU_READY or (capture is not None and capture.HAS_DXCAM and capture.HAS_AV))
_GPU_OK = [None]
_GPU_START_RETRIES = 3
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
        if token_param:
            if not _login_allowed(ip):
                return Response(429, "Too Many Requests", Headers({"Content-Type": "text/html; charset=utf-8"}), b"<h1>Blocked for 24h</h1>")
            if hmac.compare_digest(token_param, AUTH_TOKEN):
                _login_ok(ip)
                _SESSION_START = time.time()
                _audit("LOGIN OK", ip)
                cookie = f"{COOKIE_NAME}={AUTH_TOKEN}; Path=/; Max-Age={_SESSION_MAX_AGE}; HttpOnly; SameSite=Strict"
                return Response(302, "Found", Headers({"Location": "/", "Set-Cookie": cookie}), b"")
            _login_fail(ip)
            error = _LOGIN_HTML.replace("</body>", '<p style="color:#E61919;text-align:center">Invalid token</p></body>')
            return Response(200, "OK", Headers({"Content-Type": "text/html; charset=utf-8"}), error.encode("utf-8"))
        return Response(200, "OK", Headers({"Content-Type": "text/html; charset=utf-8"}), _LOGIN_HTML.encode("utf-8"))

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


# Simple mouse commands: frontend type -> do_mouse() command.  These have no
# parameters and no side effects beyond the mouse event itself.
_MOUSE_SIMPLE = {
    "mouse_click": "click",
    "mouse_double_click": "double_click",
    "mouse_down": "down",
    "mouse_up": "up",
    "mouse_right": "right",
    "mouse_right_down": "right_down",
    "mouse_right_up": "right_up",
    "mouse_middle": "middle",
    "mouse_middle_down": "middle_down",
    "mouse_middle_up": "middle_up",
}
# Scroll commands need the direction encoded in dy.
_SCROLL_MAP = {
    "scroll_up": 1,
    "scroll_down": -1,
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
            global _GYRO_ON, _GVX, _GVY
            on = bool(msg.get("on", False))
            if on:
                pt = ctypes.wintypes.POINT()
                ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
                _GVX, _GVY = float(pt.x), float(pt.y)
            _GYRO_ON = on
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
            do_mouse("click")
        elif cmd == "mouse_move_to":
            x, y = msg.get("x", 0), msg.get("y", 0)
            do_mouse("move_to", x, y)
        elif cmd in _MOUSE_SIMPLE:
            do_mouse(_MOUSE_SIMPLE[cmd])
        elif cmd in _SCROLL_MAP:
            do_mouse("scroll", 0, _SCROLL_MAP[cmd])
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
    esp_cfg = {
        "relayUrl": _cfg.get("esp_relay_url", ""),
        "token": _cfg.get("esp_token", ""),
        "device": _cfg.get("esp_device", ""),
    }
    lan = _is_lan(ip)
    await websocket.send(json.dumps({
        "type": "hello",
        "streaming": _STREAMING,
        "iceServers": [],
        "espConfig": esp_cfg,
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
                            wav_path = SCRIPT_DIR / "recording.wav"
                            try:
                                with wave.open(str(wav_path), "wb") as w:
                                    w.setnchannels(1)
                                    w.setsampwidth(2)
                                    w.setframerate(16000)
                                    w.writeframes(pcm)
                            except Exception:
                                continue
                            async def _transcribe_full(path):
                                t = await loop.run_in_executor(speech.EXECUTOR, speech.transcribe, path)
                                if t:
                                    await loop.run_in_executor(executor, type_text, t[:2000])
                                else:
                                    print(f"  ASR failed, audio saved: {path}")
                            asyncio.create_task(_transcribe_full(wav_path))
                    continue
                else:
                    await _exec_cmd(msg)
    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        _audit("WS CMD DISCONNECT", ip)
        print(f"WS cmd disconnected: {websocket.remote_address}")
        global _GYRO_ON
        _GYRO_ON = False


async def ws_handler(websocket):
    """Video channel: H.264 frames + WebRTC negotiation."""
    ip = _real_ip(websocket.request.headers, websocket.remote_address[0] if websocket.remote_address else "")
    _audit("WS CONNECT", ip)
    lan = _is_lan(ip)
    if capture is not None and capture.IS_SOFT:
        fps = _SOFT_FPS
        gop = _SOFT_FPS
    else:
        fps = _pick_int(lan, "max_fps", "max_fps_lan", MAX_FPS)
        gop = _pick_int(lan, "gop", "gop_lan", GOP_SIZE)
    cq = _pick_int(lan, "cq", "cq_lan", 26, 18)
    preset = _pick(lan, "preset", "preset_lan", "p4", "p1")
    maxrate = _pick(lan, "maxrate", "maxrate_lan", "6M", "40M")
    bufsize = _pick(lan, "bufsize", "bufsize_lan", "12M", "80M")
    ice_servers = []
    loop = asyncio.get_running_loop()
    interval = 1.0 / fps
    running = True
    streaming = [False]
    encoder = [None]
    frame_seq = [0]
    _webrtc = None
    _webrtc_timeout_task = None

    async def _webrtc_timeout():
        nonlocal _webrtc
        await asyncio.sleep(30)
        if _webrtc:
            s = _webrtc._pc.iceConnectionState
            if s not in ("connected", "completed"):
                await _webrtc.close()
                _webrtc = None
                print(f"  WebRTC timeout (state={s})")

    async def screen_sender_legacy():
        """Pipeline: capture thread -> encode thread -> sender paced by absolute schedule."""
        stop = threading.Event()
        raw_q = queue.Queue(maxsize=2)
        out_q = queue.Queue(maxsize=2)

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
                if streaming[0] and _STREAMING and not _webrtc:
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
            while not stop.is_set():
                if not (streaming[0] and _STREAMING and not _webrtc):
                    time.sleep(0.5)
                    continue
                try:
                    raw = raw_q.get(timeout=0.2)
                except queue.Empty:
                    continue
                try:
                    if encoder[0] is None:
                        h, w = raw.shape[:2]
                        if not lan and _WAN_W and cv2 is not None:
                            ew, eh = _WAN_W, _WAN_H
                        elif capture is not None and capture.IS_SOFT and cv2 is not None:
                            ew, eh = _SOFT_WIDTH, _SOFT_HEIGHT
                        else:
                            ew, eh = w, h
                        encoder[0] = H264Encoder(ew, eh, fps=fps, gop=gop, cq=cq, maxrate=maxrate, bufsize=bufsize, preset=preset)
                        while not out_q.empty():
                            _drop_oldest(out_q)
                        out_q.put(("config", ew, eh, w, h))
                    frame = raw
                    if cv2 is not None and (raw.shape[1], raw.shape[0]) != (ew, eh):
                        frame = cv2.resize(raw, (ew, eh))
                    h264 = encoder[0].encode(frame)
                    if h264:
                        if out_q.full():
                            _drop_oldest(out_q)
                        out_q.put(("data", has_idr(h264), h264))
                except Exception:
                    traceback.print_exc()
                    encoder[0] = None

        cap_thread = threading.Thread(target=capture_worker, daemon=True)
        enc_thread = threading.Thread(target=encode_worker, daemon=True)
        cap_thread.start()
        enc_thread.start()

        try:
            next_send = time.monotonic()
            while running:
                if streaming[0] and _STREAMING and not _webrtc:
                    try:
                        item = out_q.get_nowait()
                    except queue.Empty:
                        await asyncio.sleep(0.005)
                        continue
                    now = time.monotonic()
                    if next_send > now:
                        await asyncio.sleep(next_send - now)
                    if item[0] == "config":
                        await websocket.send(json.dumps({
                            "type": "screen_config",
                            "codec": "avc1.42001F",
                            "width": item[1],
                            "height": item[2],
                            "raw_width": item[3],
                            "raw_height": item[4],
                            "fps": fps,
                        }))
                    else:
                        await websocket.send(_frame_packet(item[1], frame_seq[0], item[2]))
                        frame_seq[0] += 1
                    next_send += interval
                else:
                    if encoder[0]:
                        encoder[0].close()
                        encoder[0] = None
                    next_send = time.monotonic()
                    await asyncio.sleep(0.5)
        except websockets.exceptions.ConnectionClosed:
            return
        finally:
            stop.set()

    async def screen_sender_gpu():
        """Pure-GPU pipeline: ffmpeg ddagrab (DXGI) -> NVENC. The CPU only
        reads the encoded H.264 access units from ffmpeg's stdout."""
        streamer = [None]
        config_sent = [False]
        closing = threading.Event()

        def start_streamer():
            if streamer[0] is not None:
                return True
            ew, eh = _NATIVE_W, _NATIVE_H
            cap = None
            if not lan and _cfg.get("wan_downscale", False):
                # ddagrab video_size crops a region; to show the whole desktop
                # at a lower resolution, capture at native size and downscale.
                tw = _WAN_W or _SOFT_WIDTH
                th = _WAN_H or _SOFT_HEIGHT
                ew, eh = min(tw, _NATIVE_W), min(th, _NATIVE_H)
                if ew != _NATIVE_W or eh != _NATIVE_H:
                    cap = (_NATIVE_W, _NATIVE_H)
            s = GPUStreamer(ew, eh, fps=fps, gop=gop, cq=cq, preset=preset,
                            maxrate=maxrate, bufsize=bufsize,
                            capture_w=cap[0] if cap else ew,
                            capture_h=cap[1] if cap else eh)
            # Register before first_frame: this runs on the executor thread,
            # and a disconnect while it is starting would cancel the coroutine
            # with streamer[0] still None — leaking the ffmpeg process (which
            # keeps holding DXGI). Registering early lets the finally close it.
            streamer[0] = s
            try:
                ok = s.first_frame(timeout=4.0)
            finally:
                if closing.is_set():
                    s.close()
            if ok:
                config_sent[0] = False
                return True
            s.close()
            streamer[0] = None
            return False

        async def fallback_to_legacy(reason):
            _gpu_dbg(f"fallback to legacy ({reason})")
            print(f"  GPU streamer {reason} — falling back to legacy pipeline")
            _GPU_OK[0] = False
            await screen_sender_legacy()

        try:
            while running:
                if streaming[0] and _STREAMING and not _webrtc:
                    if streamer[0] is None:
                        # DXGI access can be transiently lost (lock screen, UAC,
                        # session switch); retry a few times before giving up
                        # and falling back to the legacy (green-crosshair) path.
                        ok = False
                        for attempt in range(_GPU_START_RETRIES):
                            ok = await loop.run_in_executor(executor, start_streamer)
                            _gpu_dbg(f"start_streamer attempt {attempt + 1} -> {ok}")
                            if ok:
                                break
                            await asyncio.sleep(_GPU_START_RETRY_DELAY)
                        if not ok:
                            await fallback_to_legacy("failed to start")
                            return
                        continue
                    if not streamer[0].alive():
                        _gpu_dbg(f"streamer dead (rc={streamer[0]._proc.returncode})")
                        streamer[0].close()
                        streamer[0] = None
                        await fallback_to_legacy("died")
                        return
                    # block until the next encoded frame arrives (the ffmpeg
                    # cadence paces delivery; no artificial timing that could
                    # burst or stall on the event loop)
                    item = await loop.run_in_executor(
                        executor, lambda: streamer[0].read(0.5))
                    if item is None:
                        await asyncio.sleep(0.005)
                        continue
                    is_idr, data = item
                    if not config_sent[0]:
                        config_sent[0] = True
                        await websocket.send(json.dumps({
                            "type": "screen_config",
                            "codec": "avc1.42001F",
                            "width": streamer[0].width,
                            "height": streamer[0].height,
                            "raw_width": _NATIVE_W,
                            "raw_height": _NATIVE_H,
                            "fps": streamer[0].fps,
                        }))
                    await websocket.send(_frame_packet(is_idr, frame_seq[0], data))
                    frame_seq[0] += 1
                else:
                    if streamer[0]:
                        streamer[0].close()
                        streamer[0] = None
                    await asyncio.sleep(0.5)
        except websockets.exceptions.ConnectionClosed:
            return
        except Exception as e:
            traceback.print_exc()
            _gpu_dbg(f"gpu sender exception: {e!r}")
        finally:
            closing.set()
            if streamer[0]:
                streamer[0].close()
                streamer[0] = None

    async def screen_sender():
        # _ffmpeg_ready() re-checks the file so a just-downloaded ffmpeg is
        # picked up; _GPU_OK is reset per connection so one early GPU failure
        # does not permanently pin this process to the legacy pipeline.
        if _ffmpeg_ready() and GPUStreamer is not None and _GPU_OK[0] is not False:
            _gpu_dbg("sender: GPU path selected")
            await screen_sender_gpu()
        else:
            _gpu_dbg("sender: legacy path selected")
            await screen_sender_legacy()

    _GPU_OK[0] = None  # re-evaluate GPU path for this connection
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
                    if _webrtc:
                        await _webrtc.close()
                        _webrtc = None
                streaming[0] = msg.get("screen", False)
                if streaming[0]:
                    encoder[0] = None
                if streaming[0] and WebRTCSession and msg.get("format") == "webrtc":
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
                    s = WebRTCSession(_webrtc_send, _dc_handler, ice_servers)
                    s.add_track(capture.capture_screen_raw, fps)
                    offer = await s.create_offer()
                    _webrtc = s
                    await websocket.send(json.dumps({
                        "type": "webrtc_offer",
                        "sdp": offer.sdp,
                        "sdp_type": offer.type,
                    }))
                    _webrtc_timeout_task = asyncio.create_task(_webrtc_timeout())
            elif cmd == "webrtc_answer":
                if _webrtc:
                    await _webrtc.handle_answer(msg["sdp"], msg.get("sdp_type", "answer"))
            elif cmd == "webrtc_ice":
                if _webrtc:
                    await _webrtc.add_ice(msg["candidate"])
    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        _audit("WS DISCONNECT", ip)
        running = False
        sender_task.cancel()
        if encoder[0]:
            encoder[0].close()
            encoder[0] = None
        if _webrtc:
            asyncio.ensure_future(_webrtc.close())
        if _webrtc_timeout_task:
            _webrtc_timeout_task.cancel()
        try:
            await sender_task
        except asyncio.CancelledError:
            pass
        print(f"WS disconnected: {websocket.remote_address}")


# ── Main ──
async def main():
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
        proto = "https"
    else:
        ssl_context = None
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
