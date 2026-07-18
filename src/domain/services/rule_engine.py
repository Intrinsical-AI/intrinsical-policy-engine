# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Rule engine: DSL parser and evaluator for condition expressions.

GRAMMAR (EBNF)
==============
expression     = or_term ;
or_term        = and_term { "OR" and_term } ;
and_term       = factor { "AND" factor } ;
factor         = "NOT" factor
               | "(" expression ")"
               | function_call ;

function_call  = identifier "(" [ arguments ] ")" ;
arguments      = argument { "," argument } ;
argument       = string_literal | list_literal ;
list_literal   = "[" [ string_literal { "," string_literal } ] "]" ;
string_literal = "'" ... "'" | '"' ... '"' ;
identifier     = [A-Za-z_][A-Za-z0-9_.]* ;  (* Dots allowed in identifiers *)

ARCHITECTURE OVERVIEW
=====================
This module parses "when" conditions into an Abstract Syntax Tree (AST),
then evaluates them against flag sets.

Parse Flow:
    1. Input string -> Tokenizer -> Tokens
    2. Tokens -> Recursive Descent Parser -> AST
    3. Cache result for performance

AST Format:
    - Tuples like ("has", "flag_name") or ("and", (node1, node2))
    - Simple, immutable, easy to debug

DSL VERSIONING
==============
The DSL grammar is versioned to ensure forward compatibility.
Rules files must declare a compatible version in the 'version' field.

Supported versions follow semver: MAJOR.MINOR.PATCH
- MAJOR changes break backward compatibility (different grammar)
- MINOR changes add features (backward compatible)
- PATCH changes are bug fixes

Current supported versions: 1.x.x (all 1.x versions are compatible)

Notes:
- Keywords are case-insensitive in the lexer: AND / and / And all parse.
- String literals are raw quoted literals without escape sequences.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterator, Set
from typing import Any, NamedTuple

from src.common.cache import cached_large
from src.domain.exceptions import RuleParseError
from src.domain.types import ASTNode, Flag, PackId

logger = logging.getLogger(__name__)

# ---- DSL Versioning ----
# Semantic versioning: MAJOR.MINOR.PATCH
# Only MAJOR version must match for compatibility
DSL_CURRENT_VERSION = "1.0.0"
DSL_SUPPORTED_MAJOR_VERSIONS = {1}  # Set of supported major versions
_SEMVER_RE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-((?:0|[1-9]\d*|[0-9A-Za-z-][0-9A-Za-z-]*)(?:\.(?:0|[1-9]\d*|[0-9A-Za-z-][0-9A-Za-z-]*))*))?"
    r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)


class DSLVersionError(Exception):
    """Raised when rules file has incompatible DSL version."""

    def __init__(self, found_version: str | None, supported: set[int], message: str | None = None):
        self.found_version = found_version
        self.supported = supported
        if message is None:
            if found_version is None:
                message = (
                    f"Rules file missing 'version' field. "
                    f"Supported major versions: {sorted(supported)}"
                )
            else:
                message = (
                    f"Rules file version '{found_version}' is incompatible. "
                    f"Supported major versions: {sorted(supported)}"
                )
        super().__init__(message)


def parse_semver(version: str) -> tuple[int, int, int] | None:
    """Parse a strict semantic version string into (major, minor, patch).

    Args:
        version: Version string like "1.0.0" or "1.0.0-rc.1+build.7"

    Returns:
        Tuple (major, minor, patch) or None if invalid format.
    """
    if not isinstance(version, str) or not version:
        return None
    match = _SEMVER_RE.fullmatch(version)
    if not match:
        return None
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


