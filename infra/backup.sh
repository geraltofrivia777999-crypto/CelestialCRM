#!/usr/bin/env sh
set -eu

destination="${1:-./backups}"
mkdir -p "$destination"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
archive="$destination/celestial-$timestamp.sql.gz"

docker compose exec -T postgres pg_dump \
  --username=celestial \
  --dbname=celestial \
  --clean \
  --if-exists \
  --no-owner | gzip > "$archive"

gzip -t "$archive"
echo "$archive"
