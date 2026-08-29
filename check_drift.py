from sqlalchemy import create_engine
from src.common.config import DB_URL
from src.monitor.schema_store import get_current_schema, load_last_schema, save_schema
from src.monitor.drift_detector import detect_drift

eng = create_engine(DB_URL)
current = get_current_schema(eng)
last = load_last_schema()

if last is None:
    print("No baseline yet — saving current schema as baseline.")
    save_schema(current)
else:
    diffs = detect_drift(last, current)
    print(diffs if diffs else "No drift detected.")