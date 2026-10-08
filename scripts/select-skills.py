#!/usr/bin/env -S uv run --locked --script
# /// script
# requires-python = ">=3.11,<3.15"
# dependencies = ["textual==8.2.8", "pyyaml==6.0.3"]
# ///
"""Browse the pinned catalog, save a selection, and optionally install it."""

from __future__ import annotations

import io
import os
import subprocess
import sys
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from pathlib import Path

# A symlink in PATH must still import from the checkout containing this script.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import yaml
from rich import markdown as rich_markdown
from rich.cells import cell_len
from rich.console import Console
from rich.padding import Padding
from rich.rule import Rule
from rich.segment import Segment, Segments
from rich.spinner import Spinner
from rich.text import Text
from rich.theme import Theme
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.events import Click, Key
from textual.geometry import Region
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Input, Static
from textual.worker import get_current_worker

import reconcile_skills
import skill_catalog
from selection_draft import Blocked, Changes, CheckoutStore, Conflict, Refused, Replace, SelectionDraft

ACCENT = "#D77757"
SECONDARY = "#999999"
# ANSI names, so the terminal theme keeps them readable on light and dark.
MANUAL = "red"
AUTOMATIC = "green"
NOT_IN_CATALOG = "Not in catalog"
LOOKUP_TIMEOUT_SECONDS = 5


@dataclass
class Skill:
    name: str
    path: Path
    description: str = ""
    group: str = ""
    manual_only: bool = False
    agent_only: bool = False
    unreadable: bool = False
    body: str = ""
    file_count: int = 0
    forked: bool = False


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
        skill.agent_only = metadata.get("user-invocable") is False
    except (OSError, UnicodeError, ValueError, yaml.YAMLError):
        skill.unreadable = True
    return skill


def display_catalog(catalog: skill_catalog.Catalog) -> dict[str, list[Skill]]:
    result = {}
    for source_name, source in catalog.sources.items():
        paths = list(source.skills.values())
        common_root = Path(os.path.commonpath([Path(path).parent for path in paths])) if paths else source.path
        skills = [display_skill(name, path, common_root) for name, path in source.skills.items()]
        files = skill_catalog.path_prefixes(source.path / path for path in source.tracked)
        changed: Counter[Path] = Counter()
        if source.upstream:
            # A missing upstream object (a shallow clone) fails the diff and marks nothing.
            diff = skill_catalog.git(["diff", "--name-only", "-z", "--no-renames", source.upstream, "HEAD"],
                                     cwd=source.path, check=False)
            changed = skill_catalog.path_prefixes(source.path / path for path in diff.split("\0") if path)
        groups = Counter(skill.group for skill in skills)
        for skill in skills:
            skill.file_count = files[skill.path]
            skill.forked = changed[skill.path] > 0
            if groups[skill.group] == 1:
                skill.group = ""
        result[source_name] = sorted(
            skills, key=lambda skill: (skill.group.casefold(), skill.group, skill.name),
        )
    return dict(sorted(result.items(), key=lambda item: (item[0].casefold(), item[0])))


