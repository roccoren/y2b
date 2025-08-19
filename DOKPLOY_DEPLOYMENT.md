# Dokploy Deployment Guide

This guide covers deploying the YouTube Audio Downloader service using Dokploy with public internet exposure.

## Overview

The application has been optimized for Dokploy deployment with the following enhancements:

- **Internal Service**: Exposes port 8080 internally for Dokploy's Traefik
- **Security Hardening**: Non-root user, resource limits
- **Production-Ready**: Enhanced health checks, logging, monitoring
- **Manual Proxy Setup**: Traefik configuration handled manually via Dokploy

## Pre-Deployment Requirements

### 1. Domain Configuration
- A domain name pointing to your Dokploy server
- DNS A record configured: `your-domain.com` → `your-server-ip`

### 2. Dokploy Server Requirements
- Dokploy installed and running
- Docker and Docker Compose available
- Traefik configured as reverse proxy
- Let's Encrypt certificate resolver enabled

### 3. Azure Container Registry Access
- Access to `dokployacr.azurecr.io` registry
- Docker login credentials for the registry
- Permissions to pull images from the registry

## Deployment Steps

### Step 1: Environment Configuration

1. **Update the `.env` file** with your specific values:

```bash
# Required: Replace with your actual domain
DOMAIN=your-domain.com

# Optional: Azure Storage (if using Azure integration)
AZURE_STORAGE_CONNECTION_STRING=DefaultEndpointsProtocol=https;AccountName=...

# Security Settings (adjust as needed)
RATE_LIMIT_BURST=10
RATE_LIMIT_AVERAGE=5
```

### Step 2: Docker Compose Configuration

The provided [`docker-compose.yml`](docker-compose.yml) includes:

#### Key Features:
- **Private Registry**: Uses Azure Container Registry (`dokplayacr.azurecr.io`)
- **Internal Service**: Only exposes port 8080 internally
- **Network Isolation**: Dedicated bridge network
- **Persistent Storage**: Named volume for downloads
- **Resource Limits**: Memory (2GB) and CPU (1 core) limits
- **Security**: Non-root user (1000:1000)
- **Health Checks**: Enhanced monitoring

#### Service Configuration:
```yaml
expose:
  - "8080"  # Internal port only
labels:
  - "app.name=yt-dlp-server"
  - "app.version=1.0.0"
  - "app.description=YouTube Audio Downloader API"
```

### Step 3: Build and Push Docker Image

Before deploying, you need to build and push the image to Azure Container Registry:

```bash
# Login to Azure Container Registry
docker login dokployacr.azurecr.io

# Build the image
docker build -t dokployacr.azurecr.io/yt-dlp-server:latest .

# Push to registry
docker push dokployacr.azurecr.io/yt-dlp-server:latest
```

### Step 4: Deploy via Dokploy

1. **Create New Application** in Dokploy dashboard
2. **Select Docker Compose** deployment type
3. **Upload your project files** or connect Git repository
4. **Configure environment variables** in Dokploy UI
5. **Ensure registry access** to `dokplayacr.azurecr.io`
6. **Deploy the application**

#### Alternative: CLI Deployment

```bash
# Clone your repository
git clone <your-repo-url>
cd yt-dlp-server

# Login to Azure Container Registry
docker login dokployacr.azurecr.io

# Build and push image (if not already done)
docker build -t dokployacr.azurecr.io/yt-dlp-server:latest .
docker push dokployacr.azurecr.io/yt-dlp-server:latest

# Update environment variables
cp .env.template .env
nano .env  # Edit with your values

# Deploy via Docker Compose
docker-compose up -d
```

## Configuration Details

### Environment Variables

| Variable | Required | Description | Default |
|----------|----------|-------------|---------|
| `DOMAIN` | Yes | Your public domain name | - |
| `YT_DLP_SERVER_HOST` | No | Server bind address | `0.0.0.0` |
| `YT_DLP_SERVER_PORT` | No | Server port | `8080` |
| `AZURE_STORAGE_CONNECTION_STRING` | No | Azure storage connection | - |
| `DOCKER_REGISTRY` | No | Docker registry URL | `dokployacr.azurecr.io` |

