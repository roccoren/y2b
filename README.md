# YouTube Audio Downloader to Azure - n8n Workflow

This project provides an automated n8n workflow that downloads YouTube videos as audio files using yt-dlp and uploads them to Azure Blob Storage, with logging to Azure Table Storage.
## Azure Integration (Built‑in API Upload)

The FastAPI service (`app/main.py`) now supports an optional automatic upload of each successfully downloaded audio file to Azure Blob Storage.

### New Response Fields (when Azure upload is enabled)

| Field | Type | Description |
|-------|------|-------------|
| `blob_uploaded` | boolean/null | True if blob upload succeeded (null if feature disabled) |
| `blob_url` | string/null | HTTPS URL of the uploaded blob |
| `blob_sas_url` | string/null | SAS-signed URL (only if SAS generation enabled) |
| `blob_error` | string/null | Present if Azure upload feature was requested but misconfigured or failed |

### Enable Azure Upload

Choose one credential mode in `.env` (see `.env.template`):

**Mode 1: Connection string (enables dynamic SAS generation)**
```
AZURE_UPLOAD_ENABLED=true
AZURE_STORAGE_CONNECTION_STRING=DefaultEndpointsProtocol=...;AccountName=...;AccountKey=...;EndpointSuffix=core.windows.net
AZURE_BLOB_CONTAINER_NAME=audio-files
AZURE_BLOB_PREFIX=optional/subdir
AZURE_GENERATE_SAS=true
AZURE_SAS_EXPIRY_SECONDS=3600
AZURE_SAS_PERMISSIONS=r
```

**Mode 2: Pre-generated SAS token (no account key stored)**
Obtain a SAS token (e.g. via Azure Portal / Storage Explorer) with at least write (w) + create (c) for upload, and read (r) if direct access needed.
```
AZURE_UPLOAD_ENABLED=true
AZURE_BLOB_ACCOUNT_URL=https://<account>.blob.core.windows.net
AZURE_SAS_TOKEN=?sv=...
AZURE_BLOB_CONTAINER_NAME=audio-files
AZURE_BLOB_PREFIX=optional/subdir
# AZURE_GENERATE_SAS is ignored in this mode
```

If `AZURE_UPLOAD_ENABLED=true` but no valid credentials are present (neither connection string nor SAS token), the API still returns audio metadata plus `blob_error`.

#### Optional: Delete Local File After Upload
Set `AZURE_DELETE_LOCAL_AFTER_UPLOAD=true` to remove the downloaded audio file from local storage immediately after a successful Azure Blob upload. This reduces disk usage (helpful for limited persistent volumes) but means:
- The file cannot be retrieved later via `GET /download/{filename}`.
- Only the blob (and optional SAS URL) will remain available.
If you rely on serving the file locally after upload, leave this flag set to `false`.

### Example Success (with SAS)

```json
{
  "success": true,
  "filename": "0c6b4e8c-....mp3",
  "file_size": 5321847,
  "duration": 312,
  "title": "Sample Video Title",
  "quality": "best",
  "blob_uploaded": true,
  "blob_url": "https://account.blob.core.windows.net/audio-files/0c6b4e8c-....mp3",
  "blob_sas_url": "https://account.blob.core.windows.net/...sig=...",
  "blob_error": null
}
```

### Example Misconfiguration

```json
{
  "success": true,
  "filename": "a0f1f3c2-....mp3",
  "file_size": 4210091,
  "duration": 205,
  "title": "Another Title",
  "quality": "high",
  "blob_uploaded": null,
  "blob_url": null,
  "blob_sas_url": null,
  "blob_error": "Azure upload enabled but missing AZURE_STORAGE_CONNECTION_STRING"
}
```

### Environment Variable Summary

