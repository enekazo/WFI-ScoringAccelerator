"""Read gold_receipts from a Fabric Lakehouse SQL analytics endpoint with Entra ID auth (no keys).

Plug the result into GoldReader:  GoldReader(sql_endpoint.fetcher(server, database)).
Needs the optional `pyodbc` package and Microsoft ODBC Driver 18 for SQL Server. Connection and
credential are injectable so the path is unit-tested without a network.
"""

import struct

SQL_SCOPE = "https://database.windows.net/.default"
SQL_COPT_SS_ACCESS_TOKEN = 1256  # pyodbc connection attribute for an Entra access token
DRIVER = "ODBC Driver 18 for SQL Server"
QUERY = ("SELECT entity_id, status, score, band, components, reasons, as_of, policy_sha256, "
         "input_sha256, receipt_sha256, scored_at FROM gold_receipts")


def token_struct(token: str) -> bytes:
    raw = token.encode("utf-16-le")
    return struct.pack(f"<I{len(raw)}s", len(raw), raw)


def _acquire_sql_token(credential=None) -> str:
    if credential is not None:
        return credential.get_token(SQL_SCOPE).token
    import os
    env_token = os.environ.get("FABRIC_SQL_ACCESS_TOKEN", "").strip()
    if env_token:
        return env_token
    from azure.identity import DefaultAzureCredential
    try:
        return DefaultAzureCredential(
            process_timeout=60,
            exclude_developer_cli_credential=True,
        ).get_token(SQL_SCOPE).token
    except Exception:
        import subprocess
        resource = SQL_SCOPE[:-len("/.default")] if SQL_SCOPE.endswith("/.default") else SQL_SCOPE
        token = subprocess.check_output(
            f"az account get-access-token --resource {resource} --query accessToken -o tsv",
            shell=True,
            text=True,
            timeout=60,
        ).strip()
        if token:
            return token
        raise


def connect(server: str, database: str, credential=None):
    import pyodbc
    token = _acquire_sql_token(credential)
    conn_str = (f"Driver={{{DRIVER}}};Server={server},1433;Database={database};"
                "Encrypt=yes;TrustServerCertificate=no")
    return pyodbc.connect(conn_str, attrs_before={SQL_COPT_SS_ACCESS_TOKEN: token_struct(token)})


def connect_mssql(server: str, database: str, credential=None):
    """Entra connection through the `mssql-python` package (bundles its driver; no ODBC install)."""
    import mssql_python
    token = _acquire_sql_token(credential)
    conn_str = f"Server={server};Database={database};Encrypt=yes"
    return mssql_python.connect(conn_str, attrs_before={SQL_COPT_SS_ACCESS_TOKEN: token_struct(token)})


def connect_auto(server: str, database: str, credential=None):
    """pyodbc + ODBC Driver 18 when installed, otherwise mssql-python. Both use Entra, no keys."""
    try:
        import pyodbc
        if DRIVER in pyodbc.drivers():
            return connect(server, database, credential)
    except ImportError:
        pass
    return connect_mssql(server, database, credential)


def fetcher(server: str, database: str, connect_fn=connect, credential=None, query: str = QUERY):
    """Return a callable yielding rows (default: gold_receipts) as dicts, for GoldReader."""
    def fetch():
        conn = connect_fn(server, database, credential) if credential else connect_fn(server, database)
        try:
            cursor = conn.cursor()
            cursor.execute(query)
            cols = [c[0] for c in cursor.description]
            return [dict(zip(cols, row)) for row in cursor.fetchall()]
        finally:
            conn.close()
    return fetch
