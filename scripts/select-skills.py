#!/usr/bin/env -S uv run --locked --script
# /// script
# requires-python = ">=3.11,<3.15"
# dependencies = ["textual==8.2.8", "pyyaml==6.0.3"]
# ///
"""Browse the pinned catalog and edit the selection without installing it."""

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
from textual.screen import ModalScreen
from textual.widgets import Markdown, Static
from textual.widgets.markdown import MarkdownFence

import reconcile_skills
import skill_catalog

ACCENT = "#D77757"
SECONDARY = "#999999"
NOT_IN_CATALOG = "Not in catalog"


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


def display_catalog(root: Path, catalog: dict[str, skill_catalog.SourceCatalog]) -> dict[str, list[Skill]]:
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
    key: str | int | None
    lines: list[Text]


class RowsView(Static):
    DEFAULT_CSS = "RowsView { width: 100%; text-wrap: nowrap; text-overflow: ellipsis; }"

    def on_click(self, event: Click) -> None:
        if isinstance(self.parent, CatalogList):
            self.parent.clicked(event.x, event.y)
            event.stop()


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

    class Activated(Highlighted):
        pass

    def __init__(self, *, circle_toggle: bool = False, activate_on_click: bool = False, **kwargs) -> None:
        super().__init__(**kwargs)
        self.circle_toggle = circle_toggle
        self.activate_on_click = activate_on_click
        self.rows: list[Row] = []
        self.cursor: int | None = None

    def compose(self) -> ComposeResult:
        yield RowsView()

    @property
    def current(self) -> str | int | None:
        return self.rows[self.cursor].key if self.cursor is not None else None

    def set_rows(self, rows: list[Row], *, keep_cursor: bool = False) -> None:
        current = self.current if keep_cursor else None
        self.rows = rows
        self.cursor = next((i for i, row in enumerate(rows) if row.key == current and current is not None),
                           next((i for i, row in enumerate(rows) if row.key is not None), None))
        if not keep_cursor:
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

    def clicked(self, x: int, y: int) -> None:
        line = 0
        for index, row in enumerate(self.rows):
            if line <= y < line + len(row.lines):
                if row.key is not None:
                    self.focus()
                    self.highlight(index)
                    if self.activate_on_click or (self.circle_toggle and x == 2 and y == line):
                        self.post_message(self.Activated(self))
                return
            line += len(row.lines)

    def on_focus(self) -> None:
        self.redraw()

    def on_blur(self) -> None:
        self.redraw()


