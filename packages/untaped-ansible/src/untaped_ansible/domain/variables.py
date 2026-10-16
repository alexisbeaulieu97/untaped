"""Static analysis of where Ansible variables are defined, set and read.

``analyse(path, text)`` reads one repository file and reports each place an
Ansible variable is *defined* (role defaults, role vars and argument specs),
*set* (any other YAML mapping key, ``register`` and ``loop_var``) or *read*
(a Jinja expression). Results are stored in an index keyed by (blob, path),
so they must depend only on the path and the text: this module imports
nothing from untaped but its data types (:mod:`.variable_models`), only the
standard library, PyYAML and Jinja2, and the index versions its rows by a
hash of both modules' source.

Rationale that protects the code:

- YAML is *composed*, never constructed, so every scalar stays the text the
  author wrote and carries its source position.
- Jinja is only *parsed*. Compiling, or ``meta.find_undeclared_variables``,
  resolves filters and tests and fails on Ansible's own (``dirname``,
  ``to_json``, ``is version``); a scope-aware walk over the parse tree needs
  none of them.
- Line numbers: a read inside a literal block (``|``) maps each Jinja line to
  its file line. Folded (``>``) blocks report their first content line, and
  every other scalar its first line, because folding loses line breaks.
- A node reached again through a YAML alias is not walked twice, so recursive
  anchors and alias bombs cannot loop or explode.
- ``analyse`` never raises: unparsable YAML or templates, and nesting too deep
  to walk, come back as ``skipped`` with no facts.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Collection, Iterable, Mapping
from typing import Final, Literal

import jinja2
import jinja2.nodes as jn
import yaml

from untaped_ansible.domain.variable_models import FileAnalysis, How, KeyPath, VariableFact

__all__ = [
    "FileAnalysis",
    "How",
    "VariableFact",
    "analyse",
    "defines",
    "selected",
    "uses",
]

_NAME: Final = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_STR_TAG: Final = "tag:yaml.org,2002:str"
_NULL_TAG: Final = "tag:yaml.org,2002:null"
_BARE_KEYS: Final = frozenset({"when", "that", "failed_when", "changed_when", "until"})
_DEFINES_DIRS: Final = frozenset({"defaults", "vars"})
_ARGSPEC_NAMES: Final = frozenset({"argument_specs.yml", "argument_specs.yaml"})
_SCOPED_NAMES: Final = frozenset({"caller", "varargs", "kwargs"})
_LOOKUPS: Final = ("lookup", "query", "q")
_VARS_PLUGINS: Final = frozenset({"vars", "ansible.builtin.vars", "ansible.legacy.vars"})
_LOADER: Final = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
_ENV: Final = jinja2.Environment(extensions=["jinja2.ext.do", "jinja2.ext.loopcontrols"])

_Mode = Literal["sets", "defines", "argspec"]


def selected(path: str) -> Literal["yaml", "template"] | None:
    """Say how ``path`` is read: as YAML, as a Jinja template, or not at all."""
    segments = path.split("/")
    if ".github" in segments or ".gitlab" in segments or segments[-1] == ".gitlab-ci.yml":
        return None
    if _in_templates_dir(segments) or path.endswith(".j2"):
        return "template"
    if path.endswith((".yml", ".yaml")):
        return "yaml"
    if "group_vars" in segments[:-1] or "host_vars" in segments[:-1]:
        return "yaml"
    return None


def analyse(path: str, text: str) -> FileAnalysis:
    """Return the sorted, deduplicated variable facts of one file."""
    kind = selected(path)
    if kind is None or "\x00" in text:  # not selected, or not text (an image under templates/)
        return FileAnalysis(facts=[])
    try:
        if kind == "template":
            facts, skipped = _analyse_template(path, text)
        else:
            facts, skipped = _analyse_yaml(path, text)
    except RecursionError:
        return FileAnalysis(facts=[], skipped="nested too deeply to read")
    except Exception as error:
        return FileAnalysis(facts=[], skipped=f"could not be read: {type(error).__name__}")
    return FileAnalysis(facts=_sorted_unique(facts), skipped=skipped)


def defines(files: Mapping[str, str]) -> set[str]:
    """Return the names defined (``how == "defines"``) by any of ``files``."""
    return {
        fact.name
        for path, text in files.items()
        for fact in analyse(path, text).facts
        if fact.how == "defines"
    }


def uses(files: Mapping[str, str], names: Collection[str]) -> list[VariableFact]:
    """Return every fact in ``files`` whose variable is one of ``names``."""
    wanted = set(names)
    return [
        fact
        for path in sorted(files)
        for fact in analyse(path, files[path]).facts
        if fact.name in wanted
    ]


# --- paths ---------------------------------------------------------------------------


def _in_templates_dir(segments: list[str]) -> bool:
    if len(segments) > 1 and segments[0] == "templates":
        return True
    return len(segments) > 3 and segments[0] == "roles" and segments[2] == "templates"


def _role_relative(segments: list[str]) -> list[str]:
    """Return the segments below the role root (repo root or ``roles/<name>/``)."""
    if len(segments) > 2 and segments[0] == "roles":
        return segments[2:]
    return segments


def _mode(path: str) -> _Mode:
    below = _role_relative(path.split("/"))
    if len(below) > 1 and below[0] in _DEFINES_DIRS:
        return "defines"
    if len(below) == 2 and below[0] == "meta" and below[1] in _ARGSPEC_NAMES:
        return "argspec"
    return "sets"


# --- results -------------------------------------------------------------------------


def _sort_key(fact: VariableFact) -> tuple[int, tuple[tuple[int, int, str], ...], str, str]:
    path = tuple((0, s, "") if isinstance(s, int) else (1, 0, s) for s in fact.key_path)
    return (fact.line, path, fact.name, fact.how)


def _sorted_unique(facts: Iterable[VariableFact]) -> list[VariableFact]:
    unique = {_sort_key(fact): fact for fact in facts}
    return [unique[key] for key in sorted(unique)]


# --- templates -----------------------------------------------------------------------


def _analyse_template(path: str, text: str) -> tuple[list[VariableFact], str | None]:
    try:
        reads = _jinja_reads(text)
    except jinja2.TemplateSyntaxError as exc:
        return [], f"{exc.lineno}: {exc.message}"
    facts = [
        VariableFact(name=name, file=path, line=line, key_path=[], how="reads")
        for name, line in reads
    ]
    return facts, None


def _jinja_reads(source: str) -> list[tuple[str, int]]:
    """Return ``(name, jinja line)`` for each free variable read in ``source``."""
    collector = _ReadCollector()
    collector.visit(_ENV.parse(source), set())
    return collector.reads


class _ReadCollector:
    """Scope-aware walk over a Jinja parse tree collecting free variable reads.

    ``bound`` is the set of names local to the current scope. Statements that
    bind for the rest of a scope (``set``, ``import``, ``macro``) add to it in
    place; constructs that open a scope (``for``, ``macro`` and ``call``
    bodies, ``with``) walk their body with a copy.
    """

    def __init__(self) -> None:
        self.reads: list[tuple[str, int]] = []

    def visit(self, node: jn.Node, bound: set[str]) -> None:
        handler = _HANDLERS.get(type(node))
        if handler is not None:
            handler(self, node, bound)
            return
        for child in node.iter_child_nodes():
            self.visit(child, bound)

    def visit_all(self, nodes: Iterable[jn.Node], bound: set[str]) -> None:
        for node in nodes:
            self.visit(node, bound)

    def read(self, name: str, line: int, bound: set[str]) -> None:
        if name not in bound and _NAME.fullmatch(name):
            self.reads.append((name, line))

    def name(self, node: jn.Node, bound: set[str]) -> None:
        assert isinstance(node, jn.Name)
        if node.ctx == "load":
            self.read(node.name, node.lineno, bound)

    def getitem(self, node: jn.Node, bound: set[str]) -> None:
        assert isinstance(node, jn.Getitem)
        self.visit(node.node, bound)
        self.visit(node.arg, bound)
        if not (isinstance(node.arg, jn.Const) and isinstance(node.arg.value, str)):
            return
        if _is_vars_lookup(node.node, bound):
            self.read(node.arg.value, node.lineno, set())

    def getattr(self, node: jn.Node, bound: set[str]) -> None:
        assert isinstance(node, jn.Getattr)
        self.visit(node.node, bound)
        if _is_vars_lookup(node.node, bound):  # ``vars.V``, ``hostvars[h].V``
            self.read(node.attr, node.lineno, set())

    def call(self, node: jn.Node, bound: set[str]) -> None:
        assert isinstance(node, jn.Call)
        for child in node.iter_child_nodes():
            self.visit(child, bound)
        args = node.args
        if not (any(_is_name(node.node, name, bound) for name in _LOOKUPS) and args):
            return
        if isinstance(args[0], jn.Const) and args[0].value in _VARS_PLUGINS:
            for arg in args[1:]:
                if isinstance(arg, jn.Const) and isinstance(arg.value, str):
                    self.read(arg.value, arg.lineno, set())

    def assign(self, node: jn.Node, bound: set[str]) -> None:
        assert isinstance(node, jn.Assign)
        self.visit(node.node, bound)
        self.bind(node.target, bound)

    def assign_block(self, node: jn.Node, bound: set[str]) -> None:
        assert isinstance(node, jn.AssignBlock)
        self.visit_all(node.body, set(bound))
        if node.filter is not None:
            self.visit(node.filter, bound)
        self.bind(node.target, bound)

    def bind(self, target: jn.Node, bound: set[str]) -> None:
        if isinstance(target, jn.NSRef):
            self.read(target.name, target.lineno, bound)
        else:
            bound.update(_target_names(target))

    def for_(self, node: jn.Node, bound: set[str]) -> None:
        assert isinstance(node, jn.For)
        self.visit(node.iter, bound)
        inner = bound | _target_names(node.target) | {"loop"}
        if node.test is not None:
            self.visit(node.test, set(inner))
        self.visit_all(node.body, set(inner))
        self.visit_all(node.else_, set(bound))  # runs when nothing was iterated

    def macro(self, node: jn.Node, bound: set[str]) -> None:
        assert isinstance(node, jn.Macro)
        bound.add(node.name)
        self.visit_all(node.defaults, bound)
        self.visit_all(node.body, bound | {arg.name for arg in node.args} | _SCOPED_NAMES)

    def call_block(self, node: jn.Node, bound: set[str]) -> None:
        assert isinstance(node, jn.CallBlock)
        self.visit(node.call, bound)
        self.visit_all(node.defaults, bound)
        self.visit_all(node.body, bound | {arg.name for arg in node.args} | _SCOPED_NAMES)

    def with_(self, node: jn.Node, bound: set[str]) -> None:
        assert isinstance(node, jn.With)
        self.visit_all(node.values, bound)
        inner = set(bound)
        for target in node.targets:
            inner.update(_target_names(target))
        self.visit_all(node.body, inner)

    def import_(self, node: jn.Node, bound: set[str]) -> None:
        assert isinstance(node, jn.Import)
        self.visit(node.template, bound)
        bound.add(node.target)

    def from_import(self, node: jn.Node, bound: set[str]) -> None:
        assert isinstance(node, jn.FromImport)
        self.visit(node.template, bound)
        for imported in node.names:
            bound.add(imported if isinstance(imported, str) else imported[1])


def _is_vars_lookup(target: jn.Node, bound: set[str]) -> bool:
    """Whether a subscript or attribute of ``target`` is a variable: ``vars``, ``hostvars[h]``."""
    return _is_name(target, "vars", bound) or (
        isinstance(target, jn.Getitem) and _is_name(target.node, "hostvars", bound)
    )


_HANDLERS: Final[dict[type[jn.Node], Callable[[_ReadCollector, jn.Node, set[str]], None]]] = {
    jn.Name: _ReadCollector.name,
    jn.Getitem: _ReadCollector.getitem,
    jn.Getattr: _ReadCollector.getattr,
    jn.Call: _ReadCollector.call,
    jn.Assign: _ReadCollector.assign,
    jn.AssignBlock: _ReadCollector.assign_block,
    jn.For: _ReadCollector.for_,
    jn.Macro: _ReadCollector.macro,
    jn.CallBlock: _ReadCollector.call_block,
    jn.With: _ReadCollector.with_,
    jn.Import: _ReadCollector.import_,
    jn.FromImport: _ReadCollector.from_import,
}


def _is_name(node: jn.Node, name: str, bound: set[str]) -> bool:
    return isinstance(node, jn.Name) and node.name == name and name not in bound


def _target_names(target: jn.Node) -> set[str]:
    if isinstance(target, jn.Name):
        return {target.name}
    if isinstance(target, jn.Tuple):
        return {name for item in target.items for name in _target_names(item)}
    return set()


# --- YAML ----------------------------------------------------------------------------


def _analyse_yaml(path: str, text: str) -> tuple[list[VariableFact], str | None]:
    try:
        documents = list(yaml.compose_all(text, Loader=_LOADER))
    except yaml.YAMLError as exc:
        return [], _yaml_error(exc)
    present = [(index, doc) for index, doc in enumerate(documents) if not _is_empty(doc)]
    walker = _YamlWalker(path, _mode(path), offset=1 if len(present) > 1 else 0)
    for index, document in present:
        walker.walk(document, [index] if walker.offset else [])
    return walker.facts, walker.skipped


def _is_empty(node: yaml.Node | None) -> bool:
    return node is None or (isinstance(node, yaml.ScalarNode) and node.tag == _NULL_TAG)


def _yaml_error(exc: yaml.YAMLError) -> str:
    if isinstance(exc, yaml.MarkedYAMLError):
        problem = exc.problem or exc.context or "invalid YAML"
        mark = exc.problem_mark or exc.context_mark
        if mark is not None:
            return f"{problem}, line {mark.line + 1}"
        return problem
    lines = str(exc).splitlines()
    return lines[0] if lines else type(exc).__name__


def _is_str(node: yaml.Node) -> bool:
    return isinstance(node, yaml.ScalarNode) and node.tag == _STR_TAG


def _has_jinja(value: str) -> bool:
    return "{{" in value or "{%" in value


class _YamlWalker:
    """Walk composed YAML nodes, recording facts with their key paths.

    ``mode`` says what a mapping key means: ``sets`` for task, play, inventory
    and variable files; ``defines`` for role defaults and vars, whose top-level
    keys define variables; ``argspec`` for role argument specs, whose options
    define them. ``offset`` is 1 when key paths start with a document index.
    """

    def __init__(self, path: str, mode: _Mode, offset: int) -> None:
        self.path = path
        self.mode = mode
        self.offset = offset
        self.facts: list[VariableFact] = []
        self.skipped: str | None = None
        self._seen: set[int] = set()

    def walk(self, node: yaml.Node, key_path: KeyPath) -> None:
        if id(node) in self._seen:
            return
        self._seen.add(id(node))
        if isinstance(node, yaml.MappingNode):
            for key_node, value in node.value:
                if not isinstance(key_node, yaml.ScalarNode) or key_node.value == "<<":
                    continue
                key = str(key_node.value)
                path = [*key_path, key]
                self._key(key_node, path)
                self._value(key, value, path)
        elif isinstance(node, yaml.SequenceNode):
            for index, item in enumerate(node.value):
                self.walk(item, [*key_path, index])
        elif _is_str(node) and _has_jinja(str(node.value)):
            self._reads(node, str(node.value), key_path)

    def _fact(self, name: str, line: int, key_path: KeyPath, how: How) -> None:
        self.facts.append(
            VariableFact(name=name, file=self.path, line=line, key_path=key_path, how=how)
        )

    def _key(self, node: yaml.ScalarNode, key_path: KeyPath) -> None:
        name = str(node.value)
        if not _NAME.fullmatch(name):
            return
        how = self._key_how(key_path[self.offset :])
        if how is not None:
            self._fact(name, node.start_mark.line + 1, key_path, how)

    def _key_how(self, segments: KeyPath) -> How | None:
        if self.mode == "sets":
            return "sets"
        if self.mode == "defines":
            return "defines" if len(segments) == 1 else None
        is_option = (
            len(segments) == 4 and segments[0] == "argument_specs" and segments[2] == "options"
        )
        return "defines" if is_option else None

    def _value(self, key: str, value: yaml.Node, key_path: KeyPath) -> None:
        if self.mode == "sets":
            if key in _BARE_KEYS:
                self._bare(value, key_path)
                return
            parent = key_path[-2] if len(key_path) > 1 else None
            if key == "register" or (key == "loop_var" and parent == "loop_control"):
                self._register(value, key_path)
        self.walk(value, key_path)

    def _register(self, value: yaml.Node, key_path: KeyPath) -> None:
        if _is_str(value) and _NAME.fullmatch(str(value.value)):
            self._fact(str(value.value), value.start_mark.line + 1, key_path, "sets")

    def _bare(self, value: yaml.Node, key_path: KeyPath) -> None:
        """Read ``when``-style values, whose strings are bare Jinja expressions."""
        if _is_str(value):
            self._seen.add(id(value))
            self._bare_scalar(value, key_path)
            return
        if isinstance(value, yaml.SequenceNode):
            self._seen.add(id(value))
            for index, item in enumerate(value.value):
                if _is_str(item):
                    self._seen.add(id(item))
                    self._bare_scalar(item, [*key_path, index])
                else:
                    self.walk(item, [*key_path, index])
            return
        self.walk(value, key_path)

    def _bare_scalar(self, node: yaml.Node, key_path: KeyPath) -> None:
        text = str(node.value)
        self._reads(node, text if _has_jinja(text) else "{{ " + text + " }}", key_path)

    def _reads(self, node: yaml.Node, source: str, key_path: KeyPath) -> None:
        try:
            reads = _jinja_reads(source)
        except jinja2.TemplateSyntaxError as exc:
            if self.skipped is None:
                self.skipped = f"partly: {_file_line(node, exc.lineno)}: {exc.message}"
            return
        for name, line in reads:
            self._fact(name, _file_line(node, line), key_path, "reads")


def _file_line(node: yaml.Node, jinja_line: int) -> int:
    """Map a 1-based line of a scalar's Jinja source to its 1-based file line.

    A literal block keeps its line breaks, so its Jinja lines map one to one
    onto the lines after the ``|`` indicator. A folded block reports its first
    content line; any other scalar, the line it starts on.
    """
    start = node.start_mark.line
    style = getattr(node, "style", None)
    if style == "|":
        return start + 1 + jinja_line
    if style == ">":
        return start + 2
    return start + 1
