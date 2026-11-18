"""Универсальный кликер: автокликер для мыши и клавиатуры (Windows).

Режимы:
  * клик     — кнопка/клавиша нажимается с заданной частотой;
  * зажатие  — кнопка/клавиша удерживается до остановки.

Глобальный старт/стоп — F6.
"""

from __future__ import annotations

import ctypes
import sys
import threading
import time
import tkinter as tk
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from tkinter import ttk

APP_TITLE = "Универсальный кликер"


def resource_path(*parts: str) -> str:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return str(base.joinpath(*parts))

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
    key_name: str = "ПРОБЕЛ"
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
            raise RuntimeError(
                "Windows заблокировал ввод. Запустите кликер от имени администратора."
            )

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

MOUSE_LABELS = {"left": "Левая", "right": "Правая", "middle": "Средняя"}
MOUSE_KEYS = {label: key for key, label in MOUSE_LABELS.items()}

SPEED_VALUES = (
    "10",
    "25",
    "50",
    "100",
    "200",
    "500",
    "1000",
    "2500",
    "5000",
    "МАКС",
)

KEY_LABELS = {
    "space": "ПРОБЕЛ",
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


def _pretty_key(event) -> str:
    keysym = event.keysym
    return KEY_LABELS.get(keysym.lower(), keysym.upper())


class UniversalClickerApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(APP_TITLE)
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
        self._active: Settings | list[Settings] | None = None
        self._capturing = False
        self._capture_context = "single"
        self._key_vk = 0x20
        self._key_name = "ПРОБЕЛ"
        self._toggle_requested = threading.Event()
        self._target_buttons: dict[str, tk.Button] = {}
        self._mode_buttons: dict[str, tk.Button] = {}
        self._config_widgets: list[tk.Widget] = []
        self._rate_count = 0
        self._rate_time = 0.0

        # вкладка «Комбо»
        self.combo_mode = {"mouse": "click", "keyboard": "click"}
        self.combo_mode_buttons: dict[str, dict[str, tk.Button]] = {
            "mouse": {},
            "keyboard": {},
        }
        self.combo_hints: dict[str, tk.Label] = {}
        self.combo_speed: dict[str, ttk.Combobox] = {}
        self.combo_press: dict[str, tk.Entry] = {}
        self._combo_key_vk = 0x20
        self._combo_key_name = "ПРОБЕЛ"
        self.combo_button_var = tk.StringVar(value=MOUSE_LABELS["left"])
        self.combo_mouse_speed_var = tk.StringVar(value="100")
        self.combo_mouse_press_var = tk.StringVar(value="0")
        self.combo_key_speed_var = tk.StringVar(value="100")
        self.combo_key_press_var = tk.StringVar(value="0")

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

        tk.Label(
            outer, text=APP_TITLE, bg=BG, fg=FG, font=(FONT, 20, "bold"), anchor="w"
        ).pack(fill="x")
        tk.Label(
            outer,
            text="Мышь и клавиатура  ·  глобальный старт по F6",
            bg=BG,
            fg=MUTED,
            font=(FONT, 9),
            anchor="w",
        ).pack(fill="x", pady=(3, 0))

        self.notebook = ttk.Notebook(outer, style="Dark.TNotebook", takefocus=False)
        self.notebook.pack(fill="both", expand=True, pady=(16, 0))
        self.single_page = tk.Frame(self.notebook, bg=BG)
        self.combo_page = tk.Frame(self.notebook, bg=BG)
        self.notebook.add(self.single_page, text="Одиночный")
        self.notebook.add(self.combo_page, text="Комбо")
        self.notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)

        body = tk.Frame(self.single_page, bg=BG)
        body.pack(fill="both", expand=True, padx=12, pady=12)

        self._section(body, "1  ·  ЦЕЛЬ").pack(fill="x")
        target_row = tk.Frame(body, bg=BG)
        target_row.pack(fill="x", pady=(8, 0))
        self._target_buttons["mouse"] = self._choice(
            target_row, "Мышь", lambda: self._choose_target("mouse")
        )
        self._target_buttons["keyboard"] = self._choice(
            target_row, "Клавиатура", lambda: self._choose_target("keyboard")
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
            mouse_row, text="Кнопка мыши", bg=CARD, fg=MUTED, font=(FONT, 9)
        ).pack(side="left")
        self.button_var = tk.StringVar(value=MOUSE_LABELS["left"])
        self.button_combo = ttk.Combobox(
            mouse_row,
            textvariable=self.button_var,
            values=tuple(MOUSE_LABELS.values()),
            state="readonly",
            width=11,
            style="Dark.TCombobox",
            font=(FONT, 10),
        )
        self.button_combo.pack(side="right")
        self._config_widgets.append(self.button_combo)

        self.key_pane = tk.Frame(self.detail, bg=CARD)
        key_row = tk.Frame(self.key_pane, bg=CARD)
        key_row.pack(fill="x", padx=14, pady=8)
        tk.Label(
            key_row, text="Клавиша", bg=CARD, fg=MUTED, font=(FONT, 9)
        ).pack(side="left")
        self.key_btn = tk.Button(
            key_row,
            text=f"{self._key_name}  ·  сменить",
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
        self._config_widgets.append(self.key_btn)

        self._section(body, "2  ·  РЕЖИМ").pack(fill="x", pady=(16, 0))
        mode_row = tk.Frame(body, bg=BG)
        mode_row.pack(fill="x", pady=(8, 0))
        self._mode_buttons["click"] = self._choice(
            mode_row, "Клик", lambda: self._choose_mode("click")
        )
        self._mode_buttons["hold"] = self._choice(
            mode_row, "Зажатие", lambda: self._choose_mode("hold")
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
            wraplength=480,
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
        self._section(body, "3  ·  СКОРОСТЬ").pack(fill="x", pady=(16, 0))

        speed_row = tk.Frame(body, bg=BG)
        speed_row.pack(fill="x", pady=(8, 0))
        tk.Label(
            speed_row, text="Частота", bg=BG, fg=FG, font=(FONT, 10)
        ).pack(side="left")
        tk.Label(
            speed_row, text="в секунду", bg=BG, fg=MUTED, font=(FONT, 9)
        ).pack(side="right")
        self.speed_var = tk.StringVar(value="100")
        self.speed_combo = ttk.Combobox(
            speed_row,
            textvariable=self.speed_var,
            values=SPEED_VALUES,
            width=10,
            style="Dark.TCombobox",
            font=(FONT, 10),
        )
        self.speed_combo.pack(side="right", padx=(0, 6))
        self._config_widgets.append(self.speed_combo)

        self.speed_hint = tk.Label(
            body,
            bg=BG,
            fg=MUTED,
            font=(FONT, 8),
            anchor="w",
            justify="left",
            wraplength=480,
        )
        self.speed_hint.pack(fill="x", pady=(3, 0))

        press_row = tk.Frame(body, bg=BG)
        press_row.pack(fill="x", pady=(8, 0))
        tk.Label(
            press_row, text="Удержание нажатия", bg=BG, fg=FG, font=(FONT, 10)
        ).pack(side="left")
        tk.Label(
            press_row, text="мс", bg=BG, fg=MUTED, font=(FONT, 9)
        ).pack(side="right")
        self.press_var = tk.StringVar(value="0")
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
        self._config_widgets.append(self.press_entry)

        self.press_hint = tk.Label(
            body,
            bg=BG,
            fg=MUTED,
            font=(FONT, 8),
            anchor="w",
            justify="left",
            wraplength=480,
        )
        self.press_hint.pack(fill="x", pady=(3, 0))

    def _build_combo_ui(self, page: tk.Frame) -> None:
        body = tk.Frame(page, bg=BG)
        body.pack(fill="both", expand=True, padx=12, pady=12)

        tk.Label(
            body,
            text=(
                "Оба канала работают одновременно и независимо. "
                "У каждого свой режим, частота и удержание."
            ),
            bg=BG,
            fg=MUTED,
            font=(FONT, 9),
            anchor="w",
            justify="left",
            wraplength=440,
        ).pack(fill="x")

        self._build_combo_channel(body, "mouse", "МЫШЬ")
        self._build_combo_channel(body, "keyboard", "КЛАВИАТУРА")

        tk.Label(
            body,
            text="Остановить оба канала: кнопка «Стоп» или F6.",
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
            self.combo_button_combo = ttk.Combobox(
                head,
                textvariable=self.combo_button_var,
                values=tuple(MOUSE_LABELS.values()),
                state="readonly",
                width=11,
                style="Dark.TCombobox",
                font=(FONT, 9),
            )
            self.combo_button_combo.pack(side="right")
        else:
            self.combo_key_btn = tk.Button(
                head,
                text=f"{self._combo_key_name}  ·  сменить",
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
        for key, label in (("click", "Клик"), ("hold", "Зажатие")):
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
            params, text="Частота", bg=CARD, fg=MUTED, font=(FONT, 9)
        ).pack(side="left")
        speed_var = (
            self.combo_mouse_speed_var
            if channel == "mouse"
            else self.combo_key_speed_var
        )
        speed_combo = ttk.Combobox(
            params,
            textvariable=speed_var,
            values=SPEED_VALUES,
            width=7,
            style="Dark.TCombobox",
            font=(FONT, 9),
        )
        speed_combo.pack(side="left", padx=(7, 2))
        self.combo_speed[channel] = speed_combo
        tk.Label(params, text="/с", bg=CARD, fg=MUTED, font=(FONT, 8)).pack(
            side="left"
        )

        tk.Label(params, text="мс", bg=CARD, fg=MUTED, font=(FONT, 8)).pack(
            side="right"
        )
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
            params, text="Удержание", bg=CARD, fg=MUTED, font=(FONT, 9)
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
            text="СТАРТ   ·   F6",
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
            text="ОСТАНОВЛЕН",
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
            text=(
                "F6 работает глобально, даже если окно свёрнуто. "
                "Если игра не принимает ввод — запустите кликер от имени администратора."
            ),
            bg=BG,
            fg=MUTED,
            font=(FONT, 8),
            anchor="w",
            justify="left",
            wraplength=480,
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
        try:
            hwnd = user32.GetParent(self.winfo_id())
            enabled = ctypes.c_int(1)
            for attribute in (20, 19):
                result = ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, attribute, ctypes.byref(enabled), ctypes.sizeof(enabled)
                )
                if result == 0:
                    break
            width, height = self.winfo_width(), self.winfo_height()
            if width > 50 and height > 50:
                # Windows 11 перерисовывает рамку только после изменения размера.
                self.geometry(f"{width + 1}x{height}")
                self.geometry(f"{width}x{height}")
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
            self.mode_desc.configure(
                text="Кнопка или клавиша нажимается и сразу отпускается. Частота задаётся ниже."
            )
            self.speed_hint.configure(
                text="МАКС — без ограничения. Фактический предел зависит от Windows и нагрузки."
            )
            self.press_hint.configure(
                text="0 — нажатие без задержки. Для игр попробуйте 10–30 мс."
            )
        else:
            self.mode_desc.configure(
                text="Кнопка или клавиша зажимается и держится до повторного F6 или кнопки «Стоп»."
            )
            self.speed_hint.configure(
                text="В режиме «Зажатие» частота и удержание не используются."
            )
            self.press_hint.configure(text="")

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
            self.combo_hints[channel].configure(
                text=(
                    "Клики с заданной частотой. Удержание — пауза "
                    "между нажатием и отпусканием."
                )
            )
        else:
            self.combo_hints[channel].configure(
                text=(
                    "Нажатие удерживается до остановки. "
                    "Частота и удержание не используются."
                )
            )
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

    def _set_status(self, text: str, color: str = MUTED) -> None:
        self.status_dot.configure(fg=color)
        self.status_label.configure(text=text, fg=DANGER if color == DANGER else FG)

    def _set_start_button(self, running: bool) -> None:
        if running:
            normal, hover = DANGER, DANGER_HOVER
            text = "ОСТАНОВИТЬ   ·   F6"
        else:
            normal, hover = ACCENT, ACCENT_HOVER
            text = "СТАРТ   ·   F6"
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
            self._set_status("F6 ЗАНЯТА — ЖМИТЕ «СТАРТ»", DANGER)

    # -- key capture --------------------------------------------------------

    def _begin_capture(self, context: str = "single") -> None:
        if self.running or self._capturing:
            return
        self._capturing = True
        self._capture_context = context
        button = self._capture_button()
        button.configure(text="Нажмите клавишу…", bg=ACCENT, fg="#ffffff")
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
                self._set_status("F6 ЗАНЯТА — ВЫБЕРИТЕ ДРУГУЮ КЛАВИШУ", DANGER)
            else:
                self._set_status("КЛАВИША WINDOWS НЕ ПОДДЕРЖИВАЕТСЯ", DANGER)
            return "break"
        if not 8 <= vk <= 0xFE:
            return "break"
        name = _pretty_key(event)
        if self._capture_context == "combo":
            self._combo_key_vk = vk
            self._combo_key_name = name
        else:
            self._key_vk = vk
            self._key_name = name
        self._end_capture()
        self._set_status(f"КЛАВИША: {name}", MUTED)
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
        name = (
            self._combo_key_name
            if self._capture_context == "combo"
            else self._key_name
        )
        button.configure(text=f"{name}  ·  сменить", bg=CARD_ALT, fg=FG)
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
        if self._capturing:
            self._end_capture()
            return
        if self.running:
            self._stop()
        else:
            self._start()

    @staticmethod
    def _parse_speed(value: str) -> float:
        raw = value.strip().upper().replace(",", ".")
        if raw in {"МАКС", "MAX", "МАКСИМУМ", "∞"}:
            return 0.0
        try:
            cps = float(raw)
        except ValueError:
            raise ValueError("Введите частоту числом или «МАКС».") from None
        if not 1 <= cps <= 1_000_000:
            raise ValueError("Частота должна быть от 1 до 1 000 000.")
        return cps

    @staticmethod
    def _parse_press(value: str) -> float:
        raw = value.strip().replace(",", ".")
        if not raw:
            return 0.0
        try:
            press_ms = float(raw)
        except ValueError:
            raise ValueError("Удержание задаётся числом миллисекунд.") from None
        if not 0 <= press_ms <= 500:
            raise ValueError("Удержание: от 0 до 500 мс.")
        return press_ms

    def _read_channel(
        self,
        target: str,
        mode: str,
        button_var: tk.StringVar,
        vk: int,
        key_name: str,
        speed_var: tk.StringVar,
        press_var: tk.StringVar,
    ) -> Settings:
        cps = 0.0
        press_ms = 0.0
        if mode == "click":
            cps = self._parse_speed(speed_var.get())
            press_ms = self._parse_press(press_var.get())

        if target == "keyboard" and vk in BLOCKED_VKS:
            raise ValueError("Выберите другую клавишу.")

        return Settings(
            target=target,
            mode=mode,
            mouse_button=MOUSE_KEYS.get(button_var.get(), "left"),
            vk=vk,
            key_name=key_name,
            cps=cps,
            press_ms=press_ms,
        )

    def _read_settings(self) -> Settings:
        return self._read_channel(
            self.target,
            self.mode,
            self.button_var,
            self._key_vk,
            self._key_name,
            self.speed_var,
            self.press_var,
        )

    def _read_combo_settings(self) -> list[Settings]:
        configs: list[Settings] = []
        channels = (
            (
                "mouse",
                "mouse",
                "Мышь",
                self._combo_key_vk,
                self._combo_key_name,
                self.combo_mouse_speed_var,
                self.combo_mouse_press_var,
            ),
            (
                "keyboard",
                "keyboard",
                "Клавиатура",
                self._combo_key_vk,
                self._combo_key_name,
                self.combo_key_speed_var,
                self.combo_key_press_var,
            ),
        )
        for channel, target, prefix, vk, key_name, speed_var, press_var in channels:
            try:
                configs.append(
                    self._read_channel(
                        target,
                        self.combo_mode[channel],
                        self.combo_button_var,
                        vk,
                        key_name,
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
            self._set_status("КЛИКЕР УЖЕ РАБОТАЕТ", DANGER)
            return

        self._active = settings
        self.running = True
        self._rate_count = 0
        self._rate_time = time.perf_counter()
        self._apply_state()
        self._set_start_button(True)
        if len(self._active_configs()) > 1:
            self._set_status("РАБОТАЕТ · 2 КАНАЛА", OK)
        else:
            self._set_status("РАБОТАЕТ", OK)
        if self._hold_only():
            self.rate_label.configure(text="УДЕРЖАНИЕ")
        else:
            self.rate_label.configure(text="0 нажатий/с")

    def _stop(self) -> None:
        if not self.running:
            return
        self.engine.stop()
        self.running = False
        self._active = None
        self._apply_state()
        self._set_start_button(False)
        self._set_status("ОСТАНОВЛЕН", MUTED)
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
                short = error if len(error) <= 64 else error[:61] + "…"
                self._set_status("Ошибка: " + short, DANGER)
            else:
                self._set_status("ОСТАНОВЛЕН", MUTED)
        elif self.running:
            if self._hold_only():
                self.rate_label.configure(text="УДЕРЖАНИЕ")
            else:
                now = time.perf_counter()
                elapsed = now - self._rate_time
                if elapsed >= 0.2:
                    rate = (count - self._rate_count) / elapsed
                    self._rate_count = count
                    self._rate_time = now
                    text = f"{rate:,.0f} нажатий/с".replace(",", " ")
                    self.rate_label.configure(text=text)
        self.after(100, self._tick)

    def _on_close(self) -> None:
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
