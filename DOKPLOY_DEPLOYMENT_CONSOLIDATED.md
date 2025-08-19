# Dokploy Deployment Guide (Consolidated)

This document supersedes prior guides (`DOKPLOY_DEPLOYMENT.md`, `DOKPLOY_DEPLOYMENT_UPDATED.md`) and reflects the current refactored service layout (`app/` package, readiness endpoint, cleanup loop, env‑driven config).

## Overview

Service: FastAPI + yt-dlp audio extraction
Container: Non-root, pinned dependencies, configurable via environment variables
Health: `/health` (liveness) + `/readiness` (filesystem & disk space validation)
Cleanup: Background task purges expired downloads
Deployment: Single service container fronted by Traefik (managed by Dokploy)

## Repository Structure (Relevant Parts)

```
app/
  config.py        # Pydantic settings loader
  main.py          # FastAPI app, endpoints, cleanup loop, download logic
Dockerfile
docker-compose.yml
.env.template
README.md
```

## Key Features

- Unified `POST /download` endpoint (JSON OR multipart form)
- Configurable formats, quality presets, concurrency, retention
- Domain restriction (YouTube domains by default)
- Automatic cleanup of files older than `MAX_FILE_AGE_HOURS`
- Readiness probe used for container healthcheck

## Prerequisites

