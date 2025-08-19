import asyncio
import os
import time
import uuid
import shutil
import logging
from pathlib import Path
from typing import Optional, List
from datetime import datetime, timedelta

import yt_dlp
from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, HttpUrl

# Optional Azure dependency (lazy / guarded)
try:
    from azure.storage.blob import BlobServiceClient, generate_blob_sas, BlobSasPermissions
except ImportError:
    BlobServiceClient = None
    generate_blob_sas = None
    BlobSasPermissions = None

from app.config import get_settings
from threading import Lock

settings = get_settings()

# Logging setup (global – controls all library loggers via LOG_LEVEL env)
LOG_LEVEL_NAME = (settings.LOG_LEVEL or "INFO").upper()
_level = getattr(logging, LOG_LEVEL_NAME, logging.INFO)

# Set a consistent format and root level
logging.basicConfig(
    level=_level,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

# Force root logger to desired level (basicConfig might no-op if already configured)
logging.getLogger().setLevel(_level)

# Apply same level to common sub-loggers that can be noisy (tunable globally)
for _lname in [
    "yt-dlp",
    "azure",
    "azure.storage",
    "azure.storage.blob",
    "azure.core.pipeline.policies.http_logging_policy",
    "urllib3",
]:
    logging.getLogger(_lname).setLevel(_level)

logger = logging.getLogger("yt-dlp-server")
logger.debug(f"Logging initialized at level {_level} ({LOG_LEVEL_NAME})")

app = FastAPI(title="yt-dlp API Server", version="1.2.0")

# Concurrency control
download_semaphore = asyncio.Semaphore(settings.MAX_CONCURRENT_DOWNLOADS)

# -----------------------------------------------------------------------------
# Asynchronous job queue support
# -----------------------------------------------------------------------------
# Job states: pending -> running -> completed | error | cancelled
job_queue: "asyncio.Queue[tuple[str, dict]]" = asyncio.Queue()
_jobs: dict = {}
_jobs_lock = Lock()


def _create_job_record(job_id: str, payload: dict):
    with _jobs_lock:
        _jobs[job_id] = {
            "id": job_id,
            "status": "pending",
            "created_at": datetime.utcnow().isoformat(),
            "updated_at": datetime.utcnow().isoformat(),
            "payload": payload,
        }


def _update_job(job_id: str, **fields):
    with _jobs_lock:
        rec = _jobs.get(job_id)
        if not rec:
            return
        rec.update(fields)
        rec["updated_at"] = datetime.utcnow().isoformat()


async def _job_worker(worker_index: int):
    logger.info(f"Job worker #{worker_index} started")
    while True:
        job_id, params = await job_queue.get()
        _update_job(job_id, status="running", worker=worker_index)
        try:
            resp = await _perform_download(
                url=params["url"],
                audio_format=params["format"],
                quality_label=params["quality_label"],
                bitrate=params["bitrate"],
                cookies_content=params.get("cookies"),
            )
            _update_job(job_id, status="completed", result=resp.dict())
        except Exception as e:
            _update_job(job_id, status="error", error=str(e))
            logger.error(f"Job {job_id} failed: {e}")
        finally:
            job_queue.task_done()

# Models
class DownloadRequest(BaseModel):
    url: HttpUrl
    format: Optional[str] = None
    quality: Optional[str] = None
    cookies: Optional[str] = None


class DownloadResponse(BaseModel):
    success: bool
    filename: str
    file_size: int
    duration: Optional[float]
    title: Optional[str]
    quality: Optional[str]
    blob_uploaded: Optional[bool] = None
    blob_url: Optional[str] = None
    blob_sas_url: Optional[str] = None
    blob_error: Optional[str] = None


# Utility functions
def _map_quality(quality: str) -> int:
    """
    Map quality keyword to bitrate for ffmpeg postprocessor.
    'best' (0) lets ffmpeg choose highest.
    """
    if not quality:
        return 0
    quality = quality.lower()
    mapping = settings.quality_bitrate_mapping
    if quality in mapping:
        return mapping[quality]
    return 0  # fallback best


def _validate_url(url: str):
    if not url:
        raise HTTPException(status_code=400, detail="URL is required")
    # Domain restriction (optional)
    if settings.allowed_domains_list:
        import urllib.parse
        try:
            host = urllib.parse.urlparse(url).netloc.lower()
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid URL")
        if not any(host.endswith(d) for d in settings.allowed_domains_list):
            raise HTTPException(status_code=400, detail="URL domain not allowed")


def _target_dir() -> Path:
    return Path(settings.YT_DLP_OUTPUT_DIR)


def _is_allowed_format(fmt: str) -> bool:
    return fmt.lower() in settings.allowed_formats_list


def _list_download_files() -> List[Path]:
    target = _target_dir()
    return [p for p in target.glob("*") if p.is_file()]


# Cleanup task
async def _cleanup_loop():
    interval = settings.CLEANUP_INTERVAL_SECONDS
    max_age_seconds = settings.MAX_FILE_AGE_HOURS * 3600
    while True:
        try:
            now = time.time()
            count_removed = 0
            for file_path in _list_download_files():
                try:
                    age = now - file_path.stat().st_mtime
                except FileNotFoundError:
                    continue
                    # Race condition benign
                if age > max_age_seconds:
                    try:
                        file_path.unlink(missing_ok=True)
                        count_removed += 1
                    except Exception as e:
                        logger.warning(f"Cleanup failed for {file_path}: {e}")
            if count_removed:
                logger.info(f"Cleanup removed {count_removed} expired files")
        except Exception as e:
            logger.error(f"Cleanup loop error: {e}")
        await asyncio.sleep(interval)


@app.on_event("startup")
async def startup_event():
    # Ensure directory exists (already ensured in config but idempotent)
    _target_dir().mkdir(parents=True, exist_ok=True)
    # Launch cleanup background loop
    asyncio.create_task(_cleanup_loop())
    # Launch job workers (same as concurrency limit)
    for i in range(settings.MAX_CONCURRENT_DOWNLOADS):
        asyncio.create_task(_job_worker(i + 1))
    logger.info("Startup complete – cleanup + job workers initiated.")


# Unified endpoint (JSON or multipart form) – synchronous (waits for completion)
@app.post("/download", response_model=DownloadResponse)
async def download_audio(
    request: Request,
    url: Optional[str] = Form(None),
    format: Optional[str] = Form(None),
    quality: Optional[str] = Form(None),
    cookies_file: Optional[UploadFile] = File(None),
):
    """
    Download audio from a YouTube URL.
    Supports:
      - JSON: { "url": "...", "format": "mp3", "quality": "best", "cookies": "cookie1=...; ..." }
      - Multipart Form: fields url, format, quality, cookies_file (file upload)
    """
    content_type = request.headers.get("content-type", "")
    cookies_content = None

    if "application/json" in content_type:
        try:
            data = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid JSON body")
        url = data.get("url")
        format = data.get("format") or settings.DEFAULT_AUDIO_FORMAT
        quality = data.get("quality") or settings.DEFAULT_AUDIO_QUALITY
        cookies_content = data.get("cookies")
    else:
        # Form branch
        if not format:
            format = settings.DEFAULT_AUDIO_FORMAT
        if not quality:
            quality = settings.DEFAULT_AUDIO_QUALITY
        if cookies_file:
            # Limit file size (64KB)
            content = await cookies_file.read()
            if len(content) > 64 * 1024:
                raise HTTPException(status_code=400, detail="Cookies file too large (>64KB)")
            cookies_content = content.decode("utf-8")
            logger.info(f"Received cookies file upload: {cookies_file.filename}")

    _validate_url(url)
    format = format.lower()
    if not _is_allowed_format(format):
        raise HTTPException(status_code=400, detail=f"Format not allowed. Allowed: {settings.allowed_formats_list}")

    # Normalize quality
    quality = (quality or settings.DEFAULT_AUDIO_QUALITY).lower()
    bitrate = _map_quality(quality)

    return await _perform_download(url=url, audio_format=format, quality_label=quality, bitrate=bitrate, cookies_content=cookies_content)


# -----------------------------------------------------------------------------
# Asynchronous queued download endpoints
# -----------------------------------------------------------------------------
class AsyncEnqueueResponse(BaseModel):
    job_id: str
    status: str


class AsyncJobStatusResponse(BaseModel):
    id: str
    status: str
    created_at: str
    updated_at: str
    result: Optional[DownloadResponse] = None
    error: Optional[str] = None


@app.post("/download/async", response_model=AsyncEnqueueResponse)
async def enqueue_download(request: Request):
    """
    Enqueue a download job (JSON body only):
    {
      "url": "...",
      "format": "mp3",
      "quality": "best",
      "cookies": "cookie1=...;"
    }
    Returns job_id; client should poll /download/async/{job_id}
    """
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    url = data.get("url")
    fmt = (data.get("format") or settings.DEFAULT_AUDIO_FORMAT).lower()
    q = (data.get("quality") or settings.DEFAULT_AUDIO_QUALITY).lower()
    cookies_content = data.get("cookies")

    _validate_url(url)
    if not _is_allowed_format(fmt):
        raise HTTPException(status_code=400, detail=f"Format not allowed. Allowed: {settings.allowed_formats_list}")

    bitrate = _map_quality(q)
    job_id = uuid.uuid4().hex
    payload = dict(url=url, format=fmt, quality_label=q, bitrate=bitrate, cookies=cookies_content)
    _create_job_record(job_id, payload)
    await job_queue.put((job_id, payload))
    logger.info(f"Enqueued job {job_id} url={url}")
    return AsyncEnqueueResponse(job_id=job_id, status="pending")


@app.get("/download/async/{job_id}", response_model=AsyncJobStatusResponse)
async def get_job_status(job_id: str):
    with _jobs_lock:
        rec = _jobs.get(job_id)
        if not rec:
            raise HTTPException(status_code=404, detail="Job not found")
        result_obj = None
        if rec.get("result"):
            # Rehydrate DownloadResponse
            result_obj = DownloadResponse(**rec["result"])
        return AsyncJobStatusResponse(
            id=rec["id"],
            status=rec["status"],
            created_at=rec["created_at"],
            updated_at=rec["updated_at"],
            result=result_obj,
            error=rec.get("error"),
        )


@app.get("/download/async", response_model=List[AsyncJobStatusResponse])
async def list_jobs(limit: int = 50):
    with _jobs_lock:
        # Sort by created_at desc
        items = list(_jobs.values())
    items.sort(key=lambda r: r["created_at"], reverse=True)
    result = []
    for rec in items[:limit]:
        result_obj = None
        if rec.get("result"):
            result_obj = DownloadResponse(**rec["result"])
        result.append(
            AsyncJobStatusResponse(
                id=rec["id"],
                status=rec["status"],
                created_at=rec["created_at"],
                updated_at=rec["updated_at"],
                result=result_obj,
                error=rec.get("error"),
            )
        )
    return result


@app.post("/download/async/form", response_model=AsyncEnqueueResponse)
async def enqueue_download_form(
    url: Optional[str] = Form(None),
    format: Optional[str] = Form(None),
    quality: Optional[str] = Form(None),
    cookies_file: Optional[UploadFile] = File(None),
):
    """
    Enqueue a download job using multipart/form-data (supports cookies file upload).
    Form fields:
      - url (required)
      - format (optional; defaults to settings.DEFAULT_AUDIO_FORMAT)
      - quality (optional; defaults to settings.DEFAULT_AUDIO_QUALITY)
      - cookies_file (optional; text cookies file, <=64KB)
    Returns a job_id for polling at /download/async/{job_id}.
    """
    if not url:
        raise HTTPException(status_code=400, detail="url field required")

    fmt = (format or settings.DEFAULT_AUDIO_FORMAT).lower()
    q = (quality or settings.DEFAULT_AUDIO_QUALITY).lower()

    if cookies_file:
        content = await cookies_file.read()
        if len(content) > 64 * 1024:
            raise HTTPException(status_code=400, detail="Cookies file too large (>64KB)")
        cookies_content = content.decode("utf-8")
        logger.info(f"Received cookies file upload (async form): {cookies_file.filename}")
    else:
        cookies_content = None

    _validate_url(url)
    if not _is_allowed_format(fmt):
        raise HTTPException(status_code=400, detail=f"Format not allowed. Allowed: {settings.allowed_formats_list}")

    bitrate = _map_quality(q)
    job_id = uuid.uuid4().hex
    payload = dict(url=url, format=fmt, quality_label=q, bitrate=bitrate, cookies=cookies_content)
    _create_job_record(job_id, payload)
    await job_queue.put((job_id, payload))
    logger.info(f"Enqueued (form) job {job_id} url={url}")
    return AsyncEnqueueResponse(job_id=job_id, status="pending")


async def _perform_download(url: str, audio_format: str, quality_label: str, bitrate: int, cookies_content: Optional[str]):
    unique_id = str(uuid.uuid4())
    out_template = str(_target_dir() / f"{unique_id}.%(ext)s")

    # Prepare yt-dlp options
    # If bitrate == 0, let ffmpeg choose best; else map to preferredquality.
    postprocessors = [{
        "key": "FFmpegExtractAudio",
        "preferredcodec": audio_format,
    }]
    if bitrate > 0:
        postprocessors[0]["preferredquality"] = str(bitrate)

    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": out_template,
        "extractaudio": True,
        "audioformat": audio_format,
        "quiet": True,
        "no_warnings": True,
        "postprocessors": postprocessors,
    }

    cookies_file_path = None
    if cookies_content:
        cookies_file_path = _target_dir() / f"{unique_id}_cookies.txt"
        cookies_file_path.write_text(cookies_content, encoding="utf-8")
        ydl_opts["cookiefile"] = str(cookies_file_path)
        logger.info("Using provided cookies for download")

    async with download_semaphore:
        try:
            def blocking_download():
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(url, download=False)
                    ydl.download([url])
                    return info

            info = await asyncio.to_thread(blocking_download)
        except yt_dlp.utils.DownloadError as e:
            logger.error(f"yt-dlp download error: {e}")
            raise HTTPException(status_code=500, detail=f"Download failed: {e}") from e
        except Exception as e:
            logger.error(f"Unexpected download error: {e}")
            raise HTTPException(status_code=500, detail="Internal download error") from e
        finally:
            if cookies_file_path and cookies_file_path.exists():
                try:
                    cookies_file_path.unlink()
                except Exception as e:
                    logger.warning(f"Failed cleanup cookies file: {e}")

    # Locate the produced audio file (match allowed formats)
    downloaded_file = None
    for path in _target_dir().glob(f"{unique_id}.*"):
        if path.suffix.lower().lstrip(".") in settings.allowed_formats_list:
            downloaded_file = path
            break

    if not downloaded_file or not downloaded_file.exists():
        raise HTTPException(status_code=500, detail="Download failed – output file not found")

    file_size = downloaded_file.stat().st_size
    title = info.get("title")
    duration = info.get("duration")

    # Optional Azure Blob upload (with misconfiguration detection)
    blob_uploaded = False
    blob_url: Optional[str] = None
    blob_sas_url: Optional[str] = None
    blob_error: Optional[str] = None

    # Azure upload decision matrix:
    # - Feature flag must be on and credentials valid (connection string OR account URL + SAS token)
    # - Library must be installed
    if settings.AZURE_UPLOAD_ENABLED and not settings.azure_is_configured:
        blob_error = "Azure upload enabled but missing credentials (provide AZURE_STORAGE_CONNECTION_STRING OR AZURE_BLOB_ACCOUNT_URL + AZURE_SAS_TOKEN)"
        logger.error(blob_error)
    elif settings.azure_is_configured and BlobServiceClient is None:
        blob_error = "Azure upload configured but azure-storage-blob not installed"
        logger.error(blob_error)
    elif settings.azure_is_configured:
        try:
            blob_url, blob_sas_url = await _upload_to_azure(downloaded_file, downloaded_file.name)
            if blob_url:
                blob_uploaded = True
                if settings.AZURE_DELETE_LOCAL_AFTER_UPLOAD:
                    try:
                        downloaded_file.unlink(missing_ok=True)
                        logger.info(f"Deleted local file {downloaded_file.name} after Azure upload (AZURE_DELETE_LOCAL_AFTER_UPLOAD=true)")
                    except Exception as e:
                        logger.warning(f"Failed to delete local file after Azure upload: {e}")
        except Exception as e:
            blob_error = f"Azure upload failed: {e}"
            logger.error(blob_error)

    return DownloadResponse(
        success=True,
        filename=downloaded_file.name,
        file_size=file_size,
        duration=duration,
        title=title,
        quality=quality_label,
        blob_uploaded=blob_uploaded if (settings.AZURE_UPLOAD_ENABLED or settings.azure_is_configured) else None,
        blob_url=blob_url,
        blob_sas_url=blob_sas_url,
        blob_error=blob_error,
    )


