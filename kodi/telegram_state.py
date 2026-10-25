"""Private, durable Telegram drafts and exact-commit human actions."""
import json
import secrets
import sqlite3
import time
from pathlib import Path


class State:
    def __init__(self, root):
        root=Path(root);root.mkdir(parents=True,exist_ok=True,mode=0o700)
        path=root/'telegram.sqlite'
        self.db=sqlite3.connect(path,timeout=10);self.db.row_factory=sqlite3.Row
        path.chmod(0o600)
        self.db.executescript('''
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS items (
                id TEXT PRIMARY KEY,origin TEXT UNIQUE NOT NULL,kind TEXT NOT NULL,
                user_id INTEGER NOT NULL,chat_id INTEGER NOT NULL,data TEXT NOT NULL,
                state TEXT NOT NULL DEFAULT 'pending',result TEXT,
                created REAL NOT NULL,expires REAL NOT NULL);
        ''')

    def setting(self,key,value=None):
        if value is not None:
            with self.db:self.db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)',(key,str(value)))
        row=self.db.execute('SELECT value FROM settings WHERE key=?',(key,)).fetchone()
        return row[0] if row else None

    def item(self,origin,kind,user,chat,data,ttl=86400):
        now=time.time()
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO items(id,origin,kind,user_id,chat_id,data,created,expires) '
                'VALUES (?,?,?,?,?,?,?,?)',(secrets.token_hex(8),origin,kind,user,chat,json.dumps(data),now,now+ttl))
        return self.db.execute('SELECT * FROM items WHERE origin=?',(origin,)).fetchone()

    def get(self,id,user,chat):
        row=self.db.execute('SELECT * FROM items WHERE id=? AND user_id=? AND chat_id=?',(id,user,chat)).fetchone()
        if row is None or row['expires']<time.time():
            raise ValueError('This action expired. Use /prs or send a new ticket message.')
        return row

    def update(self,id,*,state=None,data=None,result=None):
        fields={}
        if state is not None:fields['state']=state
        if data is not None:fields['data']=json.dumps(data)
        if result is not None:fields['result']=json.dumps(result)
        if not fields:return
        with self.db:self.db.execute('UPDATE items SET '+','.join(k+'=?' for k in fields)+' WHERE id=?',(*fields.values(),id))

    def claim(self,id,expected,new):
        with self.db:
            return bool(self.db.execute('UPDATE items SET state=? WHERE id=? AND state=?',(new,id,expected)).rowcount)
