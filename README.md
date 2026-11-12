# jsonq

Query JSON from the command line — no dependencies, pure Python standard library.

`jsonq` reads JSON from a file or stdin and extracts values with a small path
language: dotted keys, array indices, wildcards, and filters. It is the
kind of tool you reach for when `grep` is not enough and `jq` is not
installed.

## Installation

No installation and no dependencies — Python 3.8+ is all it needs.

```sh
# Run it directly
python3 jsonq.py 'users[age>30].name' data.json

# Or make it executable and put it on your PATH
chmod +x jsonq.py
cp jsonq.py /usr/local/bin/jsonq
```

## Usage

```
jsonq [QUERY] [FILE] [--json] [--count] [--sum [PATH]] [--keys]
```

- `QUERY` — a path query (omit it to pretty-print the whole document).
- `FILE` — a JSON file to read. If omitted, `jsonq` reads stdin.
- `--json` — print results as JSON instead of plain text.
- `--count` — print how many values matched.
- `--sum [PATH]` — sum the matched numbers, or the numbers at `PATH`
  under each match.
- `--keys` — list the keys of each matched object.

Exit codes: `0` on success, `1` for runtime problems (invalid JSON, missing
key, unreadable input), `2` for a malformed query or bad flags.

## The query language

| Syntax | Meaning |
|---|---|
| `users.0.name` | Dotted keys and array indices |
| `users.*.email` | `*` expands every value of an object or array |
| `users[age>30]` | Keep array elements where the field matches |
| `users[city=='NYC']` | Filters take strings, numbers, `true`, `false`, `null` |
| `users[0]`, `users[*]` | Bracket form of index and wildcard |

Filter operators: `==`, `!=`, `>`, `>=`, `<`, `<=`. Filter fields can be
dotted (`items[meta.score>=90]`). A field missing from some elements excludes
those elements; after a wildcard or filter, a key missing from some elements
is skipped instead of erroring, so `users.*.email` works even when one user
has no email address.

## Examples

Given `data.json`:

```json
{
  "users": [
    {"name": "Ana", "age": 28, "city": "Edmonton"},
    {"name": "Ben", "age": 35, "city": "Toronto"},
    {"name": "Cid", "age": 41, "city": "Edmonton"}
  ]
}
```

```sh
$ jsonq 'users.0.name' data.json
Ana

$ jsonq 'users[age>30].name' data.json
Ben
Cid

$ jsonq 'users[city==Edmonton].name' data.json --json
["Ana", "Cid"]

$ jsonq 'users.*.city' data.json --count
3

$ jsonq 'users' data.json --sum age
104

$ jsonq 'users.0' data.json --keys
name
age
city

# Pipe it: curl an API and pull one field out
$ curl -s https://api.example.com/status | jsonq 'status.uptime'

# Missing keys and bad JSON fail cleanly, with a non-zero exit code
$ jsonq 'users.9.name' data.json
jsonq: error: index 9 out of range (array has 3 items)
```

## Running the tests

```sh
python3 -m pytest tests/ -q
```

61 tests cover parsing, path evaluation, wildcards, every filter operator,
the aggregations, CLI flags, and the error cases.

## License

MIT — see [LICENSE](LICENSE).
