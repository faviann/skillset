#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["textual==8.2.8"]
# ///
"""Throwaway prototype: tree grouped by source, checkboxes, detail pane, filter, footer."""

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import Footer, Header, Input, Static, Tree

SKILLS = {
    f"source-{s}": {f"skill-{s}-{i}": f"Description of skill {s}-{i}." for i in range(35)}
    for s in range(6)
}


class SkillTree(Tree):
    BINDINGS = [
        Binding("space", "toggle_check", "Check/uncheck"),
        Binding("enter", "toggle_check", "Check/uncheck", show=False),
        Binding("right", "expand", "Open group"),
        Binding("left", "collapse", "Close group"),
    ]

    def __init__(self, selected: set[str]) -> None:
        super().__init__("skills")
        self.show_root = False
        self.selected = selected

    def render_label(self, node, base_style, style):
        if node.data is None:
            return super().render_label(node, base_style, style)
        box = "[x] " if node.data in self.selected else "[ ] "
        label = node._label.copy()
        label = type(label)(box) + label
        label.stylize(style)
        return label

    def action_toggle_check(self) -> None:
        node = self.cursor_node
        if node is None:
            return
        if node.data is None:
            node.toggle()
            return
        self.selected ^= {node.data}
        node.refresh()

    def action_expand(self) -> None:
        if self.cursor_node and self.cursor_node.allow_expand:
            self.cursor_node.expand()

    def action_collapse(self) -> None:
        node = self.cursor_node
        if node is None:
            return
        if node.allow_expand and node.is_expanded:
            node.collapse()
        elif node.parent is not None and node.parent is not self.root:
            self.select_node(node.parent)
            node.parent.collapse()


class Selector(App):
    BINDINGS = [Binding("ctrl+s", "save", "Save"), Binding("escape", "quit", "Quit")]
    CSS = "SkillTree { width: 1fr; } #detail { width: 1fr; padding: 1; }"

    def __init__(self) -> None:
        super().__init__()
        self.selected: set[str] = set()

    def compose(self) -> ComposeResult:
        yield Header()
        yield Input(placeholder="Type to filter skills", id="filter")
        with Horizontal():
            yield SkillTree(self.selected)
            yield Static("", id="detail")
        yield Footer()

    def on_mount(self) -> None:
        self.rebuild("")
        self.query_one(SkillTree).focus()

    def rebuild(self, text: str) -> None:
        tree = self.query_one(SkillTree)
        tree.clear()
        for source, skills in SKILLS.items():
            matches = {k: v for k, v in skills.items() if text.lower() in k.lower()}
            if not matches:
                continue
            group = tree.root.add(source, expand=bool(text))
            for name in matches:
                group.add_leaf(name, data=f"{source}/{name}")

    def on_input_changed(self, event: Input.Changed) -> None:
        self.rebuild(event.value)

    def on_tree_node_highlighted(self, event: Tree.NodeHighlighted) -> None:
        data = event.node.data
        text = "" if data is None else SKILLS[data.split("/")[0]][data.split("/")[1]]
        self.query_one("#detail", Static).update(text)

    def action_save(self) -> None:
        self.exit(sorted(self.selected))


if __name__ == "__main__":
    print(Selector().run())
