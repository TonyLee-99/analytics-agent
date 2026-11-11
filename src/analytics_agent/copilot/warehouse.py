"""Postgres warehouse access under the asking user's own role, with row-level security by department.

Roles
- wh_owner        NOLOGIN, owns every wh.* / acl.* object (owners bypass RLS, so nobody queries as it)
- copilot_users   NOLOGIN group: USAGE + SELECT on the four allowlisted relations and acl.role_departments
- cu_<user>       NOLOGIN, one per directory user, member of copilot_users
- copilot_gateway LOGIN, NOINHERIT, no privileges of its own; member of every cu_<user> WITH INHERIT FALSE,
                  SET TRUE, so all it can do is `SET LOCAL ROLE cu_<user>` inside a transaction

Policy on wh.service_request (one permissive policy, SELECT only):
    dept_code IN (rows of acl.role_departments where role_name = current_user)   -- synced entitlements
    AND dept_code = ANY(app.live_depts)                                          -- live entitlements
acl.role_departments has its own RLS (role_name = current_user): a user role reads only its own mapping.
An unset app.live_depts gives no rows (default deny).

Every query: BEGIN READ ONLY; SET LOCAL app.live_depts; SET LOCAL statement_timeout; SET LOCAL ROLE;
one validated statement over the extended protocol (binary=True; the server rejects a second statement);
ROLLBACK. SET LOCAL settings and the role revert at ROLLBACK, also after errors.
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any

import psycopg
import sqlglot
from psycopg import sql as psql
from sqlglot import exp

from analytics_agent.copilot.identity import DirectoryReplica, DirState
from analytics_agent.copilot.settings import PG_ADMIN_DSN, PG_GATEWAY_DSN
from analytics_agent.copilot.sqlguard import ALLOWED_RELATIONS

ROLE_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
POLICY_VERSION = "rls-v1"

SECURITY_SQL = """
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'wh_owner') THEN CREATE ROLE wh_owner NOLOGIN; END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'copilot_users') THEN CREATE ROLE copilot_users NOLOGIN; END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'copilot_gateway') THEN
    CREATE ROLE copilot_gateway LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS NOREPLICATION;
  END IF;
END $$;
ALTER ROLE copilot_gateway NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS NOREPLICATION;
ALTER ROLE copilot_gateway SET statement_timeout = '20s';
REVOKE ALL ON SCHEMA public FROM PUBLIC;
-- set_config() could rewrite app.live_depts or the role from inside a SELECT; SET statements are not
-- function calls, so the gateway's own SET LOCAL still works after this.
REVOKE EXECUTE ON FUNCTION pg_catalog.set_config(text, text, boolean) FROM PUBLIC;

CREATE TABLE IF NOT EXISTS acl.role_departments (
    role_name  name NOT NULL,
    dept_code  text NOT NULL,
    PRIMARY KEY (role_name, dept_code)
);
CREATE TABLE IF NOT EXISTS acl.sync_state (
    id int PRIMARY KEY DEFAULT 1, directory_version int NOT NULL, policy_version text NOT NULL, synced_at timestamptz
);
ALTER SCHEMA wh OWNER TO wh_owner;
ALTER SCHEMA acl OWNER TO wh_owner;
ALTER TABLE wh.department OWNER TO wh_owner;
ALTER TABLE wh.community_area OWNER TO wh_owner;
ALTER TABLE wh.request_benchmark OWNER TO wh_owner;
ALTER TABLE wh.service_request OWNER TO wh_owner;
ALTER TABLE acl.role_departments OWNER TO wh_owner;
ALTER TABLE acl.sync_state OWNER TO wh_owner;

REVOKE ALL ON ALL TABLES IN SCHEMA wh FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA acl FROM PUBLIC;
GRANT USAGE ON SCHEMA wh, acl TO copilot_users;
GRANT SELECT ON wh.service_request, wh.department, wh.community_area, wh.request_benchmark TO copilot_users;
GRANT SELECT ON acl.role_departments TO copilot_users;

ALTER TABLE acl.role_departments ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS own_mapping ON acl.role_departments;
CREATE POLICY own_mapping ON acl.role_departments FOR SELECT TO copilot_users USING (role_name = current_user);

