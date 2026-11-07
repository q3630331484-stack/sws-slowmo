# Sword With Sauce — Slow-Motion Plugin (Bullet Time)

An in-process time-scaling plugin for **Sword With Sauce** (Steam appid 581630, UE4.22):
**press a hotkey to slow the whole world down, press again to restore.**
Player, AI, physics, projectiles and animations all slow down together — while **mouse aim stays at
normal speed** (Max Payne style). Both the speed multiplier and the hotkey are configurable.

- Platform: Windows 64-bit
- Single-player only: no multiplayer, no anti-cheat. The plugin **does not modify any game files** — it only
  hooks a timing function in memory and fully restores everything on unload/exit.

---

## Quick start (3 steps)

1. Double-click **`Start-Slow-Mo.bat`** (no console window; use `Start-Slow-Mo-debug.bat` if you need logs).
   The GUI launches in English (Chinese UI is available via `启动慢动作.bat`).
2. Click **"Launch game & inject"** (or **"Inject into running game"** if the game is already open).
3. In game, press **`F8`**: slow motion ON; press again: OFF.

The status bar shows the game process, injection state, slow-motion state and the live timer-hook hit
count. A rising hit count means the hook is working.

> If frida is missing: double-click `安装依赖.bat` (install dependencies), or run
> `pip install -r requirements.txt` manually.

---

## UI reference

| Button | What it does |
| --- | --- |
| Launch game & inject | Starts the game via Steam if it isn't running, waits for the process, then injects |
| Inject into running game | Injects into the game that is already running |
| Restore real time | Turns slow motion off but keeps the hook (hotkey still works afterwards) |
| Unload plugin | Removes the timer hook and detaches. The clock catches up briefly (~1–2 s fast-forward), then the game is back to 100% normal |
| State snapshot | Prints detailed state (hook address, hit count, clock frequency, ...) to the log |
| Apply & save config | Writes hotkey / speed / options to `config.json` and applies them to the injected game immediately |

Settings:

- **Hotkey** — type it directly, or click **"Capture key"** and press the key you want. Supported:
  `F1`–`F24`, `A`–`Z`, `0`–`9`, arrow keys, `space`, `tab`, `enter`, `esc`, `mouse1`–`mouse5`, etc.
  Modifiers allowed, e.g. `ctrl+F8`, `ctrl+shift+F9`, `alt+mouse4` (case-insensitive).
- **Speed** — default `0.3` (30% speed). `0.1` is very slow, `0.5` is mild, `1.0` = unchanged.
  Allowed range: `0.05`–`2.0`.
- **Beep on toggle** — plays a system beep when toggling, as confirmation.
- **Hotkey only when the game window is focused** — checked by default so key presses in other windows
  don't trigger it. Uncheck to respond globally.
- The hotkey works in fullscreen and borderless-windowed modes.

---

## `config.json`

"Apply & save config" writes this file; you can also edit it by hand (restart the launcher or re-inject
afterwards).

| Field | Default | Description |
| --- | --- | --- |
| `hotkey` | `"F8"` | Hotkey string (syntax above) |
| `speed` | `0.3` | Slow-motion multiplier, `0.05`–`2.0` |
| `beep` | `true` | Beep on toggle |
| `require_foreground` | `true` | Only respond when the game window is focused |
| `game_path` | `D:\steam\steamapps\common\Sword With Sauce\SwordWithSauce2_4_0\Binaries\Win64\SwordWithSauce-Win64-Shipping.exe` | Game executable path |
| `process_name` | `SwordWithSauce-Win64-Shipping.exe` | Game process name |
| `steam_appid` | `"581630"` | Steam app id used for `steam://rungameid/` |
| `launch_via_steam` | `true` | Launch via Steam when clicking "Launch game & inject"; set `false` to run the exe directly |
| `start_timeout_sec` | `150` | Max seconds to wait for the game process (first launch / Steam update can be slower) |

If you moved the game, update `game_path` — otherwise "Launch game & inject" will fail (you can also
start the game manually and click "Inject into running game").

---

## FAQ

**1. Injection failed (access / permission / unable to attach)**
The game may be running as administrator. Either restart the game normally, or run this launcher as
administrator (right-click `Start-Slow-Mo-debug.bat` → Run as administrator).

**2. Hotkey does nothing**
- Make sure the game window is focused (default: foreground-only).
- The hotkey may be taken by another program — try another key (e.g. `F9`, `ctrl+F8`) and click
  "Apply & save config".
