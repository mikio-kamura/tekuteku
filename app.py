#!/usr/bin/env python3
"""てくてく — macOS menu bar productivity app with native AppKit dialogs."""
import json
import os
os.environ.setdefault('OS_ACTIVITY_MODE', 'disable')  # suppress macOS framework log noise
import random
from datetime import datetime, timedelta
from typing import List, Optional

import objc
import rumps
from Foundation import NSObject
from AppKit import (
    NSApp,
    NSAppearance,
    NSAttributedString,
    NSButton,
    NSColor,
    NSFont,
    NSForegroundColorAttributeName,
    NSImage,
    NSMakeRect,
    NSMenu,
    NSMenuItem,
    NSEventModifierFlagCommand,
    NSPasteboardTypeString,
    NSScreen,
    NSScrollView,
    NSStrikethroughStyleAttributeName,
    NSTableColumn,
    NSTableView,
    NSButtonCell,
    NSImageView,
    NSTextField,
    NSTextFieldCell,
    NSTextView,
    NSView,
    NSWindow,
)

DATA_FILE = os.path.expanduser("~/.tekuteku.json")
_OLD_DATA_FILE = os.path.expanduser("~/.progress_checker.json")
DEFAULT_INTERVAL = 20  # minutes
BREAK_MINUTES = 5
WEEKDAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
WEEKDAY_JP    = ["月",  "火",  "水",  "木",  "金",  "土",  "日"]

# NSWindowStyleMask: Titled | Closable | Miniaturizable
_STYLE = 1 | 2 | 4
# NSWindowLevel: NSModalPanelWindowLevel相当（floating より前面）
_FLOATING_LEVEL = 8
# NSWindowCollectionBehavior flags
# NOTE: CanJoinAllSpaces and MoveToActiveSpace are mutually exclusive.
_WC_MANAGED = 1 << 2
_WC_CYCLE   = 1 << 5
# Modal return codes
_BTN1, _BTN2, _BTN3 = 1000, 1001, 1002
_CANCEL = -1

_BG = NSColor.colorWithRed_green_blue_alpha_(1.0, 1.0, 1.0, 0.93)
ICON_CANDIDATES = [
    os.path.join(os.path.dirname(__file__), "white-star.png"),
    os.path.join(os.path.dirname(__file__), "assets", "white-star-d57dad08-3034-48d0-9de0-eb6324c055c3.png"),
    "/Users/mikio_kamura/.cursor/projects/Volumes-ssd-pyoi-00-dev-0-personal-diy-qol-tools-progress-checker/assets/white-star-d57dad08-3034-48d0-9de0-eb6324c055c3.png",
]

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
    f = _label(text, rect, font, color, selectable=selectable)
    f.cell().setWraps_(True)
    return f


def _sep(rect) -> NSTextField:
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
    # Explicitly allow normal text-edit behavior (copy/paste/select)
    f.setEditable_(True)
    f.setSelectable_(True)
    if default:
        f.setStringValue_(default)
    return f


def _text_view(items: List[str], rect) -> tuple:
    scroll = NSScrollView.alloc().initWithFrame_(rect)
    scroll.setHasVerticalScroller_(True)
    scroll.setAutohidesScrollers_(True)
    scroll.setBorderType_(2)
    tv = NSTextView.alloc().initWithFrame_(
        NSMakeRect(0, 0, rect.size.width - 16, rect.size.height)
    )
    tv.setFont_(NSFont.systemFontOfSize_(14))
    tv.setRichText_(False)
    tv.setAutomaticLinkDetectionEnabled_(False)
    tv.setString_("\n".join(items))
    tv.setAutoresizingMask_(2)
    scroll.setDocumentView_(tv)
    return scroll, tv


def _normalize_today(items) -> list:
    """Ensure today items are list of {"text": str, "done": bool}."""
    if isinstance(items, str):
        items = [items] if items.strip() else []
    result = []
    for item in items:
        if isinstance(item, str):
            result.append({"text": item, "done": False})
        elif isinstance(item, dict):
            result.append({"text": item.get("text", ""), "done": bool(item.get("done", False))})
    return result


def _truncate10(text: str) -> str:
    s = text or ""
    return s if len(s) <= 10 else s[:10] + "…"


# ── Week helpers ──────────────────────────────────────────────────────────────

def _monday_of(d: datetime) -> datetime:
    return d - timedelta(days=d.weekday())

def _week_range_str(d: datetime) -> str:
    mon = _monday_of(d)
    sun = mon + timedelta(days=6)
    return f"{mon.month}/{mon.day}（月）〜{sun.month}/{sun.day}（日）"

def _date_jp(d: datetime) -> str:
    return f"{d.month}月{d.day}日（{WEEKDAY_JP[d.weekday()]}）"

def _normalize_weekly(weekly) -> dict:
    """Ensure weekly is {"goal": str, "week_start": str, "days": {Mon: [str,...], ...}}."""
    base = {"goal": "", "week_start": "", "days": {k: [] for k in WEEKDAY_NAMES}}
    if isinstance(weekly, str):
        base["goal"] = weekly
        return base
    if not isinstance(weekly, dict):
        return base
    base["goal"] = weekly.get("goal", "")
    base["week_start"] = weekly.get("week_start", "")
    days = weekly.get("days", {})
    for k in WEEKDAY_NAMES:
        raw = days.get(k, [])
        base["days"][k] = _parse_day_tasks(raw)
    return base

def _parse_day_tasks(raw) -> list[str]:
    """Return list of non-empty task strings from list[str|dict] or comma string."""
    if isinstance(raw, str):
        return [s.strip() for s in raw.split(",") if s.strip()]
    if isinstance(raw, list):
        out = []
        for item in raw:
            if isinstance(item, str):
                out.append(item.strip())
            elif isinstance(item, dict):
                out.append((item.get("text") or "").strip())
        return [s for s in out if s]
    return []


def _styled_task_title(text: str, done: bool) -> NSAttributedString:
    color = NSColor.colorWithWhite_alpha_(0.45, 1.0) if done else NSColor.colorWithWhite_alpha_(0.15, 1.0)
    attrs = {NSForegroundColorAttributeName: color}
    if done:
        attrs[NSStrikethroughStyleAttributeName] = 1
    return NSAttributedString.alloc().initWithString_attributes_(text, attrs)


class _TodayTaskTableModel(NSObject):
    def initWithItems_(self, items):
        self = objc.super(_TodayTaskTableModel, self).init()
        if self is None:
            return None
        self.items = list(items or [])
        self.show_numbers = False
        return self

    def numberOfRowsInTableView_(self, _table):
        return len(self.items)

    def tableView_objectValueForTableColumn_row_(self, _table, column, row):
        if 0 <= row < len(self.items):
            if column.identifier() == "done":
                return 1 if self.items[row].get("done") else 0
            text = self.items[row]["text"]
            if self.show_numbers:
                return f"{row + 1}. {text}"
            return text
        return ""

    def tableView_setObjectValue_forTableColumn_row_(self, _table, value, column, row):
        if 0 <= row < len(self.items):
            if column.identifier() == "done":
                self.items[row]["done"] = bool(value)
            else:
                self.items[row]["text"] = str(value or "").strip()

    def tableView_writeRowsWithIndexes_toPasteboard_(self, _table, row_indexes, pasteboard):
        idx = row_indexes.firstIndex()
        pasteboard.declareTypes_owner_([NSPasteboardTypeString], self)
        pasteboard.setString_forType_(str(idx), NSPasteboardTypeString)
        return True

    def tableView_validateDrop_proposedRow_proposedDropOperation_(self, _table, _info, _row, _op):
        return 2  # NSDragOperationMove

    def tableView_acceptDrop_row_dropOperation_(self, _table, info, row, _op):
        raw = info.draggingPasteboard().stringForType_(NSPasteboardTypeString)
        if raw is None:
            return False
        try:
            src = int(raw)
        except ValueError:
            return False
        if not (0 <= src < len(self.items)):
            return False
        item = self.items.pop(src)
        if src < row:
            row -= 1
        row = max(0, min(row, len(self.items)))
        self.items.insert(row, item)
        return True


# ── Custom modal window ───────────────────────────────────────────────────────


class _PinWindowDelegate(NSObject):
    """Delegate for the floating pin window — notifies app when user closes it."""
    app_ref = None

    def windowWillClose_(self, _notif):
        if self.app_ref is not None:
            self.app_ref._on_pin_window_close()


class _Handler(NSObject):
    def click_(self, sender):
        NSApp.stopModalWithCode_(sender.tag())

    def toggle_(self, sender):
        base = str(sender.representedObject() or sender.title())
        sender.setAttributedTitle_(_styled_task_title(base, sender.state() != 0))

    def windowShouldClose_(self, _):
        NSApp.stopModalWithCode_(_CANCEL)
        return False

    def windowDidBecomeKey_(self, notif):
        win = notif.object()
        if win is not None:
            win.orderFrontRegardless()


class _VCenteredCell(NSTextFieldCell):
    """NSTextFieldCell that draws text vertically centered in its bounds."""
    def drawingRectForBounds_(self, rect):
        nr = objc.super(_VCenteredCell, self).drawingRectForBounds_(rect)
        text_size = self.cellSizeForBounds_(rect)
        delta = nr.size.height - text_size.height
        if delta > 0:
            return NSMakeRect(nr.origin.x, nr.origin.y + delta / 2.0,
                              nr.size.width, nr.size.height - delta)
        return nr