class ChoiceDialog(ModalScreen[int | None]):
    """The same numbered, cancellable choices for replacement, saving and quitting."""

    DEFAULT_CSS = """
    ChoiceDialog { align: center bottom; background: transparent; }
    #dialog { height: auto; max-height: 85%; margin: 0 2 2 2; padding: 1 2; border: round #D77757; }
    #dialog-title { height: auto; color: #D77757; margin-bottom: 1; }
    #dialog-details { height: 1fr; max-height: 12; overflow-y: auto; margin-bottom: 1; }
    #choices { height: auto; max-height: 6; }
    #dialog-hint { height: 1; margin-top: 1; color: #999999; }
    """
    BINDINGS = [
        Binding("enter", "confirm", show=False),
        Binding("escape", "cancel", show=False),
        Binding("1", "choose(1)", show=False),
        Binding("2", "choose(2)", show=False),
        Binding("3", "choose(3)", show=False),
    ]

    def __init__(self, title: str, choices: list[str], details: str = "") -> None:
        super().__init__()
        self.heading = title
        self.choices = choices
        self.details = details

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static(Text(self.heading), id="dialog-title")
            yield Static(Text(self.details), id="dialog-details")
            yield CatalogList(activate_on_click=True, id="choices")
            yield Static("Enter to confirm · Esc to cancel", id="dialog-hint")

    def on_mount(self) -> None:
        choices = self.query_one("#choices", CatalogList)
        choices.set_rows([Row(number, [Text(f"{number}. {label}")])
                          for number, label in enumerate(self.choices, 1)])
        choices.focus()

    def action_choose(self, number: int) -> None:
        if 1 <= number <= len(self.choices):
            self.dismiss(number)

    @on(CatalogList.Activated, "#choices")
    def action_confirm(self) -> None:
        self.dismiss(self.query_one("#choices", CatalogList).current)

    def action_cancel(self) -> None:
        self.dismiss(None)


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
    #skill-body, #skill-body * {
        color: ansi_default;
        link-color: ansi_default;
        link-color-hover: ansi_default;
        link-background-hover: transparent;
    }
    #skill-body MarkdownBlockQuote { background: transparent; }
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
        Binding("enter", "enter", show=False),
        Binding("space", "toggle", show=False),
        Binding("ctrl+s", "save", show=False),
        Binding("escape", "quit", show=False),
    ]

    def __init__(self, root: Path | None = None) -> None:
        super().__init__()
        self.root = (root or Path(__file__).resolve().parent.parent).resolve()
        # Browsing needs source validation, not the reconciler's deployment gate.
        modules = reconcile_skills.parse_modules(self.root)
        reconcile_skills.validate_sources(self.root, modules)
        catalog = skill_catalog.discover_catalog(self.root, modules)
        self.catalog = display_catalog(self.root, catalog)
        self.selected: dict[str | int, str] = {}
        self.off_catalog: dict[int, skill_catalog.SelectionLine] = {}
        for line in skill_catalog.read_selection_lines(self.root):
            error = line.error or skill_catalog.selection_error(line.value, catalog)
            if error:
                self.off_catalog[line.number] = skill_catalog.SelectionLine(line.value, line.number, error, line.name)
                self.selected[line.number] = line.value
            else:
                self.selected[line.value] = line.value
        self.loaded = Counter(self.selected.values())
        self.committed = Counter(line.value for line in skill_catalog.parse_selection(
            skill_catalog.git(["show", "HEAD:skills.txt"], cwd=self.root),
        ))
        self.source: str | None = NOT_IN_CATALOG if self.off_catalog else next(iter(self.catalog), None)
        self.theme = "textual-dark"
        self.ansi_color = True

    def compose(self) -> ComposeResult:
        yield Static(id="title")
        with Horizontal(id="columns"):
            with Vertical(id="source-column", classes="column"):
                yield Static("Sources", classes="heading")
                yield CatalogList(id="sources")
            with Vertical(id="skill-column", classes="column"):
                yield Static("Skills", id="skill-heading", classes="heading")
                yield CatalogList(circle_toggle=True, id="skills")
            with VerticalScroll(id="details", classes="column"):
                yield Static(id="skill-metadata")
                yield SkillMarkdown(id="skill-body")
        yield Static(id="message")
        yield Static(id="hints")

    async def on_mount(self) -> None:
        self.refresh_title()
        self.fill_sources()
        await self.fill_skills()
        self.query_one("#sources", CatalogList).focus()

    @property
    def unsaved_count(self) -> int:
        current = Counter(self.selected.values())
        return (current - self.loaded).total() + (self.loaded - current).total()

    def refresh_title(self) -> None:
        self.query_one("#title", Static).update(Text.assemble(
            ("✻ ", ACCENT), ("Skill selector", "bold"),
            (f"   {len(self.selected)} selected · {self.unsaved_count} unsaved", SECONDARY),
        ))

    def fill_sources(self, *, keep_cursor: bool = False) -> None:
        rows = []
        if self.off_catalog:
            selected = sum(key in self.selected for key in self.off_catalog)
            rows.append(Row(NOT_IN_CATALOG, [Text(NOT_IN_CATALOG),
                                             Text(f"{selected}/{len(self.off_catalog)}", style=SECONDARY)]))
        for source, skills in self.catalog.items():
            owner, repo = source.split("/")
            selected = sum(f"{source}:{skill.name}" in self.selected for skill in skills)
            rows.append(Row(source, [Text(repo), Text(f"{owner} · {selected}/{len(skills)}", style=SECONDARY)]))
        sources = self.query_one("#sources", CatalogList)
        sources.set_rows(rows, keep_cursor=keep_cursor)

    async def fill_skills(self, *, keep_cursor: bool = False) -> None:
        rows = []
        if self.source == NOT_IN_CATALOG:
            for key, line in self.off_catalog.items():
                text = Text("● " if key in self.selected else "○ ", style=ACCENT if key in self.selected else SECONDARY)
                text.append(line.value, style="default")
                rows.append(Row(key, [text, Text(line.error.replace("\n", " "), style=SECONDARY)]))
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
        self.query_one("#skills", CatalogList).set_rows(rows, keep_cursor=keep_cursor)
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
        if key in self.off_catalog:
            line = self.off_catalog[key]
            metadata.update(Text("\n".join([
                line.value, "Selected" if key in self.selected else "Not selected",
                f"skills.txt:{line.number}: {line.error}", "Can be deselected only.",
            ])))
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

    async def refresh_selection(self) -> None:
        self.refresh_title()
        self.fill_sources(keep_cursor=True)
        await self.fill_skills(keep_cursor=True)

    async def action_enter(self) -> None:
        if self.focused is self.query_one("#skills", CatalogList):
            await self.action_toggle()
        else:
            self.action_column("skills")

    @on(CatalogList.Activated, "#skills")
    async def action_toggle(self) -> None:
        key = self.query_one("#skills", CatalogList).current
        if key is None:
            return
        if key in self.selected:
            del self.selected[key]
        elif key in self.off_catalog:
            self.query_one("#message", Static).update(Text(
                "⎿ Cannot select: " + self.off_catalog[key].error,
            ))
            return
        else:
            source, name = key.split(":")
            other_key = next((selected_key for selected_key, value in self.selected.items()
                              if value != key and value.partition(":")[2] == name), None)
            if other_key is not None:
                async def replace(choice: int | None) -> None:
                    if choice == 1:
                        del self.selected[other_key]
                        self.selected[key] = key
                        await self.refresh_selection()

                self.push_screen(ChoiceDialog(f"Replace {name}?", [
                    f"Yes, use {source}", f"No, keep {self.selected[other_key].split(':')[0]}",
                ]), replace)
                return
            self.selected[key] = key
        await self.refresh_selection()

    def action_save(self) -> None:
        current = Counter(self.selected.values())
        names = Counter(line.name
                        for line in skill_catalog.parse_selection("\n".join(self.selected.values()))
                        if line.name is not None)
        duplicates = sorted(name for name, count in names.items() if count > 1)
        if duplicates:
            self.query_one("#message", Static).update(Text(
                "⎿ Cannot save: same name selected more than once: " + ", ".join(duplicates),
            ))
            return
        if current == self.committed and not self.unsaved_count:
            self.query_one("#message", Static).update("⎿ No changes to save.")
            return
        details = [f"+ {value}" for value in sorted((current - self.committed).elements())]
        details.extend(f"- {value}" for value in sorted((self.committed - current).elements()))
        self.push_screen(ChoiceDialog("Save these changes?", ["Save only", "Keep editing"],
                                      "\n".join(details) or "No changes from the last commit."),
                         self.save_choice)

    def save_choice(self, choice: int | None) -> None:
        if choice != 1:
            return
        try:
            skill_catalog.write_selection(self.root, self.selected.values())
        except OSError as error:
            self.query_one("#message", Static).update(Text(f"⎿ Could not save: {error}"))
            return
        self.loaded = Counter(self.selected.values())
        self.refresh_title()
        self.query_one("#message", Static).update("⎿ Saved. Not installed yet.")

    def action_quit(self) -> None:
        if not self.unsaved_count:
            self.exit()
            return
        self.push_screen(ChoiceDialog("Quit without saving?", [
            "Keep editing", "Review and save", "Quit without saving",
        ]), self.quit_choice)

    def quit_choice(self, choice: int | None) -> None:
        if choice == 2:
            self.action_save()
        elif choice == 3:
            self.exit()

    def action_column(self, column: str) -> None:
        self.query_one(f"#{column}", CatalogList).focus()

    def on_descendant_focus(self) -> None:
        if self.focused is self.query_one("#details"):
            hint = "↑↓ scroll · ← sources · → skills · ctrl+s save · esc quit"
        elif self.focused is self.query_one("#skills"):
            hint = "↑↓ move · space or enter toggle · ← sources · tab details · ctrl+s save · esc quit"
        else:
            hint = "↑↓ move · → or enter skills · space toggle · ctrl+s save · esc quit"
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
