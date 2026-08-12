"""Pure-GPU screen capture + H.264 encoding via the bundled ffmpeg.

Pipeline: ddagrab (DXGI Desktop Duplication, D3D11) -> h264_nvenc (NVENC).
Everything stays on the GPU; the CPU only reads the encoded H.264 bitstream
from ffmpeg's stdout. This is the same architecture as Sunshine.

Requires the bundled ffmpeg build (ffmpeg/ffmpeg.exe) with ddagrab + NVENC.
"""

import ctypes
import ctypes.wintypes
import os
import queue
import re
import subprocess
import sys
import threading
import time
import urllib.request

# 编码器启动失败的错误关键字（ffmpeg stderr）。探测阶段抓到即判失败，
# 不依赖进程退出——有些失败（挂起不产帧）进程不退，但 stderr 会报错。
_ENC_ERROR_RE = re.compile(
    br"cannot load|unknown encoder|error while opening|could not open|"
    br"not support|impossible to convert|error reinitializing|"
    br"failed to configure|invalid argument|no such file",
    re.IGNORECASE,
)

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if getattr(sys, "frozen", False):
    # PyInstaller onefile: ffmpeg may be bundled inside _MEIPASS, or kept
    # as an external ffmpeg/ folder next to the exe (smaller, faster start).
    _FFMPEG_CANDIDATES = [
        os.path.join(getattr(sys, "_MEIPASS", _SCRIPT_DIR), "ffmpeg", "ffmpeg.exe"),
        os.path.join(os.path.dirname(sys.executable), "ffmpeg", "ffmpeg.exe"),
    ]
else:
    _FFMPEG_CANDIDATES = [os.path.join(_SCRIPT_DIR, "ffmpeg", "ffmpeg.exe")]
FFMPEG_EXE = next((p for p in _FFMPEG_CANDIDATES if os.path.isfile(p)), _FFMPEG_CANDIDATES[0])
FFMPEG_EXISTS = os.path.isfile(FFMPEG_EXE)

_DEBUG_FILE = os.path.join(os.environ.get("TEMP", _SCRIPT_DIR), "gpu_stream_debug.txt")