ALTER TABLE wh.service_request ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS dept_read ON wh.service_request;
CREATE POLICY dept_read ON wh.service_request FOR SELECT TO copilot_users USING (
    dept_code IN (SELECT r.dept_code FROM acl.role_departments r WHERE r.role_name = current_user)
    AND dept_code = ANY (string_to_array(current_setting('app.live_depts', true), ','))
);
"""


def role_name(user_id: str) -> str:
    r = f"cu_{user_id}"
    if not ROLE_RE.match(r):
        raise ValueError(f"directory id {user_id!r} cannot be mapped to a database role")
    return r


def setup_security(admin_dsn: str = PG_ADMIN_DSN) -> None:
    with psycopg.connect(admin_dsn, autocommit=True) as c:
        c.execute(SECURITY_SQL)


def sync_roles(state: DirState, admin_dsn: str = PG_ADMIN_DSN) -> int:
    """Make acl.role_departments match `state` (a directory replica). Returns rows written."""
    n = 0
    with psycopg.connect(admin_dsn) as c:
        existing = {r[0] for r in c.execute("SELECT rolname FROM pg_roles WHERE rolname LIKE 'cu\\_%'")}
        for uid in sorted(state.users):
            r = role_name(uid)
            if r not in existing:
                c.execute(psql.SQL("CREATE ROLE {} NOLOGIN").format(psql.Identifier(r)))
            c.execute(psql.SQL("GRANT copilot_users TO {}").format(psql.Identifier(r)))
            c.execute(psql.SQL("GRANT {} TO copilot_gateway WITH INHERIT FALSE, SET TRUE").format(psql.Identifier(r)))
            c.execute("DELETE FROM acl.role_departments WHERE role_name = %s", (r,))
            depts = sorted(state.data_departments(uid))
            if depts:
                c.cursor().executemany("INSERT INTO acl.role_departments VALUES (%s, %s)", [(r, d) for d in depts])
                n += len(depts)
        c.execute("INSERT INTO acl.sync_state VALUES (1, %s, %s, now()) ON CONFLICT (id) DO UPDATE "
                  "SET directory_version = EXCLUDED.directory_version, policy_version = EXCLUDED.policy_version, "
                  "synced_at = now()", (state.version, POLICY_VERSION))
    return n


class WarehouseSync:
    """Lagged directory -> Postgres role-table sync (the SQL side's own replica)."""

    def __init__(self, replica: DirectoryReplica, admin_dsn: str = PG_ADMIN_DSN):
        self.replica = replica
        self.admin_dsn = admin_dsn
        self.synced_version = -1

    def sync(self, now: float | None = None) -> bool:
        self.replica.sync(now)
        if self.replica.state.version != self.synced_version:
            sync_roles(self.replica.state, self.admin_dsn)
            self.synced_version = self.replica.state.version
            return True
        return False


@dataclass
class QueryResult:
    ok: bool
    columns: list[str] = field(default_factory=list)
    rows: list[tuple] = field(default_factory=list)
    truncated: bool = False
    error: str = ""
    elapsed_ms: float = 0.0
    role: str = ""
    live_depts: tuple[str, ...] = ()


class Warehouse:
    """Gateway connection; executes one statement as `cu_<user>` with the user's live departments."""

    def __init__(self, dsn: str = PG_GATEWAY_DSN, row_limit: int = 1000, timeout_ms: int = 15000):
        self.dsn = dsn
        self.row_limit = row_limit
        self.timeout_ms = timeout_ms
        self._local = threading.local()

    def _conn(self) -> psycopg.Connection:
        c = getattr(self._local, "conn", None)
        if c is None or c.closed:
            c = psycopg.connect(self.dsn, autocommit=True)
            self._local.conn = c
        return c

    def query(self, user_id: str, live_depts: frozenset[str] | set[str], sql_text: str) -> QueryResult:
        role = role_name(user_id)
        depts = tuple(sorted(live_depts))
        for d in depts:  # dept codes come from config; refuse anything that could smuggle a separator
            if not re.match(r"^[A-Z0-9]{2,6}$", d):
                raise ValueError(f"bad department code {d!r}")
        c = self._conn()
        t0 = time.time()
        res = QueryResult(False, role=role, live_depts=depts)
        try:
            c.execute("BEGIN READ ONLY")
            c.execute(psql.SQL("SET LOCAL app.live_depts = {}").format(psql.Literal(",".join(depts))))
            c.execute(psql.SQL("SET LOCAL statement_timeout = {}").format(psql.Literal(f"{self.timeout_ms}ms")))
            c.execute(psql.SQL("SET LOCAL ROLE {}").format(psql.Identifier(role)))
            # binary=True forces the extended protocol, which rejects a second statement in the string.
            # (Plain execute() without parameters uses the simple protocol and runs every statement.)
            cur = c.execute(sql_text, binary=True)
            res.columns = [d.name for d in (cur.description or [])]
            rows = cur.fetchmany(self.row_limit + 1)
            res.truncated = len(rows) > self.row_limit
            res.rows = [tuple(r) for r in rows[: self.row_limit]]
            res.ok = True
        except psycopg.Error as e:
            res.error = f"{type(e).__name__}: {str(e).splitlines()[0][:300]}"
        finally:
            try:
                c.execute("ROLLBACK")
            except psycopg.Error:
                c.close()
        res.elapsed_ms = round((time.time() - t0) * 1000, 1)
        return res


# ---------------------------------------------------------------- independent oracle
def restrict_sql(sql_text: str, depts: frozenset[str] | set[str]) -> str:
    """Rewrite every reference to wh.service_request as a subquery with an explicit department filter.

    Used by the evaluation oracle, which runs as the owner/superuser (RLS bypassed): expected visibility
    then comes from the policy model, not from the RLS machinery under test."""
    tree = sqlglot.parse_one(sql_text, read="postgres")
    in_list = ", ".join("'" + d.replace("'", "''") + "'" for d in sorted(depts)) or "NULL"
    for t in list(tree.find_all(exp.Table)):
        if t.name == "service_request" and (t.db or "wh") == "wh":
            alias = t.alias or "service_request"
            sub = sqlglot.parse_one(f"(SELECT * FROM wh.service_request WHERE dept_code IN ({in_list})) AS {alias}",
                                    read="postgres")
            t.replace(sub)
    return tree.sql(dialect="postgres")


def oracle_query(sql_text: str, depts: frozenset[str] | set[str] | None, admin_dsn: str = PG_ADMIN_DSN,
                 row_limit: int = 1000) -> QueryResult:
    """Run as admin; `depts=None` means unrestricted (what a superuser would see)."""
    q = sql_text if depts is None else restrict_sql(sql_text, depts)
    res = QueryResult(False, role="oracle")
    t0 = time.time()
    with psycopg.connect(admin_dsn, autocommit=True) as c:
        try:
            c.execute("BEGIN READ ONLY")
            cur = c.execute(q, binary=True)
            res.columns = [d.name for d in (cur.description or [])]
            rows = cur.fetchmany(row_limit + 1)
            res.truncated = len(rows) > row_limit
            res.rows = [tuple(r) for r in rows[:row_limit]]
            res.ok = True
        except psycopg.Error as e:
            res.error = f"{type(e).__name__}: {str(e).splitlines()[0][:300]}"
        finally:
            c.execute("ROLLBACK")
    res.elapsed_ms = round((time.time() - t0) * 1000, 1)
    return res


def relations_ok(admin_dsn: str = PG_ADMIN_DSN) -> dict[str, Any]:
    """Catalog facts the permission tests assert on (privileges, policies, role attributes)."""
    with psycopg.connect(admin_dsn) as c:
        gw = c.execute("SELECT rolsuper, rolinherit, rolbypassrls, rolcreaterole, rolcreatedb FROM pg_roles "
                       "WHERE rolname = 'copilot_gateway'").fetchone()
        gw_privs = c.execute("SELECT table_schema || '.' || table_name FROM information_schema.role_table_grants "
                             "WHERE grantee = 'copilot_gateway'").fetchall()
        policies = c.execute("SELECT schemaname || '.' || tablename, policyname, permissive, cmd FROM pg_policies "
                             "WHERE schemaname IN ('wh', 'acl')").fetchall()
        rls = c.execute("SELECT n.nspname || '.' || c.relname, c.relrowsecurity FROM pg_class c JOIN pg_namespace n "
                        "ON n.oid = c.relnamespace WHERE n.nspname IN ('wh', 'acl') AND c.relkind = 'r'").fetchall()
        user_grants = c.execute("SELECT DISTINCT table_schema || '.' || table_name FROM "
                                "information_schema.role_table_grants WHERE grantee = 'copilot_users'").fetchall()
        memberships = c.execute(
            "SELECT r.rolname, m.inherit_option, m.set_option, m.admin_option FROM pg_auth_members m "
            "JOIN pg_roles r ON r.oid = m.roleid JOIN pg_roles g ON g.oid = m.member "
            "WHERE g.rolname = 'copilot_gateway'").fetchall()
    return {"gateway": dict(zip(["super", "inherit", "bypassrls", "createrole", "createdb"], gw, strict=True)),
            "gateway_table_privileges": [r[0] for r in gw_privs], "policies": policies, "rls": dict(rls),
            "user_relations": sorted(r[0] for r in user_grants), "gateway_memberships": memberships,
            "allowlist": sorted(ALLOWED_RELATIONS)}
