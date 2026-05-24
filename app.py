#!/usr/bin/env python3
"""てくてく — macOS menu bar productivity app with native AppKit dialogs."""
import ctypes
import json
import os
os.environ.setdefault('OS_ACTIVITY_MODE', 'disable')  # suppress macOS framework log noise
import random
from datetime import datetime, timedelta
from typing import List, Optional

# Set process name before AppKit loads so window switcher shows "てくてく" not "Python"
try:
    ctypes.CDLL(None).setprogname(b"\xe3\x81\xa6\xe3\x81\x8f\xe3\x81\xa6\xe3\x81\x8f")
except Exception:
    pass

import objc
import rumps
from Foundation import NSObject, NSRunLoop, NSTimer, NSRunLoopCommonModes, NSIndexSet
from AppKit import (
    NSApp,
    NSAppearance,
    NSAttributedString,
    NSButton,
    NSColor,
    NSCompositingOperationSourceOver,
    NSEvent,
    NSEventMaskKeyDown,
    NSFont,
    NSForegroundColorAttributeName,
    NSImage,
    NSMakeRect,
    NSMenu,
    NSMenuItem,
    NSEventModifierFlagCommand,
    NSEventModifierFlagShift,
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
    NSZeroRect,
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
_BTN4 = 1003
_CANCEL = -1

_BG = NSColor.colorWithRed_green_blue_alpha_(1.0, 1.0, 1.0, 0.93)
ICON_CANDIDATES = [
    os.path.join(os.path.dirname(__file__), "sam.png"),
]


def _pad_icon_to_square(img):
    """Return a new NSImage with the source centered in a square canvas (transparent padding)."""
    orig_w = img.size().width
    orig_h = img.size().height
    size = max(orig_w, orig_h)
    scale = min(size / orig_w, size / orig_h)
    draw_w = orig_w * scale
    draw_h = orig_h * scale
    x = (size - draw_w) / 2
    y = (size - draw_h) / 2
    square = NSImage.alloc().initWithSize_((size, size))
    square.lockFocus()
    img.drawInRect_fromRect_operation_fraction_(
        NSMakeRect(x, y, draw_w, draw_h), NSZeroRect, NSCompositingOperationSourceOver, 1.0)
    square.unlockFocus()
    return square


def _apply_icon():
    for icon_path in ICON_CANDIDATES:
        if not os.path.exists(icon_path):
            continue
        try:
            img = NSImage.alloc().initWithContentsOfFile_(icon_path)
            if img is not None:
                NSApp.setApplicationIconImage_(_pad_icon_to_square(img))
                return
        except Exception:
            continue


class _IconApplier(NSObject):
    def apply_(self, timer):
        _apply_icon()


_icon_applier = _IconApplier.alloc().init()


class _UiTicker(NSObject):
    """Calls app._update_countdown() on each tick, even inside modal run loops."""
    app_ref = None

    def tick_(self, timer):
        if self.app_ref is not None:
            self.app_ref._update_countdown()


_ui_ticker = _UiTicker.alloc().init()


SAM_CLOTHING_IMG = os.path.join(os.path.dirname(__file__), "sam_clothing-eyes.png")

_nudge_win_ref = [None]
_checkin_win_ref = [None]

# NSPopUpMenuWindowLevel (101): receives clicks even during modal sessions
_NUDGE_LEVEL = 101


class _NudgeWindowHandler(NSObject):
    """Handles close button and 'よし！' button for the nudge popup."""
    def closeNudge_(self, sender):
        w = _nudge_win_ref[0]
        if w is not None:
            w.orderOut_(None)
            _nudge_win_ref[0] = None

    def activateCheckin_(self, sender):
        self.closeNudge_(None)
        cw = _checkin_win_ref[0]
        if cw is not None:
            NSApp.activateIgnoringOtherApps_(True)
            cw.makeKeyAndOrderFront_(None)
            cw.orderFrontRegardless()

    def windowShouldClose_(self, win):
        self.closeNudge_(None)
        return False

    def autoClose_(self, timer):
        self.closeNudge_(None)


_nudge_handler = _NudgeWindowHandler.alloc().init()


def _show_nudge_popup():
    if _nudge_win_ref[0] is not None:
        _nudge_win_ref[0].makeKeyAndOrderFront_(None)
        return

    W, H = 280, 400
    win = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
        NSMakeRect(0, 0, W, H), 1 | 2, 2, False,
    )
    win.setTitle_("⏰ まだチェックインしてないのか？")
    win.setOpaque_(False)
    win.setBackgroundColor_(_BG)
    win.setAppearance_(NSAppearance.appearanceNamed_("NSAppearanceNameAqua"))
    win.setLevel_(_NUDGE_LEVEL)
    win.setHidesOnDeactivate_(False)
    win.setCollectionBehavior_(_WC_MANAGED | _WC_CYCLE)
    win.setDelegate_(_nudge_handler)
    _center_on_active_screen(win)

    cv = win.contentView()

    MSG_H = 80
    BTN_H = 52
    IMG_PAD = 10
    IMG_H = H - MSG_H - BTN_H - IMG_PAD

    _msg_cell = _VCenteredCell.alloc().initTextCell_(
        "タスク決めようぜ。\nまず1分だけ取り組んでみようぜ"
    )
    _msg_cell.setFont_(NSFont.systemFontOfSize_(14))
    _msg_cell.setWraps_(True)
    _msg_cell.setScrollable_(False)
    _msg_cell.setTextColor_(NSColor.colorWithWhite_alpha_(0.25, 1.0))
    _msg_tf = NSTextField.alloc().initWithFrame_(
        NSMakeRect(16, H - MSG_H, W - 32, MSG_H)
    )
    _msg_tf.setCell_(_msg_cell)
    _msg_tf.setBezeled_(False)
    _msg_tf.setDrawsBackground_(False)
    _msg_tf.setEditable_(False)
    _msg_tf.setSelectable_(False)
    cv.addSubview_(_msg_tf)

    if os.path.exists(SAM_CLOTHING_IMG):
        img = NSImage.alloc().initByReferencingFile_(SAM_CLOTHING_IMG)
        if img:
            iv = NSImageView.alloc().initWithFrame_(
                NSMakeRect(10, BTN_H, W - 20, IMG_H)
            )
            iv.setImage_(img)
            iv.setImageScaling_(3)
            iv.setImageAlignment_(5)
            cv.addSubview_(iv)

    btn = NSButton.alloc().initWithFrame_(NSMakeRect((W - 140) // 2, 12, 140, 32))
    btn.setTitle_("よし、やろう！")
    btn.setBezelStyle_(1)
    btn.setTarget_(_nudge_handler)
    btn.setAction_("activateCheckin:")
    cv.addSubview_(btn)

    _nudge_win_ref[0] = win
    win.makeKeyAndOrderFront_(None)


class _CheckinNudger(NSObject):
    """Shows a nudge popup every 5 minutes while the check-in dialog is open."""
    def nudge_(self, timer):
        try:
            _show_nudge_popup()
        except Exception as e:
            import traceback
            print(f"[nudge] error: {e}\n{traceback.format_exc()}", flush=True)


_checkin_nudger = _CheckinNudger.alloc().init()
CHECKIN_NUDGE_INTERVAL = 5 * 60  # seconds

# Hours at which the daily retrospective reminder fires
RETRO_REMINDER_HOURS = [19, 22, 0]

_retro_nudge_win_ref = [None]


class _RetroNudgeHandler(NSObject):
    """Handles the daily retrospective reminder popup."""
    app_ref = None
    date_ref = [""]  # date string this popup is for

    def dismiss_(self, sender):
        w = _retro_nudge_win_ref[0]
        if w is not None:
            w.orderOut_(None)
            _retro_nudge_win_ref[0] = None

    def doRetro_(self, sender):
        self.dismiss_(None)
        app = self.app_ref
        if app is not None:
            rumps.Timer(lambda t: (t.stop(), app._do_retrospective_for(self.date_ref[0])), 0.1).start()

    def windowShouldClose_(self, win):
        self.dismiss_(None)
        return False


_retro_nudge_handler = _RetroNudgeHandler.alloc().init()


def _show_retro_nudge_popup(app_ref, date_str: str, is_yesterday: bool = False):
    if _retro_nudge_win_ref[0] is not None:
        _retro_nudge_win_ref[0].makeKeyAndOrderFront_(None)
        return
    _retro_nudge_handler.app_ref = app_ref
    _retro_nudge_handler.date_ref[0] = date_str

    W, H = 300, 160
    win = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
        NSMakeRect(0, 0, W, H), 1 | 2, 2, False,
    )
    if is_yesterday:
        win.setTitle_("📝 昨日の振り返りが未完了です")
        msg = "昨日の振り返り（KPT）が\nまだできていません。"
    else:
        win.setTitle_("📝 振り返りの時間です")
        msg = "今日の振り返り（KPT）を\n記録しましょう！"
    win.setOpaque_(False)
    win.setBackgroundColor_(_BG)
    win.setAppearance_(NSAppearance.appearanceNamed_("NSAppearanceNameAqua"))
    win.setLevel_(_NUDGE_LEVEL)
    win.setHidesOnDeactivate_(False)
    win.setCollectionBehavior_(_WC_MANAGED | _WC_CYCLE)
    win.setDelegate_(_retro_nudge_handler)
    _center_on_active_screen(win)

    cv = win.contentView()
    _msg_cell = _VCenteredCell.alloc().initTextCell_(msg)
    _msg_cell.setFont_(NSFont.systemFontOfSize_(14))
    _msg_cell.setWraps_(True)
    _msg_cell.setScrollable_(False)
    _msg_cell.setTextColor_(NSColor.colorWithWhite_alpha_(0.2, 1.0))
    msg_tf = NSTextField.alloc().initWithFrame_(NSMakeRect(16, 60, W - 32, 88))
    msg_tf.setCell_(_msg_cell)
    msg_tf.setBezeled_(False)
    msg_tf.setDrawsBackground_(False)
    msg_tf.setEditable_(False)
    msg_tf.setSelectable_(False)
    cv.addSubview_(msg_tf)

    btn_yes = NSButton.alloc().initWithFrame_(NSMakeRect(W - 230, 12, 100, 32))
    btn_yes.setTitle_("今やる！")
    btn_yes.setBezelStyle_(1)
    btn_yes.setTarget_(_retro_nudge_handler)
    btn_yes.setAction_("doRetro:")
    btn_yes.setKeyEquivalent_("\r")
    cv.addSubview_(btn_yes)

    btn_later = NSButton.alloc().initWithFrame_(NSMakeRect(W - 120, 12, 100, 32))
    btn_later.setTitle_("後で")
    btn_later.setBezelStyle_(1)
    btn_later.setTarget_(_retro_nudge_handler)
    btn_later.setAction_("dismiss:")
    cv.addSubview_(btn_later)

    _retro_nudge_win_ref[0] = win
    win.makeKeyAndOrderFront_(None)


def _schedule_icon():
    """Schedule icon re-application in NSRunLoopCommonModes so it fires even inside modal loops."""
    t = NSTimer.timerWithTimeInterval_target_selector_userInfo_repeats_(
        0.0, _icon_applier, "apply:", None, False)
    NSRunLoop.mainRunLoop().addTimer_forMode_(t, NSRunLoopCommonModes)


def _setup_app_menu():
    """Ensure the application menu shows 'てくてく' and Cmd+Q quits. Called each time the app activates."""
    try:
        main = NSApp.mainMenu()
        if main is None:
            main = NSMenu.alloc().initWithTitle_("")
            NSApp.setMainMenu_(main)
        if main.numberOfItems() == 0 or main.itemAtIndex_(0).title() != "てくてく":
            app_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("てくてく", None, "")
            app_submenu = NSMenu.alloc().initWithTitle_("てくてく")
            quit_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                "てくてくを終了", "terminate:", "q")
            quit_item.setKeyEquivalentModifierMask_(NSEventModifierFlagCommand)
            quit_item.setTarget_(NSApp)
            app_submenu.addItem_(quit_item)
            app_item.setSubmenu_(app_submenu)
            if main.numberOfItems() == 0:
                main.insertItem_atIndex_(app_item, 0)
            else:
                main.removeItemAtIndex_(0)
                main.insertItem_atIndex_(app_item, 0)
    except Exception:
        pass


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


