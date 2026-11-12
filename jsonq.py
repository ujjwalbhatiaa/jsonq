#!/usr/bin/env python3
"""jsonq - query JSON from the command line.

Reads JSON from a file or stdin and extracts values with a small path
language:

    users.0.name          dotted keys and array indices
    users.*.email         wildcard over objects and arrays
    users[age>30].name    filter array elements (==, !=, >, >=, <, <=)
    users[city=='NYC']    filters also take quoted strings

Aggregations:

    --count               print how many values matched
    --sum [PATH]          sum numbers (optionally at PATH under each match)
    --keys                list the keys of each matched object
    --json                print results as JSON instead of plain text

Only the Python standard library is used. No dependencies.

Exit codes: 0 on success, 1 for runtime problems (invalid JSON, missing
key, unreadable input, bad aggregation), 2 for a malformed query or
command-line usage error.
"""

import argparse
import json
import os
import re
import sys

__version__ = "1.0.0"

_KEY_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_-]*")
_INDEX_RE = re.compile(r"\d+")
_FILTER_OPS = ("==", "!=", ">=", "<=", ">", "<")


class JsonqError(Exception):
    """A user-facing error: message is printed, exit code is non-zero."""


class QuerySyntaxError(JsonqError):
    """The query string could not be parsed."""


class MissingKeyError(JsonqError):
    """A requested key does not exist in the data."""


# ---------------------------------------------------------------------------
# Query parsing
# ---------------------------------------------------------------------------

def _find_closing_bracket(query, start):
    """Return the index of the ']' matching the '[' at `start`.

    Quoted strings inside the brackets may contain ']' characters.
    """
    i = start + 1
    quote = None
    while i < len(query):
        ch = query[i]
        if quote is not None:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = None
        elif ch in ("'", '"'):
            quote = ch
        elif ch == "]":
            return i
        i += 1
    raise QuerySyntaxError("unclosed '[' in query: %r" % query)


def _parse_filter_value(text):
    """Parse the literal on the right-hand side of a filter comparison."""
    text = text.strip()
    if not text:
        raise QuerySyntaxError("missing value in filter")
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ("'", '"'):
        try:
            return json.loads(text) if text[0] == '"' else text[1:-1].replace("\\'", "'")
        except json.JSONDecodeError:
            raise QuerySyntaxError("bad quoted string in filter: %r" % text)
    if text == "true":
        return True
    if text == "false":
        return False
    if text == "null":
        return None
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        pass
    # A bare word is treated as a plain string, e.g. [city==NYC].
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*", text):
        return text
    raise QuerySyntaxError("bad filter value: %r" % text)


def _parse_bracket(query, start, end):
    """Parse the contents of a bracket segment into a step tuple."""
    inner = query[start + 1:end].strip()
    if inner == "*":
        return ("wildcard",)
    if _INDEX_RE.fullmatch(inner):
        return ("index", int(inner))
    for op in _FILTER_OPS:
        pos = inner.find(op)
        if pos > 0:
            field = inner[:pos].strip()
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]*", field):
                raise QuerySyntaxError("bad filter field: %r" % field)
            value = _parse_filter_value(inner[pos + len(op):])
            return ("filter", field, op, value)
    raise QuerySyntaxError("bad bracket segment: [%s]" % inner)


def parse_query(query):
    """Parse a query string into a list of step tuples.

    Step tuples: ("key", name), ("index", n), ("wildcard",),
    ("filter", field, op, value).
    """
    if not query:
        raise QuerySyntaxError("empty query")
    steps = []
    i, n = 0, len(query)
    while i < n:
        ch = query[i]
        if ch == ".":
            i += 1
            if i >= n:
                raise QuerySyntaxError("query must not end with '.'")
            if query[i] == ".":
                raise QuerySyntaxError("empty path segment in query")
            continue
        if ch == "*":
            steps.append(("wildcard",))
            i += 1
        elif ch == "[":
            end = _find_closing_bracket(query, i)
            steps.append(_parse_bracket(query, i, end))
            i = end + 1
        elif ch.isalpha() or ch == "_":
            match = _KEY_RE.match(query, i)
            steps.append(("key", match.group(0)))
            i = match.end()
        elif ch.isdigit():
            match = _INDEX_RE.match(query, i)
            steps.append(("index", int(match.group(0))))
            i = match.end()
        else:
            raise QuerySyntaxError(
                "unexpected character %r in query" % ch)
    if not steps:
        raise QuerySyntaxError("empty query")
    return steps


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------

def _get_nested(obj, dotted):
    """Look up a dotted field path inside `obj`; return (found, value)."""
    current = obj
    for part in dotted.split("."):
        if not isinstance(current, dict) or part not in current:
            return False, None
        current = current[part]
    return True, current


def _matches(actual, op, expected):
    """Compare one value against a filter literal; type errors never match."""
    try:
        if op == "==":
            return actual == expected
        if op == "!=":
            return actual != expected
        if op == ">":
            return actual > expected
        if op == ">=":
            return actual >= expected
        if op == "<":
            return actual < expected
        if op == "<=":
            return actual <= expected
    except TypeError:
        return False
    return False


def _type_name(value):
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    if isinstance(value, str):
        return "string"
    if value is None:
        return "null"
    return type(value).__name__


