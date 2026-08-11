"""SQLite 连接与七表 Schema：patients / documents / observations / findings /
analyses / reports / patient_notes。

阶段五改动：
- patients 新增 name（真实姓名，仅本地库持久化，绝不发往外部服务）、
  name_norm（检索归一化）、last_seen_at（列表按最近使用排序）；
- analyses 新增 formula_json / trace_json，历史分析可完整回放；
- patient_notes 承接需求文档的"症状描述"输入；
- init_db() 内置幂等迁移，旧库直接升级不丢数据。
"""
import sqlite3

from . import config

DDL = """
CREATE TABLE IF NOT EXISTS users (
    id             TEXT PRIMARY KEY,
    username       TEXT NOT NULL UNIQUE,
    password_hash  TEXT NOT NULL,
    role           TEXT NOT NULL DEFAULT 'user',   -- 'admin' | 'user'
    display_name   TEXT,
    created_at     TEXT NOT NULL,
    disabled       INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);

CREATE TABLE IF NOT EXISTS patients (
    id            TEXT PRIMARY KEY,
    name          TEXT,
    name_norm     TEXT,
    id_last4      TEXT,
    pseudonym     TEXT NOT NULL,
    sex           TEXT,
    age_years     INTEGER,
    height_cm     REAL,
    weight_kg     REAL,
    owner_id      TEXT REFERENCES users(id),
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    last_seen_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_patients_name ON patients(name_norm, sex);
CREATE INDEX IF NOT EXISTS idx_patients_id4 ON patients(id_last4);
CREATE INDEX IF NOT EXISTS idx_patients_owner ON patients(owner_id);

CREATE TABLE IF NOT EXISTS documents (
    id              TEXT PRIMARY KEY,
    patient_id      TEXT NOT NULL REFERENCES patients(id),
    doc_type        TEXT NOT NULL,
    source_filename TEXT,
    stored_path     TEXT,
    engine          TEXT,
    exam_date       TEXT,
    extraction_json TEXT NOT NULL,
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_documents_patient ON documents(patient_id);

CREATE TABLE IF NOT EXISTS observations (
    id            TEXT PRIMARY KEY,
    patient_id    TEXT NOT NULL REFERENCES patients(id),
    document_id   TEXT REFERENCES documents(id),
    code          TEXT NOT NULL,
    display       TEXT,
    value_num     REAL,
    value_text    TEXT,
    unit          TEXT,
    ref_low       REAL,
    ref_high      REAL,
    abnormal_flag TEXT,
    observed_at   TEXT NOT NULL,
    created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_obs_patient_code ON observations(patient_id, code, observed_at);

CREATE TABLE IF NOT EXISTS findings (
    id           TEXT PRIMARY KEY,
    patient_id   TEXT NOT NULL REFERENCES patients(id),
    document_id  TEXT REFERENCES documents(id),
    organ        TEXT NOT NULL,
    description  TEXT NOT NULL,
    flags_json   TEXT NOT NULL DEFAULT '[]',
    observed_at  TEXT NOT NULL,
    created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_findings_patient ON findings(patient_id);

CREATE TABLE IF NOT EXISTS analyses (
    id                   TEXT PRIMARY KEY,
    patient_id           TEXT NOT NULL REFERENCES patients(id),
    input_snapshot_json  TEXT NOT NULL,
    risk_tags_json       TEXT,
    mechanism_chain_json TEXT,
    biocompute_json      TEXT,
    formula_json         TEXT,
    syndrome_tags_json   TEXT,
    interpretation_json  TEXT,
    trace_json           TEXT,
    status               TEXT NOT NULL DEFAULT 'pending',
    created_at           TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_analyses_patient ON analyses(patient_id);

CREATE TABLE IF NOT EXISTS reports (
    id          TEXT PRIMARY KEY,
    analysis_id TEXT NOT NULL REFERENCES analyses(id),
    patient_id  TEXT NOT NULL REFERENCES patients(id),
    report_type TEXT NOT NULL,
    format      TEXT NOT NULL,
    path        TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_reports_patient ON reports(patient_id);

CREATE TABLE IF NOT EXISTS patient_impressions (
    id           TEXT PRIMARY KEY,
    patient_id   TEXT NOT NULL REFERENCES patients(id),
    text         TEXT NOT NULL,
    observed_at  TEXT NOT NULL,
    created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_impressions_patient ON patient_impressions(patient_id);

CREATE TABLE IF NOT EXISTS patient_notes (
    id           TEXT PRIMARY KEY,
    patient_id   TEXT NOT NULL REFERENCES patients(id),
    text         TEXT NOT NULL,
    created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_notes_patient ON patient_notes(patient_id);
"""

# (表, 列, 追加 DDL) —— 幂等迁移：列不存在时才 ALTER
_MIGRATION_COLUMNS = [
    ("patients", "name", "ALTER TABLE patients ADD COLUMN name TEXT"),
    ("patients", "name_norm", "ALTER TABLE patients ADD COLUMN name_norm TEXT"),
    ("patients", "last_seen_at", "ALTER TABLE patients ADD COLUMN last_seen_at TEXT"),
    ("patients", "id_last4", "ALTER TABLE patients ADD COLUMN id_last4 TEXT"),
    ("patients", "owner_id", "ALTER TABLE patients ADD COLUMN owner_id TEXT"),
    ("analyses", "formula_json", "ALTER TABLE analyses ADD COLUMN formula_json TEXT"),
    ("analyses", "syndrome_tags_json",
     "ALTER TABLE analyses ADD COLUMN syndrome_tags_json TEXT"),
    ("analyses", "interpretation_json",
     "ALTER TABLE analyses ADD COLUMN interpretation_json TEXT"),
    ("analyses", "trace_json", "ALTER TABLE analyses ADD COLUMN trace_json TEXT"),
]


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    with get_conn() as conn:
        conn.executescript(DDL)
        for table, col, ddl in _MIGRATION_COLUMNS:
            has = conn.execute(
                f"SELECT 1 FROM pragma_table_info('{table}') WHERE name=?", (col,)
            ).fetchone()
            if not has:
                try:
                    conn.execute(ddl)
                except sqlite3.OperationalError:
                    pass
        conn.execute("UPDATE patients SET last_seen_at ="
                     " COALESCE(last_seen_at, updated_at, created_at)")