| Variable | Required if Enabled | Default | Description |
|----------|---------------------|---------|-------------|
| `AZURE_UPLOAD_ENABLED` | (feature flag) | false | Turn on Azure blob upload |
| `AZURE_STORAGE_CONNECTION_STRING` | One of (or SAS) | (none) | Full storage connection string (needed for dynamic SAS generation) |
| `AZURE_BLOB_ACCOUNT_URL` | One of (or connection string) | (none) | Account blob endpoint when using pre-generated SAS token |
| `AZURE_SAS_TOKEN` | With `AZURE_BLOB_ACCOUNT_URL` | (none) | Pre-generated SAS token (with or without leading ?) |
| `AZURE_BLOB_CONTAINER_NAME` | No | audio-files | Container (auto-created) |
| `AZURE_BLOB_PREFIX` | No | (blank) | Optional virtual folder/prefix |
| `AZURE_GENERATE_SAS` | Only with connection string | false | Dynamically generate a read-only SAS URL (ignored with pre-generated SAS) |
| `AZURE_SAS_EXPIRY_SECONDS` | Only if generating SAS | 3600 | SAS lifetime in seconds (<= 604800) |
| `AZURE_SAS_PERMISSIONS` | Only if generating SAS | r | SAS permissions for generated token |
| `AZURE_DELETE_LOCAL_AFTER_UPLOAD` | No | false | Delete local file immediately after a successful Azure upload (saves disk; file no longer downloadable via API) |

### Dokploy Deployment Notes

1. Build/push image:
   ```
   docker build -t your-registry/yt-dlp-api:latest .
   docker push your-registry/yt-dlp-api:latest
   ```
2. In Dokploy set:
   - Image: `your-registry/yt-dlp-api:latest`
   - Port: `8080`
   - Env vars (minimum for upload): `AZURE_UPLOAD_ENABLED=true`, `AZURE_STORAGE_CONNECTION_STRING=...`
   - Optional: `AZURE_GENERATE_SAS=true`
3. Volumes (recommended):
   | Path | Purpose |
   |------|---------|
   | `/app/downloads` | Retained downloadable copies |
   | `/tmp/yt-dlp-downloads` | Processing/staging |
4. Probes:
   - Liveness: `/health`
   - Readiness: `/readiness`

### Legacy Script

`yt-dlp-server.py` is legacy (no Azure integration & fewer health/cleanup features). Prefer `uvicorn app.main:app`. Plan to deprecate the legacy script once all automation references migrate.

---

## Features

- **Scheduled Downloads**: Configurable schedule trigger for automated downloads
- **YouTube Audio Extraction**: Uses yt-dlp to download high-quality audio from YouTube URLs
- **Azure Blob Storage**: Uploads audio files to Azure Blob Storage
- **Azure Table Storage**: Logs download metadata and status
- **Docker Support**: Easy deployment with Docker and Docker Compose
- **RESTful API**: Custom yt-dlp API server for n8n integration

## Architecture

```
Schedule Trigger → Get YouTube URL → Download YouTube Audio → Get Audio File → Upload to Azure Blob → Log to Table Storage
```

## Components

### 1. yt-dlp API Server (`app/main.py`)
- FastAPI-based REST API (async with thread offload for yt-dlp)
- Downloads YouTube audio using yt-dlp with configurable format & quality
- Provides unified endpoint for download plus file retrieval & deletion
- Includes liveness (`/health`) and readiness (`/readiness`) endpoints
- Automatic background cleanup of expired downloads

### 2. n8n Workflow (`youtube-audio-downloader-workflow.json`)
- Schedule trigger (configurable interval)
- HTTP requests to yt-dlp server
- Azure Blob Storage upload
- Azure Table Storage logging

## Prerequisites

### Azure Setup
1. **Azure Storage Account**
   - Create a storage account in Azure
   - Decide credential mode:
     - Connection string (AccountKey) for full control + dynamic SAS generation
     - OR pre-generated SAS token (least privilege, no key stored)
   - Create container: `audio-files` (if not auto-created)
   - (Optional workflow) Create table: `DownloadLogs` (for logging)

### Local Development
- Docker and Docker Compose installed
- Python 3.11+ (if running without Docker)
- FFmpeg (required by yt-dlp)

## Installation & Setup

### 1. Clone and Configure

```bash
git clone <repository-url>
cd youtube-audio-downloader
```

### 2. Environment Configuration

Create a `.env` file for Azure credentials:

```env
# Azure Storage Configuration
AZURE_STORAGE_CONNECTION_STRING=DefaultEndpointsProtocol=https;AccountName=your_account;AccountKey=your_key;EndpointSuffix=core.windows.net
```

### 3. Deploy with Docker Compose

```bash
# Build and start services
docker-compose up -d

# Check service status
docker-compose ps

# View logs
docker-compose logs -f
```

