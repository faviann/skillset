#!/usr/bin/env -S uv run --locked --script
# /// script
# requires-python = ">=3.11,<3.15"
# dependencies = ["textual==8.2.8", "pyyaml==6.0.3"]
# ///
"""Browse the pinned catalog and the selection without changing either."""

from __future__ import annotations

import os
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

# A symlink in PATH must still import from the checkout containing this script.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import yaml
from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.content import Content
from textual.events import Click
from textual.geometry import Region
from textual.message import Message
from textual.widgets import Markdown, Static
from textual.widgets.markdown import MarkdownFence

import reconcile_skills
import skill_catalog

ACCENT = "#D77757"
SECONDARY = "#999999"


@dataclass
class Skill:
    name: str
    path: Path
    description: str = ""
    group: str = ""
    manual_only: bool = False
    unreadable: bool = False
    body: str = ""
    file_count: int = 0


def display_skill(name: str, path: str, common_root: Path) -> Skill:
    """Read display metadata only; the catalog has already validated identity."""
    skill = Skill(name, Path(path))
    parent = skill.path.parent.relative_to(common_root)
    skill.group = parent.parts[0] if parent.parts else ""
    try:
        lines = (skill.path / "SKILL.md").read_text(encoding="utf-8").splitlines()
        end = lines.index("---", 1)
        skill.body = "\n".join(lines[end + 1:])
        metadata = yaml.safe_load("\n".join(lines[1:end]))
        description = metadata.get("description", "")
        category = metadata.get("category", "")
        skill.description = description if isinstance(description, str) else ""
        if isinstance(category, str) and category.strip():
            skill.group = " ".join(category.split())
        skill.manual_only = metadata.get("disable-model-invocation") is True
    except (OSError, UnicodeError, ValueError, yaml.YAMLError):
        skill.unreadable = True
    return skill


def load_catalog(root: Path) -> dict[str, list[Skill]]:
    modules = reconcile_skills.parse_modules(root)
    reconcile_skills.validate_sources(root, modules)
    catalog = skill_catalog.discover_catalog(root, modules)
    result = {}
    for module, source in catalog.items():
        paths = list(source.skills.values())
        common_root = Path(os.path.commonpath([Path(path).parent for path in paths])) if paths else root / module
        skills = [display_skill(name, path, common_root) for name, path in source.skills.items()]
        tracked = [root / module / path for path in
                   skill_catalog.git(["ls-files", "-z"], cwd=root / module).split("\0") if path]
        groups = Counter(skill.group for skill in skills)
        for skill in skills:
            skill.file_count = sum(path.is_relative_to(skill.path) for path in tracked)
            if groups[skill.group] == 1:
                skill.group = ""
        result[module.removeprefix("sources/")] = sorted(
            skills, key=lambda skill: (skill.group.casefold(), skill.group, skill.name),
        )
    return dict(sorted(result.items(), key=lambda item: (item[0].casefold(), item[0])))


@dataclass
class Row:
    key: str | None
    lines: list[Text]


class RowsView(Static):
    DEFAULT_CSS = "RowsView { width: 100%; text-wrap: nowrap; text-overflow: ellipsis; }"

    def on_click(self, event: Click) -> None:
        if isinstance(self.parent, CatalogList):
            self.parent.clicked(event.y)