1. Dokploy installed (with Traefik + Let's Encrypt configured)
2. Domain DNS A/AAAA record pointing to Dokploy host
3. (Optional) Azure Storage if integrating with n8n workflow
4. Docker login to private registry (if using external image rather than local build)

## Environment Variables (Core)

| Variable | Purpose | Default |
|----------|---------|---------|
| DOMAIN | Domain used in Traefik routing | localhost |
| DEFAULT_AUDIO_FORMAT | Default output format | mp3 |
| DEFAULT_AUDIO_QUALITY | Quality preset (best|high|medium|low) | best |
| ALLOWED_FORMATS | Comma list of allowed output formats | mp3,m4a,ogg,wav |
| MAX_CONCURRENT_DOWNLOADS | Semaphore limit for parallel downloads | 3 |
| MAX_FILE_AGE_HOURS | Retention period for downloaded files | 6 |
| CLEANUP_INTERVAL_SECONDS | Interval between cleanup scans | 600 |
| MIN_FREE_DISK_MB | Minimum free disk before readiness fails | 100 |
| QUALITY_BITRATES | Mapping for quality presets | best=0,high=192,medium=128,low=64 |
| ALLOWED_DOMAINS | Restrict source hostnames | youtube.com,youtu.be |
| LOG_LEVEL | Logging verbosity | INFO |

(See `.env.template` for the authoritative list.)

## Docker Image

Build locally (default compose flow):
```bash
docker compose build
```

Push to a registry (optional):
```bash
export IMAGE_NAME=dokployacr.azurecr.io/yt-dlp-server:1.1.0
docker build -t $IMAGE_NAME .
docker push $IMAGE_NAME
```

Then deploy using Dokploy referencing `IMAGE_NAME` environment override if needed.

## docker-compose.yml Highlights

```yaml
services:
  yt-dlp-server:
    build:
      context: .
      dockerfile: Dockerfile
    image: ${IMAGE_NAME:-yt-dlp-server:local}
    environment:
      DEFAULT_AUDIO_FORMAT: mp3
      DEFAULT_AUDIO_QUALITY: best
      # ... (additional vars)
    healthcheck:
      test: ["CMD", "curl", "-fsS", "http://localhost:8080/readiness"]
    labels:
      - traefik.enable=true
      - traefik.http.routers.yt-dlp-server.rule=Host(`${DOMAIN:-localhost}`)
      - traefik.http.routers.yt-dlp-server.entrypoints=websecure
      - traefik.http.routers.yt-dlp-server.tls.certresolver=letsencrypt
      - traefik.http.services.yt-dlp-server.loadbalancer.server.port=8080
      - traefik.http.routers.yt-dlp-server-http.rule=Host(`${DOMAIN:-localhost}`)
      - traefik.http.routers.yt-dlp-server-http.entrypoints=web
      - traefik.http.routers.yt-dlp-server-http.middlewares=redirect-to-https
      - traefik.http.middlewares.redirect-to-https.redirectscheme.scheme=https
      - traefik.http.middlewares.redirect-to-https.redirectscheme.permanent=true
```

Traefik performs HTTP→HTTPS redirect while the main router services TLS traffic.

## Deployment Paths

### Option A: Dokploy UI

1. Create new application (Docker Compose type).
2. Provide repository (Git) or upload archive with:
   - `Dockerfile`
   - `docker-compose.yml`
   - `app/` directory
   - `.env` (DO NOT COMMIT secrets)
3. Set environment variable `DOMAIN=yourdomain.tld`
4. Deploy; watch logs for successful healthcheck.

### Option B: Direct CLI then Register

1. Build & push image (if using registry).
2. In Dokploy, set the `IMAGE_NAME` environment variable to match the pushed tag OR remove build section if pulling only.
3. Deploy.

### Option C: Local Build Without Registry

Dokploy can build from source if repository contains Dockerfile & compose. No registry steps required.

## Health & Readiness

- Liveness: `/health` — lightweight JSON.
- Readiness: `/readiness`
  - Fails if free disk below `MIN_FREE_DISK_MB`
  - Fails if write test in output directory fails
  - Container healthcheck uses readiness to ensure service file system viability.

Check manually:
```bash
curl https://your-domain.com/health
curl https://your-domain.com/readiness
```

## API Summary

| Method | Path | Description |
|--------|------|-------------|
| POST | /download | Initiate download (JSON or multipart) |
| GET | /download/{filename} | Retrieve file |
| DELETE | /download/{filename} | Delete file |
| GET | /health | Liveness probe |
| GET | /readiness | Readiness probe (disk + write test) |

### JSON Download Example

```bash
curl -X POST "https://your-domain.com/download" \
  -H "Content-Type: application/json" \
  -d '{"url":"https://www.youtube.com/watch?v=VIDEO_ID","format":"mp3","quality":"high"}'
```

### Multipart with Cookies

```bash
curl -X POST "https://your-domain.com/download" \
  -F "url=https://www.youtube.com/watch?v=VIDEO_ID" \
  -F "format=mp3" \
  -F "cookies_file=@./cookies.txt"
```

## Quality & Format Handling

Quality keywords → bitrates (configurable via `QUALITY_BITRATES`):
- `best` = let ffmpeg select highest (`0` sentinel)
- `high` / `medium` / `low` map to configured numeric bitrates

Per-request overrides:
- JSON: `"quality":"medium"`
- Multipart: `-F "quality=low"`

Unsupported format requests yield 400 referencing `ALLOWED_FORMATS`.

## File Retention & Cleanup

- Download directory: `YT_DLP_OUTPUT_DIR` (default `/tmp/yt-dlp-downloads`)
- Cleanup loop every `CLEANUP_INTERVAL_SECONDS` removes files older than `MAX_FILE_AGE_HOURS`
- Adjust retention by setting `MAX_FILE_AGE_HOURS=0` to disable long-term storage (near-ephemeral)

## Disk Space Safeguard

Readiness returns 503 if available free MB below `MIN_FREE_DISK_MB`. This allows platforms (including Dokploy) to mark container unhealthy and potentially restart or alert before out-of-disk errors occur.

## Traefik / Routing Considerations

- Single HTTPS router; HTTP router + redirect middleware ensures automatic upgrade.
- If a global HTTP→HTTPS redirect already exists in Traefik cluster config, you can remove:
  - The `yt-dlp-server-http` router
  - The redirect middleware labels

## Scaling

Horizontal scaling (multiple replicas) considerations:
- Current implementation stores files on container local filesystem. To scale >1 instance behind Traefik:
  - Use shared persistent storage (e.g., NFS / object storage integration)
  - Or route sticky sessions (not ideal) / reduce retention window drastically.

For multi-instance:
1. Externalize downloads to object storage (future enhancement).
2. Disable on-disk retention or synchronize cleanup process.

## Security Hardening (Recommended Next Steps)

(Not implemented in minimal scope but recommended)
- API Key / token auth on mutating endpoints
- Rate limiting (Traefik middleware)
- CORS policy restrictions
- Request logging enhancement (structured logs)
- Audit logs for deletion actions

## Observability

Current:
- Basic logging (log level via `LOG_LEVEL`)
- Health and readiness endpoints

Future:
- Integrate Prometheus metrics (ASGI middleware) if needed
- Centralized logging (e.g., Loki / ELK stack)

## Updating the Service

```bash
# Pull latest repository changes
git pull origin main

# Rebuild image
docker compose build yt-dlp-server

# (Optional) Tag & push
docker tag yt-dlp-server:local dokployacr.azurecr.io/yt-dlp-server:1.1.1
docker push dokployacr.azurecr.io/yt-dlp-server:1.1.1

# Redeploy
docker compose up -d
```

If using Dokploy Git integration, simply push and trigger a redeploy in the UI / configured pipeline.

## Troubleshooting

| Symptom | Action |
|---------|--------|
| 503 from readiness | Check disk space, retention variables |
| 500 download error | Review container logs; verify yt-dlp compatibility |
| Slow downloads | Increase `MAX_CONCURRENT_DOWNLOADS` carefully (I/O & CPU bound) |
| High disk usage | Lower `MAX_FILE_AGE_HOURS`, increase cleanup frequency |
| 400 domain not allowed | Update `ALLOWED_DOMAINS` (comma list) |

### Inspect Logs

```bash
docker compose logs -f yt-dlp-server
```

### Check Free Space Inside Container

```bash
docker compose exec yt-dlp-server df -h /tmp/yt-dlp-downloads
```

### Validate Environment Load

```bash
docker compose exec yt-dlp-server python -c "from app.config import get_settings;print(get_settings().dict())"
```

## Migration Notes (From Legacy Single File)

Previous entrypoint `yt-dlp-server.py` is replaced by `app/main.py`. The Dockerfile now starts with:
```
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
```

If external tooling relied on running `python yt-dlp-server.py`, adjust to:
```
python -m app.main
```
(or keep legacy file as a shim if reintroduced).

## Decommissioning Old Docs

- `DOKPLOY_DEPLOYMENT.md` and `DOKPLOY_DEPLOYMENT_UPDATED.md` retained for history; this consolidated guide is authoritative.

## Quick Start (Local)

```bash
cp .env.template .env
# (edit DOMAIN and other vars if desired)
docker compose up -d --build
curl http://localhost:8080/health
curl -X POST http://localhost:8080/download \
  -H "Content-Type: application/json" \
  -d '{"url":"https://www.youtube.com/watch?v=dQw4w9WgXcQ"}'
```

## Quick Start (Dokploy)

1. Add project (Git or upload).
2. Set `DOMAIN=your.domain.tld`.
3. Deploy; wait for readiness success.
4. Test:
   ```
   curl https://your.domain.tld/health
   ```

## License & Compliance

Ensure usage complies with YouTube Terms of Service and local laws. Remove or adjust functionality if violating restrictions.

---

End of consolidated deployment guide.