def validate_dsl_version(
    rules_data: dict,
    *,
    strict: bool = False,
    supported_majors: set[int] | None = None,
) -> tuple[bool, str | None]:
    """Validate DSL version in rules data.

    Args:
        rules_data: Parsed rules dictionary (from YAML)
        strict: If True, raise DSLVersionError on incompatibility.
                If False, return (False, error_message) instead.
        supported_majors: Override default supported major versions

    Returns:
        Tuple (is_valid, error_message).
        error_message is None if valid.

    Raises:
        DSLVersionError: If strict=True and version is incompatible

    Example:
        >>> rules = {"version": "1.0.0", "derivations": [...]}
        >>> valid, err = validate_dsl_version(rules)
        >>> assert valid and err is None
    """
    if supported_majors is None:
        supported_majors = DSL_SUPPORTED_MAJOR_VERSIONS

    version_str = rules_data.get("version")

    if version_str is None:
        # Missing version field
        err = DSLVersionError(None, supported_majors)
        if strict:
            raise err
        logger.warning(str(err))
        return (False, str(err))

    semver = parse_semver(version_str)
    if semver is None:
        err_msg = (
            f"Invalid version format: '{version_str}'. "
            "Expected semantic version MAJOR.MINOR.PATCH "
            "(optional prerelease/build metadata allowed)"
        )
        if strict:
            raise DSLVersionError(version_str, supported_majors, err_msg)
        logger.warning(err_msg)
        return (False, err_msg)

    major, _, _ = semver
    if major not in supported_majors:
        err = DSLVersionError(version_str, supported_majors)
        if strict:
            raise err
        logger.warning(str(err))
        return (False, str(err))

    logger.debug(f"Rules DSL version {version_str} is compatible (major={major})")
    return (True, None)


# =============================================================================
# CONSTANTS
# =============================================================================

# AST operator names
OP_HAS = "has"
OP_ANY = "any"
OP_ALL = "all"
OP_NOT = "not"
OP_ANY_PREFIX = "any_prefix"

# Safety limits
MAX_PARSE_DEPTH = 32

# List operators that require dict expressions validation
LIST_OPERATORS = {OP_ANY, OP_ALL}


# =============================================================================
# AST CONSTRUCTORS
# =============================================================================


def Has(flag: str) -> ASTNode:
    """AST node for flag presence check."""
    return (OP_HAS, flag)


def AnyOf(*nodes) -> ASTNode:
    """AST node for OR logic."""
    return (OP_ANY, nodes)


def AllOf(*nodes) -> ASTNode:
    """AST node for AND logic."""
    return (OP_ALL, nodes)


def Not(node) -> ASTNode:
    """AST node for negation."""
    return (OP_NOT, node)


def AnyPrefix(prefix: str) -> ASTNode:
    """AST node for prefix wildcard matching."""
    return (OP_ANY_PREFIX, prefix)


# =============================================================================
# TOKENIZER
# =============================================================================


class Token(NamedTuple):
    """Token produced by the rule lexer (type/value/position)."""

    type: str
    value: str
    pos: int


# Token types
TOK_IDENT = "IDENT"
TOK_STRING = "STRING"
TOK_LPAREN = "LPAREN"
TOK_RPAREN = "RPAREN"
TOK_LBRACKET = "LBRACKET"
TOK_RBRACKET = "RBRACKET"
TOK_COMMA = "COMMA"
TOK_AND = "AND"
TOK_OR = "OR"
TOK_NOT = "NOT"
TOK_EOF = "EOF"

# Regex patterns for tokens
# Order matters: keywords must be checked before generic identifiers if they overlap,
# but here we can match identifiers and check keywords in logic.
# Strings match '...' or "..."
SPEC = [
    (TOK_STRING, r"'[^']*'|\"[^\"]*\""),
    (TOK_LPAREN, r"\("),
    (TOK_RPAREN, r"\)"),
    (TOK_LBRACKET, r"\["),
    (TOK_RBRACKET, r"\]"),
    (TOK_COMMA, r","),
    (TOK_AND, r"\band\b"),
    (TOK_OR, r"\bor\b"),
    (TOK_NOT, r"\bnot\b"),
    (TOK_IDENT, r"[A-Za-z_][A-Za-z0-9_.:*-]*"),  # Allow dots, colons, stars, dashes
    ("SKIP", r"\s+"),  # Skip whitespace
    ("MISMATCH", r"."),  # Any other char
]


