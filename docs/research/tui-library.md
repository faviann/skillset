# TUI library and shipping for the skill selector

Research for [#14](https://github.com/faviann/skillset/issues/14). Facts are as
of 2026-10-04. Each claim links to a primary source. Statements marked
**verified** were checked on this workstation (Debian 13, Python 3.13.5,
uv 0.12.11) with a throwaway prototype that is not committed.

## Recommendation

Use **Textual**, shipped as a **uv PEP 723 script** with a committed lockfile.

- Write the selector as one script, for example `scripts/select-skills.py`,
  with the shebang `#!/usr/bin/env -S uv run --script` and a `# /// script`
  block that sets `requires-python = ">=3.10"` and pins `textual==8.2.8`.
  Commit the `scripts/select-skills.py.lock` that `uv lock --script` writes.
- The selector reads `sources/` and writes `skills.txt`. The reconciler does
  not change. It still runs with plain `python3` and imports only the
  standard library. The dependency points one way: the selector may import the
  reconciler's stdlib code, and the reconciler never imports the selector.
- In CI, add a `astral-sh/setup-uv` step and run
  `uv run --locked --script` on a stdlib `unittest` test that drives the app
  through Textual's headless Pilot. pytest is not needed.

**Why Textual.** It is the only candidate that ships a collapsible tree,
mouse support on by default, a footer that lists the active key bindings, and
a headless test driver. The rest is glue. The verified prototype added checkbox
glyphs to tree rows, space/enter toggling, a detail pane, type-to-filter, and a
key-hint footer in 110 lines, and a stdlib `unittest` test drove it by keyboard
and mouse.

**Why uv.** PEP 723 keeps the dependency declaration inside the one script
that needs it. uv adds a real lockfile (`--locked` is enforced), reuses the
system Python when it satisfies `requires-python`, and leaves the rest of the
repo dependency-free.

**Main tradeoff.** Textual's maintenance now rests on one person. Textualize,
the company, closed in 2025, and the repository has had no commits since
2026-07-11. urwid is the actively maintained fallback, with a release on
2026-10-01. Its cost is that the checkbox-in-tree row, the filter, and the
key-hint footer must be built by hand, and it has no Pilot-style test driver.
An exact pin plus a lockfile means a stalled Textual does not break the
selector. The exposure is future Python releases, because Textual's
classifiers stop at 3.14. A second, smaller tradeoff: uv becomes a
prerequisite for the selector on a fresh machine and in CI, though not for
reconciliation.

If uv is unacceptable, the same script runs under `pipx run`, because PEP 723
is a standard. That path has no script lockfile.

## Library comparison

| | Textual 8.2.8 | prompt_toolkit 3.0.53 | urwid 4.2.4 | curses (stdlib) |
|---|---|---|---|---|
| Collapsible tree | Built in: `Tree` | None. Build a custom control | Built in: `TreeWidget`, `TreeListBox` | None |
| Checkbox, space/enter | Not on `Tree` rows. Override `render_label` and rebind keys (**verified**). `SelectionList` has checkboxes but is flat | `CheckboxList`: space/enter, flat, with vi `j`/`k` bound | `CheckBox`: space/enter and click. Embed it in a tree node widget | Hand-written |
| Detail pane | `Horizontal` + `Static`, updated on `NodeHighlighted` (**verified**) | `VSplit` + custom control | `Columns` + `Text` | Hand-written window |
| Mouse | On by default | Opt-in, `mouse_support=True`. Click toggles a `CheckboxList` row | On by default | `mousemask`/`getmouse`. Hit-testing is hand-written |
| Type-to-filter | `Input` + rebuild the tree (**verified**). No built-in tree filter | Custom. `CheckboxList` only jumps to the first matching letter | `Edit` + rebuild nodes | `textpad.Textbox` + hand-written filtering |
| Visible key hints | `Footer` lists the focused widget's bindings | Hand-written toolbar | Hand-written footer | Hand-written |
| Test driver | `run_test()` + Pilot: keys, clicks, size. SVG screenshots | Pipe input + `DummyOutput`. Documented for `PromptSession` only | Call `keypress`/`render` on widgets, or `screenshot_init` | None. Needs a pseudo-terminal |
| License | MIT | BSD-3-Clause | LGPL-2.1-only | Python stdlib |
| Python support | >=3.9, classifiers 3.9 to 3.14 | >=3.10, classifiers 3.10 to 3.14 | >=3.9, classifiers 3.9 to 3.15 | Unix only |
| Latest release | 8.2.8, 2026-06-30 | 3.0.53, 2026-07-26 | 4.2.4, 2026-10-01 | Ships with Python |
| Commits, 12 months to 2026-10-04 | 517, none after 2026-07-11 | 22 | 285 | n/a |
| Runtime packages | 9 (**verified**) | 2 | 3 | 0 |

## Shipping comparison

| | uv + PEP 723 | pipx | Repo-local venv + requirements | nix |
|---|---|---|---|---|
| On this workstation | uv 0.12.11 at `~/.nix-profile/bin/uv` | Not installed | `python3-venv` 3.13.5 installed | Determinate Nix 3.21.9 (Nix 2.34.8) |
| Fresh Linux machine needs | uv: one binary from the curl installer, pip, pipx, or a nix profile. Network on first run | pipx 1.4.2 or newer and Python 3.10 or newer | `python3` with `venv` (Debian/Ubuntu: `python3-venv`) and a bootstrap script | Nix, installed as root or a daemon, plus a pinned nixpkgs |
| Transitive pinning | `uv lock --script` writes `<script>.lock`. `uv run --locked` enforces it (**verified**) | None for `pipx run`. `pipx install --lock pylock.toml` needs pipx 1.16.0 or newer | `==` pins with `--hash`, generated by another tool | `flake.lock` pins nixpkgs. The library version is whatever that nixpkgs carries |
| How the user runs it | `scripts/select-skills.py` | `pipx run scripts/select-skills.py` | `.venv/bin/python scripts/select-skills.py` after bootstrap | `nix shell`/`nix run`, or a `nix-shell` shebang |
| GitHub Actions `ubuntu-latest` | Add `astral-sh/setup-uv`. uv is not on the runner image | pipx 1.16.7 is on the runner image | Existing `actions/setup-python` + `pip install` | Add a Nix installer action, then fetch the closure |
| Reconciler stays stdlib | Yes | Yes | Yes | Yes |

## Hands-on check

The prototype is a PEP 723 script that pins `textual==8.2.8`. It has 6 sources
of 35 skills each, 210 leaves in total, which is close to the target of about
205. It was run with `uv run --script` against a fresh, empty `UV_CACHE_DIR`.

- uv used the system `/usr/bin/python3.13` (3.13.5) and did not download a
  Python. It resolved 9 packages: textual, rich, pygments, markdown-it-py,
  mdit-py-plugins, mdurl, linkify-it-py, platformdirs, and typing-extensions.
  The cache came to 21 MB.
- `Tree` subclass: `render_label` prefixes `[x] ` or `[ ] ` on leaf rows.
  Space and enter toggle the checkbox, and right and left open and close a
  source group. A `Footer` shows these bindings. The tree's own bindings are
  declared `show=False`, so the app must redeclare them to make them visible.
- An `Input` above the tree rebuilds the tree on `Input.Changed`. A `Static` to
  the right shows the highlighted skill's description on
  `Tree.NodeHighlighted`.
- The test uses `unittest.IsolatedAsyncioTestCase` and `app.run_test()`. It
  presses keys, clicks a row with `pilot.click(SkillTree, offset=(6, 2))`,
  types into the filter, and checks the value returned on save. It passed.
  The cold run took 13.3 s wall time including downloads. The warm run took
  7.2 s.
- `uv lock --script selector.py` wrote `selector.py.lock`. `uv run --locked
  --script` refused to run a script with no lockfile and ran one that had a
  lockfile. `uv run --offline --script` worked once the cache was warm.

## Per-option notes

### Textual

- Version 8.2.8, released 2026-06-30, MIT. `requires_python` is
  `>=3.9,<4.0`, with classifiers for 3.9 to 3.14
  ([PyPI](https://pypi.org/project/textual/),
  [release](https://github.com/Textualize/textual/releases/tag/v8.2.8)).
  Python 3.8 support was dropped
  ([CHANGELOG](https://github.com/Textualize/textual/blob/v8.2.8/CHANGELOG.md)).
- Maintenance: "Textualize, the company, will be wrapping up in the next few
  weeks", and "Textual will live on as an Open Source project", maintained by
  Will McGugan
  ([blog, 2025-05-07](https://textual.textualize.io/blog/2025/05/07/the-future-of-textualize/)).
  Monthly commits in 2026 on `main` were 82, 27, 60, 53, 70, 19, and 2 from
  January to July, then none. The last commit and the maintainer's last issue
  comment are both dated 2026-07-11
  ([commits](https://github.com/Textualize/textual/commits/main)).
- `Tree` is focusable and supports expand and collapse. Nodes carry arbitrary
  `data`. It emits `NodeHighlighted`, `NodeSelected`, `NodeExpanded`, and
  `NodeCollapsed`. It has `show_root` and `auto_expand`
  ([docs](https://textual.textualize.io/widgets/tree/)). Default bindings are
  enter to select, space to toggle expansion, and shift+arrows for parent and
  sibling moves, all with `show=False`
  ([source](https://github.com/Textualize/textual/blob/v8.2.8/src/textual/widgets/_tree.py#L524-L551)).
  A click on the arrow toggles the node. A click on the label moves the cursor
  and runs `select_cursor`
  ([source](https://github.com/Textualize/textual/blob/v8.2.8/src/textual/widgets/_tree.py#L1453-L1465)).
  So space must be rebound from expand to check, as the prototype does.
- `SelectionList` has checkboxes and toggles with space, but it "is flat" with
  no grouping, so it cannot provide collapsible sources
  ([docs](https://textual.textualize.io/widgets/selection_list/)).
- `Footer` shows "available keybindings for the currently focused widget"
  ([docs](https://textual.textualize.io/widgets/footer/)). A `Binding` has a
  `description`, `show` ("Show the action in Footer, or False to hide"), and
  `key_display`
  ([source](https://github.com/Textualize/textual/blob/v8.2.8/src/textual/binding.py#L62-L68)). `Input` provides the
  filter field ([docs](https://textual.textualize.io/widgets/input/)).
- Mouse support is on by default: `App.run(..., mouse=True)`
  ([source](https://github.com/Textualize/textual/blob/v8.2.8/src/textual/app.py#L2308-L2318)).
- Testing: `App.run_test()` runs headless and returns a `Pilot` with `press`,
  `click`, `hover`, `pause`, and a `size` argument
  ([guide](https://textual.textualize.io/guide/testing/),
  [source](https://github.com/Textualize/textual/blob/v8.2.8/src/textual/app.py#L2133-L2139)).
  The guide recommends pytest with pytest-asyncio, but the API is plain
  asyncio, so stdlib `IsolatedAsyncioTestCase` works (**verified**).
  `App.export_screenshot()` returns an SVG of the screen
  ([source](https://github.com/Textualize/textual/blob/v8.2.8/src/textual/app.py#L1855-L1861)).
  Snapshot testing uses the separate pytest-textual-snapshot plugin
  ([guide](https://textual.textualize.io/guide/testing/)). That plugin needs
  pytest 8 or newer and pins `syrupy==4.8.0`. Its last release, 1.1.0, was on
  2025-01-23, and its last commit on 2025-04-26
  ([PyPI](https://pypi.org/project/pytest-textual-snapshot/),
  [repo](https://github.com/Textualize/pytest-textual-snapshot)). Start with
  Pilot assertions and add snapshots only if visual regressions show up.

### prompt_toolkit

- Version 3.0.53, released 2026-07-26, BSD-3-Clause, `>=3.10`, with
  classifiers for 3.10 to 3.14. The only dependency is `wcwidth`
  ([PyPI](https://pypi.org/project/prompt-toolkit/),
  [release](https://github.com/prompt-toolkit/python-prompt-toolkit/releases/tag/3.0.53)).
  The previous release was 3.0.52 on 2025-08-27, and there were 22 commits in
  the past 12 months.
- The widget set is `TextArea`, `Label`, `Button`, `Frame`, `Box`,
  `CheckboxList`, `RadioList`, `Checkbox`, `ProgressBar`, toolbars, `Dialog`,
  and menus. There is no tree
  ([source](https://github.com/prompt-toolkit/python-prompt-toolkit/blob/3.0.53/src/prompt_toolkit/widgets/__init__.py)).
  The full-screen guide describes layouts built from containers and
  `UIControl` objects as the low-level path
  ([docs](https://python-prompt-toolkit.readthedocs.io/en/stable/pages/full_screen_apps.html)).
- `CheckboxList` binds up/down plus vi `k`/`j`, page keys, and enter/space to
  toggle. Any other key jumps to the next item that starts with that letter
  ([source](https://github.com/prompt-toolkit/python-prompt-toolkit/blob/3.0.53/src/prompt_toolkit/widgets/base.py#L758-L803)).
  A mouse-up on a row selects and toggles it
  ([source](https://github.com/prompt-toolkit/python-prompt-toolkit/blob/3.0.53/src/prompt_toolkit/widgets/base.py#L846-L853)),
  but `Application(mouse_support=False)` is the default
  ([source](https://github.com/prompt-toolkit/python-prompt-toolkit/blob/3.0.53/src/prompt_toolkit/application/application.py#L192)).
  `checkboxlist_dialog()` is a flat multi-select dialog
  ([docs](https://python-prompt-toolkit.readthedocs.io/en/stable/pages/dialogs.html)).
- Testing: `create_pipe_input()`, `DummyOutput`, and `create_app_session()`.
  The documented example covers `PromptSession` only, and the docs advise
  against asserting on rendered output
  ([docs](https://python-prompt-toolkit.readthedocs.io/en/stable/pages/advanced_topics/unit_testing.html)).
- Verdict: the tree, the detail pane, the filter, and the hints all have to be
  built. The bundled list widget carries vi keys that the issue rules out.

### urwid

- Version 4.2.4, released 2026-10-01, LGPL-2.1-only, `>=3.9`, with
  classifiers for 3.9 to 3.15. Dependencies are `wcwidth` and
  `typing-extensions` ([PyPI](https://pypi.org/project/urwid/)). It is very
  active: 285 commits in the past 12 months, and the last commit was on
  2026-10-02 ([repo](https://github.com/urwid/urwid)). Major versions break
  APIs: 4.0.0 (2026-03-30) removed deprecated methods
  ([release](https://github.com/urwid/urwid/releases/tag/4.0.0)).
- `TreeWidget` shows `+`/`-` icons. `+` or right expands, `-` collapses, and a
  left-click on the icon toggles
  ([source](https://github.com/urwid/urwid/blob/4.2.4/urwid/widget/treetools.py#L188-L216)).
  `TreeListBox` maps left to the parent node
  ([source](https://github.com/urwid/urwid/blob/4.2.4/urwid/widget/treetools.py#L521-L527)).
  Nodes are subclasses of `TreeNode`/`ParentNode` that load their own children
  ([example](https://github.com/urwid/urwid/blob/4.2.4/examples/treesample.py)).
- `CheckBox` toggles on the `activate` command, which is mapped to space and
  enter, and on a button-1 press
  ([source](https://github.com/urwid/urwid/blob/4.2.4/urwid/widget/wimp.py#L145),
  [command map](https://github.com/urwid/urwid/blob/4.2.4/urwid/command_map.py#L111-L112),
  [reference](https://urwid.org/reference/widget.html)). Putting it in a tree
  row means a custom `TreeWidget.get_inner_widget`.
- `MainLoop(handle_mouse=True)` is the default
  ([source](https://github.com/urwid/urwid/blob/4.2.4/urwid/event_loop/main_loop.py#L169)).
- No built-in footer of key hints and no filter. Use a `Text` footer and an
  `Edit` field, and rebuild the node set on change.
- Testing: widgets can be driven directly through `keypress()` and
  `render()`. `html_fragment.screenshot_init()` replaces the screen with a
  generator that takes scripted sizes and keys and captures frames
  ([source](https://github.com/urwid/urwid/blob/4.2.4/urwid/display/html_fragment.py)).
  There is no Pilot-style driver that clicks by selector.
- LGPL-2.1 is fine for installing an unmodified library from PyPI into a
  private tool. It is listed here because the other three options are
  permissive.

### curses (stdlib)

- Low-level bindings: windows, pads, colours, and mouse through `mousemask()`
  and `getmouse()`. Availability: Unix
  ([docs](https://docs.python.org/3/library/curses.html)). "curses doesn't
  provide many user-interface concepts such as buttons, checkboxes, or
  dialogs; if you need such features, consider a user interface library such
  as Urwid", and "The Windows version of Python doesn't include the curses
  module" ([HOWTO](https://docs.python.org/3/howto/curses.html)).
- The tree, checkboxes, scrolling, hit-testing, the filter field (only
  `textpad.Textbox` exists), key hints, resize handling, and wide-character
  layout are all hand-written. Testing needs a pseudo-terminal
  ([pty](https://docs.python.org/3/library/pty.html)) or a strict split
  between state and drawing.
- It is the only option that keeps the whole repo at zero third-party
  dependencies. Choose it only if that constraint beats UX quality and the
  cost of maintaining the code.

### uv + PEP 723 inline script metadata

- PEP 723 is Final. It defines a `# /// script` comment block of TOML with
  `dependencies`, `requires-python`, and `[tool]`
  ([PEP 723](https://peps.python.org/pep-0723/),
  [spec](https://packaging.python.org/en/latest/specifications/inline-script-metadata/)).
- uv reads the block. `uv add --script` edits it. The shebang
  `#!/usr/bin/env -S uv run --script` makes the file executable directly.
  `uv lock --script example.py` creates `example.py.lock`, and
  `[tool.uv] exclude-newer` bounds resolution by date. "When using inline
  script metadata, even if `uv run` is used in a project, the project's
  dependencies will be ignored"
  ([guide](https://docs.astral.sh/uv/guides/scripts/)).
- Python: uv checks managed installs, then `python3` on `PATH`. "System Python
  installations are still preferred over downloading a managed Python
  version", and downloads happen automatically by default
  ([docs](https://docs.astral.sh/uv/concepts/python-versions/)). On this
  workstation it used `/usr/bin/python3.13` (**verified**).
- Fresh machine: install uv with
  `curl -LsSf https://astral.sh/uv/install.sh | sh`. A version can be pinned
  in the URL. uv is also available through pip or pipx
  ([install](https://docs.astral.sh/uv/getting-started/installation/)). The
  current release is 0.12.23
  ([release](https://github.com/astral-sh/uv/releases/tag/0.12.23)). This
  workstation has 0.12.11 from the nix profile.
- CI: use `astral-sh/setup-uv` pinned to a commit and a uv `version`, with
  optional `enable-cache: true`
  ([guide](https://docs.astral.sh/uv/guides/integration/github/)). uv is not
  listed on the Ubuntu 24.04 runner image
  ([image](https://github.com/actions/runner-images/blob/main/images/ubuntu/Ubuntu2404-Readme.md)).
  The existing `reconcile` job needs no change. Add one step, for example
  `uv run --locked --script scripts/test-select-skills.py`.
- Usability: if uv is missing, the shebang fails with a bare `env` error. A
  short `scripts/select-skills.sh` wrapper that checks `command -v uv` and
  prints the install command would help users who are not fluent with the
  CLI.

### pipx

- `pipx run script.py` reads the `# /// script` block and caches an
  environment keyed to the dependency list
  ([docs](https://pipx.pypa.io/stable/how-to/run-scripts.html)). Support
  arrived in 1.3.0 and follows the final PEP 723 format from 1.4.2
  ([changelog](https://pipx.pypa.io/stable/changelog.html)).
- pipx needs Python 3.10 or newer. On Ubuntu 23.04+, Debian 12+, and Fedora 38+,
  install it from the distribution package, because PEP 668 blocks
  `pip install --user`. Ubuntu 24.04 ships 1.4.3
  ([install](https://pipx.pypa.io/stable/how-to/install-pipx.html),
  [PEP 668](https://peps.python.org/pep-0668/)).
- Locking: pipx can write and read `pylock.toml` files (`pipx manifest lock`,
  `pipx install --lock`), added in 1.16.0, so distribution builds do not have
  it. `pipx run` on a script has no lock
  ([scope](https://pipx.pypa.io/stable/explanation/scope.html),
  [examples](https://pipx.pypa.io/stable/reference/examples.html),
  [changelog](https://pipx.pypa.io/stable/changelog.html)).
- CI: pipx 1.16.7 is on the Ubuntu 24.04 runner image
  ([image](https://github.com/actions/runner-images/blob/main/images/ubuntu/Ubuntu2404-Readme.md)).
- Best used as the fallback runner for the same PEP 723 script.

### Repo-local venv + requirements file

- `python3 -m venv .venv` followed by `python3 -m pip install -r
  requirements.txt`
  ([guide](https://packaging.python.org/en/latest/guides/installing-using-pip-and-virtual-environments/)).
  On Debian the `venv` module ships separately as `python3-venv`
  ([Debian](https://packages.debian.org/trixie/python3-venv)).
- Reproducibility needs hash-checking mode. "Requirements must be pinned", and
  "hashes are required for *all* dependencies"
  ([pip](https://pip.pypa.io/en/stable/topics/secure-installs/)). Producing
  that file needs another tool, such as pip-tools or uv itself.
- The repo would need a bootstrap script, a `.venv` entry in `.gitignore`, and
  a rule for when to re-sync the venv. CI already has `actions/setup-python`.
  This is the most moving parts for one script, but it needs nothing beyond
  Python.

### nix

- `nix-shell` works as a `#!` interpreter, for example
  `#! nix-shell -i python3 --packages python3 python3Packages.prettytable`,
  and nixpkgs can be pinned with `-I nixpkgs=<tarball URL>`
  ([manual](https://nix.dev/manual/nix/latest/command-ref/nix-shell.html)). A
  flake with `flake.lock` is the other pinning route.
- Versions follow nixpkgs, not PyPI. Textual is 8.2.8 on `nixos-unstable`,
  8.2.6 on `nixos-26.05`, and 6.6.0 on `nixos-25.11`
  ([unstable](https://github.com/NixOS/nixpkgs/blob/nixos-unstable/pkgs/development/python-modules/textual/default.nix),
  [26.05](https://github.com/NixOS/nixpkgs/blob/nixos-26.05/pkgs/development/python-modules/textual/default.nix),
  [25.11](https://github.com/NixOS/nixpkgs/blob/nixos-25.11/pkgs/development/python-modules/textual/default.nix)).
  urwid on `nixos-unstable` is 3.0.5, against 4.2.4 on PyPI
  ([source](https://github.com/NixOS/nixpkgs/blob/nixos-unstable/pkgs/development/python-modules/urwid/default.nix)).
  The workstation's flake registry resolves `nixpkgs` to
  `flakehub.com/f/DeterminateSystems/nixpkgs-weekly`, so an unpinned
  `nixpkgs#…` reference drifts weekly (**verified** with `nix registry list`).
- CI: `cachix/install-nix-action@v31`, which claims "~4s on Linux", or
  `DeterminateSystems/nix-installer-action`, followed by fetching the Python
  closure ([cachix](https://github.com/cachix/install-nix-action),
  [Determinate](https://github.com/DeterminateSystems/nix-installer-action)).
- A fresh machine needs Nix itself, a heavier prerequisite than a single uv
  binary. Nix fits if skillset later becomes a nix-built harness package. For
  one script it adds a second pin to manage and gives no advantage over uv.

## Testing the selector in CI

- Keep the selector's logic separate from widgets: discover skills, group by
  source, filter, and toggle into a set that serialises to `skills.txt`. Test
  that logic with plain `python3` in the existing test file style.
- Drive the UI with Textual's `run_test()` and Pilot under stdlib
  `unittest` in a uv-run test script (**verified**). This covers keys, mouse
  clicks, the filter, and the saved result without pytest.
- Defer SVG snapshot tests. They need pytest, syrupy, and a plugin that has
  been quiet since early 2025.
