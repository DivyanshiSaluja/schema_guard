from sqlalchemy import create_engine, text

eng = create_engine('postgresql://schemaguard:schemaguard@localhost:5432/schemaguard')
with eng.connect() as c:
    result = c.execute(text('SELECT count(*) FROM customers')).scalar()
    print(result)