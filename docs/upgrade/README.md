# Upgrading production from Horilla v1 to v2

Files used for the v1 → v2 database migration, and what the rehearsal on a
restored copy of production found. The rehearsal ran `horillasetup` 1.1.5
against this branch on PostgreSQL 15.

## Findings

1. **`horillasetup` refuses the database at stage 1 ("unrecognised schema").**
   Production was built from upstream master of December 2025, between two
   releases. It has the 1.5+ attendance columns but still the pre-1.5 Google
   Drive backup table, and the tool accepts only one layout or the other. The
   backup table is empty, and v2's `horilla_backup/0002` converts it either
   way. `horillasetup-1.1.5-hybrid-fingerprint.patch` lets the tool treat this
   case as 1.5+ when `HORILLASETUP_REHEARSAL_ASSUME_15_PLUS=1` is set. With
   it, all six stages pass.

2. **A duplicate payslip fails stage 5.** v2 adds
   `unique_payslip_per_employee_period`, which the tool's pre-flight does not
   check. Production has one pair: payslips 127 and 128, identical drafts
   created in the same second and never sent. Delete 128 before migrating.
   Checked against all 91 uniqueness rules in the v2 models, this is the only
   conflict.

3. **Six adopted columns keep v1's definition.** The tool adds columns v2
   introduced but does not alter existing ones, and v2 changed these inside
   its `0001_initial` migrations. `post_migrate_align.sql` fixes them. Without
   it, v2 fails on a loan title over 20 characters, a PMS comment over 150, or
   bank details with a blank bank name, city or state. This affects any v1
   release, not only this build.

After 2 and 3, the migrated database matches a fresh 2.1.8 install of this
branch in every table, column, constraint and index, except that columns
referencing a user are `integer` rather than `bigint` (the tool renames v1's
`auth_user`, keeping its id type).

## Order on the day

1. Take Horilla offline; snapshot the server and `pg_dump` the database.
2. Delete payslip 128.
3. Install `horillasetup==1.1.5` and apply the patch.
4. `HORILLASETUP_REHEARSAL_ASSUME_15_PLUS=1 horillasetup migrate hrms-v2 --from-v1`
   from this branch's directory, before any v2 process starts. v2's Docker
   entrypoint runs `migrate` by itself, which the migration guide warns
   destroys holiday data on a v1 database.
5. `psql -v ON_ERROR_STOP=1 -f docs/upgrade/post_migrate_align.sql`
6. Start v2 and verify.
