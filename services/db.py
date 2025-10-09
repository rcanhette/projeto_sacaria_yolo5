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

    # ---------- Migração: renomear ct -> tc (idempotente) ----------
    execute(
        """
        DO $$
        BEGIN
            IF to_regclass('public.tc') IS NULL AND to_regclass('public.ct') IS NOT NULL THEN
                EXECUTE 'ALTER TABLE ct RENAME TO tc';
            END IF;
            IF to_regclass('public.user_tc') IS NULL AND to_regclass('public.user_ct') IS NOT NULL THEN
                EXECUTE 'ALTER TABLE user_ct RENAME TO user_tc';
            END IF;
        END$$;
        """
    )

    # ---------- tc ----------
    execute("""
    CREATE TABLE IF NOT EXISTS tc (
      id SERIAL PRIMARY KEY,
      name TEXT NOT NULL,
      source_path TEXT NOT NULL,
      roi TEXT,
      model_path TEXT
    );
    """)
    # colunas que podem faltar em esquemas antigos
    execute("ALTER TABLE tc ADD COLUMN IF NOT EXISTS active BOOLEAN NOT NULL DEFAULT TRUE;")
    execute("ALTER TABLE tc ADD COLUMN IF NOT EXISTS created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW();")
    execute("CREATE INDEX IF NOT EXISTS idx_tc_active ON tc(active);")

    # ---------- user_tc (vínculo N:N) ----------
    execute("""
    CREATE TABLE IF NOT EXISTS user_tc (
      user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      tc_id   INTEGER NOT NULL REFERENCES tc(id)    ON DELETE CASCADE,
      PRIMARY KEY (user_id, tc_id)
    );
    """)
    execute("CREATE INDEX IF NOT EXISTS idx_user_tc_user ON user_tc(user_id);")
    execute("CREATE INDEX IF NOT EXISTS idx_user_tc_tc   ON user_tc(tc_id);")

    # ---------- session ----------
    # usada por services/session_repository.py
    execute("""
    CREATE TABLE IF NOT EXISTS session (
      id SERIAL PRIMARY KEY,
      ct_id INTEGER NOT NULL REFERENCES tc(id) ON DELETE CASCADE,
      lote TEXT NOT NULL,
      data_inicio TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
      data_fim TIMESTAMP WITHOUT TIME ZONE,
      status TEXT NOT NULL DEFAULT 'ativo' CHECK (status IN ('ativo','finalizado','cancelado')),
      total_final INTEGER,
      contagem_alvo INTEGER,
      observacao TEXT
    );
    """)
    # índices úteis
    execute("CREATE INDEX IF NOT EXISTS idx_session_ct ON session(ct_id);")
    execute("CREATE INDEX IF NOT EXISTS idx_session_status ON session(status);")
    execute("CREATE INDEX IF NOT EXISTS idx_session_ct_inicio ON session(ct_id, data_inicio DESC);")

    -- adicionar colunas em esquemas antigos
    execute("ALTER TABLE session ADD COLUMN IF NOT EXISTS contagem_alvo INTEGER;")
    execute("ALTER TABLE session ADD COLUMN IF NOT EXISTS observacao TEXT;")

    # ---------- session_log ----------
    execute("""
    CREATE TABLE IF NOT EXISTS session_log (
      id SERIAL PRIMARY KEY,
      session_id INTEGER NOT NULL REFERENCES session(id) ON DELETE CASCADE,
      ct_id INTEGER NOT NULL REFERENCES tc(id) ON DELETE CASCADE,
      ts TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
      delta INTEGER NOT NULL,
      total_atual INTEGER NOT NULL
    );
    """)
    execute("CREATE INDEX IF NOT EXISTS idx_session_log_session_ts ON session_log(session_id, ts);")
    execute("CREATE INDEX IF NOT EXISTS idx_session_log_ct_ts ON session_log(ct_id, ts);")

    # ---------- migração: garantir 1 sessão 'ativo' por CT ----------
    # 1) limpa duplicatas antigas marcando as mais antigas como 'cancelado'
    #    (mantém a sessão ativa mais recente de cada CT)
    execute(
        """
        UPDATE session s
           SET status = 'cancelado',
               data_fim = COALESCE(data_fim, NOW())
         WHERE s.status = 'ativo'
           AND EXISTS (
                SELECT 1
                  FROM session s2
                 WHERE s2.ct_id = s.ct_id
                   AND s2.status = 'ativo'
                   AND s2.data_inicio > s.data_inicio
           );
        """
    )

    # 2) índice único parcial para impedir nova duplicidade
    execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_indexes
                 WHERE schemaname = 'public'
                   AND indexname = 'uq_session_one_active_per_ct'
            ) THEN
                CREATE UNIQUE INDEX uq_session_one_active_per_ct
                    ON session (ct_id)
                    WHERE (status = 'ativo');
            END IF;
        END$$;
        """
    )
    
    # ---------- migração: adotar status 'operando' no lugar de 'ativo' ----------
    # Ajusta constraint, default, atualiza registros e recria índice único parcial
    execute(
        """
        DO $$
        DECLARE
            cname text;
        BEGIN
            -- Drop CHECK constraint existente (qualquer nome)
            SELECT conname INTO cname
              FROM pg_constraint
             WHERE conrelid = 'session'::regclass
               AND contype = 'c'
               AND pg_get_constraintdef(oid) ILIKE '%%status%%IN%%';
            IF cname IS NOT NULL THEN
                EXECUTE format('ALTER TABLE session DROP CONSTRAINT %%I', cname);
            END IF;

            -- Cria CHECK permitindo 'operando' (mantém 'cancelado' e 'finalizado')
            BEGIN
                ALTER TABLE session
                  ADD CONSTRAINT chk_session_status
                  CHECK (status IN ('operando','finalizado','cancelado'));
            EXCEPTION WHEN duplicate_object THEN
                -- já existe
            END;

            -- Default passa a ser 'operando'
            BEGIN
                ALTER TABLE session ALTER COLUMN status SET DEFAULT 'operando';
            EXCEPTION WHEN others THEN
                -- ignora
            END;
        END$$;
        """
    )

    # Converte valores antigos
    execute("UPDATE session SET status='operando' WHERE status='ativo';")

    # Garante unicidade por CT para sessões 'operando'
    execute(
        """
        DO $$
        BEGIN
            -- Remove índice antigo (se existir)
            IF EXISTS (
                SELECT 1 FROM pg_indexes
                 WHERE schemaname = 'public'
                   AND indexname = 'uq_session_one_active_per_ct'
            ) THEN
                EXECUTE 'DROP INDEX IF EXISTS uq_session_one_active_per_ct';
            END IF;

            -- Cria novo índice condicional em 'operando'
            IF NOT EXISTS (
                SELECT 1 FROM pg_indexes
                 WHERE schemaname = 'public'
                   AND indexname = 'uq_session_one_operando_per_ct'
            ) THEN
                CREATE UNIQUE INDEX uq_session_one_operando_per_ct
                    ON session (ct_id)
                    WHERE (status = 'operando');
            END IF;
        END$$;
        """
    )
