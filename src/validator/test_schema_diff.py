from src.validator.schema_validator import get_schema, compare_schemas
from src.common.config import DB_URL
from sqlalchemy import create_engine


engine = create_engine(DB_URL)

current_schema = get_schema(engine)

changed_schema = {
    "customers": {
        "id": "integer",
        "name": "text",
        "email": "text",
    }
}

diffs = compare_schemas(current_schema, changed_schema)

print("Detected schema changes:")

for diff in diffs:
    print(diff)
