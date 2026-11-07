# -*- coding: utf-8 -*-
"""Sword With Sauce 慢动作插件 — 图形启动器（tkinter）。

双击“启动慢动作.bat”（中文界面）或 “Start-Slow-Mo.bat”（英文界面）运行。
语言由环境变量 SWS_LANG 决定：zh（默认）/ en。
依赖：Python 3 + frida（见 requirements.txt）。

界面线程只做 UI；注入/轮询/启动游戏都在后台线程，通过队列回传结果。
"""

import os
import queue
import sys
import threading
import time
import tkinter as tk
import traceback
from tkinter import messagebox, scrolledtext, ttk

import sws_slowmo_core as core

# ================================================================ 多语言
LANG = (os.environ.get("SWS_LANG") or "").strip().lower()
if LANG not in ("zh", "en"):
    LANG = "zh"

STRINGS = {
    "zh": {
        "app_title": "Sword With Sauce 慢动作插件",
        "status": "运行状态",
        "settings": "设置",
        "log": "日志",
        "game": "游戏",
        "plugin": "插件",
        "slowmo": "慢动作",
        "timer_hook": "计时钩子",
        "hotkey": "热键：",
        "capture": "捕获按键",
        "capture_waiting": "请按键…",
        "hotkey_hint": "（支持 F1-F24、A-Z、0-9、方向键、mouse1-5，可加 ctrl / alt / shift / win）",
        "speed": "倍率：",
        "speed_hint": "0.3 = 三成速度（允许 0.05 ~ 2.0）",
        "beep": "切换时提示音",
        "foreground_only": "仅游戏窗口在前台时响应热键",
        "apply": "应用并保存配置",
        "apply_hint": "（保存到 config.json，可直接编辑）",
        "start": "一键启动游戏并注入",
        "inject": "注入到已运行的游戏",
        "restore": "恢复真实时间",
        "unload": "卸载插件",
        "snapshot": "状态快照",
        "quit": "退出",
        "bottom_hint": "提示：恢复真实时间 = 解除慢动作但保持注入；卸载插件 = 完全移除计时钩子"
                       "（卸载时会先短暂加速追平时间，不会崩溃）。热键在游戏窗口内生效。",
        "ready_log": "启动器就绪（frida {frida}）。先点“一键启动游戏并注入”，进入游戏后按 {hotkey} 切换慢动作。",
        "notice": "提示",
        "config_error": "配置错误",
        "unload_confirm": "确定卸载插件并移除计时钩子吗？\n"
                          "（卸载时会先让游戏时间快进追平，可能短暂加速 1~2 秒，之后恢复正常）",
        "quit_confirm": "游戏仍在运行且插件已注入。\n退出会卸载插件并恢复真实时间，确定退出吗？",
        "launcher_error": "启动器发生错误：\n\n{detail}\n\n详细信息已写入 launcher_error.log",
        "already_running": "启动器已经在运行了。\n请使用已经打开的窗口（开两个会重复注入）。",
        "running_pid": "运行中（PID {pid}）",
        "not_running": "未运行",
        "injected": "已注入",
        "not_injected": "未注入",
        "slowmo_on": "开启（{speed:.2f}×）",
        "slowmo_off": "关闭",
        "hits": "{rate:.0f} 次/秒（累计 {total}）",
        "waiting_timer": "等待计时…",
        "dash": "—",
        "script_loaded": "插件脚本已加载：版本 {version}，计时钩子={hook}，频率={freq} Hz。",
        "installed": "已安装",
        "not_installed": "未安装",
        "hotkey_toggle": "热键触发：慢动作{state}（{speed:.2f}×）。",
        "on": "开启",
        "off": "关闭",
        "agent_error": "插件错误：{msg}",
        "detached": "插件会话已断开（{reason}）{extra}。",
        "game_may_exit": "，游戏可能已退出",
        "internal_error": "插件内部错误：{msg}",
        "config_saved": "配置已保存到 config.json。",
        "config_later": "当前未注入，配置会在下次注入时生效。",
        "apply_failed": "把配置应用到游戏失败：{msg}",
        "apply_ok": "配置已应用到游戏：倍率 {speed:.2f}，热键 {hotkey}。",
        "poll_interrupted": "状态轮询中断：{msg}",
        "already_injected": "插件已经注入，不需要重复注入。",
        "no_game_starting": "未检测到游戏进程，正在启动游戏…",
        "launch_failed": "启动游戏失败：{msg}",
        "launch_requested": "已请求启动游戏（方式：{how}），等待进程出现（最多 {timeout} 秒）…",
        "wait_timeout": "等待超时：请手动启动游戏后点“注入到已运行的游戏”。（Steam 首次启动/更新可能较慢）",
        "no_running_game": "没有找到正在运行的游戏，请先启动游戏。",
        "injecting": "正在注入游戏进程（PID {pid}）…",
        "inject_failed": "注入失败：{msg}{hint}",
        "hint_admin": "\n提示：游戏可能以管理员身份运行。请以普通权限重新打开游戏，或右键“以管理员身份运行”本启动器。",
        "hint_exited": "\n提示：游戏进程似乎已经退出，请重新启动游戏。",
        "inject_ok": "注入成功：{detail}。",
        "hook_installed": "计时钩子已安装（{addr}），QPC 频率 {freq} Hz",
        "hook_failed": "但计时钩子安装失败",
        "hook_warn": "警告：计时钩子未安装，慢动作不会生效。请把日志反馈给作者。",
        "play_hint": "现在进入游戏，按 {hotkey} 即可切换慢动作（倍率 {speed:.2f}）。",
        "frida_error": "Frida 脚本错误：{msg}",
        "restore_failed": "恢复真实时间失败：{msg}",
        "restore_ok": "已恢复真实时间（慢动作关闭，插件仍在注入中）。",
        "unload_error": "卸载时出现异常：{msg}",
        "unloaded": "插件已卸载，游戏时间恢复正常。",
        "snapshot_failed": "读取状态失败：{msg}",
        "snapshot_log": "状态快照：\n  ",
        "capturing": "热键捕获中：请按下想用的热键（可按住 Ctrl/Alt/Shift 组合）…",
        "unknown_key": "无法识别这个按键（{key}），请换一个，或直接在输入框里填，例如 F8 / ctrl+F8。",
        "invalid_combo": "这个组合不可用：{msg}",
        "captured": "已捕获热键：{hotkey}",
        "op_failed": "操作失败：{msg}",
        "launch_steam": "Steam 协议",
        "launch_direct": "直接启动 exe",
    },
    "en": {
        "app_title": "Sword With Sauce Slow-Motion Plugin",
        "status": "Status",
        "settings": "Settings",
        "log": "Log",
        "game": "Game",
        "plugin": "Plugin",
        "slowmo": "Slow motion",
        "timer_hook": "Timer hook",
        "hotkey": "Hotkey:",
        "capture": "Capture key",
        "capture_waiting": "Press a key…",
        "hotkey_hint": "(F1-F24, A-Z, 0-9, arrows, mouse1-5; ctrl / alt / shift / win allowed)",
        "speed": "Speed:",
        "speed_hint": "0.3 = 30% speed (allowed range 0.05 ~ 2.0)",
        "beep": "Beep on toggle",
        "foreground_only": "Hotkey only when the game window is focused",
        "apply": "Apply & save config",
        "apply_hint": "(saved to config.json — editable)",
        "start": "Launch game & inject",
        "inject": "Inject into running game",
        "restore": "Restore real time",
        "unload": "Unload plugin",
        "snapshot": "State snapshot",
        "quit": "Quit",
        "bottom_hint": "Note: Restore real time keeps the hook loaded and turns slow motion off; Unload plugin removes "
                       "the hook completely (the clock catches up briefly first, no crash). Hotkey works while "
                       "the game window is focused.",
        "ready_log": "Launcher ready (frida {frida}). Click \"Launch game & inject\", then press {hotkey} in game "
                     "to toggle slow motion.",
        "notice": "Notice",
        "config_error": "Config error",
        "unload_confirm": "Unload the plugin and remove the timer hook?\n"
                          "(The game clock will catch up first — expect 1~2 seconds of fast-forward.)",
        "quit_confirm": "The game is running and the plugin is injected.\n"
                        "Quitting will unload the plugin and restore real time. Continue?",
        "launcher_error": "Launcher crashed:\n\n{detail}\n\nDetails written to launcher_error.log",
        "already_running": "The launcher is already running.\n"
                           "Use the existing window (two instances would inject twice).",
        "running_pid": "Running (PID {pid})",
        "not_running": "Not running",
        "injected": "Injected",
        "not_injected": "Not injected",
        "slowmo_on": "On ({speed:.2f}×)",
        "slowmo_off": "Off",
        "hits": "{rate:.0f} hits/s (total {total})",
        "waiting_timer": "Waiting for timing…",
        "dash": "—",
        "script_loaded": "Agent loaded: version {version}, timer hook={hook}, frequency={freq} Hz.",
        "installed": "installed",
        "not_installed": "NOT installed",
        "hotkey_toggle": "Hotkey: slow motion {state} ({speed:.2f}×).",
        "on": "ON",
        "off": "OFF",
        "agent_error": "Agent error: {msg}",
        "detached": "Agent session detached ({reason}){extra}.",
        "game_may_exit": " — the game may have exited",
        "internal_error": "Internal agent error: {msg}",
        "config_saved": "Config saved to config.json.",
        "config_later": "Not injected yet — the config will apply on the next injection.",
        "apply_failed": "Failed to apply config in game: {msg}",
        "apply_ok": "Config applied in game: speed {speed:.2f}, hotkey {hotkey}.",
        "poll_interrupted": "State polling interrupted: {msg}",
        "already_injected": "The plugin is already injected — no need to inject again.",
        "no_game_starting": "Game process not found — launching the game…",
        "launch_failed": "Failed to launch the game: {msg}",
        "launch_requested": "Launch requested ({how}); waiting for the process (up to {timeout} s)…",
        "wait_timeout": "Timed out: start the game manually, then click \"Inject into running game\". "
                        "(First launch / Steam update can be slow.)",
        "no_running_game": "No running game found — start the game first.",
        "injecting": "Injecting into the game process (PID {pid})…",
        "inject_failed": "Injection failed: {msg}{hint}",
        "hint_admin": "\nHint: the game may be running as administrator. Restart the game without elevation, or "
                      "run this launcher as administrator.",
        "hint_exited": "\nHint: the game process seems to have exited — restart the game.",
        "inject_ok": "Injected: {detail}.",
        "hook_installed": "timer hook installed ({addr}), QPC frequency {freq} Hz",
        "hook_failed": "but the timer hook failed to install",
        "hook_warn": "Warning: the timer hook is NOT installed — slow motion will not work. "
                     "Please share the log with the author.",
        "play_hint": "Now switch to the game and press {hotkey} to toggle slow motion (speed {speed:.2f}).",
        "frida_error": "Frida script error: {msg}",
        "restore_failed": "Restore failed: {msg}",
        "restore_ok": "Real time restored (slow motion off, plugin still injected).",
        "unload_error": "Error while unloading: {msg}",
        "unloaded": "Plugin unloaded — the game clock is back to real time.",
        "snapshot_failed": "Failed to read state: {msg}",
        "snapshot_log": "State snapshot:\n  ",
        "capturing": "Capturing hotkey: press the key you want (hold Ctrl/Alt/Shift for combos)…",
        "unknown_key": "Unrecognized key ({key}): try another one, or type it directly, e.g. F8 / ctrl+F8.",
        "invalid_combo": "Unsupported combo: {msg}",
        "captured": "Hotkey captured: {hotkey}",
        "op_failed": "Operation failed: {msg}",
        "launch_steam": "Steam protocol",
        "launch_direct": "direct exe",
    },
}

