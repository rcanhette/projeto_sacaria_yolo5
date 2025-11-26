# services/db.py
import os
import time
import hashlib
import configparser
from pathlib import Path
import psycopg2
import psycopg2.extras
from psycopg2.pool import SimpleConnectionPool
from contextlib import contextmanager

PG_ENV_KEYS = (
    "PGHOST",
    "PGPORT",
    "PGDATABASE",
    "PGUSER",
    "PGPASSWORD",
    "PGSERVICE",
    "PGSERVICEFILE",
    "PGPASSFILE",
    "PGCONNECT_TIMEOUT",
    "PGOPTIONS",
    "PGAPPNAME",
    "PGSSLMODE",
    "PGSSLCERT",
    "PGSSLKEY",
    "PGSSLROOTCERT",
    "PGREQUIRESSL",
)


def _read_db_config():
    """
    Resolve a configuração de conexão com o PostgreSQL com a seguinte
    prioridade:
      1) Variáveis de ambiente PG* (PGHOST, PGPORT, etc.)
      2) Arquivo central.ini (se existir) na seção [database]
      3) Defaults seguros para ambiente de desenvolvimento.
    """
    # Defaults de desenvolvimento
    host = "localhost"
    port = 5432
    db = "contagem_sacaria"
    usr = "postgres"
    pwd = "Coop@2025"

    # 1) Variáveis de ambiente têm prioridade máxima
    env_host = os.getenv("PGHOST")
    env_port = os.getenv("PGPORT")
    env_db = os.getenv("PGDATABASE")
    env_user = os.getenv("PGUSER")
    env_pwd = os.getenv("PGPASSWORD")
    if env_host or env_port or env_db or env_user or env_pwd:
        host = env_host or host
        try:
            port = int(env_port) if env_port is not None else port
        except Exception:
            pass
        db = env_db or db
        usr = env_user or usr
        pwd = env_pwd or pwd
        return host, port, db, usr, pwd

    # 2) central.ini (ou arquivo apontado por CENTRAL_INI)
    try:
        ini_candidate = os.getenv("CENTRAL_INI", "central.ini")
        ini_path = Path(ini_candidate)
        if ini_path.is_file():
            cfg = configparser.ConfigParser()
            try:
                cfg.read(ini_path, encoding="utf-8-sig")
            except Exception:
                # Fallback para encoding padrão do sistema (ex.: CP1252)
                cfg.read(ini_path)
            if cfg.has_section("database"):
                host = cfg.get("database", "host", fallback=host)
                try:
                    port = cfg.getint("database", "port", fallback=port)
                except Exception:
                    pass
                db = cfg.get("database", "database", fallback=db)
                usr = cfg.get("database", "user", fallback=usr)
                pwd = cfg.get("database", "password", fallback=pwd)
    except Exception:
        # Em caso de erro de leitura, mantém defaults/ambiente
        pass

    return host, int(port), db, usr, pwd

def _strip_pg_env():
    removed = {}
    for k in PG_ENV_KEYS:
        if k in os.environ:
            removed[k] = os.environ.pop(k, None)
    return removed

# #
# Pool de conexÃµes (Central)
# #
_POOL: SimpleConnectionPool | None = None

def _init_pool(minconn: int = 1, maxconn: int = 10) -> SimpleConnectionPool:
    global _POOL
    if _POOL is not None:
        return _POOL
    # Permite ajustar o tamanho do pool por variÃ¡veis de ambiente
    try:
        minconn = int(os.getenv("DB_POOL_MIN", str(minconn)))
    except Exception:
        pass
    try:
        maxconn = int(os.getenv("DB_POOL_MAX", str(maxconn)))
    except Exception:
        pass
    # Limpa variáveis PG* para evitar que libpq tente ler arquivos/serviços com encoding
    # problemático em Windows (ex.: servicefile/pgpass em CP1252).
    try:
        _strip_pg_env()
    except Exception:
        pass

    host, port, db, usr, pwd = _read_db_config()

    # Normaliza tipos/encodings garantindo str em UTF‑8 quando possível
    def _as_str(v):
        if isinstance(v, bytes):
            try:
                return v.decode("utf-8")
            except UnicodeDecodeError:
                return v.decode("latin-1")
        return str(v)

    host = _as_str(host)
    db = _as_str(db)
    usr = _as_str(usr)
    pwd = _as_str(pwd)
    port = int(port)

    # Força ignorar pgpass definindo um passfile nulo
    passfile = os.devnull
    try:
        os.environ["PGPASSFILE"] = passfile
    except Exception:
        pass
    # Retry/backoff para aguardar o PostgreSQL subir (evita crash imediato)
    retries = [0, 2, 4, 8, 15]
    last_err: Exception | None = None
    for wait in retries:
        try:
            _POOL = SimpleConnectionPool(
                minconn, maxconn,
                host=host, port=port, dbname=db, user=usr, password=pwd,
            )
            break
        except psycopg2.OperationalError as e:
            last_err = e
            if wait > 0:
                time.sleep(wait)
    else:
        # esgota tentativas
        raise last_err  # type: ignore[misc]
    return _POOL

