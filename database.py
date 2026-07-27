import sqlite3

DB_NAME = "bot_data.db"

def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS history (
            user_id INTEGER,
            role TEXT,
            content TEXT
        )
    """)
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT
        )
    """)
    
    cursor.execute("PRAGMA table_info(users)")
    columns = [column[1] for column in cursor.fetchall()]
    
    if "username" not in columns:
        cursor.execute("ALTER TABLE users ADD COLUMN username TEXT")
    if "first_name" not in columns:
        cursor.execute("ALTER TABLE users ADD COLUMN first_name TEXT")
        
    conn.commit()
    conn.close()

def log_user(user_id: int, username: str = None, first_name: str = None):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    uname = username if username else "без юзернейма"
    fname = first_name if first_name else "Без имени"
    
    cursor.execute("""
        INSERT INTO users (user_id, username, first_name)
        VALUES (?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            username=excluded.username,
            first_name=excluded.first_name
    """, (user_id, uname, fname))
    
    conn.commit()
    conn.close()

def get_all_users():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, username, first_name FROM users")
    rows = cursor.fetchall()
    conn.close()
    return rows

def add_history(user_id: int, role: str, content: str):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO history (user_id, role, content) VALUES (?, ?, ?)", (user_id, role, content))
    conn.commit()
    conn.close()

def get_history(user_id: int, limit: int = 6):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT role, content FROM history WHERE user_id = ? ORDER BY rowid DESC LIMIT ?", (user_id, limit))
    rows = cursor.fetchall()
    conn.close()
    history = []
    for role, content in reversed(rows):
        history.append({"role": role, "content": content})
    return history

def clear_history(user_id: int):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM history WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

def get_user_history_raw(target_user_id: int, limit: int = 15):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT role, content FROM history WHERE user_id = ? ORDER BY rowid DESC LIMIT ?", (target_user_id, limit))
    rows = cursor.fetchall()
    conn.close()
    
    if not rows:
        return None
        
    formatted_text = ""
    for role, content in reversed(rows):
        speaker = "Юзер" if role == "user" else "Бот"
        formatted_text += f"**{speaker}**: {content}\n"
    return formatted_text

init_db()