def tokenize(text: str) -> Iterator[Token]:
    """Lexical analyzer generator.

    Tokenizes a string expression into a sequence of tokens for parsing.
    Supports keywords (AND, OR, NOT), identifiers, strings, parentheses,
    brackets, and commas.

    Args:
        text: Input string to tokenize.

    Yields:
        Token objects with type, value, and position.

    Raises:
        RuleParseError: If an unexpected character is encountered.

    Example:
        >>> list(tokenize("has('flag1') AND has('flag2')"))
        [Token(type='IDENT', value='has', pos=0), Token(type='LPAREN', value='(', pos=3), ...]
    """
    regex = "|".join(f"(?P<{name}>{pattern})" for name, pattern in SPEC)
    # Use re.IGNORECASE to handle case-insensitive keywords (AND, OR, NOT)
    for match in re.finditer(regex, text, flags=re.IGNORECASE):
        kind = match.lastgroup
        value = match.group()
        if kind == "SKIP":
            continue
        elif kind == "MISMATCH":
            raise RuleParseError(
                expression=text, reason=f"Unexpected character '{value}' at index {match.start()}"
            )
        else:
            if kind == TOK_STRING:
                # Strip quotes
                value = value[1:-1]
            if kind is None:
                continue
            yield Token(kind, value, match.start())
    yield Token(TOK_EOF, "", len(text))


# =============================================================================
# RECURSIVE DESCENT PARSER
# =============================================================================


