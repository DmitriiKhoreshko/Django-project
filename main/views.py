import isodate
from datetime import datetime, timedelta
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.utils import timezone
from django.conf import settings
from django.http import JsonResponse
from googleapiclient.discovery import build
from django.core.files.base import ContentFile
from .models import Niche, Channel, FoundVideo, BackgroundRemovalTask, UserNicheUpdate, UserProfile
import io
from PIL import Image
from rembg import remove, new_session
import threading
import zipfile
from django.http import HttpResponse
import os
from io import BytesIO
from concurrent.futures import ThreadPoolExecutor
from django.db import connection
from django.contrib import messages
from .services import process_report
# Вспомогательная функция для логики API (не является View)
import isodate
from datetime import timedelta
from django.utils import timezone
from googleapiclient.discovery import build
from django.conf import settings
from .models import FoundVideo, Channel, Report

def perform_full_update(channels_queryset):
    youtube = build('youtube', 'v3', developerKey=settings.YT_API_KEY)
    now_aware = timezone.now()
    
    # Берем уникальные ID каналов, чтобы не тратить квоту API дважды
    unique_channel_ids = channels_queryset.values_list('channel_id', flat=True).distinct()

    for yt_id in unique_channel_ids:
        try:
            # 1. Получаем ID плейлиста загрузок
            ch_res = youtube.channels().list(part='contentDetails', id=yt_id).execute()
            if not ch_res.get('items'):
                continue
            
            uploads_id = ch_res['items'][0]['contentDetails']['relatedPlaylists']['uploads']

            # 2. Получаем последние 50 айтемов из плейлиста
            try:
                pl_res = youtube.playlistItems().list(
                    part='contentDetails', 
                    playlistId=uploads_id, 
                    maxResults=100
                ).execute()
            except Exception as e:
                print(f"Плейлист канала {yt_id} недоступен (404/Private): {e}")
                continue

            v_ids = [item['contentDetails']['videoId'] for item in pl_res.get('items', [])]
            if not v_ids:
                continue

            # 3. Получаем детальную статистику и длительность для всех 50 видео
            v_res = youtube.videos().list(
                part='snippet,statistics,contentDetails', 
                id=','.join(v_ids)
            ).execute()

            video_items = v_res.get('items', [])

            # 4. ПРИНУДИТЕЛЬНАЯ СОРТИРОВКА ПО ДАТЕ (от новых к старым)
            # Это исключает попадание "всплывших" старых видео в начало списка
            video_items_sorted = sorted(
                video_items, 
                key=lambda x: isodate.parse_datetime(x['snippet']['publishedAt']), 
                reverse=True
            )

            # 5. РАСПРЕДЕЛЕНИЕ ПО КАТЕГОРИЯМ
            shorts_pool = []
            long_pool = []

            for v in video_items_sorted:
                duration_sec = isodate.parse_duration(v['contentDetails']['duration']).total_seconds()
                views = int(v['statistics'].get('viewCount', 0))
                pub_date = isodate.parse_datetime(v['snippet']['publishedAt'])
                
                v_data = {
                    'v': v,
                    'views': views,
                    'pub_date': pub_date
                }

                is_shorts = False
                if duration_sec <= 60.5:
                    is_shorts = True
                elif duration_sec <= 181:
                    # Проверяем хэштег как дополнительный маркер, 
                    # но даже без него относим к Shorts, чтобы не портить 
                    # среднее значение "длинных" видео гигантскими охватами шортсов.
                    is_shorts = True 
                else:
                    is_shorts = False

                if is_shorts:
                    shorts_pool.append(v_data)
                else:
                    long_pool.append(v_data)

            # 6. РАСЧЕТ СРЕДНИХ ПРОСМОТРОВ (берем видео со 2-го по 31-е)
            def calculate_avg(pool):
                if len(pool) < 2: return 0
                subset = pool[1:31] # Пропускаем самое свежее для честного score
                return sum(item['views'] for item in subset) // len(subset)

            avg_shorts = calculate_avg(shorts_pool)
            avg_long = calculate_avg(long_pool)

            # 7. ОБНОВЛЕНИЕ ВСЕХ КОПИЙ КАНАЛА В БД
            target_channels = channels_queryset.filter(channel_id=yt_id)
            target_channels.update(updated_at=now_aware, average_views=avg_shorts)

            # Очищаем старые найденные видео для этих каналов
            FoundVideo.objects.filter(channel__in=target_channels).delete()
            
            videos_to_create = []

            for ch_obj in target_channels:
                # Берем последние 10 видео каждой категории из отсортированного пула
                recent_shorts = shorts_pool[:10]
                recent_long = long_pool[:10]

                # Считаем Score для каждого кандидата
                shorts_candidates = []
                for item in recent_shorts:
                    score = round(item['views'] / avg_shorts, 2) if avg_shorts > 0 else 0
                    shorts_candidates.append({'item': item, 'score': score, 'type': 'shorts', 'avg': avg_shorts})

                long_candidates = []
                for item in recent_long:
                    score = round(item['views'] / avg_long, 2) if avg_long > 0 else 0
                    long_candidates.append({'item': item, 'score': score, 'type': 'long', 'avg': avg_long})

                # Выбираем ТОП-3 по SCORE для каждой категории
                top_shorts = sorted(shorts_candidates, key=lambda x: x['score'], reverse=True)[:3]
                top_long = sorted(long_candidates, key=lambda x: x['score'], reverse=True)[:3]

                # Формируем объекты для bulk_create
                for entry in (top_shorts + top_long):
                    v_meta = entry['item']['v']
                    videos_to_create.append(FoundVideo(
                        channel=ch_obj,
                        video_id=v_meta['id'],
                        title=v_meta['snippet']['title'],
                        views=entry['item']['views'],
                        score=entry['score'],
                        video_type=entry['type'],
                        avg_at_moment=entry['avg'],
                        published_at=entry['item']['pub_date'],
                        thumbnail=v_meta['snippet']['thumbnails']['medium']['url']
                    ))

            # 8. МАССОВАЯ ЗАПИСЬ
            if videos_to_create:
                FoundVideo.objects.bulk_create(videos_to_create)

            print(f"Канал {yt_id}: успешно обработан (Shorts avg: {avg_shorts}, Long avg: {avg_long})")

        except Exception as e:
            print(f"Ошибка при обработке канала {yt_id}: {str(e)}")

