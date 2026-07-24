"""Elevated input helper - runs as SYSTEM via scheduled task for UAC/lock screen input."""
import json, os, socket, threading, time
from pathlib import Path
import ctypes
from ctypes import wintypes

SendInput = ctypes.windll.user32.SendInput
INPUT_MOUSE = 0
INPUT_KEYBOARD = 1

class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]

class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]

class INPUT(ctypes.Structure):
    class _INPUT(ctypes.Union):
        _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT)]
    _fields_ = [("type", wintypes.DWORD), ("union", _INPUT)]

def _inp(flags, dx=0, dy=0, data=0, vk=0, scan=0):
    if flags & 0x0800:
        inp = INPUT(type=INPUT_MOUSE)
        inp.union.mi = MOUSEINPUT(dx, dy, data, flags, 0, None)
    elif flags:
        inp = INPUT(type=INPUT_KEYBOARD)
        inp.union.ki = KEYBDINPUT(vk, scan, flags, 0, None)
    else:
        return
    SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp))

def _move_to(x, y):
    ctypes.windll.user32.SetCursorPos(x, y)

VK = {"backspace":8,"tab":9,"enter":13,"esc":27,"space":32,"pgup":33,"pgdn":34,"end":35,"home":36,"left":37,"up":38,"right":39,"down":40,"del":46,"f5":116,"caps":20,"shift":16,"ctrl":17,"alt":18,"win":91}

def _key_down(vk):
    _inp(0, vk=vk)

def _key_up(vk):
    _inp(0x0002, vk=vk)

def _type_text(text):
    for ch in text:
        vk = ctypes.windll.user32.VkKeyScanW(ord(ch)) & 0xFF
        if not vk: continue
        shift = bool(ctypes.windll.user32.VkKeyScanW(ord(ch)) >> 8 & 1)
        if shift: _key_down(VK["shift"])
        _key_down(vk); _key_up(vk)
        if shift: _key_up(VK["shift"])
        time.sleep(0.01)

def _press_combo(name):
    parts = name.replace("_","+").split("+")
    mods = [VK[p] for p in parts[:-1] if p in VK]
    key = parts[-1]
    vk = VK.get(key)
    if not vk:
        r = ctypes.windll.user32.VkKeyScanW(ord(key))
        vk = r & 0xFF
    if not vk: return
    for m in mods: _key_down(m)
    _key_down(vk); _key_up(vk)
    for m in reversed(mods): _key_up(m)

def handle(msg):
    c = msg.get("type","")
    if c == "mouse_move":
        _inp(0x0001, msg.get("dx",0), msg.get("dy",0))
    elif c == "mouse_move_to":
        _move_to(msg.get("x",0), msg.get("y",0))
    elif c == "mouse_click":
        _inp(0x0002); _inp(0x0004)
    elif c == "mouse_double_click":
        _inp(0x0002); _inp(0x0004); _inp(0x0002); _inp(0x0004)
    elif c == "mouse_click_at":
        _move_to(msg.get("x",0), msg.get("y",0))
        _inp(0x0002); _inp(0x0004)
    elif c == "mouse_down":
        _inp(0x0002)
    elif c == "mouse_up":
        _inp(0x0004)
    elif c == "mouse_right":
        _inp(0x0008); _inp(0x0010)
    elif c == "mouse_middle":
        _inp(0x0020); _inp(0x0040)
    elif c == "scroll_up":
        _inp(0x0800, data=120)
    elif c == "scroll_down":
        _inp(0x0800, data=-120)
    elif c == "type_text":
        _type_text(msg.get("text",""))
    elif c in VK:
        vk = VK[c]
        _key_down(vk); _key_up(vk)
    else:
        _press_combo(c)

def main():
    import sys
    try:
        ctypes.windll.kernel32.SetConsoleCtrlHandler(None, 1)
    except Exception:
        pass
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("127.0.0.1", 18771))
    s.listen()
    Path(Path(__file__).parent / "elevated.pid").write_text(str(os.getpid()))
    print("Elevated input ready", flush=True)
    while True:
        conn, addr = s.accept()
        with conn:
            f = conn.makefile("rw")
            for line in f:
                try:
                    handle(json.loads(line.strip()))
                except Exception as e:
                    print(f"Error: {e}", flush=True)

if __name__ == "__main__":
    main()
