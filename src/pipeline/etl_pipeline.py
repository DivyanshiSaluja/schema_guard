from sqlalchemy import create_engine, text
from src.common.config import DB_URL
from src.pipeline.transformations import transform

def run_etl():
    eng = create_engine(DB_URL)
    with eng.begin() as conn:
        rows = conn.execute(text("SELECT * FROM customers")).mappings().all()
        conn.execute(text("DELETE FROM warehouse.customers"))
        for r in rows:
            t = transform(dict(r))
            conn.execute(text(
                "INSERT INTO warehouse.customers (id, name, email) VALUES (:id, :name, :email)"
            ), t)
    print(f"Loaded {len(rows)} rows")

if __name__ == "__main__":
    run_etl()