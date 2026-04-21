#!/bin/sh
# docker-entrypoint.sh — runs migrations then starts the app
#
# Strategy:
#   - If alembic_version table does NOT exist, the schema was bootstrapped by
#     SQLAlchemy create_all (creates all tables in one shot).  Stamp alembic to
#     HEAD so it skips historical incremental DDL that would fail with
#     DuplicateColumn / already-exists errors.
#   - If alembic_version table EXISTS, run upgrade head normally (only new
#     migrations since the last stamp will be applied).
#   - Brand-new DB (no tables at all): stamp to HEAD; create_all in main.py
#     lifespan builds the full schema on first boot.
#
set -e

echo "[entrypoint] Checking database state..."

HAS_ALEMBIC=$(python3 - <<'PYEOF'
import os, sys, time
import psycopg2

url = os.environ.get("DATABASE_URL_SYNC", "")
if not url:
    url = "postgresql://{user}:{pw}@{host}:{port}/{db}".format(
        user=os.environ.get("POSTGRES_USER", "tdg"),
        pw=os.environ.get("POSTGRES_PASSWORD", "tdg_secret"),
        host=os.environ.get("POSTGRES_HOST", "postgres"),
        port=os.environ.get("POSTGRES_PORT", "5432"),
        db=os.environ.get("POSTGRES_DB", "tdg"),
    )

# Retry until Postgres is ready (up to 60s)
for attempt in range(12):
    try:
        conn = psycopg2.connect(url, connect_timeout=5)
        cur = conn.cursor()
        cur.execute("SELECT to_regclass('public.alembic_version')")
        result = cur.fetchone()[0]
        cur.close()
        conn.close()
        print("yes" if result else "no")
        sys.exit(0)
    except psycopg2.OperationalError as e:
        print("DB not ready (attempt %d/12): %s" % (attempt + 1, e), file=sys.stderr)
        time.sleep(5)
    except Exception as e:
        print("Unexpected error: %s" % e, file=sys.stderr)
        sys.exit(1)

print("DB never became ready after 60s", file=sys.stderr)
sys.exit(1)
PYEOF
)

if [ "$HAS_ALEMBIC" = "no" ]; then
    echo "[entrypoint] No alembic_version table — stamping to head (create_all handles schema)..."
    alembic stamp head
    echo "[entrypoint] Stamp complete."
else
    echo "[entrypoint] Running incremental migrations..."
    alembic upgrade head
    echo "[entrypoint] Migrations complete."
fi

echo "[entrypoint] Starting application..."
exec "$@"

