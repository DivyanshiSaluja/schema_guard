def transform(row: dict) -> dict:
    return {
        "id": row["id"],
        "name": row["full_name"],
        "email": row["email"],
    }