# --- VIEW FUNCTIONS ---
session = new_session("u2net")
executor = ThreadPoolExecutor(max_workers=8)

def process_bg_removal(task_id):
    task = None
    try:
        # Получаем задачу
        task = BackgroundRemovalTask.objects.get(id=task_id)
        task.status = 'processing'
        task.save()

        # Открываем изображение
        with Image.open(task.image.path) as input_image:
            # Если изображение в RGBA, rembg отработает лучше, если в RGB — сконвертирует
            output_image = remove(input_image, session=session)
            
            img_io = io.BytesIO()
            # Сохраняем результат в формате PNG (так как нужен прозрачный фон)
            output_image.save(img_io, format='PNG', optimize=True)
            
            # Формируем имя файла результата
            original_filename = os.path.basename(task.image.name)
            base_name = os.path.splitext(original_filename)[0]
            result_filename = f"{base_name}_no_bg.png" 

            # Сохраняем результат в поле ImageField
            # ContentFile превращает байты из BytesIO в файл, который понимает Django
            task.result.save(result_filename, ContentFile(img_io.getvalue()), save=False)
            task.status = 'completed'

    except Exception as e:
        print(f"Ошибка при обработке задачи {task_id}: {e}")
        if task:
            task.status = 'error'
    
    finally:
        if task:
            task.save()
        # Важно закрыть соединение, если функция работает в отдельном потоке (threading)
        connection.close()