def evaluate(data, steps):
    """Apply parsed query steps to `data`; return the list of matches."""
    current = [data]
    # Once a wildcard or filter has widened the match set, a key that is
    # missing from some elements is skipped instead of erroring, so
    # `users.*.email` still works when one user has no email address.
    lenient = False
    for step in steps:
        kind = step[0]
        nxt = []
        if kind == "key":
            name = step[1]
            for value in current:
                if not isinstance(value, dict):
                    raise JsonqError(
                        "cannot get key %r from %s" % (name, _type_name(value)))
                if name not in value:
                    if lenient:
                        continue
                    raise MissingKeyError("key %r not found" % name)
                nxt.append(value[name])
        elif kind == "index":
            idx = step[1]
            for value in current:
                if not isinstance(value, list):
                    raise JsonqError(
                        "cannot index %s with [%d]" % (_type_name(value), idx))
                if idx >= len(value):
                    raise JsonqError(
                        "index %d out of range (array has %d items)"
                        % (idx, len(value)))
                nxt.append(value[idx])
        elif kind == "wildcard":
            lenient = True
            for value in current:
                if isinstance(value, dict):
                    nxt.extend(value.values())
                elif isinstance(value, list):
                    nxt.extend(value)
                else:
                    raise JsonqError(
                        "cannot expand '*' over %s" % _type_name(value))
        elif kind == "filter":
            lenient = True
            _, field, op, expected = step
            for value in current:
                if not isinstance(value, list):
                    raise JsonqError(
                        "filter [...] needs an array, got %s"
                        % _type_name(value))
                for element in value:
                    if not isinstance(element, dict):
                        continue
                    found, actual = _get_nested(element, field)
                    if found and _matches(actual, op, expected):
                        nxt.append(element)
        current = nxt
    return current


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def _format_scalar(value):
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    return str(value)


def _format_value(value, as_json):
    if isinstance(value, (dict, list)):
        return json.dumps(value, indent=None if as_json else 2)
    if as_json:
        return json.dumps(value)
    return _format_scalar(value)


def _print_matches(matches, as_json):
    if as_json:
        if len(matches) == 1:
            print(_format_value(matches[0], as_json=True))
        else:
            print(json.dumps(matches))
        return
    for match in matches:
        print(_format_value(match, as_json=False))


def _aggregate_sum(matches, path):
    numbers = []
    if path is None:
        for match in matches:
            if isinstance(match, bool) or not isinstance(match, (int, float)):
                raise JsonqError(
                    "--sum needs numbers, got %s" % _type_name(match))
            numbers.append(match)
    else:
        steps = parse_query(path)
        for match in matches:
            # A match that is an array sums over its elements, so
            # `jsonq 'users' --sum age` totals every user's age.
            targets = match if isinstance(match, list) else [match]
            for target in targets:
                for value in evaluate(target, steps):
                    if isinstance(value, bool) or not isinstance(value, (int, float)):
                        raise JsonqError(
                            "--sum needs numbers, got %s" % _type_name(value))
                    numbers.append(value)
    total = sum(numbers)
    if isinstance(total, float) and total.is_integer():
        total = int(total)
    return total


def _aggregate_keys(matches):
    keys = []
    for match in matches:
        # A match that is an array lists the keys of its elements.
        targets = match if isinstance(match, list) else [match]
        for target in targets:
            if not isinstance(target, dict):
                raise JsonqError(
                    "--keys needs objects, got %s" % _type_name(target))
            for key in target:
                if key not in keys:
                    keys.append(key)
    return keys


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _read_input(path):
    try:
        if path is not None:
            with open(path, "r", encoding="utf-8") as handle:
                return handle.read()
        return sys.stdin.read()
    except OSError as exc:
        raise JsonqError("cannot read %s: %s" % (path or "stdin", exc.strerror or exc))


def build_parser():
    parser = argparse.ArgumentParser(
        prog="jsonq",
        description="Query JSON from a file or stdin with a small path language.")
    parser.add_argument("query", nargs="?", default=None,
                        help="path query, e.g. users[age>30].name "
                             "(omit to print the whole document)")
    parser.add_argument("file", nargs="?", default=None,
                        help="JSON file to read (default: stdin)")
    parser.add_argument("--json", action="store_true",
                        help="print results as JSON")
    parser.add_argument("--count", action="store_true",
                        help="print how many values matched")
    parser.add_argument("--sum", nargs="?", const=None, default=None,
                        metavar="PATH",
                        help="sum the matched numbers, or the numbers at "
                             "PATH under each match")
    parser.add_argument("--keys", action="store_true",
                        help="list the keys of each matched object")
    parser.add_argument("--version", action="version",
                        version="jsonq " + __version__)
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)

    # A lone argument that looks like a path to an existing file is the
    # file, not a query: `jsonq data.json` prints the whole document.
    if args.file is None and args.query is not None:
        if ("/" in args.query or args.query.endswith(".json")) and os.path.isfile(args.query):
            args.file = args.query
            args.query = None

    chosen = [name for name, on in
              (("count", args.count), ("sum", args.sum is not None),
               ("keys", args.keys)) if on]
    if len(chosen) > 1:
        parser.error("--count, --sum and --keys cannot be combined")

    try:
        raw = _read_input(args.file)
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise JsonqError("invalid JSON: %s" % exc)

        matches = [data]
        if args.query is not None:
            steps = parse_query(args.query)
            matches = evaluate(data, steps)

        if args.count:
            print(len(matches))
        elif args.sum is not None:
            total = _aggregate_sum(matches, args.sum)
            print(json.dumps(total) if args.json else _format_scalar(total))
        elif args.keys:
            keys = _aggregate_keys(matches)
            if args.json:
                print(json.dumps(keys))
            else:
                for key in keys:
                    print(key)
        else:
            _print_matches(matches, args.json)
    except JsonqError as exc:
        print("jsonq: error: %s" % exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
