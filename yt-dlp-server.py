#!/usr/bin/env python3
"""
yt-dlp API Server for n8n integration
Provides a REST API to download YouTube audio using yt-dlp
"""

import os
import tempfile
import asyncio
from fastapi import FastAPI, HTTPException, File, UploadFile, Form, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, HttpUrl
from typing import Optional, Union
import yt_dlp
import uvicorn
from pathlib import Path
import uuid
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="yt-dlp API Server", version="1.0.0")

class DownloadRequest(BaseModel):
    url: HttpUrl
    format: str = "mp3"
    quality: str = "best"
    cookies: str = None  # Optional cookies parameter - treats as cookie string content

class DownloadResponse(BaseModel):
    success: bool
    filename: str
    file_size: int
    duration: float
    title: str

# Create temporary directory for downloads
TEMP_DIR = Path(tempfile.gettempdir()) / "yt-dlp-downloads"
TEMP_DIR.mkdir(exist_ok=True)

@app.post("/download", response_model=DownloadResponse)
async def download_audio(
    request: Request,
    # JSON request parameters
    url: Optional[str] = Form(None),
    format: Optional[str] = Form("mp3"),
    quality: Optional[str] = Form("best"),
    cookies_file: Optional[UploadFile] = File(None)
):
    """Download YouTube audio and return file information
    
    Supports both JSON requests and form data with file uploads:
    - JSON: Send DownloadRequest with cookies as string
    - Form data: Send form fields with optional cookies_file upload
    """
    
    # Check content type to determine request format
    content_type = request.headers.get("content-type", "")
    
    if "application/json" in content_type:
        # Handle JSON request
        body = await request.body()
        import json
        try:
            json_data = json.loads(body)
            return await _download_audio_logic(
                url=json_data.get("url"),
                format=json_data.get("format", "mp3"),
                quality=json_data.get("quality", "best"),
                cookies_content=json_data.get("cookies")
            )
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="Invalid JSON")
    else:
        # Handle form data request
        if not url:
            raise HTTPException(status_code=400, detail="URL is required")
            
        cookies_content = None
        if cookies_file:
            # Read the uploaded cookies file content
            cookies_content = (await cookies_file.read()).decode('utf-8')
            logger.info(f"Received uploaded cookies file: {cookies_file.filename}")
        
        return await _download_audio_logic(
            url=url,
            format=format,
            quality=quality,
            cookies_content=cookies_content
        )

async def _download_audio_logic(url: str, format: str, quality: str, cookies_content: Optional[str] = None):
    """Common download logic for both endpoints"""
    try:
        # Generate unique filename
        unique_id = str(uuid.uuid4())
        output_template = str(TEMP_DIR / f"{unique_id}.%(ext)s")
        
        # Configure yt-dlp options
        ydl_opts = {
            'format': 'bestaudio/best',
            'outtmpl': output_template,
            'extractaudio': True,
            'audioformat': format,
            'audioquality': '192K',
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': format,
                'preferredquality': '192',
            }],
            'quiet': True,
            'no_warnings': True,
        }
        
        # Add cookies if provided
        cookies_file_path = None
        if cookies_content:
            # Save cookies content to a temporary file
            cookies_file_path = TEMP_DIR / f"{unique_id}_cookies.txt"
            with open(cookies_file_path, 'w') as f:
                f.write(cookies_content)
            ydl_opts['cookiefile'] = str(cookies_file_path)
            logger.info("Using uploaded/provided cookies")
        
        # Download the audio
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            # Extract info first
            info = ydl.extract_info(url, download=False)
            title = info.get('title', 'Unknown')
            duration = info.get('duration', 0)
            
            # Download the audio
            ydl.download([url])
        
        # Find the downloaded file
        downloaded_file = None
        for file_path in TEMP_DIR.glob(f"{unique_id}.*"):
            if file_path.suffix.lower() in ['.mp3', '.m4a', '.wav', '.ogg']:
                downloaded_file = file_path
                break
        
        if not downloaded_file or not downloaded_file.exists():
            raise HTTPException(status_code=500, detail="Download failed - file not found")
        
        file_size = downloaded_file.stat().st_size
        
        # Clean up temporary cookie file
        if cookies_file_path and cookies_file_path.exists():
            try:
                cookies_file_path.unlink()
                logger.info("Cleaned up temporary cookies file")
            except Exception as e:
                logger.warning(f"Failed to clean up cookies file: {e}")
        
        return DownloadResponse(
            success=True,
            filename=downloaded_file.name,
            file_size=file_size,
            duration=duration,
            title=title
        )
        
    except Exception as e:
        logger.error(f"Download error: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Download failed: {str(e)}")

@app.get("/download/{filename}")
async def get_file(filename: str):
    """Retrieve downloaded audio file"""
    file_path = TEMP_DIR / filename
    
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    
    return FileResponse(
        path=file_path,
        media_type='audio/mpeg',
        filename=filename
    )

@app.delete("/download/{filename}")
async def delete_file(filename: str):
    """Delete downloaded audio file"""
    file_path = TEMP_DIR / filename
    
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    
    try:
        file_path.unlink()
        return {"success": True, "message": "File deleted"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete file: {str(e)}")

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy", "service": "yt-dlp API"}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080)