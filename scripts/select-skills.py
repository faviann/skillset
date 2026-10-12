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
from collections.abc import Callable, Collection
from dataclasses import dataclass
from enum import Enum, auto
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
from textual.geometry import Offset, Region
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Input, RichLog, Static
from textual.worker import get_current_worker

import reconcile_skills
import skill_catalog
import update_sources
from selection_draft import Blocked, Changes, CheckoutStore, Conflict, Refused, Replace, SelectionDraft

ACCENT = "#D77757"
SECONDARY = "#999999"
# ANSI names, so the terminal theme keeps them readable on light and dark.
MANUAL = "red"
AUTOMATIC = "green"
NOT_IN_CATALOG = "Not in catalog"
LOOKUP_TIMEOUT_SECONDS = 5
TITLE_PREFIX = "Skill selector  "


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
        *(Binding(str(number), f"choose({number})", show=False) for number in range(1, 10)),
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


class NameDialog(ModalScreen[str | None]):
    """Ask for a profile name until it follows the skill-name rules and is not taken."""

    DEFAULT_CSS = ChoiceDialog.DEFAULT_CSS.replace("ChoiceDialog", "NameDialog") + """
    #name-box { height: 3; margin-top: 1; border: round #999999; }
    #name-input { height: 1; padding: 0; border: none; background: transparent; }
    #name-input:focus { border: none; }
    #name-error { height: auto; color: red; }
    """
    BINDINGS = [Binding("escape", "cancel", show=False)]

    def __init__(self, title: str, details: str, taken: Collection[str], *, value: str = "") -> None:
        super().__init__()
        self.heading = title
        self.details = details
        self.taken = taken
        self.value = value

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static(Text(self.heading), id="dialog-title")
            yield Static(Text(self.details), id="dialog-details")
            with Horizontal(id="name-box"):
                yield Static(">", id="search-prompt")
                yield Input(self.value, placeholder="profile name", select_on_focus=False, id="name-input")
            yield Static(id="name-error")
            yield Static("Enter to confirm · Esc to cancel", id="dialog-hint")

    @on(Input.Submitted, "#name-input")
    def submitted(self, event: Input.Submitted) -> None:
        name = event.value.strip()
        if not skill_catalog.valid_name(name):
            self.query_one("#name-error", Static).update("use 1-64 lowercase letters, digits or single hyphens")
        elif name in self.taken:
            self.query_one("#name-error", Static).update(Text(f"{name} already exists"))
        else:
            self.dismiss(name)

    def action_cancel(self) -> None:
        self.dismiss(None)


class ProfilePicker(ModalScreen[tuple[str, str] | None]):
    """Every profile, in a list dropped down under the edited profile's name in the title.

    Dismisses with (operation, profile) for the highlighted row, or None.
    """

    DEFAULT_CSS = """
    ProfilePicker { align: left top; background: transparent; }
    #dialog { height: auto; padding: 0 1; border: round #D77757; background: ansi_default; }
    #choices { height: auto; max-height: 16; }
    #dialog-hint { height: auto; margin-top: 1; color: #999999; }
    """
    BINDINGS = [
        Binding("escape", "cancel", show=False),
        Binding("enter", "pick('edit')", show=False),
        Binding("n", "pick('new')", show=False),
        Binding("c", "pick('copy')", show=False),
        Binding("r", "pick('rename')", show=False),
        Binding("d", "pick('delete')", show=False),
        Binding("u", "pick('use')", show=False),
    ]
    HINT = "enter edit · u use here · esc close\nn new · c copy · r rename · d delete"

    def __init__(self, rows: list[Row], current: str, *, under: Offset) -> None:
        super().__init__()
        self.rows = rows
        self.current = current
        self.under = under

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield CatalogList(activate_on_click=True, id="choices")
            yield Static(self.HINT, id="dialog-hint")

    def on_mount(self) -> None:
        choices = self.query_one("#choices", CatalogList)
        choices.set_rows(self.rows)
        choices.cursor = next(i for i, row in enumerate(self.rows) if row.key == self.current)
        choices.redraw()
        choices.focus()
        # Border, padding and pointer take 6 cells; the names line up under the cell `under`.
        widest = max(cell_len(line.plain) for row in self.rows for line in row.lines)
        dialog = self.query_one("#dialog")
        dialog.styles.width = max(widest, *map(cell_len, self.HINT.splitlines())) + 6
        dialog.styles.margin = (self.under.y + 1, 0, 0, self.under.x - 4)

    @on(CatalogList.Activated, "#choices")
    def activated(self) -> None:
        self.action_pick("edit")

    def action_pick(self, operation: str) -> None:
        self.dismiss((operation, self.query_one("#choices", CatalogList).current))

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_click(self, event: Click) -> None:
        if not self.query_one("#dialog").region.contains(event.screen_x, event.screen_y):
            self.dismiss(None)


