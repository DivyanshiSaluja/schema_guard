import json
from dataclasses import asdict
from sqlalchemy import create_engine
from src.common.config import DB_URL
from src.monitor.schema_store import get_current_schema, load_last_schema
from src.monitor.drift_detector import detect_drift
from src.agent.repair_agent import generate_and_verify_candidate

eng = create_engine(DB_URL)
current = get_current_schema(eng)
last = load_last_schema()
diffs = detect_drift(last, current)

if not diffs:
    print("No drift detected.")
else:
    candidates = [asdict(generate_and_verify_candidate(d)) for d in diffs]
    with open("data/candidates.json", "w") as f:
        json.dump(candidates, f, indent=2)
    print(f"Wrote {len(candidates)} candidate(s) to data/candidates.json")