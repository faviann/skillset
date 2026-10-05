#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["textual==8.2.8", "pyyaml==6.0.3"]
# ///
"""PROTOTYPE - throwaway. Not the real selector; nothing here is tested or kept.

Round 2 of "How does the selector look and behave? (prototype)".
Decided in round 1: the columns layout (sources | skills | description), click the
circle to toggle and the name to read, a review before saving, a confirmation on
quit, and swapping same-name skills after a confirmation.

Open in round 2: the visual style. Two looks of the same screen, switchable live:
  code   Claude Code: the terminal's own background, no boxes, orange accent
  app    Claude app: warm dark palette, tinted cards

F2 / F3 switch look. Saving writes nothing: the would-be skills.txt is printed on quit.

    scripts/prototype_selector.py [--look code|app]
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from rich.text import Text  # noqa: E402
from textual import on  # noqa: E402
from textual.app import App, ComposeResult  # noqa: E402
from textual.binding import Binding  # noqa: E402
from textual.containers import Horizontal, Vertical, VerticalScroll  # noqa: E402
from textual.geometry import Region  # noqa: E402
from textual.message import Message  # noqa: E402
from textual.screen import ModalScreen, Screen  # noqa: E402
from textual.theme import Theme  # noqa: E402
from textual.widgets import Input, Markdown, Static  # noqa: E402

from prototype_catalog import Skill, load_catalog, load_selection  # noqa: E402

HEADER = "# Written by the skill selector. Comments and ordering are not kept."

# Claude Code's dark palette, and claude.ai's dark palette.
LOOKS = {
    "code": dict(title="Claude Code", accent="#D77757", text="#E8E8E8", dim="#999999", faint="#505050",
                 ok="#4EBA65", bad="#FF6B80", warn="#FFC107"),
    "app": dict(title="Claude app", accent="#D97757", text="#F5F4EE", dim="#A6A39A", faint="#5E5D59",
                ok="#8DBB8F", bad="#E5737F", warn="#E8B866"),
}
MARKDOWN = {"markdown-h2-text-style": "bold", "markdown-h3-text-style": "bold",
            "markdown-h1-background": "transparent", "markdown-h2-background": "transparent",
            "markdown-h3-background": "transparent"}
THEMES = [
    Theme(name="claude-code", primary="#D77757", accent="#D77757", foreground="#E8E8E8",
          background="#000000", surface="#000000", panel="#000000", dark=True,
          variables={**MARKDOWN, "dim": "#999999", "faint": "#505050", "raised": "#262626",
                     "card": "#000000", "line": "#505050", "markdown-h1-color": "#D77757",
                     "markdown-h2-color": "#E8E8E8", "markdown-h3-color": "#E8E8E8",
                     "input-cursor-background": "#D77757"}),
    Theme(name="claude-app", primary="#D97757", accent="#D97757", foreground="#F5F4EE",
          background="#262624", surface="#30302E", panel="#1F1E1D", dark=True,
          variables={**MARKDOWN, "dim": "#A6A39A", "faint": "#5E5D59", "raised": "#30302E",
                     "card": "#1F1E1D", "line": "#3D3D3A", "markdown-h1-color": "#D97757",
                     "markdown-h2-color": "#F5F4EE", "markdown-h3-color": "#F5F4EE",
                     "input-cursor-background": "#D97757"}),
]

CSS = """
#top { height: 1; padding: 0 2; margin-top: 1; }
#searchbox { height: 3; margin: 1 2 0 2; padding: 0 1; border: round $faint; }
#searchbox:focus-within { border: round $accent; }
#prompt { width: 2; color: $accent; }
#search { border: none; padding: 0; height: 1; background: transparent; }
#search:focus { border: none; background-tint: transparent; }
#search > .input--placeholder { color: $dim; }
#columns { margin: 1 1 0 1; }
.column { padding: 0 1; }
#col-sources { width: 32; }
#col-skills { width: 1fr; min-width: 30; }
#col-detail { width: 1.3fr; }
.colhead { height: 1; color: $dim; margin-bottom: 1; }
PickList, SkillDetail {
    background: transparent;
    scrollbar-size-vertical: 1;
    scrollbar-background: transparent;
    scrollbar-background-hover: transparent;
    scrollbar-background-active: transparent;
    scrollbar-color: $faint;
    scrollbar-color-hover: $dim;
    scrollbar-color-active: $accent;
}
PickList:focus { background-tint: transparent; }
SkillDetail #head { padding-bottom: 1; }
SkillDetail Markdown { background: transparent; padding: 0; margin: 0; }
MarkdownH1 { content-align: left middle; }
MarkdownFence { background: $raised; }
#msg { height: 1; padding: 0 2; }
#hints { height: 1; padding: 0 2; color: $dim; margin-bottom: 1; }
ProtoBar { height: 1; background: #6d28d9; color: #ffffff; padding: 0 2; }

/* Claude app: tinted cards instead of open space */
SelectorScreen.look-app #searchbox { background: $raised; border: round $line; }
SelectorScreen.look-app #searchbox:focus-within { border: round $accent; }
SelectorScreen.look-app .column { background: $card; padding: 1 2; margin: 0 1; }
SelectorScreen.look-app #col-detail { background: $raised; }
"""


def palette(app: App) -> dict:
    return LOOKS[app.look]  # type: ignore[attr-defined]


# ---------------------------------------------------------------- model

class Model:
    def __init__(self) -> None:
        self.catalog = load_catalog()
        self.by_key = {s.key: s for s in self.catalog}
        selected, self.missing = load_selection(self.catalog)
        # PROTOTYPE demo row so the "Not in catalog" group is visible.
        demo = "mattpocock/skills:old-demo-skill"
        selected.add(demo)
        self.missing[demo] = "missing at the pinned commit (demo row)"
        self.baseline = set(selected)
        self.selected = set(selected)
        self.query = ""

    @staticmethod
    def name_of(key: str) -> str:
        return key.split(":", 1)[1]

    @staticmethod
    def source_of(key: str) -> str:
        return key.split(":", 1)[0]

    def rivals(self, key: str) -> list[str]:
        name = self.name_of(key)
        keys = [s.key for s in self.catalog] + list(self.missing)
        return [k for k in keys if k != key and self.name_of(k) == name]

    def taken_by(self, key: str) -> str | None:
        return next((k for k in self.rivals(key) if k in self.selected), None)

    def conflicts(self) -> list[str]:
        """Only a hand-edited file can hold two skills with one name; swapping prevents it."""
        names = [self.name_of(k) for k in self.selected]
        return sorted({n for n in names if names.count(n) > 1})

    @property
    def added(self) -> list[str]:
        return sorted(self.selected - self.baseline)

    @property
    def removed(self) -> list[str]:
        return sorted(self.baseline - self.selected)

    @property
    def dirty(self) -> bool:
        return self.selected != self.baseline

    def matches(self, skill: Skill) -> bool:
        hay = f"{skill.name} {skill.source} {skill.group} {skill.description}".lower()
        return all(word in hay for word in self.query.lower().split())

    def file_text(self) -> str:
        return "\n".join([HEADER, *sorted(self.baseline)]) + "\n"


# ---------------------------------------------------------------- list widget

@dataclass
class Row:
    key: str | None              # None for headers and spacers
    lines: list[Text] = field(default_factory=list)
    has_box: bool = False


class RowsView(Static):
    DEFAULT_CSS = "RowsView { width: 100%; text-wrap: nowrap; text-overflow: ellipsis; }"

    def on_click(self, event) -> None:
        self.parent.clicked(event.y, event.x)  # type: ignore[union-attr]


class PickList(VerticalScroll, can_focus=True):
    """A plain list with a pointer, drawn as text so it can look like anything."""

    BINDINGS = [
        Binding("up", "move(-1)", show=False),
        Binding("down", "move(1)", show=False),
        Binding("pageup", "move(-10)", show=False),
        Binding("pagedown", "move(10)", show=False),
        Binding("home", "move(-9999)", show=False),
        Binding("end", "move(9999)", show=False),
    ]

    class Highlighted(Message):
        def __init__(self, picklist: PickList, key: str | None) -> None:
            super().__init__()
            self.picklist, self.key = picklist, key

        @property
        def control(self) -> PickList:
            return self.picklist

    class BoxClicked(Message):
        def __init__(self, picklist: PickList, key: str) -> None:
            super().__init__()
            self.picklist, self.key = picklist, key

        @property
        def control(self) -> PickList:
            return self.picklist

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.rows: list[Row] = []
        self.cursor: int | None = None

    def compose(self) -> ComposeResult:
        yield RowsView()

    @property
    def current(self) -> str | None:
        return self.rows[self.cursor].key if self.cursor is not None else None

    def set_rows(self, rows: list[Row], keep: str | None = None) -> None:
        old = keep if keep is not None else self.current
        self.rows = rows
        items = [i for i, r in enumerate(rows) if r.key]
        self.cursor = next((i for i in items if rows[i].key == old), items[0] if items else None)
        self.redraw()
        self.post_message(self.Highlighted(self, self.current))

    def redraw(self) -> None:
        p = palette(self.app)
        out: list[Text] = []
        for i, row in enumerate(self.rows):
            for n, line in enumerate(row.lines):
                if not row.key:
                    out.append(line)
                    continue
                here = i == self.cursor
                if here and n == 0:
                    lead = Text("❯ ", style=f"bold {p['accent']}" if self.has_focus else p["dim"])
                else:
                    lead = Text("  ")
                body = line.copy()
                if here and self.has_focus and n == 0:
                    body.stylize("bold")
                out.append(lead + body)
        text = Text("\n").join(out) if out else Text("")
        text.no_wrap, text.overflow = True, "ellipsis"
        self.query_one(RowsView).update(text)

    def scroll_to_cursor(self) -> None:
        if self.cursor is None:
            return
        y = sum(len(r.lines) for r in self.rows[:self.cursor])
        self.scroll_to_region(Region(0, max(0, y - 1), 1, len(self.rows[self.cursor].lines) + 2),
                              animate=False, immediate=True)

    def action_move(self, delta: int) -> None:
        items = [i for i, r in enumerate(self.rows) if r.key]
        if not items:
            return
        pos = items.index(self.cursor) if self.cursor in items else 0
        self.cursor = items[max(0, min(len(items) - 1, pos + delta))]
        self.redraw()
        self.scroll_to_cursor()
        self.post_message(self.Highlighted(self, self.current))

    def clicked(self, y: int, x: int) -> None:
        line = 0
        for i, row in enumerate(self.rows):
            if line <= y < line + len(row.lines):
                if row.key:
                    self.focus()
                    if self.cursor != i:
                        self.cursor = i
                        self.redraw()
                        self.post_message(self.Highlighted(self, self.current))
                    if row.has_box and x <= 3:   # the circle, after the pointer
                        self.post_message(self.BoxClicked(self, row.key))
                return
            line += len(row.lines)

    def on_focus(self) -> None:
        self.redraw()

    def on_blur(self) -> None:
        self.redraw()


# ---------------------------------------------------------------- detail pane

class SkillDetail(VerticalScroll):
    def compose(self) -> ComposeResult:
        yield Static(id="head")
        yield Markdown(id="body")

    def show(self, text: Text, body: str = "") -> None:
        self.query_one("#head", Static).update(text)
        self.query_one("#body", Markdown).update(body)
        self.scroll_home(animate=False)


# ---------------------------------------------------------------- dialogs

class Choices(Static):
    def on_click(self, event) -> None:
        self.screen.action_pick(event.y)  # type: ignore[attr-defined]


class ChoiceDialog(ModalScreen[str | None]):
    """A Claude Code style prompt: a title, some text, numbered choices."""

    DEFAULT_CSS = """
    ChoiceDialog { align: center middle; background: $background 50%; }
    ChoiceDialog.look-code { align: center bottom; background: $background 0%; }
    ChoiceDialog #box { width: 76; height: auto; max-height: 90%; padding: 1 2; border: round $accent;
                        background: $background; }
    ChoiceDialog.look-code #box { width: 100%; margin: 0 1 2 1; background: ansi_default; }
    ChoiceDialog.look-app #box { border: round $line; background: $raised; }
    ChoiceDialog #title { text-style: bold; color: $accent; margin-bottom: 1; }
    ChoiceDialog #text { margin-bottom: 1; }
    ChoiceDialog Choices { height: auto; }
    ChoiceDialog #foot { color: $dim; margin-top: 1; }
    """
    BINDINGS = [
        Binding("up", "move(-1)", show=False),
        Binding("down", "move(1)", show=False),
        Binding("enter", "pick", show=False),
        Binding("escape", "cancel", show=False),
    ] + [Binding(str(n), f"pick({n - 1})", show=False) for n in range(1, 10)]

    def __init__(self, look: str, title: str, text: Text, choices: list[tuple[str, str]], default: int = 0) -> None:
        super().__init__(classes=f"look-{look}")
        self.look, self.title_, self.text, self.choices, self.index = look, title, text, choices, default

    def compose(self) -> ComposeResult:
        with Vertical(id="box"):
            yield Static(self.title_, id="title")
            if self.text.plain:
                yield Static(self.text, id="text")
            yield Choices()
            yield Static("Enter to confirm · Esc to cancel", id="foot")

    def on_mount(self) -> None:
        self.redraw()

    def redraw(self) -> None:
        accent = LOOKS[self.look]["accent"]
        lines = []
        for i, (_, label) in enumerate(self.choices):
            if i == self.index:
                lines.append(Text(f"❯ {i + 1}. {label}", style=f"bold {accent}"))
            else:
                lines.append(Text(f"  {i + 1}. {label}"))
        self.query_one(Choices).update(Text("\n").join(lines))

    def action_move(self, delta: int) -> None:
        self.index = (self.index + delta) % len(self.choices)
        self.redraw()

    def action_pick(self, index: int | None = None) -> None:
        if index is not None:
            if not 0 <= index < len(self.choices):
                return
            self.index = index
        self.dismiss(self.choices[self.index][0])

    def action_cancel(self) -> None:
        self.dismiss(None)


# ---------------------------------------------------------------- main screen

class ProtoBar(Static):
    """Not part of the design: switches the look."""

    def refresh_bar(self) -> None:
        look = self.app.look  # type: ignore[attr-defined]
        names = list(LOOKS)
        self.update(f"PROTOTYPE   [@click=app.prev_look]◀ F2[/]  look {names.index(look) + 1}/{len(names)}: "
                    f"{LOOKS[look]['title']}  [@click=app.next_look]F3 ▶[/]")


class SelectorScreen(Screen):
    BINDINGS = [
        Binding("ctrl+s", "save", priority=True),
        Binding("escape", "escape"),
        Binding("space", "toggle"),
        Binding("enter", "enter"),
        Binding("right", "column(1)"),
        Binding("left", "column(-1)"),
        Binding("slash", "search"),
    ]

    @property
    def model(self) -> Model:
        return self.app.model  # type: ignore[attr-defined]

    @property
    def p(self) -> dict:
        return palette(self.app)

    def compose(self) -> ComposeResult:
        yield Static(id="top")
        with Horizontal(id="searchbox"):
            yield Static(">", id="prompt")
            yield Input(placeholder="Search skills by name or description", id="search", select_on_focus=False)
        with Horizontal(id="columns"):
            with Vertical(id="col-sources", classes="column"):
                yield Static("Sources", classes="colhead")
                yield PickList(id="sources")
            with Vertical(id="col-skills", classes="column"):
                yield Static("Skills", classes="colhead", id="head-skills")
                yield PickList(id="skills")
            with Vertical(id="col-detail", classes="column"):
                yield SkillDetail()
        yield Static(id="msg")
        yield Static(id="hints")
        yield ProtoBar()

    def on_mount(self) -> None:
        self.source: str | None = None
        self.msg_timer = None
        self.restyle()
        self.query_one("#sources", PickList).focus()

    # ----- drawing

    def restyle(self) -> None:
        look = self.app.look  # type: ignore[attr-defined]
        self.set_class(look == "app", "look-app")
        self.set_class(look == "code", "look-code")
        self.refresh_all()

    def refresh_all(self) -> None:
        self.fill_sources()
        self.fill_skills()
        self.refresh_top()
        self.refresh_hints()
        self.show_detail()
        self.query_one(ProtoBar).refresh_bar()

    def refresh_top(self) -> None:
        p, m = self.p, self.model
        t = Text.assemble(("✻ ", p["accent"]), ("Skill selector", "bold"), ("   ", ""),
                          (f"{len(m.selected)} selected", p["dim"]))
        if m.dirty:
            t.append("  ·  ", style=p["faint"])
            n = len(m.added) + len(m.removed)
            t.append(f"{n} unsaved change{'s' if n != 1 else ''}", style=p["warn"])
        if m.conflicts():
            t.append("  ·  ", style=p["faint"])
            t.append(f"two skills named {', '.join(m.conflicts())}", style=p["bad"])
        self.query_one("#top", Static).update(t)

    def refresh_hints(self) -> None:
        focused = self.focused
        if isinstance(focused, Input):
            hint = "type to filter · ↓ results · esc clear"
        elif focused is self.query_one("#skills"):
            hint = "↑↓ move · space select · ← sources · / search · ctrl+s save · esc quit"
        else:
            hint = "↑↓ move · → skills · / search · ctrl+s save · esc quit"
        self.query_one("#hints", Static).update(hint)

    def say(self, text: str, tone: str = "accent") -> None:
        msg = self.query_one("#msg", Static)
        msg.update(Text.assemble(("⎿  ", self.p["faint"]), (text, self.p[tone])))
        if self.msg_timer:
            self.msg_timer.stop()
        self.msg_timer = self.set_timer(6, lambda: msg.update(""))

    def box(self, key: str) -> Text:
        p = self.p
        if key in self.model.selected:
            clash = self.model.name_of(key) in self.model.conflicts()
            return Text("● ", style=p["bad"] if clash else p["accent"])
        return Text("○ ", style=p["faint"])

    def fill_sources(self) -> None:
        p, m = self.p, self.model
        rows: list[Row] = []
        if m.missing:
            n = sum(k in m.selected for k in m.missing)
            rows.append(Row("missing", [Text("Not in catalog", style=p["bad"]),
                                        Text(f"{n} selected", style=p["dim"])]))
            rows.append(Row(None, [Text("")]))
        for src in sorted({s.source for s in m.catalog}):
            every = [s for s in m.catalog if s.source == src]
            hits = sum(m.matches(s) for s in every)
            owner, repo = src.split("/")
            n_sel = sum(s.key in m.selected for s in every)
            first = Text(repo, style="" if hits else p["faint"])
            if m.query.strip():
                first.append(f"  {hits}", style=p["accent"] if hits else p["faint"])
            second = Text(owner, style=p["dim"] if hits else p["faint"])
            second.append(f" · {n_sel}/{len(every)}", style=p["dim"] if n_sel else p["faint"])
            rows.append(Row(src, [first, second]))
            rows.append(Row(None, [Text("")]))
        self.query_one("#sources", PickList).set_rows(rows[:-1], keep=self.source)

    def fill_skills(self) -> None:
        p, m = self.p, self.model
        rows: list[Row] = []
        if self.source == "missing":
            for key in sorted(m.missing):
                rows.append(Row(key, [self.box(key) + Text(m.name_of(key))], has_box=True))
            title = "Not in catalog"
        else:
            group = None
            for s in m.catalog:
                if s.source != self.source or not m.matches(s):
                    continue
                if s.group != group:
                    group = s.group
                    if group:
                        if rows:
                            rows.append(Row(None, [Text("")]))
                        rows.append(Row(None, [Text(f"  {group}", style=p["dim"])]))
                line = self.box(s.key) + Text(s.name)
                if s.manual_only:
                    line.append("  manual", style=p["faint"])
                other = m.taken_by(s.key)
                if other and s.key not in m.selected:
                    line.append(f"  replaces {m.source_of(other).split('/')[1]}", style=p["faint"])
                rows.append(Row(s.key, [line], has_box=True))
            title = self.source or "Skills"
        if not rows:
            rows.append(Row(None, [Text("  Nothing matches.", style=p["dim"])]))
        self.query_one("#skills", PickList).set_rows(rows)
        self.query_one("#head-skills", Static).update(title)

    def show_detail(self) -> None:
        p, m = self.p, self.model
        detail = self.query_one(SkillDetail)
        key = self.query_one("#skills", PickList).current
        if self.focused is self.query_one("#sources") or key is None:
            src = self.source
            if src is None or src == "missing":
                detail.show(Text("Skills that are selected but no longer exist at the pinned commit. "
                                 "They stay selected until you deselect them.", style=p["dim"]))
                return
            every = [s for s in m.catalog if s.source == src]
            groups = sorted({s.group for s in every if s.group})
            t = Text.assemble((src, "bold"), "\n",
                              (f"{len(every)} skills · {sum(s.key in m.selected for s in every)} selected", p["dim"]))
            if groups:
                t.append("\n\n" + " · ".join(groups), style=p["dim"])
            detail.show(t)
            return
        t = Text()
        t.append(m.name_of(key), style=f"bold {p['accent']}")
        t.append("\n" + key, style=p["dim"])
        if key in m.missing:
            t.append(f"\n\nNot in catalog: {m.missing[key]}.", style=p["bad"])
            t.append("\nIt stays selected until you deselect it.", style=p["dim"])
            detail.show(t)
            return
        s = m.by_key[key]
        t.append("\n\n")
        t.append_text(Text("● Selected", style=p["accent"]) if key in m.selected
                      else Text("○ Not selected", style=p["dim"]))
        if s.manual_only:
            t.append("   Manual only: its description is not loaded into context", style=p["warn"])
        for other in m.rivals(key):
            state = "selected" if other in m.selected else "not selected"
            t.append(f"\nSame name in {m.source_of(other)} ({state})", style=p["dim"])
        t.append("\n\n")
        t.append(s.description or "No description.", style=p["text"])
        t.append(f"\n\n{s.path} · {s.file_count} files", style=p["faint"])
        t.append("\n" + "─" * 24, style=p["faint"])
        detail.show(t, s.body)

    # ----- events

    @on(PickList.Highlighted, "#sources")
    def source_moved(self, event: PickList.Highlighted) -> None:
        if event.key != self.source:
            self.source = event.key
            self.fill_skills()
        self.show_detail()

    @on(PickList.Highlighted, "#skills")
    def skill_moved(self) -> None:
        self.show_detail()

    @on(PickList.BoxClicked)
    def box_clicked(self, event: PickList.BoxClicked) -> None:
        self.toggle(event.key)

    def on_descendant_focus(self) -> None:
        if hasattr(self, "source"):
            self.refresh_hints()
            self.show_detail()

    @on(Input.Changed, "#search")
    def search_changed(self, event: Input.Changed) -> None:
        m = self.model
        m.query = event.value
        if event.value.strip() and not any(m.matches(s) for s in m.catalog if s.source == self.source):
            # Move to the first source with a match.
            self.source = next((s.source for s in m.catalog if m.matches(s)), self.source)
        self.fill_sources()
        self.fill_skills()
        self.show_detail()

    def on_key(self, event) -> None:
        search = self.query_one("#search", Input)
        if self.focused is search:
            if event.key in ("down", "enter"):
                self.query_one("#skills").focus()
                event.stop()
            return
        if event.character and event.character.isprintable() and event.character not in " /":
            search.focus()
            search.insert_text_at_cursor(event.character)
            event.stop()

    # ----- actions

    def toggle(self, key: str) -> None:
        m = self.model
        if key in m.selected:
            m.selected.discard(key)
            self.refresh_all()
            return
        if key in m.missing:
            self.say("Not in catalog: it can only be deselected.", "warn")
            return
        other = m.taken_by(key)
        if other is None:
            m.selected.add(key)
            self.refresh_all()
            return
        name = m.name_of(key)
        text = Text.assemble((m.source_of(other), "bold"),
                             f" already provides {name}. Only one skill named {name} can be installed.")

        def done(choice: str | None) -> None:
            if choice == "swap":
                m.selected.discard(other)
                m.selected.add(key)
                self.refresh_all()
                self.say(f"{name} now comes from {m.source_of(key)} instead of {m.source_of(other)}.")

        self.app.push_screen(ChoiceDialog(self.app.look, f"Replace {name}?", text,  # type: ignore[attr-defined]
                                          [("swap", f"Yes, use {m.source_of(key)}"),
                                           ("keep", f"No, keep {m.source_of(other)}")]), done)

    def action_toggle(self) -> None:
        skills = self.query_one("#skills", PickList)
        if self.focused is skills and skills.current:
            self.toggle(skills.current)

    def action_enter(self) -> None:
        if self.focused is self.query_one("#sources"):
            self.query_one("#skills").focus()
        else:
            self.action_toggle()

    def action_column(self, delta: int) -> None:
        if not isinstance(self.focused, Input):
            self.query_one("#skills" if delta > 0 else "#sources").focus()

    def action_search(self) -> None:
        self.query_one("#search").focus()

    def action_escape(self) -> None:
        search = self.query_one("#search", Input)
        if search.value:
            search.value = ""
            self.query_one("#skills").focus()
        elif self.focused is search:
            self.query_one("#skills").focus()
        else:
            self.action_quit()

    def action_save(self, then_quit: bool = False) -> None:
        m, p = self.model, self.p
        if not m.dirty:
            self.say("No changes to save.", "dim")
            return
        lines = [Text.assemble(("+ ", p["ok"]), (m.name_of(k), p["ok"]), (f"  {m.source_of(k)}", p["dim"]))
                 for k in m.added]
        lines += [Text.assemble(("- ", p["bad"]), (m.name_of(k), p["bad"]), (f"  {m.source_of(k)}", p["dim"]))
                  for k in m.removed]
        if m.conflicts():
            lines.append(Text(f"\nCan't save: two skills named {', '.join(m.conflicts())} are selected. "
                              "Deselect one first.", style=p["bad"]))
            choices = [("back", "Back to the list")]
        else:
            choices = [("save", "Save to skills.txt"), ("back", "Keep editing")]

        def done(choice: str | None) -> None:
            if choice == "save":
                count = len(m.added) + len(m.removed)
                m.baseline = set(m.selected)
                self.app.saves += 1  # type: ignore[attr-defined]
                self.refresh_all()
                self.say(f"Saved {count} changes to skills.txt (PROTOTYPE: nothing was written).", "ok")
                if then_quit:
                    self.app.exit()

        self.app.push_screen(ChoiceDialog(self.app.look, "Save these changes?",  # type: ignore[attr-defined]
                                          Text("\n").join(lines), choices), done)

    def action_quit(self) -> None:
        m = self.model
        if not m.dirty:
            self.app.exit()
            return

        def done(choice: str | None) -> None:
            if choice == "discard":
                self.app.exit()
            elif choice == "save":
                self.action_save(then_quit=True)

        n = len(m.added) + len(m.removed)
        self.app.push_screen(ChoiceDialog(self.app.look, "Quit without saving?",  # type: ignore[attr-defined]
                                          Text(f"You have {n} unsaved change{'s' if n != 1 else ''}."),
                                          [("keep", "Keep editing"), ("save", "Review and save"),
                                           ("discard", "Quit without saving")]), done)


# ---------------------------------------------------------------- app

class Selector(App):
    TITLE = "Skill selector (PROTOTYPE)"
    CSS = CSS
    ENABLE_COMMAND_PALETTE = False
    BINDINGS = [
        Binding("f2", "prev_look", show=False),
        Binding("f3", "next_look", show=False),
        Binding("ctrl+q", "screen.quit", show=False, priority=True),
    ]

    def __init__(self, look: str) -> None:
        super().__init__()
        self.look = look
        self.model = Model()
        self.saves = 0
        for theme in THEMES:
            self.register_theme(theme)
        self.theme = self.theme_name()

    def theme_name(self) -> str:
        return "claude-code" if self.look == "code" else "claude-app"

    def on_mount(self) -> None:
        self.ansi_color = self.look == "code"
        self.push_screen(SelectorScreen())

    def action_prev_look(self) -> None:
        self.cycle_look(-1)

    def action_next_look(self) -> None:
        self.cycle_look(1)

    def cycle_look(self, delta: int) -> None:
        names = list(LOOKS)
        self.look = names[(names.index(self.look) + delta) % len(names)]
        self.theme = self.theme_name()
        self.ansi_color = self.look == "code"
        if isinstance(self.screen, SelectorScreen):
            self.screen.restyle()


def main() -> None:
    parser = argparse.ArgumentParser(description="PROTOTYPE skill selector, round 2")
    parser.add_argument("--look", choices=list(LOOKS), default="code")
    args = parser.parse_args()
    app = Selector(args.look)
    app.run()
    print(f"PROTOTYPE: saved {app.saves} time(s). The skills.txt the selector would have written:\n")
    print(app.model.file_text() if app.saves else "(not saved)")


if __name__ == "__main__":
    main()
