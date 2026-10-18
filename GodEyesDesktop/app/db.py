from pathlib import Path
import sqlite3
import time
import uuid

ROOT = Path.home() / 'FocusAI_Data'
ROOT.mkdir(parents=True, exist_ok=True)
DB_PATH = ROOT / 'focusai.db'
EVIDENCE_DIR = ROOT / 'evidence'
EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)

class Database:
    def __init__(self, path=DB_PATH):
        self.path = Path(path)
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.execute('PRAGMA journal_mode=WAL')
        self.conn.execute('PRAGMA synchronous=NORMAL')
        self.conn.execute('PRAGMA foreign_keys=ON')
        self._init()

    def _init(self):
        self.conn.executescript('''
        CREATE TABLE IF NOT EXISTS sessions(
            id TEXT PRIMARY KEY,
            started REAL NOT NULL,
            ended REAL,
            duration REAL DEFAULT 0,
            status TEXT NOT NULL,
            students INTEGER DEFAULT 0,
            locked INTEGER DEFAULT 0,
            board_side TEXT DEFAULT 'RIGHT'
        );
        CREATE TABLE IF NOT EXISTS events(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            student_id TEXT NOT NULL,
            timestamp REAL NOT NULL,
            event_type TEXT NOT NULL,
            confidence REAL NOT NULL,
            assessment TEXT NOT NULL,
            evidence TEXT,
            FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
        );
        ''')
        cols = {
            'sessions': {'ended':'REAL','duration':'REAL DEFAULT 0','students':'INTEGER DEFAULT 0','locked':'INTEGER DEFAULT 0','board_side':"TEXT DEFAULT 'RIGHT'"},
            'events': {'session_id':'TEXT','student_id':'TEXT','timestamp':'REAL','event_type':'TEXT','confidence':'REAL DEFAULT 0','assessment':"TEXT DEFAULT ''",'evidence':'TEXT'}
        }
        for table, wanted in cols.items():
            existing = {r[1] for r in self.conn.execute(f'PRAGMA table_info({table})')}
            for col, definition in wanted.items():
                if col not in existing:
                    self.conn.execute(f'ALTER TABLE {table} ADD COLUMN {col} {definition}')
        self.conn.execute('CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id)')
        self.conn.execute('CREATE INDEX IF NOT EXISTS idx_events_student ON events(student_id)')
        self.conn.commit()

    def create_session(self, board_side='RIGHT'):
        sid = uuid.uuid4().hex
        self.conn.execute('INSERT INTO sessions(id,started,status,locked,board_side) VALUES(?,?,?,?,?)', (sid, time.time(), 'RUNNING', 0, board_side))
        self.conn.commit()
        return sid

    def update_session(self, sid, status, duration, students, locked=False, board_side='RIGHT'):
        self.conn.execute('UPDATE sessions SET ended=?,duration=?,status=?,students=?,locked=?,board_side=? WHERE id=?', (time.time(), duration, status, students, int(locked), board_side, sid))
        self.conn.commit()

    def add_event(self, sid, student, event_type, confidence, assessment, evidence):
        ts = time.time()
        self.conn.execute('INSERT INTO events(session_id,student_id,timestamp,event_type,confidence,assessment,evidence) VALUES(?,?,?,?,?,?,?)', (sid, student, ts, event_type, float(confidence), assessment, str(evidence) if evidence else None))
        self.conn.commit()
        return ts

    def sessions(self):
        return self.conn.execute('SELECT id,started,ended,duration,status,students,(SELECT COUNT(*) FROM events e WHERE e.session_id=s.id),board_side FROM sessions s ORDER BY started DESC').fetchall()

    def events(self, sid):
        return self.conn.execute('SELECT timestamp,student_id,event_type,confidence,assessment,evidence FROM events WHERE session_id=? ORDER BY timestamp', (sid,)).fetchall()

    def counts(self):
        return self.conn.execute('SELECT COUNT(*),(SELECT COUNT(*) FROM events) FROM sessions').fetchone()

    def delete_session(self, sid):
        self.conn.execute('DELETE FROM sessions WHERE id=?', (sid,))
        self.conn.commit()

    def delete_all(self):
        self.conn.execute('DELETE FROM sessions')
        self.conn.commit()

    def close(self):
        self.conn.close()