def branch_tip(root: Path, url: str, branch: str) -> str | None:
    ref = f"refs/heads/{branch}"
    try:
        # A 401 runs credential helpers and askpass, which GIT_TERMINAL_PROMPT does not stop.
        # ssh prompts on /dev/tty, so the lookup runs in a new session with no terminal. With no
        # terminal, ssh falls back to a GUI askpass unless SSH_ASKPASS_REQUIRE is never.
        result = subprocess.run(
            ["git", "-c", "credential.helper=", "-c", "core.askPass=", "ls-remote", "--", url, ref],
            cwd=root, env={**skill_catalog.git_env(), "SSH_ASKPASS_REQUIRE": "never"}, start_new_session=True,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, errors="replace", timeout=LOOKUP_TIMEOUT_SECONDS, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    tips = {name: oid for oid, _, name in (line.partition("\t") for line in result.stdout.splitlines())}
    return tips.get(ref)


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
        self.redraw(layout=True)

    def redraw(self, *, layout: bool = False) -> None:
        # Moving the pointer keeps every row's size, so it skips a layout pass over the whole screen.
        lines = []
        for i, row in enumerate(self.rows):
            for number, line in enumerate(row.lines):
                pointer = Text("  ")
                if row.key is not None and i == self.cursor and number == 0:
                    pointer = Text("❯ ", style=f"bold {ACCENT}" if self.has_focus else SECONDARY)
                lines.append(pointer + line)
        self.query_one(RowsView).update(Text("\n").join(lines), layout=layout)

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
    #dialog-review { height: 1fr; max-height: 12; margin-bottom: 1; }
    #dialog-details { height: auto; }
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

    def __init__(self, title: str, choices: list[str], details: str = "", *,
                 disabled: dict[int, str] | None = None) -> None:
        super().__init__()
        self.heading = title
        self.choices = choices
        self.disabled = disabled or {}
        self.details = "\n\n".join([details, *(
            f"{choices[number - 1]} unavailable: {reason}" for number, reason in self.disabled.items()
        )])

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static(Text(self.heading), id="dialog-title")
            with VerticalScroll(id="dialog-review"):
                yield Static(Text(self.details), id="dialog-details")
            yield CatalogList(activate_on_click=True, id="choices")
            yield Static("Enter to confirm · Esc to cancel", id="dialog-hint")

    def on_mount(self) -> None:
        choices = self.query_one("#choices", CatalogList)
        rows = []
        for number, label in enumerate(self.choices, 1):
            reason = self.disabled.get(number)
            rows.append(Row(None if reason else number, [
                Text(f"{number}. {label}", style=SECONDARY if reason else ""),
            ]))
        choices.set_rows(rows)
        choices.focus()

    def action_choose(self, number: int) -> None:
        if 1 <= number <= len(self.choices) and number not in self.disabled:
            self.dismiss(number)

    @on(CatalogList.Activated, "#choices")
    def action_confirm(self) -> None:
        self.dismiss(self.query_one("#choices", CatalogList).current)

    def action_cancel(self) -> None:
        self.dismiss(None)


class SkillHeading(rich_markdown.Heading):
    LEVEL_ALIGN = {**rich_markdown.Heading.LEVEL_ALIGN, "h1": "left"}


class SkillCodeBlock(rich_markdown.CodeBlock):
    def __rich_console__(self, console, options):
        # Rich's syntax themes set their own colours, unreadable on light or dark terminals.
        yield Padding(Text(str(self.text).rstrip()), (0, 0, 0, 2))


class SkillRule(rich_markdown.HorizontalRule):
    def __rich_console__(self, console, options):
        yield Rule(style=SECONDARY)
        yield Text()


class SkillMarkdown(rich_markdown.Markdown):
    elements = {**rich_markdown.Markdown.elements, "heading_open": SkillHeading, "hr": SkillRule,
                "fence": SkillCodeBlock, "code_block": SkillCodeBlock}


# Terminal foreground and plain emphasis read on light and dark terminals; secondary marks are grey.
SKILL_THEME = Theme({
    "markdown.code": "none", "markdown.block_quote": "none", "markdown.link_url": "underline",
    "markdown.h1": "bold", "markdown.h2": "underline", "markdown.h3": "bold",
    "markdown.h4": "bold underline", "markdown.h5": "bold", "markdown.h6": "bold",
    "markdown.item.number": "none", "markdown.item.bullet": "none",
    "markdown.table.header": "bold", "markdown.table.border": SECONDARY,
})


def render_markdown(markdown: str, width: int) -> Segments:
    console = Console(width=width, theme=SKILL_THEME, file=io.StringIO())
    segments = []
    for line in console.render_lines(SkillMarkdown(markdown), pad=False):
        segments += [*line, Segment.line()]
    return Segments(segments)


class SkillBody(Static):
    """The skill text, rendered in a thread so keys are handled while a long one renders."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.markdown = ""
        self.current: tuple[str, int] | None = None
        self.cache: dict[tuple[str, int], Segments] = {}

    def show(self, markdown: str) -> None:
        self.markdown = markdown
        self.refresh_body()

    def on_resize(self) -> None:
        self.refresh_body()

    def refresh_body(self) -> None:
        key = (self.markdown, self.size.width)
        if key == self.current:
            return
        self.current = key
        self.workers.cancel_group(self, "body")
        if not self.markdown or not self.size.width or key in self.cache:
            self.set_content(self.cache.get(key, ""))
        else:
            self.set_content(Spinner("dots", style=SECONDARY), loading=True)
            self.run_worker(partial(self.render_in_thread, *key), thread=True, group="body")

    def render_in_thread(self, markdown: str, width: int) -> None:
        # Held keys move on before a render starts.
        time.sleep(0.08)
        if not get_current_worker().is_cancelled:
            self.app.call_from_thread(self.rendered, markdown, width, render_markdown(markdown, width))

    def rendered(self, markdown: str, width: int, lines: Segments) -> None:
        self.cache[markdown, width] = lines
        if self.current == (markdown, width):
            self.set_content(lines)

    def set_content(self, content, *, loading: bool = False) -> None:
        self.auto_refresh = 1 / 12 if loading else None
        self.update(content)


class SelectorApp(App[Callable[[Path], int] | None]):
    TITLE = "Skill selector"
    ENABLE_COMMAND_PALETTE = False
    CSS = """
    Screen { background: ansi_default; color: ansi_default; }
    #title { height: 1; margin: 1 2; }
    #search-box { height: 3; margin: 0 2 1 2; border: round #999999; }
    #search-prompt { width: 2; }
    #search { height: 1; padding: 0; border: none; background: transparent; }
    #search:focus { border: none; }
    #columns { margin: 0 1 1 1; }
    .column { padding: 0 1; }
    #source-column, #skill-column { border-right: solid #999999; }
    #skill-column { width: 1fr; }
    #details { width: 2fr; }
    #details {
        /* A body appearing must not narrow the pane and render the body again. */
        scrollbar-gutter: stable;
        scrollbar-size-vertical: 1;
        scrollbar-background: transparent;
        scrollbar-color: #999999;
        scrollbar-color-hover: #999999;
        scrollbar-color-active: #D77757;
    }
    #skill-head { height: auto; }
    #skill-name { width: 1fr; text-style: bold; }
    #skill-state { width: auto; }
    #skill-metadata { margin-bottom: 1; }
    #skill-foot {
        text-wrap: nowrap; text-overflow: fold;
        border-bottom: solid #999999; padding-bottom: 1; margin-bottom: 1;
    }
    #details.-empty #skill-head, #details.-empty #skill-metadata, #details.-empty #skill-foot { display: none; }
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
        self.selection_catalog = skill_catalog.load_catalog(self.root)
        self.catalog = display_catalog(self.selection_catalog)
        self.draft = SelectionDraft.load(CheckoutStore(self.root), self.selection_catalog)
        self.not_installed = skill_catalog.selection_uncommitted(self.root)
        self.install_error: str | None = None
        self.install_check = None
        self.markers: dict[str, str] = {}
        self.source: str | None = NOT_IN_CATALOG if self.draft.off_catalog else next(iter(self.catalog), None)
        self.search_text = ""
        self.theme = "textual-dark"
        self.ansi_color = True

    def compose(self) -> ComposeResult:
        yield Static(id="title")
        with Horizontal(id="search-box"):
            yield Static(">", id="search-prompt")
            yield Input(placeholder="Search skills", select_on_focus=False, id="search")
        with Horizontal(id="columns"):
            with Vertical(id="source-column", classes="column"):
                yield Static("Sources", classes="heading")
                yield CatalogList(id="sources")
            with Vertical(id="skill-column", classes="column"):
                yield Static("Skills", id="skill-heading", classes="heading")
                yield CatalogList(circle_toggle=True, id="skills")
            with VerticalScroll(id="details", classes="column"):
                with Horizontal(id="skill-head"):
                    yield Static(id="skill-name")
                    yield Static(id="skill-state")
                yield Static(id="skill-metadata")
                yield Static(id="skill-foot")
                yield SkillBody(id="skill-body")
        yield Static(id="message")
        yield Static(id="hints")

    def on_mount(self) -> None:
        # Pointer, scrollbar, padding and divider take 6 cells; past the cap, rows truncate.
        names = [NOT_IN_CATALOG, *self.catalog]
        self.query_one("#source-column").styles.width = min(max(map(cell_len, names)) + 6, 49)
        self.refresh_title()
        self.fill_sources()
        self.fill_skills()
        self.query_one("#sources", CatalogList).focus()
        if not self.not_installed:
            self.install_check = self.run_worker(self.check_install_status, thread=True)
        for name, source in self.selection_catalog.sources.items():
            self.run_worker(partial(self.find_update, name, source), thread=True)

    def find_update(self, name: str, source: skill_catalog.SourceCatalog) -> None:
        worker = get_current_worker()

        def tip_unless_cancelled(url: str, branch: str) -> str | None:
            return None if worker.is_cancelled else branch_tip(self.root, url, branch)

        marker = skill_catalog.update_marker(name, source, tip_unless_cancelled)
        if marker and not worker.is_cancelled:
            self.call_from_thread(self.show_update, name, marker)

    def show_update(self, source: str, marker: str) -> None:
        self.markers[source] = marker
        self.fill_sources(keep_cursor=True)

    def check_install_status(self) -> None:
        status = reconcile_skills.install_status(self.root)
        self.call_from_thread(self.show_install_status, not status.installed, status.problem)

    def show_install_status(self, not_installed: bool, error: str | None) -> None:
        self.not_installed = not_installed
        self.install_error = error
        self.refresh_title()
        if error:
            self.query_one("#message", Static).update(Text(f"⎿ {error}"))

    def refresh_title(self) -> None:
        self.query_one("#title", Static).update(Text.assemble(
            ("✻ ", ACCENT), ("Skill selector", "bold"),
            (f"   {len(self.draft)} selected · {self.draft.unsaved} unsaved", SECONDARY),
            (" · not installed" if self.not_installed else "", ACCENT),
        ))

    def fill_sources(self, *, keep_cursor: bool = False) -> None:
        rows = []
        matching_sources = []
        off_catalog = self.draft.off_catalog
        if off_catalog:
            selected = sum(key in self.draft for key in off_catalog)
            count = sum(self.search_text in line.value.casefold() for line in off_catalog.values())
            matches = f" · {count} match{'es' if count != 1 else ''}" if self.search_text else ""
            rows.append(Row(NOT_IN_CATALOG, [Text(NOT_IN_CATALOG),
                                             Text(f"  {selected}/{len(off_catalog)}{matches}", style=SECONDARY)]))
            if count:
                matching_sources.append(NOT_IN_CATALOG)
        for source, skills in self.catalog.items():
            selected = sum(f"{source}:{skill.name}" in self.draft for skill in skills)
            count = len(self.matching_skills(source))
            matches = f" · {count} match{'es' if count != 1 else ''}" if self.search_text else ""
            marker = f" · {self.markers[source]}" if source in self.markers else ""
            rows.append(Row(source, [Text(source), Text.assemble(
                (f"  {selected}/{len(skills)}{matches}", SECONDARY), (marker, ACCENT),
            )]))
            if count:
                matching_sources.append(source)
        sources = self.query_one("#sources", CatalogList)
        sources.set_rows(rows, keep_cursor=keep_cursor)
        if self.search_text and self.source not in matching_sources and matching_sources:
            self.source = matching_sources[0]
            sources.highlight(next(i for i, row in enumerate(rows) if row.key == self.source))

    def matching_skills(self, source: str | None) -> list[Skill]:
        return [skill for skill in self.catalog.get(source, [])
                if any(self.search_text in field.casefold()
                       for field in (skill.name, source, skill.group, skill.description))]

    @on(Input.Changed, "#search")
    def search_changed(self, event: Input.Changed) -> None:
        self.search_text = event.value.strip().casefold()
        self.fill_sources(keep_cursor=True)
        self.fill_skills(keep_cursor=True)

    @on(Input.Submitted, "#search")
    def search_submitted(self) -> None:
        self.action_column("skills")

    def on_key(self, event: Key) -> None:
        if self.screen.is_modal:
            return
        search = self.query_one("#search", Input)
        if search.has_focus:
            if event.key != "down":
                return
            self.action_column("skills")
        elif event.is_printable and event.character != " ":
            search.focus()
            if event.character != "/":
                search.insert_text_at_cursor(event.character)
        else:
            return
        event.stop()
        event.prevent_default()

    def fill_skills(self, *, keep_cursor: bool = False) -> None:
        rows = []
        if self.source == NOT_IN_CATALOG:
            for key, line in self.draft.off_catalog.items():
                if self.search_text not in line.value.casefold():
                    continue
                text = Text("● " if key in self.draft else "○ ", style=ACCENT if key in self.draft else SECONDARY)
                text.append(line.value, style="default")
                rows.append(Row(key, [text, Text(line.error.replace("\n", " "), style=SECONDARY)]))
        group = ""
        for skill in self.matching_skills(self.source):
            if skill.group != group:
                group = skill.group
                # A blank line above each heading but the first separates the groups.
                rows.append(Row(None, [Text()] * bool(rows) + [Text(group, style=SECONDARY)]))
            key = f"{self.source}:{skill.name}"
            selected = key in self.draft
            line = Text("● " if selected else "○ ", style=ACCENT if selected else SECONDARY)
            line.append(skill.name, style="default")
            if skill.manual_only:
                line.append("  manual", style=SECONDARY)
            rows.append(Row(key, [line]))
        if not rows:
            empty = "No matching skills." if self.search_text else "No skills in this source."
            rows.append(Row(None, [Text(empty, style=SECONDARY)]))
        self.query_one("#skills", CatalogList).set_rows(rows, keep_cursor=keep_cursor)
        self.query_one("#skill-heading", Static).update(self.source or "Skills")
        self.refresh_details()

    def refresh_details(self) -> None:
        key = self.query_one("#skills", CatalogList).current
        metadata = self.query_one("#skill-metadata", Static)
        foot = self.query_one("#skill-foot", Static)
        details = self.query_one("#details", VerticalScroll)
        details.scroll_home(animate=False)
        details.set_class(key is None, "-empty")
        if key is None:
            for part in ("#skill-name", "#skill-state", "#skill-metadata", "#skill-foot"):
                self.query_one(part, Static).update("")
            self.query_one("#skill-body", SkillBody).show("")
            return
        selected = key in self.draft
        name_style = ACCENT if selected else SECONDARY
        self.query_one("#skill-state", Static).update(
            Text("● selected", style=ACCENT) if selected else Text("○ not selected", style=SECONDARY),
        )
        if key in self.draft.off_catalog:
            line = self.draft.off_catalog[key]
            self.query_one("#skill-name", Static).update(Text(line.value, style=name_style))
            metadata.update(Text.assemble(
                (f"skills.txt:{line.number}", SECONDARY), "\n\n", line.error,
            ))
            foot.update(Text("Can be deselected only.", style=SECONDARY))
            self.query_one("#skill-body", SkillBody).show("")
            return
        source, name = key.split(":")
        skill = next(skill for skill in self.catalog[source] if skill.name == name)
        notes = [Text(key, style=SECONDARY)]
        if not skill.unreadable:
            notes.append(Text("manual: description not loaded into context", style=MANUAL) if skill.manual_only
                         else Text("automatic only: not in the / menu", style=AUTOMATIC) if skill.agent_only
                         else Text("automatic", style=AUTOMATIC))
        others = [f"{other}:{name}" for other, skills in self.catalog.items()
                  if other != source and any(skill.name == name for skill in skills)]
        if others:
            notes.append(Text("Same name in other sources: " + ", ".join(others), style=SECONDARY))
        location = skill.path.relative_to(self.selection_catalog.sources[source].path).as_posix()
        files = f"{skill.file_count} {'file' if skill.file_count == 1 else 'files'}"
        self.query_one("#skill-name", Static).update(Text.assemble(
            (skill.name, name_style), (" (forked)", f"not bold {SECONDARY}") if skill.forked else "",
        ))
        metadata.update(Text.assemble(
            Text("\n").join(notes), "\n\n",
            ("description unreadable", SECONDARY) if skill.unreadable else skill.description,
        ))
        # Folds inside the path instead of wrapping at a space and orphaning the file count.
        foot.update(Text.assemble(
            (f"{files} · ", SECONDARY), source, ("" if location == "." else f"/{location}", SECONDARY),
        ))
        self.query_one("#skill-body", SkillBody).show(skill.body)

    @on(CatalogList.Highlighted, "#sources")
    def source_highlighted(self, event: CatalogList.Highlighted) -> None:
        if event.control.current != self.source:
            self.source = event.control.current
            self.fill_skills()

    @on(CatalogList.Highlighted, "#skills")
    def skill_highlighted(self) -> None:
        self.refresh_details()

    def refresh_selection(self) -> None:
        self.refresh_title()
        self.fill_sources(keep_cursor=True)
        self.fill_skills(keep_cursor=True)

    def action_enter(self) -> None:
        if self.focused is self.query_one("#skills", CatalogList):
            self.action_toggle()
        else:
            self.action_column("skills")

    @on(CatalogList.Activated, "#skills")
    def action_toggle(self) -> None:
        key = self.query_one("#skills", CatalogList).current
        if key is None:
            return
        outcome = self.draft.toggle(key)
        if isinstance(outcome, Refused):
            self.query_one("#message", Static).update(Text("⎿ Cannot select: " + outcome.reason))
            return
        if isinstance(outcome, Replace):
            def replace(choice: int | None) -> None:
                if choice == 1:
                    self.draft.toggle(key, replace=True)
                    self.refresh_selection()

            source, name = key.split(":")
            self.push_screen(ChoiceDialog(f"Replace {name}?", [
                f"Yes, use {source}", f"No, keep {outcome.current.split(':')[0]}",
            ]), replace)
            return
        self.refresh_selection()

    async def action_save(self) -> None:
        if self.install_check is not None:
            await self.install_check.wait()
        if duplicates := self.draft.duplicates:
            self.query_one("#message", Static).update(Text(
                "⎿ Cannot save: same name selected more than once: " + ", ".join(duplicates),
            ))
            return
        changes = self.draft.changes
        if not changes and not self.draft.unsaved and not self.not_installed:
            self.query_one("#message", Static).update("⎿ No changes to save.")
            return
        reason = self.install_unavailable_reason()
        install_label = "Save and install" if self.draft.unsaved else "Install"
        self.push_screen(ChoiceDialog("Save these changes?", [install_label, "Save only", "Keep editing"],
                                      "\n".join(changes.lines()) or "No changes from the last commit.",
                                      disabled={1: reason} if reason else None),
                         self.save_choice)

    def install_unavailable_reason(self) -> str | None:
        return (self.draft.install_blocker or reconcile_skills.selection_install_blocker(self.root)
                or (self.install_error if not self.draft.changes else None))

    def save_choice(self, choice: int | None) -> None:
        if choice in (1, 2):
            self.save_selection(install=choice == 1)

    def save_selection(self, overwrite: Conflict | None = None, *, install: bool = False) -> None:
        if install and (reason := self.install_unavailable_reason()):
            self.query_one("#message", Static).update(Text(f"⎿ Cannot install: {reason}"))
            return
        try:
            outcome = self.draft.save(install=install, overwrite=overwrite)
        except (reconcile_skills.ReconcileError, OSError) as error:
            self.query_one("#message", Static).update(Text(f"⎿ Could not save: {error}"))
            return
        if isinstance(outcome, Conflict):
            def conflict_choice(choice: int | None) -> None:
                if choice == 1:
                    self.reload_selection()
                elif choice == 2:
                    self.save_selection(overwrite=outcome, install=install)

            self.push_screen(ChoiceDialog("skills.txt changed on disk", ["Reload", "Overwrite"],
                                          "Reload drops your unsaved changes. Overwrite replaces the file."),
                             conflict_choice)
            return
        if isinstance(outcome, Blocked):
            self.query_one("#message", Static).update(Text("⎿ Cannot save: " + outcome.reason))
            return
        if install:
            self.exit(install_saved_selection)
            return
        self.not_installed = skill_catalog.selection_uncommitted(self.root)
        if not self.not_installed:
            self.install_check = self.run_worker(self.check_install_status, thread=True)
        self.refresh_title()
        self.query_one("#message", Static).update("⎿ Saved. Not installed yet.")

    def reload_selection(self) -> None:
        try:
            self.draft = SelectionDraft.load(CheckoutStore(self.root), self.selection_catalog)
        except (reconcile_skills.ReconcileError, OSError) as error:
            self.query_one("#message", Static).update(Text(f"⎿ Could not reload: {error}"))
            return
        self.source = NOT_IN_CATALOG if self.draft.off_catalog else next(iter(self.catalog), None)
        self.not_installed = skill_catalog.selection_uncommitted(self.root)
        self.install_error = None
        self.install_check = None
        if not self.not_installed:
            self.install_check = self.run_worker(self.check_install_status, thread=True)
        self.refresh_title()
        self.fill_sources()
        self.fill_skills()
        self.query_one("#message", Static).update("⎿ Reloaded skills.txt. Unsaved changes discarded.")

    def action_quit(self) -> None:
        search = self.query_one("#search", Input)
        if search.has_focus:
            if search.value:
                search.value = ""
            else:
                self.action_column("skills")
            return
        if not self.draft.unsaved:
            self.exit()
            return
        self.push_screen(ChoiceDialog("Quit without saving?", [
            "Keep editing", "Review and save", "Quit without saving",
        ]), self.quit_choice)

    async def quit_choice(self, choice: int | None) -> None:
        if choice == 2:
            await self.action_save()
        elif choice == 3:
            self.exit()

    def action_column(self, column: str) -> None:
        self.query_one(f"#{column}", CatalogList).focus()

    def on_descendant_focus(self) -> None:
        if self.focused is self.query_one("#search"):
            hint = "↓ or enter skills · esc clear or leave search · ctrl+s save"
        elif self.focused is self.query_one("#details"):
            hint = "↑↓ scroll · ← sources · → skills · ctrl+s save · esc quit"
        elif self.focused is self.query_one("#skills"):
            hint = "↑↓ move · space or enter toggle · ← sources · tab details · ctrl+s save · esc quit"
        else:
            hint = "↑↓ move · → or enter skills · space toggle · ctrl+s save · esc quit"
        self.query_one("#hints", Static).update(hint)


