import sqlite3
conn = sqlite3.connect('liepin_agent_workbench.db')
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;")
tables = [row[0] for row in cursor.fetchall()]
print('Tables:', tables)
print()

for table in tables:
    print('=== TABLE:', table, '===')
    cursor.execute(f'PRAGMA table_info({table})')
    cols = cursor.fetchall()
    for col in cols:
        not_null = 'NOT NULL' if col[3] else ''
        default = 'DEFAULT ' + str(col[4]) if col[4] is not None else ''
        pk = 'PK' if col[5] else ''
        print(f'  {col[1]} {col[2]} {not_null} {default} {pk}')
    
    cursor.execute(f"SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name='{table}';")
    indexes = cursor.fetchall()
    for idx in indexes:
        if idx[0]:
            print('  INDEX:', idx[0])
    print()

for table in tables:
    cursor.execute(f'PRAGMA foreign_key_list({table})')
    fks = cursor.fetchall()
    if fks:
        print('=== FOREIGN KEYS for', table, '===')
        for fk in fks:
            print(f'  {fk[3]} -> {fk[2]}.{fk[4]}')
        print()

conn.close()
