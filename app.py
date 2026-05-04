#!/usr/bin/env python3
"""Progress Checker — macOS menu bar app with native AppKit dialogs."""
import json
import os
from datetime import datetime, timedelta
from typing import List, Optional

import rumps
from Foundation import NSObject
from AppKit import (
    NSApp,
    NSAppearance,
    NSButton,
    NSColor,
    NSFont,
    NSMakeRect,
    NSScrollView,
    NSTextField,
    NSTextView,
    NSWindow,
)

DATA_FILE = os.path.expanduser("~/.progress_checker.json")
DEFAULT_INTERVAL = 20  # minutes
BREAK_MINUTES = 5

# NSWindowStyleMask: Titled | Closable | Miniaturizable
_STYLE = 1 | 2 | 4
# NSWindowCollectionBehavior flags
_WC_MANAGED = 1 << 2
_WC_CYCLE   = 1 << 5
# Modal return codes
_BTN1, _BTN2, _BTN3 = 1000, 1001, 1002
_CANCEL = -1

# ── Background color ─────────────────────────────────────────────────────────

_BG = NSColor.colorWithRed_green_blue_alpha_(1.0, 1.0, 1.0, 0.93)

# ── Shared UI helpers ─────────────────────────────────────────────────────────


def _label(text: str, rect, font, color=None, selectable: bool = False) -> NSTextField:
    f = NSTextField.alloc().initWithFrame_(rect)
    f.setStringValue_(text)
    f.setFont_(font)
    f.setBezeled_(False)
    f.setDrawsBackground_(False)
    f.setEditable_(False)
    f.setSelectable_(selectable)
    f.setTextColor_(color if color is not None else NSColor.colorWithWhite_alpha_(0.25, 1.0))
    return f


def _mlabel(text: str, rect, font, color=None, selectable: bool = True) -> NSTextField:
    """Wrapping multi-line label (selectable by default for copy-paste)."""
    f = _label(text, rect, font, color, selectable=selectable)
    f.cell().setWraps_(True)
    return f


def _sep(rect) -> NSTextField:
    """Thin horizontal separator line."""
    f = NSTextField.alloc().initWithFrame_(rect)
    f.setBezeled_(False)
    f.setDrawsBackground_(True)
    f.setBackgroundColor_(NSColor.separatorColor())
    f.setEditable_(False)
    return f


def _input_field(rect, font, placeholder: str = "", default: str = "") -> NSTextField:
    f = NSTextField.alloc().initWithFrame_(rect)
    f.setFont_(font)
    f.setPlaceholderString_(placeholder)
    if default:
        f.setStringValue_(default)
    return f


def _text_view(items: List[str], rect) -> tuple:
    """NSScrollView + NSTextView for multi-line list editing. Returns (scroll, tv)."""
    scroll = NSScrollView.alloc().initWithFrame_(rect)
    scroll.setHasVerticalScroller_(True)
    scroll.setAutohidesScrollers_(True)
    scroll.setBorderType_(2)   # NSBezelBorder

    tv = NSTextView.alloc().initWithFrame_(
        NSMakeRect(0, 0, rect.size.width - 16, rect.size.height)
    )
    tv.setFont_(NSFont.systemFontOfSize_(14))
    tv.setRichText_(False)
    tv.setAutomaticLinkDetectionEnabled_(False)
    tv.setString_("\n".join(items))
    tv.setAutoresizingMask_(2)   # NSViewWidthSizable
    scroll.setDocumentView_(tv)
    return scroll, tv


def _today_text(items: List[str]) -> str:
    if not items:
        return "未設定"
    return "\n".join(f"• {item}" for item in items)


# ── Custom modal window ───────────────────────────────────────────────────────


class _Handler(NSObject):
    """Shared target for button clicks and window-close across all dialogs."""
    def click_(self, sender):
        NSApp.stopModalWithCode_(sender.tag())

    def windowShouldClose_(self, _):
        NSApp.stopModalWithCode_(_CANCEL)
        return False  # let our finally block call orderOut_


_H = _Handler.alloc().init()   # singleton — safe because modals are sequential


def _make_win(title: str, w: int, h: int) -> NSWindow:
    win = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
        NSMakeRect(0, 0, w, h), _STYLE, 2, False,
    )
    win.setTitle_(title)
    win.center()
    win.setDelegate_(_H)
    win.setCollectionBehavior_(_WC_MANAGED | _WC_CYCLE)
    win.setOpaque_(False)
    win.setBackgroundColor_(_BG)
    win.setAppearance_(NSAppearance.appearanceNamed_("NSAppearanceNameAqua"))
    return win


