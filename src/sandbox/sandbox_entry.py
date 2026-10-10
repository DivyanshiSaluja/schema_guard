"""Container entry point: run one candidate file over sample rows.

Usage: python sandbox_entry.py /candidates/<id>.py

Rows come from /candidates/sample_rows.json when present, otherwise from a
small built-in sample. The last line of stdout is a JSON status record that
docker_runner.py parses.
"""

import json
import os
import sys

DEFAULT_ROWS = [
    {"id": 1, "name": "Customer 1", "email": "customer1@example.com"},
    {"id": 2, "name": "Customer 2", "email": "customer2@example.com"},
]


def main(path):
    rows_path = os.path.join(os.path.dirname(path), "sample_rows.json")
    rows = DEFAULT_ROWS
    if os.path.exists(rows_path):
        with open(rows_path) as f:
            rows = json.load(f)
    try:
        with open(path) as f:
            source = f.read()
        namespace = {}
        exec(compile(source, path, "exec"), namespace)
        transform = namespace["transform"]
        for row in rows:
            out = transform(dict(row))
            if not isinstance(out, dict):
                raise TypeError("transform must return a dict")
        print(json.dumps({"status": "SUCCESS", "rows_ok": len(rows)}))
    except Exception as error:
        print(json.dumps({"status": "FAIL", "error": f"{type(error).__name__}: {error}"}))


if __name__ == "__main__":
    main(sys.argv[1])
