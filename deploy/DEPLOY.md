# Deploy on a DigitalOcean droplet (Ubuntu 24.04, works on 512 MB RAM)

Setup: PostgreSQL, the API run by gunicorn (2 workers, kept running by systemd), and nginx in front on port 80.
Tested in production mode: the API uses about 180 MB.
Replace `139.59.3.23` with your droplet's IP and `StrongDbPass123` with your own password.

## 1. Connect (PowerShell on your PC)
```bash
ssh root@139.59.3.23
```

## 2. Swap memory (needed with 512 MB RAM)
```bash
fallocate -l 1G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
echo '/swapfile none swap sw 0 0' >> /etc/fstab
```

## 3. Software
```bash
apt update && apt upgrade -y
apt install -y python3-venv python3-pip nginx postgresql unzip
```

## 4. Database
```bash
sudo -u postgres psql -c "CREATE USER fleet WITH PASSWORD 'StrongDbPass123';"
sudo -u postgres psql -c "CREATE DATABASE fleet OWNER fleet;"
```

## 5. Upload and install
On your PC (second PowerShell window):

```bash
scp "C:\path\to\fleetbooks-backend.zip" root@139.59.3.23:/srv/
```

On the droplet:

```bash
cd /srv && unzip -o fleetbooks-backend.zip
adduser --system --group --no-create-home fleet
cd /srv/fleetbooks-backend
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## 6. Settings (.env)
```bash
SECRET=$(python3 -c "import secrets; print(secrets.token_urlsafe(50))")
cat > /srv/fleetbooks-backend/.env <<EOT
DJANGO_DEBUG=false
DJANGO_SECRET_KEY=$SECRET
DJANGO_ALLOWED_HOSTS=139.59.3.23,localhost,127.0.0.1
DJANGO_TIME_ZONE=Asia/Kolkata
DATABASE_URL=postgres://fleet:StrongDbPass123@localhost:5432/fleet
DJANGO_SECURE_COOKIES=false
DJANGO_NUM_PROXIES=1
EOT
chmod 600 .env
```

## 7. Database tables and first login
```bash
.venv/bin/python manage.py migrate
.venv/bin/python manage.py collectstatic --noinput
.venv/bin/python manage.py createsuperuser
chown -R fleet:fleet /srv/fleetbooks-backend
```

## 8. Run it (systemd + nginx + firewall)
```bash
cp deploy/fleetbooks.service /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now fleetbooks
cp deploy/nginx-fleetbooks.conf /etc/nginx/sites-available/fleetbooks
ln -sf /etc/nginx/sites-available/fleetbooks /etc/nginx/sites-enabled/fleetbooks
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl restart nginx
ufw allow OpenSSH && ufw allow 'Nginx Full' && ufw --force enable
```

Test: http://139.59.3.23/ shows {"name": "FleetBooks API", ...}. In the app, enter `http://139.59.3.23` (no :8000).

## 9. HTTPS (recommended), free, no domain needed
```bash
apt install -y certbot python3-certbot-nginx
sed -i 's/server_name 139.59.3.23;/server_name 139.59.3.23 139-59-3-23.sslip.io;/' /etc/nginx/sites-available/fleetbooks
systemctl reload nginx
certbot --nginx -d 139-59-3-23.sslip.io
sed -i 's/^DJANGO_ALLOWED_HOSTS=.*/DJANGO_ALLOWED_HOSTS=139.59.3.23,139-59-3-23.sslip.io,localhost,127.0.0.1/; s/^DJANGO_SECURE_COOKIES=.*/DJANGO_SECURE_COOKIES=true/' .env
systemctl restart fleetbooks
```

Then use `https://139-59-3-23.sslip.io` in the app. With your own domain, point it at the IP and use it instead.

## Updating, logs, backups
```bash
# update: upload the new zip, unzip -o in /srv (your .env and data are kept), then:
cd /srv/fleetbooks-backend && .venv/bin/pip install -r requirements.txt
.venv/bin/python manage.py migrate && .venv/bin/python manage.py collectstatic --noinput
chown -R fleet:fleet . && systemctl restart fleetbooks

journalctl -u fleetbooks -n 50 --no-pager                  # errors
sudo -u postgres pg_dump fleet > /root/fleet-$(date +%F).sql  # database backup (also copy media/)
```
