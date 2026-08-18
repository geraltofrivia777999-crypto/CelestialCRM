#!/bin/sh
set -eu

umask 077

database_only=0
if [ "${1:-}" = "--database-only" ]; then
  database_only=1
  shift
fi

archive="${1:-}"
uploads_archive="${2:-}"
if [ -z "$archive" ] || [ ! -f "$archive" ]; then
  echo "Usage: CONFIRM_RESTORE=celestial ./infra/restore.sh backups/file.sql.gz backups/file-uploads.tar.gz" >&2
  echo "   or: CONFIRM_RESTORE=celestial ./infra/restore.sh --database-only backups/file.sql.gz" >&2
  exit 2
fi
if [ "${CONFIRM_RESTORE:-}" != "celestial" ]; then
  echo "Restore refused. Set CONFIRM_RESTORE=celestial after verifying the target." >&2
  exit 3
fi
if [ "$database_only" -eq 0 ] && { [ -z "$uploads_archive" ] || [ ! -f "$uploads_archive" ]; }; then
  echo "The matching uploads archive is required. Use --database-only only for a dump made before Workspace attachments existed." >&2
  exit 4
fi

# Validate the complete pair before stopping services or touching live data.
gzip -t "$archive"
if [ "$database_only" -eq 0 ]; then
  tar -tzf "$uploads_archive" >/dev/null
  db_name="$(basename "$archive" .sql.gz)"
  uploads_name="$(basename "$uploads_archive")"
  if [ "$uploads_name" != "$db_name-uploads.tar.gz" ]; then
    echo "Database and uploads archives have different snapshot names." >&2
    exit 5
  fi
fi

sql_tmp="$(mktemp "${TMPDIR:-/tmp}/celestial-restore.XXXXXX.sql")"
restore_tmp="$(mktemp "${TMPDIR:-/tmp}/celestial-restore-transaction.XXXXXX.sql")"
stage_name=".celestial-restore-stage-$$"
stage_prepared=0
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
  rm -f "$sql_tmp" "$restore_tmp"
  if [ "$stage_prepared" -eq 1 ]; then
    docker compose run --rm --no-deps -T \
      -e CELESTIAL_RESTORE_STAGE="$stage_name" api sh -c '
        case "$CELESTIAL_RESTORE_STAGE" in
          .celestial-restore-stage-[0-9]*)
            rm -rf -- "/app/uploads/$CELESTIAL_RESTORE_STAGE"
            ;;
        esac
      ' >/dev/null 2>&1 || true
  fi
  if [ -n "$writers" ]; then
    docker compose start $writers >/dev/null || true
  fi
  exit "$status"
}
trap cleanup EXIT HUP INT TERM

gzip -dc "$archive" > "$sql_tmp"
test -s "$sql_tmp"

if [ -n "$writers" ]; then
  docker compose stop $writers >/dev/null
fi

if [ "$database_only" -eq 0 ]; then
  # Extract to a hidden staging directory first. A broken archive therefore
  # cannot overwrite the currently active uploads volume.
  docker compose run --rm --no-deps -T \
    -e CELESTIAL_RESTORE_STAGE="$stage_name" api sh -c '
      case "$CELESTIAL_RESTORE_STAGE" in
        .celestial-restore-stage-[0-9]*) ;;
        *) exit 64 ;;
      esac
      stage="/app/uploads/$CELESTIAL_RESTORE_STAGE"
      rm -rf -- "$stage"
      mkdir -p "$stage"
      exec tar -xzf - -C "$stage"
    ' < "$uploads_archive"
  stage_prepared=1
fi

# A plain --clean dump knows nothing about tables added after it was made.
# Recreate public and restore inside one PostgreSQL transaction: any SQL error
# rolls the DROP back and leaves the original database intact.
{
  printf '%s\n' 'DROP SCHEMA public CASCADE;' 'CREATE SCHEMA public;'
  cat "$sql_tmp"
} > "$restore_tmp"

docker compose exec -T postgres sh -c '
  exec psql \
    --username="$POSTGRES_USER" \
    --dbname="$POSTGRES_DB" \
    --single-transaction \
    --set=ON_ERROR_STOP=on
' < "$restore_tmp"

if [ "$database_only" -eq 0 ]; then
  # Exact restore: remove files absent from the snapshot, then publish the
  # already validated staging tree. Targets are constrained to /app/uploads.
  docker compose run --rm --no-deps -T \
    -e CELESTIAL_RESTORE_STAGE="$stage_name" api sh -c '
      case "$CELESTIAL_RESTORE_STAGE" in
        .celestial-restore-stage-[0-9]*) ;;
        *) exit 64 ;;
      esac
      root=/app/uploads
      stage="$root/$CELESTIAL_RESTORE_STAGE"
      test -d "$stage"
      find "$root" -mindepth 1 -maxdepth 1 \
        ! -name "$CELESTIAL_RESTORE_STAGE" -exec rm -rf -- {} +
      find "$stage" -mindepth 1 -maxdepth 1 -exec mv -- {} "$root"/ \;
      rmdir "$stage"
    '
  stage_prepared=0
fi

echo "Restore completed. Database: $archive"
if [ "$database_only" -eq 0 ]; then
  echo "Uploads: $uploads_archive"
fi
