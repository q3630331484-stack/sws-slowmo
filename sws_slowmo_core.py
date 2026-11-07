# -*- coding: utf-8 -*-
"""Sword With Sauce 慢动作插件 — 核心逻辑（配置 / 热键解析 / 游戏进程 / Frida 注入）。

供 launcher.py（GUI）与调试脚本复用，不依赖 tkinter。
"""
import json
import os
import re
import subprocess
import time

import frida

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
AGENT_PATH = os.path.join(BASE_DIR, "slowmo_agent.js")

# 语言：与 launcher 一致，由环境变量 SWS_LANG 决定（zh 默认 / en）
LANG = (os.environ.get("SWS_LANG") or "").strip().lower()
if LANG not in ("zh", "en"):
    LANG = "zh"


def tr(zh, en):
    """按当前界面语言返回提示文本。"""
    return en if LANG == "en" else zh


def how_label(how):
    """把 launch_game 的返回值转成给人看的文字。"""
    if how == "steam":
        return tr("Steam 协议", "Steam protocol")
    if how == "direct":
        return tr("直接启动 exe", "direct exe")
    return str(how)

GAME_EXE_DEFAULT = (
    r"D:\steam\steamapps\common\Sword With Sauce"
    r"\SwordWithSauce2_4_0\Binaries\Win64\SwordWithSauce-Win64-Shipping.exe"
)
GAME_PROCESS_DEFAULT = "SwordWithSauce-Win64-Shipping.exe"
STEAM_APPID_DEFAULT = "581630"

DEFAULT_CONFIG = {
    "hotkey": "F8",
    "speed": 0.3,
    "beep": True,
    "require_foreground": True,
    "game_path": GAME_EXE_DEFAULT,
    "process_name": GAME_PROCESS_DEFAULT,
    "steam_appid": STEAM_APPID_DEFAULT,
    "launch_via_steam": True,
    "start_timeout_sec": 150,
}

# --------------------------------------------------------------------- 配置
def load_config():
    cfg = dict(DEFAULT_CONFIG)
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            for k, v in data.items():
                if k in cfg and v is not None:
                    cfg[k] = v
    except FileNotFoundError:
        pass
    except (OSError, ValueError):
        pass
    return cfg


def save_config(cfg):
    data = dict(DEFAULT_CONFIG)
    data.update(cfg)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return data


def clamp_speed(value):
    try:
        s = float(value)
    except (TypeError, ValueError):
        raise ValueError(tr("倍率必须是数字，例如 0.3", "Speed must be a number, e.g. 0.3"))
    if s < 0.05:
        s = 0.05
    if s > 2.0:
        s = 2.0
    return round(s, 3)


# --------------------------------------------------------------------- 热键
# 名称 -> Windows 虚拟键码
VK_MAP = {
    "space": 0x20,
    "tab": 0x09,
    "enter": 0x0D,
    "return": 0x0D,
    "esc": 0x1B,
    "escape": 0x1B,
    "backspace": 0x08,
    "insert": 0x2D,
    "ins": 0x2D,
    "delete": 0x2E,
    "del": 0x2E,
    "home": 0x24,
    "end": 0x23,
    "pgup": 0x21,
    "pageup": 0x21,
    "pgdn": 0x22,
    "pagedown": 0x22,
    "up": 0x26,
    "down": 0x28,
    "left": 0x25,
    "right": 0x27,
    "capslock": 0x14,
    "pause": 0x13,
    "printscreen": 0x2C,
    "mouse1": 0x01,
    "mouse2": 0x02,
    "mouse3": 0x04,
    "mouse4": 0x05,
    "mouse5": 0x06,
}
for _i in range(1, 25):
    VK_MAP["f%d" % _i] = 0x6F + _i  # F1=0x70 ... F24=0x87
for _ch in "abcdefghijklmnopqrstuvwxyz":
    VK_MAP[_ch] = ord(_ch.upper())
for _d in "0123456789":
    VK_MAP[_d] = ord(_d)
for _d in "0123456789":
    VK_MAP["num" + _d] = 0x60 + int(_d)
    VK_MAP["numpad" + _d] = 0x60 + int(_d)

MOD_ALIASES = {
    "ctrl": "ctrl",
    "control": "ctrl",
    "alt": "alt",
    "shift": "shift",
    "win": "win",
    "windows": "win",
    "meta": "win",
}

