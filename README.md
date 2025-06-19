# 🧹 Immich Image Cleaner

A powerful standalone plugin for Immich that automatically detects and helps remove unwanted images from your photo library. Perfect for cleaning up after data recovery, migration from other services, or general library maintenance.

## ✨ Features

- **Screenshot Detection** - All types (mobile, desktop, web captures)
- **Web Cache Cleanup** - Thumbnails, avatars, ads, browser artifacts  
- **Data Recovery Junk** - Temp files, corrupted images, recovery artifacts
- **Smart Analysis** - Multiple detection methods with confidence scoring
- **Beautiful Web Interface** - Real-time progress and easy bulk operations
- **Leverages Immich ML** - Uses your existing facial recognition and GPU acceleration

## 🚀 Quick Start

### Docker Installation

```bash
docker run -d \
  --name immich-image-cleaner \
  --restart unless-stopped \
  -p 5001:5000 \
  -e IMMICH_URL="http://your-immich-server:2283/api" \
  -e IMMICH_API_KEY="your-api-key" \
  -e SECRET_KEY="your-secure-secret" \
  -v ./data:/app/data \
  -v ./logs:/app/logs \
  ghcr.io/YOUR-USERNAME/immich-image-cleaner:latest