class _KeyWindow(NSWindow):
    """NSWindow subclass that handles edit key equivalents directly.
    Necessary for LSUIElement apps where the main menu bar is inactive."""
    def performKeyEquivalent_(self, event):
        if event.modifierFlags() & NSEventModifierFlagCommand:
            sel = {"c": "copy:", "v": "paste:", "x": "cut:",
                   "a": "selectAll:", "z": "undo:"}.get(
                       event.charactersIgnoringModifiers())
            if sel and NSApp.sendAction_to_from_(sel, None, None):
                return True
        return objc.super(_KeyWindow, self).performKeyEquivalent_(event)


_H = _Handler.alloc().init()


def _make_win(title: str, w: int, h: int) -> NSWindow:
    win = _KeyWindow.alloc().initWithContentRect_styleMask_backing_defer_(
        NSMakeRect(0, 0, w, h), _STYLE, 2, False,
    )
    win.setTitle_(title)
    win.center()
    win.setDelegate_(_H)
    # Keep this window in the active Space and cycle list.
    # (Do not combine CanJoinAllSpaces with MoveToActiveSpace.)
    win.setCollectionBehavior_(_WC_MANAGED | _WC_CYCLE)
    win.setOpaque_(False)
    win.setBackgroundColor_(_BG)
    win.setAppearance_(NSAppearance.appearanceNamed_("NSAppearanceNameAqua"))
    win.setLevel_(_FLOATING_LEVEL)  # always on top of other windows
    win.setHidesOnDeactivate_(False)
    return win


def _btn(cv, title: str, code: int, rect, primary: bool = False) -> NSButton:
    b = NSButton.alloc().initWithFrame_(rect)
    b.setTitle_(title)
    b.setBezelStyle_(1)
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
    win.orderFrontRegardless()


def _hide() -> None:
    NSApp.setActivationPolicy_(1)


# ── Dialog functions ──────────────────────────────────────────────────────────


def show_goal_input(title: str, prompt: str, default: str = "") -> Optional[str]:
    W, H = 440, 168
    win = _make_win(title, W, H)
    cv = win.contentView()
    cv.addSubview_(_label(prompt, NSMakeRect(20, 126, W-40, 22), NSFont.boldSystemFontOfSize_(13)))
    field = _input_field(NSMakeRect(20, 82, W-40, 34), NSFont.systemFontOfSize_(15), placeholder="入力…", default=default)
    cv.addSubview_(field)
    err = _label("", NSMakeRect(20, 60, W-40, 18), NSFont.systemFontOfSize_(12), color=NSColor.systemOrangeColor())
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
    """Multi-line text area for entering a list of items (one per line). items must be List[str]."""
    W, H = 440, 268
    win = _make_win(title, W, H)
    cv = win.contentView()
    cv.addSubview_(_label(prompt, NSMakeRect(20, 234, W-40, 22), NSFont.boldSystemFontOfSize_(13)))
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


def show_interval_input(current_minutes: int) -> Optional[int]:
    """Returns new session interval in minutes, or None if cancelled."""
    W, H = 360, 160
    win = _make_win("セッション時間を設定", W, H)
    cv = win.contentView()
    cv.addSubview_(_label("セッションの長さ（分）", NSMakeRect(20, 122, W-40, 22), NSFont.boldSystemFontOfSize_(13)))
    cv.addSubview_(_label(
        "1〜120分で設定できます",
        NSMakeRect(20, 100, W-40, 18),
        NSFont.systemFontOfSize_(12), color=NSColor.colorWithWhite_alpha_(0.5, 1.0),
    ))
    field = _input_field(NSMakeRect(20, 62, 120, 34), NSFont.systemFontOfSize_(15), default=str(current_minutes))
    cv.addSubview_(field)
    err = _label("", NSMakeRect(20, 42, W-40, 18), NSFont.systemFontOfSize_(12), color=NSColor.systemOrangeColor())
    cv.addSubview_(err)
    _btn(cv, "決定", _BTN1, NSMakeRect(W-136, 16, 116, 32), primary=True)
    win.setInitialFirstResponder_(field)
    _show(win)
    try:
        while True:
            resp = NSApp.runModalForWindow_(win)
            if resp == _CANCEL:
                return None
            try:
                val = int(field.stringValue().strip())
                if 1 <= val <= 120:
                    return val
                err.setStringValue_("1〜120の数字を入力してください")
            except ValueError:
                err.setStringValue_("数字を入力してください")
    finally:
        win.orderOut_(None)
        _hide()


def show_weekly_editor(goal: str, days: dict, week_start: str = "") -> Optional[dict]:
    """Edit weekly goal + per-day task lists (comma-separated).
    Returns {"goal": str, "days": {Mon: [str,...], ...}} or None."""
    W, H = 560, 420
    win = _make_win("今週の計画", W, H)
    cv = win.contentView()

    # Compute monday date for labels
    try:
        mon_dt = datetime.strptime(week_start, "%Y-%m-%d") if week_start else None
    except ValueError:
        mon_dt = None

    if mon_dt:
        sun_dt = mon_dt + timedelta(days=6)
        title = f"今週の計画 — {mon_dt.month}/{mon_dt.day}（月）〜{sun_dt.month}/{sun_dt.day}（日）"
    else:
        title = "今週の計画"
    cv.addSubview_(_label(title, NSMakeRect(20, H-34, W-40, 22), NSFont.boldSystemFontOfSize_(13)))
    cv.addSubview_(_label("週の目標", NSMakeRect(20, H-58, 70, 18), NSFont.boldSystemFontOfSize_(12)))
    goal_field = _input_field(NSMakeRect(20, H-96, W-40, 34), NSFont.systemFontOfSize_(14),
                              placeholder="今週達成したいことを入力…", default=goal)
    cv.addSubview_(goal_field)
    cv.addSubview_(_sep(NSMakeRect(20, H-108, W-40, 1)))
    cv.addSubview_(_label(
        "各曜日のタスクをカンマ区切りで入力（例: 資料作成, レビュー）",
        NSMakeRect(20, H-126, W-40, 16),
        NSFont.systemFontOfSize_(11),
        color=NSColor.colorWithWhite_alpha_(0.5, 1.0),
    ))

    day_fields: dict[str, NSTextField] = {}
    for i, (key, jp) in enumerate(zip(WEEKDAY_NAMES, WEEKDAY_JP)):
        y = H - 156 - i * 30
        if mon_dt:
            day_dt = mon_dt + timedelta(days=i)
            day_label = f"{jp} {day_dt.month}/{day_dt.day}"
            lw = 54
        else:
            day_label = jp
            lw = 24
        cv.addSubview_(_label(day_label, NSMakeRect(20, y + 5, lw, 18),
                              NSFont.boldSystemFontOfSize_(12),
                              color=NSColor.colorWithWhite_alpha_(0.3, 1.0)))
        existing = ", ".join(days.get(key, []))
        field = _input_field(NSMakeRect(20 + lw + 6, y + 2, W - 20 - lw - 26, 22),
                             NSFont.systemFontOfSize_(13),
                             placeholder="タスクをカンマ区切りで", default=existing)
        cv.addSubview_(field)
        day_fields[key] = field

    cv.addSubview_(_sep(NSMakeRect(20, 52, W-40, 1)))
    _btn(cv, "決定", _BTN1, NSMakeRect(W-136, 12, 116, 32), primary=True)
    win.setInitialFirstResponder_(goal_field)
    _show(win)
    try:
        resp = NSApp.runModalForWindow_(win)
        if resp == _CANCEL:
            return None
        result_days = {
            key: [s.strip() for s in field.stringValue().split(",") if s.strip()]
            for key, field in day_fields.items()
        }
        return {"goal": goal_field.stringValue().strip(), "days": result_days}
    finally:
        win.orderOut_(None)
        _hide()


def _parse_task_index(raw: str, tasks: list[dict]) -> Optional[str]:
    """Parse 1-based task index and return task text."""
    s = (raw or "").strip()
    if not s:
        return None
    try:
        idx = int(s)
    except ValueError:
        return None
    if 1 <= idx <= len(tasks):
        return tasks[idx - 1]["text"]
    return None


