"""H.264 encoder using PyAV (FFmpeg bindings)."""

import av
import numpy as np

try:
    import cv2
except ImportError:
    cv2 = None


class H264Encoder:
    """Encodes raw BGRA frames to H.264 Annex B byte stream."""

    def __init__(self, width, height, fps=30, gop=1, cq=22, maxrate="20M", bufsize="40M", preset="p4", pix_fmt="yuv420p"):
        self.width = width
        self.height = height
        self._codec = self._open_codec(fps, gop, cq, maxrate, bufsize, preset, pix_fmt)
        self._frame = av.VideoFrame(width, height, pix_fmt)

    def _open_codec(self, fps, gop, cq, maxrate, bufsize, preset, pix_fmt):
        for name in ("h264_nvenc", "h264_qsv", "h264_amf"):
            try:
                opts = {"preset": preset, "tune": "ll"}
                if name == "h264_nvenc":
                    opts.update({"rc": "vbr_hq", "cq": str(cq), "maxrate": maxrate, "bufsize": bufsize})
                    if gop > 1:
                        opts["forced_idr"] = "1"
                elif name == "h264_qsv":
                    opts.update({"global_quality": "26"})
                elif name == "h264_amf":
                    opts.update({"usage": "ultralowlatency", "quality": "quality"})
                if not self._hw_usable(name, opts, fps, gop, pix_fmt):
                    continue
                return self._make_codec(name, opts, fps, gop, pix_fmt)
            except Exception:
                continue

        codec = av.CodecContext.create("libx264", "w")
        codec.width = self.width
        codec.height = self.height
        codec.pix_fmt = pix_fmt
        codec.framerate = fps
        codec.gop_size = gop
        codec.bit_rate = 0
        codec.options = {
            "preset": "veryfast" if preset == "p4" else "medium",
            "tune": "zerolatency",
            "profile": "high444" if pix_fmt == "yuv444p" else "baseline",
            "crf": str(cq),
        }
        return codec

    def _make_codec(self, name, opts, fps, gop, pix_fmt):
        codec = av.CodecContext.create(name, "w")
        codec.width = self.width
        codec.height = self.height
        codec.pix_fmt = pix_fmt
        codec.framerate = fps
        codec.gop_size = gop
        codec.bit_rate = 0
        codec.options = opts
        return codec

    def _hw_usable(self, name, opts, fps, gop, pix_fmt):
        """create() 只查注册表、不检查硬件。Kepler 等无 NVENC 的卡上 create
        照样成功，真正 encode 才失败。用独立 codec 编几帧验证，通过才采用。"""
        try:
            c = self._make_codec(name, opts, fps, gop, pix_fmt)
            f = av.VideoFrame(self.width, self.height, pix_fmt)
            f.planes[0].update(bytes(self.width * self.height))
            if len(f.planes) > 1:
                f.planes[1].update(bytes((self.width * self.height) // 4))
            if len(f.planes) > 2:
                f.planes[2].update(bytes((self.width * self.height) // 4))
            for _ in range(3):
                if list(c.encode(f)):
                    return True
            return False
        except Exception:
            return False

    def encode(self, bgra):
        """Encode one BGRA frame (numpy array or bytes). Returns bytes (Annex B H.264) or empty bytes."""
        if isinstance(bgra, np.ndarray):
            arr = bgra
        else:
            expected = self.height * self.width * 4
            if len(bgra) != expected:
                return b""
            arr = np.frombuffer(bgra, dtype=np.uint8).reshape(
                self.height, self.width, 4
            )
        try:
            if cv2 is not None:
                h = self.height
                yuv = cv2.cvtColor(arr, cv2.COLOR_BGRA2YUV_I420)
                self._frame.planes[0].update(yuv[:h])
                self._frame.planes[1].update(yuv[h:h + h // 4])
                self._frame.planes[2].update(yuv[h + h // 4:])
                frame = self._frame
            else:
                frame = av.VideoFrame.from_ndarray(arr, format="bgra")
            packets = self._codec.encode(frame)
        except Exception:
            return b""
        annex_b = bytearray()
        for pkt in packets:
            if pkt.size > 0:
                data = bytes(pkt)
                if data[:4] == b"\x00\x00\x00\x01" or data[:3] == b"\x00\x00\x01":
                    annex_b.extend(data)
                else:
                    annex_b.extend(b"\x00\x00\x00\x01")
                    annex_b.extend(data)
        return bytes(annex_b)

    def close(self):
        if self._codec:
            try:
                self._codec.encode(None)
            except Exception:
                pass
            self._codec = None


def has_idr(h264_data):
    """Check if H.264 Annex B data contains an IDR NAL unit (type 5).

    A 4-byte start code (00 00 00 01) also contains the 3-byte pattern from
    its second byte, so one search over 00 00 01 covers both cases.
    """
    start = h264_data.find(b"\x00\x00\x01")
    while start != -1:
        pos = start + 3
        if pos < len(h264_data) and (h264_data[pos] & 0x1F) == 5:
            return True
        start = h264_data.find(b"\x00\x00\x01", start + 3)
    return False