class Parser:
    """Recursive-descent parser for the rule DSL.

    Parses tokenized expressions into Abstract Syntax Trees (AST) using
    recursive descent. Supports operator precedence and nested expressions.
    """

    def __init__(self, text: str, initial_depth: int = 0):
        """Tokenize input text and set initial parser state.

        Args:
            text: Input string expression to parse.
            initial_depth: Initial recursion depth (used for depth tracking).
        """
        self.text = text
        self.tokens = list(tokenize(text))
        self.pos = 0
        self.depth = initial_depth

    def current(self) -> Token:
        """Return the current token without consuming it.

        Returns:
            Current token in the stream, or EOF token if at end.
        """
        if self.pos < len(self.tokens):
            return self.tokens[self.pos]
        return self.tokens[-1]

    def consume(self, expected_type: str | None = None) -> Token:
        """Consume the current token, optionally asserting its type.

        Args:
            expected_type: Optional token type to assert. Raises RuleParseError
                if current token doesn't match.

        Returns:
            The consumed token.

        Raises:
            RuleParseError: If expected_type is provided and doesn't match current token.
        """
        token = self.current()
        if expected_type and token.type != expected_type:
            raise RuleParseError(
                expression=self.text,
                reason=(
                    f"Expected {expected_type}, got {token.type} ('{token.value}') "
                    f"at index {token.pos}"
                ),
            )
        self.pos += 1
        return token

    def parse(self) -> ASTNode:
        """Parse the full expression and return its AST.

        Returns:
            Root ASTNode representing the parsed expression.

        Raises:
            RuleParseError: If expression is empty, invalid, or has unexpected
                tokens at the end.
        """
        if (not self.tokens or self.tokens[0].type == TOK_EOF) and not self.text.strip():
            raise RuleParseError(expression="<empty>", reason="Empty expression")

        node = self.expression()
        if self.current().type != TOK_EOF:
            raise RuleParseError(
                expression=self.text,
                reason=f"Unexpected token {self.current().value} at end of expression",
            )
        return node

    def expression(self) -> ASTNode:
        """Parse an expression (entry point)."""
        # expression = or_term
        return self.or_term()

    def or_term(self) -> ASTNode:
        """Parse OR-separated terms."""
        # or_term = and_term { "OR" and_term }
        node = self.and_term()
        while self.current().type == TOK_OR:
            self.consume(TOK_OR)
            rhs = self.and_term()
            node = AnyOf(node, rhs)
        return node

    def and_term(self) -> ASTNode:
        """Parse AND-separated factors."""
        # and_term = factor { "AND" factor }
        node = self.factor()
        while self.current().type == TOK_AND:
            self.consume(TOK_AND)
            rhs = self.factor()
            node = AllOf(node, rhs)
        return node

    def factor(self) -> ASTNode:
        """Parse NOT/grouping/function-call factors."""
        # factor = "NOT" factor | "(" expression ")" | function_call
        self.depth += 1
        if self.depth > MAX_PARSE_DEPTH:
            raise RuleParseError(expression=self.text, reason="Expression too deeply nested")

        token = self.current()

        if token.type == TOK_NOT:
            self.consume(TOK_NOT)
            node = Not(self.factor())
            self.depth -= 1
            return node

        if token.type == TOK_LPAREN:
            self.consume(TOK_LPAREN)
            node = self.expression()
            self.consume(TOK_RPAREN)
            self.depth -= 1
            return node

        if token.type == TOK_IDENT:
            node = self.function_call()
            self.depth -= 1
            return node

        raise RuleParseError(
            expression=self.text, reason=f"Unexpected token {token.value} at index {token.pos}"
        )

    def function_call(self) -> ASTNode:
        """Parse function_call := IDENT '(' arguments? ')' or bare identifiers."""
        # function_call = identifier "(" [ arguments ] ")"
        # Also supports bare identifiers if they look like flags?
        # The previous parser supported "flag1 and flag2" without has().
        # Let's check if it's a function call (followed by LPAREN).

        ident = self.consume(TOK_IDENT).value

        if self.current().type == TOK_LPAREN:
            self.consume(TOK_LPAREN)
            args = []
            if self.current().type != TOK_RPAREN:
                args = self.arguments()
            self.consume(TOK_RPAREN)

            return self._build_func_node(ident, args)
        else:
            # Bare identifier -> treat as Has(ident) or AnyPrefix if ends with .*
            # This maintains backward compatibility with "flag1 and flag2" syntax
            if ident.endswith(".*"):
                return AnyPrefix(ident[:-2])
            return Has(ident)

    def arguments(self) -> list[Any]:
        """Parse a comma-separated list of arguments."""
        # arguments = argument { "," argument }
        args = [self.argument()]
        while self.current().type == TOK_COMMA:
            self.consume(TOK_COMMA)
            args.append(self.argument())
        return args

    def argument(self) -> Any:
        """Parse a single argument (string literal or list)."""
        # argument = string_literal | list_literal
        token = self.current()
        if token.type == TOK_STRING:
            return self.consume(TOK_STRING).value
        if token.type == TOK_LBRACKET:
            return self.list_literal()

        raise RuleParseError(
            expression=self.text, reason=f"Expected string or list argument, got {token.type}"
        )

    def list_literal(self) -> list[str]:
        """Parse [ 'foo', 'bar' ] style lists."""
        # list_literal = "[" [ string_literal { "," string_literal } ] "]"
        self.consume(TOK_LBRACKET)
        items = []
        if self.current().type != TOK_RBRACKET:
            if self.current().type == TOK_STRING:
                items.append(self.consume(TOK_STRING).value)
                while self.current().type == TOK_COMMA:
                    self.consume(TOK_COMMA)
                    items.append(self.consume(TOK_STRING).value)
            else:
                raise RuleParseError(expression=self.text, reason="List must contain strings")
        self.consume(TOK_RBRACKET)
        return items

    def _build_func_node(self, func_name: str, args: list[str | list[str]]) -> ASTNode:
        """Construct AST nodes for supported function names.

        Args:
            func_name: Function name ('has', 'any', or 'has_any').
            args: Function arguments. For 'has'/'any': [str].
                For 'has_any': [list[str]].

        Returns:
            ASTNode representing the parsed function call.

        Raises:
            RuleParseError: If function name is unsupported or arguments are invalid.

        Example:
            >>> parser = Parser("has('flag.provider')")
            >>> parser._build_func_node("has", ["flag.provider"])
            ('has', 'flag.provider')
        """
        func_lower = func_name.lower()
        if func_lower == "has":
            if len(args) != 1 or not isinstance(args[0], str):
                raise RuleParseError(
                    expression=self.text, reason="has() requires 1 string argument"
                )
            return Has(args[0])

        if func_lower == "any":
            if len(args) != 1 or not isinstance(args[0], str):
                raise RuleParseError(
                    expression=self.text, reason="any() requires 1 string argument"
                )
            flag_value = args[0]
            if flag_value.endswith(".*"):
                return AnyPrefix(flag_value[:-2])
            return Has(flag_value)

        if func_lower == "has_any":
            if len(args) != 1 or not isinstance(args[0], list):
                raise RuleParseError(
                    expression=self.text, reason="has_any() requires 1 list argument"
                )
            return AnyOf(*[Has(x) for x in args[0]])

        supported = "has(), any(), has_any()"
        raise RuleParseError(
            expression=self.text,
            reason=f"Unknown function '{func_name}'. Supported functions: {supported}",
        )