# tkinter keysym -> 配置里的按键名
_KEYSYM_NAMES = {
    "Escape": "esc",
    "Tab": "tab",
    "Return": "enter",
    "KP_Enter": "enter",
    "BackSpace": "backspace",
    "Delete": "delete",
    "Insert": "insert",
    "Home": "home",
    "End": "end",
    "Prior": "pgup",
    "Next": "pgdn",
    "space": "space",
    "Up": "up",
    "Down": "down",
    "Left": "left",
    "Right": "right",
}
for _i in range(1, 25):
    _KEYSYM_NAMES["F%d" % _i] = "F%d" % _i


def keysym_to_name(keysym):
    """把 tkinter 的 event.keysym 转成配置里的热键键名，无法识别返回 None。"""
    if keysym in _KEYSYM_NAMES:
        return _KEYSYM_NAMES[keysym]
    if len(keysym) == 1 and keysym.isalnum():
        return keysym.upper()
    if keysym.startswith("KP_") and keysym[3:].isdigit():
        return "num" + keysym[3:]
    return None


def parse_hotkey(text):
    """解析 'F8' / 'ctrl+shift+F8' / 'mouse4' 等，返回 agent 用的 dict。

    返回 {"vk": int, "ctrl": bool, "alt": bool, "shift": bool, "win": bool}
    解析失败抛 ValueError（提示文字随 SWS_LANG 语言）。
    """
    if not isinstance(text, str) or not text.strip():
        raise ValueError(tr("热键不能为空", "Hotkey cannot be empty"))
    flags = {"ctrl": False, "alt": False, "shift": False, "win": False}
    raw = text.replace(" ", "").split("+")
    if any(p == "" for p in raw):
        raise ValueError(tr("热键格式不正确：%s（多余的 + 号）" % text,
                            "Invalid hotkey: %s (extra '+' sign)" % text))
    parts = raw
    if not parts:
        raise ValueError(tr("热键不能为空", "Hotkey cannot be empty"))
    key_part = None
    for idx, part in enumerate(parts):
        low = part.lower()
        if low in MOD_ALIASES:
            if idx == len(parts) - 1:
                raise ValueError(tr("热键不能只有修饰键，需要再加一个键，例如 ctrl+F8",
                                    "Hotkey needs a main key, e.g. ctrl+F8"))
            flags[MOD_ALIASES[low]] = True
        else:
            if key_part is not None:
                raise ValueError(tr("热键只能包含一个主键：%s" % text,
                                    "Hotkey may contain only one main key: %s" % text))
            key_part = low
    if key_part is None:
        raise ValueError(tr("热键缺少主键，例如 F8 或 Ctrl+F8",
                            "Hotkey is missing a main key, e.g. F8 or Ctrl+F8"))
    if key_part not in VK_MAP:
        raise ValueError(tr("不认识的按键：%s（支持 F1-F24、A-Z、0-9、方向键、mouse4/5 等）" % key_part,
                            "Unknown key: %s (supported: F1-F24, A-Z, 0-9, arrows, mouse4/5, ...)" % key_part))
    vk = VK_MAP[key_part]
    flags["vk"] = vk
    return flags


def format_hotkey(vk):
    """把 vk 转成人看的名字（用于显示）。"""
    for name, code in VK_MAP.items():
        if code == vk:
            if len(name) == 1 or (name[0] == "f" and name[1:].isdigit()):
                return name.upper()
            if name.startswith("mouse"):
                return "Mouse" + name[5:]
            if name.startswith("num") and name[3:].isdigit():
                return "Num" + name[3:]
            return name.capitalize()
    return "VK=0x%02X" % vk


def build_js_config(cfg):
    hk = parse_hotkey(cfg["hotkey"])
    return {
        "speed": clamp_speed(cfg["speed"]),
        "beep": bool(cfg["beep"]),
        "requireForeground": bool(cfg["require_foreground"]),
        "hotkey": hk,
    }


# --------------------------------------------------------------------- 游戏进程
def load_agent_source():
    with open(AGENT_PATH, "r", encoding="utf-8") as f:
        return f.read()


def _local_device():
    return frida.get_local_device()


