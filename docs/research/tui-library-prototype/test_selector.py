# /// script
# requires-python = ">=3.10"
# dependencies = ["textual==8.2.8"]
# ///
"""Headless Pilot test using only stdlib unittest (no pytest)."""

import unittest

from selector import Selector, SkillTree
from textual.widgets import Static


class SelectorTest(unittest.IsolatedAsyncioTestCase):
    async def test_toggle_filter_detail_and_mouse(self) -> None:
        app = Selector()
        async with app.run_test(size=(100, 30)) as pilot:
            tree = app.query_one(SkillTree)
            await pilot.press("down", "right", "down")  # open source-0, highlight first skill
            self.assertEqual(tree.cursor_node.data, "source-0/skill-0-0")
            self.assertIn("skill 0-0", str(app.query_one("#detail", Static).render()))
            await pilot.press("space")
            self.assertEqual(app.selected, {"source-0/skill-0-0"})
            await pilot.click(SkillTree, offset=(6, 2))  # mouse click on second skill row
            self.assertEqual(tree.cursor_node.data, "source-0/skill-0-1")
            app.query_one("#filter").focus()
            await pilot.press(*"3-1")
            await pilot.pause()
            visible = [n.data for n in tree.root.children[0].children]
            self.assertTrue(all("3-1" in d for d in visible), visible)
            app.set_focus(tree)
            await pilot.press("ctrl+s")
        self.assertEqual(app.return_value, ["source-0/skill-0-0"])


if __name__ == "__main__":
    unittest.main()