@login_required
def index(request):
    # 1. Инициализируем переменные заранее (защита от UnboundLocalError)
    niches = Niche.objects.all()
    results = []
    my_channels = []
    current_limit_count = 0 
    
    # Получаем параметры из URL
    selected_niche_id = request.GET.get('niche')
    video_type = request.GET.get('type', 'shorts') 
    
    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    is_admin = request.user.is_staff

    # 2. Проверяем валидность ID ниши (не None, не пустая строка и не текст 'None')
    if selected_niche_id and selected_niche_id not in ['None', '']:
        try:
            # Превращаем в число для проверки, что это валидный ID
            niche_id_int = int(selected_niche_id)
            
            # Получаем видео
            results_queryset = FoundVideo.objects.filter(
                channel__niche_id=niche_id_int,
                channel__user=request.user,
                video_type=video_type
            ).select_related('channel').order_by('-score')
            
            # Пагинация
            paginator = Paginator(results_queryset, 20)
            page_number = request.GET.get('page')
            results = paginator.get_page(page_number)
            
            # Каналы для модалки
            my_channels = Channel.objects.filter(
                user=request.user, 
                niche_id=niche_id_int
            ).order_by('title')

            # Лимиты
            limit_record = UserNicheUpdate.objects.filter(
                user=request.user, 
                niche_id=niche_id_int
            ).first()
            
            if limit_record:
                current_limit_count = limit_record.count
                
        except (ValueError, TypeError):
            # Если в ID попал мусор (например ?niche=abc), сбрасываем ID
            selected_niche_id = None

    return render(request, 'main/index.html', {
        'niches': niches,
        'results': results,
        'my_channels': my_channels,
        'selected_niche': selected_niche_id,
        'current_limit_count': current_limit_count,
        'max_limit': profile.niche_limit,
        'is_admin': is_admin,
        'current_type': video_type,
    })

@login_required
def update_niche(request, niche_id):
    niche = get_object_or_404(Niche, id=niche_id)
    
    # Получаем каналы текущего пользователя
    user_channels = Channel.objects.filter(niche=niche, user=request.user)
    
    if not user_channels.exists():
        messages.warning(request, "У вас нет добавленных каналов в этой нише.")
        return redirect(f'/?niche={niche_id}')

    # Работа с лимитами
    profile = request.user.profile
    limit_record, _ = UserNicheUpdate.objects.get_or_create(user=request.user, niche=niche)
    
    # --- ИСПРАВЛЕНИЕ: Добавляем "if not request.user.is_staff" ---
    if not request.user.is_staff: 
        if limit_record.count >= profile.niche_limit:
            messages.error(request, f"Лимит обновлений для этой ниши ({profile.niche_limit}) исчерпан.")
            return redirect(f'/?niche={niche_id}')

    # Если мы здесь, значит это либо админ, либо лимит не исчерпан
    perform_full_update(user_channels)
    
    # Увеличиваем счетчик (админу тоже можно записывать статистику, но это не заблокирует его)
    limit_record.count += 1
    limit_record.save()
    
    messages.success(request, f"Ниша {niche.name} успешно обновлена.")
    return redirect(f'/?niche={niche_id}')

@login_required
def update_all(request):
    channels = Channel.objects.all()
    perform_full_update(channels)
    return redirect('index')

@login_required
def tool_2(request):
    return render(request, 'main/tool_2.html')

from django.core.paginator import Paginator

@login_required
def tool_3(request):
    if request.method == 'POST':
        images = request.FILES.getlist('images')
        if images:
            for img in images:
                task = BackgroundRemovalTask.objects.create(
                    image=img,
                    user=request.user,
                    )
                # Вместо threading.Thread используем наш executor
                executor.submit(process_bg_removal, task.id)
            return redirect('tool_3')

    all_user_tasks = BackgroundRemovalTask.objects.filter(user=request.user).order_by('-created_at')
    
    # 1. Считаем общее количество завершенных задач для архива
    total_completed_count = all_user_tasks.filter(status='completed').count()
    
    # 2. Проверка, идет ли еще процесс обработки (для показа кнопки)
    any_unfinished = all_user_tasks.filter(status__in=['pending', 'processing']).exists()
    
    # 3. Пагинация для отображения плиток
    paginator = Paginator(all_user_tasks, 40)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    return render(request, 'main/tool_3.html', {
        'tasks': page_obj,
        'total_completed': total_completed_count, # Новая переменная
        'all_tasks_completed': not any_unfinished and all_user_tasks.exists()
    })

