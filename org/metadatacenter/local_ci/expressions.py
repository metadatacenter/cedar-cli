"""GitHub Actions expressions, as far as CEDAR's workflows use them.

A workflow's `if:` conditions and its `${{ }}` substitutions decide which steps run and what they
run with. Local CI evaluates the same expressions, so it runs what GitHub would run for a push to
`develop`. Anything outside the supported language raises UnsupportedExpression rather than
evaluating to a guess: the coverage check then reports the job as one local CI cannot answer for.
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from typing import Any, Callable


class UnsupportedExpression(ValueError):
    """An expression local CI cannot evaluate the way GitHub would."""


_TOKEN = re.compile(r"""
    (?P<space>\s+)
  | (?P<number>-?(?:0x[0-9a-fA-F]+|\d+(?:\.\d+)?(?:[eE][+-]?\d+)?))
  | (?P<string>'(?:[^']|'')*')
  | (?P<operator>&&|\|\||==|!=|<=|>=|<|>|!|\(|\)|\[|\]|\.|,|\*)
  | (?P<name>[A-Za-z_][A-Za-z0-9_-]*)
""", re.VERBOSE)

STATUS_FUNCTIONS = frozenset({"success", "failure", "always", "cancelled"})


@dataclass(frozen=True)
class _Token:
    kind: str
    value: str


def _tokens(text: str) -> list[_Token]:
    tokens = []
    position = 0
    while position < len(text):
        match = _TOKEN.match(text, position)
        if not match:
            raise UnsupportedExpression(f"cannot read the expression at {text[position:]!r}")
        position = match.end()
        kind = match.lastgroup
        if kind != "space":
            tokens.append(_Token(kind, match.group(kind)))
    return tokens


class _Missing:
    """A property that does not exist: null to the expression, distinct from a stored null."""


def _null(value):
    return None if isinstance(value, _Missing) else value


def truthy(value) -> bool:
    value = _null(value)
    if value is None or value is False:
        return False
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value != 0 and not math.isnan(value)
    if isinstance(value, str):
        return value != ""
    return True


def _number(value):
    value = _null(value)
    if value is None:
        return 0
    if isinstance(value, bool):
        return 1 if value else 0
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        text = value.strip()
        if text == "":
            return 0
        try:
            return int(text, 16) if text.lower().startswith("0x") else float(text)
        except ValueError:
            return math.nan
    return math.nan


def _equal(left, right) -> bool:
    left, right = _null(left), _null(right)
    if type(left) is type(right) or (isinstance(left, str) and isinstance(right, str)):
        if isinstance(left, str):
            return left.lower() == right.lower()
        return left == right
    if isinstance(left, (dict, list)) or isinstance(right, (dict, list)):
        return left is right
    return _number(left) == _number(right)


def _compare(operator: str, left, right) -> bool:
    left, right = _null(left), _null(right)
    if isinstance(left, str) and isinstance(right, str):
        left, right = left.lower(), right.lower()
    else:
        left, right = _number(left), _number(right)
        if math.isnan(left) or math.isnan(right):
            return False
    return {"<": left < right, "<=": left <= right, ">": left > right, ">=": left >= right}[operator]


def to_string(value) -> str:
    value = _null(value)
    if value is None:
        return ""
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, (dict, list)):
        return json.dumps(value, indent=2)
    return str(value)


def _format(template: str, *arguments) -> str:
    def replace(match):
        if match.group(0) in ("{{", "}}"):
            return match.group(0)[0]
        index = int(match.group(1))
        if index >= len(arguments):
            raise UnsupportedExpression(f"format() has no argument {index}")
        return to_string(arguments[index])
    return re.sub(r"\{\{|\}\}|\{(\d+)\}", replace, template)


class _Parser:
    def __init__(self, text: str, context: dict, status: Callable[[str], bool] | None,
                 functions: dict[str, Callable] | None):
        self.tokens = _tokens(text)
        self.index = 0
        self.context = context
        self.status = status
        self.functions = functions or {}
        self.used_status = False

    def peek(self, value: str | None = None, kind: str | None = None) -> bool:
        if self.index >= len(self.tokens):
            return False
        token = self.tokens[self.index]
        return (value is None or token.value == value) and (kind is None or token.kind == kind)

    def take(self, value: str | None = None) -> _Token:
        if self.index >= len(self.tokens):
            raise UnsupportedExpression("the expression ends early")
        token = self.tokens[self.index]
        if value is not None and token.value != value:
            raise UnsupportedExpression(f"expected {value!r}, found {token.value!r}")
        self.index += 1
        return token

    def parse(self):
        value = self.disjunction()
        if self.index != len(self.tokens):
            raise UnsupportedExpression(f"unexpected {self.tokens[self.index].value!r}")
        return value

    def disjunction(self):
        value = self.conjunction()
        while self.peek("||"):
            self.take()
            right = self.conjunction()
            value = value if truthy(value) else right
        return value

    def conjunction(self):
        value = self.equality()
        while self.peek("&&"):
            self.take()
            right = self.equality()
            value = right if truthy(value) else value
        return value

    def equality(self):
        value = self.comparison()
        while self.peek("==") or self.peek("!="):
            operator = self.take().value
            right = self.comparison()
            value = _equal(value, right) if operator == "==" else not _equal(value, right)
        return value

    def comparison(self):
        value = self.unary()
        while any(self.peek(operator) for operator in ("<", "<=", ">", ">=")):
            operator = self.take().value
            value = _compare(operator, value, self.unary())
        return value

    def unary(self):
        if self.peek("!"):
            self.take()
            return not truthy(self.unary())
        return self.postfix(self.primary())

    def primary(self):
        token = self.take()
        if token.value == "(":
            value = self.disjunction()
            self.take(")")
            return value
        if token.kind == "number":
            text = token.value
            return int(text, 16) if text.lower().startswith(("0x", "-0x")) else (
                float(text) if any(c in text for c in ".eE") else int(text))
        if token.kind == "string":
            return token.value[1:-1].replace("''", "'")
        if token.kind != "name":
            raise UnsupportedExpression(f"unexpected {token.value!r}")
        name = token.value
        if name in ("true", "false"):
            return name == "true"
        if name == "null":
            return None
        if self.peek("("):
            return self.call(name)
        if name not in self.context:
            raise UnsupportedExpression(f"the context {name!r} is not available locally")
        return self.context[name]

    def call(self, name: str):
        self.take("(")
        arguments = []
        if not self.peek(")"):
            arguments.append(self.disjunction())
            while self.peek(","):
                self.take()
                arguments.append(self.disjunction())
        self.take(")")
        lowered = name.lower()
        if lowered in STATUS_FUNCTIONS:
            if arguments:
                raise UnsupportedExpression(f"{name}() takes no arguments")
            self.used_status = True
            if self.status is None:
                raise UnsupportedExpression(f"{name}() is only meaningful in a condition")
            return self.status(lowered)
        if lowered == "contains":
            haystack, needle = (_null(argument) for argument in arguments)
            if isinstance(haystack, list):
                return any(_equal(item, needle) for item in haystack)
            return to_string(needle).lower() in to_string(haystack).lower()
        if lowered == "startswith":
            return to_string(arguments[0]).lower().startswith(to_string(arguments[1]).lower())
        if lowered == "endswith":
            return to_string(arguments[0]).lower().endswith(to_string(arguments[1]).lower())
        if lowered == "format":
            return _format(to_string(arguments[0]), *arguments[1:])
        if lowered == "join":
            values = _null(arguments[0])
            separator = to_string(arguments[1]) if len(arguments) > 1 else ","
            return separator.join(to_string(item) for item in values) if isinstance(values, list) \
                else to_string(values)
        if lowered == "tojson":
            return json.dumps(_null(arguments[0]), indent=2)
        if lowered == "fromjson":
            try:
                return json.loads(to_string(arguments[0]))
            except json.JSONDecodeError as error:
                raise UnsupportedExpression(f"fromJSON() could not read its argument: {error}")
        if lowered in self.functions:
            return self.functions[lowered](*arguments)
        raise UnsupportedExpression(f"the function {name}() is not available locally")

    def postfix(self, value):
        while True:
            if self.peek("."):
                self.take()
                if self.peek("*"):
                    raise UnsupportedExpression("object filters (.*) are not available locally")
                key = self.take()
                if key.kind not in ("name", "number"):
                    raise UnsupportedExpression(f"unexpected {key.value!r} after '.'")
                value = self.member(value, key.value)
            elif self.peek("["):
                self.take()
                key = self.disjunction()
                self.take("]")
                value = self.member(value, key)
            else:
                return value

    @staticmethod
    def member(value, key):
        value = _null(value)
        if isinstance(value, dict):
            if isinstance(key, str):
                for name, item in value.items():
                    if isinstance(name, str) and name.lower() == key.lower():
                        return item
            elif key in value:
                return value[key]
            return _Missing()
        if isinstance(value, list):
            index = _number(key)
            if isinstance(index, (int, float)) and not math.isnan(index) and float(index).is_integer():
                index = int(index)
                return value[index] if 0 <= index < len(value) else _Missing()
        return _Missing()


def evaluate(text: str, context: dict, *, status: Callable[[str], bool] | None = None,
             functions: dict[str, Callable] | None = None):
    """The value of one expression, without its `${{ }}` delimiters."""
    return _null(_Parser(text, context, status, functions).parse())


def uses_status_function(text: str) -> bool:
    parser = _Parser(text, {}, None, None)
    return any(token.kind == "name" and token.value.lower() in STATUS_FUNCTIONS
               and index + 1 < len(parser.tokens) and parser.tokens[index + 1].value == "("
               for index, token in enumerate(parser.tokens))


_TEMPLATE = re.compile(r"\$\{\{(.*?)\}\}", re.DOTALL)


def _whole_expression(text: str) -> str | None:
    """The expression a value consists of, when it is one `${{ }}` and nothing else.

    `${{ a }} ${{ b }}` also starts with `${{` and ends with `}}`, but it is text holding two
    expressions, so each is substituted into the text rather than the span between them evaluated.
    """
    matches = list(_TEMPLATE.finditer(text))
    if len(matches) == 1 and matches[0].span() == (0, len(text)):
        return matches[0].group(1)
    return None


def interpolate(value, context: dict, *, functions: dict[str, Callable] | None = None):
    """A workflow value with every `${{ }}` replaced, recursively through lists and mappings."""
    if isinstance(value, str):
        whole = _whole_expression(value)
        if whole is not None:
            return evaluate(whole, context, functions=functions)
        return _TEMPLATE.sub(lambda match: to_string(
            evaluate(match.group(1), context, functions=functions)), value)
    if isinstance(value, list):
        return [interpolate(item, context, functions=functions) for item in value]
    if isinstance(value, dict):
        return {key: interpolate(item, context, functions=functions) for key, item in value.items()}
    return value


def condition(text, context: dict, status: Callable[[str], bool], *,
              functions: dict[str, Callable] | None = None) -> bool:
    """Whether an `if:` holds. Without a status function, GitHub requires success() as well."""
    if text is None:
        return status("success")
    if isinstance(text, bool):
        return text and status("success")
    expression = str(text).strip()
    whole = _whole_expression(expression)
    if whole is not None:
        expression = whole.strip()
    elif "${{" in expression:
        raise UnsupportedExpression(f"a condition mixing text and expressions: {text!r}")
    if not uses_status_function(expression):
        return status("success") and truthy(evaluate(expression, context, functions=functions))
    return truthy(evaluate(expression, context, status=status, functions=functions))


def expressions_in(value) -> list[str]:
    """Every `${{ }}` expression a workflow value contains, for the coverage check."""
    if isinstance(value, str):
        return [match.group(1).strip() for match in _TEMPLATE.finditer(value)]
    if isinstance(value, list):
        return [expression for item in value for expression in expressions_in(item)]
    if isinstance(value, dict):
        return [expression for item in value.values() for expression in expressions_in(item)]
    return []


def referenced_contexts(text: str) -> set[str]:
    """The top-level contexts and property paths an expression reads, such as secrets.TOKEN."""
    tokens = _tokens(text)
    references = set()
    for index, token in enumerate(tokens):
        if token.kind != "name" or (index > 0 and tokens[index - 1].value == "."):
            continue
        if index + 1 < len(tokens) and tokens[index + 1].value == "(":
            continue
        if token.value in ("true", "false", "null"):
            continue
        path = token.value
        if index + 2 < len(tokens) and tokens[index + 1].value == "." and tokens[index + 2].kind == "name":
            path = f"{token.value}.{tokens[index + 2].value}"
        references.add(path)
    return references