def _btn(cv, title: str, code: int, rect, primary: bool = False) -> NSButton:
    b = NSButton.alloc().initWithFrame_(rect)
    b.setTitle_(title)
    b.setBezelStyle_(1)   # NSBezelStyleRounded
    b.setTag_(code)
    b.setTarget_(_H)
    b.setAction_("click:")
    if primary:
        b.setKeyEquivalent_("\r")
    cv.addSubview_(b)
    return b


def _show(win: NSWindow) -> None:
    NSApp.setActivationPolicy_(0)
    NSApp.activateIgnoringOtherApps_(True)
    win.makeKeyAndOrderFront_(None)


def _hide() -> None:
    NSApp.setActivationPolicy_(1)


# ── Dialog functions ──────────────────────────────────────────────────────────


def show_goal_input(title: str, prompt: str, default: str = "") -> Optional[str]:
    W, H = 440, 168
    win = _make_win(title, W, H)
    cv = win.contentView()

    cv.addSubview_(_label(
        prompt, NSMakeRect(20, 126, W-40, 22),
        NSFont.boldSystemFontOfSize_(13),
    ))
    field = _input_field(
        NSMakeRect(20, 82, W-40, 34),
        NSFont.systemFontOfSize_(15),
        placeholder="入力…", default=default,
    )
    cv.addSubview_(field)
    err = _label(
        "", NSMakeRect(20, 60, W-40, 18),
        NSFont.systemFontOfSize_(12), color=NSColor.systemOrangeColor(),
    )
    cv.addSubview_(err)
    _btn(cv, "決定", _BTN1, NSMakeRect(W-136, 16, 116, 32), primary=True)

    win.setInitialFirstResponder_(field)
    _show(win)
    try:
        while True:
            resp = NSApp.runModalForWindow_(win)
            if resp == _CANCEL:
                return None
            val = field.stringValue().strip()
            if val:
                return val
            err.setStringValue_("入力してください")
    finally:
        win.orderOut_(None)
        _hide()


def show_list_input(title: str, prompt: str, items: List[str]) -> Optional[List[str]]:
    """Multi-line text area for entering a list of items (one per line)."""
    W, H = 440, 268
    win = _make_win(title, W, H)
    cv = win.contentView()

    cv.addSubview_(_label(
        prompt, NSMakeRect(20, 234, W-40, 22),
        NSFont.boldSystemFontOfSize_(13),
    ))
    cv.addSubview_(_label(
        "1行につき1つ入力してください",
        NSMakeRect(20, 210, W-40, 18),
        NSFont.systemFontOfSize_(12), color=NSColor.colorWithWhite_alpha_(0.5, 1.0),
    ))

    scroll, tv = _text_view(items, NSMakeRect(20, 60, W-40, 142))
    cv.addSubview_(scroll)

    _btn(cv, "決定", _BTN1, NSMakeRect(W-136, 16, 116, 32), primary=True)

    win.makeFirstResponder_(tv)
    _show(win)
    try:
        resp = NSApp.runModalForWindow_(win)
        if resp == _CANCEL:
            return None
        lines = [l.strip() for l in str(tv.string()).split("\n") if l.strip()]
        return lines if lines else None
    finally:
        win.orderOut_(None)
        _hide()