This will start:
- **yt-dlp server** on `http://localhost:8080`
- **n8n** on `http://localhost:5678`

### 4. n8n Configuration

1. Access n8n at `http://localhost:5678`
   - Username: `admin`
   - Password: `password`

2. Import the workflow:
   - Go to Workflows → Import from file
   - Select `youtube-audio-downloader-workflow.json`

3. Configure Azure credentials:
   - Go to Credentials → Add Credential
   - Select "Microsoft Azure" for Blob Storage
   - Select "Microsoft Azure Table Storage" for Table Storage
   - Enter your Azure Storage connection string

4. Update the workflow nodes:
   - **Get YouTube URL**: Update with your YouTube URL source
   - **Schedule Trigger**: Configure your desired schedule
   - **Upload to Azure Blob**: Verify container name (`audio-files`)
   - **Log to Table Storage**: Verify table name (`DownloadLogs`)

## Usage

### Manual Execution
1. Open the workflow in n8n
2. Update the YouTube URL in the "Get YouTube URL" node
3. Click "Execute Workflow"

### Scheduled Execution
1. Activate the workflow in n8n
2. The workflow will run according to the configured schedule
3. Monitor execution in the n8n interface

### API Endpoints

The yt-dlp server provides these endpoints:

- `POST /download` - Download YouTube audio (JSON or multipart form; multipart supports cookies_file)
- `GET /download/{filename}` - Retrieve downloaded file
- `DELETE /download/{filename}` - Delete file
- `GET /health` - Liveness probe (lightweight)
- `GET /readiness` - Readiness probe (disk space & write test)

#### Environment & Runtime Configuration (via `.env`)

Key variables (see `.env.template` for full list):
- `DEFAULT_AUDIO_FORMAT` (default: mp3)
- `DEFAULT_AUDIO_QUALITY` (best|high|medium|low)
- `ALLOWED_FORMATS` (comma list)
- `MAX_CONCURRENT_DOWNLOADS`
- `MAX_FILE_AGE_HOURS`, `CLEANUP_INTERVAL_SECONDS`
- `MIN_FREE_DISK_MB`
- `ALLOWED_DOMAINS` (restrict download source domains)

Quality presets map to bitrates (configurable by `QUALITY_BITRATES`).

#### Example API Usage

```bash
# Download audio (basic JSON request)
curl -X POST "http://localhost:8080/download" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://www.youtube.com/watch?v=VIDEO_ID", "format": "mp3"}'

# Download audio with cookies string (JSON request)
curl -X POST "http://localhost:8080/download" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://www.youtube.com/watch?v=VIDEO_ID", "format": "mp3", "cookies": "cookie1=value1; cookie2=value2"}'

# Download audio with cookies file upload (form data)
curl -X POST "http://localhost:8080/download" \
  -F "url=https://www.youtube.com/watch?v=VIDEO_ID" \
  -F "format=mp3" \
  -F "cookies_file=@/path/to/local/cookies.txt"

# Download audio with form data (no cookies file)
curl -X POST "http://localhost:8080/download" \
  -F "url=https://www.youtube.com/watch?v=VIDEO_ID" \
  -F "format=mp3"

# Get file
curl "http://localhost:8080/download/filename.mp3" --output audio.mp3
```

#### API Parameters

**POST /download** (unified endpoint supporting both JSON and form data):

**JSON Request:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `url` | string | Yes | YouTube video URL |
| `format` | string | No | Audio format (default: "mp3") |
| `quality` | string | No | Audio quality (default: "best") |
| `cookies` | string | No | Cookie string content |

**Form Data Request:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `url` | string | Yes | YouTube video URL |
| `format` | string | No | Audio format (default: "mp3") |
| `quality` | string | No | Audio quality (default: "best") |
| `cookies_file` | file | No | Cookies file upload from user's local machine |

#### Using Cookies for Private/Age-Restricted Videos

The server provides two ways to pass cookies to yt-dlp for private or age-restricted videos using the SINGLE unified `POST /download` endpoint:

**Option 1: JSON with cookie string**
```json
{
  "url": "https://www.youtube.com/watch?v=VIDEO_ID",
  "format": "mp3",
  "cookies": "cookie1=value1; cookie2=value2"
}
```

