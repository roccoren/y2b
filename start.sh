#!/bin/bash

# YouTube Audio Downloader to Azure - Startup Script

set -e

echo "🚀 Starting YouTube Audio Downloader to Azure Setup..."

# Check if Docker is installed
if ! command -v docker &> /dev/null; then
    echo "❌ Docker is not installed. Please install Docker first."
    exit 1
fi

if ! command -v docker-compose &> /dev/null; then
    echo "❌ Docker Compose is not installed. Please install Docker Compose first."
    exit 1
fi

# Check if .env file exists
if [ ! -f .env ]; then
    echo "⚠️  .env file not found. Creating from template..."
    cp .env.template .env
    echo "📝 Please edit .env file with your Azure credentials before continuing."
    echo "   Required: AZURE_STORAGE_CONNECTION_STRING"
    echo ""
    read -p "Press Enter after configuring .env file..."
fi

# Create necessary directories
echo "📁 Creating directories..."
mkdir -p downloads
mkdir -p workflows

# Copy workflow to workflows directory
echo "📋 Copying workflow..."
cp youtube-audio-downloader-workflow.json workflows/

# Build and start services
echo "🔨 Building Docker images..."
docker-compose build

echo "🚀 Starting services..."
docker-compose up -d

# Wait for services to be ready
echo "⏳ Waiting for services to start..."
sleep 10

# Check service health
echo "🔍 Checking service health..."

# Check yt-dlp server
if curl -s http://localhost:8080/health > /dev/null; then
    echo "✅ yt-dlp server is running at http://localhost:8080"
else
    echo "❌ yt-dlp server is not responding"
fi

# Check n8n
if curl -s http://localhost:5678 > /dev/null; then
    echo "✅ n8n is running at http://localhost:5678"
else
    echo "❌ n8n is not responding"
fi

echo ""
echo "🎉 Setup complete!"
echo ""
echo "Next steps:"
echo "1. Open n8n at http://localhost:5678"
echo "   - Username: admin"
echo "   - Password: password"
echo ""
echo "2. Import the workflow:"
echo "   - Go to Workflows → Import from file"
echo "   - Select workflows/youtube-audio-downloader-workflow.json"
echo ""
echo "3. Configure Azure credentials in n8n:"
echo "   - Go to Credentials → Add Credential"
echo "   - Add Microsoft Azure credentials with your connection string"
echo ""
echo "4. Update the 'Get YouTube URL' node with your desired YouTube URL"
echo ""
echo "5. Activate the workflow to start scheduled downloads"
echo ""
echo "📊 Monitor services:"
echo "   docker-compose logs -f"
echo ""
echo "🛑 Stop services:"
echo "   docker-compose down"