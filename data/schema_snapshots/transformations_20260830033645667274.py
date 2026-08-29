def transform(row):
    return {'id': row['id'], 'name': row['full_name'], 'email': row['email']}