class Phase(Enum):
    PREPARING = auto()
    REVIEW = auto()
    PUBLISHING = auto()
    DISCARDING = auto()
    DONE = auto()
    FAILED = auto()


class UpdateDialog(ModalScreen[bool]):
    """Update one source: prepare it in a thread, review it, then publish and install it or discard it.

    Dismisses with whether the update reached origin/main, so the selector knows to reload.
    """

    DEFAULT_CSS = """
    UpdateDialog { align: center bottom; background: transparent; }
    #update-dialog { height: 85%; margin: 0 2 2 2; padding: 1 2; border: round #D77757; }
    #update-title { height: auto; color: #D77757; margin-bottom: 1; }
    #update-log {
        height: 1fr; background: transparent;
        scrollbar-size-vertical: 1;
        scrollbar-background: transparent;
        scrollbar-color: #999999;
        scrollbar-color-active: #D77757;
    }
    #update-status { height: auto; margin-top: 1; }
    #update-hint { height: 1; margin-top: 1; color: #999999; }
    """
    BINDINGS = [
        Binding("a", "accept", show=False),
        Binding("d", "diff", show=False),
        Binding("escape", "close", show=False),
    ]

    def __init__(self, root: Path, source: str) -> None:
        super().__init__()
        self.root = root
        self.source = source
        self.phase = Phase.PREPARING
        self.prepared: update_sources.Prepared | None = None
        self.showing_diff = False
        self.pushed = False

    def compose(self) -> ComposeResult:
        with Vertical(id="update-dialog"):
            yield Static(Text(f"Update {self.source}"), id="update-title")
            yield RichLog(wrap=True, id="update-log")
            yield Static(id="update-status")
            yield Static(id="update-hint")

    def on_mount(self) -> None:
        self.enter(Phase.PREPARING)
        self.run_worker(self.prepare, thread=True)

    def enter(self, phase: Phase, status: str = "") -> None:
        self.phase = phase
        self.query_one("#update-status", Static).update(
            Text(status, style=ACCENT if phase is Phase.DONE else MANUAL if phase is Phase.FAILED else ""))
        self.query_one("#update-hint", Static).update({
            Phase.PREPARING: "Preparing the update…",
            Phase.REVIEW: f"a accept and push · d {'summary' if self.showing_diff else 'diff'} · esc discard",
            Phase.PUBLISHING: "Publishing and installing…",
            Phase.DISCARDING: "Discarding the update…",
            Phase.DONE: "Esc to close",
            Phase.FAILED: "Esc to close",
        }[phase])

    def progress(self, line: str) -> None:
        self.app.call_from_thread(self.query_one(RichLog).write, Text(line))

    def finished(self, phase: Phase, status: str) -> None:
        self.app.call_from_thread(self.enter, phase, status)

    def prepare(self) -> None:
        try:
            worktree = update_sources.start(self.root, self.progress)
            catalog = skill_catalog.load_catalog(worktree)
        except (reconcile_skills.ReconcileError, OSError, subprocess.SubprocessError) as error:
            self.finished(Phase.FAILED, f"error: {error}")
            return
        if self.source not in catalog.sources:
            self.finished(Phase.DONE, f"{self.source} is no longer a source")
            return
        try:
            prepared = update_sources.prepare(worktree, self.source, catalog, self.progress)
        except (reconcile_skills.ReconcileError, OSError, subprocess.SubprocessError) as error:
            self.finished(Phase.FAILED, f"{self.source}: skipped: {error}")
            return
        if isinstance(prepared, str):
            self.finished(Phase.DONE, f"{self.source}: {prepared}")
        else:
            self.app.call_from_thread(self.review, prepared)

    def review(self, prepared: update_sources.Prepared) -> None:
        self.prepared = prepared
        self.show_review()
        self.enter(Phase.REVIEW)

    def show_review(self) -> None:
        log = self.query_one(RichLog)
        log.clear()
        if not self.showing_diff:
            log.write(Text("\n".join(self.prepared.summary.lines(self.prepared.update, named=False))))
            return
        for line in update_sources.diff_text(self.prepared).splitlines():
            log.write(Text(line, style=AUTOMATIC if line.startswith("+") else MANUAL if line.startswith("-")
                           else SECONDARY if line.startswith("@@") else ""))

    def action_diff(self) -> None:
        if self.phase is Phase.REVIEW:
            self.showing_diff = not self.showing_diff
            self.show_review()
            self.enter(Phase.REVIEW)

    def action_accept(self) -> None:
        if self.phase is Phase.REVIEW:
            self.showing_diff = False
            self.show_review()
            self.enter(Phase.PUBLISHING)
            self.run_worker(self.publish, thread=True)

    def publish(self) -> None:
        try:
            update_sources.publish(self.prepared, self.progress)
        except (reconcile_skills.ReconcileError, OSError, subprocess.SubprocessError) as error:
            self.finished(Phase.FAILED, f"{self.source}: skipped: {error}")
            return
        self.pushed = True
        self.progress(f"{self.source}: pushed")
        try:
            update_sources.finish(self.root, self.progress, shell_path_env())
        except (reconcile_skills.ReconcileError, OSError, subprocess.SubprocessError) as error:
            self.finished(Phase.FAILED, f"{self.source}: pushed, but {error}")
            return
        self.finished(Phase.DONE, f"{self.source}: pushed and installed")

    def discard(self) -> None:
        try:
            update_sources.reset_worktree(self.prepared.worktree, self.prepared.checkpoint, self.progress)
        except (reconcile_skills.ReconcileError, OSError, subprocess.SubprocessError) as error:
            self.finished(Phase.FAILED, f"{self.source}: could not discard the update: {error}")
            return
        self.app.call_from_thread(self.dismiss, False)

    def action_close(self) -> None:
        if self.phase is Phase.REVIEW:
            self.enter(Phase.DISCARDING)
            self.run_worker(self.discard, thread=True)
        elif self.phase in (Phase.DONE, Phase.FAILED):
            self.dismiss(self.pushed)


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
    """Edit one profile at a time. Exits with the terminal handoff that commits it, if any."""

    TITLE = "Skill selector"
    ENABLE_COMMAND_PALETTE = False
    # Textual's own ANSI rule renders the screen behind a dialog dim.
    SUSPENDED_SCREEN_CLASS = "-screen-suspended"
    CSS = """
    Screen { background: ansi_default; color: ansi_default; }
    /* The Screen rule above also paints dialog screens opaque, hiding the selector behind them. */
    ModalScreen { background: transparent; }
    /* Dim fades text but not border colours, so grey the borders behind a dialog here. */
    Screen.-screen-suspended #search-box { border: round #5f5f5f; }
    Screen.-screen-suspended #source-column, Screen.-screen-suspended #skill-column { border-right: solid #5f5f5f; }
    Screen.-screen-suspended #skill-foot { border-bottom: solid #5f5f5f; }
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
        Binding("ctrl+u", "update", show=False),
        Binding("ctrl+p", "profiles", show=False),
        Binding("escape", "quit", show=False),
    ]

    def __init__(self, root: Path | None = None) -> None:
        super().__init__()
        self.root = (root or Path(__file__).resolve().parent.parent).resolve()
        self.record_problem = ""
        try:
            self.active: str | None = reconcile_skills.active_profile(self.root)
        except reconcile_skills.ReconcileError as error:
            # The launch prompt asks which profile to use; until then the first one is edited.
            self.active = None
            # A broken or stale record says why at the prompt; a missing one is simply a fresh home.
            self.record_problem = (str(error).partition("\n")[0]
                                   if os.path.lexists(reconcile_skills.record_path()) else "")
            if not skill_catalog.list_profiles(self.root):
                raise
        # The profile being edited; the picker switches it to any other.
        self.profile = self.active or skill_catalog.list_profiles(self.root)[0]
        # Browsing needs source validation, not the reconciler's deployment gate.
        self.selection_catalog = skill_catalog.load_catalog(self.root)
        self.catalog = display_catalog(self.selection_catalog)
        self.store = CheckoutStore(self.root, self.profile)
        self.draft = SelectionDraft.load(self.store, self.selection_catalog)
        self.not_installed = False
        self.not_committed = False
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
        self.refresh_file_status()
        self.refresh_title()
        self.fill_sources()
        self.fill_skills()
        self.query_one("#sources", CatalogList).focus()
        for name, source in self.selection_catalog.sources.items():
            self.run_worker(partial(self.find_update, name, source), thread=True)
        if self.active is None:
            self.call_after_refresh(self.choose_profile_here)

    def choose_profile_here(self) -> None:
        """Ask a home with no profile which one to use, as setup opens the selector on a fresh home."""
        names = self.profile_names()
        disabled = {number: reason for number, name in enumerate(names, 1)
                    if (reason := self.file_operation_blocker(name))}

        def chosen(choice: int | None) -> None:
            if choice is None:
                self.query_one("#message", Static).update(
                    "⎿ Browsing without a profile for this home. Saving offers to use the edited one here.")
            elif choice > len(names):
                self.new_profile()
            else:
                self.exit(partial(finish_handoff, profile=names[choice - 1], after=After.USE_HERE))

        self.push_screen(ChoiceDialog(
            "This home has no profile yet. Which one should it use?", [*names, "New profile"],
            (f"{self.record_problem}\n\n" if self.record_problem else "")
            + "The choice is saved in ~/.agents/.skillset/profile for this home only, "
            "then that profile is installed.\nEsc to browse first and decide when you save.",
            disabled=disabled), chosen)

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
        self.refresh_hints()

    def check_install_status(self) -> None:
        status = reconcile_skills.install_status(self.root)
        self.call_from_thread(self.show_install_status, not status.installed, status.problem)

    def refresh_file_status(self) -> None:
        """Mark the edited profile's saved file not installed or not committed, or check installation in a thread.

        Only the profile this home uses can be installed here; any other is only committed.
        """
        used_here = self.profile == self.active
        self.not_installed = used_here and skill_catalog.selection_uncommitted(self.root, self.profile)
        self.not_committed = not used_here and self.profile in self.uncommitted_profiles()
        self.install_error = None
        self.install_check = None
        if used_here and not self.not_installed:
            self.install_check = self.run_worker(self.check_install_status, thread=True)

    def show_install_status(self, not_installed: bool, error: str | None) -> None:
        if self.profile != self.active:
            return
        self.not_installed = not_installed
        self.install_error = error
        self.refresh_title()
        if error:
            self.query_one("#message", Static).update(Text(f"⎿ {error}"))

    def refresh_title(self) -> None:
        where = (("  · used here", ACCENT) if self.profile == self.active
                 else ("  · this home has no profile", ACCENT) if self.active is None
                 else (f"  · this home uses {self.active}", SECONDARY))
        self.query_one("#title", Static).update(Text.assemble(
            ("✻ ", ACCENT), (TITLE_PREFIX, "bold"), (self.profile, f"bold {ACCENT}"), where,
            (f"   {len(self.draft)} selected · {self.draft.unsaved} unsaved", SECONDARY),
            (" · not installed" if self.not_installed else " · not committed" if self.not_committed else "", ACCENT),
        ))

    def profile_names(self) -> list[str]:
        """Profiles at HEAD or on disk, and the edited one, sorted."""
        names = {self.profile, *skill_catalog.list_profiles(self.root)}
        directory = self.root / skill_catalog.PROFILES_DIR
        if directory.is_dir() and not directory.is_symlink():
            names.update(path.stem for path in directory.glob("*.txt")
                         if skill_catalog.valid_name(path.stem) and path.is_file() and not path.is_symlink())
        return sorted(names)

    def uncommitted_profiles(self) -> set[str]:
        """Profiles whose file differs from HEAD, including files Git does not track yet."""
        status = skill_catalog.git(["status", "--porcelain", "-z", "--no-renames", "--untracked-files=all",
                                    "--", f"{skill_catalog.PROFILES_DIR}/"], cwd=self.root)
        paths = {entry[3:] for entry in status.split("\0") if entry}
        return {name for name in self.profile_names() if skill_catalog.profile_path(name) in paths}

    def profile_rows(self) -> list[Row]:
        names = self.profile_names()
        uncommitted = self.uncommitted_profiles()
        width = max(map(cell_len, names)) + 3
        rows = []
        for name in names:
            try:
                text = self.store.read() if name == self.profile else skill_catalog.read_selection_text(self.root, name)
                count = len(skill_catalog.parse_selection(text))
            except (reconcile_skills.ReconcileError, OSError):
                count = 0
            notes = [("● used here", ACCENT)] if name == self.active else []
            notes.append((f"{count} skill{'' if count == 1 else 's'}", SECONDARY))
            if name == self.profile and self.draft.unsaved:
                notes.append((f"{self.draft.unsaved} unsaved", ACCENT))
            elif name in uncommitted:
                notes.append(("not committed", SECONDARY))
            label = Text(name, style=f"bold {ACCENT}" if name == self.profile else "default")
            label.pad_right(width - cell_len(name))
            rows.append(Row(name, [label + Text("  ").join(Text(text, style=style) for text, style in notes)]))
        return rows

    def action_profiles(self) -> None:
        def picked(choice: tuple[str, str] | None) -> None:
            if choice:
                self.profile_operation(*choice)

        title = self.query_one("#title").region
        name = Offset(title.x + cell_len("✻ " + TITLE_PREFIX), title.y)
        self.push_screen(ProfilePicker(self.profile_rows(), self.profile, under=name), picked)

    def profile_operation(self, operation: str, name: str) -> None:
        if operation == "edit":
            self.switch_to(name)
        elif operation in ("new", "copy"):
            self.new_profile(copy_from=name if operation == "copy" else None)
        elif operation == "rename":
            self.rename_profile(name)
        elif operation == "delete":
            self.delete_profile(name)
        elif operation == "use":
            self.use_here(name)

    def switch_to(self, name: str, edit: Callable[[], None] | None = None) -> None:
        """Edit name, through `edit` if given, after asking whether to drop unsaved changes."""
        edit = edit or partial(self.edit_profile, name)
        if name == self.profile:
            return
        if not self.draft.unsaved:
            edit()
            return

        async def choice(number: int | None) -> None:
            if number == 2:
                await self.action_save()
            elif number == 3:
                edit()

        self.push_screen(ChoiceDialog(f"Switch to {name} with unsaved changes?",
                                      ["Keep editing", "Review and save", "Discard and switch"],
                                      "\n".join(self.draft.changes.lines())), choice)

    def edit_profile(self, name: str) -> None:
        if not self.load_profile(name, "Could not edit"):
            return
        where = ("" if name == self.active else " This home has no profile." if self.active is None
                 else f" This home uses {self.active}.")
        self.query_one("#message", Static).update(Text(f"⎿ Editing {name}.{where}"))

    def use_here(self, name: str) -> None:
        """Ask, then close for the terminal handoff to make name this home's profile."""
        if name == self.active:
            self.query_one("#message", Static).update(Text(f"⎿ This home already uses {name}."))
            return
        # --profile installs the file at HEAD, and closing would drop unsaved changes.
        if reason := self.file_operation_blocker(name):
            self.query_one("#message", Static).update(Text(f"⎿ Cannot use {name} here: {reason}"))
            return
        where = (f"This home uses {self.active}; skills only it selects are uninstalled here." if self.active
                 else "This home has no profile yet.")

        def confirmed(choice: int | None) -> None:
            if choice == 1:
                self.exit(partial(finish_handoff, profile=name, after=After.USE_HERE))

        self.push_screen(ChoiceDialog(f"Use {name} in this home?", [f"Use {name} and install", "Cancel"],
                                      f"{where}\nOther homes are not affected."), confirmed)

    def new_profile(self, *, copy_from: str | None = None) -> None:
        """Name a profile that starts empty or as a copy of a saved one, and edit it unsaved."""
        try:
            start = (skill_catalog.read_selection_text(self.root, copy_from) if copy_from
                     else skill_catalog.SELECTION_HEADER + "\n")
        except reconcile_skills.ReconcileError as error:
            self.query_one("#message", Static).update(Text(f"⎿ Cannot copy {copy_from}: {error}"))
            return
        details = (f"Starts as a copy of {copy_from}; the two stay independent afterwards." if copy_from
                   else "Starts empty.") + " It exists only in the selector until you save it."

        def named(name: str | None) -> None:
            if name:
                self.switch_to(name, partial(self.create_profile, name, start))

        self.push_screen(NameDialog(f"Copy {copy_from} as" if copy_from else "New profile", details,
                                    self.profile_names()), named)

    def create_profile(self, name: str, start: str) -> None:
        if self.load_profile(name, "Could not create", start=start):
            self.query_one("#message", Static).update(
                Text(f"⎿ Created {name}. It exists only in the selector until you save it."))

    def load_profile(self, name: str, failure: str, *, start: str | None = None) -> bool:
        """Edit the saved file of name, dropping unsaved changes, or show why it cannot be read.

        With `start`, name has no file yet: the draft starts from that content, unsaved.
        """
        try:
            store = CheckoutStore(self.root, name, start=start)
            self.draft = SelectionDraft.load(store, self.selection_catalog, new=start is not None)
        except (reconcile_skills.ReconcileError, OSError) as error:
            self.query_one("#message", Static).update(Text(f"⎿ {failure}: {error}"))
            return False
        self.store = store
        self.profile = name
        self.source = NOT_IN_CATALOG if self.draft.off_catalog else next(iter(self.catalog), None)
        self.refresh_file_status()
        self.refresh_title()
        self.fill_sources()
        self.fill_skills()
        return True

    def file_operation_blocker(self, name: str) -> str | None:
        """Why name cannot be renamed, deleted or used here now, or None.

        Each closes the selector and acts on name's file as it is at HEAD, so nothing may be unsaved or uncommitted.
        """
        if self.draft.unsaved:
            return "save or discard the unsaved changes first"
        if name not in skill_catalog.list_profiles(self.root) or name in self.uncommitted_profiles():
            return f"{skill_catalog.profile_path(name)} is not committed; save and commit it first"
        return reconcile_skills.selection_install_blocker(self.root, name)

    def rename_profile(self, name: str) -> None:
        """Ask for the new name, then close for the terminal handoff to commit the rename."""
        if reason := self.file_operation_blocker(name):
            self.query_one("#message", Static).update(Text(f"⎿ Cannot rename {name}: {reason}"))
            return
        details = (f"Other homes that use {name} stop installing until they run "
                   "reconcile-skills.sh --profile <new name> again; this selector can't see them.")
        if name == self.active:
            details += "\nThis home uses it, so its record is updated too."

        def named(new: str | None) -> None:
            if new and new != name:
                self.exit(partial(commit_rename, profile=name, new=new,
                                  after=After.USE_HERE if name == self.active else After.NOTHING))

        self.push_screen(NameDialog(f"Rename {name}", details, set(self.profile_names()) - {name}, value=name), named)

    def delete_profile(self, name: str) -> None:
        """Confirm, then close for the terminal handoff to commit the deletion."""
        # A profile saved but not committed is not counted: the deletion's commit would leave HEAD with none.
        reason = ("it is the last profile" if skill_catalog.list_profiles(self.root) == [name]
                  else "this home uses it; choose another here first" if name == self.active
                  else self.file_operation_blocker(name))
        if reason:
            self.query_one("#message", Static).update(Text(f"⎿ Cannot delete {name}: {reason}"))
            return

        def confirmed(choice: int | None) -> None:
            if choice == 1:
                self.exit(partial(commit_delete, profile=name))

        self.push_screen(ChoiceDialog(f"Delete {name}?", [f"Delete {name}", "Keep it"],
                                      f"Other homes that use {name} stop installing until they choose another "
                                      "profile with reconcile-skills.sh --profile <name>; this selector can't see "
                                      "them."), confirmed)

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
                (f"{skill_catalog.profile_path(self.profile)}:{line.number}", SECONDARY), "\n\n", line.error,
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
            self.refresh_hints()

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
        review = "\n".join(changes.lines()) or "No changes from the last commit."
        reason = self.handoff_blocker()
        if self.profile == self.active:
            if not changes and not self.draft.unsaved and not self.not_installed:
                self.query_one("#message", Static).update("⎿ No changes to save.")
                return
            install_label = "Save and install" if self.draft.unsaved else "Install"
            dialog = ChoiceDialog("Save these changes?", [install_label, "Save only", "Keep editing"], review,
                                  disabled={1: reason} if reason else None)
            afters = {1: After.INSTALL, 2: None}
        else:
            if not self.draft.unsaved and not self.not_committed:
                self.query_one("#message", Static).update("⎿ No changes to save.")
                return
            where = (f"This home uses {self.active}; saving {self.profile} doesn't change what's installed here."
                     if self.active else "This home has no profile yet.")
            dialog = ChoiceDialog(f"Save {self.profile}?",
                                  ["Save and commit (nothing installed here)", "Save, commit and use it here",
                                   "Save only"],
                                  f"{review}\n\n{where}", disabled={1: reason, 2: reason} if reason else None)
            afters = {1: After.NOTHING, 2: After.USE_HERE, 3: None}

        def save_choice(choice: int | None) -> None:
            if choice in afters:
                self.save_selection(after=afters[choice])

        self.push_screen(dialog, save_choice)

    def handoff_blocker(self) -> str | None:
        """Why Save cannot commit the edited profile, or install it when this home uses it, or None."""
        return (self.draft.install_blocker or reconcile_skills.selection_install_blocker(self.root, self.profile)
                or (self.install_error if self.profile == self.active and not self.draft.changes else None))

    def save_selection(self, overwrite: Conflict | None = None, *, after: After | None = None) -> None:
        """Save the draft, then close for the terminal handoff to commit it unless `after` is None (Save only)."""
        if after is not None and (reason := self.handoff_blocker()):
            action = "install" if after is After.INSTALL else "commit"
            self.query_one("#message", Static).update(Text(f"⎿ Cannot {action}: {reason}"))
            return
        try:
            outcome = self.draft.save(install=after is not None, overwrite=overwrite)
        except (reconcile_skills.ReconcileError, OSError) as error:
            self.query_one("#message", Static).update(Text(f"⎿ Could not save: {error}"))
            return
        if isinstance(outcome, Conflict):
            def conflict_choice(choice: int | None) -> None:
                if choice == 1:
                    self.reload_selection()
                elif choice == 2:
                    self.save_selection(overwrite=outcome, after=after)

            self.push_screen(ChoiceDialog(f"{skill_catalog.profile_path(self.profile)} changed on disk",
                                          ["Reload", "Overwrite"],
                                          "Reload drops your unsaved changes. Overwrite replaces the file."),
                             conflict_choice)
            return
        if isinstance(outcome, Blocked):
            self.query_one("#message", Static).update(Text("⎿ Cannot save: " + outcome.reason))
            return
        if after is not None:
            self.exit(partial(commit_profile, profile=self.profile, after=after))
            return
        self.refresh_file_status()
        self.refresh_title()
        self.query_one("#message", Static).update(
            "⎿ Saved. Not installed yet." if self.profile == self.active
            else Text(f"⎿ Saved {skill_catalog.profile_path(self.profile)}. Not committed."))

    def reload_selection(self) -> None:
        if not self.load_profile(self.profile, "Could not reload"):
            return
        self.query_one("#message", Static).update(
            Text(f"⎿ Reloaded {skill_catalog.profile_path(self.profile)}. Unsaved changes discarded."))

    def action_update(self) -> None:
        # The update refuses any profile file that differs from HEAD, so saving alone is not enough.
        then = "install" if self.profile == self.active else "commit"
        reason = (f"the selection has unsaved changes; save and {then} first" if self.draft.unsaved
                  else update_sources.update_blocker(self.root))
        message = self.query_one("#message", Static)
        if reason:
            message.update(Text(f"⎿ Cannot update sources: {reason}"))
        elif self.source not in self.selection_catalog.sources:
            message.update("⎿ Highlight a source to update it.")
        elif (marker := self.markers.get(self.source)) != skill_catalog.UPDATE_AVAILABLE:
            message.update(Text(f"⎿ {self.source}: {marker}" if marker else f"⎿ No update found for {self.source}"))
        else:
            self.push_screen(UpdateDialog(self.root, self.source), self.update_closed)

    def update_closed(self, pushed: bool | None) -> None:
        if not pushed:
            return
        self.markers.pop(self.source, None)
        try:
            self.selection_catalog = skill_catalog.load_catalog(self.root)
            self.catalog = display_catalog(self.selection_catalog)
            store = CheckoutStore(self.root, self.profile)
            self.draft = SelectionDraft.load(store, self.selection_catalog)
        except (reconcile_skills.ReconcileError, OSError) as error:
            self.query_one("#message", Static).update(Text(f"⎿ Could not reload: {error}"))
            return
        self.store = store
        self.refresh_file_status()
        self.refresh_title()
        self.fill_sources(keep_cursor=True)
        self.source = self.query_one("#sources", CatalogList).current
        self.fill_skills(keep_cursor=True)
        self.refresh_hints()

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
        self.refresh_hints()

    def refresh_hints(self) -> None:
        update = Text("ctrl+u update",
                      style=ACCENT if self.markers.get(self.source) == skill_catalog.UPDATE_AVAILABLE else SECONDARY)
        if self.focused is self.query_one("#search"):
            hint = Text("↓ or enter skills · esc clear or leave search · ctrl+s save · ctrl+p profiles")
        elif self.focused is self.query_one("#details"):
            hint = Text.assemble("↑↓ scroll · ← sources · → skills · ctrl+s save · ctrl+p profiles · ", update,
                                 " · esc quit")
        elif self.focused is self.query_one("#skills"):
            hint = Text.assemble("↑↓ move · space or enter toggle · ← sources · tab details · ctrl+s save · "
                                 "ctrl+p profiles · ", update, " · esc quit")
        else:
            hint = Text.assemble("↑↓ move · → skills · space toggle · ctrl+s save · ctrl+p profiles · ", update,
                                 " · esc quit")
        self.query_one("#hints", Static).update(hint)


