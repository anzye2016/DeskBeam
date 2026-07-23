"""H.264 encoder using PyAV (FFmpeg bindings)."""

import av
import numpy as np


class H264Encoder:
    """Encodes raw BGRA frames to H.264 Annex B byte stream."""

    def __init__(self, width, height, fps=30, gop=1):
        self.width = width
        self.height = height
        self._codec = self._open_codec(fps, gop)

    def _open_codec(self, fps, gop):
        for name in ("h264_nvenc", "h264_qsv", "h264_amf"):
            try:
                codec = av.CodecContext.create(name, "w")
            except Exception:
                continue
            codec.width = self.width
            codec.height = self.height
            codec.pix_fmt = "yuv420p"
            codec.framerate = fps
            codec.bit_rate = 0
            codec.gop_size = gop
            opts = {"preset": "p4", "tune": "ll"}
            if name == "h264_nvenc":
                opts.update({"rc": "vbr_hq", "cq": "26", "maxrate": "20M", "bufsize": "40M"})
                if gop > 1:
                    opts["forced_idr"] = "1"
            elif name == "h264_qsv":
                opts.update({"global_quality": "26"})
            elif name == "h264_amf":
                opts.update({"usage": "ultralowlatency", "quality": "quality"})
            codec.options = opts
            return codec

        codec = av.CodecContext.create("libx264", "w")
        codec.width = self.width
        codec.height = self.height
        codec.pix_fmt = "yuv420p"
        codec.framerate = fps
        codec.bit_rate = 0
        codec.gop_size = gop
        codec.options = {
            "preset": "veryfast",
            "tune": "zerolatency",
            "profile": "baseline",
            "crf": "26",
        }
        return codec

    @property
    def name(self):
        return self._codec.name

    def encode(self, bgra_bytes):
        """Encode one BGRA frame. Returns bytes (Annex B H.264) or empty bytes."""
        expected = self.height * self.width * 4
        if len(bgra_bytes) != expected:
            return b""
        try:
            arr = np.frombuffer(bgra_bytes, dtype=np.uint8).reshape(
                self.height, self.width, 4
            )
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
    """Check if H.264 Annex B data contains an IDR NAL unit (type 5)."""
    i = 0
    while i < len(h264_data) - 3:
        if h264_data[i : i + 4] == b"\x00\x00\x00\x01":
            i += 4
        elif h264_data[i : i + 3] == b"\x00\x00\x01":
            i += 3
        else:
            i += 1
            continue
        if i < len(h264_data) and (h264_data[i] & 0x1F) == 5:
            return True
    return False