**Option 2: Multipart form upload with cookies file**
```bash
curl -X POST "http://localhost:8080/download" \
  -F "url=https://www.youtube.com/watch?v=VIDEO_ID" \
  -F "format=mp3" \
  -F "cookies_file=@/path/to/cookies.txt"
```

Notes:
- Uploaded cookies file size limited to 64KB.
- Temporary cookies file is removed after download.
- Do NOT use a separate endpoint; `/download` auto-detects JSON vs multipart.

## Workflow Configuration

### Schedule Trigger Options
- **Interval**: Every hour, daily, weekly, etc.
- **Cron Expression**: For complex schedules
- **Manual**: Trigger manually when needed

### YouTube URL Sources
You can modify the "Get YouTube URL" node to get URLs from:
- **Manual Input**: Hard-coded URL
- **Webhook**: HTTP endpoint to receive URLs
- **Database**: Query from external database
- **File**: Read from file or spreadsheet
- **API**: Fetch from external API

### Azure Storage Configuration
- **Container Name**: `audio-files` (configurable)
- **Blob Naming**: Uses video title and timestamp
- **Table Name**: `DownloadLogs` (configurable)
- **Partition Key**: `downloads`

## Monitoring & Logging

### Azure Table Storage Schema
The workflow logs the following information:

| Field | Type | Description |
|-------|------|-------------|
| PartitionKey | String | "downloads" |
| RowKey | String | Timestamp + random ID |
| youtube_url | String | Original YouTube URL |
| video_title | String | Video title from YouTube |
| blob_name | String | Name of uploaded blob |
| file_size | Number | File size in bytes |
| duration | Number | Video duration in seconds |
| download_timestamp | DateTime | ISO timestamp |
| status | String | "completed" or error status |

### n8n Execution Logs
- View execution history in n8n interface
- Monitor success/failure rates
- Debug individual node executions

## Troubleshooting

### Common Issues

1. **yt-dlp Download Fails**
   - Check YouTube URL validity
   - Verify yt-dlp server is running
   - Check server logs: `docker-compose logs yt-dlp-server`

2. **Azure Upload Fails**
   - Verify Azure credentials
   - Check container exists
   - Verify connection string format

3. **Workflow Not Triggering**
   - Ensure workflow is activated
   - Check schedule configuration
   - Review n8n execution logs

### Debug Commands

```bash
# Check service health
curl http://localhost:8080/health

# View yt-dlp server logs
docker-compose logs -f yt-dlp-server

# View n8n logs
docker-compose logs -f n8n

# Test Azure connection (requires Azure CLI)
az storage blob list --container-name audio-files --connection-string "YOUR_CONNECTION_STRING"
```

## Customization

### Modify Audio Quality / Formats

Adjust environment variables (preferred) in `.env`:
```
DEFAULT_AUDIO_FORMAT=mp3
DEFAULT_AUDIO_QUALITY=high
QUALITY_BITRATES=best=0,high=192,medium=128,low=64
```

Or edit logic inside `app/main.py` (function `_map_quality`) or allowed formats in `.env` (`ALLOWED_FORMATS`).

Per-request overrides:
- JSON: `"format": "m4a", "quality": "medium"`
- Form: `-F "format=m4a" -F "quality=medium"`

### Change Schedule
Modify the Schedule Trigger node in the workflow:
- Hourly: `{"hours": 1}`
- Daily: `{"days": 1}`
- Weekly: `{"weeks": 1}`
- Custom cron: Use cron expression

### Add Error Handling
Enhance the workflow with:
- Error catch nodes
- Retry logic
- Notification nodes (email, Slack, etc.)

## Security Considerations

1. **Azure Credentials**: Store securely, use managed identities in production
2. **n8n Authentication**: Change default credentials
3. **Network Security**: Use HTTPS in production
4. **File Cleanup**: Implement automatic cleanup of temporary files

## Production Deployment

For production deployment:

1. **Use Azure Container Instances or Kubernetes**
2. **Configure HTTPS/SSL certificates**
3. **Set up proper logging and monitoring**
4. **Use Azure Key Vault for credentials**
5. **Implement backup strategies**
6. **Configure auto-scaling**

## License

This project is provided as-is for educational and personal use. Ensure compliance with YouTube's Terms of Service and applicable laws when downloading content.