def show_today_task_editor(title: str, items: list[dict]) -> Optional[list]:
    """Task editor with add/delete and drag & drop reorder."""
    W, H = 520, 360
    win = _make_win(title, W, H)
    cv = win.contentView()
    cv.addSubview_(_label("今日行う細分タスク（ドラッグで並び替え）", NSMakeRect(20, 328, W-40, 20), NSFont.boldSystemFontOfSize_(13)))

    model = _TodayTaskTableModel.alloc().initWithItems_(_normalize_today(items))
    scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(20, 96, W - 40, 224))
    scroll.setHasVerticalScroller_(True)
    scroll.setAutohidesScrollers_(True)
    scroll.setBorderType_(2)

    table = NSTableView.alloc().initWithFrame_(NSMakeRect(0, 0, W - 56, 224))
    col = NSTableColumn.alloc().initWithIdentifier_("task")
    col.setWidth_(W - 56)
    col.setEditable_(True)
    table.addTableColumn_(col)
    table.setHeaderView_(None)
    table.setUsesAlternatingRowBackgroundColors_(True)
    table.setAllowsMultipleSelection_(False)
    table.setAllowsEmptySelection_(True)
    table.setDataSource_(model)
    table.setDelegate_(model)
    table.setDraggingSourceOperationMask_forLocal_(2, True)  # move
    table.registerForDraggedTypes_([NSPasteboardTypeString])
    scroll.setDocumentView_(table)
    cv.addSubview_(scroll)

    input_field = _input_field(NSMakeRect(20, 58, W - 170, 30), NSFont.systemFontOfSize_(14), placeholder="細分タスクを入力して追加")
    cv.addSubview_(input_field)
    err = _label("", NSMakeRect(20, 40, W - 40, 16), NSFont.systemFontOfSize_(12), color=NSColor.systemOrangeColor())
    cv.addSubview_(err)

    _btn(cv, "追加", _BTN3, NSMakeRect(W - 140, 58, 60, 30))
    _btn(cv, "削除", _BTN2, NSMakeRect(W - 74, 58, 54, 30))
    _btn(cv, "決定", _BTN1, NSMakeRect(W - 136, 10, 116, 28), primary=False)
    # Enter in input field should add item, not submit dialog.
    input_field.setTag_(_BTN3)
    input_field.setTarget_(_H)
    input_field.setAction_("click:")
    win.setInitialFirstResponder_(input_field)
    _show(win)
    try:
        while True:
            resp = NSApp.runModalForWindow_(win)
            if resp == _CANCEL:
                return None
            if resp == _BTN3:
                text = input_field.stringValue().strip()
                if not text:
                    err.setStringValue_("追加するタスクを入力してください")
                    continue
                model.items.append({"text": text, "done": False})
                input_field.setStringValue_("")
                table.reloadData()
                continue
            if resp == _BTN2:
                row = table.selectedRow()
                if row < 0 or row >= len(model.items):
                    err.setStringValue_("削除する行を選択してください")
                    continue
                model.items.pop(row)
                table.reloadData()
                continue
            clean = []
            for item in model.items:
                text = (item.get("text") or "").strip()
                if text:
                    clean.append({"text": text, "done": bool(item.get("done", False))})
            return clean
    finally:
        win.orderOut_(None)
        _hide()


SAM_IMG = os.path.join(os.path.dirname(__file__), "sam.png")


