"""The wire contract in ``server/protocol.py``, checked against the source.

Guards the C3 class of bug: the server adds or drops a key in a payload, the
client still reads the old one and gets ``undefined``.

This file replaces a guard that could not fail. The previous version grepped
``websocket.py`` for ``msg_type == "…"``; the #363 dispatch-table refactor
removed that pattern entirely, so the matched set was always empty and
``test_no_orphan_handlers`` passed on an empty comparison for months while
``all_time`` and ``teams`` drifted onto live frames (#749).

Nothing is grepped now:

* the server→client frames are parsed out of the real dict literals with
  ``ast`` and compared field-by-field with ``SERVER_FRAMES``;
* ``CLIENT_MESSAGE_TYPES`` is compared against the handler's imported
  ``_DISPATCH`` table.

To prove it can fail: add a key to any frame in ``server/websocket.py`` and
run this file — ``test_no_build_site_sends_an_undeclared_key`` goes red and
names the key, the frame and the line.
"""

from __future__ import annotations

import ast
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from custom_components.quizify.server.protocol import (  # noqa: E402
    CLIENT_MESSAGE_TYPES,
    OUT_OF_BAND_CLIENT_TYPES,
    SERVER_FRAMES,
)

_PACKAGE = _REPO_ROOT / "custom_components" / "quizify"

# The one frame whose wire ``type`` is a variable rather than a literal: the
# coalesced roster broadcast picks ``player_joined`` or ``player_left`` at
# flush time (#453). Keyed by ``module::function`` so it survives edits above
# it, and asserted to be the ONLY such site — a second one has to be declared
# here on purpose rather than slipping through unchecked.
_DYNAMIC_TYPE_SITES: dict[str, tuple[str, ...]] = {
    "server/websocket.py::_flush_roster_after_window": (
        "player_joined",
        "player_left",
    ),
}


@dataclass(frozen=True)
class _FrameSite:
    """One dict literal in the source that builds a wire frame."""

    module: str
    lineno: int
    function: str
    #: The literal ``type`` value, or None when it is a variable.
    type_name: str | None
    #: Value of a non-literal ``type``, unparsed — for the error message.
    type_expr: str
    #: Everything the frame can carry: the literal keys plus any added by a
    #: later ``payload["x"] = …`` on the same local.
    keys: frozenset[str]
    #: Only the keys spelled out in the literal itself. A key added by a
    #: later assignment may sit behind an ``if``, so it cannot be treated as
    #: unconditionally present.
    literal_keys: frozenset[str]
    has_spread: bool

    @property
    def where(self) -> str:
        return f"{self.module}:{self.lineno} ({self.function})"

    @property
    def scope_key(self) -> str:
        return f"{self.module}::{self.function}"


def _own_nodes(scope: ast.AST) -> list[ast.AST]:
    """Every node belonging to ``scope``, not descending into nested defs."""
    out: list[ast.AST] = []
    stack: list[ast.AST] = [scope]
    while stack:
        node = stack.pop()
        out.append(node)
        for child in ast.iter_child_nodes(node):
            # A nested def is its own scope and gets its own pass, so its
            # locals never bleed into this one.
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            stack.append(child)
    return out


def _scopes(tree: ast.Module) -> list[tuple[str, ast.AST]]:
    scopes: list[tuple[str, ast.AST]] = [("<module>", tree)]
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            scopes.append((node.name, node))
    return scopes


