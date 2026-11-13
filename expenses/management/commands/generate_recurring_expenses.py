from django.core.management.base import BaseCommand

from expenses.recurrence import generate_due


class Command(BaseCommand):
    help = 'Crea los gastos recurrentes que tocan hasta hoy. Programarlo una vez al día.'

    def handle(self, *args, **options):
        created = generate_due()
        self.stdout.write(f'Gastos recurrentes creados: {created}')
