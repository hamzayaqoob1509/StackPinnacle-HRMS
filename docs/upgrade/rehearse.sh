#!/usr/bin/env bash
# Rehearse the v1 -> v2 migration on a fresh restore of a production backup.
#
#   docs/upgrade/rehearse.sh ~/horilla-backups/v1-backup.dump
#
# Runs on a developer machine with Docker. Everything happens in throwaway
# databases inside the hrms-rehearsal-db container; the backup file is only
# read. Steps, in the order production will run them (RUNBOOK.md part B):
#
#   1. restore the backup into a fresh database and count rows
#   2. check the data against every v2 uniqueness rule
#   3. remove the known duplicate payslip (128, identical to 127)
#   4. horillasetup migrate hrms-v2 --from-v1, with the hybrid patch
#   5. post_migrate_align.sql
#   6. checks: Google Drive backup table, migrate --check, schema against a
#      fresh v2 install, row counts before and after
#
# Prints PASS or FAIL for each check and exits non-zero on any FAIL.

set -uo pipefail

DUMP="${1:?usage: rehearse.sh <backup.dump>}"
[ -f "$DUMP" ] || { echo "no such file: $DUMP"; exit 2; }

V2_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
NETWORK=hrms-rehearsal
DB_CONTAINER=hrms-rehearsal-db
DB_PASSWORD=rehearsal-local-only
BASE_IMAGE=stackpinnacle-hrms-v2:dev
IMAGE=stackpinnacle-hrms-v2:rehearsal
WORK="$HOME/horilla-backups/rehearsal-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$WORK" && chmod 700 "$WORK"
FAILED=0

say() { printf '\n== %s\n' "$*"; }
pass() { printf '   PASS  %s\n' "$*"; }
fail() { printf '   FAIL  %s\n' "$*"; FAILED=1; }
psql_db() { docker exec -i "$DB_CONTAINER" psql -U rehearsal -v ON_ERROR_STOP=1 -tA "$@"; }
manage() {
    local db="$1"; shift
    docker run --rm -i --network "$NETWORK" \
        -e DEBUG=True -e SECRET_KEY=rehearsal-only -e DB_INIT_PASSWORD=rehearsal-init \
        -e ALLOWED_HOSTS=localhost \
        -e "DATABASE_URL=postgres://rehearsal:$DB_PASSWORD@$DB_CONTAINER:5432/$db" \
        -v "$V2_DIR":/app --user "$(id -u):$(id -g)" \
        --entrypoint python "$IMAGE" -W ignore manage.py "$@"
}

say "Preparing"
if ! docker image inspect "$BASE_IMAGE" >/dev/null 2>&1; then
    docker build -q -t "$BASE_IMAGE" "$V2_DIR" >/dev/null || { echo "v2 image build failed"; exit 1; }