def shell_path_env() -> dict[str, str]:
    selector_bin = Path(sys.prefix, "bin")
    path = os.environ.get("PATH", os.defpath).split(os.pathsep)
    return dict(os.environ, PATH=os.pathsep.join(entry for entry in path if Path(entry) != selector_bin))


class After(Enum):
    """What the terminal handoff does once the edited profile is committed."""

    NOTHING = auto()  # Save and commit: nothing is installed here.
    INSTALL = auto()  # Save and install the profile this home uses.
    USE_HERE = auto()  # Save, commit and use it here: reconcile-skills.sh --profile <name>.


def commit_profile(root: Path, profile: str, after: After) -> int:
    """Run only after the TUI closes, with hooks and signing in the terminal."""
    path = skill_catalog.profile_path(profile)
    if reason := reconcile_skills.selection_install_blocker(root, profile):
        print(f"Could not commit: {reason}. {path} stays saved; nothing was committed or installed.")
        return 1
    try:
        store = CheckoutStore(root, profile)
        changes = Changes.between(store.head(), store.read())
        message = f"Update {profile} profile ({changes.summary})"
        if not skill_catalog.git(["ls-files", "--", path], cwd=root):
            # A new profile: `commit --only` takes only paths Git already knows.
            skill_catalog.git(["add", "--", path], cwd=root)
            message = f"Add {profile} profile ({changes.summary})"
        # Match the checkout inspected by the catalog's Git reads, while
        # retaining terminal streams for hooks and interactive signing.
        if skill_catalog.selection_uncommitted(root, profile):
            commit = subprocess.run(
                ["git", "commit", "-m", message, "-m", "\n".join(changes.lines()), "--only", "--", path],
                cwd=root, env=skill_catalog.git_env(interactive=True), check=False,
            )
            if commit.returncode:
                print(f"Commit failed. {path} stays saved; nothing was committed or installed.")
                return commit.returncode
    except (reconcile_skills.ReconcileError, OSError) as error:
        print(f"Could not commit: {error}. {path} stays saved; nothing was committed or installed.")
        return 1
    return finish_handoff(root, profile, after)


