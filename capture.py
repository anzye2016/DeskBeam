"""Screen capture via DXGI (dxcam) for the legacy software-encoding pipeline.

Produces raw BGRA frames with the cursor drawn in, used by the WebSocket
video pipeline and the WebRTC fallback track. Requires dxcam + numpy + av.
"""

import ctypes
import ctypes.wintypes
import threading
import traceback

try:
    import dxcam
except ImportError:
    dxcam = None

try:
    import numpy as np
except ImportError:
    np = None

try:
    import av
except ImportError:
    av = None

try:
    from encoder import H264Encoder
except ImportError:
    H264Encoder = None

HAS_DXCAM = dxcam is not None
HAS_AV = av is not None and np is not None and H264Encoder is not None

_capture_running = False
_capture_lock = threading.Lock()
_camera = None
_last_frame = None


def probe_hw_encoder():
    """Return the hardware encoder FFmpeg will use, or 'libx264' for software."""
    if av is None:
        return "libx264"
    for name in ("h264_nvenc", "h264_qsv", "h264_amf"):
        try:
            av.CodecContext.create(name, "w")
            return name
        except Exception:
            continue
    return "libx264"


IS_SOFT = probe_hw_encoder() == "libx264"


def _init_camera():
    global _camera
    if _camera is None and HAS_DXCAM:
        # 双显卡机器上 dxcam 默认 output_idx=0 可能指向没有实际桌面的
        # 输出（如 NVIDIA 独显，桌面实际渲染在 Intel 核显）。遍历所有
        # 输出，选第一个能真正抓到帧的。
        for i in range(8):
            try:
                cam = dxcam.create(output_idx=i, output_color="BGRA")
                if cam is None:
                    continue
                cam.start(target_fps=60)
                probe = cam.grab()
                if probe is not None and getattr(probe, "size", 0) > 0:
                    _camera = cam
                    print(f"  dxcam: selected output_idx={i}")
                    return
                cam.stop()
                cam.release()
            except Exception:
                continue
        print("  dxcam: no usable output found")


def capture_screen_raw():
    """Capture screen via DXGI as raw BGRA bytes with cursor drawn. Returns (bytes, w, h)."""
    global _capture_running, _camera, _last_frame
    with _capture_lock:
        if _capture_running:
            if _last_frame is not None:
                return _last_frame
            return None, 0, 0
        _capture_running = True
    try:
        if not HAS_DXCAM:
            return None, 0, 0

        _init_camera()
        if _camera is None:
            return None, 0, 0

        frame = _camera.grab()
        if frame is None or frame.size == 0:
            if _last_frame is not None:
                return _last_frame
            return None, 0, 0

        h, w = frame.shape[:2]
        bgra = frame.copy()
        try:
            pt = ctypes.wintypes.POINT()
            ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
            cx, cy = pt.x, pt.y
            if 0 <= cx < w and 0 <= cy < h:
                cs = 16
                x1, x2 = max(0, cx - cs), min(w - 1, cx + cs)
                y1, y2 = max(0, cy - cs), min(h - 1, cy + cs)
                for dy in (-1, 0, 1):
                    r = cy + dy
                    if 0 <= r < h:
                        bgra[r, x1:x2 + 1] = [0, 255, 0, 255]
                for dx in (-1, 0, 1):
                    c = cx + dx
                    if 0 <= c < w:
                        bgra[y1:y2 + 1, c] = [0, 255, 0, 255]
                bgra[cy, cx] = [255, 255, 255, 255]
        except Exception:
            pass
        _last_frame = (bgra, w, h)
        return _last_frame
    except Exception:
        traceback.print_exc()
        if _last_frame is not None:
            return _last_frame
        return None, 0, 0
    finally:
        _capture_running = False
