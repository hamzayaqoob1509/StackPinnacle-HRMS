"""
List v1 rows that v2's uniqueness rules would reject. Counts and ids only.

Checks every unique field, unique_together and UniqueConstraint in the v2
models against a v1 database, so duplicates are found before a migration
fails on them part-way. horillasetup's own pre-flight checks a fixed list,
which missed unique_payslip_per_employee_period.

Run from the v2 project with the v1 database in V1_DATABASE_URL:

    V1_DATABASE_URL=postgres://user:pass@host:5432/db \
        python manage.py shell -c "exec(open('docs/upgrade/check_v2_uniqueness.py').read())"

Prints one DUPLICATES line per conflicting rule, then a summary line.
"""

import os
from urllib.parse import unquote, urlparse

import psycopg2
from django.apps import apps
from django.db.models import UniqueConstraint

url = urlparse(os.environ["V1_DATABASE_URL"])
conn = psycopg2.connect(
    host=url.hostname,
    port=url.port or 5432,
    dbname=url.path.lstrip("/"),
    user=unquote(url.username or ""),
    password=unquote(url.password or ""),
)
cur = conn.cursor()
cur.execute(
    "select table_name, column_name from information_schema.columns "
    "where table_schema = 'public'"
)
present = {}
for table, column in cur.fetchall():
    present.setdefault(table, set()).add(column)

rules = []
for model in apps.get_models():
    meta = model._meta
    if meta.proxy or not meta.managed:
        continue

    def column(name):
        try:
            return meta.get_field(name).column
        except Exception:
            return None

    for field in meta.local_fields:
        if field.unique and not field.primary_key:
            rules.append((meta.db_table, [field.column], f"{field.name} unique"))
    for group in meta.unique_together:
        rules.append((meta.db_table, [column(f) for f in group], "unique_together"))
    for constraint in meta.constraints:
        # Conditional constraints in v2 only exclude NULLs, which the NOT NULL
        # filter below already does.
        if isinstance(constraint, UniqueConstraint) and constraint.fields:
            rules.append(
                (meta.db_table, [column(f) for f in constraint.fields], constraint.name)
            )

checked = conflicts = 0
for table, columns, name in rules:
    if table not in present or None in columns or not set(columns) <= present[table]:
        continue  # new in v2: nothing to collide with
    checked += 1
    cols = ", ".join(f'"{c}"' for c in columns)
    not_null = " and ".join(f'"{c}" is not null' for c in columns)
    duplicated = (
        f'select {cols} from "{table}" where {not_null} '
        f"group by {cols} having count(*) > 1"
    )
    cur.execute(f"select count(*) from ({duplicated}) d")
    groups = cur.fetchone()[0]
    if groups:
        conflicts += 1
        cur.execute(
            f'select array_agg(id order by id) from "{table}" '
            f"where ({cols}) in ({duplicated})"
        )
        ids = cur.fetchone()[0]
        print(f"DUPLICATES {table} ({', '.join(columns)}) [{name}]: {groups} group(s), ids={ids}")
print(f"UNIQUENESS checked {checked} rules, {conflicts} with duplicates")