def commit_rename(root: Path, profile: str, new: str, after: After) -> int:
    """Rename a committed profile with git mv and commit only that, then use it here if `after` says so."""
    path, new_path = skill_catalog.profile_path(profile), skill_catalog.profile_path(new)
    try:
        skill_catalog.git(["mv", "--", path, new_path], cwd=root)
        commit = subprocess.run(["git", "commit", "-m", f"Rename {profile} profile to {new}", "--only", "--",
                                 path, new_path], cwd=root, env=skill_catalog.git_env(interactive=True), check=False)
        if commit.returncode:
            skill_catalog.git(["mv", "--", new_path, path], cwd=root)
            print(f"Commit failed. {path} was not renamed.")
            return commit.returncode
    except (reconcile_skills.ReconcileError, OSError) as error:
        print(f"Could not rename: {error}")
        return 1
    return finish_handoff(root, new, after)


def commit_delete(root: Path, profile: str) -> int:
    """Delete a committed profile with git rm and commit only that."""
    path = skill_catalog.profile_path(profile)
    try:
        skill_catalog.git(["rm", "-q", "--", path], cwd=root)
        commit = subprocess.run(["git", "commit", "-m", f"Delete {profile} profile", "--only", "--", path],
                                cwd=root, env=skill_catalog.git_env(interactive=True), check=False)
        if commit.returncode:
            skill_catalog.git(["checkout", "HEAD", "--", path], cwd=root)
            print(f"Commit failed. {path} was not deleted.")
            return commit.returncode
    except (reconcile_skills.ReconcileError, OSError) as error:
        print(f"Could not delete: {error}")
        return 1
    return finish_handoff(root, profile, After.NOTHING)


def finish_handoff(root: Path, profile: str, after: After) -> int:
    """Install profile here as `after` says, once it is committed, and point out an unpushed commit."""
    code = 0
    if after is not After.NOTHING:
        command = [str(root / "scripts/reconcile-skills.sh")]
        if after is After.USE_HERE:
            command += ["--profile", profile]
        try:
            code = subprocess.run(command, cwd=root, env=shell_path_env(), check=False).returncode
        except OSError as error:
            print(f"Could not install: {error}. The selection commit was kept.")
            code = 1
    if skill_catalog.git(["rev-parse", "--verify", "@{upstream}"], cwd=root, check=False):
        if int(skill_catalog.git(["rev-list", "--count", "@{upstream}..HEAD"], cwd=root)):
            print("Not pushed yet. To back it up: git push")
    if code:
        print(f"run scripts/reconcile-skills.sh --profile {profile} again" if after is After.USE_HERE
              else "run the skill selector again and choose Install")
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
