import os
import sys
from django.apps import AppConfig

class MainConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'main'

    def ready(self):
        # 1. Защита: не запускаем логику БД во время самих миграций или сбора статики
        if any(cmd in sys.argv for cmd in ['migrate', 'makemigrations', 'collectstatic']):
            return

        # 2. Вызываем создание админа напрямую (после загрузки всех приложений)
        try:
            self.create_admin_direct()
        except Exception as e:
            print(f"Ошибка при автоматическом создании админа: {e}")

    def create_admin_direct(self):
        from django.contrib.auth import get_user_model
        from .models import UserProfile
        
        User = get_user_model()
        # Берем данные из окружения или ставим дефолты
        admin_username = os.getenv('ADMIN_USERNAME', 'admin')
        admin_password = os.getenv('ADMIN_PASSWORD')
        admin_email = os.getenv('ADMIN_EMAIL', 'admin@example.com')

        if not admin_password:
            return

        if not User.objects.filter(username=admin_username).exists():
            new_admin = User.objects.create_superuser(
                username=admin_username,
                email=admin_email,
                password=admin_password
            )
            # Профиль создастся автоматически через сигнал post_save в models.py,
            # но для надежности проверим/создадим:
            UserProfile.objects.get_or_create(user=new_admin)
            print(f"--- Суперпользователь '{admin_username}' создан успешно! ---")
        else:
            # Убедимся, что у существующего админа есть профиль
            admin_user = User.objects.get(username=admin_username)
            UserProfile.objects.get_or_create(user=admin_user)