class _TabTextViewDelegate(NSObject):
    """NSTextView delegate: Tab → next key view, Shift+Tab → previous."""
    def textView_doCommandBySelector_(self, tv, selector):
        if selector == "insertTab:":
            tv.window().selectNextKeyView_(tv)
            return True
        if selector == "insertBacktab:":
            tv.window().selectPreviousKeyView_(tv)
            return True
        return False

_tab_tv_delegate = _TabTextViewDelegate.alloc().init()


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
    tv.setDelegate_(_tab_tv_delegate)
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


def _truncate8(text: str) -> str:
    s = text or ""
    return s if len(s) <= 8 else s[:8] + "…"


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


def _arrival_color(time_str: str):
    """Map 'HH:MM' arrival time to an NSColor. Earlier = darker green."""
    from AppKit import NSColor as _NSColor
    try:
        h, m = map(int, time_str.split(":"))
    except (ValueError, AttributeError):
        return _NSColor.colorWithRed_green_blue_alpha_(0.60, 0.82, 0.64, 1.0)
    mins = h * 60 + m
    # (R, G, B) solid colors, dark → light green
    if mins < 10 * 60:  r, g, b = 0.07, 0.36, 0.16   # #0d5c28 very dark
    elif mins < 14 * 60: r, g, b = 0.13, 0.43, 0.22  # #216e39 dark
    elif mins < 16 * 60: r, g, b = 0.26, 0.57, 0.35  # medium
    elif mins < 18 * 60: r, g, b = 0.40, 0.70, 0.48  # medium-light
    elif mins < 21 * 60: r, g, b = 0.55, 0.80, 0.60  # light green
    elif mins < 23 * 60: r, g, b = 0.68, 0.88, 0.72  # very light green
    else:                r, g, b = 0.78, 0.92, 0.80  # pale green (still clearly green)
    return _NSColor.colorWithRed_green_blue_alpha_(r, g, b, 1.0)


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
                text = str(value or "").strip()
                if self.show_numbers and text and text[0].isdigit():
                    dot_idx = text.find(". ")
                    if dot_idx > 0 and text[:dot_idx].isdigit():
                        text = text[dot_idx + 2:]
                self.items[row]["text"] = text

    def tableView_writeRowsWithIndexes_toPasteboard_(self, _table, row_indexes, pasteboard):
        count = row_indexes.count()
        idxs = []
        idx = row_indexes.firstIndex()
        for _ in range(count):
            idxs.append(idx)
            idx = row_indexes.indexGreaterThanIndex_(idx)
        pasteboard.declareTypes_owner_([NSPasteboardTypeString], self)
        pasteboard.setString_forType_(",".join(str(i) for i in idxs), NSPasteboardTypeString)
        return True

    def tableView_validateDrop_proposedRow_proposedDropOperation_(self, _table, _info, _row, _op):
        return 2  # NSDragOperationMove

    def tableView_acceptDrop_row_dropOperation_(self, _table, info, row, _op):
        raw = info.draggingPasteboard().stringForType_(NSPasteboardTypeString)
        if raw is None:
            return False
        try:
            src_set = {int(s) for s in raw.split(",")}
        except ValueError:
            return False
        moving = [self.items[i] for i in sorted(src_set) if 0 <= i < len(self.items)]
        if not moving:
            return False
        n_before = sum(1 for i in src_set if i < row)
        remaining = [item for i, item in enumerate(self.items) if i not in src_set]
        insert_at = max(0, min(row - n_before, len(remaining)))
        for item in reversed(moving):
            remaining.insert(insert_at, item)
        self.items[:] = remaining
        _table.deselectAll_(None)
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
        flags = event.modifierFlags()
        char = event.charactersIgnoringModifiers()
        if flags & NSEventModifierFlagCommand:
            if (flags & NSEventModifierFlagShift) and char == "z":
                if NSApp.sendAction_to_from_("redo:", None, None):
                    return True
            else:
                sel = {"c": "copy:", "v": "paste:", "x": "cut:",
                       "a": "selectAll:", "z": "undo:"}.get(char)
                if sel and NSApp.sendAction_to_from_(sel, None, None):
                    return True
        return objc.super(_KeyWindow, self).performKeyEquivalent_(event)