# =============================================================================
# PARSING HELPERS
# =============================================================================


@cached_large
def _parse_when_string_cached(s: str, _depth: int) -> ASTNode:
    """Parse when condition string into AST using Recursive Descent."""
    if not s.strip():
        raise RuleParseError(expression="<empty>", reason="Empty expression")
    try:
        # Pass current depth to parser to enforce global recursion limit
        parser = Parser(s, initial_depth=_depth)
        return parser.parse()
    except RecursionError:
        raise RuleParseError(
            expression=s[:100], reason="Expression too complex (recursion limit)"
        ) from None


def _parse_dict_when(obj: dict, _depth: int) -> ASTNode:
    """Parse dict-based DSL into AST.

    Args:
        obj: Dictionary containing when condition
        _depth: Current recursion depth

    Returns:
        AST node

    Raises:
        RuleParseError: If dict syntax is unsupported
    """
    if OP_HAS in obj:
        return Has(obj[OP_HAS])
    if OP_ANY in obj:
        return AnyOf(*[parse_when(x, _depth + 1) for x in obj[OP_ANY]])
    if OP_ALL in obj:
        return AllOf(*[parse_when(x, _depth + 1) for x in obj[OP_ALL]])
    if OP_NOT in obj:
        return Not(parse_when(obj[OP_NOT], _depth + 1))
    if OP_ANY_PREFIX in obj:
        prefix = obj[OP_ANY_PREFIX]
        if not prefix:
            raise RuleParseError(expression=str(obj), reason="any_prefix requires non-empty prefix")
        return AnyPrefix(prefix)
    raise RuleParseError(expression=str(obj), reason="Unsupported dict-based when syntax")