@login_required
def download_all_zip(request):
    # 1. Берем ВЕХ завершенные задачи пользователя (без пагинации!)
    # Если нужно скачать вообще всё (даже без результата), уберите фильтр status
    tasks = BackgroundRemovalTask.objects.filter(status='completed', user=request.user) 

    if not tasks.exists():
        return HttpResponse("Нет готовых файлов для скачивания", status=404)

    # 2. Создаем архив в памяти
    byte_data = BytesIO()
    with zipfile.ZipFile(byte_data, 'w') as zip_file:
        for task in tasks:
            if task.result: # Проверяем, что файл результата существует
                file_path = task.result.path
                file_name = os.path.basename(file_path)
                zip_file.write(file_path, file_name)

    # 3. Отправляем пользователю
    response = HttpResponse(byte_data.getvalue(), content_type='application/zip')
    response['Content-Disposition'] = 'attachment; filename="all_processed_images.zip"'
    return response

@login_required
def clear_history(request):
    # Просто удаляем записи из базы. 
    # Благодаря вашему сигналу post_delete в models.py, 
    # файлы с диска удалятся автоматически!
    BackgroundRemovalTask.objects.filter(user=request.user).delete()
    
    return redirect('tool_3')

# Массовый импорт каналов и массовое удаление каналов

from .forms import ImportChannelsForm, BulkDeleteChannelsForm

@login_required
def import_channels_view(request):
    if request.method == "POST":
        form = ImportChannelsForm(request.POST)
        if form.is_valid():
            # Получаем ID ниши из формы или URL, чтобы не все шло в "Неразобранное"
            niche_id = request.POST.get('niche_id')
            niche = get_object_or_404(Niche, id=niche_id) if niche_id else None
            if not niche:
                niche, _ = Niche.objects.get_or_create(name="Неразобранное")

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
                        # ВАЖНО: Добавляем user=request.user
                        Channel.objects.update_or_create(
                            channel_id=item['id'],
                            user=request.user, # Привязка к текущему юзеру
                            defaults={
                                'title': item['snippet']['title'], 
                                'niche': niche
                            }
                        )
                        count += 1
                except Exception as e:
                    print(f"Error importing {url}: {e}")
            
            messages.success(request, f"Успешно добавлено каналов: {count}")
            return redirect(f'/?niche={niche.id}')
    
    return redirect('/')

@login_required
def bulk_delete_channels_view(request):
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
                        # ВАЖНО: Удаляем только каналы ТЕКУЩЕГО пользователя
                        deleted, _ = Channel.objects.filter(
                            channel_id=target_id, 
                            user=request.user
                        ).delete()
                        deleted_count += deleted
                except Exception as e:
                    print(f"Error deleting {url}: {e}")

            messages.warning(request, f"Удалено ваших каналов: {deleted_count}")
    
    return redirect(request.META.get('HTTP_REFERER', '/'))

@login_required
def delete_single_channel(request, channel_pk):
    # Ищем канал по первичному ключу (pk) и проверяем владельца
    channel = get_object_or_404(Channel, pk=channel_pk, user=request.user)
    niche_id = channel.niche_id
    channel_title = channel.title or channel.channel_id
    
    channel.delete()
    
    messages.success(request, f"Канал '{channel_title}' удален из вашего списка.")
    return redirect(f'/?niche={niche_id}')

@login_required
def create_niche(request):
    # Проверяем наличие права 'can_add_niche' у пользователя
    if not request.user.has_perm('main.can_add_niche'): 
        messages.error(request, "У вас нет специального разрешения на создание ниш.")
        return redirect('index')

    if request.method == 'POST':
        name = request.POST.get('name')
        if name:
            Niche.objects.create(name=name)
            messages.success(request, f"Ниша '{name}' создана!")
    return redirect('index')

