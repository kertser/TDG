# Deploying TDG at tdg.alpha-numerical.com

> **Краткое резюме (RU):** Это руководство описывает, как запустить TDG под доменным именем
> `tdg.alpha-numerical.com` с автоматическим TLS (Let's Encrypt) через общий Caddy
> reverse-proxy, уже используемый приложением SmartVoter на том же сервере
> `129.159.153.132`. Для этого нужно: добавить DNS A-запись, создать общую Docker-сеть
> `web`, добавить блок TDG в `Caddyfile` SmartVoter и поднять TDG через prod-оверлей
> (`docker-compose.prod.yml`). После этого TDG перестаёт слушать порты 80/443 напрямую —
> весь трафик проходит через Caddy по HTTPS.

---

## Prerequisites

- DNS `A` record `tdg → 129.159.153.132` must resolve before the first Caddy request
  (Caddy uses HTTP-01 challenge to issue the Let's Encrypt certificate).
- TCP 80 and TCP 443 must be open on the server's firewall / Oracle Cloud security list.
  (They are already open because SmartVoter works via HTTPS.)
- SSH access to the server `129.159.153.132`.

---

## Step 1 — Add DNS Record

In the registrar panel where `smartvoter.alpha-numerical.com` is already configured,
add one more `A` record:

| Type | Host | Value           | TTL       |
|------|------|-----------------|-----------|
| A    | tdg  | 129.159.153.132 | Automatic |

After saving, verify propagation from your Windows machine (PowerShell):

```powershell
Resolve-DnsName tdg.alpha-numerical.com -Type A
```

Expected output includes `129.159.153.132`.

---

## Step 2 — Confirm Firewall

TCP 80 and TCP 443 must already be open (SmartVoter proves they are).
If in doubt, check in the Oracle Cloud console:
**Networking → VCNs → Security Lists → Ingress Rules** — both ports with `0.0.0.0/0`.

No changes needed on the server's OS firewall (`iptables`/`ufw`) either,
because SmartVoter traffic already passes through.

---

## Step 3 — Create the Shared Docker Network (one-time)

Connect to the server via SSH from PowerShell:

```powershell
# On your Windows machine:
ssh <user>@129.159.153.132
```

Then, **on the server** (Linux bash):

```bash
# Create the shared network — skip if it already exists
docker network create web
```

This network is what lets the Caddy container (in the SmartVoter stack)
reach the TDG nginx container by hostname `tdg-nginx`.

---

## Step 4 — Update the SmartVoter Caddyfile

On the server, inside the SmartVoter repository directory (e.g. `~/SmartVoter`),
append the contents of `Caddyfile.tdg` from this repository to the SmartVoter `Caddyfile`:

```bash
# On the server:
cd ~/SmartVoter
cat ~/TDG/Caddyfile.tdg >> Caddyfile
```

Also make sure the Caddy service in the SmartVoter `docker-compose.prod.yml`
is attached to the `web` network.
See the companion SmartVoter PR for the exact change
(link will be provided once the PR is opened).

After editing, reload the SmartVoter stack so Caddy picks up the new block:

```bash
# On the server:
cd ~/SmartVoter
docker compose -f docker-compose.prod.yml up -d
```

Caddy will automatically obtain a TLS certificate for `tdg.alpha-numerical.com`
on the first incoming request, **after** the DNS record resolves.

---

## Step 5 — Deploy TDG with the Production Overlay

On the server, pull the latest TDG code and start the stack with the production overlay:

```bash
# On the server:
cd ~/TDG
git pull
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

What the overlay (`docker-compose.prod.yml`) does differently from the base compose:

- `nginx.ports` overridden to `[]` — nginx no longer binds host ports 80/443.
- `nginx` and `backend` services joined to the external `web` network so Caddy can reach them.

The base `docker-compose.yml` is **not modified** — local development continues
to work with plain `docker compose up -d` (which does publish ports 80/443).

---

## Step 6 — Verify

Run these checks from your Windows machine (PowerShell):

```powershell
# 1. HTTPS endpoint responds
Invoke-WebRequest -Uri "https://tdg.alpha-numerical.com" -Method Head

# 2. Bare IP no longer serves TDG (nginx ports are not published)
# This should now time out or return an error (expected behaviour)
Invoke-WebRequest -Uri "http://129.159.153.132/" -Method Head -TimeoutSec 5
```

Or using `curl` if available:

```powershell
curl -I https://tdg.alpha-numerical.com
```

Expected: HTTP 200 (or a redirect to the app).

To verify WebSocket connectivity, open the TDG frontend in a browser at
`https://tdg.alpha-numerical.com` and check that the game session connects
(the bottom radio panel should show "connected").

---

## Step 7 — Rollback

If you need to revert to the plain (no-domain) deployment:

```bash
# On the server:
cd ~/TDG
# Stop the production overlay stack
docker compose -f docker-compose.yml -f docker-compose.prod.yml down

# Restart with the base compose — ports 80/443 are published again
docker compose up -d
```

---

## Notes

- **TLS certificates** are issued automatically by Caddy (Let's Encrypt HTTP-01 challenge).
  The DNS record **must** resolve to the server IP before the first request, otherwise
  the challenge fails and Caddy retries with exponential backoff.
- **No TLS inside TDG** — the TDG nginx container serves plain HTTP on port 80 inside
  the `web` Docker network. TLS termination is entirely Caddy's responsibility.
- **Data persistence** — the `pgdata` Docker volume is not affected by switching between
  base and production overlay.
- **Local dev** — `deploy.ps1` and `deploy.sh` use only `docker-compose.yml` and are
  unaffected by this change.