- Check the log: if the timer-hook hit count is rising, the plugin works — check that the speed isn't
  set to `1.0`.
- If no "Hotkey:" log line appears when you press the key, the plugin didn't see the key: re-inject,
  focus the game, or choose a different hotkey.

**3. Slow motion works, but mouse aiming feels slower/faster?**
Mouse input is per-pixel deltas and is not multiplied by DeltaTime, so it should stay at normal speed.
If it feels off, it is usually perception or in-game sensitivity — tune the game sensitivity.

**4. Audio and UI animations slow down too?**
Expected: the whole world runs on the same clock. Audio/UI are intentionally not compensated, because
that would break the "everything slows together" effect.

**5. Jitter/physics issues at very low speed?**
UE4's physics substepping can become unstable at extremely low time scales. Stay at or above `0.1`;
`0.2`–`0.3` feels best.

**6. Game crashed or froze?**
Click "Restore real time" / "Unload plugin", or if it's already frozen, end the game process in Task
Manager and relaunch. The plugin writes no game files — a restart returns everything to normal.

**7. Will it break when the game updates?**
No. The plugin is independent of the UE4 version and pak format (it only hooks a system timing
function). Game updates / file verification won't break it.

**8. Launcher error / frida errors in the log**
Double-click `安装依赖.bat` to reinstall frida; see `launcher_error.log` for details. Make sure you're
using the 64-bit Windows build of the game.

**9. Can I run two launchers / inject twice?**
No. The launcher is single-instance; injecting twice would scale time twice. Use one launcher window.

**10. Does "Unload plugin" crash the game?**
No — the plugin first lets the game clock catch up (a brief 1–2 s fast-forward), then removes the hook.
An earlier version could jump the clock backwards on unload; this is fixed.

---

## How it works (for the curious)

- **Why the whole world slows down**: UE4 derives each frame's DeltaTime from
  `QueryPerformanceCounter` (QPC). The plugin hooks `kernel32!QueryPerformanceCounter` inside the game
  process and scales the returned counter by the multiplier, so the engine believes only 0.3× of the
  time has passed per frame — players, AI, physics, animation and projectiles all advance on that same
  DeltaTime, while the render frame rate stays unchanged.
- **Why only `kernel32` is hooked**: on Windows the `kernel32` QPC entry is a forwarding stub that
  jumps to `ntdll!RtlQueryPerformanceCounter`. Hooking the stub is enough; hooking ntdll as well would
  scale the same call twice. The game exe's import table resolves to the kernel32 stub address (verified).
- **Why toggling doesn't jump the clock**: virtual time uses a piecewise-linear map
  `virtual = anchor_virtual + (real - anchor_real) × rate`. When the rate changes, the anchor is reset at
  the current instant so the clock stays continuous and monotonic — no rewinds, no leaps.
- **Why mouse aim is normal**: mouse input is a per-pixel delta that directly changes the view angle; it
  is not multiplied by DeltaTime. Slow motion only affects things driven by time.
- **Why no game files / no UE4SS / no Cheat Engine**: Frida injection only changes in-memory function
  behavior, which disappears when the process exits; it is independent of the engine version and pak
  format, so no old-UE4 AOB adaptation is needed.
- Real time is read through the un-hooked `ntdll!NtQueryPerformanceCounter` to avoid recursion.

---

## Files

| File | Description |
| --- | --- |
| `Start-Slow-Mo.bat` | **Double-click this** — launches the GUI in English, no console |
| `Start-Slow-Mo-debug.bat` | English UI with a console, shows errors |
| `启动慢动作.bat` | Chinese UI, no console |
| `调试启动-带控制台.bat` | Chinese UI with a console |
| `安装依赖.bat` | Install/update frida (English output on English Windows) |
| `launcher.py` | GUI launcher (UI language = `SWS_LANG`, `zh`/`en`) |
| `sws_slowmo_core.py` | Config, hotkey parsing, process lookup, injection wrapper |
| `slowmo_agent.js` | Frida script injected into the game (timer hook + hotkey polling) |
| `config.json` | Configuration (hotkey / speed / ...) |
| `requirements.txt` | Python dependencies |
| `scratch/` | Development self-tests — can be ignored |

---

## Uninstall / rollback

- In game: press the hotkey to turn slow motion off, then click "Unload plugin"; or just exit the game.
- Done with it: delete the `SwordWithSauce-SlowMo` folder. The plugin writes no registry entries, doesn't
  modify the game directory, and runs no background service.
