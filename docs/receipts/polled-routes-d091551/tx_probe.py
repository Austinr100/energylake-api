"""d091551 §1.4.1 probe. Needs a local Postgres on a socket in /var/tmp/pgd091551.
Does SET LOCAL survive / leak with the shared pool's settings? Real Postgres 16."""
import asyncio, sys
sys.path.insert(0, "/home/user/energylake-api")
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
import main

DSN = "host=/var/tmp/pgd091551 user=postgres dbname=postgres"

async def probe(label, autocommit, explicit_tx):
    async def cfg(conn):
        await conn.set_autocommit(autocommit)
    pool = AsyncConnectionPool(conninfo=DSN, min_size=1, max_size=1, open=False,
                               kwargs={"row_factory": dict_row}, check=main._pool_pre_ping,
                               configure=cfg)
    await pool.open()
    async with pool.connection() as conn:
        status_after_check = conn.info.transaction_status.name
        async def body():
            async with conn.cursor() as cur:
                await cur.execute("SET LOCAL statement_timeout = '3s'")
                await cur.execute("SHOW statement_timeout")
                return (await cur.fetchone())["statement_timeout"]
        if explicit_tx:
            async with conn.transaction():
                inside = await body()
        else:
            inside = await body()
    async with pool.connection() as conn:          # the next checkout (same conn)
        nxt = (await (await conn.execute("SHOW statement_timeout")).fetchone())["statement_timeout"]
    await pool.close()
    print(f"{label:<44} after pre-ping: {status_after_check:<8} inside: {inside:<4} next checkout: {nxt}")

async def run():
    await probe("shipped (no tx), autocommit off", False, False)
    await probe("shipped (no tx), autocommit ON", True, False)
    await probe("d091551 (conn.transaction()), autocommit off", False, True)
    await probe("d091551 (conn.transaction()), autocommit ON", True, True)

asyncio.run(run())
