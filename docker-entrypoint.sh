#!/bin/sh
# Wait for the database, apply migrations, collect static files (for /admin/), then start the API.
set -e
until python -c "import os, psycopg; psycopg.connect(os.environ['DATABASE_URL']).close()" 2>/dev/null; do
  echo "Waiting for the database..."
  sleep 2
done
python manage.py migrate --noinput
python manage.py collectstatic --noinput >/dev/null
if [ "${LOAD_DEMO_DATA:-false}" = "true" ]; then
  python manage.py seed_demo || true
fi
exec "$@"
