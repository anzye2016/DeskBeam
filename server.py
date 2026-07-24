"""DeskBeam — desktop streaming and remote control for Windows."""

import asyncio
import base64
import ctypes
import ctypes.wintypes
import io
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
import urllib.request
import wave
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

try:
    import mss
except ImportError:
    mss = None

try:
    import numpy as np
except ImportError:
    np = None

try:
    import av
except ImportError:
    av = None

try:
    from encoder import H264Encoder, has_idr
except ImportError:
    H264Encoder = None
    has_idr = None

try:
    from webrtc_streamer import WebRTCSession
except ImportError:
    WebRTCSession = None

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
    "max_fps": 15,
    "max_fps_lan": 60,
    "gop": 10,
    "gop_lan": 1,
    "streaming": True,
    "streaming": True,
    "gop": 1,
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
MAX_FPS = max(_get_int("max_fps", 15), 1)
GOP_SIZE = max(_get_int("gop", 10), 1)
AUDIT_LOG = SCRIPT_DIR / "audit.log"

_HAS_MSS = mss is not None
_HAS_AV = av is not None and np is not None and H264Encoder is not None
_STREAMING = _HAS_MSS and _HAS_AV and _cfg.get("streaming", True)

executor = ThreadPoolExecutor(max_workers=4)


def _is_lan(ip):
    return ip.startswith(("192.168.", "10.", "172.16.", "172.17.", "172.18.", "172.19.",
                          "172.20.", "172.21.", "172.22.", "172.23.", "172.24.",
                          "172.25.", "172.26.", "172.27.", "172.28.", "172.29.",
                          "172.30.", "172.31."))

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


# ── Screen capture ──
_capture_running = False
_capture_lock = threading.Lock()


def capture_screen_raw():
    """Capture screen as raw BGRA bytes with cursor drawn. Returns (bytes, w, h)."""
    global _capture_running
    with _capture_lock:
        if _capture_running:
            return None, 0, 0
        _capture_running = True
    try:
        if not _HAS_MSS:
            return None, 0, 0

        with mss.mss() as sct:
            monitor = sct.monitors[1]
            raw = sct.grab(monitor)

        bgra = np.frombuffer(raw.bgra, dtype=np.uint8).copy().reshape(raw.height, raw.width, 4)
        try:
            pt = ctypes.wintypes.POINT()
            ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
            cx, cy = pt.x, pt.y
            w, h = raw.width, raw.height
            if 0 <= cx < w and 0 <= cy < h:
                cs = 16
                x1, x2 = max(0, cx - cs), min(w - 1, cx + cs)
                y1, y2 = max(0, cy - cs), min(h - 1, cy + cs)
                for dy in (-1, 0, 1):
                    bgra[cy + dy, x1:x2 + 1] = [0, 255, 0, 255]
                for dx in (-1, 0, 1):
                    bgra[y1:y2 + 1, cx + dx] = [0, 255, 0, 255]
                bgra[cy, cx] = [255, 255, 255, 255]
        except Exception:
            pass
        return bgra.tobytes(), raw.width, raw.height
    except Exception:
        traceback.print_exc()
        return None, 0, 0
    finally:
        _capture_running = False


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
    api_url = _cfg.get("asr_api_url", "").strip()
    api_key = _cfg.get("asr_api_key", "").strip()
    if api_url and api_key:
        return _transcribe_online(wav_path, api_url, api_key)
    return _transcribe_local(wav_path)


def _transcribe_online(wav_path, url, key):
    wav_data = wav_path.read_bytes()
    b64 = base64.b64encode(wav_data).decode()
    model = _cfg.get("asr_api_model", "").strip() or "mimo-v2.5-asr"
    auth = _cfg.get("asr_api_auth", "").strip()
    if auth == "api-key":
        hdr = ("api-key", key)
    else:
        hdr = ("Authorization", f"Bearer {key}")
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": [{"type": "input_audio", "input_audio": {"data": f"data:audio/wav;base64,{b64}"}}]}],
    }).encode()
    try:
        req = urllib.request.Request(url, data=body, headers={
            hdr[0]: hdr[1].encode(),
            "Content-Type": "application/json",
        })
        resp = urllib.request.urlopen(req, timeout=60)
        data = json.loads(resp.read())
    except Exception as e:
        print(f"  ASR error: {e}")
        return ""
    path = (_cfg.get("asr_api_response_path", "") or "choices.0.message.content").split(".")
    val = data
    for k in path:
        if not k: continue
        try: val = val[int(k)] if k.isdigit() or (k[0] == '-' and k[1:].isdigit()) else val.get(k, "")
        except: val = ""
    return str(val).strip()