def _literal_str_key(node: ast.expr | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _scan_module(path: Path, module: str) -> list[_FrameSite]:
    tree = ast.parse(path.read_text("utf-8"))
    sites: list[_FrameSite] = []
    seen: set[int] = set()

    for func_name, scope in _scopes(tree):
        nodes = _own_nodes(scope)

        # ``payload = {...}`` — remember which local holds which literal.
        holder_of: dict[int, str] = {}
        for node in nodes:
            if isinstance(node, ast.Assign) and len(node.targets) == 1:
                target, value = node.targets[0], node.value
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                target, value = node.target, node.value
            else:
                continue
            if isinstance(target, ast.Name) and isinstance(value, ast.Dict):
                holder_of[id(value)] = target.id

        # ``payload["extra"] = …`` — a conditional field on the same frame.
        added_later: dict[str, set[str]] = defaultdict(set)
        for node in nodes:
            if not isinstance(node, ast.Assign) or len(node.targets) != 1:
                continue
            target = node.targets[0]
            if not isinstance(target, ast.Subscript):
                continue
            key = _literal_str_key(target.slice)
            if isinstance(target.value, ast.Name) and key is not None:
                added_later[target.value.id].add(key)

        for node in nodes:
            if not isinstance(node, ast.Dict) or id(node) in seen:
                continue
            keys: set[str] = set()
            type_name: str | None = None
            type_expr = ""
            has_type = False
            has_spread = False
            for key_node, value_node in zip(node.keys, node.values, strict=True):
                if key_node is None:
                    has_spread = True
                    continue
                key = _literal_str_key(key_node)
                if key is None:
                    continue
                keys.add(key)
                if key == "type":
                    has_type = True
                    type_name = _literal_str_key(value_node)
                    if type_name is None:
                        type_expr = ast.unparse(value_node)
            if not has_type:
                continue
            seen.add(id(node))
            literal_keys = set(keys)
            holder = holder_of.get(id(node))
            if holder is not None:
                keys |= added_later.get(holder, set())
            sites.append(
                _FrameSite(
                    module=module,
                    lineno=node.lineno,
                    function=func_name,
                    type_name=type_name,
                    type_expr=type_expr,
                    keys=frozenset(keys - {"type"}),
                    literal_keys=frozenset(literal_keys - {"type"}),
                    has_spread=has_spread,
                )
            )
    return sites


def _scan_package() -> list[_FrameSite]:
    sites: list[_FrameSite] = []
    for path in sorted(_PACKAGE.rglob("*.py")):
        sites.extend(_scan_module(path, path.relative_to(_PACKAGE).as_posix()))
    return sites


SITES = _scan_package()
LITERAL_SITES = [s for s in SITES if s.type_name is not None]
DYNAMIC_SITES = [s for s in SITES if s.type_name is None]


class TestScannerItself:
    """If the scanner stops finding frames the rest of this file is vacuous —
    exactly the failure mode #749 was about. Pin a floor and a known frame."""

    def test_the_scan_finds_the_frames(self) -> None:
        assert len(LITERAL_SITES) > 40, (
            "The frame scanner found almost nothing — it has stopped matching "
            "how the server builds payloads, so every other test here passes "
            "on an empty set."
        )

    def test_a_known_frame_is_seen_with_its_fields(self) -> None:
        joined = [s for s in LITERAL_SITES if s.type_name == "joined"]
        assert joined, "No build site found for the 'joined' frame"
        assert "session_token" in joined[0].keys


class TestServerFrames:
    def test_every_frame_built_is_declared(self) -> None:
        built = {s.type_name for s in LITERAL_SITES}
        undeclared = sorted(built - set(SERVER_FRAMES))
        assert not undeclared, (
            f"Frames the server sends with nothing declared in protocol.py: "
            f"{undeclared}. Add a FrameSpec for each."
        )

    def test_every_declared_frame_is_built(self) -> None:
        built = {s.type_name for s in LITERAL_SITES}
        for scoped in _DYNAMIC_TYPE_SITES.values():
            built |= set(scoped)
        unbuilt = sorted(set(SERVER_FRAMES) - built)
        assert not unbuilt, (
            f"protocol.py declares frames no build site sends: {unbuilt}. "
            "Either the frame was removed (drop the FrameSpec) or it is "
            "assembled in a way this scan cannot see (say so in its note)."
        )

    def test_every_build_site_sends_the_required_keys(self) -> None:
        problems: list[str] = []
        for site in LITERAL_SITES:
            spec = SERVER_FRAMES.get(site.type_name or "")
            if spec is None:
                continue
            missing = sorted(spec.required - site.keys)
            if missing:
                problems.append(
                    f"{site.type_name} at {site.where} is missing declared "
                    f"required field(s) {missing}"
                )
        assert not problems, "\n".join(problems)

    def test_no_build_site_sends_an_undeclared_key(self) -> None:
        problems: list[str] = []
        for site in LITERAL_SITES:
            spec = SERVER_FRAMES.get(site.type_name or "")
            if spec is None:
                continue
            extra = sorted(site.keys - spec.required - spec.optional)
            if extra:
                problems.append(
                    f"{site.type_name} at {site.where} sends undeclared "
                    f"field(s) {extra} — add them to its FrameSpec in "
                    f"server/protocol.py (required if every build site sends "
                    f"them, optional otherwise)"
                )
        assert not problems, "\n".join(problems)

    def test_declared_optional_fields_are_really_optional(self) -> None:
        """A field spelled out in every build site's literal is required —
        leaving it optional lets a builder quietly stop sending it.

        Only literal keys count. A ``payload["x"] = …`` after the literal may
        sit behind an ``if``, and from the source alone there is no telling.
        """
        sites_by_type: dict[str, list[_FrameSite]] = defaultdict(list)
        for site in LITERAL_SITES:
            sites_by_type[site.type_name or ""].append(site)
        problems: list[str] = []
        for type_name, spec in SERVER_FRAMES.items():
            sites = sites_by_type.get(type_name, [])
            if not sites or spec.dynamic_keys:
                continue
            always = set.intersection(*(set(s.literal_keys) for s in sites))
            over_declared = sorted(spec.optional & always)
            if over_declared:
                problems.append(
                    f"{type_name}: {over_declared} declared optional but sent "
                    f"by every build site — move them to required"
                )
        assert not problems, "\n".join(problems)

    def test_spread_sites_are_the_declared_ones(self) -> None:
        """``{**payload, "type": …}`` hides the rest of the fields from this
        scan. Which frames do that is part of the contract, not an accident."""
        spread_in_source = {
            s.type_name for s in LITERAL_SITES if s.has_spread
        }
        declared = {t for t, spec in SERVER_FRAMES.items() if spec.dynamic_keys}
        assert spread_in_source == declared, (
            f"Frames merging a runtime dict with ** : {sorted(spread_in_source)}; "
            f"declared as dynamic_keys: {sorted(declared)}. These must match — "
            "the fields of a spread frame cannot be checked from the source, "
            "so protocol.py has to say which frames give that up."
        )

    def test_dynamically_typed_frames_are_declared(self) -> None:
        found = {s.scope_key: s for s in DYNAMIC_SITES}
        assert set(found) == set(_DYNAMIC_TYPE_SITES), (
            f"Frames whose wire 'type' is a variable: {sorted(found)}; "
            f"accounted for: {sorted(_DYNAMIC_TYPE_SITES)}. A frame typed at "
            "runtime cannot be matched to a FrameSpec automatically — list it "
            "in _DYNAMIC_TYPE_SITES with the types it can carry."
        )
        problems: list[str] = []
        for scope_key, type_names in _DYNAMIC_TYPE_SITES.items():
            site = found[scope_key]
            for type_name in type_names:
                spec = SERVER_FRAMES[type_name]
                missing = sorted(spec.required - site.keys)
                extra = sorted(site.keys - spec.required - spec.optional)
                if missing or extra:
                    problems.append(
                        f"{type_name} at {site.where} (type from "
                        f"{site.type_expr!r}): missing {missing}, "
                        f"undeclared {extra}"
                    )
        assert not problems, "\n".join(problems)


class TestClientDispatchCoverage:
    """``CLIENT_MESSAGE_TYPES`` against the handler's real dispatch table.

    Imported, not grepped — that is the #749 fix. Importing ``websocket``
    needs aiohttp, which the test requirements already pull in.
    """

    def test_declared_types_match_the_dispatch_table(self) -> None:
        from custom_components.quizify.server.websocket import (
            QuizifyWebSocketHandler,
        )

        dispatched = set(QuizifyWebSocketHandler._DISPATCH)
        declared = set(CLIENT_MESSAGE_TYPES) - set(OUT_OF_BAND_CLIENT_TYPES)
        assert declared == dispatched, (
            f"Declared but not dispatched: {sorted(declared - dispatched)}; "
            f"dispatched but not declared: {sorted(dispatched - declared)}. "
            "Add the type to CLIENT_MESSAGE_TYPES or remove the handler."
        )

    def test_out_of_band_types_are_not_in_the_dispatch_table(self) -> None:
        from custom_components.quizify.server.websocket import (
            QuizifyWebSocketHandler,
        )

        overlap = OUT_OF_BAND_CLIENT_TYPES & set(QuizifyWebSocketHandler._DISPATCH)
        assert not overlap, (
            f"{sorted(overlap)} are declared as handled before the dispatch "
            "table but are also in it — one of the two is wrong."
        )

    def test_out_of_band_types_are_handled_in_the_source(self) -> None:
        """These three take their own authorization paths, so there is no
        table to compare against; assert each is at least reachable."""
        ws_src = (_PACKAGE / "server" / "websocket.py").read_text("utf-8")
        missing = [t for t in OUT_OF_BAND_CLIENT_TYPES if f'"{t}"' not in ws_src]
        assert not missing, (
            f"Out-of-band client types with no mention in websocket.py: {missing}"
        )
