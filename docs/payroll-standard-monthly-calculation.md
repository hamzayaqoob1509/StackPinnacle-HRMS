# Plan: one standard monthly salary calculation

Status: implemented on branch `fix/standard-monthly-payroll` (2026-10-27), not deployed.

## Why

Monthly payslips are calculated two different ways today, and neither treats
weekly offs and company holidays as paid days consistently:

| Path | Used when | Daily rate | Holidays / weekly offs |
|---|---|---|---|
| `monthly_computation` (fallback) | "Deduct absent days" off, Auto Payslip | salary ÷ working days | not paid days, and taken out of the divisor |
| attendance-summary branch of `compute_salary_on_period` | "Deduct absent days" on | salary ÷ days in period | paid days |

Payslip 539 (Oct 2026, holiday Oct 12, 1 unpaid leave) shows 29 paid days and
basic = salary ÷ 30 × 29. With Sat/Sun weekly offs the same LOP day cost
salary ÷ 21.

## The rule (monthly-wage contracts)

1. **Period days**: every date from start to end, both included.
2. **Daily rate** = salary × 12 ÷ 365, the same in every month and every year.
3. **Unpaid days** in the period:
   - approved unpaid leave (full day = 1, half day = 0.5),
   - plus `(1 − pct/100)` for each partial-pay (custom) leave day,
   - plus absent days, only when "Deduct absent days in payslips" is on.
   - Which days count as unpaid leave or absent is unchanged: approved
     leave via `get_leaves` (setting off) or the attendance summary
     (setting on), the same as before this change.
   - Unresolved attendance/leave conflicts keep today's rule (whole period
     unpaid) when the attendance summary is used.
4. **Paid days** = period days − unpaid days (LOP). Payslip 539: 31 − 1 = 30.
5. **Basic pay**, worked out per calendar month in the period:
   `salary − (days in the month outside the period + unpaid days) × daily rate`,
   never below 0. A full month with no LOP is exactly the salary.
6. **LOP amount** = unpaid days × daily rate, or × the contract's fixed
   "deduction for one leave" when "calculate daily leave amount" is off.
   The contract's "deduct leave from basic pay" option keeps working as today.

Payslip 539 under this rule: paid days 30, LOP 1,
basic = salary − salary × 12 ÷ 365 (≈ 96.71 % of salary).

Out of scope: daily-wage and hourly contracts keep their current formulas
(they already pay holidays/weekly offs from the attendance summary).

## Code changes

1. **`payroll/methods/methods.py`**
   - `DAYS_PER_YEAR = 365`, `monthly_daily_rate(wage)`.
   - `monthly_computation(...)` applies the rule above for both the
     attendance-summary and the leave-only path; the monthly branch of
     `compute_salary_on_period` calls it for both.
   - New payslips store `pay_head_data["calc_version"] = 2` and
     `pay_head_data["daily_rate"]` (the per-day LOP rate used).
2. **Previous-period leave adjustment** (`get_previous_period_leave_adjustment`):
   compare unpaid **days**, not amounts, so the formula change never shows up
   as a deduction.
   - Recount the previous period's unpaid days with today's data (plus the
     unpaid share of partial-pay leave); `extra_days = recounted − stored`.
     Nothing happens if `extra_days <= 0` (still no refunds).
   - Charge `extra_days × the previous payslip's daily rate`: its stored
     `daily_rate`; for older payslips with unpaid days,
     `(loss_of_pay − custom_leave_deduction) ÷ unpaid_days`; for older
     payslips with none, salary × 12 ÷ 365.
   - Pre-v2 payslips stay skipped, as today.
3. **`payroll/views/component_views.py` `generate_payslip`**: each employee
   keeps their own start date (a later contract start used to move it for
   every employee after them in the batch); the attendance summary is built
   for that period; employees without an active contract get an error
   message instead of a crash.
4. **`payroll/scheduler.py`** (Auto Payslip): uses the attendance summary
   when "Deduct absent days" is on, like Generate Payslip.

Not changed: the attendance summary (`attendance/views/summary.py`),
daily-wage and hourly contracts.

## Tests

- Payslip 539 case: Oct 2026, 1 holiday, 1 unpaid leave → paid 30, unpaid 1,
  basic = salary − salary × 12 ÷ 365.
- Same result with and without Sat/Sun weekly offs.
- Full month, no LOP → basic = salary for 28, 30 and 31-day months.
- Contract starting mid-month; period spanning two months.
- Whole month unpaid → basic 0, not negative.
- Half-day unpaid leave; 50 % custom leave; fixed per-leave penalty;
  "deduct leave from basic pay" off.
- Setting on: absent days deducted at the new rate; unresolved conflicts.
- Leave adjustment: old ÷ 31 / ÷ 30 payslips with and without late leave,
  0 stored unpaid days, stored `daily_rate`, late 50 % custom leave.
- Bulk batch where the first employee's contract starts late doesn't change
  the second employee's period.

## Local verification (2026-10-27)

- Full test suite on PostgreSQL 15.19 (same as production): 914 tests, the
  20 new ones pass; the 33 failures are the same as on `main` (notifications,
  horilla_tour, one leave test, dynamic_fields import). Re-run after the
  attendance summary changes were reverted.
- Running app, Generate Payslip for October 2026 with Sat/Sun weekly offs and
  a holiday on Oct 12 (salary 36,500, daily rate 1,200):
  - payslip-539 case ("Deduct absent days" off): 30 paid days, 1 LOP,
    basic 35,300, plus "Leave Adjustment (Sep 2026) - 1.0 day(s)" 1,200 for a
    September unpaid leave approved after September's payslip
  - contract starting Oct 20, in the same batch: 12 paid days, basic 13,700;
    the other employees kept Oct 1
  - "Deduct absent days" on, 2 absent days: 29 paid days, basic 34,100
  - payslip page and print view show the same figures, no server errors
- No migrations, package or static changes (`makemigrations --check` clean).
  The production rehearsal on a restored dump was skipped.

## Rollout

No migrations, so deploying is pull + restart (docs/DEPLOY.md steps 1 and 5).
Take a dump first anyway; production has no scheduled backups.

1. Merge the branch to `main`.
2. On the server, dump the database (DEPLOY.md step 3, first command only).
3. Pull and restart (DEPLOY.md steps 1 and 5). Expect `active` twice,
   `site: 200` and `-- No entries --`.
4. HR deletes the October draft payslips and generates them again
   (payslip 539 should show 30 paid days, 1 LOP). Confirmed or paid payslips
   are not changed.
5. HR turns the Sat/Sun weekly offs back on and regenerates one draft to see
   that pay does not change.

Rollback: DEPLOY.md "Rolling back a deploy", no-migrations case. Payslips
generated after the deploy keep the new figures until regenerated.
