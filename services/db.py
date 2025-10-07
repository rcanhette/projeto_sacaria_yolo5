# services/db.py
import os
import psycopg2
import psycopg2.extras

PG_ENV_KEYS = (
    "PGHOST", "PGPORT", "PGDATABASE", "PGUSER", "PGPASSWORD",
    "PGSERVICE", "PGSERVICEFILE", "PGPASSFILE", "PGCONNECT_TIMEOUT",
    "PGOPTIONS", "PGAPPNAME", "PGSSLMODE", "PGSSLCERT", "PGSSLKEY",
    "PGSSLROOTCERT", "PGREQUIRESSL"
)

def _read_db_config():
    host = os.getenv("PGHOST", "localhost")
    port = int(os.getenv("PGPORT", "5432"))
    db   = os.getenv("PGDATABASE", "contagem_sacaria")
    usr  = os.getenv("PGUSER", "postgres")
    pwd  = os.getenv("PGPASSWORD", "Coop@2025")
    return host, port, db, usr, pwd

def _strip_pg_env():
    removed = {}
    for k in PG_ENV_KEYS:
        if k in os.environ:
            removed[k] = os.environ.pop(k, None)
    return removed

# -------------------------------
# Conexão (robusta no Windows)
# -------------------------------
def get_conn():
    """
    Evita ler pgpass/service com encoding problemático.
    Força passfile=os.devnull (NUL no Windows) e, se necessário,
    limpa variáveis PG* e reconecta.
    """
    host, port, db, usr, pwd = _read_db_config()
    passfile = os.devnull  # 'NUL' no Windows

    try:
        os.environ["PGPASSFILE"] = passfile
        return psycopg2.connect(
            host=host, port=port, dbname=db, user=usr, password=pwd,
            passfile=passfile
        )
    except UnicodeDecodeError:
        _strip_pg_env()
        os.environ["PGPASSFILE"] = passfile
        return psycopg2.connect(
            host=host, port=port, dbname=db, user=usr, password=pwd,
            passfile=passfile
        )

# -------------------------------
# Helpers de consulta
# -------------------------------
def query_all(sql, params=None):
    with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql, params or [])
        return list(cur.fetchall())

def query_one(sql, params=None):
    rows = query_all(sql, params)
    return rows[0] if rows else None

def execute(sql, params=None):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(sql, params or [])
        conn.commit()

def execute_returning(sql, params=None):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(sql, params or [])
        row = cur.fetchone()
        conn.commit()
        return row[0] if row else None

# -------------------------------
# Schema e migrações leves
# -------------------------------
def ensure_schema() -> None:
    """
    Cria/ajusta tabelas necessárias ao app, de forma idempotente:
      - users, ct, user_ct
      - session, session_log
    Também adiciona colunas que possam faltar em esquemas antigos
    antes de criar os índices.
    """

    # ---------- users ----------
    execute("""
    CREATE TABLE IF NOT EXISTS users (
      id SERIAL PRIMARY KEY,
      username TEXT NOT NULL UNIQUE,
      password TEXT NOT NULL,
      role TEXT NOT NULL CHECK (role IN ('admin','supervisor','operator','viewer')),
      active BOOLEAN NOT NULL DEFAULT TRUE,
      created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW()
    );
    """)
    execute("CREATE INDEX IF NOT EXISTS idx_users_role   ON users(role);")
    execute("CREATE INDEX IF NOT EXISTS idx_users_active ON users(active);")

    # ---------- ct ----------
    execute("""
    CREATE TABLE IF NOT EXISTS ct (
      id SERIAL PRIMARY KEY,
      name TEXT NOT NULL,
      source_path TEXT NOT NULL,
      roi TEXT,
      model_path TEXT
    );
    """)
    # colunas que podem faltar em esquemas antigos
    execute("ALTER TABLE ct ADD COLUMN IF NOT EXISTS active BOOLEAN NOT NULL DEFAULT TRUE;")
    execute("ALTER TABLE ct ADD COLUMN IF NOT EXISTS created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW();")
    execute("CREATE INDEX IF NOT EXISTS idx_ct_active ON ct(active);")

    # ---------- user_ct (vínculo N:N) ----------
    execute("""
    CREATE TABLE IF NOT EXISTS user_ct (
      user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      ct_id   INTEGER NOT NULL REFERENCES ct(id)    ON DELETE CASCADE,
      PRIMARY KEY (user_id, ct_id)
    );
    """)
    execute("CREATE INDEX IF NOT EXISTS idx_user_ct_user ON user_ct(user_id);")
    execute("CREATE INDEX IF NOT EXISTS idx_user_ct_ct   ON user_ct(ct_id);")

    # ---------- session ----------
    # usada por services/session_repository.py
    execute("""
    CREATE TABLE IF NOT EXISTS session (
      id SERIAL PRIMARY KEY,
      ct_id INTEGER NOT NULL REFERENCES ct(id) ON DELETE CASCADE,
      lote TEXT NOT NULL,
      data_inicio TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
      data_fim TIMESTAMP WITHOUT TIME ZONE,
      status TEXT NOT NULL DEFAULT 'ativo' CHECK (status IN ('ativo','finalizado','cancelado')),
      total_final INTEGER
    );
    """)
    # índices úteis
    execute("CREATE INDEX IF NOT EXISTS idx_session_ct ON session(ct_id);")
    execute("CREATE INDEX IF NOT EXISTS idx_session_status ON session(status);")
    execute("CREATE INDEX IF NOT EXISTS idx_session_ct_inicio ON session(ct_id, data_inicio DESC);")

    # ---------- session_log ----------
    execute("""
    CREATE TABLE IF NOT EXISTS session_log (
      id SERIAL PRIMARY KEY,
      session_id INTEGER NOT NULL REFERENCES session(id) ON DELETE CASCADE,
      ct_id INTEGER NOT NULL REFERENCES ct(id) ON DELETE CASCADE,
      ts TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
      delta INTEGER NOT NULL,
      total_atual INTEGER NOT NULL
    );
    """)
    execute("CREATE INDEX IF NOT EXISTS idx_session_log_session_ts ON session_log(session_id, ts);")
    execute("CREATE INDEX IF NOT EXISTS idx_session_log_ct_ts ON session_log(ct_id, ts);")
