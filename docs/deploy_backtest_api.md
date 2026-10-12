# Deploy the persistent backtest API

The API's async queue, saved strategies, reports, and price snapshots live in SQLite/files under `MRQ_STATE_DIR`. This deployment runs one API process on a Linux VPS and stores that directory in a named Docker volume. Caddy terminates HTTPS and forwards requests to the private container network.

Do not scale the API to multiple replicas or place its state on ephemeral serverless disk. The current queue is a single-process personal service.

## Requirements

- A Linux VPS with Docker Engine and the Docker Compose plugin.
- A DNS name whose A record points to the VPS. Open inbound TCP ports 80 and 443 in both the host firewall and provider firewall. If the DNS name has an AAAA record, it must also point to a reachable IPv6 address.
- Enough disk for the Docker image, saved reports, snapshots, and backups.

## Start the service

Run these commands from the repository root on the VPS after checking out the desired release:

```bash
cp .env.example .env
chmod 600 .env
openssl rand -hex 32
```

Put the DNS name and generated random value in `.env`:

```dotenv
MRQ_DOMAIN=api.your-domain.example
MRQ_API_TOKEN=<paste-the-generated-random-value>
```

Do not commit `.env` or paste the token into a chat. Start the service:

```bash
docker compose up -d --build
docker compose ps
docker compose logs --tail=100 mrq-api caddy
```

Caddy obtains and renews the TLS certificate when DNS and ports 80/443 are reachable. The API container is not published to the host; only Caddy can reach its port 8000.

## Verify HTTPS and authentication

`/health` is intentionally unauthenticated. Every data and strategy route requires the bearer token. Check the public health endpoint, then confirm a protected route rejects a request without a token:

```bash
curl -fsS https://$MRQ_DOMAIN/health
curl -i https://$MRQ_DOMAIN/assets
```

The second command should return `401`. To make an authenticated read without printing the token:

```bash
set -a
. ./.env
set +a
curl -fsS -H "Authorization: Bearer ${MRQ_API_TOKEN}" https://$MRQ_DOMAIN/assets
curl -fsS -H "Authorization: Bearer ${MRQ_API_TOKEN}" https://$MRQ_DOMAIN/data-status
```

## Connect the private Macro Quant Terminal Site

After the API passes the HTTPS and auth checks, add these production runtime variables to the existing owner-private Site:

- `MRQ_BACKTEST_API_URL`: `https://` plus the API hostname, with no path suffix.
- `MRQ_BACKTEST_API_TOKEN`: the same random bearer token; store it as a secret.

Redeploy the saved MCP-enabled Site version to apply the new runtime revision. The Site Worker keeps the bearer secret server-side. After publishing, use the installed Macro Quant Terminal plugin to call `list_assets`, `get_data_status`, and `list_strategies`. Then submit a small acceptance backtest and retrieve its status and report to verify the full tool → Site Worker → HTTPS API → persistent report path.

Keep the Site owner-private. The token grants access to the API's saved strategies, reports, and data status.

## Persistent state and upgrades

Docker Compose creates a named `mrq_state` volume for the queue database, saved strategy versions, reports, and input snapshots. The Caddy certificate data is stored separately in `caddy_data` and `caddy_config`. Back up all three volumes to storage outside the VPS and test restoring them before relying on the service.

Update from a reviewed release with:

```bash
git pull --ff-only
docker compose up -d --build
```

Do not run `docker compose down -v` during upgrades: that removes the named volumes. Replacing the API container preserves the persistent state volume. Keep the service at one API replica.

Optional local adjusted-price files can be copied to the VPS under `data/raw/market/`; that directory is excluded from the image build to avoid baking personal market files into an image. The API checks those files before using the configured Yahoo adapter. Back up such source files separately.
