#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11,<3.15"
# dependencies = ["textual==8.2.8", "pyyaml==6.0.3"]
# ///
"""PROTOTYPE, throwaway: how select-skills shows, switches and manages profiles.

Three layouts of the real selector, switched with F2/F3, over profiles held in memory:
  A  Title + picker    the edited profile is named in the title; ctrl+p opens a profile picker
  B  Profiles column   a Profiles column left of Sources; ← from Sources reaches it
  C  Tab strip         profiles as tabs under the title; [ and ] switch; ctrl+p for profile actions

Nothing is written, committed or installed: Save and every profile operation only say what they would do.
F6 simulates another process editing the edited profile's file. F7 toggles a fresh home with no profile.

    scripts/prototype_selector_profiles.py --root ~/repos/skillset
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
spec = importlib.util.spec_from_file_location("selector", HERE / "select-skills.py")
sel = sys.modules["selector"] = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sel)

import skill_catalog
from rich.text import Text
from selection_draft import Blocked, Conflict, MemoryStore, SelectionDraft
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.events import Key
from textual.screen import ModalScreen
from textual.widgets import Input, Static

ACCENT, SECONDARY = sel.ACCENT, sel.SECONDARY
CatalogList, Row, ChoiceDialog = sel.CatalogList, sel.Row, sel.ChoiceDialog
VARIANTS = {"A": "Title + picker", "B": "Profiles column", "C": "Tab strip"}
HEADER = "# Written by the skill selector. Comments and ordering are not kept.\n"


@dataclass
class State:
    """Survives F2/F3 restarts; stands in for profiles/*.txt, HEAD and ~/.agents/.skillset/profile."""

    stores: dict[str, MemoryStore] = field(default_factory=dict)
    active: str | None = "workstation"
    editing: str = "workstation"
    variant: str = "A"
    dropdown: bool = True
    faint: bool = True
    message: str = ""


def seed(root: Path) -> State:
    text = skill_catalog.read_selection_text(root)
    values = [line.value for line in skill_catalog.parse_selection(text)]
    pick = lambda *names: HEADER + "".join(f"{v}\n" for v in values if v.partition(":")[2] in names)
    state = State()
    for name, content in {
        "workstation": text,
        "llm": pick("grilling", "domain-modeling", "research", "prototype", "tdd", "wayfinder", "code-review"),
        "zeroshot-implement": pick("tdd", "diagnosing-bugs", "codebase-design"),
        "zeroshot-review": pick("code-review", "research"),
    }.items():
        state.stores[name] = MemoryStore(content, content)
    return state


def name_problem(name: str, state: State, current: str | None = None) -> str | None:
    if len(name) > 64 or not skill_catalog.NAME_PATTERN.fullmatch(name):
        return "use 1–64 lowercase letters, digits or single hyphens"
    if name in state.stores and name != current:
        return f"{name} already exists"
    return None


class NameDialog(ModalScreen[str | None]):
    """Ask for a profile name, validated with the skill-name rules."""

    DEFAULT_CSS = ChoiceDialog.DEFAULT_CSS.replace("ChoiceDialog", "NameDialog") + """
    #name-box { height: 3; border: round #999999; }
    #name-input { height: 1; padding: 0; border: none; background: transparent; }
    #name-error { height: auto; color: red; }
    """
    BINDINGS = [Binding("escape", "cancel", show=False)]

    def __init__(self, title: str, details: str, state: State, value: str = "", current: str | None = None):
        super().__init__()
        self.heading, self.details, self.state, self.value, self.current = title, details, state, value, current

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static(Text(self.heading), id="dialog-title")
            if self.details:
                yield Static(Text(self.details), id="dialog-details")
            with Horizontal(id="name-box"):
                yield Static("> ", id="search-prompt")
                yield Input(value=self.value, placeholder="profile name", id="name-input")
            yield Static(id="name-error")
            yield Static("Enter to confirm · Esc to cancel", id="dialog-hint")

    @on(Input.Submitted)
    def submitted(self, event: Input.Submitted) -> None:
        name = event.value.strip()
        if problem := name_problem(name, self.state, self.current):
            self.query_one("#name-error", Static).update(Text(problem))
        else:
            self.dismiss(name)

    def action_cancel(self) -> None:
        self.dismiss(None)


class ProfilePicker(ModalScreen[tuple[str, str] | None]):
    """Variant A: every profile, which one is used here, and the operations on them."""

    DEFAULT_CSS = ChoiceDialog.DEFAULT_CSS.replace("ChoiceDialog", "ProfilePicker").replace(
        "#choices { height: auto; max-height: 6; }", "#choices { height: auto; max-height: 16; }")
    BINDINGS = [Binding("escape", "cancel", show=False), Binding("enter", "pick('edit')", show=False),
                *(Binding(key, f"pick('{op}')", show=False)
                  for key, op in [("n", "new"), ("c", "copy"), ("r", "rename"), ("d", "delete"), ("u", "use")])]

    def __init__(self, app: ProfilesApp) -> None:
        super().__init__()
        self.owner = app

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static(Text("Profiles"), id="dialog-title")
            yield CatalogList(activate_on_click=True, id="choices")
            yield Static("enter edit · u use here · n new · c copy · r rename · d delete · esc close",
                         id="dialog-hint")

    def on_mount(self) -> None:
        choices = self.query_one("#choices", CatalogList)
        choices.set_rows(self.owner.profile_rows(two_lines=False))
        choices.cursor = next(i for i, row in enumerate(choices.rows) if row.key == self.owner.state.editing)
        choices.redraw()
        choices.focus()

    @on(CatalogList.Activated, "#choices")
    def activated(self) -> None:
        self.action_pick("edit")

    def action_pick(self, op: str) -> None:
        self.dismiss((op, self.query_one("#choices", CatalogList).current))

    def action_cancel(self) -> None:
        self.dismiss(None)


class ProfileDropdown(ProfilePicker):
    """The same picker, dropped down under the profile name in the title instead of docked at the bottom."""

    DEFAULT_CSS = """
    ProfileDropdown { align: left top; background: transparent; }
    #dialog { width: auto; height: auto; margin: 2 0 0 18; padding: 0 1; border: round #D77757;
              background: ansi_default; }
    #choices { height: auto; max-height: 16; width: 100%; }
    #dialog-hint { height: 2; margin-top: 1; color: #999999; }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield CatalogList(activate_on_click=True, id="choices")
            yield Static("enter edit · u use here · esc close\nn new · c copy · r rename · d delete",
                         id="dialog-hint")

    def on_mount(self) -> None:
        super().on_mount()
        widest = max(sel.cell_len(line.plain) for row in self.query_one("#choices", CatalogList).rows
                     for line in row.lines)
        # Pointer 2, padding 2, border 2; never narrower than the two-line hint.
        self.query_one("#dialog").styles.width = max(widest + 6, 40)

    def on_click(self, event) -> None:
        if not self.query_one("#dialog").region.contains(event.screen_x, event.screen_y):
            self.dismiss(None)


class ProfilesApp(sel.SelectorApp):
    CSS = sel.SelectorApp.CSS + """
    /* The app's `Screen { background: ansi_default }` also paints every dialog screen opaque,
       which hides the selector behind it. A transparent dialog screen lets it show through. */
    ModalScreen { background: transparent; }
    #profile-column { border-right: solid #999999; width: 26; }
    #tabs { height: 1; margin: 0 2 1 2; text-wrap: nowrap; text-overflow: ellipsis; }
    #proto { height: 1; padding: 0 2; background: #D77757; color: black; text-wrap: nowrap; }
    """
    BINDINGS = [*sel.SelectorApp.BINDINGS,
                Binding("f2", "variant(-1)", show=False), Binding("f3", "variant(1)", show=False),
                Binding("f4", "placement", show=False), Binding("f5", "faint", show=False),Binding("f6", "external_edit", show=False), Binding("f7", "fresh_home", show=False),
                Binding("ctrl+p", "profiles", show=False)]

    def __init__(self, root: Path, state: State) -> None:
        self.state = state
        super().__init__(root)
        self.variant = state.variant
        self.SUSPENDED_SCREEN_CLASS = "-screen-suspended" if state.faint else ""
        self.load_draft()

    # ---- state -------------------------------------------------------------------------------

    @property
    def store(self) -> MemoryStore:
        return self.state.stores[self.state.editing]

    @property
    def editing_active(self) -> bool:
        return self.state.editing == self.state.active

    def load_draft(self) -> None:
        self.draft = SelectionDraft.load(self.store, self.selection_catalog)
        self.not_installed = False
        self.source = sel.NOT_IN_CATALOG if self.draft.off_catalog else next(iter(self.catalog), None)

    def uncommitted(self, name: str) -> bool:
        store = self.state.stores[name]
        return store.disk != store.head()

    def check_install_status(self) -> None:
        self.call_from_thread(self.show_install_status, self.editing_active and self.uncommitted(self.state.editing),
                              None)

    # ---- layout ------------------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Static(id="title")
        if self.variant == "C":
            yield Static(id="tabs")
        with Horizontal(id="search-box"):
            yield Static(">", id="search-prompt")
            yield Input(placeholder="Search skills", select_on_focus=False, id="search")
        with Horizontal(id="columns"):
            if self.variant == "B":
                with Vertical(id="profile-column", classes="column"):
                    yield Static("Profiles", classes="heading")
                    yield CatalogList(activate_on_click=True, id="profiles")
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
                yield sel.SkillBody(id="skill-body")
        yield Static(id="message")
        yield Static(id="hints")
        yield Static(id="proto")

    def on_mount(self) -> None:
        super().on_mount()
        self.refresh_profiles()
        if self.state.message:
            self.say(self.state.message)
            self.state.message = ""
        if self.state.active is None:
            self.call_after_refresh(self.choose_for_fresh_home)

    def say(self, message: str) -> None:
        self.query_one("#message", Static).update(Text(f"⎿ {message}"))

    def profile_rows(self, *, two_lines: bool) -> list:
        rows = []
        for name, store in self.state.stores.items():
            count = len(skill_catalog.parse_selection(store.disk))
            edited = name == self.state.editing
            label = Text(name, style=f"bold {ACCENT}" if edited else "default")
            notes = []
            if name == self.state.active:
                notes.append(("● used here", ACCENT))
            notes.append((f"{count} skills", SECONDARY))
            if edited and self.draft.unsaved:
                notes.append((f"{self.draft.unsaved} unsaved", ACCENT))
            elif self.uncommitted(name):
                notes.append(("not committed", SECONDARY))
            info = Text("  ").join(Text(text, style=style) for text, style in notes)
            pad = Text(" " * (max(map(len, self.state.stores)) - len(name) + 3))
            rows.append(Row(name, [label, Text("  ") + info] if two_lines else [label + pad + info]))
        return rows

    def refresh_title(self) -> None:
        name = self.state.editing
        if self.variant == "A":
            where = (("  · used here", ACCENT) if self.editing_active
                     else ("  · this home has no profile", ACCENT) if self.state.active is None
                     else (f"  · this home uses {self.state.active}", SECONDARY))
            head = [("✻ ", ACCENT), ("Skill selector  ", "bold"), (name, f"bold {ACCENT}"), where]
        else:
            head = [("✻ ", ACCENT), ("Skill selector", "bold")]
        status = f"   {len(self.draft)} selected · {self.draft.unsaved} unsaved"
        tail = (" · not installed" if self.not_installed else
                " · not committed" if not self.editing_active and self.uncommitted(name) else "")
        self.query_one("#title", Static).update(Text.assemble(*head, (status, SECONDARY), (tail, ACCENT)))

    def refresh_profiles(self) -> None:
        self.refresh_title()
        if self.variant == "B":
            profiles = self.query_one("#profiles", CatalogList)
            keep = profiles.current
            profiles.set_rows(self.profile_rows(two_lines=True)
                              + [Row("+new", [Text(""), Text("+ New profile", style=SECONDARY)])])
            profiles.cursor = next((i for i, row in enumerate(profiles.rows) if row.key == (keep or self.state.editing)),
                                   0)
            profiles.redraw()
        if self.variant == "C":
            parts = []
            for name in self.state.stores:
                style = f"bold underline {ACCENT}" if name == self.state.editing else SECONDARY
                mark = Text(" ●", style=ACCENT) if name == self.state.active else Text("")
                unsaved = Text(f" {self.draft.unsaved}*", style=ACCENT) if (
                    name == self.state.editing and self.draft.unsaved) else Text("")
                parts.append(Text(name, style=style) + mark + unsaved)
            legend = Text("    ● used here", style=SECONDARY)
            self.query_one("#tabs", Static).update(Text("   ").join(parts) + legend)
        proto = Text.assemble(
            ("PROTOTYPE  ", "bold"), f"F2 ◀ {self.variant} · {VARIANTS[self.variant]} ▶ F3",
            f"   F4 picker: {'dropdown' if self.state.dropdown else 'bottom'}" if self.variant == "A" else "",
            f"   F5 behind dialogs: {'faint' if self.state.faint else 'unchanged'}",
            "   F6 edit file on disk · F7 fresh home",
        )
        self.query_one("#proto", Static).update(proto)

    def refresh_selection(self) -> None:
        super().refresh_selection()
        self.refresh_profiles()

    def refresh_hints(self) -> None:
        super().refresh_hints()
        extra = {"A": "ctrl+p profiles", "C": "[ ] switch profile · ctrl+p profile actions"}.get(self.variant)
        if self.variant == "B" and self.focused is self.query_one("#profiles"):
            hint = Text("↑↓ move · enter edit · u use here · n new · c copy · r rename · d delete · → sources · "
                        "esc quit")
            self.query_one("#hints", Static).update(hint)
        elif self.variant == "B" and self.focused is self.query_one("#sources"):
            self.query_one("#hints", Static).update(Text.assemble(
                "↑↓ move · ← profiles · → skills · ctrl+s save · esc quit"))
        elif extra:
            hints = self.query_one("#hints", Static)
            hints.update(Text.assemble(extra, " · ", self.base_hint()))

    def base_hint(self) -> Text:
        if self.focused is self.query_one("#skills"):
            return Text("↑↓ move · space toggle · ← sources · ctrl+s save · esc quit")
        return Text("↑↓ move · → skills · ctrl+s save · esc quit")

    # ---- variant and simulation keys -----------------------------------------------------------

    def action_variant(self, delta: int) -> None:
        keys = list(VARIANTS)
        self.state.variant = keys[(keys.index(self.variant) + delta) % len(keys)]
        if self.draft.unsaved:
            self.state.message = "Prototype: switching layout dropped the unsaved draft."
        self.exit("restart")

    def action_faint(self) -> None:
        self.state.faint = not self.state.faint
        # Textual's own ANSI rule renders a suspended screen with text-style: dim.
        self.SUSPENDED_SCREEN_CLASS = "-screen-suspended" if self.state.faint else ""
        self.refresh_profiles()
        self.say(f"Behind dialogs: {'faint' if self.state.faint else 'unchanged'}. ctrl+p to open.")

    def action_placement(self) -> None:
        self.state.dropdown = not self.state.dropdown
        self.refresh_profiles()
        where = "drops down under the title" if self.state.dropdown else "docks at the bottom"
        self.say(f"Picker now {where}. ctrl+p to open.")

    def action_external_edit(self) -> None:
        self.store.disk += "# edited elsewhere\n"
        self.say(f"Simulated: profiles/{self.state.editing}.txt changed on disk. Save to see the conflict.")

    def action_fresh_home(self) -> None:
        self.state.active = None if self.state.active else "workstation"
        self.state.message = ("Simulated a fresh home: no ~/.agents/.skillset/profile." if self.state.active is None
                              else "Simulated: this home uses workstation.")
        self.exit("restart")

    def choose_for_fresh_home(self) -> None:
        names = list(self.state.stores)

        def chosen(choice: int | None) -> None:
            if choice is None:
                self.say("Browsing without a profile for this home. Saving offers to use the edited one here.")
            elif choice == len(names) + 1:
                self.new_profile(copy_from=None, use_here=True)
            else:
                self.use_here(names[choice - 1], edit=True)

        self.push_screen(BigChoice(
            "This home has no profile yet. Which one should it use?", [*names, "New profile…"],
            "The choice is saved in ~/.agents/.skillset/profile on this machine only, "
            "then that profile is installed.\nEsc to browse first and decide when you save.",
        ), chosen)

    # ---- switching and profile operations ----------------------------------------------------

    def on_key(self, event: Key) -> None:
        if self.screen.is_modal or self.query_one("#search", Input).has_focus:
            return super().on_key(event)
        if self.variant == "C" and event.character in ("[", "]"):
            names = list(self.state.stores)
            self.switch_to(names[(names.index(self.state.editing) + (1 if event.character == "]" else -1))
                                 % len(names)])
        elif self.variant == "B" and self.focused is self.query_one("#profiles") and event.character in tuple("ncrdu"):
            ops = {"n": "new", "c": "copy", "r": "rename", "d": "delete", "u": "use"}
            self.profile_op(ops[event.character], self.query_one("#profiles", CatalogList).current)
        else:
            return super().on_key(event)
        event.stop()
        event.prevent_default()

    def action_column(self, column: str) -> None:
        if self.variant == "B" and column == "sources" and self.focused is self.query_one("#sources"):
            column = "profiles"
        super().action_column(column)

    def action_enter(self) -> None:
        if self.variant == "B" and self.focused is self.query_one("#profiles"):
            self.profile_activated()
        else:
            super().action_enter()

    @on(CatalogList.Activated, "#profiles")
    def profile_activated(self) -> None:
        key = self.query_one("#profiles", CatalogList).current
        self.profile_op("new" if key == "+new" else "edit", key)

    def action_profiles(self) -> None:
        if self.variant == "A":
            picker = ProfileDropdown if self.state.dropdown else ProfilePicker
            self.push_screen(picker(self), lambda picked: picked and self.profile_op(*picked))
        elif self.variant == "C":
            name = self.state.editing
            ops = ["use", "new", "copy", "rename", "delete"]
            labels = [f"Use {name} in this home", "New profile", f"Copy {name}", f"Rename {name}", f"Delete {name}"]
            self.push_screen(BigChoice(f"Profile {name}", labels),
                             lambda choice: choice and self.profile_op(ops[choice - 1], name))

    def profile_op(self, op: str, name: str | None) -> None:
        if op == "edit":
            self.switch_to(name)
        elif op == "use":
            self.use_here(name, edit=False)
        elif op == "new":
            self.new_profile(copy_from=None)
        elif op == "copy":
            self.new_profile(copy_from=name)
        elif op == "rename":
            self.rename_profile(name)
        elif op == "delete":
            self.delete_profile(name)

    def switch_to(self, name: str, after=None) -> None:
        if name == self.state.editing:
            return
        if not self.draft.unsaved:
            self.state.editing = name
            self.load_draft()
            self.refresh_title()
            self.fill_sources()
            self.fill_skills()
            self.refresh_profiles()
            self.say(f"Editing {name}." + ("" if name == self.state.active else
                                            f" This home uses {self.state.active or 'no profile'}."))
            if after:
                after()
            return

        def choice(number: int | None) -> None:
            if number == 2:
                self.action_save()
            elif number == 3:
                self.draft = SelectionDraft.load(self.store, self.selection_catalog)
                self.switch_to(name, after)

        self.push_screen(ChoiceDialog(f"Switch to {name} with unsaved changes?", [
            "Keep editing", f"Review and save {self.state.editing}", "Discard changes and switch",
        ], "\n".join(self.draft.changes.lines())), choice)

    def use_here(self, name: str, *, edit: bool) -> None:
        def go() -> None:
            previous = self.state.active
            self.state.active = name
            self.refresh_profiles()
            self.say(f"Would run: reconcile-skills.sh --profile {name}  (records {name} for this home, then installs"
                     f"{'' if previous is None else f'; links only {previous} wanted are removed'})")

        if self.uncommitted(name):
            self.say(f"Cannot use {name} here: profiles/{name}.txt is not committed. Save and commit it first.")
            return
        if edit:
            self.switch_to(name, go)
        elif name != self.state.active:
            self.push_screen(ChoiceDialog(f"Use {name} in this home?", [f"Use {name} and install", "Cancel"],
                                          f"This home uses {self.state.active or 'no profile'}. Skills only it "
                                          f"selects are uninstalled here.\nOther homes are not affected."),
                             lambda choice: choice == 1 and go())

    def new_profile(self, *, copy_from: str | None, use_here: bool = False) -> None:
        details = (f"Starts as a copy of {copy_from}; the two stay independent afterwards." if copy_from
                   else "Starts empty.") + " It exists only in the selector until you save it."

        def named(name: str | None) -> None:
            if not name:
                return
            content = self.state.stores[copy_from].disk if copy_from else HEADER
            self.state.stores[name] = MemoryStore(content, "")  # no HEAD copy yet: never committed
            self.switch_to(name)
            self.say(f"Created {name}. Would write profiles/{name}.txt on save; Save and commit adds it to git.")

        self.push_screen(NameDialog(f"Copy {copy_from} as" if copy_from else "New profile", details, self.state),
                         named)

    def rename_profile(self, name: str) -> None:
        here = name == self.state.active
        details = ("Other homes that use it stop installing until they run "
                   "reconcile-skills.sh --profile <new name>; this selector can't see them.")
        if here:
            details += "\nThis home uses it, so its record is updated too."

        def named(new: str | None) -> None:
            if not new or new == name:
                return
            stores = {(new if key == name else key): store for key, store in self.state.stores.items()}
            self.state.stores = stores
            self.state.editing = new if self.state.editing == name else self.state.editing
            if here:
                self.state.active = new
            self.refresh_profiles()
            self.refresh_title()
            self.say(f"Would git mv profiles/{name}.txt profiles/{new}.txt and commit 'Rename {name} profile to "
                     f"{new}'" + (f", then reconcile-skills.sh --profile {new}" if here else ""))

        if self.draft.unsaved and name == self.state.editing:
            self.say("Cannot rename: save or discard the unsaved changes first.")
            return
        self.push_screen(NameDialog(f"Rename {name}", details, self.state, name, current=name), named)

    def delete_profile(self, name: str) -> None:
        if name == self.state.active:
            self.say(f"Cannot delete {name}: this home uses it. Use another profile here first.")
            return
        if len(self.state.stores) == 1:
            self.say("Cannot delete the last profile.")
            return

        def confirmed(choice: int | None) -> None:
            if choice != 1:
                return
            del self.state.stores[name]
            if self.state.editing == name:
                self.draft = SelectionDraft.load(self.store, self.selection_catalog)  # drop unsaved, then move
                self.state.editing = self.state.active or next(iter(self.state.stores))
                self.load_draft()
                self.fill_sources()
                self.fill_skills()
            self.refresh_profiles()
            self.say(f"Would git rm profiles/{name}.txt and commit 'Delete {name} profile'.")

        self.push_screen(ChoiceDialog(f"Delete {name}?", [f"Delete {name}", "Keep it"],
                                      "Other homes that use it stop installing until they choose another "
                                      "profile; this selector can't see them."), confirmed)

    async def quit_choice(self, choice: int | None) -> None:
        if choice == 2:
            self.action_save()
        elif choice == 3:
            self.exit()

    # ---- saving -----------------------------------------------------------------------------

    def action_save(self) -> None:
        if duplicates := self.draft.duplicates:
            self.say("Cannot save: same name selected more than once: " + ", ".join(duplicates))
            return
        name = self.state.editing
        uncommitted = self.uncommitted(name) or bool(self.draft.unsaved)
        if not uncommitted and not self.draft.unsaved and not self.not_installed:
            self.say("No changes to save.")
            return
        review = "\n".join(self.draft.changes.lines()) or "No changes from the last commit."
        if self.editing_active:
            labels = ["Save and install" if self.draft.unsaved else "Install", "Save only", "Keep editing"]
            ops = ["install", "save", None]
        else:
            where = (f"This home uses {self.state.active}; saving {name} doesn't change what's installed here."
                     if self.state.active else "This home has no profile yet.")
            review = f"{review}\n\n{where}"
            labels = [f"Save and commit {name}", f"Save, commit and use {name} here", "Save only"]
            ops = ["commit", "use", "save"]
        self.push_screen(ChoiceDialog(f"Save {name}?", labels, review),
                         lambda choice: choice and ops[choice - 1] and self.do_save(ops[choice - 1]))

    def do_save(self, op: str, overwrite: Conflict | None = None) -> None:
        name = self.state.editing
        changes = self.draft.changes
        outcome = self.draft.save(install=op == "install", overwrite=overwrite)
        if isinstance(outcome, Conflict):
            def conflict_choice(choice: int | None) -> None:
                if choice == 1:
                    self.load_draft()
                    self.refresh_selection()
                    self.say(f"Reloaded profiles/{name}.txt. Unsaved changes discarded.")
                elif choice == 2:
                    self.do_save(op, overwrite=outcome)

            self.push_screen(ChoiceDialog(f"profiles/{name}.txt changed on disk", ["Reload", "Overwrite"],
                                          "Reload drops your unsaved changes. Overwrite replaces the file."),
                             conflict_choice)
            return
        if isinstance(outcome, Blocked):
            self.say("Cannot save: " + outcome.reason)
            return
        commit = f"commit 'Update {name} profile ({changes.summary})' (only profiles/{name}.txt)"
        if op == "save":
            self.say(f"Saved profiles/{name}.txt. Not committed" + (", not installed." if self.editing_active else "."))
        else:
            self.store._head = self.store.disk
            if op == "commit":
                self.say(f"Would {commit}. Nothing installed here.")
            else:
                self.state.active = name
                self.say(f"Would {commit}, then reconcile-skills.sh --profile {name}.")
        self.not_installed = False
        self.refresh_selection()


class BigChoice(ChoiceDialog):
    """ChoiceDialog with room and number keys for more than three choices."""

    DEFAULT_CSS = ChoiceDialog.DEFAULT_CSS.replace("ChoiceDialog", "BigChoice").replace(
        "#choices { height: auto; max-height: 6; }", "#choices { height: auto; max-height: 10; }")
    BINDINGS = [*ChoiceDialog.BINDINGS, *(Binding(str(n), f"choose({n})", show=False) for n in range(4, 10))]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=HERE.parent)
    parser.add_argument("--variant", choices=list(VARIANTS), default="A")
    parser.add_argument("--fresh", action="store_true", help="start as a home with no profile")
    args = parser.parse_args()
    root = args.root.expanduser().resolve()
    state = seed(root)
    state.variant = args.variant
    if args.fresh:
        state.active = None
    while ProfilesApp(root, state).run() == "restart":
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
