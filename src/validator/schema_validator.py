from sqlalchemy import create_engine, text

from src.common.config import DB_URL
from src.common.models import SchemaDiff


def get_schema(engine, schema_name="public"):
    query = text("""
        SELECT
            table_name,
            column_name,
            data_type
        FROM information_schema.columns
        WHERE table_schema = :schema_name
        ORDER BY table_name, ordinal_position
    """)

    with engine.connect() as conn:
        rows = conn.execute(
            query,
            {"schema_name": schema_name}
        ).mappings().all()

    schema = {}

    for row in rows:
        table = row["table_name"]

        if table not in schema:
            schema[table] = {}

        schema[table][row["column_name"]] = row["data_type"]

    return schema


def compare_schemas(old_schema, new_schema):
    diffs = []

    for table, old_columns in old_schema.items():
        new_columns = new_schema.get(table, {})

        # Detect dropped columns
        for column in old_columns:
            if column not in new_columns:
                diffs.append(
                    SchemaDiff(
                        table=table,
                        change_type="DROP",
                        old_column=column,
                        old_type=old_columns[column],
                    )
                )

        # Detect type changes
        for column in old_columns:
            if column in new_columns:
                if old_columns[column] != new_columns[column]:
                    diffs.append(
                        SchemaDiff(
                            table=table,
                            change_type="TYPE_CHANGE",
                            old_column=column,
                            new_column=column,
                            old_type=old_columns[column],
                            new_type=new_columns[column],
                        )
                    )

    # Detect newly added columns
    for table, new_columns in new_schema.items():
        old_columns = old_schema.get(table, {})

        for column in new_columns:
            if column not in old_columns:
                diffs.append(
                    SchemaDiff(
                        table=table,
                        change_type="ADD",
                        new_column=column,
                        new_type=new_columns[column],
                    )
                )

    return diffs


def validate_schema(old_schema, new_schema):
    return compare_schemas(old_schema, new_schema)


if __name__ == "__main__":
    engine = create_engine(DB_URL)

    schema = get_schema(engine)

    print("Current database schema:")

    for table, columns in schema.items():
        print(f"\nTable: {table}")

        for column, data_type in columns.items():
            print(f"  {column}: {data_type}")
