import unittest

import app


class WindowBehaviorTests(unittest.TestCase):
    def test_collection_behavior_does_not_use_conflicting_flags(self):
        # Regression test:
        # NSWindowCollectionBehaviorCanJoinAllSpaces (1<<0) and
        # NSWindowCollectionBehaviorMoveToActiveSpace (1<<1) must not be
        # specified together for a single window.
        behavior = app._WC_MANAGED | app._WC_CYCLE
        all_spaces = 1 << 0
        move_to_active = 1 << 1
        self.assertFalse((behavior & all_spaces) and (behavior & move_to_active))

    def test_make_win_smoke(self):
        win = app._make_win("test", 320, 180)
        self.assertIsNotNone(win)
        # Should be the expected non-conflicting behavior bitmask.
        self.assertEqual(win.collectionBehavior(), app._WC_MANAGED | app._WC_CYCLE)
        win.close()


class DisplayAndConfigTests(unittest.TestCase):
    def test_icon_candidates_include_workspace_star(self):
        self.assertTrue(
            any(path.endswith("/sam.png") for path in app.ICON_CANDIDATES)
        )

    def test_truncate10_behavior(self):
        self.assertEqual(app._truncate10("1234567890"), "1234567890")
        self.assertEqual(app._truncate10("12345678901"), "1234567890…")


if __name__ == "__main__":
    unittest.main()


class _FakeIndexSet:
    def __init__(self, idxs):
        self._idxs = sorted(idxs)

    def count(self):
        return len(self._idxs)

    def firstIndex(self):
        return self._idxs[0]

    def indexGreaterThanIndex_(self, i):
        for j in self._idxs:
            if j > i:
                return j
        return 2**31 - 1


class _FakePasteboard:
    def __init__(self):
        self.s = ""

    def declareTypes_owner_(self, _t, _o):
        pass

    def setString_forType_(self, s, _t):
        self.s = s

    def stringForType_(self, _t):
        return self.s


class _FakeDragInfo:
    def __init__(self, pb):
        self._pb = pb

    def draggingPasteboard(self):
        return self._pb


class _FakeTable:
    def deselectAll_(self, _):
        pass


def _tree(*pairs):
    return [{"text": t, "done": False, "level": lv} for t, lv in pairs]


class TaskTreeTests(unittest.TestCase):
    def test_normalize_today_adds_level_and_clamps(self):
        items = app._normalize_today(["a", {"text": "b", "level": 5}, {"text": "c", "level": 1}])
        self.assertEqual([i["level"] for i in items], [0, 1, 1])  # b は親が level0 なので 1 に詰まる

    def test_normalize_levels_fixes_orphans(self):
        items = _tree(("a", 0), ("b", 2), ("c", 2), ("d", 0), ("e", 1))
        app._normalize_levels(items)
        self.assertEqual([i["level"] for i in items], [0, 1, 2, 0, 1])

    def test_subtree_indices(self):
        items = _tree(("a", 0), ("a1", 1), ("a1x", 2), ("a2", 1), ("b", 0))
        self.assertEqual(app._subtree_indices(items, 0), [0, 1, 2, 3])
        self.assertEqual(app._subtree_indices(items, 1), [1, 2])
        self.assertEqual(app._subtree_indices(items, 4), [4])
        self.assertEqual(app._subtree_end(items, 0), 4)

    def test_task_display_and_strip(self):
        self.assertEqual(app._task_display({"text": "x", "level": 2}, 3), "　　└ 3. x")
        self.assertEqual(app._task_display({"text": "x", "level": 0}), "x")
        self.assertEqual(app._strip_task_prefix("　　└ x"), "x")
        self.assertEqual(app._strip_task_prefix(app._ZWSP + "x"), "x")   # 新行編集開始時の見えない1文字
        self.assertEqual(app._strip_task_prefix(app._ZWSP), "")

    def test_model_done_cascades_to_children(self):
        items = _tree(("a", 0), ("a1", 1), ("a1x", 2), ("b", 0))
        m = app._TodayTaskTableModel.alloc().initWithItems_(items)
        col = type("C", (), {"identifier": lambda self: "done"})()
        m.tableView_setObjectValue_forTableColumn_row_(None, 1, col, 0)
        self.assertEqual([i["done"] for i in m.items], [True, True, True, False])

    def test_model_edit_strips_prefix_and_number(self):
        m = app._TodayTaskTableModel.alloc().initWithItems_(_tree(("a", 0), ("a1", 1)))
        m.show_numbers = True
        col = type("C", (), {"identifier": lambda self: "task"})()
        m.tableView_setObjectValue_forTableColumn_row_(None, "　└ 2. new", col, 1)
        self.assertEqual(m.items[1]["text"], "new")

    def test_model_drag_moves_subtree_and_reparents(self):
        items = _tree(("a", 0), ("a1", 1), ("a1x", 2), ("b", 0))
        m = app._TodayTaskTableModel.alloc().initWithItems_(items)
        pb = _FakePasteboard()
        m.tableView_writeRowsWithIndexes_toPasteboard_(None, _FakeIndexSet([1]), pb)
        # a1（と a1x）を b の下（末尾）へ
        ok = m.tableView_acceptDrop_row_dropOperation_(_FakeTable(), _FakeDragInfo(pb), 4, 0)
        self.assertTrue(ok)
        self.assertEqual([i["text"] for i in m.items], ["a", "b", "a1", "a1x"])
        self.assertEqual([i["level"] for i in m.items], [0, 0, 1, 2])
        # 先頭へ動かすと親がいないので level0 に上がる（子も相対的に上がる）
        pb2 = _FakePasteboard()
        m.tableView_writeRowsWithIndexes_toPasteboard_(None, _FakeIndexSet([2]), pb2)
        m.tableView_acceptDrop_row_dropOperation_(_FakeTable(), _FakeDragInfo(pb2), 0, 0)
        self.assertEqual([i["text"] for i in m.items], ["a1", "a1x", "a", "b"])
        self.assertEqual([i["level"] for i in m.items], [0, 1, 0, 0])


