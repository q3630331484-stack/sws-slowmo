/*
 * slowmo_agent.js — Sword With Sauce 慢动作插件（Frida 注入脚本）
 * 版本 3.0.0
 *
 * 原理：
 *   在游戏进程内接管 kernel32.dll!QueryPerformanceCounter (QPC)。
 *   UE4 的 FPlatformTime 用 QPC 推导 DeltaTime（帧间隔），因此把 QPC 返回值
 *   乘以倍率后，整个世界（玩家、AI、物理、动画、投射物）一起变慢，而渲染
 *   帧率保持原样。鼠标视角输入是"每像素增量"、不乘 DeltaTime，所以天然
 *   保持正常速度（马克思·佩恩式子弹时间）。
 *
 * 为什么只挂 kernel32 的桩：
 *   Windows 的导入解析把一个模块的 QPC 调用指向 kernel32.dll 里的导出桩，
 *   桩内部再跳到 ntdll!RtlQueryPerformanceCounter。实测游戏 exe 的 IAT 中
 *   QueryPerformanceCounter 解析到的就是 kernel32 桩地址，所以接管它即可
 *   捕获游戏的全部 QPC 调用；同时不碰 ntdll 目标，避免同一调用被缩放两次。
 *
 * 性能（v3 与 v2 的关键区别）：
 *   v2 用 Interceptor.attach + JS 回调，每次 QPC 调用都要进出 JS 虚拟机。
 *   实测游戏里 QPC 调用可达每秒数十万次，JS 回调开销会让游戏 CPU 明显上升。
 *   v3 把计时缩放逻辑编译成进程内机器码（Frida CModule，TinyCC），再用
 *   Interceptor.replace 把它直接作为 QPC 的替代实现 —— 每次调用只多一次
 *   原生函数跳转，不再经过 JS。JS 只负责热键、配置和状态上报。
 *   若 CModule 在目标环境不可用，自动回退到 v2 的 JS 回调模式。
 *
 * 虚拟时间模型（保证开关瞬间不跳变、不回退）：
 *   virtual = anchorVirt + (real - anchorReal) * rate
 *   切换倍率时，以"当前虚拟时间"作为新锚点，函数连续且单调递增。
 *   用 seqlock 保护锚点写入，避免多线程读到半更新的状态。
 *
 * 读取真实时间用未接管的 ntdll!RtlQueryPerformanceCounter，不会递归。
 *
 * 热键：agent 内用 GetAsyncKeyState 轮询（全局生效，不依赖窗口消息），
 *       默认只在游戏窗口位于前台时响应，防止切出去误触。
 */

'use strict';

var VERSION = '3.0.1';

var errors = [];
var hookCount = 0;          // JS 回调模式下的命中计数
var hooked = false;
var hookMode = 'none';      // 'native'（CModule 替代实现）或 'js'（回调）
var qpcAddr = null;
var hotkeyTimer = null;

var cfg = {
  speed: 0.3,
  beep: true,
  requireForeground: true,
  hotkey: { vk: 0x77 /* F8 */, ctrl: false, alt: false, shift: false, win: false }
};

var st = {
  enabled: false,
  speed: 0.3,
  rate: 1.0,
  anchorReal: 0,
  anchorVirt: 0,
  anchored: false,
  catchup: false            // 卸载前的“追平真实时间”阶段
};

var hotkeyWasDown = false;

// ------------------------------------------------------------------ 工具
function pushError(where, e) {
  var msg = where + ': ' + e;
  if (errors.length > 30) errors.shift();
  errors.push(msg);
}

function resolveExport(moduleName, exportName) {
  try {
    var a = Module.getGlobalExportByName(exportName);
    if (a !== null && typeof a !== 'undefined') return a;
  } catch (e) { /* ignore */ }
  try {
    return Process.getModuleByName(moduleName).getExportByName(exportName);
  } catch (e2) { /* ignore */ }
  return null;
}

function nativeFn(moduleName, exportName, retType, argTypes) {
  var addr = resolveExport(moduleName, exportName);
  if (addr === null) return null;
  try {
    return new NativeFunction(addr, retType, argTypes);
  } catch (e) {
    pushError('NativeFunction ' + exportName, e);
    return null;
  }
}

function clampSpeed(s) {
  s = Number(s);
  if (!isFinite(s)) return st.speed;
  if (s < 0.02) s = 0.02;
  if (s > 2.0) s = 2.0;
  return Math.round(s * 1000) / 1000;
}