@contextmanager
def pooled_conn():
    """Context manager que devolve a conexÃ£o ao pool no final."""
    pool = _init_pool()
    conn = pool.getconn()
    try:
        yield conn
    finally:
        try:
            try:
                conn.rollback()
            except Exception:
                pass
        finally:
            pool.putconn(conn, close=False)

# #
# ConexÃ£o (robusta no Windows)
# #
def get_conn():
    """
    Evita ler pgpass/service com encoding problemÃ¡tico.
    ForÃ§a passfile=os.devnull (NUL no Windows) e, se necessÃ¡rio,
    limpa variÃ¡veis PG* e reconecta.
    """
    # Remove variáveis PG* para evitar arquivos em encoding nativo do Windows
    try:
        _strip_pg_env()
    except Exception:
        pass

    host, port, db, usr, pwd = _read_db_config()

    def _as_str(v):
        if isinstance(v, bytes):
            try:
                return v.decode("utf-8")
            except UnicodeDecodeError:
                return v.decode("latin-1")
        return str(v)

    host = _as_str(host)
    db = _as_str(db)
    usr = _as_str(usr)
    pwd = _as_str(pwd)
    port = int(port)

    # Força ignorar pgpass
    try:
        os.environ["PGPASSFILE"] = os.devnull
    except Exception:
        pass

    return psycopg2.connect(
        host=host, port=port, dbname=db, user=usr, password=pwd,
    )

# #
# Helpers de consulta
# #
def query_all(sql, params=None):
    with pooled_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql, params or [])
        return list(cur.fetchall())

def query_one(sql, params=None):
    rows = query_all(sql, params)
    return rows[0] if rows else None

def execute(sql, params=None):
    with pooled_conn() as conn, conn.cursor() as cur:
        cur.execute(sql, params or [])
        conn.commit()

def execute_returning(sql, params=None):
    with pooled_conn() as conn, conn.cursor() as cur:
        cur.execute(sql, params or [])
        row = cur.fetchone()
        conn.commit()
        return row[0] if row else None

