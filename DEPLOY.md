# Hosting ShowMe for many users

On one PC, `run.bat` is all you need: SQLite, and the job worker runs inside the app. For many people at once, ShowMe runs as stateless web servers plus job workers on a shared Postgres database and a shared file store.

```
 phones ── HTTPS load balancer ──┬── web (uvicorn, N processes) ──┐
                                 ├── web ...                       ├── Postgres  (manuals, sessions, accounts, job queue)
                                 │                                 │
                      job workers ┴── worker x M ──────────────────┤
                      (read manuals, store links, 3D pre-builds)   └── shared files (/data: manual pages, photos, 3D scenes)
                                                         all call ── Meta Model API (Muse Spark, Muse Wonder)
```

## What makes it scale

| Concern | How it's handled |
| --- | --- |
| Many people, many servers | Web servers keep no state between requests; any server answers any request. Who you are travels in cookies; everything else is in Postgres. |
| Slow model work | Reading a manual, turning a store link into a guide, and pre-building 3D scenes are jobs in the `jobs` table. Web servers enqueue and return straight away; workers claim jobs with `FOR UPDATE SKIP LOCKED`, so each job runs exactly once however many workers you run. A job whose worker dies goes back in the queue after 15 minutes; failures retry up to 3 times. |
| The same product, many times | A manual or a store link (Amazon ASIN, IKEA article number...) is read **once** and shared with everyone: the first request claims the product atomically; everyone else gets the same guide. 20 people starting the same product at the same second still means one read. |
| Cost and abuse | Per-person rate limits on every endpoint that calls Muse (`RATE_PER_MINUTE`, `LIVE_RATE_PER_MINUTE`, `UPLOADS_PER_HOUR`). Upload size limits. Store links only fetch public web pages. |
| Privacy between companies | Workspaces: a brand's QR dashboards, an employer's machines and its training records are visible only to its members. Shoppers never sign in; their sessions belong to their device. |
| Health | `GET /healthz` checks the database and reports the queue depth. |

## Quick start: one server with Docker Compose

```
cp .env.example .env            # set MODEL_API_KEY, PUBLIC_URL=https://your.domain, POSTGRES_PASSWORD
docker compose up -d --build    # Postgres + web + 2 workers
```

Then put HTTPS in front (Caddy, nginx, or your cloud's load balancer) and point `PUBLIC_URL` at it, so QR codes open on any phone. **HTTPS is required** for the live camera, voice commands and AR on phones.

The first brand or employer creates a workspace at `/#/account`. Employees join at `/#/join/CODE` with the workspace's code; no password.

## Scaling out

- **More traffic:** run more `web` containers (or raise `WEB_CONCURRENCY`) behind the load balancer.
- **More manuals and links at once:** run more `worker` containers, or raise `WORKER_CONCURRENCY`. Model calls are the bottleneck, not the workers; check your Meta Model API rate limits.
- **Database:** use a managed Postgres (RDS, Cloud SQL, Neon, Supabase). Each process holds a pool of up to 10 connections; size `max_connections` accordingly or add PgBouncer.
- **Files:** every web server and worker must see the same `/data` folder. On one host that's the `files` Docker volume. Across hosts, mount shared storage (AWS EFS, Google Filestore, Azure Files) at `/data`.
- **Traces:** each process appends model-call logs to `/data/traces/`; ship them to your log system for "proof it runs on Muse" across servers.

## Settings for hosting

| Setting | Meaning |
| --- | --- |
| `DATABASE_URL` | `postgresql://user:pass@host:5432/db`. Empty means SQLite in `data/`. |
| `DATABASE_URL_UNPOOLED` | Optional direct connection used only for schema changes at start-up (Neon writes it). Leave empty to use `DATABASE_URL` for everything. |
| `MULTI_USER` | `1` turns on workspaces, sign-in and per-company privacy. On by default with `DATABASE_URL`. |
| `ALLOW_SIGNUP` | `0` stops new workspaces from being created (invite-only servers). |
| `RUN_WORKER` | `1` runs a job worker inside each web process (one PC). Set `0` when you run separate workers. |
| `WORKER_CONCURRENCY` | Jobs one worker runs at once (default 3). |
| `RATE_PER_MINUTE`, `LIVE_RATE_PER_MINUTE`, `UPLOADS_PER_HOUR` | Per-person limits (defaults 30, 90, 20). Limits are counted per server process. |
| `PUBLIC_URL` | The https address printed in QR codes. |

## Using Neon for the database

[Neon](https://neon.com) has a free Postgres plan that doesn't expire. From the project folder, signed in to Neon:

```
npm i -g neon@latest
neon login
neon init -y --org-id <your org id> --project-id <your project id> --branch production --services postgres
neon deploy
```

`neon deploy` writes `DATABASE_URL` (pooled) and `DATABASE_URL_UNPOOLED` (direct) into `.env`. Restart ShowMe and the header of `/healthz` says `"database":"postgres"`. ShowMe handles Neon's specifics itself:

- Queries go through the pooled endpoint with psycopg's server-side prepared statements turned off, since Neon's pooler (PgBouncer) runs in transaction mode.
- Schema changes run over the direct endpoint, under an advisory lock, so several servers starting at once don't collide.
- Every pooled connection is checked before reuse, so the first request after Neon suspends an idle database (scale to zero) gets a fresh connection instead of an error.

With `DATABASE_URL` set, sign-in and workspaces turn on (`MULTI_USER=1`). Set `MULTI_USER=0` to keep the one-PC behaviour.

## Running pieces by hand (no Docker)

```
pip install -r requirements.txt -r requirements-server.txt
export DATABASE_URL=postgresql://... MODEL_API_KEY=...
RUN_WORKER=0 uvicorn apprentice.app:app --app-dir backend --host 0.0.0.0 --port 8000 --workers 4 --proxy-headers
cd backend && python -m apprentice.worker          # as many as you like, on any machine with DATABASE_URL and /data
```

## Tested

The whole test suite (52 tests) runs against both SQLite and Postgres. A load check with 2 web processes and 2 separate workers on Postgres: 20 people starting the same product at once share one manual read, 15 people pasting the same store link at once get one guide, a QR guide's 3D scenes are pre-built by a worker, and one company's machines and records stay invisible to everyone else.

Not yet done for a real launch: email verification and password reset, an admin console, object storage (S3) instead of a shared folder, and a shared rate-limit store (Redis) across servers.
