from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("payroll", "0009_encashmentgeneralsettings_department_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="payrollsettings",
            name="deduct_absent_days",
            field=models.BooleanField(
                default=True,
                help_text="When enabled, Generate Payslip counts working days with no attendance as loss of pay. When disabled, only approved unpaid leave is deducted.",
                verbose_name="Deduct absent days in payslips",
            ),
        ),
    ]
