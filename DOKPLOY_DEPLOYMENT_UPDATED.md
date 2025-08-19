# Dokploy Deployment Guide - Updated Configuration

This guide covers deploying the YouTube Audio Downloader service using the updated Docker Compose configuration optimized for Dokploy.

## Key Changes in Updated Configuration

### What's Different
- **Simplified networking**: Removed custom networks that interfere with Dokploy
- **Direct port mapping**: Uses `ports: "8080:8080"` instead of `expose`
- **Traefik labels**: Added proper Traefik configuration for automatic domain routing
- **Reduced complexity**: Removed unnecessary security restrictions that conflict with Dokploy
- **Environment variable support**: Uses `${DOMAIN}` for dynamic domain configuration

### Why These Changes Matter
1. **Dokploy compatibility**: Simplified structure works better with Dokploy's service discovery
2. **Automatic SSL**: Traefik labels enable automatic Let's Encrypt certificate generation
3. **HTTP to HTTPS redirect**: Automatic redirection from HTTP to HTTPS
4. **Domain flexibility**: Easy domain configuration via environment variables

## Deployment Steps

### Step 1: Configure Environment Variables

Create your `.env` file from the template:

```bash
cp .env.template .env
```

Edit the `.env` file and set your domain:

```bash
# REQUIRED: Replace with your actual domain
DOMAIN=your-actual-domain.com

# Optional: Other configurations...
```

### Step 2: Deploy to Dokploy

#### Option A: Via Dokploy Dashboard

1. **Create New Application**:
   - Go to Dokploy dashboard
   - Click "Create Application"
   - Select "Docker Compose" deployment type

2. **Upload Project Files**:
   - Upload your project folder or connect Git repository
   - Ensure `docker-compose.yml` and `.env` are included

3. **Configure Environment**:
   - In Dokploy dashboard, go to "Environment Variables"
   - Set `DOMAIN=your-domain.com`
   - Add any other required variables

4. **Deploy**:
   - Click "Deploy" button
   - Monitor deployment logs
   - Dokploy will automatically handle Traefik configuration

#### Option B: CLI Deployment

```bash
# Clone your repository
git clone <your-repo-url>
cd yt-dlp-server

# Set your domain in .env file
echo "DOMAIN=your-domain.com" > .env

# Login to your container registry
docker login dokployacr.azurecr.io

# Deploy
docker-compose up -d
```

### Step 3: Verify Deployment

1. **Check service status**:
   ```bash
   docker-compose ps
   ```

2. **Test health endpoint**:
   ```bash
   curl https://your-domain.com/health
   ```

3. **Check Traefik dashboard** (if enabled):
   - Verify service is registered
   - Check SSL certificate status

## Configuration Details

### Docker Compose Key Features

```yaml
# Direct port mapping for Dokploy compatibility
ports:
  - "8080:8080"

# Traefik labels for automatic routing
labels:
  - "traefik.enable=true"
  - "traefik.http.routers.yt-dlp-server.rule=Host(`${DOMAIN}`)"
  - "traefik.http.routers.yt-dlp-server.entrypoints=websecure"
  - "traefik.http.routers.yt-dlp-server.tls.certresolver=letsencrypt"
```

### Traefik Configuration Explained

1. **`traefik.enable=true`**: Enables Traefik routing for this service
2. **`rule=Host(\`${DOMAIN}\`)`**: Routes traffic from your domain to this service
3. **`entrypoints=websecure`**: Uses HTTPS endpoint (port 443)
4. **`tls.certresolver=letsencrypt`**: Automatically generates SSL certificate
5. **HTTP redirect**: Automatically redirects HTTP traffic to HTTPS

### Volume Configuration

```yaml
volumes:
  - ./downloads:/app/downloads          # Local bind mount for easy access
  - downloads_temp:/tmp/yt-dlp-downloads # Named volume for temporary files
```

## Troubleshooting

### Common Issues and Solutions

#### 1. Service Not Accessible via Domain

**Symptoms**: 
- `curl https://your-domain.com/health` fails
- 502 Bad Gateway error

**Solutions**:
```bash
# Check if service is running
docker-compose ps

# Check service logs
docker-compose logs yt-dlp-server

# Verify domain DNS
nslookup your-domain.com

# Check Traefik logs (if accessible)
docker logs traefik
```

#### 2. SSL Certificate Issues

**Symptoms**: 
- SSL/TLS certificate errors
- "Certificate not valid" warnings

**Solutions**:
- Ensure domain DNS points to your server
- Wait 5-10 minutes for certificate generation
- Check Let's Encrypt rate limits
- Verify Traefik certificate resolver is configured

#### 3. Port Conflicts

**Symptoms**: 
- "Port already in use" errors
- Service fails to start

**Solutions**:
```bash
# Check what's using port 8080
sudo netstat -tulpn | grep :8080

# Change port if needed (modify docker-compose.yml)
ports:
  - "8081:8080"  # Use different external port
```

#### 4. Container Registry Access

**Symptoms**: 
- "Image pull failed" errors
- Authentication failures

**Solutions**:
```bash
# Login to Azure Container Registry
docker login dokployacr.azurecr.io

# Verify image exists
docker pull dokployacr.azurecr.io/yt-dlp-server:latest

# Build and push if needed
docker build -t dokployacr.azurecr.io/yt-dlp-server:latest .
docker push dokployacr.azurecr.io/yt-dlp-server:latest
```

## Testing Your Deployment

### 1. Health Check
```bash
curl https://your-domain.com/health
# Expected: {"status": "healthy", "service": "yt-dlp API"}
```

### 2. Download Test
```bash
# Test audio download
curl -X POST "https://your-domain.com/download" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "format": "mp3"}'
```

### 3. File Access Test
```bash
# Get file (replace with actual filename from download response)
curl "https://your-domain.com/download/FILENAME.mp3" --output test.mp3
```

## Production Recommendations

### Security Enhancements
1. **API Authentication**: Add API keys or JWT tokens
2. **Rate Limiting**: Configure Traefik rate limiting middleware
3. **CORS Configuration**: Set appropriate CORS headers
4. **Input Validation**: Validate YouTube URLs and parameters

### Performance Optimization
1. **Resource Scaling**: Adjust memory/CPU limits based on usage
2. **CDN Integration**: Use CDN for file delivery
3. **Storage Optimization**: Implement automatic cleanup of old files
4. **Caching**: Add Redis for metadata caching

### Monitoring Setup
```bash
# Monitor resource usage
docker stats yt-dlp-server

# View real-time logs
docker-compose logs -f yt-dlp-server

# Check download directory size
du -sh ./downloads
```

## Environment Variables Reference

| Variable | Required | Description | Default |
|----------|----------|-------------|---------|
| `DOMAIN` | **Yes** | Your domain name | `localhost` |
| `YT_DLP_SERVER_HOST` | No | Server bind address | `0.0.0.0` |
| `YT_DLP_SERVER_PORT` | No | Internal server port | `8080` |
| `AZURE_STORAGE_CONNECTION_STRING` | No | Azure storage connection | - |
| `TZ` | No | Container timezone | `UTC` |

## Next Steps

1. **Configure your domain**: Update DNS settings to point to your Dokploy server
2. **Set environment variables**: Update `.env` with your actual domain
3. **Deploy the service**: Use Dokploy dashboard or CLI
4. **Test the endpoints**: Verify all API endpoints work correctly
5. **Monitor performance**: Set up logging and monitoring
6. **Implement security**: Add authentication and rate limiting

This updated configuration should resolve the domain exposure issues you experienced with the previous setup.