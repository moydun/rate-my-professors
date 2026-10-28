from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("reviews", "0004_professor_prof_status_idx_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="report",
            name="reason",
            field=models.CharField(
                choices=[
                    ("spam", "Спам"),
                    ("abuse", "Оскорбления или угрозы"),
                    ("personal_data", "Личные данные"),
                    ("other", "Другое"),
                ],
                default="other",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="report",
            name="status",
            field=models.CharField(
                choices=[("pending", "Ожидает рассмотрения"), ("reviewed", "Рассмотрена")],
                default="pending",
                max_length=10,
            ),
        ),
    ]
