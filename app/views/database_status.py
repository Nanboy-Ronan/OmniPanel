"""Read-only database inventory; status is derived from the live schema."""

from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import func, inspect, literal, select, union_all

from ..config import settings
from ..db import Base


def inspect_schema(connection):
    inspector = inspect(connection)
    tables = inspector.get_table_names()
    columns = inspector.get_multi_columns(filter_names=tables)
    return tables, {
        name: {column["name"] for column in definitions}
        for (_, name), definitions in columns.items()
    }


def backup_inventory():
    """Report file metadata only; this is not a restore verification."""
    root = Path(settings.backup_dir)
    try:
        files = []
        if root.exists():
            for path in root.iterdir():
                if (
                    path.is_file()
                    and not path.name.startswith(".")
                    and path.suffix in {".sql", ".gz", ".dump", ".db"}
                ):
                    stat = path.stat()
                    files.append(
                        {
                            "name": path.name,
                            "bytes": stat.st_size,
                            "modified_at": datetime.fromtimestamp(
                                stat.st_mtime, timezone.utc
                            ).isoformat(),
                        }
                    )
        files.sort(key=lambda item: item["modified_at"], reverse=True)
        return {"status": "found" if files else "none", "files": files[:5]}
    except OSError:
        return {"status": "unavailable", "files": []}


async def inspect_database(session):
    connection = await session.connection()
    actual, columns = await connection.run_sync(inspect_schema)
    expected = Base.metadata.tables
    present = [table for name, table in expected.items() if name in actual]
    counts = {}
    if present:
        counts = dict(
            (
                await session.execute(
                    union_all(
                        *[
                            select(
                                literal(table.name).label("name"),
                                func.count().label("rows"),
                            ).select_from(table)
                            for table in present
                        ]
                    )
                )
            ).all()
        )
    details = [
        {
            "name": name,
            "present": name in actual,
            "rows": counts.get(name),
            "missing_columns": (
                sorted({c.name for c in table.columns} - columns.get(name, set()))
                if name in actual
                else []
            ),
        }
        for name, table in sorted(expected.items())
    ]
    missing = [item["name"] for item in details if not item["present"]]
    missing_columns = [
        f"{item['name']}.{column}"
        for item in details
        for column in item["missing_columns"]
    ]
    analysis_tables = {
        "orders",
        "customers",
        "youzan_orders",
        "jd_orders",
        "tmall_orders",
    }
    analysis_missing = sorted(analysis_tables - set(actual))
    analysis_columns = [
        column for column in missing_columns if column.split(".")[0] in analysis_tables
    ]
    return {
        "database_path": "PostgreSQL",
        "database_exists": True,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "health": "attention" if missing or missing_columns else "healthy",
        "tables": actual,
        "table_details": details,
        "missing_tables": missing,
        "missing_columns": missing_columns,
        "counts": counts,
        "analysis_ready": not analysis_missing
        and not analysis_columns
        and counts.get("orders", 0) > 0,
        "missing_analysis_tables": analysis_missing,
        "missing_analysis_columns": analysis_columns,
        "missing_analysis_mappings": [],
        "all_orders_count": counts.get("orders"),
    }
