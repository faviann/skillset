"""Load the selector script and drive it through Textual's pilot."""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import unittest

from fixture import CHECKOUT

spec = importlib.util.spec_from_file_location("select_skills", CHECKOUT / "scripts/select-skills.py")
selector = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = selector
spec.loader.exec_module(selector)


class PilotTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        # IsolatedAsyncioTestCase forces asyncio debug mode, which makes every
        # Textual task several times slower. Never-awaited warnings still show.
        asyncio.get_running_loop().set_debug(False)

    def text(self, app, query: str) -> str:
        content = app.screen.query_one(query, selector.Static).content
        return content.plain if isinstance(content, selector.Text) else str(content)

    def details(self, app) -> str:
        """The details header as one block: name, selection state, then metadata."""
        return "\n".join(self.text(app, part) for part in ("#skill-name", "#skill-state", "#skill-metadata", "#skill-foot"))

    def lines(self, app, column: str) -> list[str]:
        return [line.removeprefix("❯ ").strip() for line in self.text(app, f"#{column} RowsView").splitlines()]

    def body_text(self, app) -> str:
        return "\n".join(str(widget.content) for widget in app.query("#skill-body Static"))
