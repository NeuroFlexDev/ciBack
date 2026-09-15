"""Official LangGraph checkpoint stores, isolated from application tables."""
from contextlib import contextmanager, ExitStack
from sqlalchemy.engine import make_url
from app.core.config import settings


@contextmanager
def checkpoint_store():
    url = make_url(settings.DATABASE_URL)
    if url.get_backend_name() == "postgresql":
        import psycopg
        from psycopg.conninfo import make_conninfo
        from langgraph.checkpoint.postgres import PostgresSaver
        conninfo = make_conninfo(host=url.host, port=url.port or 5432,
                                dbname=url.database, user=url.username, password=url.password,
                                **dict(url.query))
        scoped = make_conninfo(conninfo, options="-c search_path=lernium_ai_graph")
        with ExitStack() as stack:
            # First jobs may start together. Serialize schema/checkpoint migrations,
            # then release the lock before any model call or graph execution.
            with psycopg.connect(conninfo, autocommit=True) as conn:
                conn.execute("SELECT pg_advisory_lock(781329441)")
                try:
                    conn.execute("CREATE SCHEMA IF NOT EXISTS lernium_ai_graph")
                    saver = stack.enter_context(PostgresSaver.from_conn_string(scoped))
                    saver.setup()
                finally:
                    conn.execute("SELECT pg_advisory_unlock(781329441)")
            yield saver
    else:
        from langgraph.checkpoint.sqlite import SqliteSaver
        path = settings.AI_CHECKPOINT_SQLITE_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        # Database-bound thread IDs prevent collisions between independent SQLite DBs.
        with SqliteSaver.from_conn_string(str(path)) as saver:
            saver.setup()
            yield saver