def show_checkin(goals: dict) -> Optional[str]:
    """Returns the task string, or None for break / window-close."""
    W, H = 480, 460

    win = _make_win("チェックイン", W, H)
    cv = win.contentView()

    # ── 今日やりたいこと ────────────────────────────────────────────────────
    cv.addSubview_(_label(
        "📅  今日やりたいこと",
        NSMakeRect(20, 432, W-40, 20),
        NSFont.boldSystemFontOfSize_(13),
        color=NSColor.systemBlueColor(),
    ))
    today_items = goals.get("today", [])
    if isinstance(today_items, str):
        today_items = [today_items] if today_items else []
    cv.addSubview_(_mlabel(
        _today_text(today_items),
        NSMakeRect(28, 372, W-48, 56),
        NSFont.systemFontOfSize_(13),
    ))
    cv.addSubview_(_sep(NSMakeRect(20, 364, W-40, 1)))

    # ── 今週 ────────────────────────────────────────────────────────────────
    cv.addSubview_(_label(
        "📋  今週",
        NSMakeRect(20, 344, W-40, 16),
        NSFont.boldSystemFontOfSize_(12),
    ))
    cv.addSubview_(_mlabel(
        goals.get("weekly") or "未設定",
        NSMakeRect(28, 318, W-48, 22),
        NSFont.systemFontOfSize_(12),
    ))
    cv.addSubview_(_sep(NSMakeRect(20, 310, W-40, 1)))

    # ── 短期目標 ────────────────────────────────────────────────────────────
    cv.addSubview_(_label(
        "📌  短期目標",
        NSMakeRect(20, 290, W-40, 16),
        NSFont.boldSystemFontOfSize_(12),
    ))
    cv.addSubview_(_mlabel(
        goals.get("short") or "未設定",
        NSMakeRect(28, 264, W-48, 22),
        NSFont.systemFontOfSize_(12),
    ))
    cv.addSubview_(_sep(NSMakeRect(20, 256, W-40, 1)))

    # ── 中期目標 ────────────────────────────────────────────────────────────
    cv.addSubview_(_label(
        "📅  中期目標",
        NSMakeRect(20, 236, W-40, 16),
        NSFont.boldSystemFontOfSize_(12),
    ))
    cv.addSubview_(_mlabel(
        goals.get("mid") or "未設定",
        NSMakeRect(28, 210, W-48, 22),
        NSFont.systemFontOfSize_(12),
    ))
    cv.addSubview_(_sep(NSMakeRect(20, 202, W-40, 1)))

    # ── 長期目標 ────────────────────────────────────────────────────────────
    cv.addSubview_(_label(
        "🌟  長期目標",
        NSMakeRect(20, 182, W-40, 16),
        NSFont.boldSystemFontOfSize_(12),
    ))
    cv.addSubview_(_mlabel(
        goals.get("long") or "未設定",
        NSMakeRect(28, 156, W-48, 22),
        NSFont.systemFontOfSize_(12),
    ))
    cv.addSubview_(_sep(NSMakeRect(20, 148, W-40, 1)))

    # ── タスク入力 ───────────────────────────────────────────────────────────
    cv.addSubview_(_label(
        "次のセッションでやる「最小タスク」は？",
        NSMakeRect(20, 122, W-40, 20),
        NSFont.boldSystemFontOfSize_(13),
    ))
    field = _input_field(
        NSMakeRect(20, 82, W-40, 34),
        NSFont.systemFontOfSize_(15),
        placeholder="例: 参考書を1ページ読む",
    )
    cv.addSubview_(field)
    err = _label(
        "", NSMakeRect(20, 60, W-40, 18),
        NSFont.systemFontOfSize_(12), color=NSColor.systemOrangeColor(),
    )
    cv.addSubview_(err)

    # ── ボタン ──────────────────────────────────────────────────────────────
    _btn(cv, "スタート！",                _BTN1, NSMakeRect(W-160, 16, 140, 36), primary=True)
    _btn(cv, f"☕  {BREAK_MINUTES}分休憩", _BTN2, NSMakeRect(W-312, 16, 140, 36))

    win.setInitialFirstResponder_(field)
    _show(win)
    try:
        while True:
            resp = NSApp.runModalForWindow_(win)
            if resp in (_CANCEL, _BTN2):
                return None
            text = field.stringValue().strip()
            if text:
                return text
            err.setStringValue_("何か入力してください（どんなに小さくてもOK！）")
    finally:
        win.orderOut_(None)
        _hide()


def show_feedback(task: str) -> str:
    """Returns 'complete', 'progress', or 'replan'."""
    W, H = 420, 172
    win = _make_win("セッション振り返り", W, H)
    cv = win.contentView()

    short = (task[:44] + "…") if len(task) > 44 else task
    cv.addSubview_(_mlabel(
        f"「{short}」",
        NSMakeRect(20, 120, W-40, 38),
        NSFont.boldSystemFontOfSize_(16),
    ))
    cv.addSubview_(_label(
        "どのくらい進みましたか？",
        NSMakeRect(20, 90, W-40, 22),
        NSFont.systemFontOfSize_(13),
    ))

    bw = (W - 40 - 16) // 3
    _btn(cv, "✅  完了！",     _BTN1, NSMakeRect(20,            16, bw, 36))
    _btn(cv, "🌱  少し進んだ", _BTN2, NSMakeRect(20 + bw + 8,   16, bw, 36))
    _btn(cv, "🔄  方針変更",   _BTN3, NSMakeRect(20 + (bw+8)*2, 16, bw, 36))

    _show(win)
    try:
        resp = NSApp.runModalForWindow_(win)
        return {_BTN1: "complete", _BTN2: "progress", _BTN3: "replan"}.get(resp, "progress")
    finally:
        win.orderOut_(None)
        _hide()


