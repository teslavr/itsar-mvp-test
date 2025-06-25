# server.py
# Простейший веб-сервер на aiohttp для обслуживания Telegram Mini App
# ВЕРСИЯ 2: С явным указанием Content-Type для исправления ошибки с отображением HTML-кода

import os
from aiohttp import web
import logging

# --- КОНФИГУРАЦИЯ ---
BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', 'YOUR_TELEGRAM_BOT_TOKEN') 
PORT = int(os.getenv('PORT', 8080))

logging.basicConfig(level=logging.INFO)

# --- ОБРАБОТЧИКИ ЗАПРОСОВ ---

# Этот обработчик будет отдавать нашу главную HTML-страницу
async def handle_index(request):
    """
    Отдает главный файл index.html, который и является нашим Mini App.
    Явно указывает content_type='text/html', чтобы браузер отображал страницу, а не ее код.
    """
    logging.info("Отдаем index.html")
    try:
        # Читаем файл и возвращаем его содержимое с правильным заголовком
        with open('./index.html', 'r', encoding='utf-8') as f:
            html_content = f.read()
        return web.Response(text=html_content, content_type='text/html')
    except FileNotFoundError:
        logging.error("Критическая ошибка: Файл index.html не найден в директории.")
        return web.Response(text="Ошибка 404: Главный файл приложения не найден.", status=404)
    except Exception as e:
        logging.error(f"Неизвестная ошибка при чтении index.html: {e}")
        return web.Response(text="Внутренняя ошибка сервера.", status=500)

# --- ЗАПУСК ВЕБ-СЕРВЕРА ---

# Создаем приложение
app = web.Application()

# Добавляем маршруты (роуты)
app.router.add_get('/', handle_index)

# Функция для запуска
async def start_server():
    """
    Запускает веб-сервер.
    """
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()
    logging.info(f"Сервер iTSAR запущен на порту {PORT}")
    # В реальном приложении здесь будет бесконечный цикл
    # Для простоты прототипа мы просто ждем
    while True:
        await asyncio.sleep(3600) # Держим сервер живым

if __name__ == "__main__":
    # Установка зависимостей: pip install aiohttp
    import asyncio
    logging.info("Запуск сервера...")
    if not BOT_TOKEN or BOT_TOKEN == 'YOUR_TELEGRAM_BOT_TOKEN':
        logging.error("!!! КРИТИЧЕСКАЯ ОШИБКА: Токен бота не найден. Убедитесь, что вы добавили переменную окружения TELEGRAM_BOT_TOKEN в настройках Render.")
    else:
        logging.info(f"Токен бота успешно загружен (первые 5 символов: {BOT_TOKEN[:5]}...).")
    
    try:
        asyncio.run(start_server())
    except KeyboardInterrupt:
        logging.info("Сервер остановлен.")