def parse_when(obj: dict[str, Any] | str | None, _depth: int = 0) -> ASTNode:
    """Parse 'when' condition into AST.

    Parses both dict-based and string-based DSL conditions into an Abstract
    Syntax Tree (AST) for evaluation. Supports nested conditions, logical
    operators, and prefix matching.

    Args:
        obj: Condition to parse. Can be:
            - Dict DSL: {"has": "flag"}, {"any": [...]}, {"all": [...]}, {"not": ...}
            - String DSL: "has('flag')", "any('prefix.*')", "flag1 AND flag2", etc.
            - None: Always true (returns empty AllOf)
        _depth: Internal recursion depth counter (used to prevent stack overflow).

    Returns:
        ASTNode tuple representing the parsed condition tree.

    Raises:
        RuleParseError: If expression is invalid, too deeply nested, or contains
            unsupported syntax.

    Example:
        >>> parse_when({"has": "role.source"})
        ('has', 'role.source')
        >>> parse_when("has('flag1') AND has('flag2')")
        ('all', (('has', 'flag1'), ('has', 'flag2')))
    """
    if _depth > MAX_PARSE_DEPTH:
        raise RuleParseError(expression=str(obj)[:100], reason="Expression too deeply nested")

    # Dict-based DSL
    if isinstance(obj, dict):
        return _parse_dict_when(obj, _depth)

    # None = always true
    if obj is None:
        return AllOf()

    # String-based DSL
    if isinstance(obj, str):
        return _parse_when_string_cached(obj, _depth)

    raise RuleParseError(expression=str(obj), reason=f"Unsupported type: {type(obj).__name__}")


def eval_ast(ast: ASTNode, flags: Set[Flag]) -> bool:
    """Evaluate AST against a set of flags.

    Recursively evaluates an AST condition tree against the provided flag set.
    Supports all DSL operators: has, any, all, not, any_prefix.

    Args:
        ast: Parsed AST node from parse_when(). Can be None (always true),
            a tuple (operator, args), or a simple flag string.
        flags: Set of active flags to evaluate against.

    Returns:
        True if the AST condition is satisfied by the flags, False otherwise.

    Raises:
        RuleEvaluationError: If AST structure is invalid or operator is unknown.

    Example:
        >>> flags = {"role.source", "classification.employment"}
        >>> ast = parse_when("has('role.source') AND has('classification.employment')")
        >>> eval_ast(ast, flags)
        True
    """
    from src.domain.exceptions import RuleEvaluationError

    # None AST means "always true" (empty condition)
    if ast is None:
        return True

    if not isinstance(ast, tuple):
        raise RuleEvaluationError(
            f"Invalid AST structure: expected tuple, got {type(ast).__name__}"
        )

    op, *args = ast

    if op == OP_HAS:
        return args[0] in flags
    if op == OP_NOT:
        return not eval_ast(args[0], flags)
    if op == OP_ANY:
        return any(eval_ast(node, flags) for node in args[0])
    if op == OP_ALL:
        return all(eval_ast(node, flags) for node in args[0])
    if op == OP_ANY_PREFIX:
        prefix = args[0]
        # Match namespace-style prefixes: exact flag or prefix followed by a dot.
        # This avoids treating single-letter prefixes like "o" as matching
        # unrelated flags such as "other.flag".
        return any(flag == prefix or flag.startswith(prefix + ".") for flag in flags)

    raise RuleEvaluationError(f"Unknown AST operator: {op}")


def select_actions(flags: set[Flag], actions: list[dict[str, Any] | Any]) -> list[str]:
    """Select action IDs whose 'when' conditions are satisfied.

    Filters actions based on their 'when' conditions. Actions with
    ``when=None`` are always selected.

    Args:
        flags: Set of active flags to evaluate conditions against.
        actions: List of ActionDefinition objects (Pydantic) or dicts with
            'id' and optional 'when' keys. Each action must have an 'id' field
            and optionally a 'when' condition (dict DSL, string DSL, or None).

    Returns:
        List of action IDs whose conditions are satisfied or have no condition.

    Example:
        >>> flags = {"role.source"}
        >>> actions = [
        ...     {"id": "CTRL-9-RMS", "when": "has('role.source')"},
        ...     {"id": "CTRL-10-DATA", "when": None},  # Always selected
        ...     {"id": "CTRL-11-TEST", "when": {"has": "role.operator"}},  # Not selected
        ... ]
        >>> select_actions(flags, actions)
        ['CTRL-9-RMS', 'CTRL-10-DATA']
    """
    result = []
    for action in actions:
        # Handle both Pydantic objects and dicts
        act_id = action.get("id") if isinstance(action, dict) else getattr(action, "id", None)
        when_clause = (
            action.get("when") if isinstance(action, dict) else getattr(action, "when", None)
        )

        # Only explicit null means "always". Empty strings/dicts are invalid contract data
        # and must be parsed/validated rather than silently treated as truthy.
        if act_id and (when_clause is None or eval_ast(parse_when(when_clause), flags)):
            result.append(act_id)
    return result


