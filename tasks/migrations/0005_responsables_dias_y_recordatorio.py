from django.conf import settings
from django.db import migrations, models


def forwards(apps, schema_editor):
    # due_at → due_date + reminder_time; assignee → assignees; weekly → su día de la semana
    Task = apps.get_model('tasks', 'Task')
    for task in Task.objects.all():
        task.due_date = task.due_at.date()
        task.reminder_time = task.due_at.time().replace(second=0, microsecond=0)
        task.repeat_days = [task.due_at.weekday()] if task.repeat_rule == 'weekly' else []
        task.save(update_fields=['due_date', 'reminder_time', 'repeat_days'])
        if task.assignee_id:
            task.assignees.add(task.assignee_id)


class Migration(migrations.Migration):

    dependencies = [
        ('tasks', '0004_task_por_hogar'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='task',
            name='assignees',
            field=models.ManyToManyField(blank=True, related_name='assigned_tasks_m2m', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='task',
            name='due_date',
            field=models.DateField(null=True),
        ),
        migrations.AddField(
            model_name='task',
            name='reminder_time',
            field=models.TimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='task',
            name='repeat_days',
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.RunPython(forwards, migrations.RunPython.noop),
        migrations.RemoveIndex(
            model_name='task',
            name='tasks_task_househo_1de7fd_idx',
        ),
        migrations.RemoveField(model_name='task', name='assignee'),
        migrations.RemoveField(model_name='task', name='due_at'),
        migrations.RemoveField(model_name='task', name='repeat_rule'),
        migrations.AlterField(
            model_name='task',
            name='assignees',
            field=models.ManyToManyField(blank=True, related_name='assigned_tasks', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AlterField(
            model_name='task',
            name='due_date',
            field=models.DateField(),
        ),
        migrations.AlterModelOptions(
            name='task',
            options={'ordering': ['due_date', 'reminder_time', 'created_at']},
        ),
        migrations.AddIndex(
            model_name='task',
            index=models.Index(fields=['household', 'due_date'], name='tasks_household_due_date_idx'),
        ),
    ]
