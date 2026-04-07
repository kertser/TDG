"""Set passwords for existing users: AAA/AAAA, BBB/BBBB, CCC/CCCC."""
import asyncio
import asyncpg
import bcrypt

USERS = {
    "AAA": "AAAA",
    "BBB": "BBBB",
    "CCC": "CCCC",
}

async def main():
    conn = await asyncpg.connect("postgresql://tdg:tdg_secret@localhost:5432/tdg")
    try:
        for name, password in USERS.items():
            hashed = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
            result = await conn.execute(
                "UPDATE users SET password_hash = $1 WHERE display_name = $2",
                hashed, name
            )
            if "UPDATE 0" in result:
                print(f"  {name}: user not found, skipping")
            else:
                print(f"  {name}: password set OK")
    finally:
        await conn.close()

if __name__ == "__main__":
    asyncio.run(main())
