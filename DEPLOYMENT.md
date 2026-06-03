# Deployment Guide - Storybook Backend

This guide covers deploying the Storybook backend application on a Linux VM.

## Current Ubuntu Deployment

This is the deployment shape used for the Ubuntu server.

- Server IP: `129.154.251.149`
- App directory: `/home/ubuntu/sb-backend`
- Python: `python3.11`
- Virtual environment: `/home/ubuntu/sb-backend/.venv`
- Backend service: `storybook-backend`
- Backend process: `uvicorn app.main:app --host 127.0.0.1 --port 8000`
- Nginx public entrypoint: `http://129.154.251.149`
- API public path: `http://129.154.251.149/api/v1/...`
- UI public path: `http://129.154.251.149/`

FastAPI already uses `API_V1_PREFIX=/api/v1`, so Nginx should proxy `/api/` to the backend without adding another `/api`.

## First-Time Server Setup

Ubuntu 22.04 does not provide `python3.12` by default. This project supports Python `>=3.11,<3.14`, so use Python 3.11.

```bash
sudo apt update
sudo apt install -y python3.11 python3.11-venv python3.11-dev python3-pip nginx mysql-server git build-essential
```

Clone the repo:

```bash
cd /home/ubuntu
git clone <your-repo-url> sb-backend
cd /home/ubuntu/sb-backend
```

Create the virtual environment and install dependencies:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

Create and edit environment file:

```bash
cp .env.example .env
nano .env
```

Important production values:

```bash
ENVIRONMENT=production
APP_DEBUG=false
DATABASE_URL=mysql+asyncmy://storybook:strong_password@127.0.0.1:3306/storybook
BACKEND_CORS_ORIGINS=http://129.154.251.149
MEDIA_ROOT=/home/ubuntu/sb-backend/photo
AUDIO_ROOT=/home/ubuntu/sb-backend/audio
MEDIA_URL_PREFIX=/photo
AUDIO_URL_PREFIX=/audio
```

Create media directories and fix permissions:

```bash
mkdir -p /home/ubuntu/sb-backend/photo /home/ubuntu/sb-backend/audio
sudo chown -R ubuntu:ubuntu /home/ubuntu/sb-backend
chmod -R u+rwX /home/ubuntu/sb-backend/photo /home/ubuntu/sb-backend/audio
```

Create MySQL database:

```bash
sudo mysql
```

```sql
CREATE DATABASE storybook CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'storybook'@'localhost' IDENTIFIED BY 'strong_password';
GRANT ALL PRIVILEGES ON storybook.* TO 'storybook'@'localhost';
FLUSH PRIVILEGES;
EXIT;
```

Run migrations:

```bash
cd /home/ubuntu/sb-backend
source .venv/bin/activate
alembic upgrade head
```

## systemd Service

Create or edit:

```bash
sudo nano /etc/systemd/system/storybook-backend.service
```

Use:

