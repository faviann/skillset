"""Hold unsaved edits to the selection and decide every rule about them."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import skill_catalog
from skill_catalog import SelectionLine, SourceCatalog

# A catalog skill is keyed by its "owner/repo:name" value, an off-catalog line by its line number.
Key = str | int

INSTALL_BLOCKED = "deselect the skills under Not in catalog"


class Store(Protocol):
    """Where the selection lives. Reads and writes raise ReconcileError or OSError."""

    def read(self) -> str: ...
    def write(self, content: str) -> None: ...
    def head(self) -> str: ...


class CheckoutStore:
    """skills.txt in a checkout, with HEAD's copy read through Git."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def read(self) -> str:
        return skill_catalog.read_selection_text(self.root)

    def write(self, content: str) -> None:
        (self.root / skill_catalog.SELECTION_PATH).write_bytes(content.encode("utf-8"))

    def head(self) -> str:
        return skill_catalog.git(["show", f"HEAD:{skill_catalog.SELECTION_PATH}"], cwd=self.root)


class MemoryStore:
    """An in-memory selection; tests edit `disk` to simulate an external change."""

    def __init__(self, disk: str, head: str = "") -> None:
        self.disk = disk
        self._head = head

    def read(self) -> str:
        return self.disk

    def write(self, content: str) -> None:
        self.disk = content

    def head(self) -> str:
        return self._head


def _values(text: str) -> Counter[str]:
    return Counter(line.value for line in skill_catalog.parse_selection(text))


@dataclass(frozen=True)
class Changes:
    """Multiset difference between two selections, values sorted."""

    added: tuple[str, ...]
    removed: tuple[str, ...]

    @classmethod
    def between(cls, head_text: str, text: str) -> Changes:
        return cls._of(_values(head_text), _values(text))

    @classmethod
    def _of(cls, head: Counter[str], current: Counter[str]) -> Changes:
        return cls(tuple(sorted((current - head).elements())), tuple(sorted((head - current).elements())))

    def lines(self) -> list[str]:
        """"+ value" lines, then "- value" lines."""
        return [f"+ {value}" for value in self.added] + [f"- {value}" for value in self.removed]

    @property
    def summary(self) -> str:
        """"+2 -1"."""
        return f"+{len(self.added)} -{len(self.removed)}"

    def __bool__(self) -> bool:
        return bool(self.added or self.removed)


# toggle outcomes
@dataclass(frozen=True)
class Toggled:
    pass


@dataclass(frozen=True)
class Refused:
    reason: str


@dataclass(frozen=True)
class Replace:
    """The name is selected from another source; toggle again with replace=True to swap."""

    current: str


# save outcomes
@dataclass(frozen=True)
class Saved:
    wrote: bool


@dataclass(frozen=True)
class Conflict:
    """The file changed since it was loaded. Pass this back as `overwrite` to replace it."""

    disk: str


@dataclass(frozen=True)
class Blocked:
    reason: str


class SelectionDraft:
    """The selector's working copy of the selection.

    Off-catalog lines (bad syntax, duplicate line, unknown source, missing or
    invalid name) stay listed for the whole session and can only be deselected.
    Every save re-reads the store and refuses to write over a change it did not load.
    """

    def __init__(self, store: Store, selected: dict[Key, str], off_catalog: dict[int, SelectionLine],
                 content: str, head: Counter[str]) -> None:
        self._store = store
        self._selected = selected
        self._off_catalog = off_catalog
        self._content = content
        self._loaded = Counter(selected.values())
        self._head = head

    @classmethod
    def load(cls, store: Store, catalog: Mapping[str, SourceCatalog]) -> SelectionDraft:
        content = store.read()
        head = _values(store.head())
        selected: dict[Key, str] = {}
        off_catalog: dict[int, SelectionLine] = {}
        for line in skill_catalog.parse_selection(content):
            error = line.error or skill_catalog.selection_error(line.value, catalog)
            if error:
                off_catalog[line.number] = SelectionLine(line.value, line.number, error, line.name)
                selected[line.number] = line.value
            else:
                selected[line.value] = line.value
        return cls(store, selected, off_catalog, content, head)

    def __contains__(self, key: Key) -> bool:
        return key in self._selected

    def __len__(self) -> int:
        """How many keys are selected."""
        return len(self._selected)

    @property
    def off_catalog(self) -> Mapping[int, SelectionLine]:
        """Every off-catalog line from the last load, in file order, with its error filled in."""
        return self._off_catalog

    def _current(self) -> Counter[str]:
        return Counter(self._selected.values())

    @property
    def unsaved(self) -> int:
        """Multiset distance from the content last loaded or saved."""
        current = self._current()
        return (current - self._loaded).total() + (self._loaded - current).total()

    @property
    def changes(self) -> Changes:
        """The draft against HEAD's selection."""
        return Changes._of(self._head, self._current())

    @property
    def duplicates(self) -> tuple[str, ...]:
        """Names selected more than once, sorted. Only a loaded file can produce them."""
        names = Counter(line.name for line in skill_catalog.parse_selection("\n".join(self._selected.values()))
                        if line.name is not None)
        return tuple(sorted(name for name, count in names.items() if count > 1))

    @property
    def install_blocker(self) -> str | None:
        """INSTALL_BLOCKED while an off-catalog line is selected."""
        return INSTALL_BLOCKED if any(key in self._selected for key in self._off_catalog) else None

    def toggle(self, key: Key, *, replace: bool = False) -> Toggled | Refused | Replace:
        """Deselect a selected key, or select one.

        Selecting an off-catalog key is Refused with its error. Selecting a name
        held by another source returns Replace and changes nothing, unless
        `replace` is set, which swaps the two.
        """
        if key in self._selected:
            del self._selected[key]
            return Toggled()
        if isinstance(key, int):
            return Refused(self._off_catalog[key].error)
        name = key.partition(":")[2]
        other = next((selected for selected, value in self._selected.items()
                      if value != key and value.partition(":")[2] == name), None)
        if other is not None:
            if not replace:
                return Replace(self._selected[other])
            del self._selected[other]
        self._selected[key] = key
        return Toggled()

    def save(self, *, install: bool = False, overwrite: Conflict | None = None) -> Saved | Conflict | Blocked:
        """Write the draft in the selector's format and make it the new baseline.

        Blocked while duplicates exist. Conflict when the store no longer holds
        the loaded content, or `overwrite.disk` when given. With `install`,
        nothing unsaved and the store unchanged, nothing is written, so saved
        bytes such as comments reach the install commit as they are. A store
        error propagates and leaves the draft unchanged.
        """
        if duplicates := self.duplicates:
            return Blocked(f"same name selected more than once: {', '.join(duplicates)}")
        disk = self._store.read()
        if disk != (self._content if overwrite is None else overwrite.disk):
            return Conflict(disk)
        content = disk
        wrote = not install or bool(self.unsaved) or disk != self._content
        if wrote:
            content = skill_catalog.format_selection(self._selected.values())
            self._store.write(content)
        self._content = content
        self._loaded = self._current()
        return Saved(wrote)