// ------------------------------------------------------------------ 真实时间
var ntQpc = nativeFn('ntdll.dll', 'NtQueryPerformanceCounter', 'int', ['pointer', 'pointer']);
var qpfBuf = Memory.alloc(8);
var realBuf = Memory.alloc(8);
var probeBuf = Memory.alloc(8);
var qpfFn = nativeFn('kernel32.dll', 'QueryPerformanceFrequency', 'int', ['pointer']);
var qpcFn = nativeFn('kernel32.dll', 'QueryPerformanceCounter', 'int', ['pointer']);
var FREQ = 10000000;
if (qpfFn !== null) {
  try {
    qpfFn(qpfBuf);
    var fq = qpfBuf.readU64().toNumber();
    if (fq > 0) FREQ = fq;
  } catch (e) { pushError('QPF', e); }
}

function readReal() {
  if (ntQpc === null) return null;
  try {
    ntQpc(realBuf, ptr(0));
    return realBuf.readU64().toNumber();
  } catch (e) {
    pushError('NtQPC', e);
    return null;
  }
}

function mapVirtual(real) {
  if (!st.anchored) return real;
  return st.anchorVirt + (real - st.anchorReal) * st.rate;
}

/** JS 回调模式：以当前时刻为锚点切换时间流速，虚拟时间连续。 */
function switchRateJs(newRate) {
  var r = readReal();
  if (r !== null) {
    if (st.anchored) {
      st.anchorVirt = mapVirtual(r);
    } else {
      st.anchorVirt = r;
    }
    st.anchorReal = r;
    st.anchored = true;
  }
  st.rate = newRate;
}

// ------------------------------------------------------------------ 原生钩子（CModule）
/*
 * 这段 C 代码被 Frida 在内置 TinyCC 里编译成机器码，直接作为 QPC 的替代实现：
 *   1) 调用未接管的 ntdll!RtlQueryPerformanceCounter 取到真实计数值；
 *   2) 按分段线性映射把虚拟时间写回调用者的 LARGE_INTEGER。
 * real_qpc 由 JS 侧通过 CModule 的 symbols 参数注入。
 * 锚点用 seqlock（写前写后各 +1 序号）保护，钩子线程只在序号稳定且为偶数
 * 时读取，最多重试 4 次；万一读到更新中的状态就保持原值返回，保证安全。
 */
var C_SOURCE = [
  '#include <stdint.h>',
  '',
  '/* 注意：Windows 上的 TinyCC 没有连接 libgcc，无符号 64 位 <-> double 的',
  ' * 隐式转换会引用 __floatundidf / __fixunsdfdi 而链接失败。QPC 计数值恒为正',
  ' * 且远小于 2^63，因此这里统一用有符号 int64_t 保存时间值，其与 double 的',
  ' * 转换由 SSE 指令直接完成，不需要运行库辅助函数。 */',
  'extern int real_qpc(int64_t *lp);',
  '',
  'static volatile long g_seq = 0;',
  'static volatile int g_anchored = 0;',
  'static volatile double g_rate = 1.0;',
  'static volatile int64_t g_anchor_real = 0;',
  'static volatile double g_anchor_virt = 0.0;',
  'static volatile double g_hits = 0.0;',
  '',
  'static void write_begin(void) { g_seq = g_seq + 1; }',
  'static void write_end(void) { g_seq = g_seq + 1; }',
  '',
  'void sws_set_rate(double rate) {',
  '    int64_t now = 0;',
  '    real_qpc(&now);',
  '    write_begin();',
  '    if (g_anchored) {',
  '        g_anchor_virt = g_anchor_virt + (double)(int64_t)(now - g_anchor_real) * g_rate;',
  '    } else {',
  '        g_anchor_virt = (double)now;',
  '        g_anchored = 1;',
  '    }',
  '    g_anchor_real = now;',
  '    g_rate = rate;',
  '    write_end();',
  '}',
  '',
  'void sws_init(void) {',
  '    int64_t now = 0;',
  '    real_qpc(&now);',
  '    write_begin();',
  '    g_anchor_real = now;',
  '    g_anchor_virt = (double)now;',
  '    g_rate = 1.0;',
  '    g_anchored = 1;',
  '    write_end();',
  '}',
  '',
  'int sws_qpc(int64_t *lp) {',
  '    int r = real_qpc(lp);',
  '    int64_t real;',
  '    int i;',
  '    long s1;',
  '    long s2;',
  '    double rate;',
  '    int64_t ar;',
  '    double av;',
  '    int64_t delta;',
  '    double v;',
  '    if (lp == 0) return r;',
  '    real = *lp;',
  '    g_hits = g_hits + 1.0;',
  '    for (i = 0; i < 4; i++) {',
  '        s1 = g_seq;',
  '        if ((s1 & 1) != 0) continue;',
  '        if (!g_anchored) return r;',
  '        rate = g_rate;',
  '        ar = g_anchor_real;',
  '        av = g_anchor_virt;',
  '        s2 = g_seq;',
  '        if (s1 != s2) continue;',
  '        delta = real - ar;',
  '        if (delta < 0) return r;',
  '        v = av + (double)delta * rate;',
  '        if (v < 0.0) v = 0.0;',
  '        *lp = (int64_t)(v + 0.5);',
  '        return r;',
  '    }',
  '    return r;',
  '}',
  '',
  'double sws_hits(void) {',
  '    return g_hits;',
  '}',
  '',
  'void sws_reset_hits(void) {',
  '    g_hits = 0.0;',
  '}'
].join('\n');