def shell_path_env() -> dict[str, str]:
    selector_bin = Path(sys.prefix, "bin")
    path = os.environ.get("PATH", os.defpath).split(os.pathsep)
    return dict(os.environ, PATH=os.pathsep.join(entry for entry in path if Path(entry) != selector_bin))


def install_saved_selection(root: Path) -> int:
    """Run only after the TUI closes, with hooks and signing in the terminal."""
    if reason := reconcile_skills.selection_install_blocker(root):
        print(f"Could not commit: {reason}. skills.txt stays saved; nothing was committed or installed.")
        return 1
    try:
        changes = Changes.between(skill_catalog.git(["show", "HEAD:skills.txt"], cwd=root),
                                  skill_catalog.read_selection_text(root))
        message = f"Update skill selection ({changes.summary})"
        # Match the checkout inspected by the catalog's Git reads, while
        # retaining terminal streams for hooks and interactive signing.
        if skill_catalog.selection_uncommitted(root):
            commit = subprocess.run(
                ["git", "commit", "-m", message, "-m", "\n".join(changes.lines()), "--only", "--", "skills.txt"],
                cwd=root, env=skill_catalog.git_env(interactive=True), check=False,
            )
            if commit.returncode:
                print("Commit failed. skills.txt stays saved; nothing was committed or installed.")
                return commit.returncode
    except (reconcile_skills.ReconcileError, OSError) as error:
        print(f"Could not commit: {error}. skills.txt stays saved; nothing was committed or installed.")
        return 1
    try:
        result = subprocess.run([str(root / "scripts/reconcile-skills.sh")], cwd=root,
                                env=shell_path_env(), check=False)
        code = result.returncode
    except OSError as error:
        print(f"Could not install: {error}. The selection commit was kept.")
        code = 1
    if skill_catalog.git(["rev-parse", "--verify", "@{upstream}"], cwd=root, check=False):
        if int(skill_catalog.git(["rev-list", "--count", "@{upstream}..HEAD"], cwd=root)):
            print("Not pushed yet. To back it up: git push")
    if code:
        print("run the skill selector again and choose Install")
    return code


def main() -> int:
    try:
        app = SelectorApp()
    except (reconcile_skills.ReconcileError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    handoff = app.run()
    return handoff(app.root) if handoff else 0


if __name__ == "__main__":
    raise SystemExit(main())