def _transcribe_local(wav_path):
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
    "up": lambda: keyboard.press_and_release("up"),
    "down": lambda: keyboard.press_and_release("down"),
    "left": lambda: keyboard.press_and_release("left"),
    "right": lambda: keyboard.press_and_release("right"),
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
    elif cmd == "move_to":
        ctypes.windll.user32.SetCursorPos(dx, dy)
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
        try: PID_FILE.unlink(missing_ok=True)
        except Exception: pass
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
    lan = _is_lan(ip)
    fps = max(_get_int("max_fps_lan", MAX_FPS) if lan else MAX_FPS, 1)
    gop = max(_get_int("gop_lan", GOP_SIZE) if lan else GOP_SIZE, 1)
    await websocket.send(json.dumps({"type": "hello", "streaming": _STREAMING}))
    loop = asyncio.get_running_loop()
    interval = 1.0 / fps
    running = True
    streaming = [False]
    encoder = [None]
    _webrtc = None
    voice_pcm = None

    async def _webrtc_timeout():
        await asyncio.sleep(5)
        if _webrtc and _webrtc._pc.iceConnectionState not in ("connected", "completed"):
            await _webrtc.close()
            _webrtc = None
            print("  WebRTC timeout, falling back to WebSocket")

    async def handle_command(msg):
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
        elif cmd == "mouse_click_at":
            x, y = msg.get("x", 0), msg.get("y", 0)
            await loop.run_in_executor(executor, do_mouse, "move_to", x, y)
            await loop.run_in_executor(executor, do_mouse, "click")
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

    async def screen_sender():
        """Capture, encode H.264, and send."""
        while running:
            if streaming[0] and _STREAMING and not _webrtc:
                try:
                    if encoder[0] is None:
                        raw, w, h = await loop.run_in_executor(executor, capture_screen_raw)
                        if raw:
                            encoder[0] = H264Encoder(w, h, fps=fps, gop=gop)
                            await websocket.send(json.dumps({
                                "type": "screen_config",
                                "codec": "avc1.42001F",
                                "width": w,
                                "height": h,
                            }))
                            h264 = encoder[0].encode(raw)
                            if h264:
                                await websocket.send((b"\x01" if has_idr(h264) else b"\x00") + h264)
                    else:
                        raw, _, _ = await loop.run_in_executor(executor, capture_screen_raw)
                        if raw:
                            h264 = encoder[0].encode(raw)
                            if h264:
                                await websocket.send((b"\x01" if has_idr(h264) else b"\x00") + h264)
                except websockets.exceptions.ConnectionClosed:
                    return
                except Exception:
                    traceback.print_exc()
            else:
                if encoder[0]:
                    encoder[0].close()
                    encoder[0] = None
            await asyncio.sleep(interval)

    sender_task = asyncio.create_task(screen_sender())

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

                cmd = msg.get("type", "")

                if cmd == "set_mode":
                    if not msg.get("screen", False):
                        if _webrtc:
                            await _webrtc.close()
                            _webrtc = None
                    streaming[0] = msg.get("screen", False)
                    if streaming[0] and WebRTCSession and msg.get("format") == "webrtc":
                        async def _webrtc_send(data):
                            try:
                                await websocket.send(data)
                            except Exception:
                                pass
                        async def _dc_handler(msg_str):
                            try:
                                await handle_command(json.loads(msg_str))
                            except Exception:
                                pass
                        s = WebRTCSession(_webrtc_send, _dc_handler)
                        s.add_track(capture_screen_raw, fps)
                        offer = await s.create_offer()
                        _webrtc = s
                        await websocket.send(json.dumps({
                            "type": "webrtc_offer",
                            "sdp": offer.sdp,
                            "sdp_type": offer.type,
                        }))
                        asyncio.create_task(_webrtc_timeout())
                elif cmd == "webrtc_answer":
                    if _webrtc:
                        await _webrtc.handle_answer(msg["sdp"], msg.get("sdp_type", "answer"))
                elif cmd == "webrtc_ice":
                    if _webrtc:
                        await _webrtc.add_ice(msg["candidate"])
                elif cmd == "voice_end":
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
                                t = await loop.run_in_executor(executor, _transcribe, path)
                                if t:
                                    await loop.run_in_executor(executor, keyboard.write, t)
                                else:
                                    print(f"  ASR failed, audio saved: {path}")
                            asyncio.create_task(_transcribe_full(wav_path))
                    continue
                else:
                    await handle_command(msg)
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
        try:
            await sender_task
        except asyncio.CancelledError:
            pass
        print(f"WS disconnected: {websocket.remote_address}")


# ── Main ──
async def main():
    if not _STREAMING:
        print("Streaming unavailable — running remote-only mode.")
        print("  Install for streaming: pip install av numpy mss")

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
        # Kill any process still holding port 8769 (except self)
        _sp.run(
            ["powershell", "-NoProfile", "-Command",
             f"$p=Get-NetTCPConnection -LocalPort {PORT} -ErrorAction SilentlyContinue;"
             f"if($p){{$p|Where-Object{{$_.OwningProcess -ne {_my_pid}}}|"
             f"ForEach-Object{{Stop-Process -Id $_.OwningProcess -Force}}}}"],
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
