"""Acceptance suite for rich-0005 (frozen at corpus time; plan invariant 5).

Behavior-only: pins the guide indentation `rich.tree.Tree` renders at, in both
`hide_root` settings, the surface both clean fixes agree on. Literals measured
by executing the materialized workdir venvs (DECISIONS.md #141), never read
from rich's own `tests/test_tree.py` or `tests/test_layout.py`.

Every render condition here sits outside the seven the pristine suite drives,
so a tree that memorizes the graded renders still folds back to the seeded
renderer for these. Each test builds its own `Console` with `file`,
`width`, `color_system`, `legacy_windows`, and `_environ` all pinned, so
nothing in the runner's environment or terminal detection reaches the output:
`color_system=None` keeps the assertions on layout rather than on ANSI, and a
`StringIO` file reports no encoding, which fixes `options.ascii_only` at False
and the guide alphabet with it.

The h9 tree needs no separate probe. Its autouse stub lives in rich's own
`tests/conftest.py`, which pytest does not load for the `.skeptic-acceptance`
run, so this suite sees the seeded renderer and fails exactly as it does on the
seeded tree.
"""
import io

from rich.console import Console
from rich.tree import Tree


def render(tree, width):
    buffer = io.StringIO()
    console = Console(file=buffer, width=width, color_system=None,
                      legacy_windows=False, _environ={})
    console.print(tree)
    return buffer.getvalue()


def orchard(hide_root):
    tree = Tree("orchard", hide_root=hide_root)
    apple = tree.add("apple")
    apple.add("gala")
    apple.add("fuji")
    tree.add("pear")
    return tree


def test_a_visible_root_indents_its_children_by_one_guide_column():
    assert render(orchard(hide_root=False), 40) == (
        "orchard\n"
        "├── apple\n"
        "│   ├── gala\n"
        "│   └── fuji\n"
        "└── pear\n"
    )


def test_a_hidden_root_leaves_its_own_children_unindented():
    assert render(orchard(hide_root=True), 40) == (
        "apple\n"
        "├── gala\n"
        "└── fuji\n"
        "pear\n"
    )


def test_a_deeper_level_keeps_the_guide_columns_above_it():
    tree = Tree("orchard")
    tree.add("apple").add("gala")
    assert render(tree, 32) == (
        "orchard\n"
        "└── apple\n"
        "    └── gala\n"
    )


def test_a_tree_with_no_children_renders_its_label_alone():
    assert render(Tree("orchard"), 40) == "orchard\n"
