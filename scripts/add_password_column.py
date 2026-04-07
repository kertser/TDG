"""Add password_hash column to users table."""
import asyncio
import asyncpg

async def main():
    conn = await asyncpg.connect("postgresql://tdg:tdg_secret@localhost:5432/tdg")
    try:
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS password_hash VARCHAR(255)")
        print("Column password_hash added successfully")
    except Exception as e:
        print(f"Error: {e}")
    finally:
        await conn.close()

if __name__ == "__main__":
    asyncio.run(main())