def show_checkin(
    goals: dict,
    today_date: str = "",
    current_task: str = "",
    queued_task: str = "",
    queued_next_task: str = "",
    current_message: str = "",
    current_interval: int = DEFAULT_INTERVAL,
    active_tries: Optional[list] = None,
) -> tuple[str, Optional[str], Optional[str], Optional[str], Optional[int], list]:
    """Returns (action, next_task, next_next_task, message, session_minutes, updated_today_items).
    action is one of: start, break, edit_today."""
    active_tries = active_tries or []
    today_items = _normalize_today(goals.get("today", []))
    try:
        _today_dt = datetime.strptime(today_date, "%Y-%m-%d") if today_date else datetime.now()
    except ValueError:
        _today_dt = datetime.now()
    n = len(today_items)

    X = 220           # left image column width (2× for larger character)
    ITEM_H = 22       # NSTableView row height
    MAX_VIS = 5       # max rows before scroll kicks in
    GAP = 6
    FIXED_BOTTOM = 428
    items_bottom_y = FIXED_BOTTOM + GAP
    items_section_h = min(max(n, 1), MAX_VIS) * ITEM_H if n else 22
    today_label_y = items_bottom_y + items_section_h + GAP
    H = today_label_y + 20 + 16

    W = X + 480
    win = _make_win("チェックイン", W, H)
    cv = win.contentView()

    # ── 左列：サム画像（下部）+ Tryリスト（サムの真上、上部は余白）──────────
    _LX = 8
    _LINE_H = 18    # height per try item row
    _ITEM_GAP = 6   # gap between items
    _LABEL_H = 14   # "Try" heading height
    _LABEL_GAP = 4  # gap between heading and first item
    _LIST_PAD = 14  # top/bottom padding of list (> _ITEM_GAP)

    _n_try = len(active_tries)
    _list_content_h = (
        _LABEL_H + _LABEL_GAP + _n_try * _LINE_H + max(0, _n_try - 1) * _ITEM_GAP
    ) if _n_try else 0
    _try_col_h = (2 * _LIST_PAD + _list_content_h) if _n_try else 0

    _sam_h = min(int(H * 0.60), H - _try_col_h - _LX * 2)
    _sam_h = max(_sam_h, 40)

    if os.path.exists(SAM_IMG):
        _img = NSImage.alloc().initByReferencingFile_(SAM_IMG)
        if _img:
            iv = NSImageView.alloc().initWithFrame_(NSMakeRect(_LX, _LX, X - _LX * 2, _sam_h))
            iv.setImage_(_img)
            iv.setImageScaling_(3)
            iv.setImageAlignment_(5)
            cv.addSubview_(iv)

    if active_tries:
        _sam_top = _LX + _sam_h
        # Bullet items: i=0 topmost, i=N-1 closest to Sam
        for _ti, _try_text in enumerate(active_tries):
            _item_y = _sam_top + _LIST_PAD + (_n_try - 1 - _ti) * (_LINE_H + _ITEM_GAP)
            cv.addSubview_(_mlabel(
                f"・{_try_text}",
                NSMakeRect(_LX, _item_y, X - _LX * 2, _LINE_H),
                NSFont.systemFontOfSize_(13),
                color=NSColor.colorWithWhite_alpha_(0.55, 1.0),
            ))
        # "Try" heading above the items
        _heading_y = (
            _sam_top + _LIST_PAD
            + _n_try * _LINE_H + max(0, _n_try - 1) * _ITEM_GAP + _LABEL_GAP
        )
        cv.addSubview_(_label(
            "Try",
            NSMakeRect(_LX, _heading_y, X - _LX * 2, _LABEL_H),
            NSFont.boldSystemFontOfSize_(11),
            color=NSColor.colorWithWhite_alpha_(0.40, 1.0),
        ))

    # ── 今日やりたいこと（ドラッグで並び替え可）──────────────────────────────
    cv.addSubview_(_label(
        f"📅  今日やりたいこと — {_date_jp(_today_dt)}",
        NSMakeRect(X + 20, today_label_y, W - X - 40, 20),
        NSFont.boldSystemFontOfSize_(13),
        color=NSColor.systemBlueColor(),
    ))
    today_model = _TodayTaskTableModel.alloc().initWithItems_(today_items)
    today_model.show_numbers = True
    scroll_h = min(max(n, 1), MAX_VIS) * ITEM_H if n else 22
    scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(X + 24, items_bottom_y, W - X - 44, scroll_h))
    scroll.setHasVerticalScroller_(n > MAX_VIS)
    scroll.setAutohidesScrollers_(True)
    scroll.setBorderType_(2)
    table_w = W - X - 60
    today_table = NSTableView.alloc().initWithFrame_(NSMakeRect(0, 0, table_w, scroll_h))
    today_table.setRowHeight_(float(ITEM_H - 2))
    # Checkbox column
    _done_col = NSTableColumn.alloc().initWithIdentifier_("done")
    _done_col.setWidth_(22)
    _done_col.setEditable_(True)
    _bcell = NSButtonCell.alloc().init()
    _bcell.setButtonType_(3)   # NSSwitchButton = checkbox
    _bcell.setTitle_("")
    _bcell.setControlSize_(1)  # NSControlSizeSmall
    _done_col.setDataCell_(_bcell)
    today_table.addTableColumn_(_done_col)
    # Task text column
    _task_col = NSTableColumn.alloc().initWithIdentifier_("task")
    _task_col.setWidth_(table_w - 22 - 4)
    _task_col.setEditable_(False)
    _task_col.dataCell().setFont_(NSFont.systemFontOfSize_(13))
    today_table.addTableColumn_(_task_col)
    today_table.setHeaderView_(None)
    today_table.setUsesAlternatingRowBackgroundColors_(True)
    today_table.setAllowsMultipleSelection_(False)
    today_table.setAllowsEmptySelection_(True)
    today_table.setDataSource_(today_model)
    today_table.setDelegate_(today_model)
    today_table.setDraggingSourceOperationMask_forLocal_(2, True)
    today_table.registerForDraggedTypes_([NSPasteboardTypeString])
    scroll.setDocumentView_(today_table)
    cv.addSubview_(scroll)
    if not today_items:
        cv.addSubview_(_mlabel("未設定（📝ボタンで追加）", NSMakeRect(X + 28, items_bottom_y + 2, W - X - 48, 18), NSFont.systemFontOfSize_(13)))

    cv.addSubview_(_sep(NSMakeRect(X + 20, FIXED_BOTTOM, W - X - 40, 1)))

    # ── 今週（目標 + 曜日別タスク一覧）──────────────────────────────────────
    _weekly = goals.get("weekly", {})
    _weekly_goal = (_weekly.get("goal") if isinstance(_weekly, dict) else str(_weekly or "")) or "未設定"
    _weekly_days = _weekly.get("days", {}) if isinstance(_weekly, dict) else {}
    cv.addSubview_(_label(
        f"📋  今週 — {_week_range_str(_today_dt)}",
        NSMakeRect(X + 20, 410, W - X - 40, 16),
        NSFont.boldSystemFontOfSize_(13),
    ))
    cv.addSubview_(_mlabel(
        _weekly_goal,
        NSMakeRect(X + 28, 394, W - X - 48, 14),
        NSFont.systemFontOfSize_(13),
        color=NSColor.colorWithWhite_alpha_(0.3, 1.0),
    ))
    # Per-day tasks (scrollable, compact)
    _mon = _monday_of(_today_dt)
    _day_lines = []
    for _i, (_key, _jp) in enumerate(zip(WEEKDAY_NAMES, WEEKDAY_JP)):
        _ddt = _mon + timedelta(days=_i)
        _tasks = _parse_day_tasks(_weekly_days.get(_key, []))
        _is_today = _ddt.date() == _today_dt.date()
        _prefix = f"▶{_jp} {_ddt.month}/{_ddt.day}" if _is_today else f"  {_jp} {_ddt.month}/{_ddt.day}"
        _day_lines.append(f"{_prefix}: {', '.join(_tasks) if _tasks else '—'}")
    _day_scroll, _day_tv = _text_view(_day_lines, NSMakeRect(X + 24, 322, W - X - 44, 70))
    _day_tv.setEditable_(False)
    _day_tv.setFont_(NSFont.systemFontOfSize_(11))
    cv.addSubview_(_day_scroll)
    cv.addSubview_(_sep(NSMakeRect(X + 20, 314, W - X - 40, 1)))

    # ── 短期目標 ────────────────────────────────────────────────────────────
    cv.addSubview_(_label("📌  短期目標", NSMakeRect(X + 20, 294, W - X - 40, 16), NSFont.boldSystemFontOfSize_(13)))
    cv.addSubview_(_mlabel(goals.get("short") or "未設定", NSMakeRect(X + 28, 270, W - X - 48, 22), NSFont.systemFontOfSize_(13)))
    cv.addSubview_(_sep(NSMakeRect(X + 20, 262, W - X - 40, 1)))

    # ── 中期目標 ────────────────────────────────────────────────────────────
    cv.addSubview_(_label("📅  中期目標", NSMakeRect(X + 20, 242, W - X - 40, 16), NSFont.boldSystemFontOfSize_(13)))
    cv.addSubview_(_mlabel(goals.get("mid") or "未設定", NSMakeRect(X + 28, 218, W - X - 48, 22), NSFont.systemFontOfSize_(13)))
    cv.addSubview_(_sep(NSMakeRect(X + 20, 210, W - X - 40, 1)))

    # ── 長期目標 ────────────────────────────────────────────────────────────
    cv.addSubview_(_label("🌟  長期目標", NSMakeRect(X + 20, 190, W - X - 40, 16), NSFont.boldSystemFontOfSize_(13)))
    cv.addSubview_(_mlabel(goals.get("long") or "未設定", NSMakeRect(X + 28, 166, W - X - 48, 22), NSFont.systemFontOfSize_(13)))
    cv.addSubview_(_sep(NSMakeRect(X + 20, 158, W - X - 40, 1)))

    # ── 次回/次々回の選択 + セッション時間 + メッセージ ──────────────────────
    cv.addSubview_(_label("次のセッション", NSMakeRect(X + 20,  132, 95, 16), NSFont.systemFontOfSize_(12)))
    cv.addSubview_(_label("その次（任意）", NSMakeRect(X + 123, 132, 95, 16), NSFont.systemFontOfSize_(12)))
    cv.addSubview_(_label("⏱分",           NSMakeRect(X + 230, 132, 40, 16), NSFont.systemFontOfSize_(12)))

    default_next = ""
    default_next_next = ""
    if today_items:
        task_to_index = {item["text"]: str(i + 1) for i, item in enumerate(today_items)}
        default_next = task_to_index.get(queued_task, "")
        default_next_next = task_to_index.get(queued_next_task, "")
        if not default_next:
            for i, item in enumerate(today_items):
                if not item["done"]:
                    default_next = str(i + 1)
                    break
        if not default_next:
            default_next = "1"

    field_next     = _input_field(NSMakeRect(X + 20,  112, 95, 26), NSFont.systemFontOfSize_(15), placeholder="番号", default=default_next)
    field_next_next= _input_field(NSMakeRect(X + 123, 112, 95, 26), NSFont.systemFontOfSize_(15), placeholder="任意", default=default_next_next)
    field_session  = _input_field(NSMakeRect(X + 230, 112, 56, 26), NSFont.systemFontOfSize_(15), default=str(current_interval))
    cv.addSubview_(field_next)
    cv.addSubview_(field_next_next)
    cv.addSubview_(field_session)

    cv.addSubview_(_label("コメント（メニューバーに表示）", NSMakeRect(X + 20, 90, W - X - 40, 16), NSFont.systemFontOfSize_(12)))
    field_msg = _input_field(NSMakeRect(X + 20, 64, W - X - 40, 24), NSFont.systemFontOfSize_(14),
                             placeholder="例: 焦らず1行だけ進める", default=current_message)
    cv.addSubview_(field_msg)
    err = _label("", NSMakeRect(X + 20, 44, W - X - 40, 16), NSFont.systemFontOfSize_(12), color=NSColor.systemOrangeColor())
    cv.addSubview_(err)

    # ── ボタン ──────────────────────────────────────────────────────────────
    _btn(cv, "スタート！",                _BTN1, NSMakeRect(W - 160, 8, 140, 28), primary=True)
    _btn(cv, f"☕  {BREAK_MINUTES}分休憩", _BTN2, NSMakeRect(W - 312, 8, 140, 28))
    _btn(cv, "📝 細分タスク編集",          _BTN3, NSMakeRect(X + 20, 8, 128, 28))

    win.setInitialFirstResponder_(field_next)
    _show(win)
    try:
        while True:
            resp = NSApp.runModalForWindow_(win)
            updated_today = [
                {"text": item["text"], "done": bool(item.get("done", False))}
                for item in today_model.items
                if (item.get("text") or "").strip()
            ]
            if resp in (_CANCEL, _BTN2):
                return "break", None, None, None, None, updated_today
            if resp == _BTN3:
                return "edit_today", None, None, None, None, updated_today

            if not today_items:
                err.setStringValue_("先に細分タスクを追加してください")
                continue

            next_task      = _parse_task_index(field_next.stringValue(), today_items)
            next_next_task = _parse_task_index(field_next_next.stringValue(), today_items)
            msg = field_msg.stringValue().strip()

            try:
                sv = int(field_session.stringValue().strip())
                if not (1 <= sv <= 120):
                    raise ValueError
                session_mins = sv
            except ValueError:
                err.setStringValue_("セッション時間は1〜120の数字で入力してください")
                continue

            if not next_task:
                err.setStringValue_("次のセッションの番号を入力してください")
                continue
            if field_next_next.stringValue().strip() and not next_next_task:
                err.setStringValue_("その次の番号が不正です")
                continue
            return "start", next_task, next_next_task, msg, session_mins, updated_today
    finally:
        win.orderOut_(None)
        _hide()


def show_feedback(task: str) -> str:
    W, H = 420, 172
    win = _make_win("セッション振り返り", W, H)
    cv = win.contentView()
    short = (task[:44] + "…") if len(task) > 44 else task
    cv.addSubview_(_mlabel(f"「{short}」", NSMakeRect(20, 120, W-40, 38), NSFont.boldSystemFontOfSize_(16)))
    cv.addSubview_(_label("どのくらい進みましたか？", NSMakeRect(20, 90, W-40, 22), NSFont.systemFontOfSize_(13)))
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


def show_history(history: list) -> None:
    """Read-only viewer for past daily task records."""
    W, H = 480, 520
    win = _make_win("過去の記録", W, H)
    cv = win.contentView()

    cv.addSubview_(_label(
        "過去の記録",
        NSMakeRect(20, H - 40, W - 40, 24),
        NSFont.boldSystemFontOfSize_(15),
        color=NSColor.colorWithWhite_alpha_(0.15, 1.0),
    ))

    lines: list[str] = []
    for entry in reversed(history):
        date = entry.get("date", "")
        tasks = entry.get("tasks", [])
        done_count = sum(1 for t in tasks if t.get("done"))
        lines.append(f"─── {date}  ({done_count}/{len(tasks)} 完了) ───")
        if tasks:
            for task in tasks:
                mark = "✅" if task.get("done") else "☐"
                lines.append(f"  {mark}  {task.get('text', '')}")
        else:
            lines.append("  (タスクなし)")
        lines.append("")

    if not lines:
        lines = ["まだ記録がありません。"]

    scroll, tv = _text_view(lines, NSMakeRect(20, 52, W - 40, H - 108))
    tv.setEditable_(False)
    cv.addSubview_(scroll)

    _btn(cv, "閉じる", _BTN1, NSMakeRect(W - 136, 12, 116, 32), primary=True)
    _show(win)
    try:
        NSApp.runModalForWindow_(win)
    finally:
        win.orderOut_(None)
        _hide()


