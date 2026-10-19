from django.db import models

class Tasks(models.Model):
    PRIORITY_CHOICES = [
        ('low', 'Low'),
        ('medium', 'Medium'),
        ('high', 'High'),
    ]

    REPEAT_RULES = [
        ('none', 'No repeat'),
        ('daily', 'Daily'),
        ('weekly', 'Weekly'),
        ('monthly', 'Monthly'),
    ]

    title = models.CharField(max_length=120)
    description = models.TextField()
    status = models.BooleanField(default=False)
    priority = models.CharField(max_length=10, choices=PRIORITY_CHOICES, default='medium')
    created_at = models.DateTimeField(auto_now_add=True)
    due_at = models.DateField()
    repeat_rule = models.CharField(max_length=20, choices=REPEAT_RULES, default='none')

    def __str__(self):
        return self.title
