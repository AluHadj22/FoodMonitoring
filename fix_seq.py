# fix_seq.py
import psycopg2

try:
    conn = psycopg2.connect('postgresql://postgres:Toshiba3377@localhost:5432/food_management')
    cur = conn.cursor()
    
    # Находим максимальный ID
    cur.execute("SELECT MAX(id) FROM users;")
    max_id = cur.fetchone()[0]
    
    if max_id:
        # Устанавливаем последовательность на максимальный ID
        cur.execute(f"SELECT setval(pg_get_serial_sequence('users', 'id'), {max_id});")
        conn.commit()
        print(f"✅ Sequence fixed! Max ID is {max_id}")
    else:
        print("No users found, sequence not changed")
    
    cur.close()
    conn.close()
    
except Exception as e:
    print(f"Error: {e}")