class CatalogList(VerticalScroll, can_focus=True):
    """Text rows with non-navigable group headings and a focus-aware pointer."""

    BINDINGS = [Binding("up", "move(-1)", show=False), Binding("down", "move(1)", show=False)]

    class Highlighted(Message):
        def __init__(self, control: CatalogList) -> None:
            super().__init__()
            self.catalog_list = control

        @property
        def control(self) -> CatalogList:
            return self.catalog_list

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.rows: list[Row] = []
        self.cursor: int | None = None

    def compose(self) -> ComposeResult:
        yield RowsView()

    @property
    def current(self) -> str | None:
        return self.rows[self.cursor].key if self.cursor is not None else None

    def set_rows(self, rows: list[Row]) -> None:
        self.rows = rows
        self.cursor = next((i for i, row in enumerate(rows) if row.key is not None), None)
        self.scroll_home(animate=False)
        self.redraw()

    def redraw(self) -> None:
        lines = []
        for i, row in enumerate(self.rows):
            for number, line in enumerate(row.lines):
                pointer = Text("  ")
                if row.key is not None and i == self.cursor and number == 0:
                    pointer = Text("❯ ", style=f"bold {ACCENT}" if self.has_focus else SECONDARY)
                lines.append(pointer + line)
        self.query_one(RowsView).update(Text("\n").join(lines))

    def highlight(self, index: int) -> None:
        self.cursor = index
        self.redraw()
        y = sum(len(row.lines) for row in self.rows[:index])
        self.scroll_to_region(Region(0, y, 1, len(self.rows[index].lines)), animate=False)
        self.post_message(self.Highlighted(self))

    def action_move(self, delta: int) -> None:
        items = [i for i, row in enumerate(self.rows) if row.key is not None]
        if self.cursor is not None:
            index = max(0, min(len(items) - 1, items.index(self.cursor) + delta))
            self.highlight(items[index])

    def clicked(self, y: int) -> None:
        line = 0
        for index, row in enumerate(self.rows):
            if line <= y < line + len(row.lines):
                if row.key is not None:
                    self.focus()
                    self.highlight(index)
                return
            line += len(row.lines)

    def on_focus(self) -> None:
        self.redraw()

    def on_blur(self) -> None:
        self.redraw()


class SkillCodeBlock(MarkdownFence):
    @classmethod
    def highlight(cls, code: str, language: str, ansi: bool = False, dark: bool = False) -> Content:
        # Textual's highlighter embeds a theme foreground that overrides CSS.
        return Content(code)


class SkillMarkdown(Markdown):
    BLOCKS = {**Markdown.BLOCKS, "fence": SkillCodeBlock, "code_block": SkillCodeBlock}