# =============================================================================
# ACTION SELECTION & FILTERING
# =============================================================================


def apply_packs(flags: set[Flag], rules) -> tuple[list[str], list[PackId], bool]:
    """Apply action packs based on flags.

    Evaluates pack rules and adds their actions to the result set. Packs are
    evaluated in order, and their actions are accumulated.

    Args:
        flags: Set of active flags to evaluate pack conditions against.
        rules: RulesContract containing pack definitions with 'when' conditions
            and 'actions' lists.

    Returns:
        Tuple containing:
            - actions: List of action IDs from all fired packs.
            - fired_packs: List of pack IDs that were activated.
            - halted: Whether any pack triggered a halt condition.

    Example:
        >>> flags = {"role.source", "classification.employment"}
        >>> rules = RulesContract(packs=[
        ...     PackRule(
        ...         id="PACK-HR",
        ...         when="has('classification.employment')",
        ...         actions=["CTRL-9-RMS"],
        ...     )
        ... ])
        >>> actions, packs, halted = apply_packs(flags, rules)
        >>> assert "CTRL-9-RMS" in actions
        >>> assert "PACK-HR" in packs
    """
    selected, fired, halted = [], [], False

    for pack in rules.packs:
        if eval_ast(parse_when(pack.when), flags):
            fired.append(pack.id)
            selected.extend(pack.add_actions)
            if pack.halt:
                halted = True
                break

    return selected, fired, halted


def evaluate_stops(flags: set[Flag], rules) -> dict | None:
    """Evaluate stop conditions and return outcome if triggered.

    Stops are special rules that halt further processing when certain flag
    combinations are detected (e.g., blocked systems, out-of-scope).

    Args:
        flags: Set of active flags to evaluate stop conditions against.
        rules: RulesContract containing stop definitions with 'when' conditions
            and 'outcome' values.

    Returns:
        Stop outcome dict with 'outcome' and 'stop_id' keys if a stop is
        triggered, None otherwise.

    Example:
        >>> flags = {"blocked.unsupported_use"}
        >>> rules = RulesContract(stops=[
        ...     StopRule(
        ...         id="STOP-BLOCKED",
        ...         when="has('blocked.unsupported_use')",
        ...         outcome="blocked"
        ...     )
        ... ])
        >>> result = evaluate_stops(flags, rules)
        >>> assert result["outcome"] == "blocked"
    """
    for stop in rules.stops:
        if eval_ast(parse_when(stop.when), flags):
            return {"outcome": stop.outcome, "stop_id": stop.id}

    return None