class NextTaskTests(unittest.TestCase):
    def test_first_undone_prefers_leaf(self):
        items = _tree(("親", 0), ("子1", 1), ("子2", 1), ("次", 0))
        items[1]["done"] = True
        self.assertEqual(app._first_undone_index(items), 2)          # 親(0)は飛ばして子2
        self.assertEqual(app._first_undone_index(items, 3), 3)
        items[2]["done"] = True
        self.assertEqual(app._first_undone_index(items), 3)          # 末端「次」
        for i in items: i["done"] = True
        self.assertIsNone(app._first_undone_index(items))


class HiddenDoneTests(unittest.TestCase):
    def test_split_and_restore_keeps_positions_after_edit(self):
        items = _tree(("親", 0), ("済み子", 1), ("未子", 1), ("済み親", 0), ("済み孫", 1), ("次", 0))
        for i in (1, 3, 4):
            items[i]["done"] = True
        visible, hidden = app._split_done(items)
        self.assertEqual([v["text"] for v in visible], ["親", "未子", "次"])
        self.assertEqual(len(hidden), 3)
        # 編集：anchor の文面と階層を変えても同一オブジェクトなので追える
        visible[0]["text"] = "親（改）"
        visible[1]["level"] = 0   # 未子を親と同じ階層に
        visible.insert(2, {"text": "新規", "done": False, "level": 0})
        out = app._restore_done(visible, hidden)
        self.assertEqual([o["text"] for o in out],
                         ["親（改）", "済み子", "未子", "済み親", "済み孫", "新規", "次"])
        self.assertEqual([o["level"] for o in out], [0, 1, 0, 0, 1, 0, 0])
        self.assertEqual([o["done"] for o in out], [False, True, False, True, True, False, False])

    def test_restore_when_anchor_deleted_keeps_record_at_end(self):
        items = _tree(("a", 0), ("済", 1), ("b", 0))
        items[1]["done"] = True
        visible, hidden = app._split_done(items)
        del visible[0]   # a を削除
        out = app._restore_done(visible, hidden)
        self.assertEqual([o["text"] for o in out], ["b", "済"])
        self.assertEqual(out[1]["level"], 0)

    def test_restore_done_at_top(self):
        items = _tree(("済", 0), ("a", 0))
        items[0]["done"] = True
        visible, hidden = app._split_done(items)
        out = app._restore_done(visible, hidden)
        self.assertEqual([o["text"] for o in out], ["済", "a"])


class MoveSubtreeTests(unittest.TestCase):
    def test_move_up_down_between_siblings(self):
        items = _tree(("a", 0), ("a1", 1), ("b", 0), ("b1", 1), ("b1x", 2), ("c", 0))
        i = app._move_subtree(items, 2, -1)   # b（子ごと）を a の前へ
        self.assertEqual(i, 0)
        self.assertEqual([t["text"] for t in items], ["b", "b1", "b1x", "a", "a1", "c"])
        i = app._move_subtree(items, 0, 1)    # b を a の後ろへ戻す
        self.assertEqual(i, 2)
        self.assertEqual([t["text"] for t in items], ["a", "a1", "b", "b1", "b1x", "c"])
        # 子は親の外へは出ない
        self.assertEqual(app._move_subtree(items, 1, -1), 1)
        self.assertEqual(app._move_subtree(items, 1, 1), 1)
        # 末尾は下に動かせない
        self.assertEqual(app._move_subtree(items, 5, 1), 5)
        self.assertEqual([t["text"] for t in items], ["a", "a1", "b", "b1", "b1x", "c"])


