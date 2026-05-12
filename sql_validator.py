"""AST-based SQL validator built on sqlglot.

Replaces the regex/keyword approach of `database_mcp_server.validate_select_query`
with a real PostgreSQL parser. This is more robust against:

- string literals containing forbidden keywords
  ('SELECT bio FROM users WHERE bio LIKE %insert%' no longer false-positives)
- comments embedded inside the SQL
- CTEs (their names are correctly excluded from the table set)
- column names like `delete_at` or `update_time`

Two functions are exposed:

    validate(query) -> ValidationResult
    extract_tables(query) -> set[str]

Both raise nothing; any parse failure is surfaced as a non-valid result.
"""

from __future__ import annotations

from dataclasses import dataclass

import sqlglot
from sqlglot import exp


WRITE_EXPRESSIONS: tuple[type[exp.Expression], ...] = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Drop,
    exp.Create,
    exp.Alter,
    exp.TruncateTable,
    exp.Merge,
)


@dataclass(frozen=True)
class ValidationResult:
    is_valid: bool
    message: str
    normalized_query: str = ""


def _has_locking_clause(stmt: exp.Expression) -> bool:
    return any(stmt.find_all(exp.Lock))


def _has_write_node(stmt: exp.Expression) -> bool:
    return any(isinstance(node, WRITE_EXPRESSIONS) for node in stmt.walk())


def _strip_trailing_semicolon(query: str) -> str:
    return query.strip().rstrip(";").strip()


def validate(query: str) -> ValidationResult:
    raw = query.strip()
    if not raw:
        return ValidationResult(False, "Query is empty.")
    if len(raw) > 5000:
        return ValidationResult(False, "Query is too long.")

    try:
        statements = sqlglot.parse(raw, read="postgres")
    except sqlglot.errors.ParseError as exc:
        return ValidationResult(False, f"SQL parse error: {exc}")

    statements = [s for s in statements if s is not None]
    if not statements:
        return ValidationResult(False, "Query did not parse into any statement.")
    if len(statements) > 1:
        return ValidationResult(False, "Only one SQL statement is allowed.")

    stmt = statements[0]

    if not isinstance(stmt, (exp.Select, exp.Subquery, exp.Union)):
        return ValidationResult(
            False,
            f"Only SELECT queries are allowed, got: {type(stmt).__name__}",
        )

    if _has_write_node(stmt):
        return ValidationResult(False, "Write operations are not allowed.")

    if _has_locking_clause(stmt):
        return ValidationResult(False, "SELECT locking clauses are not allowed.")

    normalized = _strip_trailing_semicolon(stmt.sql(dialect="postgres"))
    return ValidationResult(True, "Query is safe to execute.", normalized)


def extract_tables(query: str) -> set[str]:
    """Return the set of base table names referenced by FROM/JOIN.

    CTE names are excluded, so a query like
        WITH t AS (SELECT * FROM artist) SELECT * FROM t
    returns {"artist"} rather than {"artist", "t"}.
    """
    try:
        statements = sqlglot.parse(query, read="postgres")
    except sqlglot.errors.ParseError:
        return set()

    tables: set[str] = set()
    for stmt in statements:
        if stmt is None:
            continue
        cte_names = {cte.alias_or_name.lower() for cte in stmt.find_all(exp.CTE)}
        for table in stmt.find_all(exp.Table):
            name = table.name.lower()
            if name and name not in cte_names:
                tables.add(name)
    return tables


if __name__ == "__main__":
    # Quick smoke test
    cases = [
        ("SELECT COUNT(*) FROM artist", True),
        ("DROP TABLE artist", False),
        ("SELECT * FROM artist; DROP TABLE album", False),
        ("SELECT bio FROM users WHERE bio LIKE '%insert%'", True),
        ("WITH t AS (SELECT * FROM artist) SELECT * FROM t", True),
        ("SELECT delete_at FROM customer", True),
        ("SELECT * FROM artist FOR UPDATE", False),
        ("INSERT INTO artist VALUES (1, 'x')", False),
        ("", False),
    ]
    for sql_text, expected in cases:
        v = validate(sql_text)
        status = "OK" if v.is_valid == expected else "FAIL"
        print(f"{status}  is_valid={v.is_valid} expected={expected}  | {sql_text[:60]}")
        if v.is_valid:
            tables = extract_tables(sql_text)
            print(f"      tables={sorted(tables)}")
