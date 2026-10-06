"""Load the selector script and drive it through Textual's pilot."""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import unittest
from xml.etree import ElementTree

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

    async def settle(self, pilot) -> None:
        """Return once every queued message is handled, no body render is pending, and the screen is repainted.

        A highlight reaches the app through a chain of messages, and the body then
        renders in a delayed worker, so neither one pause nor one worker wait suffices.
        """
        app = pilot.app
        while True:
            await pilot.pause()
            rendering = [worker.wait() for worker in app.workers if worker.group == "body" and not worker.is_finished]
            if rendering:
                await asyncio.gather(*rendering, return_exceptions=True)
            elif not any(node.message_queue_size for node in (app, *app.screen.walk_children(with_self=True))):
                return

    def shown_body(self, app) -> str:
        """The body text on display now, without waiting; empty while it renders."""
        content = app.screen.query_one("#skill-body", selector.Static).content
        return "".join(segment.text for segment in getattr(content, "segments", []))

    async def body_text(self, pilot) -> str:
        """The rendered body of the current skill."""
        await self.settle(pilot)
        return self.shown_body(pilot.app)

    async def screen_text(self, pilot) -> str:
        """Every character on screen, once pending renders have painted."""
        await self.settle(pilot)
        return "".join(ElementTree.fromstring(pilot.app.export_screenshot()).itertext())