### Security Features

#### 1. Container Security
- **Non-root user**: UID/GID 1000
- **Read-only filesystem**: Where possible
- **Tmpfs mounts**: For temporary files

### Resource Management

#### Memory & CPU Limits
```yaml
deploy:
  resources:
    limits:
      memory: 2G
      cpus: '1.0'
    reservations:
      memory: 512M
      cpus: '0.25'
```

#### Storage
- **Named Volume**: `downloads` for persistence
- **Bind Mount**: `./downloads` for local access
- **Tmpfs**: `/tmp` for temporary processing

## Public Access Configuration

### Dokploy Traefik Setup

The service exposes port 8080 internally. You need to configure Dokploy's Traefik manually:

#### Manual Traefik Configuration Steps:

1. **In Dokploy Dashboard**:
   - Navigate to your deployed application
   - Go to "Domains" or "Traefik" configuration section
   - Add a new domain configuration

2. **Configure Domain Routing**:
   ```yaml
   # Example Traefik configuration for Dokploy
   Host: your-domain.com
   Target Port: 8080
   Service Name: yt-dlp-server
   Enable SSL: Yes (Let's Encrypt)
   ```

3. **Optional Middleware** (configure in Dokploy if available):
   - Rate limiting
   - CORS headers
   - Authentication
   - Request logging

