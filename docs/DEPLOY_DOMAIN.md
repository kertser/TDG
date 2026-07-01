# Deploying TDG at `tdg.alpha-numerical.com`

> TDG теперь публикуется не через собственный Caddy, а через внешний reverse proxy
> в каталоге `~/proxy` (репозиторий `kertser/proxy`).
> Домен: `tdg.alpha-numerical.com`, TLS выпускается и обновляется автоматически самим Caddy.
> Стек TDG не открывает `80/443` на хосте; он только подключает `tdg-nginx` к сети `web`.
> Источник истины для reverse proxy и сертификатов — репозиторий `kertser/proxy`.
> Подробные инструкции ниже приведены на английском.

This guide documents the current production layout: TDG runs behind the shared
reverse proxy from [`kertser/proxy`](https://github.com/kertser/proxy). The TDG
stack itself does not run Caddy and does not manage TLS certificates.

## Architecture

```text
Internet ──► proxy-caddy (:80/:443)  in ~/proxy
                 │  over Docker network "web"
                 ▼
             tdg-nginx (:80 internal)
                 │
                 ▼
             tdg-backend, tdg-postgres, tdg-redis, tdg-llm
```

## Prerequisites

- DNS `A` record `tdg → <server IP>` exists at the same registrar that already
  hosts the `smartvoter` DNS records.
- Ingress TCP ports 80 and 443 are open in the cloud provider security list.
- Docker Compose v2 is installed on the host.

For proxy-specific details, see the
[`kertser/proxy` README](https://github.com/kertser/proxy).

## One-time setup

All command blocks in this document run **on the server** and therefore use
**bash**. The developer workstation is Windows/PowerShell, but that is not used
for the steps below.

```bash
docker network create web   # skip if it already exists
git clone https://github.com/kertser/proxy.git ~/proxy
cd ~/proxy
docker compose up -d
```

This creates or reuses the external Docker network `web` and starts the shared
host-wide Caddy container (`proxy-caddy`).

## Add TDG to the Caddyfile in `~/proxy`

This is already present in the current `kertser/proxy` `main` branch. First,
verify that the site block exists:

```bash
grep tdg.alpha-numerical.com ~/proxy/Caddyfile
```

If the block is missing, append this configuration in `~/proxy/Caddyfile`:

```caddyfile
tdg.alpha-numerical.com {
    @ws path /ws/*
    reverse_proxy @ws tdg-nginx:80
    reverse_proxy tdg-nginx:80
    encode gzip
}
```

Then hot-reload the proxy:

```bash
cd ~/proxy && docker compose exec caddy caddy reload --config /etc/caddy/Caddyfile
```

The shared Caddy instance terminates TLS and forwards plain HTTP to
`tdg-nginx:80` over the `web` Docker network.

## Deploy TDG

Deploy TDG with the production overlay that removes host port bindings and
attaches `nginx` and `backend` to `web`:

```bash
cd ~/TDG
git pull
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

The base `docker-compose.yml` remains unchanged, so local development and
non-proxy deployments still work with plain `docker compose up -d`.

## Verify

Run the following checks on the server:

```bash
curl -I https://tdg.alpha-numerical.com
curl -I http://<server IP>/
```

Expected results:

- `curl -I https://tdg.alpha-numerical.com` returns `200` (or an expected app redirect).
- `curl -I http://<server IP>/` does **not** return TDG. The bare-IP endpoint is intentionally gone.

Also verify that a WebSocket endpoint upgrades correctly through the proxy:

```bash
curl -i \
  -H "Connection: Upgrade" \
  -H "Upgrade: websocket" \
  -H "Sec-WebSocket-Version: 13" \
  -H "Sec-WebSocket-Key: SGVsbG8sIFRERyE=" \
  https://tdg.alpha-numerical.com/ws/
```

Any correct WebSocket upgrade response path is acceptable; the important point
is that the request reaches TDG through `wss://tdg.alpha-numerical.com/ws/...`.

## Rollback

To return to a per-host TDG deployment on port 80:

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml down
docker compose up -d
```

Warning: this conflicts with `proxy-caddy` on ports 80/443. Stop the proxy
first, or do not perform this rollback while the shared proxy is running.

## Certificates

TLS certificates live in the `proxy_caddy_data` volume owned by the proxy
stack. This TDG stack does **not** create, mount, rotate, or otherwise manage
certificates.
