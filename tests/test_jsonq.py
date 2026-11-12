"""Tests for jsonq: path language, filters, aggregations, CLI behaviour."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jsonq import (  # noqa: E402
    JsonqError,
    MissingKeyError,
    QuerySyntaxError,
    _aggregate_keys,
    _aggregate_sum,
    evaluate,
    parse_query,
)

JSONQ = Path(__file__).resolve().parent.parent / "jsonq.py"

SAMPLE = {
    "users": [
        {"name": "Ana", "age": 28, "city": "Edmonton", "email": "ana@example.com",
         "tags": ["dev", "ml"]},
        {"name": "Ben", "age": 35, "city": "Toronto", "tags": ["dev"]},
        {"name": "Cid", "age": 41, "city": "Edmonton", "email": "cid@example.com",
         "tags": []},
    ],
    "meta": {"count": 3, "active": True, "ratio": 1.5, "note": None},
}


def run_cli(*args, stdin_text=None):
    return subprocess.run(
        [sys.executable, str(JSONQ), *args],
        input=stdin_text, capture_output=True, text=True, timeout=30)


def run_cli_json(data, *args):
    return run_cli(*args, stdin_text=json.dumps(data))


def query(data, text):
    return evaluate(data, parse_query(text))


# --- parsing ---------------------------------------------------------------

def test_parse_simple_dotted_path():
    assert parse_query("users.0.name") == [
        ("key", "users"), ("index", 0), ("key", "name")]


def test_parse_wildcard():
    assert parse_query("users.*.email") == [
        ("key", "users"), ("wildcard",), ("key", "email")]


def test_parse_filter_gt():
    assert parse_query("users[age>30]") == [
        ("key", "users"), ("filter", "age", ">", 30)]


def test_parse_filter_all_operators():
    for op in ("==", "!=", ">", ">=", "<", "<="):
        steps = parse_query("users[age%s30]" % op)
        assert steps[1] == ("filter", "age", op, 30)


def test_parse_filter_quoted_string():
    steps = parse_query("users[city=='New York']")
    assert steps[1] == ("filter", "city", "==", "New York")


def test_parse_filter_double_quoted_string():
    steps = parse_query('users[city=="Toronto"]')
    assert steps[1] == ("filter", "city", "==", "Toronto")


def test_parse_filter_bare_string():
    steps = parse_query("users[city==Edmonton]")
    assert steps[1] == ("filter", "city", "==", "Edmonton")


def test_parse_filter_bool_and_null():
    assert parse_query("x[active==true]")[1][3] is True
    assert parse_query("x[active==false]")[1][3] is False
    assert parse_query("x[note==null]")[1][3] is None


def test_parse_filter_float():
    assert parse_query("x[ratio>=1.5]")[1][3] == 1.5


def test_parse_bracket_index_and_wildcard():
    assert parse_query("users[0]") == [("key", "users"), ("index", 0)]
    assert parse_query("users[*]") == [("key", "users"), ("wildcard",)]


def test_parse_empty_query_raises():
    with pytest.raises(QuerySyntaxError):
        parse_query("")


def test_parse_trailing_dot_raises():
    with pytest.raises(QuerySyntaxError):
        parse_query("users.")


def test_parse_double_dot_raises():
    with pytest.raises(QuerySyntaxError):
        parse_query("users..name")


def test_parse_unclosed_bracket_raises():
    with pytest.raises(QuerySyntaxError):
        parse_query("users[age>30")


def test_parse_bad_character_raises():
    with pytest.raises(QuerySyntaxError):
        parse_query("users#name")


def test_parse_bad_bracket_raises():
    with pytest.raises(QuerySyntaxError):
        parse_query("users[age?30]")


# --- evaluation: paths and wildcards ---------------------------------------

def test_nested_key_path():
    assert query(SAMPLE, "meta.count") == [3]
    assert query(SAMPLE, "meta.ratio") == [1.5]


def test_array_index():
    assert query(SAMPLE, "users.0.name") == ["Ana"]
    assert query(SAMPLE, "users.0.tags") == [["dev", "ml"]]


def test_index_out_of_range_errors():
    with pytest.raises(JsonqError):
        query(SAMPLE, "users.9.name")


def test_missing_key_errors():
    with pytest.raises(MissingKeyError):
        query(SAMPLE, "meta.nope")


def test_key_on_scalar_errors():
    with pytest.raises(JsonqError):
        query(SAMPLE, "meta.count.x")


def test_index_on_object_errors():
    with pytest.raises(JsonqError):
        query(SAMPLE, "meta.0")


def test_wildcard_over_array():
    assert query(SAMPLE, "users.*.name") == ["Ana", "Ben", "Cid"]


def test_wildcard_over_object():
    assert query(SAMPLE, "meta.*") == [3, True, 1.5, None]


def test_wildcard_skips_missing_keys():
    # Ben has no email; the lenient wildcard path skips him cleanly.
    assert query(SAMPLE, "users.*.email") == ["ana@example.com", "cid@example.com"]


def test_wildcard_on_scalar_errors():
    with pytest.raises(JsonqError):
        query(SAMPLE, "meta.count.*")


def test_deep_nested_path():
    assert query(SAMPLE, "users.1.tags.0") == ["dev"]


# --- evaluation: filters ---------------------------------------------------

def test_filter_gt():
    assert [u["name"] for u in query(SAMPLE, "users[age>30]")] == ["Ben", "Cid"]


def test_filter_gte_and_lte():
    assert [u["name"] for u in query(SAMPLE, "users[age>=35]")] == ["Ben", "Cid"]
    assert [u["name"] for u in query(SAMPLE, "users[age<=28]")] == ["Ana"]


def test_filter_lt_and_ne():
    assert [u["name"] for u in query(SAMPLE, "users[age<35]")] == ["Ana"]
    assert [u["name"] for u in query(SAMPLE, "users[city!=Edmonton]")] == ["Ben"]


def test_filter_string_equality():
    assert [u["name"] for u in query(SAMPLE, "users[city==Edmonton]")] == ["Ana", "Cid"]


def test_filter_then_key():
    assert query(SAMPLE, "users[age>30].name") == ["Ben", "Cid"]


def test_filter_no_match_is_empty():
    assert query(SAMPLE, "users[age>100]") == []


def test_filter_on_non_array_errors():
    with pytest.raises(JsonqError):
        query(SAMPLE, "meta[age>30]")


def test_filter_missing_field_excludes_element():
    data = {"items": [{"a": 1}, {"b": 2}, {"a": 5}]}
    assert query(data, "items[a>2]") == [{"a": 5}]


def test_filter_nested_field():
    data = {"items": [{"meta": {"score": 9}}, {"meta": {"score": 4}}]}
    assert query(data, "items[meta.score>=9]") == [{"meta": {"score": 9}}]


def test_filter_bool_value():
    data = {"items": [{"on": True}, {"on": False}]}
    assert query(data, "items[on==true]") == [{"on": True}]


# --- aggregations ----------------------------------------------------------

def test_sum_with_path():
    assert _aggregate_sum(query(SAMPLE, "users"), "age") == 104


def test_sum_bare_matches():
    assert _aggregate_sum([1, 2, 3], None) == 6


def test_sum_non_numeric_errors():
    with pytest.raises(JsonqError):
        _aggregate_sum(query(SAMPLE, "users"), "name")


def test_keys_deduped_in_order():
    assert _aggregate_keys(query(SAMPLE, "users")) == [
        "name", "age", "city", "email", "tags"]


def test_keys_on_scalar_errors():
    with pytest.raises(JsonqError):
        _aggregate_keys([1, 2])


# --- CLI -------------------------------------------------------------------

def test_cli_simple_query_from_stdin():
    result = run_cli_json(SAMPLE, "users.0.name")
    assert result.returncode == 0
    assert result.stdout.strip() == "Ana"


def test_cli_wildcard_query():
    result = run_cli_json(SAMPLE, "users.*.name")
    assert result.returncode == 0
    assert result.stdout.splitlines() == ["Ana", "Ben", "Cid"]


def test_cli_filter_query():
    result = run_cli_json(SAMPLE, "users[age>30].name")
    assert result.returncode == 0
    assert result.stdout.splitlines() == ["Ben", "Cid"]


def test_cli_json_flag_single_object():
    result = run_cli_json(SAMPLE, "users.0", "--json")
    assert result.returncode == 0
    assert json.loads(result.stdout) == SAMPLE["users"][0]


def test_cli_json_flag_multiple_matches_is_array():
    result = run_cli_json(SAMPLE, "users.*.name", "--json")
    assert result.returncode == 0
    assert json.loads(result.stdout) == ["Ana", "Ben", "Cid"]


def test_cli_count_flag():
    result = run_cli_json(SAMPLE, "users[age>30]", "--count")
    assert result.returncode == 0
    assert result.stdout.strip() == "2"


def test_cli_sum_flag_with_path():
    result = run_cli_json(SAMPLE, "users", "--sum", "age")
    assert result.returncode == 0
    assert result.stdout.strip() == "104"


def test_cli_keys_flag():
    result = run_cli_json(SAMPLE, "users.0", "--keys")
    assert result.returncode == 0
    assert result.stdout.splitlines() == ["name", "age", "city", "email", "tags"]


def test_cli_no_query_prints_whole_document():
    result = run_cli_json(SAMPLE)
    assert result.returncode == 0
    assert json.loads(result.stdout) == SAMPLE


def test_cli_reads_from_file(tmp_path):
    path = tmp_path / "data.json"
    path.write_text(json.dumps(SAMPLE), encoding="utf-8")
    result = run_cli("users.1.city", str(path))
    assert result.returncode == 0
    assert result.stdout.strip() == "Toronto"


def test_cli_invalid_json_clean_error():
    result = run_cli("users", stdin_text="{not json")
    assert result.returncode == 1
    assert result.stdout == ""
    assert "invalid JSON" in result.stderr


def test_cli_missing_key_clean_error():
    result = run_cli_json(SAMPLE, "meta.nope")
    assert result.returncode == 1
    assert "not found" in result.stderr


def test_cli_missing_file_clean_error():
    result = run_cli("users", "/does/not/exist.json")
    assert result.returncode == 1
    assert "cannot read" in result.stderr


def test_cli_bad_query_is_clean_error():
    result = run_cli_json(SAMPLE, "users[age>30")
    assert result.returncode == 1
    assert "unclosed" in result.stderr


def test_cli_conflicting_flags_rejected():
    result = run_cli_json(SAMPLE, "users", "--count", "--keys")
    assert result.returncode == 2


def test_cli_boolean_and_null_scalars():
    result = run_cli_json(SAMPLE, "meta.active")
    assert result.stdout.strip() == "true"
    result = run_cli_json(SAMPLE, "meta.note")
    assert result.stdout.strip() == "null"


def test_cli_empty_filter_result_is_success():
    result = run_cli_json(SAMPLE, "users[age>100].name")
    assert result.returncode == 0
    assert result.stdout == ""


def test_cli_lone_file_argument_prints_whole_document(tmp_path):
    path = tmp_path / "data.json"
    path.write_text(json.dumps(SAMPLE), encoding="utf-8")
    result = run_cli(str(path))
    assert result.returncode == 0
    assert json.loads(result.stdout) == SAMPLE


def test_cli_version_flag():
    result = run_cli("--version")
    assert result.returncode == 0
    assert result.stdout.strip().startswith("jsonq ")
