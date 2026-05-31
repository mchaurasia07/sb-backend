# Deployment Guide - Storybook Backend

This guide covers deploying the Storybook backend application on a Linux VM.

## Storage Configuration

The application uses configurable storage paths for images and audio files. This allows you to store files on any mounted volume or directory.

### Environment Variables

Configure these in your `.env` file:

```bash
# Storage base for relative roots
STORAGE_BASE_PATH=/var/storybook

# Image Storage
MEDIA_ROOT=images
MEDIA_URL_PREFIX=/photo

# Audio Storage
AUDIO_ROOT=audio
AUDIO_URL_PREFIX=/audio
```

You can also set `MEDIA_ROOT` and `AUDIO_ROOT` to absolute paths. Absolute paths are used directly; relative paths are resolved under `STORAGE_BASE_PATH`.

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
# Use a stable storage base path
STORAGE_BASE_PATH=/var/storybook
MEDIA_ROOT=images
AUDIO_ROOT=audio

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
