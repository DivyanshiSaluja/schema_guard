def transform(row: dict) -> dict:
    return {
        "id": row["id"],
        "name": None,
        "email": row["email"],
    }