fi
# The tool exactly as production installs it: horillasetup 1.1.5 from PyPI
# plus the hybrid-fingerprint patch from this directory.
docker build -q -t "$IMAGE" -f - "$V2_DIR/docs/upgrade" >/dev/null <<EOF || { echo "rehearsal image build failed"; exit 1; }
FROM $BASE_IMAGE
USER root
RUN apt-get update && apt-get install -y --no-install-recommends postgresql-client patch \\
    && rm -rf /var/lib/apt/lists/*
COPY horillasetup-1.1.5-hybrid-fingerprint.patch /tmp/hybrid.patch
RUN pip install --no-cache-dir horillasetup==1.1.5 \\
    && cd "\$(python -c 'import horillasetup,os;print(os.path.dirname(os.path.dirname(horillasetup.__file__)))')" \\
    && patch -p1 < /tmp/hybrid.patch
USER appuser
EOF
docker network create "$NETWORK" >/dev/null 2>&1
if ! docker inspect "$DB_CONTAINER" >/dev/null 2>&1; then
    docker run -d --name "$DB_CONTAINER" --network "$NETWORK" \
        -e POSTGRES_USER=rehearsal -e POSTGRES_PASSWORD="$DB_PASSWORD" -e POSTGRES_DB=postgres \
        -v hrms-rehearsal-data:/var/lib/postgresql/data postgres:15-bookworm >/dev/null
fi
docker start "$DB_CONTAINER" >/dev/null
until docker exec "$DB_CONTAINER" pg_isready -U rehearsal -q; do sleep 1; done
sleep 2
echo "   working files: $WORK"

say "1. Restore into a fresh database"
for db in horilla_rehearsal horilla_v2_reference; do
    docker exec "$DB_CONTAINER" dropdb -U rehearsal --if-exists "$db"
    docker exec "$DB_CONTAINER" createdb -U rehearsal "$db"
done
docker cp "$DUMP" "$DB_CONTAINER:/tmp/rehearsal.dump"
if docker exec "$DB_CONTAINER" pg_restore --no-owner --no-privileges -U rehearsal \
        -d horilla_rehearsal /tmp/rehearsal.dump; then
    pass "restored $(psql_db -d horilla_rehearsal -c "select count(*) from information_schema.tables where table_schema='public'") tables"
else
    fail "pg_restore reported errors"
fi
docker exec "$DB_CONTAINER" rm -f /tmp/rehearsal.dump

# Row counts that must survive. Holidays are counted across both apps, since
# v2 moves them from leave to base.
COUNTS="
select 'users', count(*) from (select id from %USERS%) u
union all select 'employees', count(*) from employee_employee
union all select 'active employees', count(*) from employee_employee where is_active
union all select 'companies', count(*) from base_company
union all select 'departments', count(*) from base_department
union all select 'holidays', (select count(*) from (select distinct name, start_date from base_holidays %LEAVE_HOLIDAYS%) h)
union all select 'company leaves', (select count(*) from (select distinct based_on_week, based_on_week_day from base_companyleaves %LEAVE_COMPANYLEAVES%) c)
union all select 'leave types', count(*) from leave_leavetype
union all select 'leave requests', count(*) from leave_leaverequest
union all select 'available leave rows', count(*) from leave_availableleave
union all select 'available leave days', coalesce(sum(available_days), 0)::bigint from leave_availableleave
union all select 'attendance rows', count(*) from attendance_attendance
union all select 'contracts', count(*) from payroll_contract
union all select 'payslips', count(*) from payroll_payslip
union all select 'handbook entries', count(*) from employee_handbook_handbookdocument
union all select 'probation notifications', count(*) from employee_probationnotification
union all select 'pro-rata allocations', count(*) from leave_prorataleaveallocation
"
count_rows() {
    local sql="$COUNTS"
    if [ "$(psql_db -d horilla_rehearsal -c "select to_regclass('auth_user') is not null")" = "t" ]; then
        sql="${sql//%USERS%/auth_user}"
    else
        sql="${sql//%USERS%/horilla_auth_horillauser}"
    fi
    if [ "$(psql_db -d horilla_rehearsal -c "select to_regclass('leave_holiday') is not null")" = "t" ]; then
        sql="${sql//%LEAVE_HOLIDAYS%/union select name, start_date from leave_holiday}"
        sql="${sql//%LEAVE_COMPANYLEAVES%/union select based_on_week, based_on_week_day from leave_companyleave}"
    else
        sql="${sql//%LEAVE_HOLIDAYS%/}"
        sql="${sql//%LEAVE_COMPANYLEAVES%/}"
    fi
    psql_db -d horilla_rehearsal -F '|' -c "$sql"
}
count_rows > "$WORK/counts-before.txt" || fail "could not count rows"
sed 's/|/: /' "$WORK/counts-before.txt" | sed 's/^/   /'

say "2. Check against every v2 uniqueness rule"
docker run --rm -i --network "$NETWORK" \
    -e DEBUG=True -e SECRET_KEY=rehearsal-only -e DB_INIT_PASSWORD=rehearsal-init -e ALLOWED_HOSTS=localhost \
    -e "DATABASE_URL=postgres://rehearsal:$DB_PASSWORD@$DB_CONTAINER:5432/horilla_v2_reference" \
    -e "V1_DATABASE_URL=postgres://rehearsal:$DB_PASSWORD@$DB_CONTAINER:5432/horilla_rehearsal" \
    -v "$V2_DIR":/app --user "$(id -u):$(id -g)" --entrypoint python "$IMAGE" -W ignore \
    manage.py shell -c "exec(open('docs/upgrade/check_v2_uniqueness.py').read())" 2>/dev/null \
    | grep -E '^(DUPLICATES|UNIQUENESS)' > "$WORK/uniqueness.txt"
sed 's/^/   /' "$WORK/uniqueness.txt"
unexpected=$(grep '^DUPLICATES' "$WORK/uniqueness.txt" \
    | grep -v -F 'payroll_payslip (employee_id_id, start_date, end_date) [unique_payslip_per_employee_period]: 1 group(s), ids=[127, 128]')
if ! grep -q '^UNIQUENESS' "$WORK/uniqueness.txt"; then
    fail "uniqueness check did not run"
elif [ -n "$unexpected" ]; then
    fail "duplicates other than payslips 127/128; resolve them before migrating"
else
    pass "no duplicates other than the known payslip pair"
fi

say "3. Remove the known duplicate payslip"
deleted=$(psql_db -q -d horilla_rehearsal -c "
delete from payroll_payslip d using payroll_payslip k
 where d.id = 128 and k.id = 127 and d.status = 'draft'
   and d.employee_id_id = k.employee_id_id and d.start_date = k.start_date and d.end_date = k.end_date
   and d.net_pay = k.net_pay and d.pay_head_data::text = k.pay_head_data::text
   and not exists (select 1 from payroll_payslip_installment_ids i where i.payslip_id = 128)
 returning d.id")
if [ "$deleted" = "128" ]; then
    pass "deleted payslip 128"
elif [ -z "$(psql_db -d horilla_rehearsal -c 'select 1 from payroll_payslip where id = 128')" ]; then
    pass "payslip 128 already gone"
else
    fail "payslip 128 exists but no longer matches 127; check it before migrating"
fi

if [ "$FAILED" -ne 0 ]; then
    say "Stopping before the migration: fix the failures above first"
    exit 1
fi

say "4. horillasetup migrate hrms-v2 --from-v1"
mkdir -p "$WORK/tool-backup"
echo y | docker run --rm -i --network "$NETWORK" \
    -e DEBUG=True -e SECRET_KEY=rehearsal-only -e DB_INIT_PASSWORD=rehearsal-init -e ALLOWED_HOSTS=localhost \
    -e "DATABASE_URL=postgres://rehearsal:$DB_PASSWORD@$DB_CONTAINER:5432/horilla_rehearsal" \
    -e HORILLASETUP_REHEARSAL_ASSUME_15_PLUS=1 \
    -v "$V2_DIR":/app -v "$WORK/tool-backup":/backups --user "$(id -u):$(id -g)" \
    --entrypoint horillasetup "$IMAGE" migrate hrms-v2 --from-v1 --backup-dir /backups \
    > "$WORK/horillasetup.log" 2>&1
tool_exit=$?
grep -E 'Stage [0-9]/6|✅|❌|•|users to migrate|unapplied|reordered|reset to match|carried' "$WORK/horillasetup.log" \
    | grep -v '^\s*$' | sed 's/^/   /'
if [ "$tool_exit" -eq 0 ] && grep -q 'Migration complete' "$WORK/horillasetup.log"; then
    pass "all six stages completed"
else
    fail "horillasetup exited $tool_exit; full log: $WORK/horillasetup.log"
    exit 1
fi

say "5. post_migrate_align.sql"
if psql_db -d horilla_rehearsal -q < "$V2_DIR/docs/upgrade/post_migrate_align.sql" \
        && psql_db -d horilla_rehearsal -q < "$V2_DIR/docs/upgrade/post_migrate_align.sql"; then
    pass "applied, and re-running it is a no-op"
else
    fail "post_migrate_align.sql failed"
fi

say "6. Checks"
gdrive=$(psql_db -d horilla_rehearsal -c "
select (select count(*) from information_schema.columns where table_name = 'horilla_backup_googledrivebackup'
         and column_name in ('access_token', 'oauth_credentials_file', 'refresh_token', 'token_expiry'))
    || ' ' || exists(select 1 from information_schema.columns where table_name = 'horilla_backup_googledrivebackup'
         and column_name = 'service_account_file')")
if [ "$gdrive" = "4 false" ]; then
    pass "Google Drive backup table has the 4 OAuth columns and no service_account_file"
else
    fail "Google Drive backup table: OAuth columns / service_account_file = $gdrive"
fi

if manage horilla_rehearsal migrate --check >/dev/null 2>&1; then
    pass "no pending migrations"
else
    fail "migrate --check reports pending migrations"
fi

manage horilla_v2_reference migrate --noinput >/dev/null 2>&1 || fail "could not build the fresh v2 reference"
SCHEMA="
select 'COL ' || table_name || '.' || column_name || ' '
       || case when data_type in ('integer', 'bigint') then 'int' else data_type end
       || coalesce('(' || character_maximum_length || ')', '') || ' null=' || is_nullable
       || ' default=' || coalesce(regexp_replace(column_default, 'nextval\(.*\)', 'nextval'), '-')
  from information_schema.columns where table_schema = 'public'
union all
select 'CON ' || conrelid::regclass::text || ' ' || contype::text || ' ' || pg_get_constraintdef(oid)
  from pg_constraint where connamespace = 'public'::regnamespace
union all
select 'IDX ' || tablename || ' ' || regexp_replace(indexdef, 'INDEX \S+ ON', 'INDEX ON')
  from pg_indexes where schemaname = 'public'"
psql_db -d horilla_rehearsal -c "$SCHEMA" | sort > "$WORK/schema-migrated.txt"
psql_db -d horilla_v2_reference -c "$SCHEMA" | sort > "$WORK/schema-fresh-v2.txt"
diff "$WORK/schema-migrated.txt" "$WORK/schema-fresh-v2.txt" > "$WORK/schema.diff"
differences=$(grep -c '^[<>]' "$WORK/schema.diff")
if [ "$differences" -eq 0 ]; then
    pass "schema matches a fresh v2 install (ignoring integer/bigint user ids)"
else
    fail "$differences schema difference(s) from a fresh v2 install; see $WORK/schema.diff"
fi

count_rows > "$WORK/counts-after.txt" || fail "could not count rows after"
printf '   %-26s %12s %12s\n' "" before after
mismatch=0
while IFS='|' read -r label before; do
    after=$(grep "^$label|" "$WORK/counts-after.txt" | cut -d'|' -f2)
    expected="$before"
    if [ "$label" = "payslips" ] && [ "$deleted" = "128" ]; then expected=$((before - 1)); fi
    marker=""
    if [ "$after" != "$expected" ]; then marker="  <-- expected $expected"; mismatch=1; fi
    printf '   %-26s %12s %12s%s\n' "$label" "$before" "$after" "$marker"
done < "$WORK/counts-before.txt"
if [ "$mismatch" -eq 0 ]; then
    pass "row counts match (payslips minus the removed duplicate)"
else
    fail "row counts changed"
fi

if [ "$FAILED" -eq 0 ]; then
    say "REHEARSAL PASSED"
else
    say "REHEARSAL FAILED"
fi
exit "$FAILED"
