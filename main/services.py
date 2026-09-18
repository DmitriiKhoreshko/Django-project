import pandas as pd
from .models import Report

def process_report(path,report_id):
    report = Report.objects.get(id=report_id)
    try:
        print(f"--- Начинаю обработку тяжелого файла (ЛИСТ 2): {path} ---")
        
        # Указываем sheet_name=1, чтобы читать ВТОРОЙ лист
        # 1. Сначала ищем заголовки в первых 100 строках второго листа
        header_search_df = pd.read_excel(path, sheet_name=1, nrows=100, header=None, engine='openpyxl')
        
        required = ['Release Artist', 'Net Payable', 'Units Sold', 'Release Title']
        header_idx = None

        for i in range(len(header_search_df)):
            # Переводим всю строку в текст и очищаем от пробелов
            row_values = [str(x).strip().lower() for x in header_search_df.iloc[i].values]
            
            # Проверяем совпадения (приводим к нижнему регистру для надежности)
            matches = sum(1 for col in required if col.lower() in row_values)
            
            if matches >= 3:
                header_idx = i
                print(f"✅ Заголовки найдены на строке {i}")
                break
        
        if header_idx is None:
            print("❌ Заголовки все еще не найдены на 2-м листе.")
            # Выведем для отладки, что вообще есть в первых строках
            print("Пример содержимого первой строки 2-го листа:", header_search_df.iloc[0].values)
            return {'summary': [], 'details': {}}

        # 2. Теперь читаем данные со второго листа, начиная с нужной строки
        # Используем usecols, чтобы не грузить лишние 30МБ колонок
        # Сначала быстро берем названия колонок
        cols_df = pd.read_excel(path, sheet_name=1, skiprows=header_idx, nrows=0, engine='openpyxl')
        all_cols = cols_df.columns.tolist()
        
        needed_cols = [c for c in all_cols if any(r.lower() in str(c).lower() for r in required) 
                       or str(c).lower() in ['vendor', 'country code', 'store', 'country']]

        print(f"Загружаю данные из колонок: {needed_cols}...")
        
        df = pd.read_excel(path, sheet_name=1, skiprows=header_idx, engine='openpyxl')

        # НОРМАЛИЗАЦИЯ: убираем лишние пробелы внутри имен
        # 'fenekot  tvoy' превратится в 'fenekot tvoy'
        df['Release Artist'] = df['Release Artist'].astype(str).replace(r'\s+', ' ', regex=True).str.strip()
        df['Release Title'] = df['Release Title'].astype(str).str.strip()

        # Приведение типов для суммирования
        df['Net Payable'] = pd.to_numeric(df['Net Payable'], errors='coerce').fillna(0)
        df['Units Sold'] = pd.to_numeric(df['Units Sold'], errors='coerce').fillna(0)

        # Группировка для главной таблицы
        summary_df = df.groupby('Release Artist').agg({
            'Net Payable': 'sum',
            'Units Sold': 'sum',
            'Release Title': 'nunique'
        }).reset_index()
        
        # Ключи в summary_df['Release Artist'] теперь на 100% совпадают с исходным файлом
        summary_df.columns = ['Release_Artist', 'Net_Payable', 'Units_Sold', 'Release_Title']
        
        details = {}
        for _, row in summary_df.iterrows():
            full_name = row['Release_Artist']
            # Фильтруем строго по полному имени
            artist_df = df[df['Release Artist'] == full_name]
            
            details[full_name] = {
                'by_vendor': artist_df.groupby('Vendor')['Net Payable'].sum().to_dict(),
                'by_country': artist_df.groupby('Country Code')['Net Payable'].sum().nlargest(5).to_dict(),
                'top_tracks': artist_df.groupby('Release Title')['Net Payable'].sum().nlargest(5).to_dict()
            }
        # Формируем итоговый результат
        final_data = {
            'summary': summary_df.to_dict('records'),
            'details': details
        }

        # 1. Сохраняем данные (Django автоматически преобразует дикт в JSON, если поле JSONField)
        report.analysis_data = final_data 
        
        # 2. МЕНЯЕМ СТАТУС НА ГОТОВО
        report.status = 'ready' 
        
        # 3. Указываем количество строк для красоты в админке
        report.row_count = len(df)
        
        report.save()
        print(f"✅ Анализ отчета {report_id} успешно завершен!")
        
        return final_data

    except Exception as e:
        print(f"❌ Ошибка в процессе анализа: {e}")
        # Если произошла ошибка, помечаем отчет, чтобы он не висел в "processing" вечно
        try:
            error_report = Report.objects.get(id=report_id)
            error_report.status = 'error'
            error_report.save()
        except:
            pass
        return {'summary': [], 'details': {}}