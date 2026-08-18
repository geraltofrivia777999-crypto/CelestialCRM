#!/bin/sh
set -eu

# Backups contain credentials, finance data and private knowledge-base files.
umask 077

destination="${1:-./backups}"
mkdir -p "$destination"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
archive="$destination/celestial-$timestamp.sql.gz"
uploads_archive="$destination/celestial-$timestamp-uploads.tar.gz"

sql_tmp="$(mktemp "$destination/.celestial-$timestamp.sql.XXXXXX")"
archive_tmp="$(mktemp "$destination/.celestial-$timestamp.sql.gz.XXXXXX")"
uploads_tmp="$(mktemp "$destination/.celestial-$timestamp-uploads.tar.gz.XXXXXX")"
writers=""

running_services="$(docker compose ps --status running --services)"
for service in api worker scheduler; do
  if printf '%s\n' "$running_services" | grep -qx "$service"; then
    writers="$writers $service"
  fi
done

cleanup() {
  status=$?
  trap - EXIT HUP INT TERM
  rm -f "$sql_tmp" "$archive_tmp" "$uploads_tmp"
  if [ -n "$writers" ]; then
    # A failed backup must not leave the CRM offline.
    docker compose start $writers >/dev/null || true
  fi
  exit "$status"
}
trap cleanup EXIT HUP INT TERM

# Quiesce every process that writes the database or attachment volume. This
# makes the SQL dump and uploads archive one application-level snapshot.
if [ -n "$writers" ]; then
  docker compose stop $writers >/dev/null
fi

# Do not pipe pg_dump into gzip: POSIX sh reports only gzip's status and could
# otherwise turn a failed dump into a valid, empty .gz file.
docker compose exec -T postgres sh -c '
  exec pg_dump \
    --username="$POSTGRES_USER" \
    --dbname="$POSTGRES_DB" \
    --clean \
    --if-exists \
    --no-owner
' > "$sql_tmp"
test -s "$sql_tmp"
gzip -c "$sql_tmp" > "$archive_tmp"
gzip -t "$archive_tmp"

# Use a one-off API container because the normal API is deliberately stopped;
# it mounts the same persistent uploads_data volume.
docker compose run --rm --no-deps -T api sh -c '
  mkdir -p /app/uploads
  exec tar -C /app/uploads -czf - .
' > "$uploads_tmp"
tar -tzf "$uploads_tmp" >/dev/null

# Publish only fully validated files. A watcher can never pick up a half-written
# dump or attachment archive.
mv "$archive_tmp" "$archive"
mv "$uploads_tmp" "$uploads_archive"

echo "$archive"
echo "$uploads_archive"