class NextTaskIndexTests(unittest.TestCase):
    def test_smallest_unchecked_skipping_parents_with_open_children(self):
        items = _tree(("親", 0), ("子1", 1), ("子2", 1), ("次", 0))
        self.assertEqual(app._next_task_index(items), 1)       # 親は子が残っているので飛ばす
        items[1]["done"] = True
        self.assertEqual(app._next_task_index(items), 2)
        items[2]["done"] = True
        self.assertEqual(app._next_task_index(items), 0)       # 子が全部済んだら親自身が一番若い未完了
        items[0]["done"] = True
        self.assertEqual(app._next_task_index(items), 3)
        items[3]["done"] = True
        self.assertIsNone(app._next_task_index(items))


class TriggerFreshnessTests(unittest.TestCase):
    def test_fresh_and_stale(self):
        self.assertTrue(app._is_fresh_trigger({"t": 1000.0}, now=1001.0))
        self.assertFalse(app._is_fresh_trigger({"t": 1000.0}, now=1000.0 + app.TRIGGER_MAX_AGE_SEC + 1))
        self.assertTrue(app._is_fresh_trigger(None))
        self.assertTrue(app._is_fresh_trigger({"t": "garbage"}))


class _FakeTimer:
    def __init__(self):
        self.stopped = False

    def stop(self):
        self.stopped = True


def _bare_app(today):
    """rumps の初期化を通さずに ProgressChecker のメソッドだけ試すための素体。"""
    obj = app.ProgressChecker.__new__(app.ProgressChecker)
    obj.data = {"goals": {"today": list(today)}, "current_task": "", "parallel_task": "", "current_message": ""}
    obj._break_mode = True
    obj._timer = _FakeTimer()
    obj.saved = 0
    obj._save = lambda: setattr(obj, "saved", obj.saved + 1)
    obj._refresh_ui = lambda: None
    obj._reset_timer = lambda override_minutes=None: None
    return obj


class ActivitySessionTaskTests(unittest.TestCase):
    def test_begin_adds_task_once_and_finish_checks_it(self):
        obj = _bare_app([{"text": "論文", "done": False, "level": 0}])
        obj._begin_activity_session("9月14日のKPT")
        obj._begin_activity_session("9月14日のKPT")  # 2回呼んでも増えない
        texts = [t["text"] for t in obj.data["goals"]["today"]]
        self.assertEqual(texts, ["論文", "9月14日のKPT"])
        self.assertFalse(obj._break_mode)
        obj._finish_activity_session("9月14日のKPT")
        self.assertTrue(obj.data["goals"]["today"][1]["done"])
        self.assertFalse(obj.data["goals"]["today"][0]["done"])

    def test_finish_without_task_is_noop(self):
        obj = _bare_app([])
        obj._finish_activity_session("無い")
        self.assertEqual(obj.saved, 0)

    def test_week_review_task_name(self):
        obj = _bare_app([])
        self.assertEqual(obj._week_review_task_name("2026-09-07"), "9/7〜9/13週の振り返り（週次KPT）")
        self.assertEqual(obj._week_review_task_name("bad"), "先週の振り返り（週次KPT）")

    def test_go_idle_clears_break_and_session(self):
        obj = _bare_app([])
        obj.data["current_task"] = "何か"
        obj._go_idle()
        self.assertFalse(obj._break_mode)
        self.assertEqual(obj.data["current_task"], "")
        self.assertTrue(obj._timer.stopped)
        self.assertGreater(obj._next_checkin_at, app.datetime.now() + app.timedelta(days=300))


class FocusFileTests(unittest.TestCase):
    MD = (
        "# いま集中すること\n\n前書き\n\n## いまの集中\n\n"
        "- **今週の1つ**：感性工学会 [x](a.md) を出す\n- **決めごと**：日曜21:00\n\n"
        "## ゲートを通さなくていいもの\n\n- **健康**：体調\n\n## 更新履歴\n\n- 2026-09-13 初版\n"
    )

    def test_body_and_get(self):
        body = app._focus_body(self.MD.split("\n"))
        self.assertEqual(body, ["- **今週の1つ**：感性工学会 [x](a.md) を出す", "- **決めごと**：日曜21:00"])
        self.assertEqual(app._strip_md_links(app._focus_get(body, "今週の1つ")), "感性工学会 x を出す")
        self.assertEqual(app._focus_get(body, "ない"), "")

    def test_set_replaces_or_prepends(self):
        body = app._focus_body(self.MD.split("\n"))
        b2 = app._focus_set(body, "今週の1つ", "新しい目標")
        self.assertEqual(b2[0], "- **今週の1つ**：新しい目標")
        self.assertEqual(len(b2), 2)
        b3 = app._focus_set(body, "締切", "10/20")
        self.assertEqual(b3[0], "- **締切**：10/20")
        self.assertEqual(len(b3), 3)

    def test_replace_keeps_other_sections_and_logs(self):
        body = ["- **今週の1つ**：A"]
        out = app._focus_replace(self.MD, body, note="てくてくから更新", today="2026-10-17")
        self.assertIn("## いまの集中\n\n- **今週の1つ**：A\n\n## ゲートを通さなくていいもの", out)
        self.assertIn("## 更新履歴\n\n- 2026-10-17 てくてくから更新\n- 2026-09-13 初版", out)
        self.assertTrue(out.startswith("# いま集中すること\n\n前書き"))

    def test_replace_without_section_is_noop(self):
        self.assertEqual(app._focus_replace("# x\n\n## 別\n", ["- a"]), "# x\n\n## 別\n")


