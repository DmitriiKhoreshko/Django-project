from django.db import models
from django.contrib.auth.models import User
from django.db.models.signals import post_delete
from django.dispatch import receiver
from django.conf import settings
# --- МОДЕЛИ НИШ И КАНАЛОВ ---

class Niche(models.Model):
    name = models.CharField(max_length=100, verbose_name="Название ниши")

    class Meta:
        verbose_name = "Ниша"
        verbose_name_plural = "Ниши"

        permissions = [
            ("can_add_niche", "Может добавлять ниши"),
        ]

    def __str__(self):
        return self.name

class Channel(models.Model):
    # Добавляем владельца канала
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='my_channels', verbose_name="Пользователь")
    niche = models.ForeignKey(Niche, on_delete=models.CASCADE, related_name='channels', verbose_name="Ниша")
    
    channel_id = models.CharField(max_length=100, verbose_name="YouTube Channel ID") # Убираем unique=True, так как один ID могут добавить разные юзеры
    title = models.CharField(max_length=200, blank=True, verbose_name="Название канала")
    
    average_views = models.IntegerField(default=0, verbose_name="Средние просмотры")
    updated_at = models.DateTimeField(null=True, blank=True, verbose_name="Последнее обновление")

    class Meta:
        verbose_name = "Канал"
        verbose_name_plural = "Каналы"
        # Гарантируем, что один пользователь не добавит один и тот же канал дважды
        unique_together = ('user', 'channel_id')

    def __str__(self):
        return f"{self.user.username} | {self.title or self.channel_id}"

# --- МОДЕЛИ ВИДЕО И ЗАДАЧ ---

class FoundVideo(models.Model):
    VIDEO_TYPES = [
        ('shorts', 'Shorts'),
        ('long', 'Long Video'),
    ]
    channel = models.ForeignKey(Channel, on_delete=models.CASCADE, related_name='found_videos')
    title = models.CharField(max_length=255)
    video_id = models.CharField(max_length=50)
    views = models.IntegerField()
    avg_at_moment = models.IntegerField() 
    score = models.FloatField()
    published_at = models.DateTimeField()
    thumbnail = models.URLField()
    sample_size = models.IntegerField(default=0)
    video_type = models.CharField(max_length=10, choices=VIDEO_TYPES, default='shorts')
    class Meta:
        ordering = ['-score']
        verbose_name = "Найденные видео всех пользователей"
        verbose_name_plural = "Найденные видео всех пользователей"

class BackgroundRemovalTask(models.Model):
    STATUS_CHOICES = [
        ('pending', 'В очереди'),
        ('processing', 'Обработка'),
        ('completed', 'Готово'),
        ('error', 'Ошибка'),
    ]
    
    # КЛЮЧЕВОЕ ИЗМЕНЕНИЕ: Привязка к пользователю
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='bg_tasks', verbose_name="Пользователь")
    
    # Организуем хранение файлов по папкам пользователей для порядка на сервере
    image = models.ImageField(upload_to='bg_remover/%Y/%m/%d/input/')
    result = models.ImageField(upload_to='bg_remover/%Y/%m/%d/output/', null=True, blank=True)
    
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Задача удаления фона"
        verbose_name_plural = "Задачи удаления фона"
        ordering = ['-created_at']

# --- ПРОФИЛЬ И СЧЕТЧИКИ ОБНОВЛЕНИЙ ---

class UserProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    # Общий лимит пользователя (сколько раз он может обновить ОДНУ любую нишу)
    niche_limit = models.PositiveIntegerField(default=1, verbose_name="Доступно обновлений на одну нишу")

    class Meta:
        verbose_name = "Профиль"
        verbose_name_plural = "Профили"

class UserNicheUpdate(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='niche_updates')
    niche = models.ForeignKey('Niche', on_delete=models.CASCADE, verbose_name="Ниша")
    # Тот самый счетчик, который вы хотите менять в админке
    count = models.PositiveIntegerField(default=0, verbose_name="Использовано попыток")
    last_update = models.DateTimeField(auto_now=True, verbose_name="Дата последнего обновления")

    class Meta:
        unique_together = ('user', 'niche')
        verbose_name = "Счетчик обновлений ниши"
        verbose_name_plural = "Счетчики обновлений ниш"

    def __str__(self):
        return f"{self.user.username} | {self.niche.name} | {self.count}"

# --- СИГНАЛЫ ---

@receiver(post_delete, sender=BackgroundRemovalTask)
def submission_delete(sender, instance, **kwargs):
    if instance.image:
        instance.image.delete(False)
    if instance.result:
        instance.result.delete(False)

from django.db.models.signals import post_save
from django.dispatch import receiver
from django.contrib.auth.models import User

@receiver(post_save, sender=User)
def create_user_profile(sender, instance, created, **kwargs):
    if created:
        UserProfile.objects.get_or_create(user=instance)

@receiver(post_save, sender=User)
def save_user_profile(sender, instance, **kwargs):
    # Use hasattr to check if the profile exists before trying to save it
    if hasattr(instance, 'profile'):
        instance.profile.save()
    else:
        # Optional: Create it now if it's missing
        UserProfile.objects.get_or_create(user=instance)

class Report(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, 
        on_delete=models.CASCADE,
        null=True, # Позволит существующим записям выжить при миграции
        blank=True
    )
    status = models.CharField(max_length=20, default='processing') # 'processing', 'ready', 'error'
    title = models.CharField(max_length=255)
    file = models.FileField(upload_to='reports/%Y/%m/%d/')
    uploaded_at = models.DateTimeField(auto_now_add=True)
    analysis_data = models.JSONField(null=True, blank=True) 
    row_count = models.IntegerField(default=0)
    uploaded_at = models.DateTimeField(auto_now_add=True)
    def __str__(self):
        return self.title