var cm = null;
var swsSetRate = null;
var swsHits = null;

function installNativeHook() {
  var rtlQpc = null;
  try {
    rtlQpc = Process.getModuleByName('ntdll.dll').getExportByName('RtlQueryPerformanceCounter');
  } catch (e) {
    pushError('ntdll RtlQPC', e);
  }
  if (rtlQpc === null) throw new Error('ntdll.dll!RtlQueryPerformanceCounter 未找到');

  var mod = new CModule(C_SOURCE, { real_qpc: rtlQpc });
  var init = new NativeFunction(mod.sws_init, 'void', []);
  swsSetRate = new NativeFunction(mod.sws_set_rate, 'void', ['double']);
  swsHits = new NativeFunction(mod.sws_hits, 'double', []);
  init();

  Interceptor.replace(qpcAddr, new NativePointer(mod.sws_qpc));

  cm = mod;
  hookMode = 'native';
  hooked = true;
}

/** v2 的回退实现：JS 回调方式（如果 CModule 不可用）。 */
function installJsHook() {
  try {
    Interceptor.attach(qpcAddr, {
      onEnter: function (args) {
        this.lp = args[0];
      },
      onLeave: function (retval) {
        hookCount++;
        var lp = this.lp;
        if (lp === null || lp.isNull()) return;
        var real;
        try {
          real = lp.readU64().toNumber();
        } catch (e) {
          return;
        }
        if (!st.anchored) {
          st.anchorReal = real;
          st.anchorVirt = real;
          st.anchored = true;
        }
        var v = st.anchorVirt + (real - st.anchorReal) * st.rate;
        if (v < 0) v = 0;
        if (st.catchup && v >= real) {
          // 虚拟时间已追上真实时间：切换为恒等映射，之后摘除钩子不会造成时间倒跳
          st.catchup = false;
          st.anchorReal = real;
          st.anchorVirt = real;
          st.rate = 1.0;
          st.enabled = false;
          v = real;
          send({ type: 'caughtup' });
        }
        try {
          lp.writeU64(Math.round(v));
        } catch (e) {
          pushError('write qpc', e);
        }
      }
    });
    hookMode = 'js';
    hooked = true;
  } catch (e) {
    pushError('Interceptor.attach', e);
    send({ type: 'error', message: '安装 QPC 钩子失败: ' + e });
  }
}

function installHook() {
  var addr = null;
  try {
    addr = Process.getModuleByName('kernel32.dll').getExportByName('QueryPerformanceCounter');
  } catch (e) {
    pushError('kernel32 QPC', e);
  }
  if (addr === null) {
    send({ type: 'error', message: 'kernel32.dll!QueryPerformanceCounter 未找到，无法安装计时钩子' });
    return false;
  }
  qpcAddr = addr;

  try {
    installNativeHook();
  } catch (e) {
    pushError('native hook', e);
    installJsHook();
  }
  return hooked;
}

/**
 * 统一入口：切换时间流速（原生模式走 C，回退模式走 JS 锚点）。
 * 注意：这里必须先用“旧 rate”计算锚点虚拟时间，再改 st.rate，
 * 否则切换瞬间虚拟时间会倒跳（旧 rate 的累计滞后），引擎会报
 * "Detected negative delta time" 并 Fatal。
 */
function applyRate(rate) {
  if (hookMode === 'native' && swsSetRate !== null) {
    swsSetRate(rate);
    st.rate = rate;
  } else {
    switchRateJs(rate); // 内部先按旧 st.rate 求当前虚拟时间，再改 st.rate
  }
}

function getHits() {
  if (hookMode === 'native' && swsHits !== null) {
    try {
      return Math.round(swsHits());
    } catch (e) {
      return 0;
    }
  }
  return hookCount;
}

