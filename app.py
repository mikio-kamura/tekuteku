#!/usr/bin/env python3
"""てくてく — macOS menu bar productivity app with native AppKit dialogs."""
import json
import os
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
    NSScrollView,
    NSStrikethroughStyleAttributeName,
    NSTableColumn,
    NSTableView,
    NSTextField,
    NSTextView,
    NSView,
    NSWindow,
)

DATA_FILE = os.path.expanduser("~/.tekuteku.json")
_OLD_DATA_FILE = os.path.expanduser("~/.progress_checker.json")
DEFAULT_INTERVAL = 20  # minutes
BREAK_MINUTES = 5

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
        return self

    def numberOfRowsInTableView_(self, _table):
        return len(self.items)

    def tableView_objectValueForTableColumn_row_(self, _table, _column, row):
        if 0 <= row < len(self.items):
            return self.items[row]["text"]
        return ""

    def tableView_setObjectValue_forTableColumn_row_(self, _table, value, _column, row):
        if 0 <= row < len(self.items):
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


class _Handler(NSObject):
    def click_(self, sender):
        NSApp.stopModalWithCode_(sender.tag())

    def toggle_(self, sender):
        base = str(sender.representedObject() or sender.title())
        sender.setAttributedTitle_(_styled_task_title(base, sender.state() != 0))

    def windowShouldClose_(self, _):
        NSApp.stopModalWithCode_(_CANCEL)
        return False


_H = _Handler.alloc().init()


def _make_win(title: str, w: int, h: int) -> NSWindow:
    win = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
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


