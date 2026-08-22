import os
import sys
import hashlib
import sqlite3
import threading
import time
import logging
import subprocess
from datetime import datetime

# === МИНИМАЛЬНАЯ УСТАНОВКА ЗАВИСИМОСТЕЙ ===
deps = ['pyperclip', 'keyboard', 'pystray', 'winshell']
for dep in deps:
    try:
        __import__(dep.replace('-', '_'))
    except ImportError:
        subprocess.check_call([sys.executable, '-m', 'pip', 'install', dep, '--quiet'])

import pyperclip
import keyboard
import pystray
import winshell
from PIL import Image, ImageDraw

# === ПУТИ ===
APP_DATA = os.path.join(os.getenv('APPDATA'), 'ClipSaver')
os.makedirs(APP_DATA, exist_ok=True)
DB_PATH = os.path.join(APP_DATA, 'history.db')
LOG_PATH = os.path.join(APP_DATA, 'log.txt')

# === ЛОГИРОВАНИЕ (только ошибки) ===
logging.basicConfig(
    filename=LOG_PATH,
    level=logging.ERROR,
    format='%(asctime)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

# === БАЗА ДАННЫХ (оптимизированная) ===
def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute("DROP TABLE IF EXISTS history")
        c.execute('''CREATE TABLE history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content TEXT NOT NULL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )''')
        c.execute("CREATE INDEX IF NOT EXISTS idx_timestamp ON history(timestamp DESC)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_content ON history(content)")
        conn.commit()

init_db()

# === КЭШ ХЕША ===
last_hash = None

def get_clipboard_text():
    try:
        text = pyperclip.paste()
        if text and isinstance(text, str) and text.strip():
            return text
    except:
        pass
    return None

def save_to_db(text):
    try:
        with sqlite3.connect(DB_PATH) as conn:
            c = conn.cursor()
            c.execute("INSERT INTO history (content) VALUES (?)", (text,))
            # Удаляем старые записи одним запросом
            c.execute("DELETE FROM history WHERE id <= (SELECT id FROM history ORDER BY timestamp DESC LIMIT 1 OFFSET 500)")
            if os.path.getsize(DB_PATH) > 10 * 1024 * 1024:
                c.execute("VACUUM")
            conn.commit()
    except Exception as e:
        logging.error(f"Save error: {e}")

def monitor_loop():
    global last_hash
    while True:
        try:
            text = get_clipboard_text()
            if text:
                h = hashlib.md5(text.encode('utf-8')).hexdigest()
                if h != last_hash:
                    save_to_db(text)
                    last_hash = h
        except:
            pass
        time.sleep(3)

threading.Thread(target=monitor_loop, daemon=True).start()

# === ИНТЕРФЕЙС (лёгкий) ===
import tkinter as tk
from tkinter import ttk

window = None
tree = None

def open_history():
    global window, tree
    if window is not None:
        try:
            window.lift()
            window.deiconify()
            return
        except:
            window = None
    
    window = tk.Tk()
    window.title("ClipSaver History")
    window.geometry("600x400")
    window.resizable(True, True)
    
    def on_close():
        global window, tree
        if window:
            try:
                if tree and tree.winfo_exists():
                    tree.unbind('<Double-1>')
            except:
                pass
            window.quit()
            window.destroy()
            window = None
            tree = None
    
    window.protocol("WM_DELETE_WINDOW", on_close)
    
    # Поиск
    search_var = tk.StringVar()
    entry = tk.Entry(window, textvariable=search_var, font=('Arial', 10))
    entry.pack(fill='x', padx=5, pady=5)
    entry.focus()
    
    # Таблица с оптимизированными колонками
    tree = ttk.Treeview(window, columns=('id', 'content', 'time'), show='headings', height=15)
    tree.heading('id', text='#')
    tree.heading('content', text='Content')
    tree.heading('time', text='Time')
    tree.column('id', width=30, anchor='center')
    tree.column('content', width=420)
    tree.column('time', width=120)
    tree.pack(fill='both', expand=True, padx=5, pady=5)
    
    # Скролл
    scroll = ttk.Scrollbar(window, orient='vertical', command=tree.yview)
    tree.configure(yscrollcommand=scroll.set)
    scroll.pack(side='right', fill='y')
    
    # Кэш для данных (чтобы не пересоздавать строки)
    current_data = []
    
    def refresh_list(filter_text=''):
        nonlocal current_data
        try:
            with sqlite3.connect(DB_PATH) as conn:
                c = conn.cursor()
                if filter_text:
                    c.execute("SELECT id, content, timestamp FROM history WHERE content LIKE ? ORDER BY timestamp DESC LIMIT 50", (f'%{filter_text}%',))
                else:
                    c.execute("SELECT id, content, timestamp FROM history ORDER BY timestamp DESC LIMIT 50")
                rows = c.fetchall()
            
            # Обновляем только если данные изменились
            if rows != current_data:
                current_data = rows
                tree.delete(*tree.get_children())
                for row in rows:
                    rid, content, ts = row
                    if isinstance(content, bytes):
                        try:
                            content = content.decode('utf-8')
                        except:
                            content = "[Binary]"
                    elif content is None:
                        content = ""
                    preview = content[:80] + ('...' if len(content) > 80 else '')
                    tree.insert('', 'end', values=(rid, preview, ts))
        except Exception as e:
            logging.error(f"Refresh error: {e}")
    
    def on_search(*args):
        refresh_list(search_var.get())
    
    search_var.trace('w', on_search)
    
    def on_double_click(event):
        global window, tree
        try:
            if not window or not window.winfo_exists() or not tree or not tree.winfo_exists():
                return
            sel = tree.selection()
            if not sel:
                return
            item = tree.item(sel[0])
            rid = item['values'][0]
            
            with sqlite3.connect(DB_PATH) as conn:
                c = conn.cursor()
                c.execute("SELECT content FROM history WHERE id=?", (rid,))
                content = c.fetchone()[0]
            
            if isinstance(content, bytes):
                try:
                    content = content.decode('utf-8')
                except:
                    content = str(content)
            elif content is None:
                content = ""
            
            pyperclip.copy(str(content))
            
            try:
                if tree and tree.winfo_exists():
                    tree.unbind('<Double-1>')
            except:
                pass
                
            if window and window.winfo_exists():
                window.quit()
                window.destroy()
                window = None
                tree = None
        except Exception as e:
            logging.error(f"Double click error: {e}")
            try:
                if window and window.winfo_exists():
                    window.quit()
                    window.destroy()
                    window = None
                    tree = None
            except:
                pass
    
    tree.bind('<Double-1>', on_double_click)
    refresh_list()
    window.mainloop()

# === ХОТКЕЙ ===
try:
    keyboard.remove_hotkey('ctrl+shift+v')
except:
    pass
keyboard.add_hotkey('ctrl+shift+v', open_history)

# === АВТОЗАГРУЗКА ===
def add_to_startup():
    startup = os.path.join(os.getenv('APPDATA'), 'Microsoft/Windows/Start Menu/Programs/Startup')
    target = os.path.abspath(sys.argv[0])
    link_path = os.path.join(startup, 'ClipSaver.lnk')
    if not os.path.exists(link_path):
        try:
            with winshell.shortcut(link_path) as shortcut:
                shortcut.path = target
                shortcut.description = "ClipSaver"
        except:
            pass

add_to_startup()

# === ТРЕЙ ===
def create_icon():
    img = Image.new('RGB', (64, 64), color='black')
    d = ImageDraw.Draw(img)
    d.rectangle([16, 16, 48, 48], fill='cyan')
    return img

def quit_app(icon, item):
    icon.stop()
    os._exit(0)

def show_window(icon, item):
    open_history()

try:
    icon = pystray.Icon("ClipSaver", create_icon(), "ClipSaver", menu=pystray.Menu(
        pystray.MenuItem("Show History", show_window),
        pystray.MenuItem("Exit", quit_app)
    ))
    threading.Thread(target=icon.run, daemon=True).start()
except:
    pass

# === ГЛАВНЫЙ ЦИКЛ ===
while True:
    try:
        time.sleep(1)
    except KeyboardInterrupt:
        break