async def _upload_to_azure(local_path: Path, logical_name: str):
    """
    Upload the file at local_path to Azure Blob Storage if configured.
    Returns (blob_url, blob_sas_url) where blob_sas_url may be None.
    """
    if not settings.azure_is_configured:
        return None, None
    if BlobServiceClient is None:
        raise RuntimeError("azure-storage-blob not installed but AZURE_UPLOAD_ENABLED set")

    # Build blob name with optional prefix
    prefix = settings.AZURE_BLOB_PREFIX.strip().strip("/")
    if prefix:
        blob_name = f"{prefix}/{logical_name}"
    else:
        blob_name = logical_name

    # Use thread offloading for blocking SDK calls
    def _blocking():
        # Instantiate BlobServiceClient according to credential mode
        if settings.azure_uses_connection_string:
            bsc = BlobServiceClient.from_connection_string(settings.AZURE_STORAGE_CONNECTION_STRING)
        elif settings.azure_uses_sas:
            bsc = BlobServiceClient(account_url=settings.AZURE_BLOB_ACCOUNT_URL, credential=settings.azure_sas_token_clean)
        else:
            raise RuntimeError("Azure not properly configured (no connection string or SAS token)")

        container_client = bsc.get_container_client(settings.AZURE_BLOB_CONTAINER_NAME)
        try:
            container_client.create_container()
        except Exception:
            pass  # Already exists or race

        blob_client = container_client.get_blob_client(blob_name)

        # Upload (overwrite behavior)
        with open(local_path, "rb") as f:
            blob_client.upload_blob(f, overwrite=True)

        blob_url = blob_client.url
        sas_url = None

        # Only attempt SAS generation if using connection string (need account key)
        if settings.azure_uses_connection_string:
            if settings.AZURE_GENERATE_SAS and generate_blob_sas and BlobSasPermissions:
                try:
                    perms = BlobSasPermissions.from_string(settings.AZURE_SAS_PERMISSIONS)
                    expiry = datetime.utcnow() + timedelta(seconds=settings.AZURE_SAS_EXPIRY_SECONDS)
                    sas_token = generate_blob_sas(
                        account_name=bsc.account_name,
                        container_name=settings.AZURE_BLOB_CONTAINER_NAME,
                        blob_name=blob_name,
                        permission=perms,
                        expiry=expiry,
                    )
                    sas_url = f"{blob_url}?{sas_token}"
                except Exception as e:
                    logging.getLogger("yt-dlp-server").warning(f"Failed to generate SAS: {e}")
        else:
            # If user requested SAS generation but only supplied a pre-generated SAS token, warn once.
            if settings.AZURE_GENERATE_SAS:
                logging.getLogger("yt-dlp-server").warning("AZURE_GENERATE_SAS ignored when using pre-generated SAS credentials (provide connection string for dynamic SAS).")

        return blob_url, sas_url

    return await asyncio.to_thread(_blocking)