_H = _Handler.alloc().init()


def _center_on_active_screen(win: NSWindow) -> None:
    """Center window on the screen that currently has the mouse cursor."""
    mouse_loc = NSEvent.mouseLocation()
    target_screen = NSScreen.mainScreen()
    for screen in NSScreen.screens():
        sf = screen.frame()
        if (sf.origin.x <= mouse_loc.x < sf.origin.x + sf.size.width and
                sf.origin.y <= mouse_loc.y < sf.origin.y + sf.size.height):
            target_screen = screen
            break
    sf = target_screen.visibleFrame()
    wf = win.frame()
    x = sf.origin.x + (sf.size.width - wf.size.width) / 2
    y = sf.origin.y + (sf.size.height - wf.size.height) / 2
    win.setFrameOrigin_((x, y))


def _make_win(title: str, w: int, h: int) -> NSWindow:
    win = _KeyWindow.alloc().initWithContentRect_styleMask_backing_defer_(
        NSMakeRect(0, 0, w, h), _STYLE, 2, False,
    )
    win.setTitle_(title)
    _center_on_active_screen(win)
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
    _setup_app_menu()
    _apply_icon()
    _schedule_icon()  # re-apply on first modal run loop tick (NSRunLoopCommonModes)
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
    ordered_day_fields = [day_fields[k] for k in WEEKDAY_NAMES]
    goal_field.setNextKeyView_(ordered_day_fields[0])
    for i in range(len(ordered_day_fields) - 1):
        ordered_day_fields[i].setNextKeyView_(ordered_day_fields[i + 1])
    ordered_day_fields[-1].setNextKeyView_(goal_field)
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


def show_today_task_editor(title: str, items: list[dict]) -> Optional[tuple]:
    """Task editor with add/delete/defer and drag & drop reorder.
    Returns (kept_items, deferred_items) or None if cancelled."""
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
    table.setAllowsMultipleSelection_(True)
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
    _btn(cv, "📅 翌日に移動", _BTN4, NSMakeRect(20, 10, 128, 28))
    _btn(cv, "決定", _BTN1, NSMakeRect(W - 136, 10, 116, 28), primary=False)
    # Enter in input field should add item, not submit dialog.
    input_field.setTag_(_BTN3)
    input_field.setTarget_(_H)
    input_field.setAction_("click:")
    win.setInitialFirstResponder_(input_field)
    deferred_items: list = []
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
                sel = table.selectedRow()
                insert_at = (sel + 1) if sel >= 0 else len(model.items)
                model.items.insert(insert_at, {"text": text, "done": False})
                input_field.setStringValue_("")
                table.reloadData()
                table.selectRowIndexes_byExtendingSelection_(
                    NSIndexSet.indexSetWithIndex_(insert_at), False)
                continue
            if resp == _BTN2:
                sel = table.selectedRowIndexes()
                if sel.count() == 0:
                    err.setStringValue_("削除する行を選択してください")
                    continue
                count = sel.count()
                idxs = []
                idx = sel.firstIndex()
                for _ in range(count):
                    idxs.append(idx)
                    idx = sel.indexGreaterThanIndex_(idx)
                for idx in reversed(idxs):
                    if 0 <= idx < len(model.items):
                        model.items.pop(idx)
                table.reloadData()
                continue
            if resp == _BTN4:
                sel = table.selectedRowIndexes()
                if sel.count() == 0:
                    err.setStringValue_("翌日に移動する行を選択してください")
                    continue
                count = sel.count()
                idxs = []
                idx = sel.firstIndex()
                for _ in range(count):
                    idxs.append(idx)
                    idx = sel.indexGreaterThanIndex_(idx)
                for idx in reversed(idxs):
                    if 0 <= idx < len(model.items):
                        deferred_items.append(model.items.pop(idx))
                err.setStringValue_(f"翌日に移動しました（{len(deferred_items)}件）")
                table.reloadData()
                continue
            clean = []
            for item in model.items:
                text = (item.get("text") or "").strip()
                if text:
                    clean.append({"text": text, "done": bool(item.get("done", False))})
            deferred_clean = [
                {"text": (item.get("text") or "").strip(), "done": False}
                for item in deferred_items
                if (item.get("text") or "").strip()
            ]
            return clean, deferred_clean
    finally:
        win.orderOut_(None)
        _hide()


SAM_IMG = os.path.join(os.path.dirname(__file__), "sam.png")


