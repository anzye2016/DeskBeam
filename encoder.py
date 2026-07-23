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


def _parse_nal_units(data):
    """Yield individual NAL unit bytes from Annex B H.264 data."""
    pos = 0
    while pos < len(data) - 2:
        if data[pos : pos + 4] == b"\x00\x00\x00\x01":
            pos += 4
        elif data[pos : pos + 3] == b"\x00\x00\x01":
            pos += 3
        else:
            pos += 1
            continue
        start = pos
        while pos < len(data) - 2:
            if data[pos : pos + 4] == b"\x00\x00\x00\x01" or data[pos : pos + 3] == b"\x00\x00\x01":
                break
            pos += 1
        yield data[start:pos]


def _box(boxtype, data=b""):
    import struct
    return struct.pack(">I", len(data) + 8) + boxtype + data


def _avcc(sps_list, pps_list):
    buf = bytearray([1])
    buf.append(sps_list[0][1])
    buf.append(sps_list[0][2])
    buf.append(sps_list[0][3])
    buf.append(0xFF)
    buf.append(0xE0 | len(sps_list))
    import struct
    for s in sps_list:
        buf.extend(struct.pack(">H", len(s)))
        buf.extend(s)
    buf.append(len(pps_list))
    for p in pps_list:
        buf.extend(struct.pack(">H", len(p)))
        buf.extend(p)
    return bytes(buf)


class MP4Muxer:
    """Lightweight fMP4 muxer for H.264 Annex B streaming."""

    def __init__(self, codec_str="avc1.42001F"):
        self._codec_str = codec_str
        self._sps = []
        self._pps = []
        self._seq = 1
        self._timescale = 90000
        self._track_id = 1
        self._base_time = 0
        self._init_data = None

    def init_segment(self):
        return self._init_data

    def _ensure_init(self, nal_units):
        if self._init_data is not None:
            return
        for n in nal_units:
            t = n[0] & 0x1F
            if t == 7:
                self._sps.append(n)
            elif t == 8:
                self._pps.append(n)
        if not self._sps:
            return
        import struct
        ftyp = _box(b"ftyp", b"iso5" + struct.pack(">I", 0))
        avc1 = _box(b"avc1",
            struct.pack(">6I", 0, 0, 0, 0, 0, 1) +
            struct.pack(">HH", 0, 0) +
            b"\x00\x00\x00\x00" +
            struct.pack(">HH", 0, 0) +
            b"\x00\x48\x00\x00\x00\x48\x00\x00\x00\x00\x00\x00\x00\x01" +
            b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00" +
            struct.pack(">H", 24) +
            b"\xFF\xFF" +
            _avcc(self._sps, self._pps)
        )
        stsd = _box(b"stsd", struct.pack(">I", 1) + avc1)
        stts = _box(b"stts", struct.pack(">II", 0, 0))
        stsc = _box(b"stsc", struct.pack(">III", 0, 0, 0))
        stsz = _box(b"stsz", struct.pack(">III", 0, 0, 0))
        stco = _box(b"stco", struct.pack(">II", 0, 0))
        stbl = _box(b"stbl", stsd + stts + stsc + stsz + stco)
        mdhd = _box(b"mdhd",
            struct.pack(">I", 0) +
            struct.pack(">6I", 0, 0, 0, 0, 0, 0) +
            struct.pack(">II", self._timescale, 0)
        )
        hdlr = _box(b"hdlr", struct.pack(">4I", 0, 0, 0, 0) + b"vide")
        mdia = _box(b"mdia", mdhd + hdlr + _box(b"minf", _box(b"vmhd", struct.pack(">4H", 0, 0, 0, 0)) + _box(b"dinf", _box(b"dref", struct.pack(">I", 1) + struct.pack(">3I", 0, 0, 1))) + stbl))
        tkhd = _box(b"tkhd",
            struct.pack(">BI", 0, 7) +
            struct.pack(">8I", 0, 0, 0, 0, 0, 0, 0, 0) +
            struct.pack(">8I", 0, 0, 0, 0, 0, 0, 0, 0x40000000)
        )
        trak = _box(b"trak", tkhd + mdia)
        mvhd = _box(b"mvhd",
            struct.pack(">I", 0) +
            struct.pack(">6I", 0, 0, 0, 0, 0, 0) +
            struct.pack(">II", self._timescale, 0) +
            struct.pack(">2I", 0x10000, 0)
        )
        self._init_data = ftyp + _box(b"moov", mvhd + trak)

    def mux(self, annex_b_data, duration=0, is_key=False):
        nals = list(_parse_nal_units(annex_b_data))
        if not nals:
            return b""
        self._ensure_init(nals)
        # Build mdat with 4-byte length prefix
        mdat_data = bytearray()
        for n in nals:
            t = n[0] & 0x1F
            if t == 7 or t == 8 or t in (6, 9, 10, 11, 12):
                continue
            mdat_data += struct.pack(">I", len(n)) + n
        mdat = _box(b"mdat", bytes(mdat_data))
        # Build moof
        dur = duration if duration > 0 else 1000
        tfhd = struct.pack(">III", 0x020000, self._track_id, 0)
        trun_opts = 0x000100
        trun = struct.pack(">II", trun_opts, 1)
        trun += struct.pack(">I", dur)
        trun += struct.pack(">I", len(mdat) - 8)
        trun += struct.pack(">I", 0)
        trun += struct.pack(">H", 0)
        trun += struct.pack(">H", 0)
        traf = _box(b"traf", _box(b"tfhd", tfhd) + _box(b"trun", trun))
        moof = _box(b"moof", _box(b"mfhd", struct.pack(">I", self._seq)) + traf)
        self._seq += 1
        return moof + mdat