// ------------------------------------------------------------------ 热键
var GetAsyncKeyState = nativeFn('user32.dll', 'GetAsyncKeyState', 'int16', ['int']);
var GetForegroundWindow = nativeFn('user32.dll', 'GetForegroundWindow', 'pointer', []);
var GetWindowThreadProcessId = nativeFn('user32.dll', 'GetWindowThreadProcessId', 'uint32', ['pointer', 'pointer']);
var MessageBeep = nativeFn('user32.dll', 'MessageBeep', 'int', ['uint32']);
var fgPidBuf = Memory.alloc(4);

function keyDown(vk) {
  if (GetAsyncKeyState === null) return false;
  return (GetAsyncKeyState(vk) & 0x8000) !== 0;
}

function isGameForeground() {
  if (GetForegroundWindow === null || GetWindowThreadProcessId === null) return true;
  try {
    var hwnd = GetForegroundWindow();
    if (hwnd === null || hwnd.isNull()) return false;
    GetWindowThreadProcessId(hwnd, fgPidBuf);
    return fgPidBuf.readUInt32() === Process.id;
  } catch (e) {
    return true;
  }
}

function modsOk() {
  var hk = cfg.hotkey;
  if (hk.ctrl && !keyDown(0x11)) return false;
  if (hk.alt && !keyDown(0x12)) return false;
  if (hk.shift && !keyDown(0x10)) return false;
  if (hk.win && !keyDown(0x5B) && !keyDown(0x5C)) return false;
  return true;
}

function pollHotkey() {
  try {
    if (GetAsyncKeyState === null) return;
    var vk = cfg.hotkey.vk | 0;
    if (vk <= 0) return;
    var down = keyDown(vk);
    if (down && !hotkeyWasDown && modsOk()) {
      if (!cfg.requireForeground || isGameForeground()) {
        applyEnabled(!st.enabled, 'hotkey');
      }
    }
    hotkeyWasDown = down;
  } catch (e) {
    pushError('hotkey poll', e);
  }
}

// ------------------------------------------------------------------ 状态/切换
function stateSnapshot() {
  var realNow = readReal();
  var virtNow = null;
  var lagSec = null;
  if (realNow !== null) {
    virtNow = (hookMode === 'js' || !st.anchored) ? mapVirtual(realNow) : null;
    if (virtNow !== null && st.anchored) lagSec = (realNow - virtNow) / FREQ;
  }
  return {
    version: VERSION,
    enabled: st.enabled,
    speed: st.speed,
    rate: st.rate,
    freq: FREQ,
    hookCount: getHits(),
    hooked: hooked,
    hookMode: hookMode,
    catchup: st.catchup,
    lagSec: lagSec,
    hookAddr: (qpcAddr === null) ? null : qpcAddr.toString(),
    realNow: realNow,
    virtualNow: virtNow,
    pid: Process.id,
    arch: Process.arch,
    hotkey: { vk: cfg.hotkey.vk, ctrl: cfg.hotkey.ctrl, alt: cfg.hotkey.alt, shift: cfg.hotkey.shift },
    errors: errors.slice(-10)
  };
}

/**
 * 卸载前“追平”：让虚拟时间以更快的流速赶上真实时间，追上后转为恒等映射，
 * 这样再摘除 QPC 钩子不会出现时间倒跳（否则引擎会报 negative delta time）。
 */
function beginCatchUp(durationSec) {
  if (hookMode !== 'js') {
    return { catchup: false, lag: null, note: 'native mode' };
  }
  var r = readReal();
  if (r === null) return { catchup: false, lag: null, error: 'readReal failed' };
  var v = mapVirtual(r);
  var lag = r - v;
  if (lag <= Math.round(FREQ * 0.002)) {
    st.catchup = false;
    st.anchorReal = r;
    st.anchorVirt = r;
    st.rate = 1.0;
    st.enabled = false;
    return { catchup: false, lag: 0 };
  }
  var dur = durationSec || 2.0;
  var rate = 1.0 + lag / (dur * FREQ);
  if (rate > 30.0) rate = 30.0;
  st.catchup = true;
  st.enabled = false;
  st.anchorReal = r;
  st.anchorVirt = v;
  st.rate = rate;
  return { catchup: true, lag: lag / FREQ, rate: rate };
}

function applyEnabled(on, source) {
  on = !!on;
  st.enabled = on;
  st.speed = clampSpeed(st.speed);
  applyRate(on ? st.speed : 1.0);
  if (cfg.beep && MessageBeep !== null) {
    try { MessageBeep(0xFFFFFFFF); } catch (e) { /* ignore */ }
  }
  send({
    type: 'toggle',
    enabled: st.enabled,
    speed: st.speed,
    source: source || 'rpc',
    hookCount: getHits(),
    hookMode: hookMode
  });
  return stateSnapshot();
}