4. **Service Access**:
   - **Internal**: `http://yt-dlp-server:8080` (within Docker network)
   - **External**: `https://your-domain.com` (via Dokploy's Traefik)

#### Important Notes:
- No Traefik labels in docker-compose.yml are needed
- Dokploy handles all reverse proxy configuration
- SSL certificates are managed automatically by Dokploy's Traefik
- Rate limiting and security should be configured in Dokploy's interface

### API Endpoints

Once deployed, your API will be available at:

- **Health Check**: `https://your-domain.com/health`
- **Download Audio**: `POST https://your-domain.com/download`
- **Get File**: `GET https://your-domain.com/download/{filename}`
- **Delete File**: `DELETE https://your-domain.com/download/{filename}`

### Example API Usage

```bash
# Download YouTube audio
curl -X POST "https://your-domain.com/download" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://www.youtube.com/watch?v=VIDEO_ID", "format": "mp3"}'

# Download with cookies file
curl -X POST "https://your-domain.com/download" \
  -F "url=https://www.youtube.com/watch?v=VIDEO_ID" \
  -F "cookies_file=@/path/to/cookies.txt"

# Health check
curl "https://your-domain.com/health"
```

## Monitoring & Logging

### Health Checks
- **Endpoint**: `/health`
- **Interval**: 30 seconds
- **Timeout**: 10 seconds
- **Retries**: 3

### Logging Configuration
```yaml
logging:
  driver: "json-file"
  options:
    max-size: "50m"
    max-file: "3"
```

### Monitoring Commands
```bash
# View logs
docker-compose logs -f yt-dlp-server

# Check health
docker-compose ps

# Monitor resources
docker stats yt-dlp-server
```

## Troubleshooting

### Common Issues

#### 1. SSL Certificate Issues
```bash
# Check Traefik logs
docker logs traefik

# Verify DNS resolution
nslookup your-domain.com
```

#### 2. Service Not Accessible
- **Symptom**: Cannot reach service via domain
- **Solution**: Check Dokploy's Traefik configuration and domain routing

#### 3. Download Failures
```bash
# Check container logs
docker-compose logs yt-dlp-server

# Test health endpoint
curl https://your-domain.com/health
```

#### 4. Permission Issues
```bash
# Fix file permissions
sudo chown -R 1000:1000 ./downloads
```

### Debug Commands

```bash
# Container shell access
docker-compose exec yt-dlp-server bash

# Test internal connectivity
docker-compose exec yt-dlp-server curl localhost:8080/health

# Check resource usage
docker-compose exec yt-dlp-server top
```

## Security Considerations

### Production Security Checklist

- [ ] Domain SSL certificate active
- [ ] Rate limiting configured
- [ ] Non-root container user
- [ ] Resource limits set
- [ ] Log rotation enabled
- [ ] Regular security updates
- [ ] Firewall rules configured
- [ ] Backup strategy implemented

### Recommended Security Enhancements

1. **API Authentication**: Add API keys for production use
2. **Request Validation**: Implement input sanitization
3. **File Scanning**: Add malware scanning for downloads
4. **Audit Logging**: Log all API requests
5. **Network Segmentation**: Use dedicated VPC/subnet

## Backup & Recovery

### Backup Strategy

```bash
# Backup download files
tar -czf downloads-backup-$(date +%Y%m%d).tar.gz ./downloads

# Backup configuration
cp .env env-backup-$(date +%Y%m%d)
cp docker-compose.yml compose-backup-$(date +%Y%m%d).yml
```

### Recovery Process

```bash
# Restore from backup
tar -xzf downloads-backup-YYYYMMDD.tar.gz
cp env-backup-YYYYMMDD .env
docker-compose up -d
```

## Performance Optimization

### Scaling Options

1. **Horizontal Scaling**: Multiple container instances
2. **Load Balancing**: Traefik automatic load balancing
3. **Resource Tuning**: Adjust CPU/memory limits
4. **Storage Optimization**: Use SSD for downloads

### Performance Monitoring

```bash
# Resource usage
docker stats yt-dlp-server

# Network traffic
docker exec yt-dlp-server netstat -i

# Disk usage
du -sh ./downloads
```

## Support & Maintenance

### Regular Maintenance Tasks

1. **Log Rotation**: Automated via Docker logging
2. **Image Updates**: Rebuild with latest base images
3. **Security Patches**: Regular system updates
4. **Storage Cleanup**: Remove old download files
5. **Certificate Renewal**: Automatic via Let's Encrypt

### Update Process

```bash
# Pull latest changes
git pull origin main

# Build and push new image version
docker build -t dokployacr.azurecr.io/yt-dlp-server:latest .
docker push dokployacr.azurecr.io/yt-dlp-server:latest

# Pull new image and restart
docker-compose pull
docker-compose up -d
```

## Azure Container Registry Setup

### Registry Authentication

For Dokploy to pull images from the private Azure Container Registry, ensure proper authentication:

#### Option 1: Docker Login (Recommended for development)
```bash
# Login to Azure Container Registry
docker login dokployacr.azurecr.io
# Enter username and password when prompted
```

#### Option 2: Service Principal (Recommended for production)
```bash
# Using Azure CLI
az acr login --name dokployacr

# Or with service principal
docker login dokployacr.azurecr.io \
  --username <service-principal-id> \
  --password <service-principal-password>
```

#### Option 3: Managed Identity (For Azure VMs)
```bash
# Enable managed identity on your VM
az acr login --name dokployacr --identity
```

### Image Management

#### Building and Tagging
```bash
# Build with specific tag
docker build -t dokployacr.azurecr.io/yt-dlp-server:v1.0.0 .
docker build -t dokployacr.azurecr.io/yt-dlp-server:latest .

# Push both tags
docker push dokployacr.azurecr.io/yt-dlp-server:v1.0.0
docker push dokployacr.azurecr.io/yt-dlp-server:latest
```

#### Registry Troubleshooting
```bash
# Test registry connectivity
docker pull dokployacr.azurecr.io/yt-dlp-server:latest

# List available images
az acr repository list --name dokployacr --output table

# Check image details
az acr repository show-tags --name dokployacr --repository yt-dlp-server
```

This deployment configuration provides a production-ready, secure, and scalable YouTube audio downloader service accessible via the public internet through Dokploy.