class SelectorApp(App):
    TITLE = "Skill selector"
    ENABLE_COMMAND_PALETTE = False
    CSS = """
    Screen { background: ansi_default; color: ansi_default; }
    #title { height: 1; margin: 1 2; }
    #columns { margin: 0 1 1 1; }
    .column { padding: 0 1; }
    #source-column { width: 30; }
    #skill-column { width: 1fr; }
    #details { width: 1.3fr; }
    #skill-metadata { margin-bottom: 1; }
    #skill-body { padding: 0; }
    #skill-body, #skill-body * { color: ansi_default; }
    #skill-body MarkdownBlock > .code_inline { color: ansi_default; }
    .heading { height: 1; color: #999999; margin-bottom: 1; }
    CatalogList {
        background: transparent;
        scrollbar-size-vertical: 1;
        scrollbar-background: transparent;
        scrollbar-color: #999999;
        scrollbar-color-active: #D77757;
    }
    CatalogList:focus { background-tint: transparent; }
    #title, #message, #hints, .heading { text-wrap: nowrap; text-overflow: ellipsis; }
    #message, #hints { height: 1; padding: 0 2; color: #999999; }
    #hints { margin-bottom: 1; }
    """
    BINDINGS = [
        Binding("left", "column('sources')", show=False),
        Binding("right", "column('skills')", show=False),
        Binding("enter", "column('skills')", show=False),
        Binding("escape", "quit", show=False),
    ]

    def __init__(self, root: Path | None = None) -> None:
        super().__init__()
        self.root = (root or Path(__file__).resolve().parent.parent).resolve()
        # Browsing needs source validation, not the reconciler's deployment gate.
        self.catalog = load_catalog(self.root)
        self.selected = frozenset(skill_catalog.read_selection(self.root))
        self.source: str | None = next(iter(self.catalog), None)
        self.theme = "textual-dark"
        self.ansi_color = True

    def compose(self) -> ComposeResult:
        yield Static(Text.assemble(("✻ ", ACCENT), ("Skill selector", "bold"),
                                   (f"   {len(self.selected)} selected", SECONDARY)), id="title")
        with Horizontal(id="columns"):
            with Vertical(id="source-column", classes="column"):
                yield Static("Sources", classes="heading")
                yield CatalogList(id="sources")
            with Vertical(id="skill-column", classes="column"):
                yield Static("Skills", id="skill-heading", classes="heading")
                yield CatalogList(id="skills")
            with VerticalScroll(id="details", classes="column"):
                yield Static(id="skill-metadata")
                yield SkillMarkdown(id="skill-body")
        yield Static("⎿ Browse the catalog. Selection is read-only.", id="message")
        yield Static(id="hints")

    async def on_mount(self) -> None:
        rows = []
        for source, skills in self.catalog.items():
            owner, repo = source.split("/")
            selected = sum(f"{source}:{skill.name}" in self.selected for skill in skills)
            rows.append(Row(source, [Text(repo), Text(f"{owner} · {selected}/{len(skills)}", style=SECONDARY)]))
        sources = self.query_one("#sources", CatalogList)
        sources.set_rows(rows)
        await self.fill_skills()
        sources.focus()

    async def fill_skills(self) -> None:
        rows = []
        group = ""
        for skill in self.catalog.get(self.source, []):
            if skill.group != group:
                group = skill.group
                rows.append(Row(None, [Text(group, style=SECONDARY)]))
            key = f"{self.source}:{skill.name}"
            selected = key in self.selected
            line = Text("● " if selected else "○ ", style=ACCENT if selected else SECONDARY)
            line.append(skill.name, style="default")
            if skill.manual_only:
                line.append("  manual", style=SECONDARY)
            rows.append(Row(key, [line]))
        if not rows:
            rows.append(Row(None, [Text("No skills in this source.", style=SECONDARY)]))
        self.query_one("#skills", CatalogList).set_rows(rows)
        self.query_one("#skill-heading", Static).update(self.source or "Skills")
        await self.refresh_details()

    async def refresh_details(self) -> None:
        key = self.query_one("#skills", CatalogList).current
        metadata = self.query_one("#skill-metadata", Static)
        body = self.query_one("#skill-body", Markdown)
        self.query_one("#details", VerticalScroll).scroll_home(animate=False)
        if key is None:
            metadata.update("")
            await body.update("")
            return
        source, name = key.split(":")
        skill = next(skill for skill in self.catalog[source] if skill.name == name)
        lines = [skill.name, key, "Selected" if key in self.selected else "Not selected"]
        if skill.manual_only:
            lines.append("Manual only: description not loaded into context")
        others = [f"{other}:{name}" for other, skills in self.catalog.items()
                  if other != source and any(skill.name == name for skill in skills)]
        if others:
            lines.append("Same name in other sources: " + ", ".join(others))
        lines.extend([
            "description unreadable" if skill.unreadable else skill.description,
            str(skill.path),
            f"{skill.file_count} tracked {'file' if skill.file_count == 1 else 'files'}",
        ])
        metadata.update(Text("\n".join(lines)))
        await body.update(skill.body)

    @on(CatalogList.Highlighted, "#sources")
    async def source_highlighted(self, event: CatalogList.Highlighted) -> None:
        if event.control.current != self.source:
            self.source = event.control.current
            await self.fill_skills()

    @on(CatalogList.Highlighted, "#skills")
    async def skill_highlighted(self) -> None:
        await self.refresh_details()

    def action_column(self, column: str) -> None:
        self.query_one(f"#{column}", CatalogList).focus()

    def on_descendant_focus(self) -> None:
        if self.focused is self.query_one("#details"):
            hint = "↑↓ scroll · ← sources · → skills · esc quit"
        elif self.focused is self.query_one("#skills"):
            hint = "↑↓ move · ← sources · tab details · esc quit"
        else:
            hint = "↑↓ move · → or enter skills · esc quit"
        self.query_one("#hints", Static).update(hint)


def main() -> int:
    try:
        app = SelectorApp()
    except (reconcile_skills.ReconcileError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    app.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
