from django.urls import path
from . import views
from django.conf import settings


urlpatterns = [
    # Инструмент 1 (Главная)
    path('', views.index, name='index'),
    
    # Инструмент 2
    path('tool-2/', views.tool_2, name='tool_2'),
    
    # Инструмент 3 (Удаление фона)
    path('tool-3/', views.tool_3, name='tool_3'),
    path('tool-3/download-zip/', views.download_all_zip, name='download_all_zip'),
    path('tool-3/clear/', views.clear_history, name='clear_history'),
    
    path('tool-4/', views.tool_4, name='tool_4'),
    path('check-reports-status/', views.check_reports_status, name='check_reports_status'),
    # Ваши существующие пути для обновления ниш (из первого скрина)
    path('update/<int:niche_id>/', views.update_niche, name='update_niche'),
    path('update-all/', views.update_all, name='update_all'),

    path('import-channels/', views.import_channels_view, name='import_channels_url'),
    path('bulk-delete-channels/', views.bulk_delete_channels_view, name='bulk_delete_url'),

    path('delete-channel/<int:channel_pk>/', views.delete_single_channel, name='delete_single_channel'),

    path('niche/create/', views.create_niche, name='create_niche'),
    path('niche/delete/<int:niche_id>/', views.delete_niche, name='delete_niche')
]