APP_VERSION = "1.0.0"


def T(key, **kwargs):
    table = STRINGS.get(LANG) or STRINGS["zh"]
    text = table.get(key)
    if text is None:
        text = STRINGS["zh"].get(key, key)
    if kwargs:
        try:
            text = text.format(**kwargs)
        except (KeyError, IndexError, ValueError):
            pass
    return text


APP_TITLE = T("app_title")

# 单实例互斥锁句柄（必须保持引用，否则会被回收）
_MUTEX_HANDLE = None


def _dpi_aware():
    try:
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass


def _single_instance_ok():
    """用命名互斥量防止开两个启动器（否则会重复注入，时间被缩放两次）。"""
    global _MUTEX_HANDLE
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        _MUTEX_HANDLE = kernel32.CreateMutexW(None, False, "Local\\SWS_SlowMo_Launcher")
        if not _MUTEX_HANDLE:
            return True
        return kernel32.GetLastError() != 183  # ERROR_ALREADY_EXISTS
    except Exception:
        return True


class SlowMoApp:
    def __init__(self, root):
        self.root = root
        self.cfg = core.load_config()
        if not os.path.exists(core.CONFIG_PATH):
            try:
                core.save_config(self.cfg)
            except OSError:
                pass

        self.lock = threading.RLock()
        self.rpc_lock = threading.Lock()
        self.session = None       # SlowMoSession 或 None
        self.script = None
        self.game_pid = None
        self.last_state = None
        self.busy = False
        self.closing = threading.Event()
        self.capture_mode = False
        self.seen_errors = set()
        self.queue = queue.Queue()

        self._build_ui()
        self._refresh_ui()
        self.root.after(150, self._drain_queue)
        self._start_watch()
        self._log(T("ready_log",
                    frida=getattr(__import__("frida"), "__version__", "?"),
                    hotkey=self.cfg.get("hotkey", "F8")))

    # ================================================================ UI 构建
    def _build_ui(self):
        root = self.root
        root.title("%s v%s" % (APP_TITLE, APP_VERSION))
        root.geometry("860x620")
        root.minsize(780, 540)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(4, weight=1)

        style = ttk.Style()
        try:
            style.theme_use("vista")
        except tk.TclError:
            pass
        for name, color in (("Ok", "#1b5e20"), ("Warn", "#e65100"),
                            ("Bad", "#b71c1c"), ("Idle", "#757575"), ("On", "#1565c0")):
            style.configure(name + ".TLabel", foreground=color,
                            font=("Microsoft YaHei UI", 9, "bold"))

        # ---------------------------------------------------- 运行状态
        status = ttk.LabelFrame(root, text=T("status"))
        status.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 6))
        for i in range(4):
            status.columnconfigure(i * 2 + 1, weight=1)

        def add_status(col, title):
            ttk.Label(status, text=title + "：").grid(row=0, column=col * 2,
                                                     sticky="e", padx=(10, 0), pady=7)
            lbl = ttk.Label(status, text=T("dash"), style="Idle.TLabel", width=20)
            lbl.grid(row=0, column=col * 2 + 1, sticky="w", padx=(2, 10))
            return lbl

        self.game_lbl = add_status(0, T("game"))
        self.plugin_lbl = add_status(1, T("plugin"))
        self.slowmo_lbl = add_status(2, T("slowmo"))
        self.hook_lbl = add_status(3, T("timer_hook"))

        # ---------------------------------------------------- 设置
        settings = ttk.LabelFrame(root, text=T("settings"))
        settings.grid(row=1, column=0, sticky="ew", padx=10, pady=6)
        settings.columnconfigure(5, weight=1)

        ttk.Label(settings, text=T("hotkey")).grid(row=0, column=0, sticky="e", padx=(10, 2), pady=(8, 2))
        self.hotkey_var = tk.StringVar(value=str(self.cfg.get("hotkey", "F8")))
        self.hotkey_entry = ttk.Entry(settings, textvariable=self.hotkey_var, width=16)
        self.hotkey_entry.grid(row=0, column=1, sticky="w", pady=(8, 2))
        self.capture_btn = ttk.Button(settings, text=T("capture"), width=12, command=self._toggle_capture)
        self.capture_btn.grid(row=0, column=2, padx=6, pady=(8, 2))
        ttk.Label(settings, text=T("hotkey_hint"), foreground="#555555"
                  ).grid(row=0, column=3, columnspan=3, sticky="w", pady=(8, 2))

        ttk.Label(settings, text=T("speed")).grid(row=1, column=0, sticky="e", padx=(10, 2), pady=4)
        self.speed_var = tk.StringVar(value="%g" % float(self.cfg.get("speed", 0.3)))
        ttk.Entry(settings, textvariable=self.speed_var, width=8).grid(row=1, column=1, sticky="w", pady=4)
        ttk.Label(settings, text=T("speed_hint"), foreground="#555555"
                  ).grid(row=1, column=2, columnspan=2, sticky="w", pady=4)

        self.beep_var = tk.BooleanVar(value=bool(self.cfg.get("beep", True)))
        ttk.Checkbutton(settings, text=T("beep"), variable=self.beep_var
                        ).grid(row=1, column=4, sticky="w", padx=(10, 0), pady=4)
        self.fg_var = tk.BooleanVar(value=bool(self.cfg.get("require_foreground", True)))
        ttk.Checkbutton(settings, text=T("foreground_only"), variable=self.fg_var
                        ).grid(row=2, column=0, columnspan=3, sticky="w", padx=10, pady=(2, 8))

        self.apply_btn = ttk.Button(settings, text=T("apply"), command=self._on_apply)
        self.apply_btn.grid(row=2, column=3, columnspan=2, sticky="w", padx=10, pady=(2, 8))
        ttk.Label(settings, text=T("apply_hint"), foreground="#555555"
                  ).grid(row=2, column=5, sticky="w", pady=(2, 8))

        # ---------------------------------------------------- 操作按钮
        actions = ttk.Frame(root)
        actions.grid(row=2, column=0, sticky="ew", padx=10, pady=6)

        self.start_btn = ttk.Button(actions, text=T("start"), command=self._on_start)
        self.start_btn.grid(row=0, column=0, padx=(0, 8), pady=4, ipadx=6)
        self.inject_btn = ttk.Button(actions, text=T("inject"), command=self._on_inject)
        self.inject_btn.grid(row=0, column=1, padx=(0, 8), pady=4, ipadx=6)

        self.restore_btn = ttk.Button(actions, text=T("restore"), command=self._on_restore)
        self.restore_btn.grid(row=1, column=0, padx=(0, 8), pady=4, sticky="w")
        self.unload_btn = ttk.Button(actions, text=T("unload"), command=self._on_unload)
        self.unload_btn.grid(row=1, column=1, padx=(0, 8), pady=4, sticky="w")
        self.snapshot_btn = ttk.Button(actions, text=T("snapshot"), command=self._on_snapshot)
        self.snapshot_btn.grid(row=1, column=2, padx=(0, 8), pady=4, sticky="w")
        self.quit_btn = ttk.Button(actions, text=T("quit"), command=self._on_close)
        self.quit_btn.grid(row=0, column=2, padx=(0, 8), pady=4, sticky="w")

        # ---------------------------------------------------- 日志
        logf = ttk.LabelFrame(root, text=T("log"))
        logf.grid(row=4, column=0, sticky="nsew", padx=10, pady=(6, 4))
        logf.columnconfigure(0, weight=1)
        logf.rowconfigure(0, weight=1)
        self.log_box = scrolledtext.ScrolledText(logf, height=14, wrap="word",
                                                 font=("Consolas", 9), state="disabled")
        self.log_box.grid(row=0, column=0, sticky="nsew", padx=4, pady=4)

        # ---------------------------------------------------- 底部提示
        ttk.Label(root, text=T("bottom_hint"), foreground="#666666"
                  ).grid(row=5, column=0, sticky="w", padx=12, pady=(0, 8))

    # ================================================================ 日志/事件
    def _log(self, text):
        ts = time.strftime("%H:%M:%S")
        self.log_box.configure(state="normal")
        self.log_box.insert("end", "[%s] %s\n" % (ts, text))
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _put(self, kind, payload=None):
        self.queue.put((kind, payload))

    def _drain_queue(self):
        try:
            while True:
                kind, payload = self.queue.get_nowait()
                self._handle_event(kind, payload)
        except queue.Empty:
            pass
        self.root.after(120, self._drain_queue)

    def _handle_event(self, kind, payload):
        if kind == "log":
            self._log(str(payload))
        elif kind == "alert":
            messagebox.showerror(T("notice"), str(payload))
        elif kind == "busy":
            self._set_busy(bool(payload))
        elif kind == "game":
            self.game_pid = payload
            self._refresh_ui()
        elif kind == "state":
            self._apply_state(payload)
        elif kind == "agent":
            self._handle_agent(payload)
        elif kind == "detached":
            self._handle_detached(*payload)

    def _handle_agent(self, payload):
        if not isinstance(payload, dict):
            return
        t = payload.get("type")
        if t == "ready":
            self._log(T("script_loaded",
                        version=payload.get("version"),
                        hook=T("installed") if payload.get("hooked") else T("not_installed"),
                        freq=payload.get("freq")))
        elif t == "toggle":
            on = bool(payload.get("enabled"))
            self._log(T("hotkey_toggle", state=T("on") if on else T("off"),
                        speed=float(payload.get("speed") or 0)))
        elif t == "error":
            self._log(T("agent_error", msg=payload.get("message")))

    def _handle_detached(self, reason, crash):
        if reason == "application-requested":
            return  # 我们主动卸载的，日志已写过
        with self.lock:
            self.session = None
            self.script = None
        self.seen_errors.clear()
        extra = T("game_may_exit") if "terminated" in str(reason) else ""
        self._log(T("detached", reason=reason, extra=extra))
        self._refresh_ui()

    def _apply_state(self, st):
        if st is None:
            self.last_state = None
            self._refresh_ui()
            return
        self.last_state = st
        # 新错误写进日志
        for err in (st.get("errors") or []):
            if err not in self.seen_errors:
                self.seen_errors.add(err)
                self._log(T("internal_error", msg=err))
        self._refresh_ui()

    # ================================================================ 状态刷新
    def _set_busy(self, busy):
        self.busy = busy
        if busy:
            self._refresh_ui()

    def _refresh_ui(self):
        with self.lock:
            attached = self.session is not None
        busy = self.busy

        # 游戏
        if attached or self.game_pid:
            self.game_lbl.configure(text=T("running_pid", pid=self.game_pid or 0), style="Ok.TLabel")
        else:
            self.game_lbl.configure(text=T("not_running"), style="Idle.TLabel")

        # 插件
        if attached:
            self.plugin_lbl.configure(text=T("injected"), style="Ok.TLabel")
        else:
            self.plugin_lbl.configure(text=T("not_injected"), style="Idle.TLabel")

        # 慢动作
        st = self.last_state or {}
        if st.get("enabled"):
            self.slowmo_lbl.configure(text=T("slowmo_on", speed=float(st.get("speed") or 0)),
                                      style="Warn.TLabel")
        else:
            self.slowmo_lbl.configure(text=T("slowmo_off"), style="Idle.TLabel")

        # 钩子命中率
        hits = st.get("_hitsPerSec")
        if attached and hits is not None:
            self.hook_lbl.configure(text=T("hits", rate=hits, total=int(st.get("hookCount") or 0)),
                                    style="On.TLabel")
        elif attached:
            self.hook_lbl.configure(text=T("waiting_timer"), style="Idle.TLabel")
        else:
            self.hook_lbl.configure(text=T("dash"), style="Idle.TLabel")

        def st_btn(btn, enabled):
            if enabled:
                btn.state(["!disabled"])
            else:
                btn.state(["disabled"])

        st_btn(self.start_btn, not attached and not busy)
        st_btn(self.inject_btn, not attached and not busy)
        st_btn(self.restore_btn, attached and not busy)
        st_btn(self.unload_btn, attached and not busy)
        st_btn(self.snapshot_btn, attached and not busy)
        st_btn(self.apply_btn, not busy)

    # ================================================================ 后台轮询
    def _start_watch(self):
        """未注入时每 2 秒看一下游戏是否在运行。"""
        def loop():
            last = object()
            while not self.closing.is_set():
                with self.lock:
                    attached = self.session is not None
                if not attached:
                    try:
                        pid = core.find_game_pid()
                    except Exception:
                        pid = None
                    if pid != last:
                        last = pid
                        self._put("game", pid)
                time.sleep(2.0)

        threading.Thread(target=loop, daemon=True, name="sws-watch").start()

    def _start_poll(self, script):
        """注入后每 0.5 秒拉一次 agent 状态。"""
        def loop():
            last_count = None
            last_t = None
            while not self.closing.is_set():
                with self.lock:
                    if self.script is not script:
                        return
                try:
                    with self.rpc_lock:
                        st = script.exports_sync.state()
                except Exception as e:
                    with self.lock:
                        still = self.script is script
                    if still and not self.closing.is_set():
                        self._put("log", T("poll_interrupted", msg=e))
                    return
                now = time.time()
                st = dict(st or {})
                count = int(st.get("hookCount") or 0)
                if last_count is not None and last_t is not None and now > last_t:
                    st["_hitsPerSec"] = (count - last_count) / (now - last_t)
                last_count, last_t = count, now
                self._put("state", st)
                time.sleep(0.5)

        threading.Thread(target=loop, daemon=True, name="sws-poll").start()

    # ================================================================ 配置
    def _collect_config(self):
        cfg = dict(self.cfg)
        cfg["hotkey"] = self.hotkey_var.get().strip()
        core.parse_hotkey(cfg["hotkey"])  # 校验，失败抛 ValueError
        cfg["speed"] = core.clamp_speed(self.speed_var.get())
        cfg["beep"] = bool(self.beep_var.get())
        cfg["require_foreground"] = bool(self.fg_var.get())
        return cfg

    def _on_apply(self):
        try:
            cfg = self._collect_config()
        except ValueError as e:
            messagebox.showerror(T("config_error"), str(e))
            return
        self.cfg = core.save_config(cfg)
        self._log(T("config_saved"))
        self._run_bg(self._task_apply, self.cfg)

    def _task_apply(self, cfg):
        with self.lock:
            sess = self.session
        if sess is None:
            self._put("log", T("config_later"))
            return
        try:
            with self.rpc_lock:
                st = sess.configure(cfg)
        except Exception as e:
            self._put("log", T("apply_failed", msg=e))
            return
        self._put("log", T("apply_ok", speed=float(st.get("speed") or 0), hotkey=cfg["hotkey"]))
        self._put("state", st)

    # ================================================================ 注入
    def _attached(self):
        with self.lock:
            return self.session is not None

    def _validate_or_alert(self):
        try:
            return self._collect_config()
        except ValueError as e:
            messagebox.showerror(T("config_error"), str(e))
            return None

    def _on_start(self):
        if self._attached():
            messagebox.showinfo(T("notice"), T("already_injected"))
            return
        cfg = self._validate_or_alert()
        if cfg is None:
            return
        self.cfg = core.save_config(cfg)
        self._run_bg(self._task_start_inject, self.cfg)

    def _on_inject(self):
        if self._attached():
            messagebox.showinfo(T("notice"), T("already_injected"))
            return
        cfg = self._validate_or_alert()
        if cfg is None:
            return
        self.cfg = core.save_config(cfg)
        self._run_bg(self._task_inject_only, self.cfg)

    def _task_start_inject(self, cfg):
        device = core._local_device()
        pid = core.find_game_pid(device, cfg.get("process_name"))
        if pid is None:
            self._put("log", T("no_game_starting"))
            try:
                how = core.launch_game(cfg, device)
            except Exception as e:
                self._put("alert", T("launch_failed", msg=e))
                return
            timeout = float(cfg.get("start_timeout_sec") or 150)
            self._put("log", T("launch_requested", how=core.how_label(how), timeout=timeout))
            pid = core.wait_for_game(cfg, device)
            if pid is None:
                self._put("log", T("wait_timeout"))
                return
        self._put("game", pid)
        self._do_attach(pid, cfg)

    def _task_inject_only(self, cfg):
        device = core._local_device()
        pid = core.find_game_pid(device, cfg.get("process_name"))
        if pid is None:
            self._put("log", T("no_running_game"))
            return
        self._put("game", pid)
        self._do_attach(pid, cfg)

    def _do_attach(self, pid, cfg):
        self._put("log", T("injecting", pid=pid))
        try:
            sess, st = core.SlowMoSession.attach(pid, cfg, messages_cb=self._on_agent_message)
        except Exception as e:
            msg = str(e)
            low = msg.lower()
            hint = ""
            if "unable to attach" in low or "access" in low or "permission" in low or "elevat" in low:
                hint = T("hint_admin")
            elif "unable to find process" in low or "not found" in low:
                hint = T("hint_exited")
            self._put("log", T("inject_failed", msg=msg, hint=hint))
            self._put("alert", T("inject_failed", msg=msg, hint=hint))
            return

        with self.lock:
            self.session = sess
            self.script = sess.script
            self.game_pid = pid
        sess.attach_detached_handler(
            lambda reason, crash: self._put("detached", (reason, crash)))

        hooked = bool(st.get("hooked"))
        if hooked:
            detail = T("hook_installed", addr=st.get("hookAddr"), freq=int(st.get("freq") or 0))
        else:
            detail = T("hook_failed")
        self._log(T("inject_ok", detail=detail))
        if not hooked:
            self._log(T("hook_warn"))
        self._put("state", st)
        self._start_poll(sess.script)
        self._log(T("play_hint", hotkey=cfg["hotkey"],
                    speed=float(st.get("speed") or cfg.get("speed") or 0)))
        self._refresh_ui()

    def _on_agent_message(self, message, data):
        if message.get("type") == "send":
            self._put("agent", message.get("payload"))
        elif message.get("type") == "error":
            self._put("log", T("frida_error", msg=message.get("description")))

    # ================================================================ 其它操作
    def _on_restore(self):
        if not self._attached():
            return
        self._run_bg(self._task_restore)

    def _task_restore(self):
        with self.lock:
            sess = self.session
        if sess is None:
            return
        try:
            with self.rpc_lock:
                st = sess.restore()
        except Exception as e:
            self._put("log", T("restore_failed", msg=e))
            return
        self._put("log", T("restore_ok"))
        self._put("state", st)

    def _on_unload(self):
        if not self._attached():
            return
        if not messagebox.askokcancel(T("unload"), T("unload_confirm")):
            return
        self._run_bg(self._task_unload)

    def _task_unload(self):
        with self.lock:
            sess = self.session
            self.session = None
            self.script = None
        if sess is not None:
            try:
                sess.close()
            except Exception as e:
                self._put("log", T("unload_error", msg=e))
        self._put("log", T("unloaded"))
        self._put("state", None)
        self._refresh_ui()

    def _on_snapshot(self):
        if not self._attached():
            return
        self._run_bg(self._task_snapshot)

    def _task_snapshot(self):
        with self.lock:
            sess = self.session
        if sess is None:
            return
        try:
            with self.rpc_lock:
                st = sess.state()
        except Exception as e:
            self._put("log", T("snapshot_failed", msg=e))
            return
        lines = [
            "enabled=%s  speed=%s  rate=%s" % (st.get("enabled"), st.get("speed"), st.get("rate")),
            "hooked=%s  hookAddr=%s  hookCount=%d" % (st.get("hooked"), st.get("hookAddr"),
                                                      int(st.get("hookCount") or 0)),
            "freq=%d  realNow=%s  virtualNow=%s" % (int(st.get("freq") or 0),
                                                    st.get("realNow"), st.get("virtualNow")),
            "pid=%s  arch=%s  version=%s" % (st.get("pid"), st.get("arch"), st.get("version")),
        ]
        for err in (st.get("errors") or []):
            lines.append("error: %s" % err)
        self._put("log", T("snapshot_log") + "\n  ".join(lines))

    # ================================================================ 热键捕获
    def _toggle_capture(self):
        self.capture_mode = not self.capture_mode
        if self.capture_mode:
            self.capture_btn.configure(text=T("capture_waiting"))
            self._log(T("capturing"))
            self.root.bind_all("<KeyPress>", self._on_capture_key)
        else:
            self._end_capture()

    def _end_capture(self):
        self.capture_mode = False
        self.capture_btn.configure(text=T("capture"))
        self.root.unbind_all("<KeyPress>")

    def _on_capture_key(self, event):
        if not self.capture_mode:
            return None
        mods_only = {"Control_L", "Control_R", "Shift_L", "Shift_R", "Alt_L", "Alt_R",
                     "Win_L", "Win_R", "Super_L", "Super_R", "Caps_Lock"}
        if event.keysym in mods_only:
            return "break"
        mods = []
        state = event.state
        if state & 0x4:
            mods.append("ctrl")
        if state & 0x1:
            mods.append("shift")
        if state & 0x20000:
            mods.append("alt")
        name = core.keysym_to_name(event.keysym)
        if name is None:
            self._log(T("unknown_key", key=event.keysym))
            return "break"
        value = "+".join(mods + [name])
        try:
            core.parse_hotkey(value)
        except ValueError as e:
            self._log(T("invalid_combo", msg=e))
            return "break"
        self.hotkey_var.set(value)
        self._log(T("captured", hotkey=value))
        self._end_capture()
        return "break"

    # ================================================================ 生命周期
    def _run_bg(self, fn, *args):
        self._set_busy(True)
        self._refresh_ui()

        def wrapper():
            try:
                fn(*args)
            except Exception as e:
                self._put("log", T("op_failed", msg=e))
            finally:
                self._put("busy", False)

        threading.Thread(target=wrapper, daemon=True, name="sws-task").start()

    def _on_close(self):
        if self._attached():
            ok = messagebox.askokcancel(T("quit"), T("quit_confirm"))
            if not ok:
                return
        self.closing.set()
        with self.lock:
            sess = self.session
            self.session = None
            self.script = None
        if sess is not None:
            try:
                sess.close()
            except Exception:
                pass
        try:
            self.root.destroy()
        except tk.TclError:
            pass


def main():
    _dpi_aware()
    root = tk.Tk()
    if not _single_instance_ok():
        messagebox.showwarning(APP_TITLE, T("already_running"))
        root.destroy()
        return
    SlowMoApp(root)
    root.mainloop()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        tb = traceback.format_exc()
        try:
            with open(os.path.join(core.BASE_DIR, "launcher_error.log"), "w", encoding="utf-8") as f:
                f.write(tb)
        except OSError:
            pass
        try:
            messagebox.showerror(T("app_title") + " — error", T("launcher_error", detail=tb[-1500:]))
        except Exception:
            pass
