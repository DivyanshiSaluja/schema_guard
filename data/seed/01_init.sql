CREATE TABLE customers (
    id SERIAL PRIMARY KEY, 
    full_name TEXT NOT NULL,
    email TEXT NOT NULL
);

CREATE SCHEMA warehouse;
CREATE TABLE warehouse.customers (
    id INT PRIMARY KEY,
    name TEXT,
    email TEXT
);

INSERT INTO Customers (full_name, email)
SELECT 'Customer ' || g, 'customer' || g || '@example.com'
FROM generate_series(1, 500) g;