@app.get("/download/{filename}")
async def get_file(filename: str):
    # Enforce UUID filename pattern
    if not _is_valid_uuid_prefix(filename):
        raise HTTPException(status_code=400, detail="Invalid filename pattern")
    file_path = _target_dir() / filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(path=file_path, media_type="audio/mpeg", filename=filename)


@app.delete("/download/{filename}")
async def delete_file(filename: str):
    if not _is_valid_uuid_prefix(filename):
        raise HTTPException(status_code=400, detail="Invalid filename pattern")
    file_path = _target_dir() / filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    try:
        file_path.unlink()
        return {"success": True, "message": "File deleted"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete file: {e}")


def _is_valid_uuid_prefix(name: str) -> bool:
    try:
        stem = name.split(".", 1)[0]
        uuid.UUID(stem)
        return True
    except Exception:
        return False


@app.get("/health")
async def health():
    """
    Lightweight liveness probe (does not perform filesystem writes).
    """
    return {"status": "ok", "service": "yt-dlp API", "version": app.version}


@app.get("/readiness")
async def readiness():
    """
    Readiness probe performing:
      - Directory existence & write test
      - Free disk space check
    """
    target = _target_dir()
    target.mkdir(parents=True, exist_ok=True)
    # Disk space
    usage = shutil.disk_usage(target)
    free_mb = usage.free / (1024 * 1024)
    if free_mb < settings.MIN_FREE_DISK_MB:
        raise HTTPException(status_code=503, detail="Insufficient disk space")

    # Write test
    test_file = target / f"._rw_test_{uuid.uuid4().hex}"
    try:
        test_file.write_text("ok", encoding="utf-8")
        test_file.unlink(missing_ok=True)
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Write test failed: {e}")

    return {
        "status": "ready",
        "free_disk_mb": int(free_mb),
        "min_required_mb": settings.MIN_FREE_DISK_MB,
        "output_dir": str(target),
        "concurrency_limit": settings.MAX_CONCURRENT_DOWNLOADS,
    }


if __name__ == "__main__":
    # Allow running standalone: python -m app.main
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8080)