@login_required
def delete_niche(request, niche_id):
    if not request.user.is_staff:
        messages.error(request, "Только администратор может удалять ниши.")
        return redirect('index')

    niche = get_object_or_404(Niche, id=niche_id)
    niche_name = niche.name
    niche.delete()
    
    messages.success(request, f"Ниша '{niche_name}' была удалена.")
    return redirect('index')


import openpyxl
import pandas as pd

@login_required
def tool_4(request):
    reports = Report.objects.filter(user=request.user).order_by('-uploaded_at')
    active_report = None
    data = []
    details = {}
    total_income = 0
    total_units = 0
    total_releases = 0

    # --- 1. ЛОГИКА GET (Удаление) ---
    if 'delete' in request.GET:
        delete_id = request.GET.get('delete')
        report_to_delete = get_object_or_404(Report, id=delete_id, user=request.user)
        if report_to_delete.file:
            report_to_delete.file.delete(save=False)
        report_to_delete.delete()
        return redirect('tool_4')

    # --- 2. ЛОГИКА POST ---
    if request.method == 'POST':
        # Вариант А: Переименование
        if 'rename_id' in request.POST:
            rename_id = request.POST.get('rename_id')
            new_title = request.POST.get('new_title')
            report = get_object_or_404(Report, id=rename_id, user=request.user)
            if new_title:
                report.title = new_title
                report.save()
            return redirect('tool_4')
        
        # Вариант Б: Загрузка нового файла (AJAX)
        elif 'report_file' in request.FILES:
            file = request.FILES.get('report_file')
            # Создаем запись со статусом processing
            report = Report.objects.create(
                user=request.user, 
                title=file.name, 
                file=file,
                status='processing' 
            )

            # ЗАПУСКАЕМ В ФОНЕ
            # Передаем путь к файлу и ID отчета
            thread = threading.Thread(
                target=process_report, 
                args=(report.file.path, report.id)
            )
            thread.start()

            # Если это AJAX запрос (от нашего нового скрипта), возвращаем JSON
            if request.headers.get('x-requested-with') == 'XMLHttpRequest':
                return JsonResponse({'status': 'processing', 'report_id': report.id})
            
            # Если обычная отправка формы — просто редирект, отчет уже будет в списке со статусом
            return redirect('tool_4')

    # --- 3. ЛОГИКА ПРОСМОТРА ---
    report_id = request.GET.get('view')
    if report_id:
        active_report = Report.objects.filter(id=report_id, user=request.user).first()
        
        # Блокируем просмотр, если еще идет анализ
        if active_report and active_report.status == 'processing':
            # Можно добавить message, что отчет еще не готов
            pass
        elif active_report and active_report.analysis_data:
            data = active_report.analysis_data.get('summary', [])
            details = active_report.analysis_data.get('details', {})
            
            total_income = sum(float(item.get('Net_Payable', 0) or 0) for item in data)
            total_units = sum(float(item.get('Units_Sold', 0) or 0) for item in data)
            total_releases = sum(int(item.get('Release_Title', 0) or 0) for item in data)

    return render(request, 'main/tool_4.html', {
        'reports': reports,
        'data': data,
        'details': details,
        'active_report': active_report,
        'total_income': total_income,
        'total_units': total_units,
        'total_releases': total_releases,
    })

def check_reports_status(request):
    # Проверяем, есть ли у пользователя отчеты со статусом 'processing'
    # Если ты еще не добавил поле user, используй Report.objects.filter...
    any_processing = Report.objects.filter(status='processing').exists()
    
    # Возвращаем результат: если обрабатываемых нет (False), 
    # значит всё закончилось и можно обновлять страницу.
    return JsonResponse({
        'any_processing': any_processing,
        'refresh_needed': not any_processing 
    })