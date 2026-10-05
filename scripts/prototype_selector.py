#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["textual==8.2.8", "pyyaml==6.0.3"]
# ///
"""PROTOTYPE - throwaway. Not the real selector; nothing here is tested or kept.

Question (ticket "How does the selector look and behave? (prototype)"):
how should the skill selector look and behave for someone who is not CLI-fluent?

Three structurally different variants on the real catalog, switchable live:
  A  Tree + reading pane      source > group > skill tree, description pane on the right
  B  Columns                  sources | skills | description, with a selection tray
  C  Search-first list        one flat table under a search box, with All / Selected / Changes tabs

F2 / F3 switch variant, F4 cycles the name-collision policy (block, swap, warn).
Saving writes nothing: the would-be skills.txt is printed when you quit.

    scripts/prototype_selector.py [--variant A|B|C] [--policy block|swap|warn]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from rich.text import Text  # noqa: E402
from textual import on  # noqa: E402
from textual.app import App, ComposeResult  # noqa: E402
from textual.binding import Binding  # noqa: E402
from textual.containers import Horizontal, Vertical, VerticalScroll  # noqa: E402
from textual.screen import ModalScreen, Screen  # noqa: E402
from textual.widgets import (  # noqa: E402
    Button, Checkbox, DataTable, Footer, Input, Markdown, OptionList, Static, Tabs, Tab, Tree,
)
from textual.widgets.option_list import Option  # noqa: E402
from textual.widgets._tree import TOGGLE_STYLE  # noqa: E402

from prototype_catalog import Skill, load_catalog, load_selection  # noqa: E402

POLICIES = ["block", "swap", "warn"]
POLICY_HELP = {
    "block": "can't check a second skill with the same name",
    "swap": "checking one unchecks the other",
    "warn": "both allowed, Save refused until fixed",
}
HEADER = "# Written by the skill selector. Comments and ordering are not kept."


# ---------------------------------------------------------------- model

class Model:
    def __init__(self, policy: str) -> None:
        self.catalog = load_catalog()
        self.by_key = {s.key: s for s in self.catalog}
        selected, self.missing = load_selection(self.catalog)
        # PROTOTYPE demo row so the "Not in catalog" group is visible.
        demo = "mattpocock/skills:old-demo-skill"
        selected.add(demo)
        self.missing[demo] = "missing at the pinned commit (demo row)"
        self.baseline = set(selected)
        self.selected = set(selected)
        self.policy = policy
        self.query = ""
        self.only_selected = False

    def name_of(self, key: str) -> str:
        return key.split(":", 1)[1]

    def source_of(self, key: str) -> str:
        return key.split(":", 1)[0]

    def rivals(self, key: str) -> list[str]:
        """Other skills, catalog or not, with the same name."""
        name = self.name_of(key)
        keys = [s.key for s in self.catalog] + list(self.missing)
        return [k for k in keys if k != key and self.name_of(k) == name]

    def conflicts(self) -> list[str]:
        seen: dict[str, list[str]] = {}
        for key in self.selected:
            seen.setdefault(self.name_of(key), []).append(key)
        return sorted(name for name, keys in seen.items() if len(keys) > 1)

    def in_conflict(self, key: str) -> bool:
        return key in self.selected and self.name_of(key) in self.conflicts()

    def taken_by(self, key: str) -> str | None:
        return next((k for k in self.rivals(key) if k in self.selected), None)

    def toggle(self, key: str) -> tuple[bool, str | None]:
        """Returns (changed, message for the user)."""
        if key in self.selected:
            self.selected.discard(key)
            return True, None
        if key in self.missing:
            return False, "Not in catalog: this skill can only be removed."
        other = self.taken_by(key)
        if other and self.policy == "block":
            return False, (f"Another skill named '{self.name_of(key)}' is already checked "
                           f"({self.source_of(other)}). Uncheck it first.")
        self.selected.add(key)
        if other and self.policy == "swap":
            self.selected.discard(other)
            return True, f"Swapped '{self.name_of(key)}': {self.source_of(other)} -> {self.source_of(key)}"
        if other and self.policy == "warn":
            return True, f"Two skills named '{self.name_of(key)}' are checked. Remove one before saving."
        return True, None

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
        if self.only_selected and skill.key not in self.selected:
            return False
        hay = f"{skill.name} {skill.source} {skill.group} {skill.description}".lower()
        return all(word in hay for word in self.query.lower().split())

    def missing_matches(self, key: str) -> bool:
        return all(word in key.lower() for word in self.query.lower().split())

    def file_text(self) -> str:
        return "\n".join([HEADER, *sorted(self.baseline)]) + "\n"

    def status(self) -> str:
        bits = [f"[b]{len(self.selected)}[/] selected"]
        if self.dirty:
            bits.append(f"[yellow]unsaved: [green]+{len(self.added)}[/] [red]-{len(self.removed)}[/][/]")
        else:
            bits.append("[dim]no unsaved changes[/]")
        if self.conflicts():
            bits.append(f"[red b]name conflict: {', '.join(self.conflicts())}[/]")
        return "  |  ".join(bits)


def box(model: Model, key: str) -> Text:
    if model.in_conflict(key):
        return Text("[!]", style="bold red")
    return Text("[x]" if key in model.selected else "[ ]", style="bold" if key in model.selected else "")


# ---------------------------------------------------------------- shared widgets

class SkillDetail(VerticalScroll):
    """Header fields plus the rendered SKILL.md body."""

    DEFAULT_CSS = """
    SkillDetail { padding: 0 1; }
    SkillDetail #head { padding-bottom: 1; border-bottom: solid $primary-darken-2; margin-bottom: 1; }
    """

    def compose(self) -> ComposeResult:
        yield Static(id="head")
        yield Markdown(id="body")

    def show_key(self, model: Model, key: str | None) -> None:
        head = self.query_one("#head", Static)
        body = self.query_one("#body", Markdown)
        self.scroll_home(animate=False)
        if key is None:
            head.update("[dim]Highlight a skill to read about it.[/]")
            body.update("")
            return
        if key in model.missing:
            head.update(f"[b]{model.name_of(key)}[/]   {self._state(model, key)}\n"
                        f"[cyan]{key}[/]\n\n[red]Not in catalog:[/] {model.missing[key]}\n"
                        "It stays in the selection until you uncheck it.")
            body.update("")
            return
        s = model.by_key[key]
        lines = [f"[b]{s.name}[/]   {self._state(model, key)}",
                 f"[cyan]{s.key}[/]",
                 f"[dim]{s.path}  ·  {s.file_count} files[/]"]
        if s.manual_only:
            lines.append("[yellow]Manual only: description not loaded into context[/]")
        for other in model.rivals(key):
            mark = "checked" if other in model.selected else "not checked"
            lines.append(f"[magenta]Same name in {model.source_of(other)} ({mark})[/]")
        lines.append("")
        lines.append(s.description or "[dim]No description.[/]")
        head.update("\n".join(lines))
        body.update(s.body)

    def show_text(self, text: str) -> None:
        self.query_one("#head", Static).update(text)
        self.query_one("#body", Markdown).update("")

    def _state(self, model: Model, key: str) -> str:
        if model.in_conflict(key):
            return "[red b][!] checked, name conflict[/]"
        if key in model.selected:
            return "[green b][x] checked[/]"
        return "[dim][ ] not checked[/]"


class ProtoBar(Static):
    """Not part of the design: switches variants and the collision policy."""

    DEFAULT_CSS = """
    ProtoBar { height: 1; background: $warning; color: $background; text-style: bold; padding: 0 1; }
    """

    def on_mount(self) -> None:
        self.refresh_bar()

    def refresh_bar(self) -> None:
        app: Selector = self.app  # type: ignore[assignment]
        key, title = VARIANTS[app.variant][0], VARIANTS[app.variant][1]
        self.update(
            f"PROTOTYPE  [@click=app.prev_variant]< F2[/]  Variant {key}: {title}  "
            f"[@click=app.next_variant]F3 >[/]    [@click=app.cycle_policy]F4 Collisions: "
            f"{app.model.policy}[/] ({POLICY_HELP[app.model.policy]})")


class StatusLine(Static):
    DEFAULT_CSS = "StatusLine { height: 1; padding: 0 1; background: $panel; }"


class VariantScreen(Screen):
    """Each variant implements refresh_all() and current_key()."""

    BINDINGS = [
        Binding("ctrl+s", "app.save", "Save", priority=True),
        Binding("escape", "escape", "Quit"),
    ]

    @property
    def model(self) -> Model:
        return self.app.model  # type: ignore[attr-defined]

    def action_escape(self) -> None:
        if isinstance(self.focused, Input) and self.focused.value:
            self.focused.value = ""
            return
        self.app.action_leave()  # type: ignore[attr-defined]

    def refresh_status(self) -> None:
        self.query_one(StatusLine).update(self.model.status())

    def refresh_all(self) -> None:
        raise NotImplementedError

    def toggle(self, key: str) -> None:
        changed, message = self.model.toggle(key)
        if message:
            self.notify(message, severity="warning" if not changed or "Remove" in message else "information")
        self.refresh_all()


# ---------------------------------------------------------------- variant A: tree

class SkillTree(Tree):
    BINDINGS = [
        Binding("space", "check", "Check / uncheck"),
        Binding("enter", "check", "Check / uncheck", show=False),
        Binding("right", "open", "Open group"),
        Binding("left", "close", "Close group"),
    ]

    def __init__(self) -> None:
        super().__init__("skills")
        self.show_root = False
        self.auto_expand = False

    @property
    def model(self) -> Model:
        return self.app.model  # type: ignore[attr-defined]

    def render_label(self, node, base_style, style):
        if not isinstance(node.data, str):
            return super().render_label(node, base_style, style)
        key = node.data
        label = Text.assemble(box(self.model, key))
        label.stylize(base_style + TOGGLE_STYLE)   # clicking the box toggles it
        name = Text(" " + self.model.name_of(key))
        name.stylize(style)
        label.append_text(name)
        skill = self.model.by_key.get(key)
        if skill and skill.manual_only:
            label.append("  manual", style="dim yellow")
        other = self.model.taken_by(key)
        if other and key not in self.model.selected:
            label.append(f"  name taken by {self.model.source_of(other)}", style="dim magenta")
        elif self.model.rivals(key) and key not in self.model.missing:
            label.append(f"  also in {self.model.source_of(self.model.rivals(key)[0])}", style="dim magenta")
        if key in self.model.missing:
            label.append(f"  {self.model.source_of(key)}: {self.model.missing[key]}", style="dim red")
        return label

    def _toggle_node(self, node) -> None:  # click on a box
        if isinstance(node.data, str):
            self.screen.toggle(node.data)  # type: ignore[attr-defined]
        else:
            super()._toggle_node(node)

    def action_check(self) -> None:
        node = self.cursor_node
        if node is None:
            return
        if isinstance(node.data, str):
            self.screen.toggle(node.data)  # type: ignore[attr-defined]
        else:
            node.toggle()

    def action_open(self) -> None:
        node = self.cursor_node
        if node and node.allow_expand:
            if node.is_expanded and node.children:
                self.move_cursor(node.children[0])
            node.expand()

    def action_close(self) -> None:
        node = self.cursor_node
        if node is None:
            return
        if node.allow_expand and node.is_expanded:
            node.collapse()
        elif node.parent is not None and node.parent is not self.root:
            self.move_cursor(node.parent)

    def on_key(self, event) -> None:
        # Typing goes straight to the search box.
        if event.character and event.character.isprintable() and event.character != " ":
            search = self.screen.query_one("#search", Input)
            search.focus()
            search.insert_text_at_cursor(event.character)
            event.stop()


class TreeScreen(VariantScreen):
    CSS = """
    #top { height: 3; }
    #search { width: 1fr; }
    #only { width: auto; }
    SkillTree { width: 45%; border: round $primary-darken-2; }
    SkillTree:focus { border: round $accent; }
    SkillDetail { border: round $primary-darken-2; }
    """
    BINDINGS = [
        Binding("f5", "only_selected", "Show only checked"),
        Binding("slash", "search", "Search", show=False),
    ]

    def compose(self) -> ComposeResult:
        yield StatusLine()
        with Horizontal(id="top"):
            yield Input(placeholder="Type to search names and descriptions", id="search", select_on_focus=False)
            yield Checkbox("Show only checked (F5)", id="only")
        with Horizontal():
            yield SkillTree()
            yield SkillDetail()
        yield ProtoBar()
        yield Footer()

    def on_mount(self) -> None:
        self.expanded: set[str] = set()
        self.query_one("#search", Input).value = self.model.query
        self.query_one("#only", Checkbox).value = self.model.only_selected
        self.rebuild()
        self.refresh_status()
        self.query_one(SkillTree).focus()

    def source_label(self, source: str) -> Text:
        every = [s for s in self.model.catalog if s.source == source]
        n_sel = sum(s.key in self.model.selected for s in every)
        return Text.assemble((source, "bold"), (f"  {n_sel}/{len(every)} checked", "dim"))

    def group_label(self, source: str, group: str) -> Text:
        in_group = [s for s in self.model.catalog if s.source == source and s.group == group]
        g_sel = sum(s.key in self.model.selected for s in in_group)
        return Text.assemble(group, (f"  {g_sel}/{len(in_group)}", "dim"))

    def relabel(self) -> None:
        tree = self.query_one(SkillTree)
        stack = list(tree.root.children)
        while stack:
            node = stack.pop()
            stack.extend(node.children)
            if isinstance(node.data, tuple) and node.data[0] == "src" and node.data[1] != "missing":
                node.set_label(self.source_label(node.data[1]))
            elif isinstance(node.data, tuple) and node.data[0] == "grp":
                source, group = node.data[1].rsplit("/", 1)
                node.set_label(self.group_label(source, group))
        tree._invalidate()

    def rebuild(self) -> None:
        tree = self.query_one(SkillTree)
        cursor = tree.cursor_node.data if tree.cursor_node else None
        tree.clear()
        filtering = bool(self.model.query.strip()) or self.model.only_selected
        target = None
        missing = [k for k in sorted(self.model.missing)
                   if self.model.missing_matches(k) and (not self.model.only_selected or k in self.model.selected)]
        if missing:
            node = tree.root.add(f"Not in catalog  ({len(missing)})", data=("src", "missing"), expand=True)
            for key in missing:
                leaf = node.add_leaf(key, data=key)
                target = leaf if key == cursor else target
        sources: dict[str, dict[str, list[Skill]]] = {}
        for s in self.model.catalog:
            if self.model.matches(s):
                sources.setdefault(s.source, {}).setdefault(s.group, []).append(s)
        for source, groups in sources.items():
            src = tree.root.add(self.source_label(source), data=("src", source),
                                expand=filtering or f"src:{source}" in self.expanded)
            for group, skills in groups.items():
                parent = src
                if group:
                    parent = src.add(self.group_label(source, group), data=("grp", f"{source}/{group}"),
                                     expand=filtering or f"grp:{source}/{group}" in self.expanded)
                for s in skills:
                    leaf = parent.add_leaf(s.name, data=s.key)
                    target = leaf if s.key == cursor else target
            if cursor == ("src", source):
                target = src
        if target is not None:
            self.call_after_refresh(tree.move_cursor, target)
        if not sources and not missing:
            tree.root.add_leaf(Text("No skills match.", style="dim"), data=("none", ""))

    def refresh_all(self) -> None:
        # Rows stay put after a toggle, even under "Show only checked", so nothing jumps.
        self.relabel()
        self.refresh_status()
        self.show_current()

    def show_current(self) -> None:
        tree = self.query_one(SkillTree)
        node = tree.cursor_node
        detail = self.query_one(SkillDetail)
        if node is None or node.data is None:
            detail.show_key(self.model, None)
        elif isinstance(node.data, str):
            detail.show_key(self.model, node.data)
        elif node.data[0] in ("src", "grp"):
            detail.show_text(f"[b]{node.label}[/]\n\n[dim]Right arrow or click the triangle to open, "
                             "left arrow to close. Highlight a skill to read about it.[/]")

    @on(Tree.NodeHighlighted)
    def highlighted(self, event: Tree.NodeHighlighted) -> None:
        self.show_current()

    @on(Tree.NodeSelected)
    def clicked(self, event: Tree.NodeSelected) -> None:
        # A click on a source or group name opens or closes it; a click on a skill only highlights it.
        if not isinstance(event.node.data, str):
            event.node.toggle()

    @on(Tree.NodeExpanded)
    def expanded_node(self, event: Tree.NodeExpanded) -> None:
        if isinstance(event.node.data, tuple):
            self.expanded.add(f"{event.node.data[0]}:{event.node.data[1]}")

    @on(Tree.NodeCollapsed)
    def collapsed_node(self, event: Tree.NodeCollapsed) -> None:
        if isinstance(event.node.data, tuple):
            self.expanded.discard(f"{event.node.data[0]}:{event.node.data[1]}")

    @on(Input.Changed, "#search")
    def search_changed(self, event: Input.Changed) -> None:
        self.model.query = event.value
        self.rebuild()

    @on(Input.Submitted, "#search")
    def search_done(self) -> None:
        self.query_one(SkillTree).focus()

    def on_key(self, event) -> None:
        if event.key == "down" and self.focused is self.query_one("#search"):
            self.query_one(SkillTree).focus()
            event.stop()

    @on(Checkbox.Changed, "#only")
    def only_changed(self, event: Checkbox.Changed) -> None:
        self.model.only_selected = event.value
        self.rebuild()

    def action_only_selected(self) -> None:
        box_ = self.query_one("#only", Checkbox)
        box_.value = not box_.value

    def action_search(self) -> None:
        self.query_one("#search", Input).focus()


# ---------------------------------------------------------------- variant B: columns

class Column(OptionList):
    # OptionList scrolls sideways on left/right; here they move between columns.
    BINDINGS = [
        Binding("right", "screen.next_column", "Skills column"),
        Binding("left", "screen.prev_column", "Sources column"),
    ]


class ColumnsScreen(VariantScreen):
    CSS = """
    #search { margin: 0 0; }
    #sources { width: 30; height: 1fr; border: round $primary-darken-2; }
    #skills { width: 1fr; height: 1fr; border: round $primary-darken-2; }
    #sources:focus, #skills:focus { border: round $accent; }
    SkillDetail { width: 1fr; border: round $primary-darken-2; }
    #tray { height: auto; max-height: 4; padding: 0 1; background: $boost; overflow-y: auto; }
    """
    BINDINGS = [
        Binding("space", "check", "Check / uncheck"),
        Binding("slash", "search", "Search", show=False),
    ]

    def compose(self) -> ComposeResult:
        yield StatusLine()
        yield Input(placeholder="Type to search all sources", id="search", select_on_focus=False)
        with Horizontal():
            yield Column(id="sources")
            yield Column(id="skills")
            yield SkillDetail()
        yield Static(id="tray")
        yield ProtoBar()
        yield Footer()

    def on_mount(self) -> None:
        self.source: str | None = None
        self.query_one("#search", Input).value = self.model.query
        self.query_one("#sources").border_title = "Sources"
        self.query_one("#skills").border_title = "Skills"
        self.fill_sources()
        self.query_one("#sources").focus()
        self.refresh_tray()
        self.refresh_status()

    def source_names(self) -> list[str]:
        names = sorted({s.source for s in self.model.catalog})
        return (["missing"] if self.model.missing else []) + names

    def fill_sources(self) -> None:
        ol = self.query_one("#sources", OptionList)
        keep = ol.highlighted
        options = []
        for src in self.source_names():
            if src == "missing":
                keys = [k for k in self.model.missing if self.model.missing_matches(k)]
                n_sel = sum(k in self.model.selected for k in self.model.missing)
                prompt = Text.assemble(("Not in catalog", "red"), (f"  {n_sel}", "dim"))
                options.append(Option(prompt, id="src:missing", disabled=not keys))
                continue
            every = [s for s in self.model.catalog if s.source == src]
            hits = [s for s in every if self.model.matches(s)]
            n_sel = sum(s.key in self.model.selected for s in every)
            owner, repo = src.split("/")
            prompt = Text.assemble((repo, "bold" if hits else "dim"))
            if self.model.query.strip():
                prompt.append(f"  {len(hits)} found", style="cyan" if hits else "dim")
            prompt.append(f"\n{owner} · {n_sel}/{len(every)} checked", style="dim")
            options.append(Option(prompt, id=f"src:{src}", disabled=not hits))
        ol.set_options(options)
        if keep is not None and keep < len(options):
            ol.highlighted = keep
        elif options:
            ol.highlighted = next((i for i, o in enumerate(options) if not o.disabled), 0)

    def fill_skills(self) -> None:
        ol = self.query_one("#skills", OptionList)
        keep = ol.highlighted
        options: list[Option | None] = []
        if self.source == "missing":
            for key in sorted(self.model.missing):
                prompt = Text.assemble(box(self.model, key), f" {self.model.name_of(key)}",
                                       (f"  {self.model.source_of(key)}", "dim"))
                options.append(Option(prompt, id=key))
        else:
            group = None
            for s in self.model.catalog:
                if s.source != self.source or not self.model.matches(s):
                    continue
                if s.group != group:
                    group = s.group
                    if group:
                        options.append(Option(Text(f"── {group} ", style="bold $accent"), disabled=True))
                prompt = Text.assemble(box(self.model, s.key), f" {s.name}")
                if s.manual_only:
                    prompt.append("  manual", style="dim yellow")
                other = self.model.taken_by(s.key)
                if other and s.key not in self.model.selected:
                    prompt.append(f"  name taken by {self.model.source_of(other)}", style="dim magenta")
                options.append(Option(prompt, id=s.key))
        if not options:
            options.append(Option(Text("No skills match.", style="dim"), disabled=True))
        ol.set_options(options)
        if keep is not None and keep < len(options) and not options[keep].disabled:
            ol.highlighted = keep
        else:
            ol.highlighted = next((i for i, o in enumerate(options) if o and not o.disabled), None)
        title = "Not in catalog" if self.source == "missing" else f"Skills in {self.source}"
        self.query_one("#skills").border_title = title if self.source else "Skills"

    def refresh_tray(self) -> None:
        names = sorted(self.model.selected, key=self.model.name_of)
        parts = []
        for key in names:
            style = "red" if self.model.in_conflict(key) else ("green" if key in self.model.added else "")
            parts.append(f"[@click=screen.jump('{key}')][{style}]{self.model.name_of(key)}[/][/]"
                         if style else f"[@click=screen.jump('{key}')]{self.model.name_of(key)}[/]")
        removed = [f"[red strike]{self.model.name_of(k)}[/]" for k in self.model.removed]
        self.query_one("#tray", Static).update(
            f"[b]Checked ({len(names)}):[/] " + " · ".join(parts + removed)
            + "   [dim](click a name to jump to it)[/]")

    def refresh_all(self) -> None:
        self.fill_sources()
        self.fill_skills()
        self.refresh_tray()
        self.refresh_status()
        self.show_current()

    def current_key(self) -> str | None:
        ol = self.query_one("#skills", OptionList)
        opt = ol.highlighted_option
        return opt.id if opt and opt.id else None

    def show_current(self) -> None:
        detail = self.query_one(SkillDetail)
        if self.focused is self.query_one("#sources") or self.current_key() is None:
            src = self.source
            if src and src != "missing":
                every = [s for s in self.model.catalog if s.source == src]
                groups = sorted({s.group for s in every if s.group})
                detail.show_text(f"[b]{src}[/]\n{len(every)} skills, "
                                 f"{sum(s.key in self.model.selected for s in every)} checked\n"
                                 + (f"Groups: {', '.join(groups)}\n" if groups else "")
                                 + "\n[dim]Right arrow or click to see its skills.[/]")
            else:
                detail.show_key(self.model, None)
        else:
            detail.show_key(self.model, self.current_key())

    @on(OptionList.OptionHighlighted, "#sources")
    def source_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        new = event.option.id.removeprefix("src:") if event.option.id else None
        if new != self.source:
            self.source = new
            self.query_one("#skills", OptionList).highlighted = None
            self.fill_skills()
        self.show_current()

    @on(OptionList.OptionSelected, "#sources")
    def source_selected(self) -> None:
        self.query_one("#skills").focus()

    @on(OptionList.OptionHighlighted, "#skills")
    def skill_highlighted(self) -> None:
        self.show_current()

    @on(OptionList.OptionSelected, "#skills")
    def skill_selected(self, event: OptionList.OptionSelected) -> None:
        # Enter or a click toggles the checkbox in this variant.
        if event.option.id:
            self.toggle(event.option.id)

    def on_descendant_focus(self) -> None:
        if hasattr(self, "source"):
            self.show_current()

    def action_check(self) -> None:
        if self.focused is self.query_one("#skills") and self.current_key():
            self.toggle(self.current_key())

    def action_next_column(self) -> None:
        if self.focused is self.query_one("#sources"):
            self.query_one("#skills").focus()

    def action_prev_column(self) -> None:
        if self.focused is self.query_one("#skills"):
            self.query_one("#sources").focus()

    def action_search(self) -> None:
        self.query_one("#search").focus()

    def action_jump(self, key: str) -> None:
        self.model.query = ""
        self.query_one("#search", Input).value = ""
        src = "missing" if key in self.model.missing else self.model.source_of(key)
        sources = self.query_one("#sources", OptionList)
        sources.highlighted = sources.get_option_index(f"src:{src}")
        self.source = src
        self.fill_skills()
        skills = self.query_one("#skills", OptionList)
        skills.highlighted = skills.get_option_index(key)
        skills.focus()

    @on(Input.Changed, "#search")
    def search_changed(self, event: Input.Changed) -> None:
        self.model.query = event.value
        self.fill_sources()
        sources = self.query_one("#sources", OptionList)
        opt = sources.highlighted_option
        if opt is None or opt.disabled:
            first = next((i for i, o in enumerate(sources.options) if not o.disabled), None)
            sources.highlighted = first
        self.fill_skills()

    @on(Input.Submitted, "#search")
    def search_done(self) -> None:
        self.query_one("#skills").focus()

    def on_key(self, event) -> None:
        search = self.query_one("#search", Input)
        if event.key == "down" and self.focused is search:
            self.query_one("#skills").focus()
            event.stop()
        elif (self.focused is not search and event.character and event.character.isprintable()
              and event.character != " "):
            search.focus()
            search.insert_text_at_cursor(event.character)
            event.stop()


# ---------------------------------------------------------------- variant C: search-first list

class ListScreen(VariantScreen):
    CSS = """
    #search { border: tall $accent; }
    DataTable { height: 1fr; }
    SkillDetail { height: 45%; border-top: heavy $primary-darken-2; }
    """
    BINDINGS = [
        Binding("enter", "check", "Check / uncheck", priority=True),
        Binding("space", "space", "Check / uncheck", show=False),
        Binding("tab", "next_tab", "Next tab", priority=True),
        Binding("shift+tab", "prev_tab", "Previous tab", show=False, priority=True),
    ]

    def compose(self) -> ComposeResult:
        yield StatusLine()
        yield Input(placeholder="Search by name, source or description", id="search", select_on_focus=False)
        yield Tabs(Tab("All", id="all"), Tab("Checked", id="checked"), Tab("Changes", id="changes"))
        yield DataTable(cursor_type="row", zebra_stripes=True)
        yield SkillDetail()
        yield ProtoBar()
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.add_column("", key="box", width=5)
        table.add_column("Skill", key="name", width=30)
        table.add_column("Source", key="source", width=28)
        table.add_column("Group", key="group", width=18)
        table.add_column("Description", key="desc")
        self.query_one("#search", Input).value = self.model.query
        self.fill()
        self.refresh_status()
        self.query_one("#search").focus()

    @property
    def tab(self) -> str:
        active = self.query_one(Tabs).active
        return active or "all"

    def rows(self) -> list[str]:
        keys: list[str] = []
        if self.tab == "changes":
            return self.model.added + self.model.removed
        for key in sorted(self.model.missing):
            if self.model.missing_matches(key) and (self.tab != "checked" or key in self.model.selected):
                keys.append(key)
        for s in self.model.catalog:
            if self.tab == "checked" and s.key not in self.model.selected:
                continue
            if self.model.matches(s):
                keys.append(s.key)
        return keys

    def fill(self) -> None:
        table = self.query_one(DataTable)
        current = self.current_key()
        table.clear()
        added, removed = set(self.model.added), set(self.model.removed)
        for key in self.rows():
            s = self.model.by_key.get(key)
            mark = box(self.model, key)
            if key in added:
                mark = Text.assemble(mark, (" +", "green bold"))
            elif key in removed:
                mark = Text.assemble(mark, (" -", "red bold"))
            name = Text(self.model.name_of(key))
            if s and s.manual_only:
                name.append(" manual", style="dim yellow")
            other = self.model.taken_by(key)
            if other and key not in self.model.selected:
                name.append(" taken", style="dim magenta")
            if s:
                desc = Text(s.description, style="dim", overflow="ellipsis", no_wrap=True)
                group = s.group
            else:
                desc = Text(self.model.missing[key], style="red")
                group = "Not in catalog"
            table.add_row(mark, name, self.model.source_of(key), group, desc, key=key)
        if table.row_count == 0:
            msg = "No changes to save." if self.tab == "changes" else "No skills match."
            table.add_row("", Text(msg, style="dim"), "", "", "", key="__none__")
        if current and current in table.rows:
            table.move_cursor(row=table.get_row_index(current), animate=False)
        tabs = self.query_one(Tabs)
        tabs.query_one("#all", Tab).label = f"All ({len(self.model.catalog) + len(self.model.missing)})"
        tabs.query_one("#checked", Tab).label = f"Checked ({len(self.model.selected)})"
        tabs.query_one("#changes", Tab).label = (f"Changes (+{len(self.model.added)} -{len(self.model.removed)})"
                                                 if self.model.dirty else "Changes")
        self.show_current()

    def current_key(self) -> str | None:
        table = self.query_one(DataTable)
        if table.row_count == 0:
            return None
        key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value
        return None if key == "__none__" else key

    def show_current(self) -> None:
        detail = self.query_one(SkillDetail)
        if self.tab == "changes" and self.model.dirty and self.current_key() is None:
            detail.show_text("Press [b]ctrl+s[/] to save these changes.")
        else:
            detail.show_key(self.model, self.current_key())

    def refresh_all(self) -> None:
        self.fill()
        self.refresh_status()

    @on(DataTable.RowHighlighted)
    def row_highlighted(self) -> None:
        self.show_current()

    @on(DataTable.RowSelected)
    def row_selected(self, event: DataTable.RowSelected) -> None:
        # Enter or a click on a row toggles it.
        if event.row_key.value != "__none__":
            self.toggle(event.row_key.value)

    @on(Tabs.TabActivated)
    def tab_changed(self) -> None:
        if self.is_mounted and self.query(DataTable):
            self.fill()
            if self.tab == "changes" and self.model.dirty:
                self.notify("These are your unsaved changes. Press ctrl+s to save them.")

    @on(Input.Changed, "#search")
    def search_changed(self, event: Input.Changed) -> None:
        self.model.query = event.value
        self.fill()

    def action_check(self) -> None:
        # Space types into the search box; Enter always toggles the highlighted row.
        if isinstance(self.focused, Input):
            return
        if self.current_key():
            self.toggle(self.current_key())

    def action_space(self) -> None:
        if isinstance(self.focused, Input):
            self.focused.insert_text_at_cursor(" ")
        elif self.current_key():
            self.toggle(self.current_key())

    def action_next_tab(self) -> None:
        self.query_one(Tabs).action_next_tab()

    def action_prev_tab(self) -> None:
        self.query_one(Tabs).action_previous_tab()

    def on_key(self, event) -> None:
        search = self.query_one("#search", Input)
        table = self.query_one(DataTable)
        if self.focused is search:
            # Arrows and Enter drive the list while you type.
            if event.key in ("up", "down", "pageup", "pagedown"):
                getattr(table, {"up": "action_cursor_up", "down": "action_cursor_down",
                                "pageup": "action_page_up", "pagedown": "action_page_down"}[event.key])()
                event.stop()
        elif event.character and event.character.isprintable() and event.character != " ":
            search.focus()
            search.insert_text_at_cursor(event.character)
            event.stop()


# ---------------------------------------------------------------- leaving: review and quit

class ReviewScreen(ModalScreen[bool]):
    CSS = """
    ReviewScreen { align: center middle; }
    #dialog { width: 72; height: auto; max-height: 80%; border: thick $accent; background: $surface; padding: 1 2; }
    #changes { height: auto; max-height: 20; }
    #buttons { height: auto; margin-top: 1; align-horizontal: right; }
    Button { margin-left: 2; }
    """
    BINDINGS = [Binding("escape", "cancel", "Back")]

    def __init__(self, model: Model) -> None:
        super().__init__()
        self.model = model

    def compose(self) -> ComposeResult:
        m = self.model
        lines = []
        lines += [f"[green]+ {m.name_of(k)}[/]  [dim]{m.source_of(k)}[/]" for k in m.added]
        lines += [f"[red]- {m.name_of(k)}[/]  [dim]{m.source_of(k)}[/]" for k in m.removed]
        conflicts = m.conflicts()
        with Vertical(id="dialog"):
            yield Static(f"[b]Save these changes to skills.txt?[/]\n"
                         f"{len(m.added)} added, {len(m.removed)} removed, {len(m.selected)} checked in total.\n")
            yield VerticalScroll(Static("\n".join(lines)), id="changes")
            if conflicts:
                yield Static(f"\n[red b]Can't save yet:[/] more than one skill is checked for "
                             f"{', '.join(conflicts)}. Uncheck one of each.")
            with Horizontal(id="buttons"):
                yield Button("Back to list", id="back")
                yield Button("Save", id="save", variant="primary", disabled=bool(conflicts))

    def on_mount(self) -> None:
        self.query_one("#back" if self.model.conflicts() else "#save", Button).focus()

    @on(Button.Pressed)
    def pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "save")

    def action_cancel(self) -> None:
        self.dismiss(False)


class QuitScreen(ModalScreen[str]):
    CSS = """
    QuitScreen { align: center middle; }
    #dialog { width: 72; height: auto; border: thick $warning; background: $surface; padding: 1 2; }
    #buttons { height: auto; margin-top: 1; align-horizontal: right; }
    Button { margin-left: 2; }
    """
    BINDINGS = [Binding("escape", "keep", "Keep editing")]

    def __init__(self, model: Model) -> None:
        super().__init__()
        self.model = model

    def compose(self) -> ComposeResult:
        m = self.model
        with Vertical(id="dialog"):
            yield Static(f"[b]You have unsaved changes[/]  ([green]+{len(m.added)}[/] [red]-{len(m.removed)}[/])\n"
                         "Quitting now throws them away.")
            with Horizontal(id="buttons"):
                yield Button("Keep editing", id="keep")
                yield Button("Quit without saving", id="discard", variant="error")
                yield Button("Review and save", id="save", variant="primary")

    def on_mount(self) -> None:
        self.query_one("#keep", Button).focus()

    @on(Button.Pressed)
    def pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id)

    def action_keep(self) -> None:
        self.dismiss("keep")


# ---------------------------------------------------------------- app

VARIANTS = [
    ("A", "Tree + reading pane", TreeScreen),
    ("B", "Columns + selection tray", ColumnsScreen),
    ("C", "Search-first list with tabs", ListScreen),
]


class Selector(App):
    TITLE = "Skill selector (PROTOTYPE)"
    BINDINGS = [
        Binding("f2", "prev_variant", "Prev variant", show=False),
        Binding("f3", "next_variant", "Next variant", show=False),
        Binding("f4", "cycle_policy", "Collision policy", show=False),
        Binding("ctrl+q", "leave", "Quit", show=False, priority=True),
    ]

    def __init__(self, variant: int, policy: str) -> None:
        super().__init__()
        self.variant = variant
        self.model = Model(policy)
        self.saves = 0

    def on_mount(self) -> None:
        self.push_screen(VARIANTS[self.variant][2]())

    def show_variant(self, index: int) -> None:
        self.variant = index % len(VARIANTS)
        self.switch_screen(VARIANTS[self.variant][2]())

    def action_prev_variant(self) -> None:
        self.show_variant(self.variant - 1)

    def action_next_variant(self) -> None:
        self.show_variant(self.variant + 1)

    def action_cycle_policy(self) -> None:
        self.model.policy = POLICIES[(POLICIES.index(self.model.policy) + 1) % len(POLICIES)]
        self.screen.query_one(ProtoBar).refresh_bar()
        self.screen.refresh_all()  # type: ignore[attr-defined]
        self.notify(f"Collisions: {self.model.policy}: {POLICY_HELP[self.model.policy]}")

    def action_save(self) -> None:
        if not self.model.dirty:
            self.notify("Nothing to save.")
            return
        if isinstance(self.screen, ListScreen) and self.screen.tab != "changes":
            # Variant C reviews in its Changes tab instead of a dialog.
            self.screen.query_one(Tabs).active = "changes"
            return
        if isinstance(self.screen, ListScreen):
            if self.model.conflicts():
                self.notify(f"Can't save: more than one skill is checked for {', '.join(self.model.conflicts())}.",
                            severity="error")
                return
            self.commit()
            return
        self.push_screen(ReviewScreen(self.model), self.after_review)

    def after_review(self, save: bool | None) -> None:
        if save:
            self.commit()

    def commit(self) -> None:
        count = len(self.model.added) + len(self.model.removed)
        self.model.baseline = set(self.model.selected)
        self.saves += 1
        self.screen.refresh_all()  # type: ignore[attr-defined]
        self.notify(f"Saved {count} changes to skills.txt (PROTOTYPE: nothing was written).", title="Saved")

    def action_leave(self) -> None:
        if not isinstance(self.screen, VariantScreen):
            return
        if not self.model.dirty:
            self.exit()
            return
        self.push_screen(QuitScreen(self.model), self.after_quit)

    def after_quit(self, choice: str | None) -> None:
        if choice == "discard":
            self.exit()
        elif choice == "save":
            self.action_save()


def main() -> None:
    parser = argparse.ArgumentParser(description="PROTOTYPE skill selector")
    parser.add_argument("--variant", choices="ABC", default="A")
    parser.add_argument("--policy", choices=POLICIES, default="block")
    args = parser.parse_args()
    app = Selector("ABC".index(args.variant), args.policy)
    app.run()
    print(f"PROTOTYPE: saved {app.saves} time(s). The skills.txt the selector would have written:\n")
    print(Model.file_text(app.model) if app.saves else "(not saved)")


if __name__ == "__main__":
    main()
