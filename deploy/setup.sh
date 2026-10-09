#!/bin/bash
# FleetBooks API: one-time setup on a fresh Ubuntu droplet. Run as root.
set -e
IP=139.59.3.23
REPO=https://github.com/abdulrahoofpc/bus-api.git
DIR=/srv/fleetbooks-backend
DB_PASS=$(python3 -c "import secrets; print(secrets.token_urlsafe(18))")

echo "== 1/7 swap memory"
if ! swapon --show | grep -q /swapfile; then
  fallocate -l 1G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
  grep -q '/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

echo "== 2/7 software"
apt-get update -y
DEBIAN_FRONTEND=noninteractive apt-get install -y git python3-venv python3-pip nginx postgresql

echo "== 3/7 code"
id fleet >/dev/null 2>&1 || adduser --system --group --no-create-home fleet
git config --global --add safe.directory "$DIR"
if [ -d "$DIR/.git" ]; then git -C "$DIR" pull; else git clone "$REPO" "$DIR"; fi
cd "$DIR"
python3 -m venv .venv
.venv/bin/pip install -q -r requirements.txt

echo "== 4/7 database"
if ! sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='fleet'" | grep -q 1; then
  sudo -u postgres psql -c "CREATE USER fleet WITH PASSWORD '$DB_PASS';"
  sudo -u postgres psql -c "CREATE DATABASE fleet OWNER fleet;"
  NEW_DB=1
fi

echo "== 5/7 settings (.env)"
if [ ! -f .env ]; then
  [ -n "$NEW_DB" ] || { echo "Database user exists but .env is missing: put DATABASE_URL in $DIR/.env yourself"; exit 1; }
  cat > .env <<EOF
DJANGO_DEBUG=false
DJANGO_SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_urlsafe(50))")
DJANGO_ALLOWED_HOSTS=$IP,localhost,127.0.0.1
DJANGO_TIME_ZONE=Asia/Kolkata
DATABASE_URL=postgres://fleet:$DB_PASS@localhost:5432/fleet
DJANGO_SECURE_COOKIES=false
DJANGO_NUM_PROXIES=1
EOF
  chmod 600 .env
fi
.venv/bin/python manage.py migrate --noinput
.venv/bin/python manage.py collectstatic --noinput >/dev/null
chown -R fleet:fleet "$DIR"

echo "== 6/7 API service + nginx"
cp deploy/fleetbooks.service /etc/systemd/system/fleetbooks.service
sed "s/server_name .*;/server_name $IP;/" deploy/nginx-fleetbooks.conf > /etc/nginx/sites-available/fleetbooks
ln -sf /etc/nginx/sites-available/fleetbooks /etc/nginx/sites-enabled/fleetbooks
rm -f /etc/nginx/sites-enabled/default
systemctl daemon-reload
systemctl enable fleetbooks >/dev/null 2>&1
systemctl restart fleetbooks
nginx -t && systemctl restart nginx

echo "== 7/7 firewall"
ufw allow OpenSSH >/dev/null && ufw allow 'Nginx Full' >/dev/null && ufw --force enable

sleep 3
if curl -fs http://127.0.0.1/ | grep -q "FleetBooks API"; then
  echo ""
  echo "DONE. The API is running at http://$IP/   (docs: http://$IP/api/docs/)"
  echo "Next: create your admin login with"
  echo "  cd $DIR && sudo -u fleet .venv/bin/python manage.py createsuperuser"
else
  echo "The API didn't answer. Last log lines:"; journalctl -u fleetbooks -n 30 --no-pager
fi
