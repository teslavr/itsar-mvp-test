# server.py
# ВЕРСИЯ 43: Откат к стабильной архитектуре с надежным подключением к БД

import os
import logging
import uuid
import json
import secrets
from aiohttp import web
import sqlalchemy
from sqlalchemy.dialects.postgresql import UUID
import databases

# --- КОНФИГУРАЦИЯ ---
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    logging.critical("КРИТИЧЕСКАЯ ОШИБКА: Переменная DATABASE_URL не установлена!")
    exit() # Выходим, если нет подключения к БД

database = databases.Database(DATABASE_URL)
metadata = sqlalchemy.MetaData()

# --- ОПРЕДЕЛЕНИЕ ТАБЛИЦ ---
users = sqlalchemy.Table(
    "users", metadata,
    sqlalchemy.Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
    sqlalchemy.Column("telegram_id", sqlalchemy.BigInteger, unique=True, nullable=False),
    sqlalchemy.Column("username", sqlalchemy.String, nullable=True),
    sqlalchemy.Column("first_name", sqlalchemy.String, nullable=True),
    sqlalchemy.Column("points", sqlalchemy.BigInteger, default=0, nullable=False),
    sqlalchemy.Column("has_completed_genesis", sqlalchemy.Boolean, default=False, nullable=False),
    # ... и другие поля, которые мы определили ранее
)

# --- MIDDLEWARE ДЛЯ УПРАВЛЕНИЯ ПОДКЛЮЧЕНИЕМ К БД ---
# Это самый надежный способ: перед каждым запросом мы проверяем,
# активно ли соединение, и если нет - переподключаемся.
@web.middleware
async def db_connection_middleware(request, handler):
    if not database.is_connected:
        try:
            await database.connect()
            logging.info("Установлено новое подключение к базе данных.")
        except Exception as e:
            logging.error(f"Не удалось переподключиться к БД: {e}")
            return web.json_response({'error': 'Сервис временно недоступен, попробуйте через минуту.'}, status=503)
    
    response = await handler(request)
    return response

# --- ОБРАБОТЧИКИ ЗАПРОСОВ (API) ---

async def get_user_status(request):
    try:
        telegram_id = int(request.query['telegram_id'])
        query = users.select().where(users.c.telegram_id == telegram_id)
        user = await database.fetch_one(query)
        
        if user:
            # Логика получения инвайтов и других данных будет здесь
            return web.json_response({'status': 'registered', 'points': user['points']})
        else:
            return web.json_response({'status': 'not_registered'}, status=404)
    except Exception as e:
        logging.error(f"Ошибка в get_user_status: {e}")
        return web.json_response({'error': 'Ошибка на сервере'}, status=500)

async def handle_index(request):
    try:
        with open('./index.html', 'r', encoding='utf-8') as f:
            return web.Response(text=f.read(), content_type='text/html')
    except FileNotFoundError:
        return web.Response(text="404: Not Found", status=404)

# --- УПРАВЛЕНИЕ ЖИЗНЕННЫМ ЦИКЛОМ ПРИЛОЖЕНИЯ ---
async def on_startup(app):
    try:
        engine = sqlalchemy.create_engine(DATABASE_URL)
        metadata.create_all(engine)
        await database.connect()
        logging.info("Первичное подключение к базе данных установлено.")
    except Exception as e:
        logging.critical(f"Не удалось подключиться к БД при старте: {e}")

async def on_shutdown(app):
    if database.is_connected:
        await database.disconnect()
        logging.info("Подключение к базе данных закрыто.")

# --- СБОРКА И ЗАПУСК ПРИЛОЖЕНИЯ ---
app = web.Application(middlewares=[db_connection_middleware])
app.router.add_get('/', handle_index)
app.router.add_get('/api/user/status', get_user_status)
# ... остальные роуты будут добавлены позже

app.on_startup.append(on_startup)
app.on_shutdown.append(on_shutdown)

if __name__ == "__main__":
    web.run_app(app, port=int(os.getenv("PORT", 8080)), host='0.0.0.0')