def apply_role_filter(
    action_ids: list[str], actions_catalog: list[Any], flags: set[Flag]
) -> list[str]:
    """Filter actions based on active role flags.

    Removes actions that don't apply to the active roles. Actions with
    applies_to='any' are always included.

    Args:
        action_ids: List of action IDs to filter.
        actions_catalog: List of ActionDefinition objects (Pydantic) or dicts
            containing 'id' and 'applies_to' fields.
        flags: Set of active flags (used to extract roles).

    Returns:
        Filtered list of action IDs that apply to the active roles.

    Example:
        >>> flags = {"role.source"}
        >>> actions = [
        ...     {"id": "CTRL-9-RMS", "applies_to": "provider"},
        ...     {"id": "DP-26-OVERSIGHT", "applies_to": "deployer"},
        ... ]
        >>> filtered = apply_role_filter(["CTRL-9-RMS", "DP-26-OVERSIGHT"], actions, flags)
        >>> assert "CTRL-9-RMS" in filtered
        >>> assert "DP-26-OVERSIGHT" not in filtered
    """
    # Extract roles generically from role.<role_id> flags so framework packs can
    # declare arbitrary role vocabularies (e.g. ict_provider).
    roles = {
        str(flag).split(".", 1)[1]
        for flag in flags
        if isinstance(flag, str) and flag.startswith("role.") and "." in flag
    }

    # Build map for quick lookup (handle both Pydantic objects and dicts)
    action_info = {}
    for action in actions_catalog:
        # Handle both Pydantic objects and dicts
        action_id = action.get("id") if isinstance(action, dict) else getattr(action, "id", None)

        if action_id:
            action_info[action_id] = action

    # Filter actions by role applicability
    result = []
    for aid in action_ids:
        action = action_info.get(aid)
        if action is None:
            # E17: Log warning for actions not in catalog
            logger.warning(f"Action '{aid}' not found in catalog; keeping (may be from pack)")
            result.append(aid)
        else:
            # Handle both Pydantic objects and dicts
            applies_to = (
                action.get("applies_to", "any")
                if isinstance(action, dict)
                else getattr(action, "applies_to", "any")
            )

            # Handle list of roles (ActionDefinition now allows list[str])
            if isinstance(applies_to, list):
                # Apply if ANY of the target roles are present (OR logic)
                # or if 'any' is in the list
                if "any" in applies_to or any(r in roles for r in applies_to):
                    result.append(aid)
            else:
                # Single role string
                if applies_to == "any" or applies_to in roles:
                    result.append(aid)

    return result


def _validate_list_operator(obj: dict, operator: str) -> None:
    """Validate that a list operator contains only dict or string expressions."""
    seq = obj[operator]
    # Allow dicts (nested DSL) OR strings (string DSL)
    if not isinstance(seq, list) or not all(isinstance(x, (dict, str)) for x in seq):
        raise RuleParseError(
            expression=str(obj), reason=f"'{operator}' must be a list of dict or string expressions"
        )


def validate_when(obj: Any) -> None:
    """Validate a 'when' expression; raise RuleParseError on invalid input."""
    if isinstance(obj, dict):
        # Enforce structure for dict DSL
        for operator in LIST_OPERATORS:
            if operator in obj:
                _validate_list_operator(obj, operator)

    # Fallback to parser to validate semantics and string-DSL
    _ = parse_when(obj)


def _collect_flags_prefixes(ast: ASTNode) -> tuple[set[Flag], set[str]]:
    """Collect exact flags and prefix patterns from AST.

    Refactored to match AST structure produced by Recursive Descent Parser.
    AST is tuple: (op, arg) or (op, (arg1, arg2, ...))
    """
    if not isinstance(ast, tuple):
        return set(), set()

    op = ast[0]
    args = ast[1]

    if op == OP_HAS:
        # args is flag string
        return {args}, set()
    if op == OP_ANY_PREFIX:
        # args is prefix string
        return set(), {args}
    if op == OP_NOT:
        # args is single node
        return _collect_flags_prefixes(args)
    if op in LIST_OPERATORS:
        # args is tuple of nodes
        has_acc: set[Flag] = set()
        pref_acc: set[str] = set()
        for node in args:
            h, p = _collect_flags_prefixes(node)
            has_acc |= h
            pref_acc |= p
        return has_acc, pref_acc

    return set(), set()


def analyze_when(obj: dict[str, Any] | str | None) -> tuple[set[Flag], set[str]]:
    """Return (has_flags, prefixes_used) from a 'when' expression.

    Args:
        obj: Condition to analyze (dict DSL, string DSL, or None).

    Returns:
        Tuple of (set of exact flags referenced, set of prefix patterns used).
    """
    ast = parse_when(obj)
    return _collect_flags_prefixes(ast)