```ini
[Unit]
Description=Storybook Backend API
After=network.target mysql.service

[Service]
User=ubuntu
Group=ubuntu
WorkingDirectory=/home/ubuntu/sb-backend
Environment="PATH=/home/ubuntu/sb-backend/.venv/bin"
ExecStart=/home/ubuntu/sb-backend/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Start the service:

```bash
sudo systemctl daemon-reload
sudo systemctl enable storybook-backend
sudo systemctl restart storybook-backend
sudo systemctl status storybook-backend
```

Watch logs:

```bash
sudo journalctl -u storybook-backend -f
```

The app is healthy when logs show:

```text
Application startup complete.
Uvicorn running on http://127.0.0.1:8000
```

## Nginx For UI And API

The desired Nginx behavior:

- UI served at `/`
- Backend API proxied at `/api/`
- Backend media proxied at `/photo/` and `/audio/`
- Health check proxied at `/health`

Check the active Nginx site:

```bash
ls -l /etc/nginx/sites-enabled/
sudo nginx -T | grep -E "server_name|proxy_pass|listen"
```

In the current server, the active file is:

```text
/etc/nginx/sites-available/myapp
```

Back it up:

```bash
sudo cp /etc/nginx/sites-available/myapp /etc/nginx/sites-available/myapp.backup
```

Edit it:

```bash
sudo nano /etc/nginx/sites-available/myapp
```

Use this if the UI is a static build copied to `/var/www/storybook-ui`:

```nginx
server {
    listen 80 default_server;
    listen [::]:80 default_server;

    server_name 129.154.251.149 _;

    client_max_body_size 50M;

    root /var/www/storybook-ui;
    index index.html;

    location /api/ {
        proxy_pass http://127.0.0.1:8000/api/;
        proxy_http_version 1.1;

        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location /photo/ {
        proxy_pass http://127.0.0.1:8000/photo/;
    }

    location /audio/ {
        proxy_pass http://127.0.0.1:8000/audio/;
    }

    location /health {
        proxy_pass http://127.0.0.1:8000/health;
        access_log off;
    }

    location / {
        try_files $uri $uri/ /index.html;
    }
}
```

Reload Nginx:

```bash
sudo nginx -t
sudo systemctl reload nginx
```

Test from the server:

```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1/health
curl http://129.154.251.149/health
```

Test from local machine:

```text
http://129.154.251.149/health
http://129.154.251.149/
```

If using Oracle Cloud, also allow ingress TCP `80` in the VCN security list or network security group.

## UI Deployment

Build the UI on the UI project:

```bash
npm install
npm run build
```

Copy the generated static files to the server. For Vite/React this is usually `dist/`:

```bash
sudo mkdir -p /var/www/storybook-ui
sudo rm -rf /var/www/storybook-ui/*
sudo cp -r dist/* /var/www/storybook-ui/
sudo chown -R www-data:www-data /var/www/storybook-ui
sudo systemctl reload nginx
```

Frontend API base URL should be:

```text
/api/v1
```

Do not use `/api/api/v1`.

## Backend Redeploy Steps

Use this every time backend code changes:

```bash
cd /home/ubuntu/sb-backend
git pull
source .venv/bin/activate
pip install -r requirements.txt
alembic upgrade head
sudo systemctl restart storybook-backend
sudo journalctl -u storybook-backend -n 50 --no-pager
```

Check health:

```bash
curl http://127.0.0.1:8000/health
curl http://129.154.251.149/health
```

## UI Redeploy Steps

Build the UI again, copy `dist` files, and reload Nginx:

```bash
npm run build
sudo rm -rf /var/www/storybook-ui/*
sudo cp -r dist/* /var/www/storybook-ui/
sudo chown -R www-data:www-data /var/www/storybook-ui
sudo nginx -t
sudo systemctl reload nginx
```

## Storage Configuration

The application uses configurable storage paths for images and audio files. This allows you to store files on any mounted volume or directory.

### Environment Variables

Configure these in your `.env` file:

```bash
# Image Storage
MEDIA_ROOT=/var/storybook/images
MEDIA_URL_PREFIX=/photo
IMAGE_STORAGE_PROVIDER=r2

# Cloudflare R2 image storage
CLOUDFLARE_R2_ACCOUNT_ID=
CLOUDFLARE_R2_ACCESS_KEY_ID=
CLOUDFLARE_R2_SECRET_ACCESS_KEY=
CLOUDFLARE_R2_BUCKET_NAME=
CLOUDFLARE_R2_PUBLIC_BASE_URL=
CLOUDFLARE_R2_IMAGE_KEY_PREFIX=photo
CLOUDFLARE_R2_REGION=auto
CLOUDFLARE_R2_CACHE_CONTROL=public, max-age=31536000, immutable

# Audio Storage
AUDIO_ROOT=/var/storybook/audio
AUDIO_URL_PREFIX=/audio
AUDIO_STORAGE_PROVIDER=local
CLOUDFLARE_R2_AUDIO_KEY_PREFIX=audio
```

Use absolute paths for production. Relative `MEDIA_ROOT` and `AUDIO_ROOT` values are resolved from the app working directory.

For Cloudflare R2 image storage, create an R2 bucket and S3 API token in Cloudflare, then set:

```bash
IMAGE_STORAGE_PROVIDER=r2
AUDIO_STORAGE_PROVIDER=r2
CLOUDFLARE_R2_ACCOUNT_ID=your_account_id
CLOUDFLARE_R2_ACCESS_KEY_ID=your_r2_access_key
CLOUDFLARE_R2_SECRET_ACCESS_KEY=your_r2_secret
CLOUDFLARE_R2_BUCKET_NAME=your_bucket
CLOUDFLARE_R2_PUBLIC_BASE_URL=https://media.yourdomain.com
CLOUDFLARE_R2_IMAGE_KEY_PREFIX=photo
CLOUDFLARE_R2_AUDIO_KEY_PREFIX=audio
```

R2 media keys keep the same logical structure as local storage, for example `photo/{parent_id}/{child_id}/profile.jpg`, `photo/stories/{story_id}/cover.png`, and `audio/stories/{story_id}/{language}/page_1.wav`.

### Linux VM Deployment Steps

#### 1. Create Storage Directories

```bash
# Create directories with appropriate permissions
sudo mkdir -p /var/storybook/images
sudo mkdir -p /var/storybook/audio

# Set ownership (replace 'storybook' with your app user)
sudo chown -R storybook:storybook /var/storybook

# Set permissions
sudo chmod -R 755 /var/storybook
```

#### 2. Configure Environment

```bash
# Copy example env file
cp .env.example .env

# Edit with your production settings
nano .env
```

Update these critical settings:
```bash
# Use stable absolute storage paths
MEDIA_ROOT=/var/storybook/images
AUDIO_ROOT=/var/storybook/audio

# Update database connection
DATABASE_URL=mysql+asyncmy://user:password@localhost:3306/storybook

# Set production environment
ENVIRONMENT=production
DEBUG=false

# Configure CORS for your domain
BACKEND_CORS_ORIGINS=https://yourdomain.com

# Update JWT secret (generate a secure random key)
JWT_SECRET_KEY=your_secure_random_key_here
```

#### 3. Install Dependencies

```bash
# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

#### 4. Run Database Migrations

```bash
# Run Alembic migrations
alembic upgrade head
```

#### 5. Start the Application

**Using Uvicorn directly:**
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4
```

**Using systemd service (recommended):**

Create `/etc/systemd/system/storybook-backend.service`:

```ini
[Unit]
Description=Storybook Backend API
After=network.target mysql.service

[Service]
Type=notify
User=storybook
Group=storybook
WorkingDirectory=/opt/storybook-backend
Environment="PATH=/opt/storybook-backend/.venv/bin"
ExecStart=/opt/storybook-backend/.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Enable and start the service:
```bash
sudo systemctl daemon-reload
sudo systemctl enable storybook-backend
sudo systemctl start storybook-backend
sudo systemctl status storybook-backend
```

#### 6. Configure Nginx (Reverse Proxy)

Create `/etc/nginx/sites-available/storybook-backend`:

```nginx
server {
    listen 80;
    server_name api.yourdomain.com;

    client_max_body_size 10M;

    # API endpoints
    location /api {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # Static files - Images
    location /photo {
        alias /var/storybook/images;
        expires 30d;
        add_header Cache-Control "public, immutable";
    }

    # Static files - Audio
    location /audio {
        alias /var/storybook/audio;
        expires 30d;
        add_header Cache-Control "public, immutable";
    }

    # Health check
    location /health {
        proxy_pass http://127.0.0.1:8000;
        access_log off;
    }
}
```

Enable the site:
```bash
sudo ln -s /etc/nginx/sites-available/storybook-backend /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

#### 7. Setup SSL (Let's Encrypt)

```bash
sudo apt install certbot python3-certbot-nginx
sudo certbot --nginx -d api.yourdomain.com
```

## Storage on Mounted Volumes

If you want to use a separate mounted volume for storage:

```bash
# Mount your volume (example: /dev/sdb1)
sudo mkdir -p /mnt/storage
sudo mount /dev/sdb1 /mnt/storage

# Create storage directories
sudo mkdir -p /mnt/storage/storybook/images
sudo mkdir -p /mnt/storage/storybook/audio
sudo chown -R storybook:storybook /mnt/storage/storybook

# Update .env
MEDIA_ROOT=/mnt/storage/storybook/images
AUDIO_ROOT=/mnt/storage/storybook/audio

# Make mount persistent (add to /etc/fstab)
echo "/dev/sdb1 /mnt/storage ext4 defaults 0 2" | sudo tee -a /etc/fstab
```

## Monitoring and Logs

```bash
# View application logs
sudo journalctl -u storybook-backend -f

# View Nginx logs
sudo tail -f /var/log/nginx/access.log
sudo tail -f /var/log/nginx/error.log

# Check disk usage
df -h /var/storybook
```

## Backup Strategy

```bash
# Backup storage directories
sudo tar -czf /backup/storybook-images-$(date +%Y%m%d).tar.gz /var/storybook/images
sudo tar -czf /backup/storybook-audio-$(date +%Y%m%d).tar.gz /var/storybook/audio

# Backup database
mysqldump -u root -p storybook > /backup/storybook-db-$(date +%Y%m%d).sql
```

## Troubleshooting

### Permission Issues
```bash
# Check directory ownership
ls -la /var/storybook

# Fix permissions if needed
sudo chown -R storybook:storybook /var/storybook
sudo chmod -R 755 /var/storybook
```

### Storage Full
```bash
# Check disk usage
df -h
du -sh /var/storybook/*

# Clean old files if needed (be careful!)
find /var/storybook/audio -type f -mtime +90 -delete
```

### Application Won't Start
```bash
# Check service status
sudo systemctl status storybook-backend

# Check logs
sudo journalctl -u storybook-backend --no-pager | tail -50

# Test configuration
source .venv/bin/activate
python -c "from app.core.config import settings; print(settings.media_root_path); print(settings.audio_root_path)"
```

## Security Checklist

- [ ] Storage directories have correct permissions (755 for directories, 644 for files)
- [ ] Application runs as non-root user
- [ ] `.env` file is not world-readable (`chmod 600 .env`)
- [ ] SSL/TLS is enabled
- [ ] Database credentials are secure
- [ ] JWT secret is randomly generated and secure
- [ ] CORS is configured for your domain only
- [ ] Rate limiting is enabled
- [ ] Firewall rules are configured (ufw or iptables)

## Performance Tuning

```bash
# Adjust worker count based on CPU cores
# Formula: (2 x CPU cores) + 1
uvicorn app.main:app --workers 8

# Enable database connection pooling (already configured)
# Check app/core/database.py for pool settings

# Monitor resource usage
htop
iotop
```
