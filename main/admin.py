
from django.contrib import admin, messages
from django.shortcuts import render, redirect
from django.urls import path
from googleapiclient.discovery import build
from django.conf import settings
from .models import Niche, Channel, FoundVideo, Report
from .forms import ImportChannelsForm, BulkDeleteChannelsForm 
@admin.register(Channel)
class ChannelAdmin(admin.ModelAdmin):
    list_display = ('title', 'user', 'niche', 'updated_at')
    list_filter = ('user', 'niche') # Поможет быстро найти каналы конкретного юзера
    search_fields = ('title', 'channel_id', 'user__username')

    change_list_template = "main/admin/channel_changelist.html"

    def get_urls(self):
        urls = super().get_urls()
        my_urls = [
            path('import-channels/', self.import_channels, name="import-channels"),
            path('bulk-delete/', self.bulk_delete_channels, name="bulk-delete"),
        ]
        return my_urls + urls

    def import_channels(self, request):
        if request.method == "POST":
            form = ImportChannelsForm(request.POST)
            if form.is_valid():
                raw_urls = form.cleaned_data['urls'].split('\n')
                youtube = build('youtube', 'v3', developerKey=settings.YT_API_KEY)
                count = 0
                for url in raw_urls:
                    url = url.strip()
                    if not url: continue
                    try:
                        res = None
                        if '@' in url:
                            handle = url.split('@')[-1].split('/')[0]
                            res = youtube.channels().list(part='id,snippet', forHandle=handle).execute()
                        elif '/channel/' in url:
                            c_id = url.split('/channel/')[-1].split('/')[0]
                            res = youtube.channels().list(part='id,snippet', id=c_id).execute()

                        if res and res.get('items'):
                            item = res['items'][0]
                            niche, _ = Niche.objects.get_or_create(name="Неразобранное")
                            Channel.objects.update_or_create(
                                channel_id=item['id'],
                                defaults={'title': item['snippet']['title'], 'niche': niche}
                            )
                            count += 1
                    except Exception as e:
                        print(f"Error: {e}")
                self.message_user(request, f"Добавлено каналов: {count}")
                return redirect("..")
        form = ImportChannelsForm()
        return render(request, "main/admin/import_channels_form.html", {"form": form})

    # МЕТОД УДАЛЕНИЯ
    def bulk_delete_channels(self, request):
        if request.method == "POST":
            form = BulkDeleteChannelsForm(request.POST)
            if form.is_valid():
                raw_urls = form.cleaned_data['urls'].split('\n')
                deleted_count = 0
                youtube = build('youtube', 'v3', developerKey=settings.YT_API_KEY)
                
                for url in raw_urls:
                    url = url.strip()
                    if not url: continue
                    try:
                        target_id = None
                        if '@' in url:
                            handle = url.split('@')[-1].split('/')[0]
                            res = youtube.channels().list(part='id', forHandle=handle).execute()
                            if res.get('items'):
                                target_id = res['items'][0]['id']
                        elif '/channel/' in url:
                            target_id = url.split('/channel/')[-1].split('/')[0]

                        if target_id:
                            deleted, _ = Channel.objects.filter(channel_id=target_id).delete()
                            deleted_count += deleted
                    except Exception as e:
                        print(f"Error: {e}")

                self.message_user(request, f"Удалено каналов: {deleted_count}", messages.WARNING)
                return redirect("..")
        form = BulkDeleteChannelsForm()
        return render(request, "main/admin/bulk_delete_form.html", {"form": form})
    
@admin.register(Niche)
class NicheAdmin(admin.ModelAdmin):
    list_display = ('name',)

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import User
from .models import UserProfile, UserNicheUpdate

# Счетчик внутри страницы пользователя
class UserNicheUpdateInline(admin.TabularInline):
    model = UserNicheUpdate
    extra = 0  # Чтобы не плодились пустые строки

# Лимит внутри страницы пользователя
class UserProfileInline(admin.StackedInline):
    model = UserProfile
    can_delete = False

admin.site.unregister(User)

@admin.register(User)
class UserAdmin(BaseUserAdmin):
    inlines = (UserProfileInline, UserNicheUpdateInline)


@admin.register(FoundVideo)
class FoundVideoAdmin(admin.ModelAdmin):
    # Столбцы, которые будут видны в списке
    
    list_display = ('title', 'get_user', 'get_niche', 'score', 'views', 'avg_at_moment', 'published_at')
    
    # Фильтры справа
    list_filter = ('channel__user', 'channel__niche', 'published_at')
    
    # Поиск по названию видео и ID канала
    search_fields = ('title', 'video_id', 'channel__channel_id')
    
    # Сортировка по умолчанию (сначала самые высокие Score)
    ordering = ('-score',)

    # Вспомогательные методы для отображения владельца и ниши
    def get_user(self, obj):
        return obj.channel.user.username
    get_user.short_description = 'Пользователь'

    def get_niche(self, obj):
        return obj.channel.niche.name
    get_niche.short_description = 'Ниша'

    @admin.register(Report)
    class ReportAdmin(admin.ModelAdmin):
        # Поля, которые будут видны в таблице списка
        list_display = ('title', 'status', 'uploaded_at', 'row_count')
        
        # Фильтры справа (удобно искать по статусу)
        list_filter = ('status', 'uploaded_at')
        
        # Поиск по названию
        search_fields = ('title',)
        
        # Поля только для чтения (чтобы случайно не сломать JSON данные в админке)
        readonly_fields = ('uploaded_at',)

        # Если ты добавил поле user (как мы обсуждали), раскомментируй строку ниже:
        # list_display = ('title', 'user', 'status', 'uploaded_at')