def find_game_pid(device=None, process_name=GAME_PROCESS_DEFAULT):
    """查找真正的游戏进程。

    注意：Steam 启动时会先出现一个很小的启动器 SwordWithSauce.exe，然后才启动
    真正的 UE4 游戏进程 SwordWithSauce-Win64-Shipping.exe。这里只认后者，
    避免注入到启动器（它几乎不调用 QPC，注入进去会表现为“钩子 0 命中”）。
    """
    device = device or _local_device()
    want = (process_name or GAME_PROCESS_DEFAULT).lower()
    want_base = want[:-4] if want.endswith(".exe") else want
    try:
        procs = device.enumerate_processes()
    except Exception:
        return None
    fallback = None
    for p in procs:
        name = (p.name or "").lower()
        base = name[:-4] if name.endswith(".exe") else name
        if name == want or base == want_base:
            return p.pid
        # 兜底：只认真正的 Shipping 游戏进程，不认启动器
        if fallback is None and "swordwithsauce" in name and "shipping" in name:
            fallback = p.pid
    return fallback


def steam_running(device=None):
    device = device or _local_device()
    try:
        return any((p.name or "").lower() == "steam.exe" for p in device.enumerate_processes())
    except Exception:
        return False


def launch_game(cfg=None, device=None):
    """启动游戏。优先用 Steam 协议，失败/未开启时直接跑 exe。返回启动方式字符串。"""
    cfg = cfg or DEFAULT_CONFIG
    device = device or _local_device()
    exe = cfg.get("game_path") or GAME_EXE_DEFAULT
    appid = str(cfg.get("steam_appid") or STEAM_APPID_DEFAULT)
    if cfg.get("launch_via_steam", True):
        try:
            os.startfile("steam://rungameid/%s" % appid)
            return "steam"
        except OSError:
            pass
    if os.path.isfile(exe):
        subprocess.Popen([exe], cwd=os.path.dirname(exe))
        return "direct"
    raise RuntimeError(tr("找不到游戏程序：%s\n请检查 config.json 里的 game_path" % exe,
                         "Game executable not found: %s\nCheck game_path in config.json" % exe))


def wait_for_game(cfg=None, device=None, timeout=None, cancel=None):
    """等待游戏进程出现，返回 pid 或 None。cancel 为 threading.Event。"""
    cfg = cfg or DEFAULT_CONFIG
    device = device or _local_device()
    name = cfg.get("process_name") or GAME_PROCESS_DEFAULT
    timeout = timeout or float(cfg.get("start_timeout_sec") or 150)
    deadline = time.time() + timeout
    while time.time() < deadline:
        if cancel is not None and cancel.is_set():
            return None
        pid = find_game_pid(device, name)
        if pid is not None:
            return pid
        time.sleep(1.0)
    return None


class SlowMoSession:
    """一次注入会话：持有 frida session/script，封装常用 RPC。"""

    def __init__(self, pid, session, script, messages_cb=None):
        self.pid = pid
        self.session = session
        self.script = script
        self.messages_cb = messages_cb

    @classmethod
    def attach(cls, pid, cfg, messages_cb=None):
        source = load_agent_source()
        session = _local_device().attach(pid)
        script = session.create_script(source)

        if messages_cb is not None:
            def on_message(message, data):
                try:
                    messages_cb(message, data)
                except Exception:
                    pass

            script.on("message", on_message)
        script.load()
        js_cfg = build_js_config(cfg)
        state = script.exports_sync.configure(js_cfg)
        return cls(pid, session, script, messages_cb), state

    # -- RPC 包装
    def state(self):
        return self.script.exports_sync.state()

    def configure(self, cfg):
        return self.script.exports_sync.configure(build_js_config(cfg))

    def set_enabled(self, on):
        return self.script.exports_sync.set_enabled(bool(on))

    def toggle(self):
        return self.script.exports_sync.toggle()

    def restore(self):
        return self.script.exports_sync.restore()

    def prepare_unload(self):
        return self.script.exports_sync.prepare_unload()

    def close(self, timeout=6.0):
        """安全卸载：先把虚拟时间追平真实时间，再摘除钩子，避免时间倒跳崩溃。"""
        try:
            self.script.exports_sync.prepare_unload()
        except Exception:
            pass
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                st = self.script.exports_sync.state()
            except Exception:
                break
            catchup = bool(st.get("catchup"))
            lag = st.get("lagSec")
            if not catchup and (lag is None or abs(lag) < 0.05):
                break
            time.sleep(0.05)
        try:
            self.script.exports_sync.shutdown()
        except Exception:
            pass
        try:
            self.session.detach()
        except Exception:
            pass

    def attach_detached_handler(self, cb):
        def handler(reason, crash):
            try:
                cb(reason, crash)
            except Exception:
                pass

        self.session.on("detached", handler)
