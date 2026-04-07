"""Set passwords for existing users AAA, BBB, CCC."""
import asyncio
import asyncpg
import bcrypt

def hash_pw(pw):
    return bcrypt.hashpw(pw.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

async def main():
    conn = await asyncpg.connect('postgresql://tdg:tdg_secret@localhost:5432/tdg')
    try:
        users = [('AAA', 'AAAA'), ('BBB', 'BBBB'), ('CCC', 'CCCC')]
        for name, pw in users:
            h = hash_pw(pw)
            res = await conn.execute(
                'UPDATE users SET password_hash = $1 WHERE display_name = $2',
                h, name
            )
            print(f'{name}: {res}')

        # List all users
        rows = await conn.fetch('SELECT display_name, password_hash IS NOT NULL as has_pw FROM users')
        for r in rows:
            print(f'  User: {r["display_name"]} | has_password: {r["has_pw"]}')
    except Exception as e:
        print(f'Error: {e}')
    finally:
        await conn.close()

if __name__ == "__main__":
    asyncio.run(main())

