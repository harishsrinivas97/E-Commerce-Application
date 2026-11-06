"""
Utility script to migrate existing tables and records from MySQL to SQLite.
Run this script if you ever wish to re-sync MySQL data into SQLite.
"""

import os
import sys
import sqlite3
import mysql.connector

# Ensure root directory is on sys.path
root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

import config

def migrate():
    db_path = getattr(config, 'DB_PATH', os.path.join(os.path.dirname(__file__), "smartcart.db"))
    
    print(f"Connecting to MySQL ({config.DB_HOST} / {config.DB_NAME})...")
    try:
        mysql_conn = mysql.connector.connect(
            host=config.DB_HOST,
            user=config.DB_USER,
            password=config.DB_PASSWORD,
            database=config.DB_NAME
        )
    except Exception as e:
        print(f"Error connecting to MySQL: {e}")
        return

    mysql_cur = mysql_conn.cursor(dictionary=True)

    print(f"Setting up SQLite database at: {db_path}...")
    sqlite_conn = sqlite3.connect(db_path)
    sqlite_cur = sqlite_conn.cursor()
    sqlite_cur.execute("PRAGMA foreign_keys = ON;")

    # Schema creation
    sqlite_cur.executescript("""
    CREATE TABLE IF NOT EXISTS admin (
        admin_id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        email TEXT UNIQUE,
        password TEXT,
        profile_image TEXT
    );

    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        email TEXT UNIQUE NOT NULL,
        phone TEXT NOT NULL,
        password TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        profile_image TEXT
    );

    CREATE TABLE IF NOT EXISTS products (
        product_id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        description TEXT,
        category TEXT,
        price REAL,
        image TEXT,
        admin_id INTEGER REFERENCES admin(admin_id)
    );

    CREATE TABLE IF NOT EXISTS orders (
        order_id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        razorpay_order_id TEXT UNIQUE NOT NULL,
        razorpay_payment_id TEXT UNIQUE NOT NULL,
        razorpay_signature TEXT NOT NULL,
        total_amount REAL NOT NULL,
        currency TEXT DEFAULT 'INR',
        payment_status TEXT DEFAULT 'PAID',
        order_status TEXT DEFAULT 'PLACED',
        full_name TEXT NOT NULL,
        phone TEXT NOT NULL,
        address_line TEXT NOT NULL,
        city TEXT NOT NULL,
        state TEXT NOT NULL,
        pincode TEXT NOT NULL,
        landmark TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS order_items (
        order_item_id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_id INTEGER NOT NULL REFERENCES orders(order_id) ON DELETE CASCADE,
        product_id INTEGER NOT NULL,
        product_name TEXT NOT NULL,
        product_image TEXT,
        price REAL NOT NULL,
        quantity INTEGER NOT NULL,
        subtotal REAL NOT NULL
    );
    """)
    sqlite_conn.commit()

    tables = ['admin', 'users', 'products', 'orders', 'order_items']

    for table in tables:
        mysql_cur.execute(f"SELECT * FROM {table}")
        rows = mysql_cur.fetchall()
        if rows:
            cols = list(rows[0].keys())
            col_names = ", ".join(cols)
            placeholders = ", ".join(["?"] * len(cols))
            insert_sql = f"INSERT OR REPLACE INTO {table} ({col_names}) VALUES ({placeholders})"
            
            val_list = []
            for r in rows:
                row_vals = []
                for col in cols:
                    v = r[col]
                    if hasattr(v, '__float__') and not isinstance(v, (int, float)):
                        v = float(v)
                    elif hasattr(v, 'isoformat'):
                        v = v.strftime("%Y-%m-%d %H:%M:%S")
                    row_vals.append(v)
                val_list.append(tuple(row_vals))
                
            sqlite_cur.executemany(insert_sql, val_list)
            sqlite_conn.commit()
        print(f"Migrated {len(rows)} rows for '{table}'.")

    mysql_cur.close()
    mysql_conn.close()
    sqlite_cur.close()
    sqlite_conn.close()
    print("Migration finished successfully!")

if __name__ == "__main__":
    migrate()