def show_checkin(
    goals: dict,
    today_date: str = "",
    current_task: str = "",
    queued_task: str = "",

    current_message: str = "",
    current_interval: int = DEFAULT_INTERVAL,
    active_tries: Optional[list] = None,
    weekly_tries: Optional[list] = None,
) -> tuple[str, Optional[str], Optional[str], Optional[str], Optional[int], list]:
    """Returns (action, next_task, parallel_task, message, session_minutes, updated_today_items).
    action is one of: start, break, edit_today."""
    active_tries = active_tries or []
    weekly_tries = weekly_tries or []
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
    _checkin_win_ref[0] = win
    cv = win.contentView()

    # ── 左列：サム画像（下部）+ Tryリスト（サムの真上、上部は余白）──────────
    _LX = 8
    _LINE_H = 18    # height per try item row
    _ITEM_GAP = 6   # gap between items
    _LABEL_H = 14   # section heading height
    _LABEL_GAP = 4  # gap between heading and first item
    _LIST_PAD = 14  # top/bottom padding of list
    _SECTION_GAP = 10  # gap between 日 and 週 sections

    _n_daily = len(active_tries)
    _n_weekly = len(weekly_tries)
    _daily_block_h = (_n_daily * _LINE_H + max(0, _n_daily - 1) * _ITEM_GAP + _LABEL_GAP + _LABEL_H) if _n_daily else 0
    _weekly_block_h = (_n_weekly * _LINE_H + max(0, _n_weekly - 1) * _ITEM_GAP + _LABEL_GAP + _LABEL_H) if _n_weekly else 0
    _between_h = _SECTION_GAP if (_n_daily and _n_weekly) else 0
    _total_content_h = _daily_block_h + _between_h + _weekly_block_h
    _try_col_h = (2 * _LIST_PAD + _total_content_h) if _total_content_h else 0

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

    if _n_daily or _n_weekly:
        _sam_top = _LX + _sam_h
        _base_daily = _sam_top + _LIST_PAD
        _base_weekly = _base_daily + _daily_block_h + _between_h

        # 日 Try section (closest to Sam)
        if _n_daily:
            _daily_items_h = _n_daily * _LINE_H + max(0, _n_daily - 1) * _ITEM_GAP
            for _ti, _try_text in enumerate(active_tries):
                _item_y = _base_daily + (_n_daily - 1 - _ti) * (_LINE_H + _ITEM_GAP)
                cv.addSubview_(_mlabel(
                    f"・{_try_text}",
                    NSMakeRect(_LX, _item_y, X - _LX * 2, _LINE_H),
                    NSFont.systemFontOfSize_(13),
                    color=NSColor.colorWithWhite_alpha_(0.55, 1.0),
                ))
            cv.addSubview_(_label(
                "日 Try",
                NSMakeRect(_LX, _base_daily + _daily_items_h + _LABEL_GAP, X - _LX * 2, _LABEL_H),
                NSFont.boldSystemFontOfSize_(11),
                color=NSColor.colorWithWhite_alpha_(0.40, 1.0),
            ))

        # 週 Try section (above 日 section)
        if _n_weekly:
            _weekly_items_h = _n_weekly * _LINE_H + max(0, _n_weekly - 1) * _ITEM_GAP
            for _ti, _try_text in enumerate(weekly_tries):
                _item_y = _base_weekly + (_n_weekly - 1 - _ti) * (_LINE_H + _ITEM_GAP)
                cv.addSubview_(_mlabel(
                    f"・{_try_text}",
                    NSMakeRect(_LX, _item_y, X - _LX * 2, _LINE_H),
                    NSFont.systemFontOfSize_(13),
                    color=NSColor.colorWithWhite_alpha_(0.45, 1.0),
                ))
            cv.addSubview_(_label(
                "週 Try",
                NSMakeRect(_LX, _base_weekly + _weekly_items_h + _LABEL_GAP, X - _LX * 2, _LABEL_H),
                NSFont.boldSystemFontOfSize_(11),
                color=NSColor.colorWithWhite_alpha_(0.35, 1.0),
            ))

    # ── 今日やりたいこと（ドラッグで並び替え可）──────────────────────────────
    cv.addSubview_(_label(
        f"📅  今日やりたいこと — {_date_jp(_today_dt)}  （ダブルクリックで文面を編集）",
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
    _task_col.setEditable_(True)
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
    cv.addSubview_(_label("同時タスク（任意）", NSMakeRect(X + 123, 132, 100, 16), NSFont.systemFontOfSize_(12)))
    cv.addSubview_(_label("⏱分",           NSMakeRect(X + 230, 132, 40, 16), NSFont.systemFontOfSize_(12)))

    default_next = ""
    if today_items:
        task_to_index = {item["text"]: str(i + 1) for i, item in enumerate(today_items)}
        default_next = task_to_index.get(queued_task, "")
        if not default_next:
            for i, item in enumerate(today_items):
                if not item["done"]:
                    default_next = str(i + 1)
                    break
        if not default_next:
            default_next = "1"

    field_next     = _input_field(NSMakeRect(X + 20,  112, 95, 26), NSFont.systemFontOfSize_(15), placeholder="番号", default=default_next)
    field_next_next= _input_field(NSMakeRect(X + 123, 112, 95, 26), NSFont.systemFontOfSize_(15), placeholder="番号", default="")
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

    field_next.setNextKeyView_(field_next_next)
    field_next_next.setNextKeyView_(field_session)
    field_session.setNextKeyView_(field_msg)
    field_msg.setNextKeyView_(field_next)
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

            if not updated_today:
                err.setStringValue_("先に細分タスクを追加してください")
                continue

            next_task      = _parse_task_index(field_next.stringValue(), updated_today)
            parallel_task  = _parse_task_index(field_next_next.stringValue(), updated_today)
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
            if field_next_next.stringValue().strip() and not parallel_task:
                err.setStringValue_("同時タスクの番号が不正です")
                continue
            return "start", next_task, parallel_task, msg, session_mins, updated_today
    finally:
        _checkin_win_ref[0] = None
        if _nudge_win_ref[0] is not None:
            _nudge_win_ref[0].orderOut_(None)
            _nudge_win_ref[0] = None
        win.orderOut_(None)
        _hide()


def show_feedback(task: str, parallel_task: str = "") -> tuple:
    """Returns (result_str, parallel_done_bool)."""
    W = 420
    H = 210 if parallel_task else 172
    win = _make_win("セッション振り返り", W, H)
    cv = win.contentView()
    short = (task[:44] + "…") if len(task) > 44 else task
    cv.addSubview_(_mlabel(f"「{short}」", NSMakeRect(20, H - 52, W-40, 38), NSFont.boldSystemFontOfSize_(16)))
    cv.addSubview_(_label("どのくらい進みましたか？", NSMakeRect(20, H - 82, W-40, 22), NSFont.systemFontOfSize_(13)))

    parallel_check = None
    if parallel_task:
        short_p = (parallel_task[:36] + "…") if len(parallel_task) > 36 else parallel_task
        parallel_check = NSButton.alloc().initWithFrame_(NSMakeRect(20, 94, W - 40, 24))
        parallel_check.setButtonType_(3)  # NSSwitchButton = checkbox
        parallel_check.setTitle_(f"並行「{short_p}」も完了")
        parallel_check.setState_(0)
        cv.addSubview_(parallel_check)

    bw = (W - 40 - 16) // 3
    _btn(cv, "✅  完了！",     _BTN1, NSMakeRect(20,            16, bw, 36))
    _btn(cv, "🌱  少し進んだ", _BTN2, NSMakeRect(20 + bw + 8,   16, bw, 36))
    _btn(cv, "🔄  方針変更",   _BTN3, NSMakeRect(20 + (bw+8)*2, 16, bw, 36))
    _show(win)
    try:
        resp = NSApp.runModalForWindow_(win)
        parallel_done = bool(parallel_check and parallel_check.state() == 1)
        result = {_BTN1: "complete", _BTN2: "progress", _BTN3: "replan"}.get(resp, "progress")
        return result, parallel_done
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


def show_kpt_editor(keep: list, problem: list, try_: list, kpt_history: list = None) -> Optional[dict]:
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
    if kpt_history is not None:
        _kpt_hist_btn_handler.history = list(kpt_history)
        hist_btn = NSButton.alloc().initWithFrame_(NSMakeRect(20, 12, 140, 32))
        hist_btn.setTitle_("📊 過去を見る")
        hist_btn.setBezelStyle_(1)
        hist_btn.setTarget_(_kpt_hist_btn_handler)
        hist_btn.setAction_("show:")
        cv.addSubview_(hist_btn)
    for i in range(len(text_views)):
        text_views[i].setNextKeyView_(text_views[(i + 1) % len(text_views)])
    win.setInitialFirstResponder_(text_views[0])
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


_kpt_hist_float_ref = [None]


class _KptHistFloatHandler(NSObject):
    """Handler for the non-modal KPT history reference window."""
    def closeHistFloat_(self, sender):
        w = _kpt_hist_float_ref[0]
        if w is not None:
            w.orderOut_(None)
            _kpt_hist_float_ref[0] = None

    def windowWillClose_(self, notif):
        _kpt_hist_float_ref[0] = None


_kpt_hist_float_handler = _KptHistFloatHandler.alloc().init()


class _KptHistBtnHandler(NSObject):
    """Button handler inside the KPT editor that shows history without stopping the modal."""
    history = []

    def show_(self, sender):
        _show_kpt_history_float(self.history)


_kpt_hist_btn_handler = _KptHistBtnHandler.alloc().init()


def _show_kpt_history_float(kpt_history: list):
    """Show KPT history as a non-modal floating window (can coexist with the editor modal)."""
    if _kpt_hist_float_ref[0] is not None:
        _kpt_hist_float_ref[0].makeKeyAndOrderFront_(None)
        return
    W, H = 500, 540
    win = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
        NSMakeRect(0, 0, W, H), 1 | 2, 2, False,
    )
    win.setTitle_("過去の振り返り記録")
    win.setOpaque_(False)
    win.setBackgroundColor_(_BG)
    win.setAppearance_(NSAppearance.appearanceNamed_("NSAppearanceNameAqua"))
    win.setLevel_(_NUDGE_LEVEL)
    win.setHidesOnDeactivate_(False)
    win.setCollectionBehavior_(_WC_MANAGED | _WC_CYCLE)
    win.setDelegate_(_kpt_hist_float_handler)
    _center_on_active_screen(win)
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
    close_btn = NSButton.alloc().initWithFrame_(NSMakeRect(W - 136, 12, 116, 32))
    close_btn.setTitle_("閉じる")
    close_btn.setBezelStyle_(1)
    close_btn.setTarget_(win)
    close_btn.setAction_("orderOut:")
    cv.addSubview_(close_btn)
    _kpt_hist_float_ref[0] = win
    win.makeKeyAndOrderFront_(None)
    win.orderFrontRegardless()


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
    text_views[0].setNextKeyView_(text_views[1])
    text_views[1].setNextKeyView_(text_views[2])
    text_views[2].setNextKeyView_(summary_field)
    summary_field.setNextKeyView_(text_views[0])
    win.setInitialFirstResponder_(text_views[0])
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


_lab_reminder_win_ref = [None]


class _LabReminderHandler(NSObject):
    app_ref = None

    def dismiss_(self, sender):
        w = _lab_reminder_win_ref[0]
        if w is not None:
            w.orderOut_(None)
            _lab_reminder_win_ref[0] = None

    def doRecord_(self, sender):
        self.dismiss_(None)
        app = self.app_ref
        if app is not None:
            rumps.Timer(lambda t: (t.stop(), app._cmd_record_lab_arrival(None)), 0.1).start()

    def windowShouldClose_(self, win):
        self.dismiss_(None)
        return False


_lab_reminder_handler = _LabReminderHandler.alloc().init()


def _show_lab_reminder_popup(app_ref):
    if _lab_reminder_win_ref[0] is not None:
        _lab_reminder_win_ref[0].makeKeyAndOrderFront_(None)
        return
    _lab_reminder_handler.app_ref = app_ref

    W, H = 310, 130
    win = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
        NSMakeRect(0, 0, W, H), 1 | 2, 2, False,
    )
    win.setTitle_("🏢 ラボへ着きましたか？")
    win.setOpaque_(False)
    win.setBackgroundColor_(_BG)
    win.setAppearance_(NSAppearance.appearanceNamed_("NSAppearanceNameAqua"))
    win.setLevel_(_NUDGE_LEVEL)
    win.setHidesOnDeactivate_(False)
    win.setCollectionBehavior_(_WC_MANAGED | _WC_CYCLE)
    win.setDelegate_(_lab_reminder_handler)
    _center_on_active_screen(win)

    cv = win.contentView()
    _mc = _VCenteredCell.alloc().initTextCell_("ラボに行く予定でしたが、\n到着記録がありません。")
    _mc.setFont_(NSFont.systemFontOfSize_(13))
    _mc.setWraps_(True)
    _mc.setScrollable_(False)
    _mc.setTextColor_(NSColor.colorWithWhite_alpha_(0.25, 1.0))
    _mt = NSTextField.alloc().initWithFrame_(NSMakeRect(16, 56, W - 32, 62))
    _mt.setCell_(_mc)
    _mt.setBezeled_(False)
    _mt.setDrawsBackground_(False)
    _mt.setEditable_(False)
    _mt.setSelectable_(False)
    cv.addSubview_(_mt)

    btn_r = NSButton.alloc().initWithFrame_(NSMakeRect(W - 252, 12, 120, 32))
    btn_r.setTitle_("到着を記録する")
    btn_r.setBezelStyle_(1)
    btn_r.setTarget_(_lab_reminder_handler)
    btn_r.setAction_("doRecord:")
    btn_r.setKeyEquivalent_("\r")
    cv.addSubview_(btn_r)

    btn_l = NSButton.alloc().initWithFrame_(NSMakeRect(W - 124, 12, 104, 32))
    btn_l.setTitle_("あとで")
    btn_l.setBezelStyle_(1)
    btn_l.setTarget_(_lab_reminder_handler)
    btn_l.setAction_("dismiss:")
    cv.addSubview_(btn_l)

    _lab_reminder_win_ref[0] = win
    NSApp.activateIgnoringOtherApps_(True)
    win.makeKeyAndOrderFront_(None)
    win.orderFrontRegardless()


