#!/usr/bin/env bash
# Restore drill: load the newest database dump into a throwaway database,
# print row counts of key tables, then drop it. Never touches the live DB.
#
#   sudo /opt/rpa/current/scripts/restore_drill.sh                 # newest dump in BACKUP_DIR
#   sudo /opt/rpa/current/scripts/restore_drill.sh --dump FILE.sql # a specific dump
#   sudo /opt/rpa/current/scripts/restore_drill.sh --backup-dir /var/backups/rpa-deploy/pre-migrate
#
# Uses RAP_MIGRATION_DATABASE_URL (the owner role, needs CREATEDB) from
# /etc/rpa/rpa-migration.env when present, else RAP_DATABASE_URL. See
# docs/maintenance.md. Exit code 0 = restore succeeded.
set -Eeuo pipefail
APP_DIR=${APP_DIR:-/opt/rpa/current}
VENV=${VENV:-/opt/rpa/venv}
RUNTIME_ENV=${RUNTIME_ENV:-/etc/rpa/rpa.env}
MIGRATION_ENV=${MIGRATION_ENV:-/etc/rpa/rpa-migration.env}

# rpa.env is a systemd EnvironmentFile (not necessarily shell syntax), so only
# the two values needed here are read from it, without sourcing the file.
read_env_value() {
    local name=$1 file=$2
    [[ -f "$file" ]] || return 0
    sed -n "s/^${name}=//p" "$file" | tail -1 | sed -e 's/^"\(.*\)"$/\1/' -e "s/^'\(.*\)'$/\1/"
}
if [[ -z ${BACKUP_DIR:-} ]]; then
    BACKUP_DIR=$(read_env_value BACKUP_DIR "$RUNTIME_ENV")
    if [[ -n "$BACKUP_DIR" ]]; then export BACKUP_DIR; fi
fi
if [[ -z ${RAP_DATABASE_URL:-} ]]; then
    RAP_DATABASE_URL=$(read_env_value RAP_DATABASE_URL "$RUNTIME_ENV")
    if [[ -n "$RAP_DATABASE_URL" ]]; then export RAP_DATABASE_URL; fi
fi
# The migration env is shell syntax (deploy.sh sources it the same way).
if [[ -f "$MIGRATION_ENV" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "$MIGRATION_ENV"
    set +a
fi

cd "$APP_DIR"
exec "$VENV/bin/python" -m app.db.backup restore-drill "$@"