# ── Notification ──────────────────────────────────────────────────────────────


def notify(title: str, subtitle: str, body: str = ""):
    try:
        rumps.notification(title, subtitle, body)
    except Exception:
        pass


# ── App ───────────────────────────────────────────────────────────────────────


class ProgressChecker(rumps.App):

    def __init__(self):
        super().__init__("🎯", quit_button=None)
        self.data = self._load()
        self._checkin_active = False
        self._break_mode = False

        self._task_item = rumps.MenuItem("📌 タスク未設定", callback=None)
        self.menu = [
            self._task_item,
            None,
            rumps.MenuItem("🔄 今すぐチェックイン",   callback=self._cmd_checkin),
            None,
            rumps.MenuItem("🌟 長期目標を変更",       callback=self._cmd_edit_long),
            rumps.MenuItem("📅 中期目標を変更",       callback=self._cmd_edit_mid),
            rumps.MenuItem("📌 短期目標を変更",       callback=self._cmd_edit_short),
            rumps.MenuItem("📋 今週の目標を変更",     callback=self._cmd_edit_weekly),
            rumps.MenuItem("🗓  今日の目標を変更",     callback=self._cmd_edit_today),
            None,
            rumps.MenuItem("❌ 終了", callback=rumps.quit_application),
        ]
        self._start_timer()
        self._start_watchdog()
        self._start_ui_timer()
        self._refresh_ui()

        if not self.data["goals"].get("short"):
            rumps.Timer(self._first_run, 1).start()

    # ── Persistence ───────────────────────────────────────────────────────

    def _load(self) -> dict:
        if os.path.exists(DATA_FILE):
            try:
                with open(DATA_FILE) as f:
                    data = json.load(f)
                g = data.setdefault("goals", {})
                for key in ("long", "mid", "short", "weekly"):
                    g.setdefault(key, "")
                # migrate today: string → list
                today = g.get("today", [])
                if isinstance(today, str):
                    g["today"] = [today] if today.strip() else []
                else:
                    g.setdefault("today", [])
                return data
            except Exception:
                pass
        return {
            "goals": {"long": "", "mid": "", "short": "", "weekly": "", "today": []},
            "current_task": "",
            "interval_minutes": DEFAULT_INTERVAL,
        }

    def _save(self):
        with open(DATA_FILE, "w") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)

    # ── Display ───────────────────────────────────────────────────────────

    def _refresh_ui(self):
        task = self.data.get("current_task") or "タスク未設定"
        self._task_item.title = f"📌 今やること: {task}"
        self._update_countdown()

    def _update_countdown(self):
        remaining = self._next_checkin_at - datetime.now()
        total_secs = max(0, int(remaining.total_seconds()))
        mins, secs = divmod(total_secs, 60)
        if self._break_mode:
            self.title = f"☕ 休憩 {mins}:{secs:02d}"
        else:
            task = self.data.get("current_task") or "タスク未設定"
            label = (task[:15] + "…") if len(task) > 15 else task
            self.title = f"🎯 {label} {mins}:{secs:02d}"

    # ── Timer ─────────────────────────────────────────────────────────────

    def _start_timer(self, override_minutes: Optional[int] = None):
        interval = override_minutes if override_minutes is not None \
            else self.data.get("interval_minutes", DEFAULT_INTERVAL)
        self._next_checkin_at = datetime.now() + timedelta(minutes=interval)
        self._timer = rumps.Timer(self._on_timer_fire, interval * 60)
        self._timer.start()

    def _reset_timer(self, override_minutes: Optional[int] = None):
        self._timer.stop()
        self._start_timer(override_minutes)

    def _on_timer_fire(self, _):
        if datetime.now() < self._next_checkin_at:
            return
        self._do_checkin()

    def _start_watchdog(self):
        self._watchdog = rumps.Timer(self._on_watchdog, 60)
        self._watchdog.start()

    def _start_ui_timer(self):
        self._ui_timer = rumps.Timer(self._on_ui_tick, 1)
        self._ui_timer.start()

    def _on_ui_tick(self, _):
        if not self._checkin_active:
            self._update_countdown()

    def _on_watchdog(self, _):
        if datetime.now() >= self._next_checkin_at:
            self._do_checkin()

    # ── Core flows ────────────────────────────────────────────────────────

    def _first_run(self, timer: rumps.Timer):
        timer.stop()
        self._setup_all_goals()

    def _setup_all_goals(self):
        self._checkin_active = True
        try:
            g = self.data["goals"]
            str_entries = [
                ("long",   "長期目標 (1/5)", "1〜2年後に達成したいことは？"),
                ("mid",    "中期目標 (2/5)", "1〜5ヶ月で達成したいことは？"),
                ("short",  "短期目標 (3/5)", "今日〜1ヶ月で達成したいことは？"),
                ("weekly", "今週の目標 (4/5)", "今週やりたいことは？"),
            ]
            updated = {}
            for key, title, prompt in str_entries:
                val = show_goal_input(title, prompt, default=g.get(key, ""))
                updated[key] = val if val is not None else g.get(key, "")

            today_default = g.get("today", [])
            if isinstance(today_default, str):
                today_default = [today_default] if today_default.strip() else []
            val = show_list_input("今日の目標 (5/5)", "今日やりたいことは？", today_default)
            updated["today"] = val if val is not None else today_default

            self.data["goals"] = updated
            self._save()
        finally:
            self._checkin_active = False
        self._do_checkin()

    def _edit_goal(self, key: str, title: str, prompt: str):
        if self._checkin_active:
            return
        self._checkin_active = True
        try:
            current = self.data["goals"].get(key, "")
            val = show_goal_input(title, prompt, default=current)
            if val is not None:
                self.data["goals"][key] = val
                self._save()
        finally:
            self._checkin_active = False

    def _edit_today(self):
        if self._checkin_active:
            return
        self._checkin_active = True
        try:
            current = self.data["goals"].get("today", [])
            if isinstance(current, str):
                current = [current] if current.strip() else []
            val = show_list_input("今日の目標を変更", "今日やりたいことは？", current)
            if val is not None:
                self.data["goals"]["today"] = val
                self._save()
        finally:
            self._checkin_active = False

    def _do_checkin(self):
        if self._checkin_active:
            return
        self._checkin_active = True
        try:
            self._do_checkin_inner()
        finally:
            self._checkin_active = False

    def _do_checkin_inner(self):
        current = self.data.get("current_task", "")
        if current and not self._break_mode:
            result = show_feedback(current)
            if result == "complete":
                notify("🎉 完了！", current, "素晴らしい！この調子で続けよう！")
            elif result == "progress":
                notify("💪 前進中！", current, "少しでも動けたことが大切！")
            elif result == "replan":
                notify("🔄 賢い判断！", "難しすぎたのかも", "もっと小さなタスクに分けてみよう 💡")

        new_task = show_checkin(self.data["goals"])

        self.data["last_checkin"] = datetime.now().isoformat()
        self._save()

        if new_task is None:
            self._break_mode = True
            self._reset_timer(override_minutes=BREAK_MINUTES)
            self._refresh_ui()
            notify("☕ 休憩スタート！", f"{BREAK_MINUTES}分後にチェックインします", "ゆっくり休んでください")
            return

        self._break_mode = False
        self.data["current_task"] = new_task
        self._save()
        self._reset_timer()
        self._refresh_ui()

        mins = self.data.get("interval_minutes", DEFAULT_INTERVAL)
        notify("スタート！ 🚀", f"今やること: {new_task}", f"{mins}分後にまたチェックインします")

    # ── Menu callbacks ────────────────────────────────────────────────────

    def _cmd_checkin(self, _):
        self._do_checkin()

    def _cmd_edit_long(self, _):
        self._edit_goal("long", "長期目標を変更", "1〜2年後に達成したいことは？")

    def _cmd_edit_mid(self, _):
        self._edit_goal("mid", "中期目標を変更", "1〜5ヶ月で達成したいことは？")

    def _cmd_edit_short(self, _):
        self._edit_goal("short", "短期目標を変更", "今日〜1ヶ月で達成したいことは？")

    def _cmd_edit_weekly(self, _):
        self._edit_goal("weekly", "今週の目標を変更", "今週やりたいことは？")

    def _cmd_edit_today(self, _):
        self._edit_today()


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    ProgressChecker().run()
