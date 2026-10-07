-- Run once, right after `horillasetup migrate hrms-v2 --from-v1` succeeds.
--
-- horillasetup adopts v1's existing tables and adds the columns v2 introduced,
-- but leaves an existing column as v1 defined it. v2 changed these inside its
-- 0001_initial migrations, so on a migrated database they keep v1's stricter
-- definition and v2 fails when it writes a value v1 would not have accepted.
-- Found by diffing a migrated database against a fresh 2.1.8 install.
--
-- Safe to re-run. Only widens or relaxes columns; no row is changed.

BEGIN;

-- v2: CharField(max_length=100). v1: 20.
ALTER TABLE payroll_loanaccount ALTER COLUMN title TYPE varchar(100);

-- v2: TextField. v1: CharField(max_length=150).
ALTER TABLE pms_comment ALTER COLUMN comment TYPE text;

-- v2: null=True. v1: NOT NULL.
ALTER TABLE employee_employeebankdetails
    ALTER COLUMN bank_name DROP NOT NULL,
    ALTER COLUMN city DROP NOT NULL,
    ALTER COLUMN state DROP NOT NULL;

-- v2: NOT NULL. v1: nullable. Tightened only when no row would violate it.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM facedetection_employeefacedetection WHERE image IS NULL) THEN
        ALTER TABLE facedetection_employeefacedetection ALTER COLUMN image SET NOT NULL;
    ELSE
        RAISE NOTICE 'facedetection_employeefacedetection.image has NULLs; left nullable';
    END IF;
END $$;

-- Leftover v1 table; the model is commented out in v2. Dropped only if empty.
DO $$
BEGIN
    IF to_regclass('public.payroll_overrideattendance') IS NOT NULL THEN
        IF NOT EXISTS (SELECT 1 FROM payroll_overrideattendance) THEN
            DROP TABLE payroll_overrideattendance;
        ELSE
            RAISE NOTICE 'payroll_overrideattendance has rows; left in place';
        END IF;
    END IF;
END $$;

COMMIT;