def show_lab_arrival_dialog(today_str: str, current_data: dict) -> Optional[dict]:
    """Dialog to record lab arrival status for today.
    Returns {"status": "arrived", "time": "HH:MM"} | {"status": "not_going"} |
            {"status": "pending"} | None (cancelled)."""
    W, H = 400, 210
    win = _make_win("🏢 ラボ到着を記録", W, H)
    cv = win.contentView()

    try:
        dt = datetime.strptime(today_str, "%Y-%m-%d")
        date_label = _date_jp(dt)
    except (ValueError, TypeError):
        date_label = today_str

    cv.addSubview_(_label(
        f"ラボ到着記録 — {date_label}",
        NSMakeRect(20, 184, W - 40, 22),
        NSFont.boldSystemFontOfSize_(13),
    ))

    status = current_data.get("status", "")
    time_val = current_data.get("time", "")
    if status == "arrived" and time_val:
        status_text = f"現在の記録: {time_val} 着"
    elif status == "not_going":
        status_text = "現在の記録: 今日は行かない"
    elif status == "pending":
        status_text = "現在の記録: 行く予定（未到着）"
    else:
        status_text = "現在の記録: 未記録"

    cv.addSubview_(_label(
        status_text,
        NSMakeRect(20, 164, W - 40, 16),
        NSFont.systemFontOfSize_(12),
        color=NSColor.colorWithWhite_alpha_(0.5, 1.0),
    ))
    cv.addSubview_(_sep(NSMakeRect(20, 156, W - 40, 1)))

    cv.addSubview_(_label("到着時刻を入力:", NSMakeRect(20, 132, 110, 16), NSFont.systemFontOfSize_(12)))
    default_time = time_val if time_val else datetime.now().strftime("%H:%M")
    time_field = _input_field(
        NSMakeRect(132, 128, 72, 22), NSFont.systemFontOfSize_(13),
        placeholder="HH:MM", default=default_time,
    )
    cv.addSubview_(time_field)

    err = _label("", NSMakeRect(20, 108, W - 40, 16), NSFont.systemFontOfSize_(12),
                 color=NSColor.systemOrangeColor())
    cv.addSubview_(err)
    cv.addSubview_(_sep(NSMakeRect(20, 100, W - 40, 1)))

    _btn(cv, "今着いた！", _BTN1, NSMakeRect(20, 64, 110, 28))
    _btn(cv, "時刻を記録", _BTN3, NSMakeRect(138, 64, 110, 28))
    cv.addSubview_(_sep(NSMakeRect(20, 54, W - 40, 1)))

    _btn(cv, "まだ（行く予定）", _BTN2, NSMakeRect(20, 14, 136, 28))
    btn4 = NSButton.alloc().initWithFrame_(NSMakeRect(164, 14, 136, 28))
    btn4.setTitle_("今日は行かない")
    btn4.setBezelStyle_(1)
    btn4.setTag_(_BTN4)
    btn4.setTarget_(_H)
    btn4.setAction_("click:")
    cv.addSubview_(btn4)

    win.setInitialFirstResponder_(time_field)
    _show(win)
    try:
        while True:
            resp = NSApp.runModalForWindow_(win)
            if resp == _CANCEL:
                return None
            if resp == _BTN1:
                return {"status": "arrived", "time": datetime.now().strftime("%H:%M")}
            if resp == _BTN2:
                return {"status": "pending"}
            if resp == _BTN4:
                return {"status": "not_going"}
            raw = time_field.stringValue().strip()
            try:
                parts = raw.split(":")
                if len(parts) != 2:
                    raise ValueError
                h_v, m_v = int(parts[0]), int(parts[1])
                if not (0 <= h_v <= 23 and 0 <= m_v <= 59):
                    raise ValueError
                return {"status": "arrived", "time": f"{h_v:02d}:{m_v:02d}"}
            except (ValueError, IndexError):
                err.setStringValue_("HH:MM 形式で入力してください（例: 09:30）")
    finally:
        win.orderOut_(None)
        _hide()