def show_kpt_editor(keep: list, problem: list, try_: list) -> Optional[dict]:
    """KPT retrospective editor.
    Returns {"keep": [...], "problem": [...], "try": [...]} or None."""
    W, H = 580, 520
    win = _make_win("今日の振り返り（KPT）", W, H)
    cv = win.contentView()

    cv.addSubview_(_label("今日の振り返り（KPT）", NSMakeRect(20, H - 34, W - 40, 22), NSFont.boldSystemFontOfSize_(13)))
    cv.addSubview_(_label(
        "1行に1つ入力してください",
        NSMakeRect(20, H - 54, W - 40, 16),
        NSFont.systemFontOfSize_(11), color=NSColor.colorWithWhite_alpha_(0.5, 1.0),
    ))

    col_w = (W - 60) // 3  # ≈ 173
    headers = [
        ("✅ Keep",    "うまくいったこと", keep),
        ("⚠️ Problem", "改善できること",   problem),
        ("🚀 Try",     "次に試すこと",     try_),
    ]
    text_views = []
    for idx, (title, subtitle, items) in enumerate(headers):
        x = 20 + idx * (col_w + 10)
        cv.addSubview_(_label(title, NSMakeRect(x, H - 76, col_w, 18), NSFont.boldSystemFontOfSize_(12)))
        cv.addSubview_(_label(
            subtitle, NSMakeRect(x, H - 96, col_w, 16),
            NSFont.systemFontOfSize_(11), color=NSColor.colorWithWhite_alpha_(0.5, 1.0),
        ))
        scroll, tv = _text_view(items, NSMakeRect(x, 52, col_w, H - 152))
        tv.setFont_(NSFont.systemFontOfSize_(13))
        cv.addSubview_(scroll)
        text_views.append(tv)

    _btn(cv, "決定", _BTN1, NSMakeRect(W - 136, 12, 116, 32), primary=True)
    win.makeFirstResponder_(text_views[0])
    _show(win)
    try:
        resp = NSApp.runModalForWindow_(win)
        if resp == _CANCEL:
            return None
        def _parse(tv):
            return [l.strip() for l in str(tv.string()).split("\n") if l.strip()]
        return {"keep": _parse(text_views[0]), "problem": _parse(text_views[1]), "try": _parse(text_views[2])}
    finally:
        win.orderOut_(None)
        _hide()


def show_try_selector(try_items: list) -> list:
    """Checkbox + drag-and-drop dialog for selecting and reordering Try items to carry forward."""
    if not try_items:
        return []
    n = len(try_items)
    W = 460
    ITEM_H = 24
    MAX_VIS = 6
    table_h = min(n, MAX_VIS) * ITEM_H
    H = max(180, table_h + 110)

    win = _make_win("明日に持ち越すTryを選ぶ", W, H)
    cv = win.contentView()
    cv.addSubview_(_label(
        "持ち越すTryを選択・並び替え",
        NSMakeRect(20, H - 34, W - 40, 22), NSFont.boldSystemFontOfSize_(13),
    ))
    cv.addSubview_(_label(
        "チェックで選択、ドラッグで順番を変更",
        NSMakeRect(20, H - 54, W - 40, 16),
        NSFont.systemFontOfSize_(11), color=NSColor.colorWithWhite_alpha_(0.5, 1.0),
    ))

    items = [{"text": t, "done": True} for t in try_items]
    model = _TodayTaskTableModel.alloc().initWithItems_(items)

    scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(20, 52, W - 40, table_h))
    scroll.setHasVerticalScroller_(n > MAX_VIS)
    scroll.setAutohidesScrollers_(True)
    scroll.setBorderType_(2)

    table_w = W - 56
    table = NSTableView.alloc().initWithFrame_(NSMakeRect(0, 0, table_w, table_h))
    table.setRowHeight_(float(ITEM_H - 2))

    done_col = NSTableColumn.alloc().initWithIdentifier_("done")
    done_col.setWidth_(22)
    done_col.setEditable_(True)
    bcell = NSButtonCell.alloc().init()
    bcell.setButtonType_(3)
    bcell.setTitle_("")
    bcell.setControlSize_(1)
    done_col.setDataCell_(bcell)
    table.addTableColumn_(done_col)

    task_col = NSTableColumn.alloc().initWithIdentifier_("task")
    task_col.setWidth_(table_w - 22 - 4)
    task_col.setEditable_(False)
    task_col.dataCell().setFont_(NSFont.systemFontOfSize_(13))
    table.addTableColumn_(task_col)

    table.setHeaderView_(None)
    table.setUsesAlternatingRowBackgroundColors_(True)
    table.setAllowsMultipleSelection_(False)
    table.setAllowsEmptySelection_(True)
    table.setDataSource_(model)
    table.setDelegate_(model)
    table.setDraggingSourceOperationMask_forLocal_(2, True)
    table.registerForDraggedTypes_([NSPasteboardTypeString])
    scroll.setDocumentView_(table)
    cv.addSubview_(scroll)

    _btn(cv, "決定", _BTN1, NSMakeRect(W - 136, 12, 116, 32), primary=True)
    _show(win)
    try:
        resp = NSApp.runModalForWindow_(win)
        if resp == _CANCEL:
            return []
        return [item["text"] for item in model.items if item.get("done")]
    finally:
        win.orderOut_(None)
        _hide()


def show_kpt_history(kpt_history: list) -> None:
    """Read-only viewer for past KPT retrospective records."""
    W, H = 520, 560
    win = _make_win("過去の振り返り記録", W, H)
    cv = win.contentView()
    cv.addSubview_(_label(
        "過去の振り返り記録",
        NSMakeRect(20, H - 40, W - 40, 24),
        NSFont.boldSystemFontOfSize_(15),
        color=NSColor.colorWithWhite_alpha_(0.15, 1.0),
    ))
    lines: list[str] = []
    for entry in reversed(kpt_history):
        date = entry.get("date", "")
        lines.append(f"─── {date} ───")
        for section, label in (("keep", "✅ Keep"), ("problem", "⚠️ Problem"), ("try", "🚀 Try")):
            lines.append(label)
            items = entry.get(section, [])
            for item in items:
                lines.append(f"  • {item}")
            if not items:
                lines.append("  （なし）")
        lines.append("")
    if not lines:
        lines = ["まだ振り返り記録がありません。"]
    scroll, tv = _text_view(lines, NSMakeRect(20, 52, W - 40, H - 108))
    tv.setEditable_(False)
    cv.addSubview_(scroll)
    _btn(cv, "閉じる", _BTN1, NSMakeRect(W - 136, 12, 116, 32), primary=True)
    _show(win)
    try:
        NSApp.runModalForWindow_(win)
    finally:
        win.orderOut_(None)
        _hide()


def show_weekly_review(
    week_start_str: str,
    daily_summaries: list,
    prev_keep: list,
    prev_problem: list,
    prev_try: list,
) -> Optional[dict]:
    """Weekly retrospective: shows last week's tasks + KPT + summary comment.
    Returns {"keep": [...], "problem": [...], "try": [...], "summary": str} or None (skipped)."""
    W, H = 600, 680
    win = _make_win("先週の振り返り", W, H)
    cv = win.contentView()

    try:
        mon_dt = datetime.strptime(week_start_str, "%Y-%m-%d")
        sun_dt = mon_dt + timedelta(days=6)
        title_text = (
            f"先週の振り返り — {mon_dt.month}/{mon_dt.day}（月）〜"
            f"{sun_dt.month}/{sun_dt.day}（日）"
        )
    except (ValueError, TypeError):
        title_text = "先週の振り返り"

    cv.addSubview_(_label(title_text, NSMakeRect(20, 642, W-40, 22), NSFont.boldSystemFontOfSize_(13)))

    # Task summary
    cv.addSubview_(_label("先週のタスク", NSMakeRect(20, 618, 100, 16), NSFont.boldSystemFontOfSize_(12)))
    lines: list[str] = []
    if daily_summaries:
        for entry in daily_summaries:
            date = entry.get("date", "")
            tasks = entry.get("tasks", [])
            done_count = sum(1 for t in tasks if t.get("done"))
            try:
                d = datetime.strptime(date, "%Y-%m-%d")
                date_label = f"─── {d.month}/{d.day}（{WEEKDAY_JP[d.weekday()]}）  {done_count}/{len(tasks)} 完了"
            except (ValueError, TypeError):
                date_label = f"─── {date}  {done_count}/{len(tasks)} 完了"
            lines.append(date_label)
            for task in tasks:
                mark = "✅" if task.get("done") else "☐"
                lines.append(f"  {mark}  {task.get('text', '')}")
            lines.append("")
    else:
        lines = ["先週の記録はありません。"]
    scroll, tv = _text_view(lines, NSMakeRect(20, 420, W-40, 196))
    tv.setEditable_(False)
    tv.setFont_(NSFont.systemFontOfSize_(12))
    cv.addSubview_(scroll)

    cv.addSubview_(_sep(NSMakeRect(20, 410, W-40, 1)))

    # KPT section
    cv.addSubview_(_label("KPT振り返り", NSMakeRect(20, 390, 100, 16), NSFont.boldSystemFontOfSize_(12)))
    cv.addSubview_(_label(
        "1行に1つ入力してください",
        NSMakeRect(20, 372, W-40, 14),
        NSFont.systemFontOfSize_(11), color=NSColor.colorWithWhite_alpha_(0.5, 1.0),
    ))

    col_w = (W - 60) // 3  # 180
    headers = [
        ("✅ Keep", "うまくいったこと", prev_keep),
        ("⚠️ Problem", "改善できること", prev_problem),
        ("🚀 Try", "次に試すこと", prev_try),
    ]
    text_views = []
    for idx, (h_title, h_sub, items) in enumerate(headers):
        x = 20 + idx * (col_w + 10)
        cv.addSubview_(_label(h_title, NSMakeRect(x, 348, col_w, 18), NSFont.boldSystemFontOfSize_(12)))
        cv.addSubview_(_label(
            h_sub, NSMakeRect(x, 328, col_w, 14),
            NSFont.systemFontOfSize_(11), color=NSColor.colorWithWhite_alpha_(0.5, 1.0),
        ))
        scroll2, tv2 = _text_view(items, NSMakeRect(x, 98, col_w, 226))
        tv2.setFont_(NSFont.systemFontOfSize_(13))
        cv.addSubview_(scroll2)
        text_views.append(tv2)

    # Summary comment
    cv.addSubview_(_label(
        "今週を一言でまとめると",
        NSMakeRect(20, 78, W-40, 16),
        NSFont.boldSystemFontOfSize_(12),
    ))
    summary_field = _input_field(NSMakeRect(20, 50, W-40, 24), NSFont.systemFontOfSize_(13),
                                 placeholder="例: 集中できた週だった、疲れ気味だったが粘れた…")
    cv.addSubview_(summary_field)

    _btn(cv, "スキップ", _BTN2, NSMakeRect(W-264, 12, 116, 32))
    _btn(cv, "決定", _BTN1, NSMakeRect(W-136, 12, 116, 32), primary=True)
    win.makeFirstResponder_(text_views[0])
    _show(win)
    try:
        resp = NSApp.runModalForWindow_(win)
        if resp in (_CANCEL, _BTN2):
            return None
        def _parse_tv(t):
            return [l.strip() for l in str(t.string()).split("\n") if l.strip()]
        return {
            "keep": _parse_tv(text_views[0]),
            "problem": _parse_tv(text_views[1]),
            "try": _parse_tv(text_views[2]),
            "summary": summary_field.stringValue().strip(),
        }
    finally:
        win.orderOut_(None)
        _hide()