def _dbg(msg):
    try:
        with open(_DEBUG_FILE, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%H:%M:%S')} {msg}\n")
    except Exception:
        pass


def ffmpeg_ready():
    """True when an ffmpeg.exe is available (any of the candidate paths)."""
    return any(os.path.isfile(p) for p in _FFMPEG_CANDIDATES)


def ensure_ffmpeg(url=None, timeout=900):
    """Auto-download ffmpeg.exe if it is missing and a URL is configured.

    Returns True when ffmpeg is available afterwards.  Downloads to the
    writable candidate path (next to the exe when frozen, else next to the
    script) and verifies the binary runs before installing it.
    """
    if ffmpeg_ready():
        return True
    if not url:
        _dbg("ffmpeg missing; no ffmpeg_url configured, GPU streaming unavailable")
        return False
    target = _FFMPEG_CANDIDATES[-1]  # writable: exe dir (frozen) / script dir (dev)
    tmp = target + ".tmp"
    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        _dbg(f"downloading ffmpeg -> {target}")
        req = urllib.request.Request(url, headers={"User-Agent": "DeskBeam/1.0"})
        with urllib.request.urlopen(req, timeout=60) as resp, open(tmp, "wb") as f:
            while True:
                chunk = resp.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
        if _verify_ffmpeg(tmp):
            os.replace(tmp, target)
            _dbg("ffmpeg downloaded and verified OK")
            return True
        _dbg("downloaded file failed ffmpeg verification, discarding")
    except Exception as e:
        _dbg(f"ffmpeg download failed: {e!r}")
    finally:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass
    return ffmpeg_ready()


def _verify_ffmpeg(path):
    try:
        r = subprocess.run([path, "-version"], capture_output=True, timeout=20)
        return r.returncode == 0
    except Exception:
        return False


def native_screen_size():
    """Return the primary monitor resolution (native capture size)."""
    try:
        user32 = ctypes.windll.user32
        return user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
    except Exception:
        return 1920, 1080


def refresh_rate():
    """Return the primary display refresh rate in Hz (best effort)."""
    try:
        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32
        dc = user32.GetDC(0)
        try:
            rate = gdi32.GetDeviceCaps(dc, 116)  # VREFRESH
        finally:
            user32.ReleaseDC(0, dc)
        return int(rate)
    except Exception:
        return 0


def snap_fps(requested, refresh=None):
    """Pick an fps that keeps the capture cadence steady without over-downgrading.

    ddagrab paces its captures against the monitor's presents; a badly
    non-integer ratio of presents-per-frame (e.g. 60 fps on a 165 Hz panel =
    2.75) makes the capture cadence jitter, which shows up as periodic gaps.
    When that happens we snap to the divisor of the refresh rate closest to
    the request — but only if it costs little (<~15%).  Otherwise we keep the
    requested fps: on refresh rates like 75 Hz the only divisor is 25 fps, and
    running at 25 fps hurts far more than the mild cadence jitter at 60 fps.
    """
    if refresh is None:
        refresh = refresh_rate()
    if refresh <= 0 or requested <= 0:
        return max(int(requested), 1)
    req = int(requested)
    ratio = refresh / req
    if abs(ratio - round(ratio)) < 0.05:
        return req  # already a whole number of presents per frame
    best = 1
    for n in range(2, req + 1):
        if refresh % n == 0 and abs(n - req) < abs(best - req):
            best = n
    if best < req * 0.85:
        return req
    return best


class _NudgeWindow:
    """A tiny topmost window used to force Desktop Duplication to emit frames.

    DXGI Desktop Duplication only reports *surface changes*: on a perfectly
    static desktop ddagrab would emit nothing (no first frame, and the stream
    would stall). Briefly showing/hiding this 2x2 pixel corner window creates a
    real surface change so ddagrab always produces an initial frame. With
    ddagrab's `dup_frames=1` the stream then keeps flowing at `framerate`.
    """

    _CLS = "DeskBeamNudgeWnd"

    def __init__(self):
        self._user32 = ctypes.windll.user32
        self._hwnd = None
        self._pump_tid = None
        try:
            WNDPROC = ctypes.WINFUNCTYPE(
                ctypes.c_longlong, ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint, ctypes.c_longlong)

            @WNDPROC
            def wndproc(hwnd, msg, wp, lp):
                if msg == 0x000F:  # WM_PAINT
                    ps = ctypes.wintypes.PAINTSTRUCT()
                    self._user32.BeginPaint(hwnd, ctypes.byref(ps))
                    self._user32.EndPaint(hwnd, ctypes.byref(ps))
                    return 0
                return self._user32.DefWindowProcW(hwnd, msg, wp, lp)

            class WNDCLASSW(ctypes.Structure):
                _fields_ = [("style", ctypes.c_uint), ("lpfnWndProc", WNDPROC),
                            ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                            ("hInstance", ctypes.c_void_p), ("hIcon", ctypes.c_void_p),
                            ("hCursor", ctypes.c_void_p), ("hbrBackground", ctypes.c_void_p),
                            ("lpszMenuName", ctypes.c_wchar_p), ("lpszClassName", ctypes.c_wchar_p)]

            wc = WNDCLASSW()
            wc.lpfnWndProc = wndproc
            wc.hInstance = ctypes.windll.kernel32.GetModuleHandleW(None)
            wc.lpszClassName = self._CLS
            self._user32.RegisterClassW(ctypes.byref(wc))
            w, h = native_screen_size()
            self._user32.CreateWindowExW.restype = ctypes.c_void_p
            self._hwnd = self._user32.CreateWindowExW(
                0x08000000 | 0x20,  # WS_EX_TOPMOST | WS_EX_TOOLWINDOW
                self._CLS, "n",
                0x40000000,  # WS_POPUP
                w - 2, h - 2, 2, 2, 0, 0, wc.hInstance, 0)
            if self._hwnd:
                threading.Thread(target=self._pump, daemon=True).start()
        except Exception:
            self._hwnd = None

    def _pump(self):
        self._pump_tid = ctypes.windll.kernel32.GetCurrentThreadId()
        msg = ctypes.wintypes.MSG()
        while self._user32.GetMessageW(ctypes.byref(msg), 0, 0, 0) > 0:
            self._user32.TranslateMessage(ctypes.byref(msg))
            self._user32.DispatchMessageW(ctypes.byref(msg))

    def toggle(self, times=3, interval=0.4):
        """Flash the window a few times; each flash is a desktop surface change."""
        if not self._hwnd:
            return
        for _ in range(times):
            self._user32.ShowWindow(self._hwnd, 5)  # SW_SHOW
            time.sleep(interval)
            self._user32.ShowWindow(self._hwnd, 0)  # SW_HIDE
            time.sleep(interval)

    def destroy(self):
        if self._hwnd:
            self._user32.DestroyWindow(self._hwnd)
            self._hwnd = None
        if self._pump_tid:
            ctypes.windll.kernel32.PostThreadMessageW(self._pump_tid, 0x0012, 0, 0)  # WM_QUIT


class GPUStreamer:
    """Runs `ffmpeg -f lavfi -i ddagrab=... -c:v h264_nvenc -f h264 -` and
    yields encoded H.264 access units read from its stdout.

    Frames are delimited with the `h264_metadata=aud=insert` bitstream filter
    (an AUD NAL starts every access unit), so no CPU-side pixel work happens.
    """

    _START = b"\x00\x00\x01"

    def __init__(self, width, height, fps=30, gop=15, cq=18, preset="p1",
                 maxrate="40M", bufsize="80M", draw_mouse=True,
                 capture_w=None, capture_h=None):
        self.width = width
        self.height = height
        # Snap to a whole divisor of the monitor refresh (e.g. 55 on a 165 Hz
        # panel instead of 60) so the capture cadence is steady; report the
        # actual fps via self.fps so the client paces correctly.
        self.fps = snap_fps(fps)
        # ddagrab captures a fixed region; when the requested output is smaller
        # than the desktop we must capture the native desktop and downscale,
        # otherwise the picture would be cropped. capture_w/h = native size.
        cap_w = capture_w or width
        cap_h = capture_h or height
        vf = None
        if cap_w != width or cap_h != height:
            vf = f"hwdownload,format=bgra,scale={width}:{height},format=nv12"
        self._q = queue.Queue(maxsize=16)
        self._pending = None
        self._stop = threading.Event()
        self._frame_nals = []
        self._frame_idr = False
        self.produced = 0
        self.dropped = 0
        self._proc = None
        self._encoder = None
        self._enc_err = False
        self._err_buf = b""
        # 编码器自动降级链：NVENC → Intel QSV → libx264 软编。
        # 老卡（Kepler 等）NVENC API 与新版 ffmpeg 不兼容会启动失败；
        # Intel 核显可用 h264_qsv；都不行时回退 libx264 软编，保证有画面。
        for enc in self._encoder_chain():
            cmd = self._build_cmd(enc, vf, cap_w, cap_h, width, height,
                                  draw_mouse, fps, cq, gop, maxrate, bufsize, preset)
            _dbg(f"trying encoder: {enc}")
            try:
                proc = subprocess.Popen(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    bufsize=0, creationflags=subprocess.CREATE_NO_WINDOW,
                )
                try:
                    os.set_blocking(proc.stderr.fileno(), False)
                except Exception:
                    pass
            except OSError as e:
                _dbg(f"encoder {enc}: spawn failed ({e})")
                continue
            # 非阻塞探测：进程退出或 stderr 出现错误关键字（如 Kepler NVENC 的
            # Cannot load cuMemAllocAsync）都算失败。成功编码器永不退出，错误
            # 关键字在几百毫秒内暴露，0.5s 窗口足够，成功路径不干等。
            err_buf = b""
            failed = False
            deadline = time.monotonic() + 0.5
            while time.monotonic() < deadline:
                rc = proc.poll()
                if rc is not None:
                    err = proc.stderr.read(4096).decode("utf-8", "replace").strip()[:300]
                    _dbg(f"encoder {enc}: exited rc={rc} ({err})")
                    failed = True
                    break
                err_buf += self._drain_stderr(proc)
                if _ENC_ERROR_RE.search(err_buf):
                    _dbg(f"encoder {enc}: error in stderr ({err_buf[-200:].decode('utf-8','replace').strip()})")
                    failed = True
                    break
                time.sleep(0.02)
            if failed:
                try:
                    proc.kill()
                except Exception:
                    pass
                proc.wait(timeout=2)
                continue
            self._proc = proc
            self._encoder = enc
            self._err_buf = err_buf + self._drain_stderr(proc)
            break
        if self._proc is None:
            raise RuntimeError("no usable H.264 encoder (nvenc/qsv/libx264 all failed)")
        threading.Thread(target=self._read_loop, daemon=True).start()
        threading.Thread(target=self._err_loop, daemon=True).start()
        self._nudge = _NudgeWindow()
        _dbg(f"started {width}x{height} fps={self.fps} encoder={self._encoder} pid={self._proc.pid}")

    def _encoder_chain(self):
        chain = ["h264_nvenc", "h264_qsv", "libx264"]
        return chain

    def _build_cmd(self, enc, vf, cap_w, cap_h, width, height,
                   draw_mouse, fps, cq, gop, maxrate, bufsize, preset):
        if enc == "h264_qsv":
            # QSV 吃不了 ddagrab 的 d3d11 硬件帧，先 hwdownload 再喂编码器
            vf = vf or "hwdownload,format=bgra,format=nv12"
            # QSV 用 load_plugin=hw（Win 走 MF/兼容层），参数取低延迟风格
            enc_args = [
                "-c:v", "h264_qsv",
                "-preset", "veryfast",
                "-global_quality", str(cq),
                "-g", str(gop), "-bf", "0",
                "-profile:v", "main",
            ]
        elif enc == "libx264":
            # 软编吃不了 ddagrab 的 d3d11 硬件帧，必须 hwdownload 到 CPU 内存
            vf = vf or "hwdownload,format=bgra,format=nv12"
            enc_args = [
                "-c:v", "libx264",
                "-preset", "veryfast", "-tune", "zerolatency",
                "-profile:v", "main", "-crf", str(cq), "-g", str(gop),
            ]
        else:  # h264_nvenc
            enc_args = [
                "-c:v", enc,
                "-preset", preset, "-tune", "ll", "-zerolatency", "1",
                "-rc", "vbr", "-cq", str(cq), "-g", str(gop), "-b:v", "0",
                "-maxrate", maxrate, "-bufsize", bufsize,
                "-profile:v", "main", "-bf", "0",
                "-fflags", "nobuffer", "-flags", "low_delay",
            ]
        cmd = [
            FFMPEG_EXE, "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i",
            f"ddagrab=video_size={cap_w}x{cap_h}:framerate={fps}:draw_mouse={1 if draw_mouse else 0}:dup_frames=1",
            "-bsf:v", "h264_metadata=aud=insert",
        ]
        if vf:
            cmd += ["-vf", vf]
        cmd += enc_args + ["-f", "h264", "-"]
        return cmd

    def _drain_stderr(self, proc):
        """Non-blocking drain of a subprocess's stderr pipe. Returns bytes."""
        chunk = b""
        try:
            fd = proc.stderr.fileno()
            while True:
                try:
                    c = os.read(fd, 65536)
                except (OSError, BlockingIOError):
                    break
                if not c:
                    break
                chunk += c
        except Exception:
            pass
        return chunk

    def _err_loop(self):
        try:
            while not self._stop.is_set():
                chunk = self._drain_stderr(self._proc)
                if not chunk:
                    time.sleep(0.2)
                    continue
                for line in chunk.splitlines():
                    line = line.decode("utf-8", "replace").strip()
                    if line:
                        _dbg("stderr: " + line)
        except Exception:
            pass

    # -- low level: read stdout, split NAL units, group into access units --
    @staticmethod
    def _find_start(buf, pos=0):
        i = buf.find(GPUStreamer._START, pos)
        if i == -1:
            return -1
        if i > 0 and buf[i - 1] == 0:  # 4-byte start code
            return i - 1
        return i

    def _read_loop(self):
        buf = b""
        while not self._stop.is_set():
            try:
                chunk = self._proc.stdout.read(65536)
            except Exception:
                break
            if not chunk:
                break
            buf += chunk
            buf = self._drain(buf)
        self._stop.set()

    def _drain(self, buf):
        frame_nals = self._frame_nals
        frame_idr = self._frame_idr
        while True:
            s = self._find_start(buf, 0)
            if s == -1:
                break
            s2 = self._find_start(buf, s + 3)
            if s2 == -1:
                break
            nal = buf[s:s2]
            buf = buf[s2:]
            if len(nal) < 5:
                continue
            sc4 = nal[:4] == b"\x00\x00\x00\x01"
            header = nal[4] if sc4 else nal[3]
            ntype = header & 0x1F
            if ntype == 9:  # AUD -> boundary between access units
                if frame_nals:
                    if self._q.full():
                        # keep the freshest frame: drop the oldest
                        try:
                            self._q.get_nowait()
                            self.dropped += 1
                        except queue.Empty:
                            pass
                    self._q.put((frame_idr, b"".join(frame_nals)))
                    self.produced += 1
                    frame_nals.clear()
                    frame_idr = False
                continue
            if ntype in (1, 5, 6, 7, 8):  # slices, SEI, SPS, PPS
                frame_nals.append(nal)
                if ntype == 5:
                    frame_idr = True
        self._frame_nals = frame_nals
        self._frame_idr = frame_idr
        return buf

    # -- public API --
    def first_frame(self, timeout=6.0):
        """Wait for the first access unit, nudging the desktop (tiny window
        flash) in the background in case it is static. The first access unit
        is kept for the next read()."""
        time.sleep(0.1)  # let the nudge window's message pump start
        start = time.monotonic()
        next_nudge = start
        while time.monotonic() - start < timeout:
            if self._proc.poll() is not None:
                _dbg(f"first_frame: proc exited rc={self._proc.returncode}")
                return False
            if time.monotonic() >= next_nudge:
                self._nudge.toggle(times=1, interval=0.4)
                next_nudge = time.monotonic() + 1.2
            try:
                self._pending = self._q.get(timeout=0.1)
                _dbg(f"first_frame: OK after {time.monotonic()-start:.2f}s")
                return True
            except queue.Empty:
                continue
        _dbg(f"first_frame: TIMEOUT {timeout}s")
        return False

    def read(self, timeout=0.2):
        """Return (is_idr, h264_access_unit) or None when nothing is ready."""
        if self._pending is not None:
            item, self._pending = self._pending, None
            return item
        try:
            return self._q.get(timeout=timeout)
        except queue.Empty:
            return None

    def alive(self):
        return self._proc.poll() is None

    def close(self):
        self._stop.set()
        try:
            self._nudge.destroy()
        except Exception:
            pass
        rc = self._proc.poll()
        if rc is None:
            try:
                self._proc.terminate()
            except Exception:
                pass
            try:
                self._proc.wait(timeout=3)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass
        else:
            _dbg(f"close: ffmpeg already exited rc={rc}")
        try:
            err = self._proc.stderr.read(4096)
        except Exception:
            err = b""
        if err and err.strip():
            _dbg("close stderr: " + err.decode("utf-8", "replace").strip()[:300])