def show_lab_history_view(lab_arrivals: dict) -> None:
    """GitHub contribution-style grass calendar showing lab arrival history."""
    CELL = 11
    GAP = 2
    STEP = CELL + GAP
    WEEKS = 52
    DAYS = 7
    LEFT_MARGIN = 28
    grid_base_y = 52        # y of bottom row (Sunday)
    grid_h = DAYS * STEP    # 91
    month_label_y = grid_base_y + grid_h + 4   # 147
    title_y = month_label_y + 16 + 6           # 169

    W = LEFT_MARGIN + WEEKS * STEP + 24        # 728
    H = title_y + 26                           # 195

    win = _make_win("📊 ラボ出席カレンダー", W, H)
    cv = win.contentView()

    cv.addSubview_(_label(
        "ラボ出席カレンダー（過去1年間）",
        NSMakeRect(LEFT_MARGIN, title_y, W - LEFT_MARGIN - 20, 22),
        NSFont.boldSystemFontOfSize_(13),
    ))

    today = datetime.now().date()
    current_monday = today - timedelta(days=today.weekday())
    grid_start = current_monday - timedelta(weeks=WEEKS - 1)

    shown_months: set = set()

    def _grass_cell(rect, bg_color, tooltip=""):
        c = NSTextField.alloc().initWithFrame_(rect)
        c.setBezeled_(False)
        c.setEditable_(False)
        c.setSelectable_(False)
        c.setDrawsBackground_(True)
        c.setStringValue_("")
        c.setBackgroundColor_(bg_color)
        if tooltip:
            c.setToolTip_(tooltip)
        return c

    for week in range(WEEKS):
        for day in range(DAYS):
            cell_date = grid_start + timedelta(weeks=week, days=day)
            date_str = cell_date.strftime("%Y-%m-%d")
            x = LEFT_MARGIN + week * STEP
            y = grid_base_y + (DAYS - 1 - day) * STEP

            if cell_date.day <= 7 and cell_date.month not in shown_months and week < WEEKS - 1:
                shown_months.add(cell_date.month)
                cv.addSubview_(_label(
                    f"{cell_date.month}月",
                    NSMakeRect(x, month_label_y, 22, 14),
                    NSFont.systemFontOfSize_(10),
                    color=NSColor.colorWithWhite_alpha_(0.5, 1.0),
                ))

            rect = NSMakeRect(x, y, CELL, CELL)
            if cell_date > today:
                cell = _grass_cell(rect, NSColor.colorWithWhite_alpha_(0.93, 1.0), date_str)
            else:
                arrival = lab_arrivals.get(date_str, {})
                arrival_status = arrival.get("status", "")
                time_str = arrival.get("time", "")
                if arrival_status == "arrived" and time_str:
                    color = _arrival_color(time_str)
                    cell = _grass_cell(rect, color, f"{date_str}  {time_str}着")
                elif arrival_status == "not_going":
                    cell = _grass_cell(rect, NSColor.colorWithWhite_alpha_(0.75, 1.0),
                                       f"{date_str}  今日は行かない")
                elif arrival_status == "pending":
                    color = NSColor.colorWithRed_green_blue_alpha_(1.0, 0.78, 0.10, 0.8)
                    cell = _grass_cell(rect, color, f"{date_str}  行く予定（未記録）")
                else:
                    cell = _grass_cell(rect, NSColor.colorWithWhite_alpha_(0.87, 1.0), date_str)
            cv.addSubview_(cell)

    for day, label in enumerate(["月", "", "水", "", "金", "", "日"]):
        if not label:
            continue
        y = grid_base_y + (DAYS - 1 - day) * STEP
        cv.addSubview_(_label(
            label, NSMakeRect(2, y + 1, LEFT_MARGIN - 4, CELL),
            NSFont.systemFontOfSize_(9),
            color=NSColor.colorWithWhite_alpha_(0.5, 1.0),
        ))

    # Legend
    lx = LEFT_MARGIN
    cv.addSubview_(_label("早い", NSMakeRect(lx, 12, 28, 12), NSFont.systemFontOfSize_(10),
                          color=NSColor.colorWithWhite_alpha_(0.5, 1.0)))
    legend_times = ["09:00", "12:00", "15:00", "17:00", "20:00", "22:00"]
    for i, t in enumerate(legend_times):
        ix = lx + 32 + i * (CELL + 2)
        leg = _grass_cell(NSMakeRect(ix, 12, CELL, CELL), _arrival_color(t))
        cv.addSubview_(leg)
    end_lx = lx + 32 + len(legend_times) * (CELL + 2) + 4
    cv.addSubview_(_label("遅い", NSMakeRect(end_lx, 12, 28, 12), NSFont.systemFontOfSize_(10),
                          color=NSColor.colorWithWhite_alpha_(0.5, 1.0)))

    _btn(cv, "閉じる", _BTN1, NSMakeRect(W - 136, 8, 116, 30), primary=True)
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
        self._lab_reminder_timer = None
        self._pin_win = None
        self._pin_msg_label = None
        self._pin_delegate = None

        self._task_item = rumps.MenuItem("📌 タスク未設定", callback=None)
        self._message_item = rumps.MenuItem("💬 コメント: 未設定", callback=None)
        self._next_item = rumps.MenuItem("🔀 並行: ―", callback=None)
        self._pin_item = rumps.MenuItem("📌 サムをピン留め", callback=self._cmd_toggle_pin)
        self.menu = [
            self._task_item,
            self._next_item,
            self._message_item,
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
            rumps.MenuItem("🏢 ラボ到着を記録",            callback=self._cmd_record_lab_arrival),
            rumps.MenuItem("📊 ラボ出席履歴を見る",        callback=self._cmd_show_lab_history),
            None,
            rumps.MenuItem("📝 今日の振り返り（KPT）",    callback=self._cmd_do_retrospective),
            rumps.MenuItem("📊 過去の振り返りを見る",      callback=self._cmd_show_kpt_history),
            rumps.MenuItem("📆 週次振り返りを見る",        callback=self._cmd_show_weekly_review_history),
            None,
            rumps.MenuItem("❌ 終了", callback=rumps.quit_application),
        ]
        if self.data.get("current_task"):
            self._start_timer()
        else:
            # No active session: create timer but don't start it.
            # _next_checkin_at set far in future so watchdog never auto-fires.
            interval = self.data.get("interval_minutes", DEFAULT_INTERVAL)
            self._next_checkin_at = datetime.now() + timedelta(days=365)
            self._timer = rumps.Timer(self._on_timer_fire, interval * 60)
        self._start_watchdog()
        self._start_ui_timer()
        self._install_edit_shortcuts()
        self._apply_app_icon()
        self._refresh_ui()
        self._check_date_change()
        self._check_week_change()

        self._update_lab_reminder_timer()
        if not self.data["goals"].get("short"):
            rumps.Timer(self._first_run, 1).start()
        else:
            rumps.Timer(self._autoshow_pin, 0.5).start()
            rumps.Timer(self._prompt_missed_retro, 3).start()

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
                data.setdefault("parallel_task", "")
                data.setdefault("current_message", "")
                data.setdefault("sam_messages", [])
                data.setdefault("sam_message", "")
                data.setdefault("kpt", {"date": "", "keep": [], "problem": [], "try": []})
                data.setdefault("kpt_history", [])
                data.setdefault("weekly_review_history", [])
                data.setdefault("active_tries", [])
                data.setdefault("today_date", datetime.now().strftime("%Y-%m-%d"))
                data.setdefault("history", [])
                data.setdefault("lab_arrivals", {})
                data.setdefault("deferred_tasks", [])
                data.setdefault("retro_reminded", {"date": "", "hours": []})
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
            "parallel_task": "",
            "current_message": "",
            "sam_messages": [],
            "sam_message": "",
            "kpt": {"date": "", "keep": [], "problem": [], "try": []},
            "kpt_history": [],
            "active_tries": [],
            "interval_minutes": DEFAULT_INTERVAL,
            "today_date": datetime.now().strftime("%Y-%m-%d"),
            "history": [],
            "lab_arrivals": {},
            "deferred_tasks": [],
        }

    def _save(self):
        with open(DATA_FILE, "w") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)

    # ── Display ───────────────────────────────────────────────────────────

    def _refresh_ui(self):
        task = self.data.get("current_task") or "タスク未設定"
        msg = self.data.get("current_message") or "未設定"
        parallel = self.data.get("parallel_task") or ""
        self._task_item.title = f"📌 今: {_truncate8(task)}"
        self._next_item.title = f"🔀 並行: {_truncate8(parallel)}" if parallel else "🔀 並行: ―"
        self._message_item.title = f"💬 コメント: {_truncate10(msg)}"
        self._update_countdown()
        sam_msg = self.data.get("sam_message") or "—"
        if self._pin_win is not None and self._pin_msg_label is not None:
            try:
                self._pin_msg_label.setStringValue_(sam_msg)
            except Exception:
                pass

    def _install_edit_shortcuts(self):
        try:
            _setup_app_menu()

            main = NSApp.mainMenu()
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

        # Fallback: local key monitor catches Cmd+Q even if menu dispatch fails
        try:
            NSEvent.addLocalMonitorForEventsMatchingMask_handler_(
                NSEventMaskKeyDown,
                lambda event: (
                    rumps.quit_application() or None
                    if (event.modifierFlags() & NSEventModifierFlagCommand
                        and event.charactersIgnoringModifiers() == "q")
                    else event
                ),
            )
        except Exception:
            pass

    def _apply_app_icon(self):
        for icon_path in ICON_CANDIDATES:
            if not os.path.exists(icon_path):
                continue
            try:
                img = NSImage.alloc().initByReferencingFile_(icon_path)
                if img is not None:
                    NSApp.setApplicationIconImage_(_pad_icon_to_square(img))
                    return
            except Exception:
                continue

    def _update_countdown(self):
        if not self._break_mode and not self.data.get("current_task"):
            self.title = "🎯"
            return
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
        _ui_ticker.app_ref = self
        t = NSTimer.timerWithTimeInterval_target_selector_userInfo_repeats_(
            1.0, _ui_ticker, "tick:", None, True)
        NSRunLoop.mainRunLoop().addTimer_forMode_(t, NSRunLoopCommonModes)
        self._ui_timer = t

    def _on_watchdog(self, _):
        if datetime.now() >= self._next_checkin_at:
            self._do_checkin()
            return
        self._check_date_change()
        self._check_week_change()
        self._check_retro_reminder()

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
        new_today = scheduled + carryover
        deferred = self.data.pop("deferred_tasks", [])
        if deferred:
            existing_texts = {t["text"] for t in new_today}
            for t in deferred:
                text = (t.get("text") or "") if isinstance(t, dict) else str(t)
                if text and text not in existing_texts:
                    new_today.append({"text": text, "done": False})
                    existing_texts.add(text)
        self.data["today_date"] = today_str
        self.data["goals"]["today"] = new_today
        # Reset daily retro reminder tracking for the new day
        self.data["retro_reminded"] = {"date": today_str, "hours": []}
        self._save()
        self._update_lab_reminder_timer()
        self._check_week_change()
        if not self._checkin_active:
            rumps.Timer(self._prompt_new_day, 2).start()
            rumps.Timer(self._prompt_missed_retro, 4).start()

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

    def _autoshow_pin(self, timer: rumps.Timer):
        timer.stop()
        self._show_pin_window()

    def _first_run(self, timer: rumps.Timer):
        timer.stop()
        self._setup_all_goals()
        self._show_pin_window()

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
                kept, deferred = val
                self.data["goals"]["today"] = kept
                self._merge_deferred(deferred)
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
            result, parallel_done = show_feedback(current, self.data.get("parallel_task", ""))
            if result == "complete":
                notify("🎉 完了！", current, "素晴らしい！この調子で続けよう！")
                today_items = _normalize_today(self.data["goals"].get("today", []))
                current_idx = next(
                    (i for i, t in enumerate(today_items) if t["text"] == current), None)
                if current_idx is not None:
                    today_items[current_idx]["done"] = True
                    self.data["goals"]["today"] = today_items
                    if not self.data.get("next_task"):
                        next_undone = next(
                            (t["text"] for t in today_items[current_idx + 1:] if not t["done"]),
                            None)
                        if next_undone:
                            self.data["next_task"] = next_undone
                    self._save()
            elif result == "progress":
                notify("💪 前進中！", current, "少しでも動けたことが大切！")
            elif result == "replan":
                notify("🔄 賢い判断！", "難しすぎたのかも", "もっと小さなタスクに分けてみよう 💡")

            if parallel_done:
                parallel = self.data.get("parallel_task", "")
                if parallel:
                    today_items = _normalize_today(self.data["goals"].get("today", []))
                    pidx = next((i for i, t in enumerate(today_items) if t["text"] == parallel), None)
                    if pidx is not None:
                        today_items[pidx]["done"] = True
                        self.data["goals"]["today"] = today_items
                    self.data["parallel_task"] = ""
                    self._save()
                    notify("✅ 並行タスクも完了！", parallel[:30], "")

        nudge_timer = NSTimer.timerWithTimeInterval_target_selector_userInfo_repeats_(
            CHECKIN_NUDGE_INTERVAL, _checkin_nudger, "nudge:", None, True)
        NSRunLoop.mainRunLoop().addTimer_forMode_(nudge_timer, NSRunLoopCommonModes)
        NSRunLoop.mainRunLoop().addTimer_forMode_(nudge_timer, "NSModalPanelRunLoopMode")
        try:
            while True:
                _wr_history = self.data.get("weekly_review_history", [])
                _weekly_tries = _wr_history[-1]["kpt"].get("try", []) if _wr_history else []
                action, new_task, parallel_task, message, session_mins, updated_today = show_checkin(
                    self.data["goals"],
                    today_date=self.data.get("today_date", ""),
                    current_task=self.data.get("current_task", ""),
                    queued_task=self.data.get("next_task", ""),
                    current_message=self.data.get("current_message", ""),
                    current_interval=self.data.get("interval_minutes", DEFAULT_INTERVAL),
                    active_tries=self.data.get("active_tries", []),
                    weekly_tries=_weekly_tries,
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
                    kept, deferred = val
                    self.data["goals"]["today"] = kept
                    self._merge_deferred(deferred)
                    self._save()
        finally:
            nudge_timer.invalidate()

        if action == "break" or new_task is None:
            self._break_mode = True
            self._reset_timer(override_minutes=BREAK_MINUTES)
            self._refresh_ui()
            notify("☕ 休憩スタート！", f"{BREAK_MINUTES}分後にチェックインします", "ゆっくり休んでください")
            return

        self._break_mode = False
        self.data["current_task"] = new_task
        self.data["next_task"] = ""
        self.data["parallel_task"] = parallel_task or ""
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

        # Position bottom-left of active screen
        try:
            mouse_loc = NSEvent.mouseLocation()
            target_screen = NSScreen.mainScreen()
            for _s in NSScreen.screens():
                _sf = _s.frame()
                if (_sf.origin.x <= mouse_loc.x < _sf.origin.x + _sf.size.width and
                        _sf.origin.y <= mouse_loc.y < _sf.origin.y + _sf.size.height):
                    target_screen = _s
                    break
            sr = target_screen.visibleFrame()
            win_x = sr.origin.x + 20
            win_y = sr.origin.y + 10
            win.setFrameOrigin_((win_x, win_y))
        except Exception:
            _center_on_active_screen(win)

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

    def _check_retro_reminder(self):
        """Fire retrospective reminder popup at scheduled hours if KPT not yet done today."""
        if self._checkin_active:
            return
        now = datetime.now()
        today_str = self.data.get("today_date", now.strftime("%Y-%m-%d"))
        hour = now.hour
        reminded = self.data.setdefault("retro_reminded", {"date": today_str, "hours": []})
        if reminded.get("date") != today_str:
            reminded["date"] = today_str
            reminded["hours"] = []
        if hour not in RETRO_REMINDER_HOURS:
            return
        if hour in reminded.get("hours", []):
            return
        # Check if already done today
        kpt_history = self.data.get("kpt_history", [])
        if any(e.get("date") == today_str for e in kpt_history):
            reminded["hours"].append(hour)
            self._save()
            return
        reminded["hours"].append(hour)
        self._save()
        rumps.Timer(lambda t: (t.stop(), _show_retro_nudge_popup(self, today_str, False)), 1).start()

    def _prompt_missed_retro(self, timer: rumps.Timer):
        """On startup or date change: prompt for yesterday's retro if it was skipped."""
        timer.stop()
        if self._checkin_active:
            return
        today_str = self.data.get("today_date", datetime.now().strftime("%Y-%m-%d"))
        try:
            today_dt = datetime.strptime(today_str, "%Y-%m-%d")
            yesterday_str = (today_dt - timedelta(days=1)).strftime("%Y-%m-%d")
        except ValueError:
            return
        kpt_history = self.data.get("kpt_history", [])
        if any(e.get("date") == yesterday_str for e in kpt_history):
            return
        # Only prompt if yesterday exists in task history (i.e. we actually used the app)
        task_history = self.data.get("history", [])
        if not any(e.get("date") == yesterday_str for e in task_history):
            return
        _show_retro_nudge_popup(self, yesterday_str, is_yesterday=True)

    def _do_retrospective_for(self, date_str: str):
        """Run the KPT editor and save the result for the given date."""
        if self._checkin_active:
            return
        self._checkin_active = True
        try:
            # Pre-fill only if an entry for this date already exists; otherwise start empty
            kpt_history = self.data.get("kpt_history", [])
            existing = next((e for e in kpt_history if e.get("date") == date_str), {})
            result = show_kpt_editor(
                existing.get("keep", []),
                existing.get("problem", []),
                existing.get("try", []),
                kpt_history=kpt_history,
            )
            if result is None:
                return
            result["date"] = date_str
            # Upsert into kpt_history
            for i, entry in enumerate(kpt_history):
                if entry.get("date") == date_str:
                    kpt_history[i] = dict(result)
                    break
            else:
                kpt_history.append(dict(result))
            # Update current kpt only if this is today
            today_str = self.data.get("today_date", datetime.now().strftime("%Y-%m-%d"))
            if date_str == today_str:
                self.data["kpt"] = result
                selected = show_try_selector(result.get("try", []))
                self.data["active_tries"] = selected
            self._save()
        finally:
            self._checkin_active = False

    def _cmd_do_retrospective(self, _):
        today_str = self.data.get("today_date", datetime.now().strftime("%Y-%m-%d"))
        self._do_retrospective_for(today_str)

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

    # ── Deferred tasks ────────────────────────────────────────────────────

    def _merge_deferred(self, deferred: list):
        """Append deferred tasks to the queue (skipping duplicates)."""
        if not deferred:
            return
        existing = self.data.setdefault("deferred_tasks", [])
        existing_texts = {t["text"] for t in existing}
        for t in deferred:
            if t["text"] and t["text"] not in existing_texts:
                existing.append(t)
                existing_texts.add(t["text"])

    # ── Lab arrival tracking ──────────────────────────────────────────────

    def _update_lab_reminder_timer(self):
        """Start 30-min reminder timer if today's lab status is 'pending'; stop otherwise."""
        today_str = self.data.get("today_date", datetime.now().strftime("%Y-%m-%d"))
        lab_arrivals = self.data.get("lab_arrivals", {})
        status = lab_arrivals.get(today_str, {}).get("status", "")
        if status == "pending":
            if self._lab_reminder_timer is None:
                self._lab_reminder_timer = rumps.Timer(self._on_lab_reminder_fire, 30 * 60)
                self._lab_reminder_timer.start()
        else:
            if self._lab_reminder_timer is not None:
                self._lab_reminder_timer.stop()
                self._lab_reminder_timer = None

    def _on_lab_reminder_fire(self, timer):
        today_str = self.data.get("today_date", datetime.now().strftime("%Y-%m-%d"))
        status = self.data.get("lab_arrivals", {}).get(today_str, {}).get("status", "")
        if status != "pending":
            timer.stop()
            self._lab_reminder_timer = None
            return
        if not self._checkin_active:
            _show_lab_reminder_popup(self)

    def _cmd_record_lab_arrival(self, _):
        if self._checkin_active:
            return
        self._checkin_active = True
        try:
            today_str = self.data.get("today_date", datetime.now().strftime("%Y-%m-%d"))
            lab_arrivals = self.data.setdefault("lab_arrivals", {})
            current = lab_arrivals.get(today_str, {})
            result = show_lab_arrival_dialog(today_str, current)
            if result is not None:
                lab_arrivals[today_str] = result
                self._save()
                self._update_lab_reminder_timer()
                status = result.get("status", "")
                time_str = result.get("time", "")
                if status == "arrived":
                    notify("🏢 ラボ到着を記録しました", f"到着時刻: {time_str}", "")
                elif status == "pending":
                    notify("🏢 行く予定として記録しました", "30分ごとにリマインドします", "")
                elif status == "not_going":
                    notify("🏢 今日はラボなしで記録しました", "", "")
        finally:
            self._checkin_active = False

    def _cmd_show_lab_history(self, _):
        if self._checkin_active:
            return
        self._checkin_active = True
        try:
            show_lab_history_view(self.data.get("lab_arrivals", {}))
        finally:
            self._checkin_active = False


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    ProgressChecker().run()
