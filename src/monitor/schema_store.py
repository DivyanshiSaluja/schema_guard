import json, os
STORE_PATH = "data/schema_snapshot.json"

def get_current_schema(engine):
    from sqlalchemy import text
    with engine.connect() as c:
        rows = c.execute(text("""
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_name = 'customers'
        """)).all()
    return {r[0]: r[1] for r in rows}

def load_last_schema():
    if not os.path.exists(STORE_PATH):
        return None
    with open(STORE_PATH) as f:
        return json.load(f)

def save_schema(schema: dict):
    with open(STORE_PATH, "w") as f:
        json.dump(schema, f, indent=2)