#!/usr/bin/env sh
set -eu

archive="${1:-}"
if [ -z "$archive" ] || [ ! -f "$archive" ]; then
  echo "Usage: CONFIRM_RESTORE=celestial ./infra/restore.sh backups/file.sql.gz" >&2
  exit 2
fi
if [ "${CONFIRM_RESTORE:-}" != "celestial" ]; then
  echo "Set CONFIRM_RESTORE=celestial to confirm database replacement." >&2
  exit 3
fi

gzip -t "$archive"
gzip -dc "$archive" | docker compose exec -T postgres psql \
  --username=celestial \
  --dbname=celestial \
  --set=ON_ERROR_STOP=on
