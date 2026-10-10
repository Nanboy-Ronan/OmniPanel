"""Static checks on deploy.sh ordering (the script itself only runs on the VM)."""


def test_deploy_takes_pre_migration_backup_before_alembic():
    """deploy.sh must dump the DB (and abort on failure) before migrating."""
    from pathlib import Path

    script = (Path(__file__).resolve().parents[1] / "deploy.sh").read_text()
    assert "set -Eeuo pipefail" in script and "trap rollback ERR" in script
    backup_at = script.index("-m app.db.backup backup pre-migrate")
    upgrade_at = script.index("alembic\" -c alembic.ini upgrade head")
    assert backup_at < upgrade_at
    # Captured via $(...) so a non-zero exit trips set -e / the ERR trap.
    assert 'PRE_MIGRATE_DUMP=$("$VENV/bin/python" -m app.db.backup backup pre-migrate' in script
