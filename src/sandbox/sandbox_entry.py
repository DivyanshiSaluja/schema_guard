import sys, json, importlib.util
from sqlalchemy import create_engine, text

candidate_path = sys.argv[1]
spec = importlib.util.spec_from_file_location("candidate", candidate_path)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

eng = create_engine("postgresql://schemaguard:schemaguard@db:5432/schemaguard")
with eng.connect() as c:
    rows = c.execute(text("SELECT * FROM customers")).mappings().all()

ok, failed = 0, 0
for r in rows:
    try:
        result = mod.transform(dict(r))
        if result.get("name") is None:
            print(json.dumps({"status": "FAILURE", "error": f"name is None for row {dict(r)}"}))
            sys.exit(1)
        ok += 1
    except Exception as e:
        print(json.dumps({"status": "FAILURE", "error": str(e), "row": dict(r)}))
        sys.exit(1)

print(json.dumps({"status": "SUCCESS", "rows_ok": ok}))