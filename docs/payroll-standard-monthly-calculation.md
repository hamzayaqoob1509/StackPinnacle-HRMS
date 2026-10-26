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
   - Weekly offs and company holidays are never unpaid, even inside a leave.
   - Unresolved attendance/leave conflicts keep today's rule (whole period
     unpaid) when the attendance summary is used.
4. **Paid days** = period days − unpaid days. Holidays and weekly offs count
   as paid.
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
   - Add `DAYS_PER_YEAR = 365` and `monthly_daily_rate(wage)`.
   - Add `monthly_salary(employee, wage, start_date, end_date, day_counts)`
     that applies steps 4–6 and returns the same keys the callers use today
     (`basic_pay`, `loss_of_pay`, `paid_days`, `unpaid_days`,
     `custom_leave_deduction`, `custom_leave_breakdown`, `month_data`, …).
   - Add `period_day_counts(employee, start_date, end_date, month_summary)`
     that returns `unpaid_leave`, `absent`, `partial_pay` from either the
     attendance summary (setting on) or `get_leaves` + half-day logic
     (setting off), so both paths feed the same formula.
   - `compute_salary_on_period`: the monthly branch calls these two for both
     paths. Remove `monthly_computation`'s money logic and
     `get_daily_salary` use for monthly contracts.
   - `months_between_range`: keep the shape of `month_data` (it is passed as
     `day_dict` to allowance/deduction code), set `per_day_amount` to the new
     daily rate.
   - Store `pay_head_data["calc_version"] = 2` and
     `pay_head_data["daily_rate"]` (the per-day LOP rate used) on new
     payslips.
2. **Previous-period leave adjustment** (`get_previous_period_leave_adjustment`):
   compare unpaid **days**, not amounts, so a formula change can never show
   up as a deduction. It works for payslips made before and after the
   change.
   - Recount the previous period's unpaid-equivalent days with today's data
     (unpaid leave + absent if that payslip used the attendance summary +
     the unpaid share of partial-pay leave).
   - `extra_days = recounted − stored`. Nothing happens if `extra_days <= 0`
     (still no refunds).
   - Charge `extra_days × the previous payslip's daily rate`:
     - new payslips: the stored `daily_rate`;
     - older payslips with unpaid days: stored
       `(loss_of_pay − custom_leave_deduction) ÷ unpaid_days`, the rate that
       payslip actually used;
     - older payslips with no unpaid days (no rate saved): the new standard
       rate, salary × 12 ÷ 365.
   - Older payslips' stored custom-leave days come from
     `custom_leave_breakdown`, which already saves days and percentage.
   - Pre-v2 payslips stay skipped, as today.
   - Known edge: the new rules don't count a holiday/weekly off inside an
     unpaid leave as unpaid. If an old summary-based payslip counted one,
     the recount is lower by that day, which can hide one late-approved day
     on that payslip only.
3. **`attendance/views/summary.py` `build_monthly_summary`**
   - Classify leave with `payment_type` (fallback to legacy `payment`), the
     way `get_leaves` does, so custom-percentage leave isn't counted as fully
     unpaid.
   - A holiday or weekly off inside a leave counts as holiday/weekly off,
     not leave (matches the fallback today). This also changes the attendance
     summary screen's counts for those dates.
   - Scope holidays to the employee (`get_holiday_dates(..., employee)`) so
     employee-specific holidays don't apply to everyone.
4. **`payroll/views/component_views.py` `generate_payslip`**: use a
   per-employee start date. Today one employee's later contract start moves
   the start date for every employee after them in the batch.
5. **`payroll/scheduler.py`** (Auto Payslip): goes through the same
   `compute_salary_on_period`, so it follows the setting like bulk generation.

## Tests

- Payslip 539 case: Oct 2026, 1 holiday, 1 unpaid leave → paid 30, unpaid 1,
  basic = salary − salary × 12 ÷ 365.
- Same result with and without Sat/Sun weekly offs.
- Full month, no LOP → basic = salary for 28, 30 and 31-day months.
- Contract starting mid-month; period spanning two months.
- Whole month unpaid → basic 0, not negative.
- Half-day unpaid leave; 50 % custom leave; fixed per-leave penalty;
  "deduct leave from basic pay" off.
- Setting on: absent days deducted at the new rate; holidays/weekly offs paid.
- Leave adjustment, old-formula previous payslip (÷ 31 and ÷ 30 cases):
  no late leave → no adjustment; 1 late unpaid day → charged at that
  payslip's own rate.
- Leave adjustment, previous payslip with 0 unpaid days + 1 late day →
  charged at salary × 12 ÷ 365.
- Leave adjustment, new-formula previous payslip → uses stored `daily_rate`.
- Late 50 % custom leave → charged half a day.
- Bulk batch where the first employee's contract starts late doesn't change
  the second employee's period.
- Update `test_compute_salary_on_period.py` expectations for the new rate.

## Rollout

1. Rehearse on a restored copy of production: generate October payslips
   before and after, compare paid days / LOP days per employee id (no names
   or amounts in the output).
2. Take a database dump, deploy, run tests on the server.
3. HR deletes and regenerates October draft payslips (539 and others).
   Confirmed or paid payslips are not changed.
4. HR turns the Sat/Sun weekly offs back on; pay should not change.
