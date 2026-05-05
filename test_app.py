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
            any(path.endswith("/white-star.png") for path in app.ICON_CANDIDATES)
        )

    def test_truncate10_behavior(self):
        self.assertEqual(app._truncate10("1234567890"), "1234567890")
        self.assertEqual(app._truncate10("12345678901"), "1234567890…")


if __name__ == "__main__":
    unittest.main()
