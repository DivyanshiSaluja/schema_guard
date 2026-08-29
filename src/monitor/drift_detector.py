from src.common.models import SchemaDiff

def detect_drift(old_schema: dict, new_schema: dict) -> list[SchemaDiff]:
    diffs = []
    old_cols, new_cols = set(old_schema), set(new_schema)

    dropped = old_cols - new_cols
    added = new_cols - old_cols

    # naive rename heuristic: one dropped + one added at the same time = rename
    if len(dropped) == 1 and len(added) == 1:
        diffs.append(SchemaDiff(
            table="customers", change_type="RENAME",
            old_column=next(iter(dropped)), new_column=next(iter(added))
        ))
    else:
        for col in dropped:
            diffs.append(SchemaDiff(table="customers", change_type="DROP", old_column=col))
        for col in added:
            diffs.append(SchemaDiff(table="customers", change_type="ADD", new_column=col))

    for col in old_cols & new_cols:
        if old_schema[col] != new_schema[col]:
            diffs.append(SchemaDiff(
                table="customers", change_type="TYPE_CHANGE",
                old_column=col, new_column=col
            ))
    return diffs