# #
# Schema e migraÃ§Ãµes leves
# #
def ensure_schema() -> None:
    """
    Cria/ajusta tabelas necessÃ¡rias ao app, de forma idempotente:
      - users, ct, user_ct
      - session, session_log
    TambÃ©m adiciona colunas que possam faltar em esquemas antigos
    antes de criar os Ã­ndices.
    """

    # #
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

    # #
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
            IF to_regclass('public.user_tc') IS NOT NULL THEN
                IF EXISTS (
                    SELECT 1 FROM information_schema.columns
                     WHERE table_schema='public' AND table_name='user_tc' AND column_name='ct_id'
                ) THEN
                    EXECUTE 'ALTER TABLE user_tc RENAME COLUMN ct_id TO tc_id';
                END IF;
            END IF;
        END$$;
        """
    )

    # #
    execute("""
    CREATE TABLE IF NOT EXISTS tc (
      id SERIAL PRIMARY KEY,
      name TEXT NOT NULL,
      source_path TEXT NOT NULL,
      roi TEXT,
      model_path TEXT,
      line_offset_red INTEGER DEFAULT 40,
      line_offset_blue INTEGER DEFAULT -40,
      flow_mode TEXT DEFAULT 'cima',
      max_lost INTEGER DEFAULT 2,
      match_dist INTEGER DEFAULT 150,
      min_conf NUMERIC(6,4) DEFAULT 0.8000,
      missed_frame_dir TEXT
    );
    """)
    # colunas que podem faltar em esquemas antigos
    execute("ALTER TABLE tc ADD COLUMN IF NOT EXISTS active BOOLEAN NOT NULL DEFAULT TRUE;")
    execute("ALTER TABLE tc ADD COLUMN IF NOT EXISTS created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW();")
    execute("ALTER TABLE tc ADD COLUMN IF NOT EXISTS line_offset_red INTEGER DEFAULT 40;")
    execute("ALTER TABLE tc ADD COLUMN IF NOT EXISTS line_offset_blue INTEGER DEFAULT -40;")
    execute("ALTER TABLE tc ADD COLUMN IF NOT EXISTS flow_mode TEXT DEFAULT 'cima';")
    execute("ALTER TABLE tc ADD COLUMN IF NOT EXISTS max_lost INTEGER DEFAULT 2;")
    execute("ALTER TABLE tc ADD COLUMN IF NOT EXISTS match_dist INTEGER DEFAULT 150;")
    execute("ALTER TABLE tc ADD COLUMN IF NOT EXISTS min_conf NUMERIC(6,4) DEFAULT 0.8000;")
    execute("ALTER TABLE tc ADD COLUMN IF NOT EXISTS missed_frame_dir TEXT;")
    # streaming params (persistentes por TC)
    execute("ALTER TABLE tc ADD COLUMN IF NOT EXISTS stream_fps INTEGER;")
    execute("ALTER TABLE tc ADD COLUMN IF NOT EXISTS stream_quality INTEGER;")
    execute("UPDATE tc SET line_offset_red = 40 WHERE line_offset_red IS NULL;")
    execute("UPDATE tc SET line_offset_blue = -40 WHERE line_offset_blue IS NULL;")
    execute("UPDATE tc SET flow_mode = 'cima' WHERE flow_mode IS NULL OR TRIM(flow_mode) = '';")
    execute("UPDATE tc SET max_lost = 2 WHERE max_lost IS NULL;")
    execute("UPDATE tc SET match_dist = 150 WHERE match_dist IS NULL;")
    execute("UPDATE tc SET min_conf = 0.8000 WHERE min_conf IS NULL;")
    execute("UPDATE tc SET missed_frame_dir = '' WHERE missed_frame_dir IS NULL;")
    execute("CREATE INDEX IF NOT EXISTS idx_tc_active ON tc(active);")

    # #
    execute("""
    CREATE TABLE IF NOT EXISTS user_tc (
      user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      tc_id   INTEGER NOT NULL REFERENCES tc(id)    ON DELETE CASCADE,
      PRIMARY KEY (user_id, tc_id)
    );
    """)
    execute("CREATE INDEX IF NOT EXISTS idx_user_tc_user ON user_tc(user_id);")
    execute("CREATE INDEX IF NOT EXISTS idx_user_tc_tc   ON user_tc(tc_id);")

    # #
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
    # Ã­ndices Ãºteis
    execute("CREATE INDEX IF NOT EXISTS idx_session_ct ON session(ct_id);")
    execute("CREATE INDEX IF NOT EXISTS idx_session_status ON session(status);")
    execute("CREATE INDEX IF NOT EXISTS idx_session_ct_inicio ON session(ct_id, data_inicio DESC);")

    # adicionar colunas em esquemas antigos
    execute("ALTER TABLE session ADD COLUMN IF NOT EXISTS contagem_alvo INTEGER;")
    execute("ALTER TABLE session ADD COLUMN IF NOT EXISTS observacao TEXT;")

    # #
    execute("""
    CREATE TABLE IF NOT EXISTS session_log (
      id SERIAL PRIMARY KEY,
      session_id INTEGER NOT NULL REFERENCES session(id) ON DELETE CASCADE,
      ct_id INTEGER NOT NULL REFERENCES tc(id) ON DELETE CASCADE,
      ts TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
      delta INTEGER NOT NULL,
      total_atual INTEGER NOT NULL,
      event_id TEXT
    );
    """)
    # Em esquemas antigos, a tabela session_log pode existir sem a coluna event_id
    execute("ALTER TABLE session_log ADD COLUMN IF NOT EXISTS event_id TEXT;")
    execute("CREATE INDEX IF NOT EXISTS idx_session_log_session_ts ON session_log(session_id, ts);")
    execute("CREATE INDEX IF NOT EXISTS idx_session_log_ct_ts ON session_log(ct_id, ts);")
    # Garantir unicidade de event_id compatível com ON CONFLICT (event_id)
    execute(
        """
        DO $$
        BEGIN
            -- Remove índice antigo (parcial) se existir
            IF EXISTS (
                SELECT 1 FROM pg_indexes
                 WHERE schemaname = 'public'
                   AND indexname = 'uq_session_log_event_id'
            ) THEN
                EXECUTE 'DROP INDEX IF EXISTS uq_session_log_event_id';
            END IF;

            -- Cria índice único total (sem WHERE)
            IF NOT EXISTS (
                SELECT 1 FROM pg_indexes
                 WHERE schemaname = 'public'
                   AND indexname = 'uq_session_log_event_id'
            ) THEN
                CREATE UNIQUE INDEX uq_session_log_event_id
                    ON session_log (event_id);
            END IF;
        END$$;
        """
    )

    # #
    # Usuário padrão (admin/admin) – criado apenas se não existir
    # #
    try:
        default_username = "admin"
        default_password_hash = hashlib.sha256("admin".encode("utf-8")).hexdigest()
        execute(
            (
                "INSERT INTO users (username, password, role, active) "
                "VALUES (%s, %s, 'admin', TRUE) "
                "ON CONFLICT (username) DO NOTHING"
            ),
            [default_username, default_password_hash],
        )
    except Exception:
        # Seed é best-effort; não deve impedir o boot se falhar
        pass

    # #
    # 1) limpa duplicatas antigas marcando as mais antigas como 'cancelado'
    #    (mantÃ©m a sessÃ£o ativa mais recente de cada CT)
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

    # 2) Ã­ndice Ãºnico parcial para impedir nova duplicidade
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

    # #
    # Tabela de cadastro de agentes (opcional quando sem auth)
    execute(
        """
        CREATE TABLE IF NOT EXISTS agent (
          id SERIAL PRIMARY KEY,
          agent_id TEXT NOT NULL UNIQUE,
          token TEXT UNIQUE,
          tc_id INTEGER REFERENCES tc(id) ON DELETE SET NULL,
          active BOOLEAN NOT NULL DEFAULT TRUE,
          created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW()
        );
        """
    )
    execute("CREATE INDEX IF NOT EXISTS idx_agent_active ON agent(active);")
    execute("CREATE INDEX IF NOT EXISTS idx_agent_tc ON agent(tc_id);")

    # Status por agente (chave primÃ¡ria = agent_id para ON CONFLICT)
    execute(
        """
        CREATE TABLE IF NOT EXISTS agent_status (
          agent_id INTEGER PRIMARY KEY REFERENCES agent(id) ON DELETE CASCADE,
          last_seen TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
          hostname TEXT,
          version TEXT,
          status TEXT,
          tc_id INTEGER REFERENCES tc(id) ON DELETE SET NULL
        );
        """
    )
    execute("CREATE INDEX IF NOT EXISTS idx_agent_status_last_seen ON agent_status(last_seen DESC);")

    # Status por TC (para modo permissivo sem cadastro de agente)
    execute(
        """
        CREATE TABLE IF NOT EXISTS tc_agent_status (
          tc_id INTEGER PRIMARY KEY REFERENCES tc(id) ON DELETE CASCADE,
          last_seen TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
          hostname TEXT,
          version TEXT,
          status TEXT
        );
        """
    )
    execute("CREATE INDEX IF NOT EXISTS idx_tc_agent_status_last_seen ON tc_agent_status(last_seen DESC);")
    #

    # #
    # Layouts de paineis grandes (tc-wall)
    execute(
        """
        CREATE TABLE IF NOT EXISTS tc_wall_layout (
          id SERIAL PRIMARY KEY,
          name TEXT NOT NULL,
          left_tc_id  INTEGER REFERENCES tc(id) ON DELETE SET NULL,
          right_tc_id INTEGER REFERENCES tc(id) ON DELETE SET NULL,
          created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW()
        );
        """
    )
    execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_tc_wall_layout_name ON tc_wall_layout(name);"
    )
    
    # #
    # Ajusta constraint, default, atualiza registros e recria Ã­ndice Ãºnico parcial
    execute(
        """
        DO $$
        DECLARE
            cname text;
        BEGIN
            
            SELECT conname INTO cname
              FROM pg_constraint
             WHERE conrelid = 'session'::regclass
               AND contype = 'c'
               AND pg_get_constraintdef(oid) ILIKE '%%status%%IN%%';
            IF cname IS NOT NULL THEN
                EXECUTE format('ALTER TABLE session DROP CONSTRAINT %%I', cname);
            END IF;

            
            BEGIN
                ALTER TABLE session
                  ADD CONSTRAINT chk_session_status
                  CHECK (status IN ('operando','finalizado','cancelado'));
            EXCEPTION WHEN duplicate_object THEN
                
            END;

            
            BEGIN
                ALTER TABLE session ALTER COLUMN status SET DEFAULT 'operando';
            EXCEPTION WHEN others THEN
                
            END;
        END$$;
        """
    )

    # Converte valores antigos
    execute("UPDATE session SET status='operando' WHERE status='ativo';")

    # Garante unicidade por CT para sessÃµes 'operando'
    execute(
        """
        DO $$
        BEGIN
            
            IF EXISTS (
                SELECT 1 FROM pg_indexes
                 WHERE schemaname = 'public'
                   AND indexname = 'uq_session_one_active_per_ct'
            ) THEN
                EXECUTE 'DROP INDEX IF EXISTS uq_session_one_active_per_ct';
            END IF;

            
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