class InboxMergeTests(unittest.TestCase):
    def test_merge_creates_parent_and_children(self):
        items = [{"text": "既存", "done": True, "level": 0}]
        out, n = app._merge_inbox(items, "🤖 提案", ["a（5分）", {"text": "b（8分）"}, "a（5分）", ""])
        self.assertEqual(n, 2)
        self.assertEqual([(t["text"], t["level"]) for t in out],
                         [("既存", 0), ("🤖 提案", 0), ("a（5分）", 1), ("b（8分）", 1)])

    def test_merge_appends_under_existing_parent_and_skips_dupes(self):
        items = [{"text": "🤖 提案", "done": True, "level": 0}, {"text": "a", "done": True, "level": 1},
                 {"text": "次", "done": False, "level": 0}]
        out, n = app._merge_inbox(items, "🤖 提案", ["a", "c"])
        self.assertEqual(n, 1)
        self.assertEqual([t["text"] for t in out], ["🤖 提案", "a", "c", "次"])
        self.assertFalse(out[0]["done"])  # 親は未完了に戻る

    def test_merge_without_parent_adds_top_level(self):
        out, n = app._merge_inbox([], "", ["x"])
        self.assertEqual(n, 1)
        self.assertEqual(out, [{"text": "x", "done": False, "level": 0}])

    def test_read_inbox_rejects_garbage(self):
        import tempfile, os
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            f.write("{nope")
        try:
            self.assertIsNone(app._read_inbox(f.name))
        finally:
            os.remove(f.name)


class FocusItemsTests(unittest.TestCase):
    def test_items_and_others_roundtrip(self):
        body = ["- **今週の1つ**：A", "※ 補足", "- **決めごと**：B", "🤖 メモ"]
        items, others = app._focus_items(body)
        self.assertEqual(items, [("今週の1つ", "A"), ("決めごと", "B")])
        self.assertEqual(others, ["※ 補足", "🤖 メモ"])
        self.assertEqual(app._focus_body_from_items(items + [("", ""), ("新", "C")], others),
                         ["- **今週の1つ**：A", "- **決めごと**：B", "- **新**：C", "※ 補足", "🤖 メモ"])


class ReportAndPlanTests(unittest.TestCase):
    def test_daily_report_format(self):
        kpt = {"keep": ["ラボに来た", "振り返り"], "problem": ["時が流れる"], "try": [], "trouble": ["倫理の書き方"]}
        items = [{"text": "親", "done": False, "level": 0}, {"text": "子完了", "done": True, "level": 1},
                 {"text": "子未完（8分）", "done": False, "level": 1}]
        txt = app._daily_report_text("10/17", kpt, items)
        self.assertEqual(txt, "K　ラボに来た・振り返り\nP　時が流れる\nT　（なし）\n\n今日やったこと\n・子完了\n次やること\n・子未完（8分）\n困っていること\n・倫理の書き方\n")

    def test_plan_text_and_normalize(self):
        plan = app._normalize_plan({"goal": "通す", "outputs": ["原稿", "- 図"], "this_week": "a\nb", "review": "10/20"})
        self.assertEqual(plan["outputs"], "原稿\n- 図")
        txt = app._plan_text(plan)
        self.assertTrue(txt.startswith(":dart:目的：　通す\n"))
        self.assertIn(":spiral_note_pad:アウトプット：\n- 原稿\n- 図\n", txt)
        self.assertIn("今週\n- a\n- b\n", txt)
        self.assertIn("来週\n- （未記入）", txt)
        self.assertTrue(txt.rstrip().endswith("レビューがほしいタイミング：10/20"))
        self.assertTrue(app._plan_is_empty(app._normalize_plan({})))
        self.assertIn("## 🎯 目的\n\n通す", app._plan_md(plan, "2026-10-17 23:00"))

    def test_inbox_with_plan_only(self):
        import tempfile, os, json
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump({"plan": {"goal": "x"}}, f)
        try:
            box = app._read_inbox(f.name)
            self.assertEqual(box["tasks"], [])
            self.assertEqual(box["plan"]["goal"], "x")
        finally:
            os.remove(f.name)
