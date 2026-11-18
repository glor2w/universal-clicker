"""Универсальный кликер: автокликер для мыши и клавиатуры (Windows).

Режимы:
  * клик     — кнопка/клавиша нажимается с заданной частотой;
  * зажатие  — кнопка/клавиша удерживается до остановки.

Глобальный старт/стоп — F6.
"""

from __future__ import annotations

import ctypes
import json
import os
import sys
import threading
import time
import tkinter as tk
import webbrowser
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from tkinter import ttk

APP_VERSION = "1.1.0"
REPO_URL = "https://github.com/glor2w/universal-clicker"
DEFAULT_LANGUAGE = "en"
LANGUAGE_NAMES = {"en": "English", "ru": "Русский"}
SETTINGS_FILE = (
    Path(os.environ.get("APPDATA", str(Path.home())))
    / "UniversalClicker"
    / "settings.json"
)


def resource_path(*parts: str) -> str:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return str(base.joinpath(*parts))


def load_language() -> str:
    try:
        data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return DEFAULT_LANGUAGE
    language = data.get("language")
    return language if language in LANGUAGE_NAMES else DEFAULT_LANGUAGE


def save_language(language: str) -> None:
    try:
        SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS_FILE.write_text(
            json.dumps({"language": language}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError:
        pass

# --- Win32 -----------------------------------------------------------------

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

INPUT_MOUSE = 0
INPUT_KEYBOARD = 1

MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040

KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008

MOUSE_FLAGS = {
    "left": (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP),
    "right": (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP),
    "middle": (MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP),
}

# Клавиши, для которых Windows требует флаг extended.
EXTENDED_VKS = {
    0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28,  # pgup/pgdn/end/home/arrows
    0x2C, 0x2D, 0x2E,                                # print screen/insert/delete
    0x5C, 0x5D, 0x6F, 0x90, 0xA3, 0xA5,              # rwin/apps/numdiv/numlock/rctrl/ralt
    0xAD, 0xAE, 0xAF, 0xB0, 0xB1, 0xB2, 0xB3,        # media keys
}

VK_F6 = 0x75
VK_LWIN = 0x5B
VK_RWIN = 0x5C
BLOCKED_VKS = {VK_F6, VK_LWIN, VK_RWIN}

HOTKEY_ID = 1
WM_HOTKEY = 0x0312
MOD_NOREPEAT = 0x4000

ULONG_PTR = ctypes.c_size_t


class MOUSEINPUT(ctypes.Structure):
    _fields_ = (
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    )


class KEYBDINPUT(ctypes.Structure):
    _fields_ = (
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    )


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = (
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    )


class INPUT(ctypes.Structure):
    class _INPUT_UNION(ctypes.Union):
        _fields_ = (
            ("mi", MOUSEINPUT),
            ("ki", KEYBDINPUT),
            ("hi", HARDWAREINPUT),
        )

    _anonymous_ = ("u",)
    _fields_ = (
        ("type", wintypes.DWORD),
        ("u", _INPUT_UNION),
    )


INPUT_SIZE = ctypes.sizeof(INPUT)

user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
user32.SendInput.restype = wintypes.UINT
user32.MapVirtualKeyW.argtypes = (wintypes.UINT, wintypes.UINT)
user32.MapVirtualKeyW.restype = wintypes.UINT
user32.RegisterHotKey.argtypes = (wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT)
user32.RegisterHotKey.restype = wintypes.BOOL
user32.UnregisterHotKey.argtypes = (wintypes.HWND, ctypes.c_int)
user32.UnregisterHotKey.restype = wintypes.BOOL
user32.GetMessageW.argtypes = (ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT)
user32.GetMessageW.restype = ctypes.c_int


def _mouse_input(flag: int) -> INPUT:
    event = INPUT()
    event.type = INPUT_MOUSE
    event.mi.dx = 0
    event.mi.dy = 0
    event.mi.mouseData = 0
    event.mi.dwFlags = flag
    event.mi.time = 0
    event.mi.dwExtraInfo = 0
    return event


def _key_input(vk: int, key_up: bool) -> INPUT:
    scan = user32.MapVirtualKeyW(vk, 0)
    event = INPUT()
    event.type = INPUT_KEYBOARD
    event.ki.time = 0
    event.ki.dwExtraInfo = 0
    if scan:
        flags = KEYEVENTF_SCANCODE
        if key_up:
            flags |= KEYEVENTF_KEYUP
        if vk in EXTENDED_VKS:
            flags |= KEYEVENTF_EXTENDEDKEY
        event.ki.wVk = 0
        event.ki.wScan = scan
        event.ki.dwFlags = flags
    else:
        event.ki.wVk = vk
        event.ki.wScan = 0
        event.ki.dwFlags = KEYEVENTF_KEYUP if key_up else 0
    return event


@dataclass(frozen=True)
class Settings:
    target: str              # mouse | keyboard
    mode: str                # click | hold
    mouse_button: str = "left"
    vk: int = 0x20
    cps: float = 100.0       # 0 — без ограничения
    press_ms: float = 0.0


class ClickerEngine:
    """Отправляет ввод в отдельных потоках; снаружи читается через snapshot()."""

    def __init__(self) -> None:
        self._threads: list[threading.Thread] = []
        self._monitor: threading.Thread | None = None
        self._stop = threading.Event()
        self._running = False
        self._counts: list[int] = []
        self._t0 = 0.0
        self._error = ""

    def snapshot(self) -> tuple[bool, int, float, str]:
        return self._running, sum(self._counts), self._t0, self._error

    def start(self, settings: Settings | list[Settings]) -> bool:
        if self._running:
            return False
        configs = [settings] if isinstance(settings, Settings) else list(settings)
        if not configs:
            return False
        self._stop.clear()
        self._error = ""
        self._counts = [0] * len(configs)
        self._t0 = time.perf_counter()
        self._running = True
        self._threads = [
            threading.Thread(
                target=self._run,
                args=(config, index),
                daemon=True,
                name=f"clicker-{config.target}",
            )
            for index, config in enumerate(configs)
        ]
        for thread in self._threads:
            thread.start()
        self._monitor = threading.Thread(
            target=self._watch, daemon=True, name="clicker-watch"
        )
        self._monitor.start()
        return True

    def stop(self) -> None:
        self._stop.set()
        current = threading.current_thread()
        for thread in self._threads:
            if thread.is_alive() and thread is not current:
                thread.join(timeout=1.0)
        monitor = self._monitor
        if monitor and monitor.is_alive() and monitor is not current:
            monitor.join(timeout=1.0)

    # -- worker -------------------------------------------------------------

    def _watch(self) -> None:
        for thread in list(self._threads):
            thread.join()
        self._running = False

    def _set_count(self, index: int, value: int) -> None:
        self._counts[index] = value

    def _run(self, settings: Settings, index: int) -> None:
        try:
            if settings.mode == "hold":
                self._run_hold(settings, index)
            else:
                self._run_clicks(settings, index)
        except Exception as exc:  # noqa: BLE001 - ошибка показывается в GUI
            if not self._error:
                self._error = str(exc) or exc.__class__.__name__
            self._stop.set()

    @staticmethod
    def _send(events, count: int) -> None:
        if user32.SendInput(count, events, INPUT_SIZE) != count:
            code = ctypes.get_last_error()
            if code:
                raise OSError(f"SendInput: {ctypes.FormatError(code)}")
            raise RuntimeError("input_blocked")

    def _edge_events(self, settings: Settings):
        if settings.target == "mouse":
            down_flag, up_flag = MOUSE_FLAGS[settings.mouse_button]
            down = (INPUT * 1)(_mouse_input(down_flag))
            up = (INPUT * 1)(_mouse_input(up_flag))
        else:
            down = (INPUT * 1)(_key_input(settings.vk, False))
            up = (INPUT * 1)(_key_input(settings.vk, True))
        return down, up

    def _run_hold(self, settings: Settings, index: int) -> None:
        down, up = self._edge_events(settings)
        self._send(down, 1)
        self._set_count(index, 1)
        try:
            self._stop.wait()
        finally:
            self._send(up, 1)

    def _run_clicks(self, settings: Settings, index: int) -> None:
        send = self._send
        stop_set = self._stop.is_set
        if settings.target == "mouse":
            down_flag, up_flag = MOUSE_FLAGS[settings.mouse_button]
            down_event = _mouse_input(down_flag)
            up_event = _mouse_input(up_flag)
        else:
            down_event = _key_input(settings.vk, False)
            up_event = _key_input(settings.vk, True)

        hold = max(0.0, settings.press_ms) / 1000.0
        period = 1.0 / settings.cps if settings.cps > 0 else 0.0

        if period == 0 and hold == 0:
            # MAX: пачка кликов одним SendInput — так Windows принимает ввод
            # заметно быстрее, чем по одному вызову на клик.
            batch = 16
            events = (INPUT * (batch * 2))(*([down_event, up_event] * batch))
            total = batch * 2
            count = 0
            while not stop_set():
                send(events, total)
                count += batch
                self._set_count(index, count)
            self._set_count(index, count)
            return

        if hold > 0:
            down = (INPUT * 1)(down_event)
            up = (INPUT * 1)(up_event)

            def action() -> None:
                send(down, 1)
                try:
                    self._hold_wait(hold)
                finally:
                    send(up, 1)
        else:
            pair = (INPUT * 2)(down_event, up_event)

            def action() -> None:
                send(pair, 2)

        count = 0
        next_at = time.perf_counter()
        while not stop_set():
            action()
            count += 1
            self._set_count(index, count)
            if period:
                next_at += period
                now = time.perf_counter()
                if next_at <= now:
                    next_at = now
                else:
                    self._wait_until(next_at)
        self._set_count(index, count)

    def _hold_wait(self, seconds: float) -> None:
        deadline = time.perf_counter() + seconds
        while not self._stop.is_set():
            left = deadline - time.perf_counter()
            if left <= 0:
                return
            if left > 0.0015:
                time.sleep(min(left - 0.001, 0.005))

    def _wait_until(self, deadline: float) -> None:
        while not self._stop.is_set():
            left = deadline - time.perf_counter()
            if left <= 0:
                return
            if left > 0.0015:
                time.sleep(min(left - 0.001, 0.01))


class GlobalHotkey(threading.Thread):
    """Глобальный F6 через RegisterHotKey (работает без фокуса окна)."""

    def __init__(self, callback) -> None:
        super().__init__(daemon=True, name="hotkey")
        self.callback = callback
        self.ready = threading.Event()
        self.registered = False

    def run(self) -> None:
        try:
            self.registered = bool(
                user32.RegisterHotKey(None, HOTKEY_ID, MOD_NOREPEAT, VK_F6)
            )
        finally:
            self.ready.set()
        if not self.registered:
            return
        try:
            msg = wintypes.MSG()
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == WM_HOTKEY and msg.wParam == HOTKEY_ID:
                    try:
                        self.callback()
                    except Exception:
                        pass
        finally:
            user32.UnregisterHotKey(None, HOTKEY_ID)


# --- UI --------------------------------------------------------------------

BG = "#0f1117"
CARD = "#191c24"
CARD_ALT = "#232834"
CARD_HOVER = "#2a3040"
FG = "#eef1f7"
MUTED = "#8b93a7"
ACCENT = "#4f7cff"
ACCENT_HOVER = "#6b90ff"
DANGER = "#ff5566"
DANGER_HOVER = "#ff7583"
OK = "#3ddc84"
FONT = "Segoe UI"

STRINGS = {
    "en": {
        "app_title": "Universal Clicker",
        "subtitle": "Mouse & keyboard  ·  global start with F6",
        "tab_single": "Single",
        "tab_combo": "Combo",
        "section_target": "1  ·  TARGET",
        "target_mouse": "Mouse",
        "target_keyboard": "Keyboard",
        "mouse_button": "Mouse button",
        "key": "Key",
        "change": "change",
        "section_mode": "2  ·  MODE",
        "mode_click": "Click",
        "mode_hold": "Hold",
        "mode_desc_click": "The button or key is pressed and released immediately. Frequency is set below.",
        "mode_desc_hold": "The button or key is held until F6 is pressed again or Stop is clicked.",
        "section_speed": "3  ·  SPEED",
        "frequency": "Frequency",
        "per_second": "per second",
        "per_second_short": "/s",
        "press_hold": "Press duration",
        "press_short": "Press",
        "ms": "ms",
        "hint_speed_click": "MAX — unlimited. The real limit depends on Windows and system load.",
        "hint_press_click": "0 — no delay between press and release. For games, try 10–30 ms.",
        "hint_speed_hold": "In Hold mode, frequency and hold time are not used.",
        "hint_press_hold": "",
        "combo_intro": "Both channels run at the same time and independently. Each has its own mode, frequency and hold time.",
        "combo_mouse": "MOUSE",
        "combo_keyboard": "KEYBOARD",
        "combo_stop_hint": "Stop both channels with the Stop button or F6.",
        "combo_hint_click": "Clicks at the selected frequency. Hold time is the pause between press and release.",
        "combo_hint_hold": "The button is held until stop. Frequency and hold time are not used.",
        "start": "START",
        "stop": "STOP",
        "status_stopped": "STOPPED",
        "status_running": "RUNNING",
        "status_running_combo": "RUNNING · 2 CHANNELS",
        "status_holding": "HOLDING",
        "rate_clicks": "{rate} clicks/s",
        "footer_note": "F6 works globally, even when the window is minimized. If a game does not accept input, run the clicker as administrator.",
        "status_f6_busy": "F6 IS BUSY — PRESS START",
        "status_f6_reserved": "F6 IS RESERVED — CHOOSE ANOTHER KEY",
        "status_win_key": "WINDOWS KEY IS NOT SUPPORTED",
        "capture_wait": "Press a key…",
        "status_key": "KEY: {name}",
        "status_already_running": "CLICKER IS ALREADY RUNNING",
        "status_error": "Error: {message}",
        "status_language": "LANGUAGE: {language}",
        "error_speed_number": "Enter the frequency as a number or MAX.",
        "error_speed_range": "Frequency must be from 1 to 1,000,000.",
        "error_press_number": "Hold time must be a number of milliseconds.",
        "error_press_range": "Hold time: 0 to 500 ms.",
        "error_other_key": "Choose another key.",
        "error_input_blocked": "Windows blocked the input. Run the clicker as administrator.",
        "prefix_mouse": "Mouse",
        "prefix_keyboard": "Keyboard",
        "speed_max": "MAX",
        "settings": "Settings",
        "settings_title": "Settings",
        "settings_language": "Language",
        "settings_language_hint": "Changes apply immediately and are saved automatically.",
        "settings_about": "ABOUT",
        "settings_about_text": "Universal Clicker v{version}\nMouse and keyboard auto-clicker for Windows.",
        "settings_github": "Open on GitHub",
        "settings_close": "Close",
    },
    "ru": {
        "app_title": "Универсальный кликер",
        "subtitle": "Мышь и клавиатура  ·  глобальный старт по F6",
        "tab_single": "Одиночный",
        "tab_combo": "Комбо",
        "section_target": "1  ·  ЦЕЛЬ",
        "target_mouse": "Мышь",
        "target_keyboard": "Клавиатура",
        "mouse_button": "Кнопка мыши",
        "key": "Клавиша",
        "change": "сменить",
        "section_mode": "2  ·  РЕЖИМ",
        "mode_click": "Клик",
        "mode_hold": "Зажатие",
        "mode_desc_click": "Кнопка или клавиша нажимается и сразу отпускается. Частота задаётся ниже.",
        "mode_desc_hold": "Кнопка или клавиша зажимается и держится до повторного F6 или кнопки «Стоп».",
        "section_speed": "3  ·  СКОРОСТЬ",
        "frequency": "Частота",
        "per_second": "в секунду",
        "per_second_short": "/с",
        "press_hold": "Удержание нажатия",
        "press_short": "Удержание",
        "ms": "мс",
        "hint_speed_click": "МАКС — без ограничения. Фактический предел зависит от Windows и нагрузки.",
        "hint_press_click": "0 — нажатие без задержки. Для игр попробуйте 10–30 мс.",
        "hint_speed_hold": "В режиме «Зажатие» частота и удержание не используются.",
        "hint_press_hold": "",
        "combo_intro": "Оба канала работают одновременно и независимо. У каждого свой режим, частота и удержание.",
        "combo_mouse": "МЫШЬ",
        "combo_keyboard": "КЛАВИАТУРА",
        "combo_stop_hint": "Остановить оба канала: кнопка «Стоп» или F6.",
        "combo_hint_click": "Клики с заданной частотой. Удержание — пауза между нажатием и отпусканием.",
        "combo_hint_hold": "Нажатие удерживается до остановки. Частота и удержание не используются.",
        "start": "СТАРТ",
        "stop": "ОСТАНОВИТЬ",
        "status_stopped": "ОСТАНОВЛЕН",
        "status_running": "РАБОТАЕТ",
        "status_running_combo": "РАБОТАЕТ · 2 КАНАЛА",
        "status_holding": "УДЕРЖАНИЕ",
        "rate_clicks": "{rate} нажатий/с",
        "footer_note": "F6 работает глобально, даже если окно свёрнуто. Если игра не принимает ввод — запустите кликер от имени администратора.",
        "status_f6_busy": "F6 ЗАНЯТА — ЖМИТЕ «СТАРТ»",
        "status_f6_reserved": "F6 ЗАНЯТА — ВЫБЕРИТЕ ДРУГУЮ КЛАВИШУ",
        "status_win_key": "КЛАВИША WINDOWS НЕ ПОДДЕРЖИВАЕТСЯ",
        "capture_wait": "Нажмите клавишу…",
        "status_key": "КЛАВИША: {name}",
        "status_already_running": "КЛИКЕР УЖЕ РАБОТАЕТ",
        "status_error": "Ошибка: {message}",
        "status_language": "ЯЗЫК: {language}",
        "error_speed_number": "Введите частоту числом или «МАКС».",
        "error_speed_range": "Частота должна быть от 1 до 1 000 000.",
        "error_press_number": "Удержание задаётся числом миллисекунд.",
        "error_press_range": "Удержание: от 0 до 500 мс.",
        "error_other_key": "Выберите другую клавишу.",
        "error_input_blocked": "Windows заблокировал ввод. Запустите кликер от имени администратора.",
        "prefix_mouse": "Мышь",
        "prefix_keyboard": "Клавиатура",
        "speed_max": "МАКС",
        "settings": "Настройки",
        "settings_title": "Настройки",
        "settings_language": "Язык",
        "settings_language_hint": "Изменения применяются сразу и сохраняются автоматически.",
        "settings_about": "О ПРИЛОЖЕНИИ",
        "settings_about_text": "Universal Clicker v{version}\nАвтокликер для мыши и клавиатуры под Windows.",
        "settings_github": "Открыть на GitHub",
        "settings_close": "Закрыть",
    },
}


def tr(lang: str, key: str, **kwargs) -> str:
    table = STRINGS.get(lang, STRINGS[DEFAULT_LANGUAGE])
    text = table.get(key, STRINGS[DEFAULT_LANGUAGE].get(key, key))
    return text.format(**kwargs) if kwargs else text


MOUSE_LABELS = {
    "en": {"left": "Left", "right": "Right", "middle": "Middle"},
    "ru": {"left": "Левая", "right": "Правая", "middle": "Средняя"},
}

SPEED_PRESETS = ("10", "25", "50", "100", "200", "500", "1000", "2500", "5000")
MAX_VALUES = {"MAX", "МАКС", "МАКСИМУМ", "∞"}

KEY_LABELS_EN = {
    "space": "SPACE",
    "return": "ENTER",
    "escape": "ESC",
    "backspace": "BACKSPACE",
    "tab": "TAB",
    "caps_lock": "CAPS LOCK",
    "shift_l": "SHIFT",
    "shift_r": "SHIFT",
    "control_l": "CTRL",
    "control_r": "CTRL",
    "alt_l": "ALT",
    "alt_r": "ALT",
    "super_l": "WIN",
    "super_r": "WIN",
    "menu": "MENU",
    "left": "LEFT",
    "right": "RIGHT",
    "up": "UP",
    "down": "DOWN",
    "prior": "PAGE UP",
    "next": "PAGE DOWN",
    "home": "HOME",
    "end": "END",
    "insert": "INSERT",
    "delete": "DELETE",
    "print": "PRINT SCREEN",
    "scroll_lock": "SCROLL LOCK",
    "num_lock": "NUM LOCK",
}

KEY_LABELS = {
    "en": KEY_LABELS_EN,
    "ru": {**KEY_LABELS_EN, "space": "ПРОБЕЛ"},
}


def key_label(language: str, keysym: str) -> str:
    labels = KEY_LABELS.get(language, KEY_LABELS[DEFAULT_LANGUAGE])
    return labels.get(keysym.lower(), keysym.upper())


class UniversalClickerApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.language = load_language()
        self.title(tr(self.language, "app_title"))
        self.configure(bg=BG)
        try:
            self.iconbitmap(resource_path("assets", "icon.ico"))
        except tk.TclError:
            pass
        self.option_add("*TCombobox*Listbox.background", CARD_ALT)
        self.option_add("*TCombobox*Listbox.foreground", FG)
        self.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
        self.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")

        self.engine = ClickerEngine()
        self.running = False
        self.active_tab = "single"
        self.target = "mouse"
        self.mode = "click"
        self.mouse_button = "left"
        self._active: Settings | list[Settings] | None = None
        self._capturing = False
        self._capture_context = "single"
        self._key_vk = 0x20
        self._key_sym = "space"
        self._toggle_requested = threading.Event()
        self._target_buttons: dict[str, tk.Button] = {}
        self._mode_buttons: dict[str, tk.Button] = {}
        self._rate_count = 0
        self._rate_time = 0.0

        self.combo_mode = {"mouse": "click", "keyboard": "click"}
        self.combo_mode_buttons: dict[str, dict[str, tk.Button]] = {
            "mouse": {},
            "keyboard": {},
        }
        self.combo_hints: dict[str, tk.Label] = {}
        self.combo_speed: dict[str, ttk.Combobox] = {}
        self.combo_press: dict[str, tk.Entry] = {}
        self._combo_key_vk = 0x20
        self._combo_key_sym = "space"
        self.combo_mouse_button = "left"
        self.button_var = tk.StringVar()
        self.speed_var = tk.StringVar(value="100")
        self.press_var = tk.StringVar(value="0")
        self.combo_button_var = tk.StringVar()
        self.combo_mouse_speed_var = tk.StringVar(value="100")
        self.combo_mouse_press_var = tk.StringVar(value="0")
        self.combo_key_speed_var = tk.StringVar(value="100")
        self.combo_key_press_var = tk.StringVar(value="0")
        self._settings_window: tk.Toplevel | None = None

        self._build_style()
        self._build_ui()
        self._sync_target()
        self._sync_mode()
        self._sync_combo("mouse")
        self._sync_combo("keyboard")

        self._hotkey = GlobalHotkey(self._request_toggle)
        self._hotkey.start()
        self._fit_and_center()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(60, self._poll_hotkey)
        self.after(120, self._tick)
        self.after(400, self._check_hotkey)
        self.after(50, self._apply_dark_titlebar)

    def _t(self, key: str, **kwargs) -> str:
        return tr(self.language, key, **kwargs)

    def _mouse_label(self, code: str) -> str:
        return MOUSE_LABELS[self.language].get(code, code)

    def _mouse_code(self, label: str) -> str:
        for code, text in MOUSE_LABELS[self.language].items():
            if text == label:
                return code
        return "left"

    def _key_display(self, combo: bool = False) -> str:
        sym = self._combo_key_sym if combo else self._key_sym
        return key_label(self.language, sym)

    def _speed_values(self) -> tuple[str, ...]:
        return (*SPEED_PRESETS, self._t("speed_max"))

    def _localized_speed_value(self, value: str) -> str:
        return self._t("speed_max") if value.strip().upper() in MAX_VALUES else value

    def _format_count(self, value: float) -> str:
        text = f"{value:,.0f}"
        return text.replace(",", " ") if self.language == "ru" else text

    # -- construction -------------------------------------------------------

    def _build_style(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(
            "Dark.TCombobox",
            fieldbackground=CARD_ALT,
            background=CARD_ALT,
            foreground=FG,
            arrowcolor=MUTED,
            bordercolor=CARD_ALT,
            lightcolor=CARD_ALT,
            darkcolor=CARD_ALT,
            selectbackground=CARD_ALT,
            selectforeground=FG,
            padding=6,
            relief="flat",
        )
        style.map(
            "Dark.TCombobox",
            fieldbackground=[("readonly", CARD_ALT), ("disabled", CARD_ALT)],
            foreground=[("readonly", FG), ("disabled", "#596173")],
            background=[("active", CARD_ALT)],
            arrowcolor=[("disabled", "#4a5161")],
        )
        style.configure(
            "Dark.TNotebook",
            background=BG,
            bordercolor=BG,
            lightcolor=BG,
            darkcolor=BG,
            borderwidth=0,
            tabmargins=(0, 0, 0, 0),
        )
        style.configure(
            "Dark.TNotebook.Tab",
            background=CARD_ALT,
            foreground=MUTED,
            bordercolor=CARD_ALT,
            lightcolor=CARD_ALT,
            darkcolor=CARD_ALT,
            padding=(20, 8),
            borderwidth=0,
            focuscolor=BG,
            font=(FONT, 10, "bold"),
        )
        style.map(
            "Dark.TNotebook.Tab",
            background=[
                ("selected", CARD),
                ("active", CARD_HOVER),
                ("disabled", BG),
            ],
            foreground=[
                ("selected", FG),
                ("active", FG),
                ("disabled", "#4a5161"),
            ],
            bordercolor=[("selected", CARD), ("active", CARD_HOVER)],
            lightcolor=[("selected", CARD), ("active", CARD_HOVER)],
            darkcolor=[("selected", CARD), ("active", CARD_HOVER)],
        )
        style.layout(
            "Dark.TNotebook.Tab",
            [
                (
                    "Notebook.tab",
                    {
                        "sticky": "nswe",
                        "children": [
                            (
                                "Notebook.padding",
                                {
                                    "sticky": "nswe",
                                    "children": [
                                        ("Notebook.label", {"sticky": "nswe"})
                                    ],
                                },
                            )
                        ],
                    },
                )
            ],
        )

    def _build_ui(self) -> None:
        outer = tk.Frame(self, bg=BG)
        outer.pack(fill="both", expand=True, padx=26, pady=22)

        header = tk.Frame(outer, bg=BG)
        header.pack(fill="x")
        try:
            self._logo_image = tk.PhotoImage(
                file=resource_path("assets", "icon.png")
            ).subsample(24, 24)
            tk.Label(header, image=self._logo_image, bg=BG).pack(
                side="left", padx=(0, 12)
            )
        except tk.TclError:
            self._logo_image = None

        title_box = tk.Frame(header, bg=BG)
        title_box.pack(side="left", fill="x", expand=True)
        tk.Label(
            title_box,
            text=self._t("app_title"),
            bg=BG,
            fg=FG,
            font=(FONT, 20, "bold"),
            anchor="w",
        ).pack(fill="x")
        tk.Label(
            title_box,
            text=self._t("subtitle"),
            bg=BG,
            fg=MUTED,
            font=(FONT, 9),
            anchor="w",
        ).pack(fill="x", pady=(3, 0))

        self.settings_btn = tk.Button(
            header,
            text=f"⚙  {self._t('settings')}",
            command=self._open_settings,
            bg=CARD_ALT,
            fg=FG,
            activebackground=CARD_HOVER,
            activeforeground=FG,
            relief="flat",
            bd=0,
            cursor="hand2",
            font=(FONT, 9, "bold"),
            padx=10,
            pady=7,
        )
        self.settings_btn.pack(side="right", anchor="n", padx=(8, 0))
        self._bind_hover(self.settings_btn, CARD_ALT, CARD_HOVER)

        self.notebook = ttk.Notebook(outer, style="Dark.TNotebook", takefocus=False)
        self.notebook.pack(fill="both", expand=True, pady=(16, 0))
        self.single_page = tk.Frame(self.notebook, bg=BG)
        self.combo_page = tk.Frame(self.notebook, bg=BG)
        self.notebook.add(self.single_page, text=self._t("tab_single"))
        self.notebook.add(self.combo_page, text=self._t("tab_combo"))
        self.notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)

        body = tk.Frame(self.single_page, bg=BG)
        body.pack(fill="both", expand=True, padx=12, pady=12)

        self._section(body, self._t("section_target")).pack(fill="x")
        target_row = tk.Frame(body, bg=BG)
        target_row.pack(fill="x", pady=(8, 0))
        self._target_buttons["mouse"] = self._choice(
            target_row, self._t("target_mouse"), lambda: self._choose_target("mouse")
        )
        self._target_buttons["keyboard"] = self._choice(
            target_row,
            self._t("target_keyboard"),
            lambda: self._choose_target("keyboard"),
        )
        self._target_buttons["mouse"].pack(
            side="left", fill="x", expand=True, padx=(0, 4)
        )
        self._target_buttons["keyboard"].pack(
            side="left", fill="x", expand=True, padx=(4, 0)
        )

        self.detail = tk.Frame(body, bg=CARD)
        self.detail.pack(fill="x", pady=(8, 0))

        self.mouse_pane = tk.Frame(self.detail, bg=CARD)
        mouse_row = tk.Frame(self.mouse_pane, bg=CARD)
        mouse_row.pack(fill="x", padx=14, pady=11)
        tk.Label(
            mouse_row,
            text=self._t("mouse_button"),
            bg=CARD,
            fg=MUTED,
            font=(FONT, 9),
        ).pack(side="left")
        self.button_var.set(self._mouse_label(self.mouse_button))
        self.button_combo = ttk.Combobox(
            mouse_row,
            textvariable=self.button_var,
            values=tuple(MOUSE_LABELS[self.language].values()),
            state="readonly",
            width=11,
            style="Dark.TCombobox",
            font=(FONT, 10),
        )
        self.button_combo.pack(side="right")
        self.button_combo.bind("<<ComboboxSelected>>", self._on_mouse_selected)

        self.key_pane = tk.Frame(self.detail, bg=CARD)
        key_row = tk.Frame(self.key_pane, bg=CARD)
        key_row.pack(fill="x", padx=14, pady=8)
        tk.Label(
            key_row, text=self._t("key"), bg=CARD, fg=MUTED, font=(FONT, 9)
        ).pack(side="left")
        self.key_btn = tk.Button(
            key_row,
            text=f"{self._key_display()}  ·  {self._t('change')}",
            command=self._begin_capture,
            bg=CARD_ALT,
            fg=FG,
            activebackground=CARD_HOVER,
            activeforeground=FG,
            relief="flat",
            bd=0,
            cursor="hand2",
            font=(FONT, 10, "bold"),
            padx=14,
            pady=6,
        )
        self.key_btn.pack(side="right")
        self._bind_hover(self.key_btn, CARD_ALT, CARD_HOVER)

        self._section(body, self._t("section_mode")).pack(fill="x", pady=(16, 0))
        mode_row = tk.Frame(body, bg=BG)
        mode_row.pack(fill="x", pady=(8, 0))
        self._mode_buttons["click"] = self._choice(
            mode_row, self._t("mode_click"), lambda: self._choose_mode("click")
        )
        self._mode_buttons["hold"] = self._choice(
            mode_row, self._t("mode_hold"), lambda: self._choose_mode("hold")
        )
        self._mode_buttons["click"].pack(
            side="left", fill="x", expand=True, padx=(0, 4)
        )
        self._mode_buttons["hold"].pack(
            side="left", fill="x", expand=True, padx=(4, 0)
        )
        self.mode_desc = tk.Label(
            body,
            bg=BG,
            fg=MUTED,
            font=(FONT, 9),
            anchor="w",
            justify="left",
            wraplength=460,
        )
        self.mode_desc.pack(fill="x", pady=(7, 0))

        self._build_speed_ui(body)

        self._build_combo_ui(self.combo_page)
        self._build_footer(outer)

    def _section(self, parent: tk.Widget, text: str) -> tk.Label:
        return tk.Label(
            parent, text=text, bg=BG, fg=MUTED, font=(FONT, 8, "bold"), anchor="w"
        )

    def _choice(self, parent: tk.Widget, text: str, command) -> tk.Button:
        button = tk.Button(
            parent,
            text=text,
            command=command,
            bg=CARD_ALT,
            fg=FG,
            activebackground=CARD_HOVER,
            activeforeground=FG,
            relief="flat",
            bd=0,
            cursor="hand2",
            font=(FONT, 10, "bold"),
            pady=9,
        )
        self._bind_hover(button, CARD_ALT, CARD_HOVER)
        return button

    @staticmethod
    def _bind_hover(button: tk.Button, normal: str, hover: str) -> None:
        button._normal_bg = normal  # type: ignore[attr-defined]
        button._hover_bg = hover  # type: ignore[attr-defined]

        def on_enter(_event) -> None:
            if str(button["state"]) != "disabled":
                button.configure(bg=hover)

        def on_leave(_event) -> None:
            if str(button["state"]) != "disabled":
                button.configure(bg=normal)

        button.bind("<Enter>", on_enter)
        button.bind("<Leave>", on_leave)

    def _build_speed_ui(self, body: tk.Frame) -> None:
        self._section(body, self._t("section_speed")).pack(fill="x", pady=(16, 0))

        speed_row = tk.Frame(body, bg=BG)
        speed_row.pack(fill="x", pady=(8, 0))
        tk.Label(
            speed_row, text=self._t("frequency"), bg=BG, fg=FG, font=(FONT, 10)
        ).pack(side="left")
        tk.Label(
            speed_row, text=self._t("per_second"), bg=BG, fg=MUTED, font=(FONT, 9)
        ).pack(side="right")
        self.speed_var.set(self._localized_speed_value(self.speed_var.get()))
        self.speed_combo = ttk.Combobox(
            speed_row,
            textvariable=self.speed_var,
            values=self._speed_values(),
            width=10,
            style="Dark.TCombobox",
            font=(FONT, 10),
        )
        self.speed_combo.pack(side="right", padx=(0, 6))

        self.speed_hint = tk.Label(
            body,
            bg=BG,
            fg=MUTED,
            font=(FONT, 8),
            anchor="w",
            justify="left",
            wraplength=460,
        )
        self.speed_hint.pack(fill="x", pady=(3, 0))

        press_row = tk.Frame(body, bg=BG)
        press_row.pack(fill="x", pady=(8, 0))
        tk.Label(
            press_row, text=self._t("press_hold"), bg=BG, fg=FG, font=(FONT, 10)
        ).pack(side="left")
        tk.Label(
            press_row, text=self._t("ms"), bg=BG, fg=MUTED, font=(FONT, 9)
        ).pack(side="right")
        self.press_entry = tk.Entry(
            press_row,
            textvariable=self.press_var,
            width=7,
            justify="center",
            bg=CARD_ALT,
            fg=FG,
            insertbackground=FG,
            disabledbackground=CARD_ALT,
            disabledforeground="#596173",
            relief="flat",
            bd=0,
            highlightthickness=1,
            highlightbackground=CARD_ALT,
            highlightcolor=ACCENT,
            font=(FONT, 10),
        )
        self.press_entry.pack(side="right", ipady=5)

        self.press_hint = tk.Label(
            body,
            bg=BG,
            fg=MUTED,
            font=(FONT, 8),
            anchor="w",
            justify="left",
            wraplength=460,
        )
        self.press_hint.pack(fill="x", pady=(3, 0))

    def _build_combo_ui(self, page: tk.Frame) -> None:
        body = tk.Frame(page, bg=BG)
        body.pack(fill="both", expand=True, padx=12, pady=12)

        tk.Label(
            body,
            text=self._t("combo_intro"),
            bg=BG,
            fg=MUTED,
            font=(FONT, 9),
            anchor="w",
            justify="left",
            wraplength=440,
        ).pack(fill="x")

        self._build_combo_channel(body, "mouse", self._t("combo_mouse"))
        self._build_combo_channel(body, "keyboard", self._t("combo_keyboard"))

        tk.Label(
            body,
            text=self._t("combo_stop_hint"),
            bg=BG,
            fg=MUTED,
            font=(FONT, 8),
            anchor="w",
        ).pack(fill="x", pady=(9, 0))

    def _build_combo_channel(
        self, parent: tk.Frame, channel: str, title: str
    ) -> None:
        card = tk.Frame(parent, bg=CARD)
        card.pack(fill="x", pady=(11, 0))

        head = tk.Frame(card, bg=CARD)
        head.pack(fill="x", padx=14, pady=(10, 4))
        tk.Label(
            head, text=title, bg=CARD, fg=FG, font=(FONT, 9, "bold")
        ).pack(side="left")

        if channel == "mouse":
            self.combo_button_var.set(
                self._mouse_label(self.combo_mouse_button)
            )
            self.combo_button_combo = ttk.Combobox(
                head,
                textvariable=self.combo_button_var,
                values=tuple(MOUSE_LABELS[self.language].values()),
                state="readonly",
                width=11,
                style="Dark.TCombobox",
                font=(FONT, 9),
            )
            self.combo_button_combo.pack(side="right")
            self.combo_button_combo.bind(
                "<<ComboboxSelected>>", self._on_combo_mouse_selected
            )
        else:
            self.combo_key_btn = tk.Button(
                head,
                text=f"{self._key_display(True)}  ·  {self._t('change')}",
                command=lambda: self._begin_capture("combo"),
                bg=CARD_ALT,
                fg=FG,
                activebackground=CARD_HOVER,
                activeforeground=FG,
                relief="flat",
                bd=0,
                cursor="hand2",
                font=(FONT, 9, "bold"),
                padx=12,
                pady=4,
            )
            self.combo_key_btn.pack(side="right")
            self._bind_hover(self.combo_key_btn, CARD_ALT, CARD_HOVER)

        mode_row = tk.Frame(card, bg=CARD)
        mode_row.pack(fill="x", padx=14, pady=(2, 6))
        for key, label in (
            ("click", self._t("mode_click")),
            ("hold", self._t("mode_hold")),
        ):
            button = self._choice(
                mode_row,
                label,
                lambda c=channel, m=key: self._choose_combo_mode(c, m),
            )
            button.configure(font=(FONT, 9, "bold"), pady=6)
            button.pack(
                side="left",
                fill="x",
                expand=True,
                padx=(0, 4) if key == "click" else (4, 0),
            )
            self.combo_mode_buttons[channel][key] = button

        params = tk.Frame(card, bg=CARD)
        params.pack(fill="x", padx=14, pady=(4, 6))
        tk.Label(
            params, text=self._t("frequency"), bg=CARD, fg=MUTED, font=(FONT, 9)
        ).pack(side="left")
        speed_var = (
            self.combo_mouse_speed_var
            if channel == "mouse"
            else self.combo_key_speed_var
        )
        speed_var.set(self._localized_speed_value(speed_var.get()))
        speed_combo = ttk.Combobox(
            params,
            textvariable=speed_var,
            values=self._speed_values(),
            width=7,
            style="Dark.TCombobox",
            font=(FONT, 9),
        )
        speed_combo.pack(side="left", padx=(7, 2))
        self.combo_speed[channel] = speed_combo
        tk.Label(
            params, text=self._t("per_second_short"), bg=CARD, fg=MUTED, font=(FONT, 8)
        ).pack(side="left")

        tk.Label(
            params, text=self._t("ms"), bg=CARD, fg=MUTED, font=(FONT, 8)
        ).pack(side="right")
        press_var = (
            self.combo_mouse_press_var
            if channel == "mouse"
            else self.combo_key_press_var
        )
        press_entry = tk.Entry(
            params,
            textvariable=press_var,
            width=5,
            justify="center",
            bg=CARD_ALT,
            fg=FG,
            insertbackground=FG,
            disabledbackground=CARD_ALT,
            disabledforeground="#596173",
            relief="flat",
            bd=0,
            highlightthickness=1,
            highlightbackground=CARD_ALT,
            highlightcolor=ACCENT,
            font=(FONT, 9),
        )
        press_entry.pack(side="right", ipady=4, padx=(0, 4))
        self.combo_press[channel] = press_entry
        tk.Label(
            params, text=self._t("press_short"), bg=CARD, fg=MUTED, font=(FONT, 9)
        ).pack(side="right", padx=(0, 5))

        hint = tk.Label(
            card,
            bg=CARD,
            fg=MUTED,
            font=(FONT, 8),
            anchor="w",
            justify="left",
            wraplength=440,
        )
        hint.pack(fill="x", padx=14, pady=(0, 10))
        self.combo_hints[channel] = hint

    def _build_footer(self, outer: tk.Frame) -> None:
        self.start_btn = tk.Button(
            outer,
            text=f"{self._t('start')}   ·   F6",
            command=self._toggle,
            bg=ACCENT,
            fg="#ffffff",
            activebackground=ACCENT_HOVER,
            activeforeground="#ffffff",
            relief="flat",
            bd=0,
            cursor="hand2",
            font=(FONT, 12, "bold"),
            pady=13,
        )
        self.start_btn.pack(fill="x", pady=(20, 0))
        self._bind_hover(self.start_btn, ACCENT, ACCENT_HOVER)

        status_row = tk.Frame(outer, bg=BG)
        status_row.pack(fill="x", pady=(11, 0))
        self.status_dot = tk.Label(
            status_row, text="●", bg=BG, fg=MUTED, font=(FONT, 9)
        )
        self.status_dot.pack(side="left")
        self.status_label = tk.Label(
            status_row,
            text=self._t("status_stopped"),
            bg=BG,
            fg=FG,
            font=(FONT, 9, "bold"),
        )
        self.status_label.pack(side="left", padx=(6, 0))
        self.rate_label = tk.Label(
            status_row, text="", bg=BG, fg=MUTED, font=(FONT, 9)
        )
        self.rate_label.pack(side="right")

        tk.Label(
            outer,
            text=self._t("footer_note"),
            bg=BG,
            fg=MUTED,
            font=(FONT, 8),
            anchor="w",
            justify="left",
            wraplength=460,
        ).pack(fill="x", pady=(7, 0))

    def _fit_and_center(self) -> None:
        self.update_idletasks()
        width = max(540, self.winfo_reqwidth())
        height = self.winfo_reqheight()
        x = max(0, (self.winfo_screenwidth() - width) // 2)
        y = max(0, (self.winfo_screenheight() - height) // 3)
        self.geometry(f"{width}x{height}+{x}+{y}")
        self.resizable(False, False)

    def _apply_dark_titlebar(self) -> None:
        self._set_dark_titlebar(self)

    def _set_dark_titlebar(self, window: tk.Misc) -> None:
        try:
            hwnd = user32.GetParent(window.winfo_id())
            enabled = ctypes.c_int(1)
            for attribute in (20, 19):
                result = ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, attribute, ctypes.byref(enabled), ctypes.sizeof(enabled)
                )
                if result == 0:
                    break
            width, height = window.winfo_width(), window.winfo_height()
            if width > 50 and height > 50:
                window.geometry(f"{width + 1}x{height}")
                window.geometry(f"{width}x{height}")
        except Exception:
            pass

    # -- state --------------------------------------------------------------

    def _choose_target(self, target: str) -> None:
        if self.running:
            return
        self.target = target
        self._sync_target()

    def _choose_mode(self, mode: str) -> None:
        if self.running:
            return
        self.mode = mode
        self._sync_mode()

    def _on_mouse_selected(self, _event=None) -> None:
        self.mouse_button = self._mouse_code(self.button_var.get())

    def _on_combo_mouse_selected(self, _event=None) -> None:
        self.combo_mouse_button = self._mouse_code(self.combo_button_var.get())

    def _on_tab_changed(self, _event=None) -> None:
        try:
            index = self.notebook.index("current")
        except tk.TclError:
            return
        self.active_tab = "combo" if index == 1 else "single"
        if self._capturing:
            self._end_capture()

    def _sync_target(self) -> None:
        mouse = self.target == "mouse"
        self.mouse_pane.pack_forget()
        self.key_pane.pack_forget()
        (self.mouse_pane if mouse else self.key_pane).pack(fill="x")
        for key, button in self._target_buttons.items():
            selected = key == self.target
            normal = ACCENT if selected else CARD_ALT
            hover = ACCENT_HOVER if selected else CARD_HOVER
            button.configure(
                bg=normal,
                fg="#ffffff" if selected else FG,
                activebackground=hover,
            )
            self._bind_hover(button, normal, hover)

    def _sync_mode(self) -> None:
        click = self.mode == "click"
        for key, button in self._mode_buttons.items():
            selected = key == self.mode
            normal = ACCENT if selected else CARD_ALT
            hover = ACCENT_HOVER if selected else CARD_HOVER
            button.configure(
                bg=normal,
                fg="#ffffff" if selected else FG,
                activebackground=hover,
            )
            self._bind_hover(button, normal, hover)

        if click:
            self.mode_desc.configure(text=self._t("mode_desc_click"))
            self.speed_hint.configure(text=self._t("hint_speed_click"))
            self.press_hint.configure(text=self._t("hint_press_click"))
        else:
            self.mode_desc.configure(text=self._t("mode_desc_hold"))
            self.speed_hint.configure(text=self._t("hint_speed_hold"))
            self.press_hint.configure(text=self._t("hint_press_hold"))

        if not self.running:
            state = "normal" if click else "disabled"
            self.speed_combo.configure(state=state)
            self.press_entry.configure(state=state)

    def _choose_combo_mode(self, channel: str, mode: str) -> None:
        if self.running:
            return
        self.combo_mode[channel] = mode
        self._sync_combo(channel)

    def _sync_combo(self, channel: str) -> None:
        click = self.combo_mode[channel] == "click"
        for key, button in self.combo_mode_buttons[channel].items():
            selected = key == self.combo_mode[channel]
            normal = ACCENT if selected else CARD_ALT
            hover = ACCENT_HOVER if selected else CARD_HOVER
            button.configure(
                bg=normal,
                fg="#ffffff" if selected else FG,
                activebackground=hover,
            )
            self._bind_hover(button, normal, hover)

        if click:
            self.combo_hints[channel].configure(text=self._t("combo_hint_click"))
        else:
            self.combo_hints[channel].configure(text=self._t("combo_hint_hold"))
        self._apply_combo_state(channel)

    def _apply_combo_state(self, channel: str, enabled: bool | None = None) -> None:
        if enabled is None:
            enabled = not self.running
        click = self.combo_mode[channel] == "click"
        for button in self.combo_mode_buttons[channel].values():
            button.configure(
                state="normal" if enabled else "disabled",
                cursor="hand2" if enabled else "arrow",
            )
        if channel == "mouse":
            self.combo_button_combo.configure(
                state="readonly" if enabled else "disabled"
            )
        else:
            self.combo_key_btn.configure(
                state="normal" if enabled else "disabled",
                cursor="hand2" if enabled else "arrow",
            )
        state = "normal" if enabled and click else "disabled"
        self.combo_speed[channel].configure(state=state)
        self.combo_press[channel].configure(state=state)

    def _apply_state(self) -> None:
        enabled = not self.running
        for button in list(self._target_buttons.values()) + list(
            self._mode_buttons.values()
        ):
            button.configure(
                state="normal" if enabled else "disabled",
                cursor="hand2" if enabled else "arrow",
            )
        self.button_combo.configure(state="readonly" if enabled else "disabled")
        self.key_btn.configure(
            state="normal" if enabled else "disabled",
            cursor="hand2" if enabled else "arrow",
        )
        if enabled and self.mode == "click":
            self.speed_combo.configure(state="normal")
            self.press_entry.configure(state="normal")
        else:
            self.speed_combo.configure(state="disabled")
            self.press_entry.configure(state="disabled")
        for channel in ("mouse", "keyboard"):
            self._apply_combo_state(channel, enabled)
        self.notebook.tab(
            0,
            state="normal" if enabled or self.active_tab == "single" else "disabled",
        )
        self.notebook.tab(
            1,
            state="normal" if enabled or self.active_tab == "combo" else "disabled",
        )
        self.settings_btn.configure(
            state="normal" if enabled else "disabled",
            cursor="hand2" if enabled else "arrow",
        )

    def _set_status(self, text: str, color: str = MUTED) -> None:
        self.status_dot.configure(fg=color)
        self.status_label.configure(text=text, fg=DANGER if color == DANGER else FG)

    def _set_start_button(self, running: bool) -> None:
        if running:
            normal, hover = DANGER, DANGER_HOVER
            text = f"{self._t('stop')}   ·   F6"
        else:
            normal, hover = ACCENT, ACCENT_HOVER
            text = f"{self._t('start')}   ·   F6"
        self.start_btn.configure(
            text=text, bg=normal, activebackground=hover
        )
        self._bind_hover(self.start_btn, normal, hover)

    def _check_hotkey(self) -> None:
        if (
            self._hotkey.ready.is_set()
            and not self._hotkey.registered
            and not self.running
        ):
            self._set_status(self._t("status_f6_busy"), DANGER)

    def _open_settings(self) -> None:
        if self.running:
            return
        if self._settings_window is not None:
            try:
                self._settings_window.lift()
                self._settings_window.focus_force()
            except tk.TclError:
                self._settings_window = None
            return

        window = tk.Toplevel(self)
        self._settings_window = window
        window.title(self._t("settings_title"))
        window.configure(bg=BG)
        window.transient(self)
        window.resizable(False, False)
        try:
            window.iconbitmap(resource_path("assets", "icon.ico"))
        except tk.TclError:
            pass
        window.protocol("WM_DELETE_WINDOW", self._close_settings)
        window.bind("<Escape>", lambda _event: self._close_settings())

        outer = tk.Frame(window, bg=BG)
        outer.pack(fill="both", expand=True, padx=24, pady=20)
        tk.Label(
            outer,
            text=self._t("settings_title"),
            bg=BG,
            fg=FG,
            font=(FONT, 16, "bold"),
            anchor="w",
        ).pack(fill="x")

        tk.Label(
            outer,
            text=self._t("settings_language"),
            bg=BG,
            fg=MUTED,
            font=(FONT, 9),
            anchor="w",
        ).pack(fill="x", pady=(16, 6))
        self.language_var = tk.StringVar(value=LANGUAGE_NAMES[self.language])
        language_combo = ttk.Combobox(
            outer,
            textvariable=self.language_var,
            values=tuple(LANGUAGE_NAMES.values()),
            state="readonly",
            width=18,
            style="Dark.TCombobox",
            font=(FONT, 10),
        )
        language_combo.pack(fill="x")
        language_combo.bind("<<ComboboxSelected>>", self._select_language)
        tk.Label(
            outer,
            text=self._t("settings_language_hint"),
            bg=BG,
            fg=MUTED,
            font=(FONT, 8),
            anchor="w",
            justify="left",
            wraplength=320,
        ).pack(fill="x", pady=(6, 0))

        tk.Frame(outer, bg=CARD_ALT, height=1).pack(fill="x", pady=(18, 14))
        tk.Label(
            outer,
            text=self._t("settings_about"),
            bg=BG,
            fg=MUTED,
            font=(FONT, 8, "bold"),
            anchor="w",
        ).pack(fill="x")
        tk.Label(
            outer,
            text=self._t("settings_about_text", version=APP_VERSION),
            bg=BG,
            fg=FG,
            font=(FONT, 9),
            anchor="w",
            justify="left",
            wraplength=320,
        ).pack(fill="x", pady=(6, 8))

        link = tk.Label(
            outer,
            text=self._t("settings_github"),
            bg=BG,
            fg=ACCENT,
            font=(FONT, 9, "underline"),
            cursor="hand2",
            anchor="w",
        )
        link.pack(fill="x")
        link.bind("<Button-1>", lambda _event: webbrowser.open(REPO_URL))
        link.bind("<Enter>", lambda _event: link.configure(fg=ACCENT_HOVER))
        link.bind("<Leave>", lambda _event: link.configure(fg=ACCENT))

        close_btn = tk.Button(
            outer,
            text=self._t("settings_close"),
            command=self._close_settings,
            bg=CARD_ALT,
            fg=FG,
            activebackground=CARD_HOVER,
            activeforeground=FG,
            relief="flat",
            bd=0,
            cursor="hand2",
            font=(FONT, 10, "bold"),
            pady=9,
        )
        close_btn.pack(fill="x", pady=(18, 0))
        self._bind_hover(close_btn, CARD_ALT, CARD_HOVER)

        window.update_idletasks()
        width = max(350, window.winfo_reqwidth())
        height = window.winfo_reqheight()
        x = self.winfo_rootx() + (self.winfo_width() - width) // 2
        y = self.winfo_rooty() + (self.winfo_height() - height) // 3
        window.geometry(f"{width}x{height}+{max(0, x)}+{max(0, y)}")
        self._set_dark_titlebar(window)
        window.grab_set()
        window.focus_force()
        language_combo.focus_set()

    def _close_settings(self) -> None:
        window = self._settings_window
        self._settings_window = None
        if window is None:
            return
        try:
            window.grab_release()
            window.destroy()
        except tk.TclError:
            pass

    def _select_language(self, _event=None) -> None:
        selected = self.language_var.get()
        language = next(
            (code for code, name in LANGUAGE_NAMES.items() if name == selected),
            self.language,
        )
        if language == self.language:
            return
        self.language = language
        save_language(language)
        self._close_settings()
        self._rebuild_ui()
        self._set_status(
            self._t("status_language", language=LANGUAGE_NAMES[language].upper()),
            MUTED,
        )

    def _rebuild_ui(self) -> None:
        if self._capturing:
            self._end_capture()
        self.title(self._t("app_title"))
        for child in list(self.winfo_children()):
            child.destroy()
        self._build_ui()
        index = 1 if self.active_tab == "combo" else 0
        self.notebook.select(index)
        self.active_tab = "combo" if index else "single"
        self._sync_target()
        self._sync_mode()
        self._sync_combo("mouse")
        self._sync_combo("keyboard")
        self._apply_state()
        self._set_start_button(False)
        self._set_status(self._t("status_stopped"), MUTED)
        self.rate_label.configure(text="")
        self._fit_and_center()

    # -- key capture --------------------------------------------------------

    def _begin_capture(self, context: str = "single") -> None:
        if self.running or self._capturing:
            return
        self._capturing = True
        self._capture_context = context
        button = self._capture_button()
        button.configure(text=self._t("capture_wait"), bg=ACCENT, fg="#ffffff")
        self._bind_hover(button, ACCENT, ACCENT_HOVER)
        self.bind_all("<KeyPress>", self._capture_key)
        self.focus_force()

    def _capture_button(self) -> tk.Button:
        return self.combo_key_btn if self._capture_context == "combo" else self.key_btn

    def _capture_key(self, event):
        if not self._capturing:
            return
        vk = int(event.keycode)
        if vk in BLOCKED_VKS:
            self._end_capture()
            if vk == VK_F6:
                self._set_status(self._t("status_f6_reserved"), DANGER)
            else:
                self._set_status(self._t("status_win_key"), DANGER)
            return "break"
        if not 8 <= vk <= 0xFE:
            return "break"
        keysym = event.keysym.lower()
        if self._capture_context == "combo":
            self._combo_key_vk = vk
            self._combo_key_sym = keysym
        else:
            self._key_vk = vk
            self._key_sym = keysym
        self._end_capture()
        self._set_status(
            self._t("status_key", name=key_label(self.language, keysym)), MUTED
        )
        return "break"

    def _end_capture(self) -> None:
        if not self._capturing:
            return
        self._capturing = False
        button = self._capture_button()
        try:
            self.unbind_all("<KeyPress>")
        except tk.TclError:
            pass
        combo = self._capture_context == "combo"
        button.configure(
            text=f"{self._key_display(combo)}  ·  {self._t('change')}",
            bg=CARD_ALT,
            fg=FG,
        )
        self._bind_hover(button, CARD_ALT, CARD_HOVER)

    # -- run control --------------------------------------------------------

    def _request_toggle(self) -> None:
        self._toggle_requested.set()

    def _poll_hotkey(self) -> None:
        if self._toggle_requested.is_set():
            self._toggle_requested.clear()
            self._toggle()
        self.after(60, self._poll_hotkey)

    def _toggle(self) -> None:
        if self._settings_window is not None:
            return
        if self._capturing:
            self._end_capture()
            return
        if self.running:
            self._stop()
        else:
            self._start()

    def _parse_speed(self, value: str) -> float:
        raw = value.strip().upper().replace(",", ".")
        if raw in MAX_VALUES:
            return 0.0
        try:
            cps = float(raw)
        except ValueError:
            raise ValueError(self._t("error_speed_number")) from None
        if not 1 <= cps <= 1_000_000:
            raise ValueError(self._t("error_speed_range"))
        return cps

    def _parse_press(self, value: str) -> float:
        raw = value.strip().replace(",", ".")
        if not raw:
            return 0.0
        try:
            press_ms = float(raw)
        except ValueError:
            raise ValueError(self._t("error_press_number")) from None
        if not 0 <= press_ms <= 500:
            raise ValueError(self._t("error_press_range"))
        return press_ms

    def _read_channel(
        self,
        target: str,
        mode: str,
        mouse_button: str,
        vk: int,
        speed_var: tk.StringVar,
        press_var: tk.StringVar,
    ) -> Settings:
        cps = 0.0
        press_ms = 0.0
        if mode == "click":
            cps = self._parse_speed(speed_var.get())
            press_ms = self._parse_press(press_var.get())

        if target == "keyboard" and vk in BLOCKED_VKS:
            raise ValueError(self._t("error_other_key"))

        return Settings(
            target=target,
            mode=mode,
            mouse_button=mouse_button,
            vk=vk,
            cps=cps,
            press_ms=press_ms,
        )

    def _read_settings(self) -> Settings:
        return self._read_channel(
            self.target,
            self.mode,
            self.mouse_button,
            self._key_vk,
            self.speed_var,
            self.press_var,
        )

    def _read_combo_settings(self) -> list[Settings]:
        configs: list[Settings] = []
        channels = (
            (
                "mouse",
                "mouse",
                self._t("prefix_mouse"),
                self._combo_key_vk,
                self.combo_mouse_speed_var,
                self.combo_mouse_press_var,
            ),
            (
                "keyboard",
                "keyboard",
                self._t("prefix_keyboard"),
                self._combo_key_vk,
                self.combo_key_speed_var,
                self.combo_key_press_var,
            ),
        )
        for channel, target, prefix, vk, speed_var, press_var in channels:
            try:
                configs.append(
                    self._read_channel(
                        target,
                        self.combo_mode[channel],
                        self.combo_mouse_button,
                        vk,
                        speed_var,
                        press_var,
                    )
                )
            except ValueError as exc:
                raise ValueError(f"{prefix}: {exc}") from None
        return configs

    def _read_active_settings(self) -> Settings | list[Settings]:
        if self.active_tab == "combo":
            return self._read_combo_settings()
        return self._read_settings()

    def _active_configs(self) -> list[Settings]:
        active = self._active
        if active is None:
            return []
        return active if isinstance(active, list) else [active]

    def _hold_only(self) -> bool:
        configs = self._active_configs()
        return bool(configs) and all(config.mode == "hold" for config in configs)

    def _start(self) -> None:
        if self.running:
            return
        try:
            settings = self._read_active_settings()
        except ValueError as exc:
            self._set_status(str(exc).upper(), DANGER)
            return

        if not self.engine.start(settings):
            self._set_status(self._t("status_already_running"), DANGER)
            return

        self._active = settings
        self.running = True
        self._rate_count = 0
        self._rate_time = time.perf_counter()
        self._apply_state()
        self._set_start_button(True)
        if len(self._active_configs()) > 1:
            self._set_status(self._t("status_running_combo"), OK)
        else:
            self._set_status(self._t("status_running"), OK)
        if self._hold_only():
            self.rate_label.configure(text=self._t("status_holding"))
        else:
            self.rate_label.configure(text=self._t("rate_clicks", rate="0"))

    def _stop(self) -> None:
        if not self.running:
            return
        self.engine.stop()
        self.running = False
        self._active = None
        self._apply_state()
        self._set_start_button(False)
        self._set_status(self._t("status_stopped"), MUTED)
        self.rate_label.configure(text="")

    # -- periodic -----------------------------------------------------------

    def _tick(self) -> None:
        running, count, _started, error = self.engine.snapshot()
        if self.running and not running:
            self.running = False
            self._active = None
            self._apply_state()
            self._set_start_button(False)
            self.rate_label.configure(text="")
            if error:
                message = (
                    self._t("error_input_blocked")
                    if error == "input_blocked"
                    else error
                )
                if len(message) > 64:
                    message = message[:61] + "…"
                self._set_status(
                    self._t("status_error", message=message), DANGER
                )
            else:
                self._set_status(self._t("status_stopped"), MUTED)
        elif self.running:
            if self._hold_only():
                self.rate_label.configure(text=self._t("status_holding"))
            else:
                now = time.perf_counter()
                elapsed = now - self._rate_time
                if elapsed >= 0.2:
                    rate = (count - self._rate_count) / elapsed
                    self._rate_count = count
                    self._rate_time = now
                    self.rate_label.configure(
                        text=self._t(
                            "rate_clicks", rate=self._format_count(rate)
                        )
                    )
        self.after(100, self._tick)

    def _on_close(self) -> None:
        self._close_settings()
        if self._capturing:
            self._end_capture()
        self.engine.stop()
        try:
            self.destroy()
        except tk.TclError:
            pass


def main() -> None:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass
    app = UniversalClickerApp()
    app.mainloop()


if __name__ == "__main__":
    main()
