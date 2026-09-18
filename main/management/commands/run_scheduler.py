from django.core.management.base import BaseCommand
from apscheduler.schedulers.blocking import BlockingScheduler
from django_apscheduler.jobstores import DjangoJobStore, register_events
from django.conf import settings
from django.utils import timezone
from main.models import UserNicheUpdate, Channel
from main.views import perform_full_update 

def daily_maintenance_job():
    """Задача, которая выполняется автоматически"""
    print(f"[{timezone.now()}] СТАРТ: Ежедневное обслуживание (Amvera)")
    
    try:
        # 1. Сброс лимитов
        updated_count = UserNicheUpdate.objects.all().update(count=0)
        print(f"--- Лимиты сброшены для {updated_count} записей.")

        # 2. Обновление каналов
        all_channels = Channel.objects.all()
        if all_channels.exists():
            print(f"--- Начинаю обновление данных для {all_channels.count()} каналов...")
            perform_full_update(all_channels)
            print("--- Все данные успешно синхронизированы.")
        else:
            print("--- В базе пока нет каналов.")

    except Exception as e:
        print(f"!!! КРИТИЧЕСКАЯ ОШИБКА: {e}")
    
    print(f"[{timezone.now()}] ЗАВЕРШЕНО.")

class Command(BaseCommand):
    help = "Запуск планировщика задач"

    def handle(self, *args, **options):
        # Используем BlockingScheduler для работы в отдельном процессе
        scheduler = BlockingScheduler(timezone=settings.TIME_ZONE)
        scheduler.add_jobstore(DjangoJobStore(), "default")

        scheduler.add_job(
            daily_maintenance_job,
            trigger='cron',
            hour=0,
            minute=0,
            id="daily_maintenance_job",
            max_instances=1,
            replace_existing=True,
        )

        register_events(scheduler) # Чтобы видеть историю в админке

        print("--- APScheduler запущен (через команду) ---")
        try:
            scheduler.start()
        except (KeyboardInterrupt, SystemExit):
            print("--- Планировщик остановлен ---")
            scheduler.shutdown()