// ------------------------------------------------------------------ 配置
function normalizeHotkey(h) {
  h = h || {};
  return {
    vk: (h.vk | 0) || 0x77,
    ctrl: !!h.ctrl,
    alt: !!h.alt,
    shift: !!h.shift,
    win: !!h.win
  };
}

function configure(c) {
  c = c || {};
  if (typeof c.speed !== 'undefined') st.speed = clampSpeed(c.speed);
  if (typeof c.beep !== 'undefined') cfg.beep = !!c.beep;
  if (typeof c.requireForeground !== 'undefined') cfg.requireForeground = !!c.requireForeground;
  if (c.hotkey) cfg.hotkey = normalizeHotkey(c.hotkey);
  cfg.speed = st.speed;
  if (st.enabled) applyRate(st.speed);
  return stateSnapshot();
}

// ------------------------------------------------------------------ RPC
rpc.exports = {
  version: function () {
    return VERSION;
  },
  configure: function (c) {
    return configure(c);
  },
  state: function () {
    return stateSnapshot();
  },
  setEnabled: function (on) {
    return applyEnabled(on, 'rpc');
  },
  toggle: function () {
    return applyEnabled(!st.enabled, 'rpc');
  },
  setSpeed: function (s) {
    st.speed = clampSpeed(s);
    if (st.enabled) applyRate(st.speed);
    return stateSnapshot();
  },
  /**
   * 诊断用：在本进程内比较“真实时间”与“经过钩子的 QPC 返回值”的流速，
   * 用于确认钩子确实在缩放调用者看到的时间（正常应接近 rate）。
   * 会忙等约 0.2 秒。
   */
  selfTest: function () {
    var r1 = readReal();
    var s1 = null;
    if (qpcFn !== null) {
      try {
        qpcFn(probeBuf);
        s1 = probeBuf.readU64().toNumber();
      } catch (e) {
        pushError('selfTest qpc1', e);
      }
    }
    if (r1 === null) return { error: 'readReal failed' };
    var target = r1 + Math.round(FREQ * 0.2);
    var guard = 0;
    var rNow = r1;
    while (guard++ < 100000) {
      rNow = readReal();
      if (rNow === null || rNow >= target) break;
    }
    var r2 = readReal();
    var s2 = null;
    if (qpcFn !== null) {
      try {
        qpcFn(probeBuf);
        s2 = probeBuf.readU64().toNumber();
      } catch (e) {
        pushError('selfTest qpc2', e);
      }
    }
    var realDelta = (r2 === null || r1 === null) ? null : (r2 - r1);
    var virtDelta = (s1 === null || s2 === null) ? null : (s2 - s1);
    return {
      ratio: (realDelta && virtDelta !== null) ? virtDelta / realDelta : null,
      rate: st.rate,
      enabled: st.enabled,
      speed: st.speed,
      hookMode: hookMode,
      realDelta: realDelta,
      virtDelta: virtDelta,
      freq: FREQ,
      hookCount: getHits()
    };
  },
  restore: function () {
    return applyEnabled(false, 'restore');
  },
  /**
   * 卸载前调用：先关闭慢动作，再以“快进”方式把虚拟时间追平真实时间。
   * Python 侧轮询 state().catchup / lagSec，追平后再 shutdown 摘钩子，
   * 这样不会出现时间倒跳（负 delta）。
   */
  prepareUnload: function () {
    if (st.enabled) applyEnabled(false, 'unload');
    beginCatchUp(2.0);
    return stateSnapshot();
  },
  /** 兼容旧测试脚本：返回当前状态里的“虚拟时间流速”。 */
  shutdown: function () {
    try {
      if (hotkeyTimer !== null) {
        clearInterval(hotkeyTimer);
        hotkeyTimer = null;
      }
    } catch (e) { pushError('clear hotkey timer', e); }
    try {
      if (hookMode === 'native' && qpcAddr !== null) {
        Interceptor.revert(qpcAddr);
      } else {
        Interceptor.detachAll();
      }
      hooked = false;
    } catch (e) { pushError('revert hook', e); }
    return true;
  }
};

// ------------------------------------------------------------------ 启动
installHook();
try {
  hotkeyTimer = setInterval(pollHotkey, 40);
} catch (e) {
  pushError('setInterval', e);
}

send({
  type: 'ready',
  version: VERSION,
  pid: Process.id,
  arch: Process.arch,
  hooked: hooked,
  hookMode: hookMode,
  hookAddr: (qpcAddr === null) ? null : qpcAddr.toString(),
  freq: FREQ,
  errors: errors.slice(-5)
});
