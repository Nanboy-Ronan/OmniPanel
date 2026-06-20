"""SQL validation helpers for the SQL Query Console.

Two public functions are exported:

    validate_sql_query(sql)         – raises ValueError on any unsafe query.
    enforce_limit(sql, max_limit)   – ensures a LIMIT clause ≤ max_limit exists.

Security model
──────────────
Four layered guards in order:

  1. No semicolons — prevents the most obvious multi-statement injection.
  2. Must start with SELECT or WITH — blocks plain DML/DDL at the top level.
  3. No DML/DDL keywords anywhere after stripping string literals and comments
     — catches writable CTEs:
         WITH bad AS (UPDATE orders SET price=0 RETURNING id) SELECT id FROM bad
     String literals are stripped first so a filter like
         WHERE action = 'update_role'
     does NOT false-positive.
  4. No SELECT ... INTO — blocks the PostgreSQL syntax that writes a new table.
"""
from __future__ import annotations

import re

# ── compiled patterns ──────────────────────────────────────────────────────────

_SEMICOLON_RE = re.compile(r";")

_ALLOWED_START_RE = re.compile(r"^\s*(with|select)\b", re.IGNORECASE)

_LIMIT_RE = re.compile(r"\bLIMIT\s+(\d+)\b", re.IGNORECASE)

# Strip '' string literals (handles '' escapes inside strings).
_STRING_LITERAL_RE = re.compile(r"'(?:[^']|'')*'")

# Strip single-line and block comments.
_INLINE_COMMENT_RE = re.compile(r"--[^\n]*")
_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)

# DML / DDL keywords that are forbidden anywhere in the query — even inside CTE
# bodies.  Scanned on the comment- and literal-stripped form of the SQL.
_FORBIDDEN_KEYWORDS_RE = re.compile(
    r"\b(INSERT|UPDATE|DELETE|TRUNCATE|DROP|CREATE|ALTER|REPLACE|MERGE"
    r"|CALL|EXECUTE|EXEC|GRANT|REVOKE|COPY|VACUUM|ANALYZE)\b",
    re.IGNORECASE,
)

# SELECT ... INTO tablename — PostgreSQL syntax that writes a new table.
_SELECT_INTO_RE = re.compile(r"\bSELECT\b.+?\bINTO\b", re.IGNORECASE | re.DOTALL)

MAX_LIMIT: int = 5000


# ── helpers ────────────────────────────────────────────────────────────────────

def _sanitise_for_scanning(sql: str) -> str:
    """Strip comments and string literals to avoid false-positive keyword hits."""
    s = _BLOCK_COMMENT_RE.sub(" ", sql)
    s = _INLINE_COMMENT_RE.sub(" ", s)
    s = _STRING_LITERAL_RE.sub("''", s)
    return s


# ── public API ─────────────────────────────────────────────────────────────────

def validate_sql_query(sql: str) -> None:
    """Validate that *sql* is a single, safe SELECT (or CTE) statement.

    Raises:
        ValueError: with a human-readable message safe to forward to the client.
    """
    if not sql or not sql.strip():
        raise ValueError("SQL query cannot be empty or blank.")

    # Guard 1: no semicolons (multi-statement prevention).
    if _SEMICOLON_RE.search(sql):
        raise ValueError(
            "Multi-statement queries are not allowed. "
            "Remove all semicolons and submit a single SELECT statement."
        )

    # Guard 2: must start with SELECT or WITH.
    if not _ALLOWED_START_RE.match(sql):
        raise ValueError(
            "Only SELECT statements are allowed. "
            "DML and DDL queries (INSERT, UPDATE, DELETE, DROP, ALTER, …) "
            "are not permitted."
        )

    # Strip string literals and comments before deeper scanning so that a
    # filter value like WHERE action = 'update_role' does not false-positive.
    clean = _sanitise_for_scanning(sql)

    # Guard 3: no DML/DDL anywhere — catches writable CTEs such as:
    #   WITH bad AS (UPDATE orders SET price = 0 RETURNING id) SELECT id FROM bad
    m = _FORBIDDEN_KEYWORDS_RE.search(clean)
    if m:
        raise ValueError(
            f"Forbidden keyword '{m.group(1).upper()}' detected. "
            "Only read-only SELECT queries are permitted — data modification "
            "and DDL statements are not allowed, even inside CTE bodies."
        )

    # Guard 4: SELECT ... INTO creates a new PostgreSQL table (write operation).
    if _SELECT_INTO_RE.search(clean):
        raise ValueError(
            "SELECT INTO is not permitted — it writes data to a new table. "
            "Use a plain SELECT to read data."
        )


def enforce_limit(sql: str, max_limit: int = MAX_LIMIT) -> str:
    """Ensure *sql* has a LIMIT clause that does not exceed *max_limit*.

    - LIMIT absent       → append ``LIMIT {max_limit}`` and return.
    - LIMIT ≤ max_limit  → return *sql* unchanged.
    - LIMIT > max_limit  → raise ``ValueError`` (user must edit their query).
    """
    m = _LIMIT_RE.search(sql)

    if m is None:
        return f"{sql.rstrip()} LIMIT {max_limit}"

    limit_value = int(m.group(1))
    if limit_value > max_limit:
        raise ValueError(
            f"LIMIT {limit_value} exceeds the maximum allowed value of "
            f"{max_limit}. Use LIMIT ≤ {max_limit}."
        )

    return sql