def show_checkin(
    goals: dict,
    current_task: str = "",
    queued_task: str = "",
    queued_next_task: str = "",
    current_message: str = "",
) -> tuple[str, Optional[str], Optional[str], Optional[str], list]:
    """Returns (action, next_task, next_next_task, message, updated_today_items).
    action is one of: start, break, edit_today."""
    today_items = _normalize_today(goals.get("today", []))
    n = len(today_items)

    ITEM_H = 24
    GAP = 6
    # Fixed bottom section: y=0..364. Today section sits above y=364.
    items_bottom_y = 364 + GAP
    items_section_h = min(max(n, 1), 10) * ITEM_H if n else 22
    today_label_y = items_bottom_y + items_section_h + GAP
    H = today_label_y + 20 + 16  # label h=20, top margin=16

    W = 480
    win = _make_win("チェックイン", W, H)
    cv = win.contentView()

    # ── 今日やりたいこと ────────────────────────────────────────────────────
    cv.addSubview_(_label(
        "📅  今日やりたいこと",
        NSMakeRect(20, today_label_y, W-40, 20),
        NSFont.boldSystemFontOfSize_(13),
        color=NSColor.systemBlueColor(),
    ))
    checkboxes: list[NSButton] = []
    if today_items:
        view_h = min(max(n, 1), 10) * ITEM_H
        scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(24, items_bottom_y, W - 40, view_h))
        scroll.setHasVerticalScroller_(n > 10)
        scroll.setAutohidesScrollers_(True)
        scroll.setBorderType_(2)
        doc_h = max(view_h, n * ITEM_H)
        doc = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, W - 58, doc_h))
        for i, item in enumerate(today_items):
            # item[0] is topmost visually → highest Y in doc coords
            item_y = doc_h - (i + 1) * ITEM_H
            cb = NSButton.alloc().initWithFrame_(NSMakeRect(4, item_y, W - 64, ITEM_H - 2))
            cb.setAttributedTitle_(_styled_task_title(f"{i+1}. {item['text']}", bool(item["done"])))
            cb.setRepresentedObject_(f"{i+1}. {item['text']}")
            cb.setButtonType_(3)  # NSSwitchButton = checkbox
            cb.setState_(1 if item["done"] else 0)
            cb.setFont_(NSFont.systemFontOfSize_(13))
            cb.setTarget_(_H)
            cb.setAction_("toggle:")
            doc.addSubview_(cb)
            checkboxes.append(cb)
        scroll.setDocumentView_(doc)
        cv.addSubview_(scroll)
    else:
        cv.addSubview_(_mlabel("未設定", NSMakeRect(28, items_bottom_y, W-48, 22), NSFont.systemFontOfSize_(13)))

    cv.addSubview_(_sep(NSMakeRect(20, 364, W-40, 1)))

    # ── 今週 ────────────────────────────────────────────────────────────────
    cv.addSubview_(_label("📋  今週", NSMakeRect(20, 344, W-40, 16), NSFont.boldSystemFontOfSize_(12)))
    cv.addSubview_(_mlabel(goals.get("weekly") or "未設定", NSMakeRect(28, 318, W-48, 22), NSFont.systemFontOfSize_(12)))
    cv.addSubview_(_sep(NSMakeRect(20, 310, W-40, 1)))

    # ── 短期目標 ────────────────────────────────────────────────────────────
    cv.addSubview_(_label("📌  短期目標", NSMakeRect(20, 290, W-40, 16), NSFont.boldSystemFontOfSize_(12)))
    cv.addSubview_(_mlabel(goals.get("short") or "未設定", NSMakeRect(28, 264, W-48, 22), NSFont.systemFontOfSize_(12)))
    cv.addSubview_(_sep(NSMakeRect(20, 256, W-40, 1)))

    # ── 中期目標 ────────────────────────────────────────────────────────────
    cv.addSubview_(_label("📅  中期目標", NSMakeRect(20, 236, W-40, 16), NSFont.boldSystemFontOfSize_(12)))
    cv.addSubview_(_mlabel(goals.get("mid") or "未設定", NSMakeRect(28, 210, W-48, 22), NSFont.systemFontOfSize_(12)))
    cv.addSubview_(_sep(NSMakeRect(20, 202, W-40, 1)))

    # ── 長期目標 ────────────────────────────────────────────────────────────
    cv.addSubview_(_label("🌟  長期目標", NSMakeRect(20, 182, W-40, 16), NSFont.boldSystemFontOfSize_(12)))
    cv.addSubview_(_mlabel(goals.get("long") or "未設定", NSMakeRect(28, 156, W-48, 22), NSFont.systemFontOfSize_(12)))
    cv.addSubview_(_sep(NSMakeRect(20, 148, W-40, 1)))

    # ── 次回/次々回の選択 + メッセージ ────────────────────────────────────────
    cv.addSubview_(_label("次回・次々回にやる番号を選ぶ", NSMakeRect(20, 144, W-40, 18), NSFont.boldSystemFontOfSize_(13)))
    cv.addSubview_(_label("次のセッション", NSMakeRect(20, 122, 110, 18), NSFont.systemFontOfSize_(12)))
    cv.addSubview_(_label("その次（任意）", NSMakeRect(156, 122, 110, 18), NSFont.systemFontOfSize_(12)))

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

    field_next = _input_field(
        NSMakeRect(20, 102, 120, 26),
        NSFont.systemFontOfSize_(15),
        placeholder="番号",
        default=default_next,
    )
    field_next_next = _input_field(
        NSMakeRect(156, 102, 120, 26),
        NSFont.systemFontOfSize_(15),
        placeholder="任意",
        default=default_next_next,
    )
    cv.addSubview_(field_next)
    cv.addSubview_(field_next_next)
    cv.addSubview_(_label("自分へのメッセージ", NSMakeRect(20, 82, W-40, 16), NSFont.systemFontOfSize_(12)))
    field_msg = _input_field(
        NSMakeRect(20, 56, W - 40, 24),
        NSFont.systemFontOfSize_(14),
        placeholder="例: 焦らず1行だけ進める",
        default=current_message,
    )
    cv.addSubview_(field_msg)
    err = _label("", NSMakeRect(20, 36, W-40, 16), NSFont.systemFontOfSize_(12), color=NSColor.systemOrangeColor())
    cv.addSubview_(err)

    # ── ボタン ──────────────────────────────────────────────────────────────
    _btn(cv, "スタート！",                _BTN1, NSMakeRect(W-160, 8, 140, 28), primary=True)
    _btn(cv, f"☕  {BREAK_MINUTES}分休憩", _BTN2, NSMakeRect(W-312, 8, 140, 28))
    _btn(cv, "📝 細分タスク編集",          _BTN3, NSMakeRect(20, 8, 128, 28))

    win.setInitialFirstResponder_(field_next)
    _show(win)
    try:
        while True:
            resp = NSApp.runModalForWindow_(win)
            updated_today = [
                {"text": item["text"], "done": checkboxes[i].state() != 0}
                for i, item in enumerate(today_items)
            ]
            if resp in (_CANCEL, _BTN2):
                return "break", None, None, None, updated_today
            if resp == _BTN3:
                return "edit_today", None, None, None, updated_today

            if not today_items:
                err.setStringValue_("先に細分タスクを追加してください")
                continue

            next_task = _parse_task_index(field_next.stringValue(), today_items)
            next_next_task = _parse_task_index(field_next_next.stringValue(), today_items)
            msg = field_msg.stringValue().strip()
            if not next_task:
                err.setStringValue_("次のセッションの番号を入力してください")
                continue
            if field_next_next.stringValue().strip() and not next_next_task:
                err.setStringValue_("その次の番号が不正です")
                continue
            return "start", next_task, next_next_task, msg, updated_today
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
        self._message_item = rumps.MenuItem("💬 コメント: 未設定", callback=None)
        self._next_item = rumps.MenuItem("⏭ 次: 未設定", callback=None)
        self.menu = [
            self._task_item,
            self._message_item,
            self._next_item,
            None,
            rumps.MenuItem("🔄 今すぐチェックイン",    callback=self._cmd_checkin),
            rumps.MenuItem("⏱ セッション時間を変更",  callback=self._cmd_edit_interval),
            None,
            rumps.MenuItem("🌟 長期目標を変更",        callback=self._cmd_edit_long),
            rumps.MenuItem("📅 中期目標を変更",        callback=self._cmd_edit_mid),
            rumps.MenuItem("📌 短期目標を変更",        callback=self._cmd_edit_short),
            rumps.MenuItem("📋 今週の目標を変更",      callback=self._cmd_edit_weekly),
            rumps.MenuItem("🗓  今日の目標を変更",      callback=self._cmd_edit_today),
            None,
            rumps.MenuItem("❌ 終了", callback=rumps.quit_application),
        ]
        self._start_timer()
        self._start_watchdog()
        self._start_ui_timer()
        self._install_edit_shortcuts()
        self._apply_app_icon()
        self._refresh_ui()

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
                for key in ("long", "mid", "short", "weekly"):
                    g.setdefault(key, "")
                g["today"] = _normalize_today(g.get("today", []))
                data.setdefault("next_task", "")
                data.setdefault("next_next_task", "")
                data.setdefault("current_message", "")
                data.setdefault("today_date", datetime.now().strftime("%Y-%m-%d"))
                return data
            except Exception:
                pass
        return {
            "goals": {"long": "", "mid": "", "short": "", "weekly": "", "today": []},
            "current_task": "",
            "next_task": "",
            "next_next_task": "",
            "current_message": "",
            "interval_minutes": DEFAULT_INTERVAL,
            "today_date": datetime.now().strftime("%Y-%m-%d"),
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

    def _install_edit_shortcuts(self):
        try:
            main = NSApp.mainMenu()
            if main is None:
                main = NSMenu.alloc().initWithTitle_("")
                NSApp.setMainMenu_(main)

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

    def _check_date_change(self):
        today_str = datetime.now().strftime("%Y-%m-%d")
        if self.data.get("today_date") == today_str:
            return
        # Date rolled over: reset checkboxes and prompt for new goals
        self.data["today_date"] = today_str
        for item in self.data["goals"].get("today", []):
            if isinstance(item, dict):
                item["done"] = False
        self._save()
        if not self._checkin_active:
            rumps.Timer(self._prompt_new_day, 2).start()

    def _prompt_new_day(self, timer: rumps.Timer):
        timer.stop()
        if self._checkin_active:
            return
        notify("🌅 新しい日が始まりました！", "今日の目標を更新しましょう")
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
                ("long",   "長期目標 (1/5)", "1〜2年後に達成したいことは？"),
                ("mid",    "中期目標 (2/5)", "1〜5ヶ月で達成したいことは？"),
                ("short",  "短期目標 (3/5)", "今日〜1ヶ月で達成したいことは？"),
                ("weekly", "今週の目標 (4/5)", "今週やりたいことは？"),
            ]
            updated: dict = {k: g.get(k, "") for k in ("long", "mid", "short", "weekly")}
            for key, title, prompt in str_entries:
                val = show_goal_input(title, prompt, default=g.get(key, ""))
                if val is not None:
                    updated[key] = val

            today_texts = [item["text"] for item in _normalize_today(g.get("today", []))]
            val = show_list_input("今日の目標 (5/5)", "今日やりたいことは？", today_texts)
            if val is not None:
                old_done = {item["text"]: item["done"] for item in _normalize_today(g.get("today", []))}
                updated["today"] = [{"text": t, "done": old_done.get(t, False)} for t in val]
            else:
                updated["today"] = _normalize_today(g.get("today", []))

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
            action, new_task, queued_next_task, message, updated_today = show_checkin(
                self.data["goals"],
                current_task=self.data.get("current_task", ""),
                queued_task=self.data.get("next_task", ""),
                queued_next_task=self.data.get("next_next_task", ""),
                current_message=self.data.get("current_message", ""),
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
        self._save()
        self._reset_timer()
        self._refresh_ui()

        mins = self.data.get("interval_minutes", DEFAULT_INTERVAL)
        subtitle = f"今やること: {new_task}"
        body = f"コメント: {self.data.get('current_message', '')}" if self.data.get("current_message") else ""
        notify("スタート！ 🚀", subtitle, body or f"{mins}分後にまたチェックインします")

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
        self._edit_goal("weekly", "今週の目標を変更", "今週やりたいことは？")

    def _cmd_edit_today(self, _):
        self._edit_today()


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    ProgressChecker().run()