def show_weekly_review_history(weekly_reviews: list) -> None:
    """Read-only viewer for past weekly retrospective records."""
    W, H = 520, 560
    win = _make_win("週次振り返りの記録", W, H)
    cv = win.contentView()
    cv.addSubview_(_label(
        "週次振り返りの記録",
        NSMakeRect(20, H-40, W-40, 24),
        NSFont.boldSystemFontOfSize_(15),
        color=NSColor.colorWithWhite_alpha_(0.15, 1.0),
    ))
    lines: list[str] = []
    for entry in reversed(weekly_reviews):
        ws = entry.get("week_start", "")
        we = entry.get("week_end", "")
        try:
            mon_dt = datetime.strptime(ws, "%Y-%m-%d")
            sun_dt = datetime.strptime(we, "%Y-%m-%d")
            week_label = f"─── {mon_dt.month}/{mon_dt.day}（月）〜{sun_dt.month}/{sun_dt.day}（日）"
        except (ValueError, TypeError):
            week_label = f"─── {ws} 〜 {we}"
        lines.append(week_label)
        kpt = entry.get("kpt", {})
        summary = kpt.get("summary", "")
        if summary:
            lines.append(f"  💬 {summary}")
        for section, label in (("keep", "✅ Keep"), ("problem", "⚠️ Problem"), ("try", "🚀 Try")):
            lines.append(f"  {label}")
            items = kpt.get(section, [])
            for item in items:
                lines.append(f"    • {item}")
            if not items:
                lines.append("    （なし）")
        lines.append("")
    if not lines:
        lines = ["まだ週次振り返りの記録がありません。"]
    scroll, tv = _text_view(lines, NSMakeRect(20, 52, W-40, H-108))
    tv.setEditable_(False)
    cv.addSubview_(scroll)
    _btn(cv, "閉じる", _BTN1, NSMakeRect(W-136, 12, 116, 32), primary=True)
    _show(win)
    try:
        NSApp.runModalForWindow_(win)
    finally:
        win.orderOut_(None)
        _hide()


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
        self._pin_win = None
        self._pin_msg_label = None
        self._pin_delegate = None

        self._task_item = rumps.MenuItem("📌 タスク未設定", callback=None)
        self._message_item = rumps.MenuItem("💬 コメント: 未設定", callback=None)
        self._next_item = rumps.MenuItem("⏭ 次: 未設定", callback=None)
        self._pin_item = rumps.MenuItem("📌 サムをピン留め", callback=self._cmd_toggle_pin)
        self.menu = [
            self._task_item,
            self._message_item,
            self._next_item,
            None,
            rumps.MenuItem("🔄 今すぐチェックイン",    callback=self._cmd_checkin),
            rumps.MenuItem("⏱ セッション時間を変更",  callback=self._cmd_edit_interval),
            self._pin_item,
            rumps.MenuItem("✉️ サムのメッセージを編集", callback=self._cmd_edit_sam_messages),
            None,
            rumps.MenuItem("🌟 長期目標を変更",        callback=self._cmd_edit_long),
            rumps.MenuItem("📅 中期目標を変更",        callback=self._cmd_edit_mid),
            rumps.MenuItem("📌 短期目標を変更",        callback=self._cmd_edit_short),
            rumps.MenuItem("📋 今週の計画を変更",        callback=self._cmd_edit_weekly),
            rumps.MenuItem("🗓  今日の目標を変更",      callback=self._cmd_edit_today),
            None,
            rumps.MenuItem("📜 過去の記録を見る",         callback=self._cmd_show_history),
            None,
            rumps.MenuItem("📝 今日の振り返り（KPT）",    callback=self._cmd_do_retrospective),
            rumps.MenuItem("📊 過去の振り返りを見る",      callback=self._cmd_show_kpt_history),
            rumps.MenuItem("📆 週次振り返りを見る",        callback=self._cmd_show_weekly_review_history),
            None,
            rumps.MenuItem("❌ 終了", callback=rumps.quit_application),
        ]
        self._start_timer()
        self._start_watchdog()
        self._start_ui_timer()
        self._install_edit_shortcuts()
        self._apply_app_icon()
        self._refresh_ui()
        self._check_date_change()
        self._check_week_change()

        if not self.data["goals"].get("short"):
            rumps.Timer(self._first_run, 1).start()

    # ── Persistence ───────────────────────────────────────────────────────

    def _load(self) -> dict:
        if not os.path.exists(DATA_FILE) and os.path.exists(_OLD_DATA_FILE):
            os.rename(_OLD_DATA_FILE, DATA_FILE)
        if os.path.exists(DATA_FILE):
            try:
                with open(DATA_FILE) as f:
                    data = json.load(f)
                g = data.setdefault("goals", {})
                for key in ("long", "mid", "short"):
                    g.setdefault(key, "")
                g["weekly"] = _normalize_weekly(g.get("weekly", ""))
                g["today"] = _normalize_today(g.get("today", []))
                data.setdefault("next_task", "")
                data.setdefault("next_next_task", "")
                data.setdefault("current_message", "")
                data.setdefault("sam_messages", [])
                data.setdefault("sam_message", "")
                data.setdefault("kpt", {"date": "", "keep": [], "problem": [], "try": []})
                data.setdefault("kpt_history", [])
                data.setdefault("weekly_review_history", [])
                data.setdefault("active_tries", [])
                data.setdefault("today_date", datetime.now().strftime("%Y-%m-%d"))
                data.setdefault("history", [])
                return data
            except Exception:
                pass
        return {
            "goals": {
                "long": "", "mid": "", "short": "",
                "weekly": {"goal": "", "week_start": "", "days": {k: [] for k in WEEKDAY_NAMES}},
                "today": [],
            },
            "current_task": "",
            "next_task": "",
            "next_next_task": "",
            "current_message": "",
            "sam_messages": [],
            "sam_message": "",
            "kpt": {"date": "", "keep": [], "problem": [], "try": []},
            "kpt_history": [],
            "active_tries": [],
            "interval_minutes": DEFAULT_INTERVAL,
            "today_date": datetime.now().strftime("%Y-%m-%d"),
            "history": [],
        }

    def _save(self):
        with open(DATA_FILE, "w") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)

    # ── Display ───────────────────────────────────────────────────────────

    def _refresh_ui(self):
        task = self.data.get("current_task") or "タスク未設定"
        msg = self.data.get("current_message") or "未設定"
        next_task = self.data.get("next_task") or "未設定"
        self._task_item.title = f"📌 今: {_truncate10(task)}"
        self._message_item.title = f"💬 コメント: {_truncate10(msg)}"
        self._next_item.title = f"⏭ 次: {_truncate10(next_task)}"
        self._update_countdown()
        sam_msg = self.data.get("sam_message") or "—"
        if self._pin_win is not None and self._pin_msg_label is not None:
            try:
                self._pin_msg_label.setStringValue_(sam_msg)
            except Exception:
                pass

    def _install_edit_shortcuts(self):
        try:
            main = NSApp.mainMenu()
            if main is None:
                main = NSMenu.alloc().initWithTitle_("")
                NSApp.setMainMenu_(main)

            # macOS requires an application menu at index 0 to process key equivalents
            if main.numberOfItems() == 0 or main.itemAtIndex_(0).title() != "":
                app_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("", None, "")
                app_item.setSubmenu_(NSMenu.alloc().initWithTitle_(""))
                main.insertItem_atIndex_(app_item, 0)

            edit_root = None
            for i in range(main.numberOfItems()):
                item = main.itemAtIndex_(i)
                if item.title() == "Edit":
                    edit_root = item
                    break

            if edit_root is None:
                edit_root = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("Edit", None, "")
                main.addItem_(edit_root)

            edit_menu = edit_root.submenu()
            if edit_menu is None:
                edit_menu = NSMenu.alloc().initWithTitle_("Edit")
                edit_root.setSubmenu_(edit_menu)

            if edit_menu.numberOfItems() == 0:
                for title, action, key in (
                    ("Cut", "cut:", "x"),
                    ("Copy", "copy:", "c"),
                    ("Paste", "paste:", "v"),
                    ("Select All", "selectAll:", "a"),
                ):
                    it = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, action, key)
                    it.setKeyEquivalentModifierMask_(NSEventModifierFlagCommand)
                    edit_menu.addItem_(it)
        except Exception:
            pass

    def _apply_app_icon(self):
        for icon_path in ICON_CANDIDATES:
            if not os.path.exists(icon_path):
                continue
            try:
                img = NSImage.alloc().initByReferencingFile_(icon_path)
                if img is not None:
                    NSApp.setApplicationIconImage_(img)
                    return
            except Exception:
                continue

    def _update_countdown(self):
        remaining = self._next_checkin_at - datetime.now()
        total_secs = max(0, int(remaining.total_seconds()))
        mins, secs = divmod(total_secs, 60)
        if self._break_mode:
            self.title = f"☕ 休憩 {mins}:{secs:02d}"
        else:
            task = self.data.get("current_task") or "タスク未設定"
            msg = self.data.get("current_message") or ""
            task_label = _truncate10(task)
            msg_label = _truncate10(msg) if msg else "-"
            self.title = f"🎯 {task_label}/{msg_label} {mins}:{secs:02d}"

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
            return
        self._check_date_change()
        self._check_week_change()

    def _add_to_history(self, date: str, tasks: list):
        if not date or not tasks:
            return
        history = self.data.setdefault("history", [])
        for entry in history:
            if entry.get("date") == date:
                entry["tasks"] = [dict(t) for t in tasks]
                return
        history.append({"date": date, "tasks": [dict(t) for t in tasks]})

    def _check_date_change(self):
        today_str = datetime.now().strftime("%Y-%m-%d")
        if self.data.get("today_date") == today_str:
            return
        # Save current day's tasks to history
        old_date = self.data.get("today_date", "")
        old_tasks = _normalize_today(self.data["goals"].get("today", []))
        self._add_to_history(old_date, old_tasks)
        # Carry over undone tasks from the previous day
        carryover = [{"text": t["text"], "done": False}
                     for t in old_tasks if not t.get("done")]
        carryover_texts = {t["text"] for t in carryover}
        # Add today's weekday tasks from the weekly plan (skip duplicates)
        today_dt = datetime.strptime(today_str, "%Y-%m-%d")
        weekday_key = WEEKDAY_NAMES[today_dt.weekday()]
        weekly = _normalize_weekly(self.data["goals"].get("weekly", {}))
        scheduled = [
            {"text": text, "done": False}
            for text in weekly["days"].get(weekday_key, [])
            if text not in carryover_texts
        ]
        self.data["today_date"] = today_str
        self.data["goals"]["today"] = scheduled + carryover
        self._save()
        self._check_week_change()
        if not self._checkin_active:
            rumps.Timer(self._prompt_new_day, 2).start()

    def _check_week_change(self):
        today_str = self.data.get("today_date", datetime.now().strftime("%Y-%m-%d"))
        try:
            today_dt = datetime.strptime(today_str, "%Y-%m-%d")
        except ValueError:
            return
        current_week_start = _monday_of(today_dt).strftime("%Y-%m-%d")
        weekly = _normalize_weekly(self.data["goals"].get("weekly", {}))
        if weekly.get("week_start") == current_week_start:
            return
        # Week changed: update week_start and prompt
        weekly["week_start"] = current_week_start
        self.data["goals"]["weekly"] = weekly
        self._save()
        if not self._checkin_active:
            rumps.Timer(self._prompt_new_week, 3).start()

    def _prompt_new_week(self, timer: rumps.Timer):
        timer.stop()
        if self._checkin_active:
            return

        # Step 1: weekly retrospective of last week
        self._checkin_active = True
        try:
            today_str = self.data.get("today_date", datetime.now().strftime("%Y-%m-%d"))
            try:
                today_dt = datetime.strptime(today_str, "%Y-%m-%d")
            except ValueError:
                today_dt = datetime.now()
            this_monday = _monday_of(today_dt)
            last_monday = this_monday - timedelta(days=7)
            last_week_start = last_monday.strftime("%Y-%m-%d")
            last_week_end = (last_monday + timedelta(days=6)).strftime("%Y-%m-%d")

            history = self.data.get("history", [])
            last_week_entries = sorted(
                [e for e in history if last_week_start <= e.get("date", "") <= last_week_end],
                key=lambda e: e.get("date", ""),
            )

            notify("📅 新しい週が始まりました！", "まず先週を振り返りましょう")
            result = show_weekly_review(last_week_start, last_week_entries, [], [], [])
            if result is not None:
                review_entry = {
                    "week_start": last_week_start,
                    "week_end": last_week_end,
                    "kpt": result,
                }
                weekly_reviews = self.data.setdefault("weekly_review_history", [])
                for i, entry in enumerate(weekly_reviews):
                    if entry.get("week_start") == last_week_start:
                        weekly_reviews[i] = review_entry
                        break
                else:
                    weekly_reviews.append(review_entry)
                self._save()
                selected = show_try_selector(result.get("try", []))
                self.data["active_tries"] = selected
                self._save()
        finally:
            self._checkin_active = False

        # Step 2: set this week's plan
        notify("📋 今週の計画を立てましょう！", "")
        self._edit_weekly()

        # After setting weekly plan, reload today's tasks
        today_str = self.data.get("today_date", datetime.now().strftime("%Y-%m-%d"))
        try:
            today_dt = datetime.strptime(today_str, "%Y-%m-%d")
        except ValueError:
            return
        weekday_key = WEEKDAY_NAMES[today_dt.weekday()]
        weekly = _normalize_weekly(self.data["goals"].get("weekly", {}))
        scheduled = [{"text": t, "done": False} for t in weekly["days"].get(weekday_key, [])]
        existing_texts = {t["text"] for t in self.data["goals"].get("today", [])}
        for task in scheduled:
            if task["text"] not in existing_texts:
                self.data["goals"].setdefault("today", []).append(task)
        self._save()

    def _prompt_new_day(self, timer: rumps.Timer):
        timer.stop()
        if self._checkin_active:
            return
        notify("🌅 新しい日が始まりました！", "今日のタスクを追加しましょう")
        self._edit_today()

    # ── Core flows ────────────────────────────────────────────────────────

    def _first_run(self, timer: rumps.Timer):
        timer.stop()
        self._setup_all_goals()

    def _setup_all_goals(self):
        self._checkin_active = True
        try:
            g = self.data["goals"]
            str_entries = [
                ("long",  "長期目標 (1/4)", "1〜2年後に達成したいことは？"),
                ("mid",   "中期目標 (2/4)", "1〜5ヶ月で達成したいことは？"),
                ("short", "短期目標 (3/4)", "今日〜1ヶ月で達成したいことは？"),
            ]
            updated: dict = {k: g.get(k, "") for k in ("long", "mid", "short")}
            for key, title, prompt in str_entries:
                val = show_goal_input(title, prompt, default=g.get(key, ""))
                if val is not None:
                    updated[key] = val

            # Weekly plan (4/4): goal + per-day tasks
            weekly = _normalize_weekly(g.get("weekly", {}))
            weekly_result = show_weekly_editor(weekly["goal"], weekly["days"], weekly.get("week_start", ""))
            if weekly_result is not None:
                weekly["goal"] = weekly_result["goal"]
                weekly["days"] = weekly_result["days"]
            today_str = self.data.get("today_date", datetime.now().strftime("%Y-%m-%d"))
            weekly["week_start"] = _monday_of(datetime.strptime(today_str, "%Y-%m-%d")).strftime("%Y-%m-%d")
            updated["weekly"] = weekly

            # Today's tasks: seed from weekly plan for today's weekday
            today_dt = datetime.strptime(today_str, "%Y-%m-%d")
            weekday_key = WEEKDAY_NAMES[today_dt.weekday()]
            scheduled = [{"text": t, "done": False} for t in weekly["days"].get(weekday_key, [])]
            old_today = _normalize_today(g.get("today", []))
            old_done = {t["text"]: t["done"] for t in old_today}
            merged = {t["text"]: t for t in scheduled}
            for t in old_today:
                if t["text"] not in merged:
                    merged[t["text"]] = t
                else:
                    merged[t["text"]]["done"] = old_done.get(t["text"], False)
            updated["today"] = list(merged.values())

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
            current = _normalize_today(self.data["goals"].get("today", []))
            val = show_today_task_editor("今日の目標を変更", current)
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

        while True:
            action, new_task, queued_next_task, message, session_mins, updated_today = show_checkin(
                self.data["goals"],
                today_date=self.data.get("today_date", ""),
                current_task=self.data.get("current_task", ""),
                queued_task=self.data.get("next_task", ""),
                queued_next_task=self.data.get("next_next_task", ""),
                current_message=self.data.get("current_message", ""),
                current_interval=self.data.get("interval_minutes", DEFAULT_INTERVAL),
                active_tries=self.data.get("active_tries", []),
            )

            # Save checkbox state regardless of which button was pressed
            self.data["goals"]["today"] = updated_today
            self.data["last_checkin"] = datetime.now().isoformat()
            self._save()

            if action != "edit_today":
                break
            current = _normalize_today(self.data["goals"].get("today", []))
            val = show_today_task_editor("今日の細分タスクを編集", current)
            if val is not None:
                self.data["goals"]["today"] = val
                self._save()

        if action == "break" or new_task is None:
            self._break_mode = True
            self._reset_timer(override_minutes=BREAK_MINUTES)
            self._refresh_ui()
            notify("☕ 休憩スタート！", f"{BREAK_MINUTES}分後にチェックインします", "ゆっくり休んでください")
            return

        self._break_mode = False
        self.data["current_task"] = new_task
        self.data["next_task"] = queued_next_task or ""
        self.data["next_next_task"] = ""
        self.data["current_message"] = message or ""
        sam_msgs = self.data.get("sam_messages", [])
        if sam_msgs:
            self.data["sam_message"] = random.choice(sam_msgs)
        if session_mins is not None:
            self.data["interval_minutes"] = session_mins
        self._save()
        self._reset_timer()
        self._refresh_ui()

        mins = self.data.get("interval_minutes", DEFAULT_INTERVAL)
        subtitle = f"今やること: {new_task}"
        body = f"コメント: {self.data.get('current_message', '')}" if self.data.get("current_message") else ""
        notify("スタート！ 🚀", subtitle, body or f"{mins}分後にまたチェックインします")

    # ── Pin window ────────────────────────────────────────────────────────

    def _show_pin_window(self):
        W, H = 200, 310
        # Titled(1) | Closable(2) — no miniaturize/resize
        win = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, W, H), 1 | 2, 2, False,
        )
        win.setTitle_("サム")
        win.setOpaque_(False)
        win.setBackgroundColor_(_BG)
        win.setAppearance_(NSAppearance.appearanceNamed_("NSAppearanceNameAqua"))
        win.setLevel_(3)  # NSFloatingWindowLevel
        win.setHidesOnDeactivate_(False)
        win.setCollectionBehavior_(_WC_MANAGED | _WC_CYCLE)

        cv = win.contentView()

        _TEXT_H = 72   # height of text block above image
        _IMG_PAD = 15  # padding below image
        _PAD_H = 11    # horizontal padding for text label

        if os.path.exists(SAM_IMG):
            _img = NSImage.alloc().initByReferencingFile_(SAM_IMG)
            if _img:
                iv = NSImageView.alloc().initWithFrame_(
                    NSMakeRect(10, _IMG_PAD, W - 20, H - _TEXT_H - _IMG_PAD)
                )
                iv.setImage_(_img)
                iv.setImageScaling_(3)
                iv.setImageAlignment_(5)  # NSImageAlignBottom
                cv.addSubview_(iv)

        msg = self.data.get("sam_message") or "—"
        _cell = _VCenteredCell.alloc().initTextCell_(msg)
        _cell.setFont_(NSFont.systemFontOfSize_(14))
        _cell.setWraps_(True)
        _cell.setScrollable_(False)
        _cell.setTextColor_(NSColor.colorWithWhite_alpha_(0.2, 1.0))
        self._pin_msg_label = NSTextField.alloc().initWithFrame_(
            NSMakeRect(_PAD_H, H - _TEXT_H, W - 2 * _PAD_H, _TEXT_H)
        )
        self._pin_msg_label.setCell_(_cell)
        self._pin_msg_label.setBezeled_(False)
        self._pin_msg_label.setDrawsBackground_(False)
        self._pin_msg_label.setEditable_(False)
        self._pin_msg_label.setSelectable_(False)
        cv.addSubview_(self._pin_msg_label)

        self._pin_delegate = _PinWindowDelegate.alloc().init()
        self._pin_delegate.app_ref = self
        win.setDelegate_(self._pin_delegate)

        # Position top-right of visible screen area
        try:
            sr = NSScreen.mainScreen().visibleFrame()
            win_x = sr.origin.x + sr.size.width - W - 20
            win_y = sr.origin.y + sr.size.height - H - 10
            win.setFrameOrigin_((win_x, win_y))
        except Exception:
            win.center()

        self._pin_win = win
        win.orderFront_(None)
        self._pin_item.title = "📌 サムを隠す"

    def _hide_pin_window(self):
        if self._pin_win is not None:
            self._pin_win.orderOut_(None)
            self._pin_win = None
            self._pin_msg_label = None
        self._pin_item.title = "📌 サムをピン留め"

    def _on_pin_window_close(self):
        self._pin_win = None
        self._pin_msg_label = None
        self._pin_item.title = "📌 サムをピン留め"

    def _cmd_toggle_pin(self, _):
        if self._pin_win is not None:
            self._hide_pin_window()
        else:
            self._show_pin_window()

    def _cmd_edit_sam_messages(self, _):
        if self._checkin_active:
            return
        self._checkin_active = True
        try:
            current = self.data.get("sam_messages", [])
            val = show_list_input(
                "サムのメッセージ一覧",
                "チェックインごとにランダム表示するメッセージ（1行に1つ）",
                current,
            )
            if val is not None:
                self.data["sam_messages"] = val
                if val:
                    self.data["sam_message"] = random.choice(val)
                    self._refresh_ui()
                self._save()
        finally:
            self._checkin_active = False

    # ── Menu callbacks ────────────────────────────────────────────────────

    def _cmd_checkin(self, _):
        self._do_checkin()

    def _cmd_edit_interval(self, _):
        if self._checkin_active:
            return
        current = self.data.get("interval_minutes", DEFAULT_INTERVAL)
        val = show_interval_input(current)
        if val is not None:
            self.data["interval_minutes"] = val
            self._save()
            notify("⏱ 更新しました", f"セッション時間: {val}分", "次のセッションから適用されます")

    def _cmd_edit_long(self, _):
        self._edit_goal("long", "長期目標を変更", "1〜2年後に達成したいことは？")

    def _cmd_edit_mid(self, _):
        self._edit_goal("mid", "中期目標を変更", "1〜5ヶ月で達成したいことは？")

    def _cmd_edit_short(self, _):
        self._edit_goal("short", "短期目標を変更", "今日〜1ヶ月で達成したいことは？")

    def _cmd_edit_weekly(self, _):
        self._edit_weekly()

    def _edit_weekly(self):
        if self._checkin_active:
            return
        self._checkin_active = True
        try:
            weekly = _normalize_weekly(self.data["goals"].get("weekly", {}))
            result = show_weekly_editor(weekly["goal"], weekly["days"], weekly.get("week_start", ""))
            if result is not None:
                weekly["goal"] = result["goal"]
                weekly["days"] = result["days"]
                self.data["goals"]["weekly"] = weekly
                self._save()
        finally:
            self._checkin_active = False

    def _cmd_edit_today(self, _):
        self._edit_today()

    def _cmd_show_history(self, _):
        if self._checkin_active:
            return
        self._checkin_active = True
        try:
            show_history(self.data.get("history", []))
        finally:
            self._checkin_active = False

    def _cmd_do_retrospective(self, _):
        if self._checkin_active:
            return
        self._checkin_active = True
        try:
            kpt = self.data.get("kpt", {})
            result = show_kpt_editor(
                kpt.get("keep", []),
                kpt.get("problem", []),
                kpt.get("try", []),
            )
            if result is None:
                return
            result["date"] = self.data.get("today_date", datetime.now().strftime("%Y-%m-%d"))
            self.data["kpt"] = result
            # Upsert into kpt_history
            history = self.data.setdefault("kpt_history", [])
            for i, entry in enumerate(history):
                if entry.get("date") == result["date"]:
                    history[i] = dict(result)
                    break
            else:
                history.append(dict(result))
            # Ask which Try items to carry forward to tomorrow's check-ins
            selected = show_try_selector(result.get("try", []))
            self.data["active_tries"] = selected
            self._save()
        finally:
            self._checkin_active = False

    def _cmd_show_kpt_history(self, _):
        if self._checkin_active:
            return
        self._checkin_active = True
        try:
            show_kpt_history(self.data.get("kpt_history", []))
        finally:
            self._checkin_active = False

    def _cmd_show_weekly_review_history(self, _):
        if self._checkin_active:
            return
        self._checkin_active = True
        try:
            show_weekly_review_history(self.data.get("weekly_review_history", []))
        finally:
            self._checkin_active = False


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    ProgressChecker().run()
