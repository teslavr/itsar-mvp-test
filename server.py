# server.py
# ВЕРСИЯ 5: Финальная, с расширенным логгированием

import os
import logging
import uuid
from aiohttp import web
import sqlalchemy
from sqlalchemy.dialects.postgresql import UUID
import databases

# --- КОНФИГУРАЦИЯ ---
DATABASE_URL = os.getenv("DATABASE_URL")
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") 
PORT = int(os.getenv("PORT", 8080))

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- НАСТРОЙКА БАЗЫ ДАННЫХ ---
database = None
metadata = sqlalchemy.MetaData()

if DATABASE_URL:
    try:
        database = databases.Database(DATABASE_URL)
        users = sqlalchemy.Table(
            "users",
            metadata,
            sqlalchemy.Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
            sqlalchemy.Column("telegram_id", sqlalchemy.BigInteger, unique=True, nullable=False),
            sqlalchemy.Column("username", sqlalchemy.String, nullable=True),
            sqlalchemy.Column("first_name", sqlalchemy.String, nullable=True),
            sqlalchemy.Column("points", sqlalchemy.BigInteger, default=0),
            sqlalchemy.Column("referral_code", sqlalchemy.String, unique=True, default=lambda: str(uuid.uuid4())),
            sqlalchemy.Column("invited_by_id", UUID(as_uuid=True), sqlalchemy.ForeignKey("users.id"), nullable=True),
            sqlalchemy.Column("created_at", sqlalchemy.DateTime, server_default=sqlalchemy.func.now()),
        )
    except Exception as e:
        logging.critical(f"Ошибка инициализации базы данных: {e}")
        database = None
else:
    logging.critical("Критическая ошибка: Переменная DATABASE_URL не установлена!")

# --- ОБРАБОТЧИКИ ЗАПРОСОВ (API) ---

async def get_user_status(request):
    """Проверяет, зарегистрирован ли пользователь, с детальным логгированием."""
    logging.info("API: /api/user/status вызван.")
    
    if not database:
        logging.error("API: База данных не инициализирована!")
        return web.json_response({'error': 'Сервер временно недоступен (DB init failed)'}, status=503)

    try:
        telegram_id = int(request.query['telegram_id'])
        logging.info(f"API: Проверка статуса для telegram_id: {telegram_id}")
    except (KeyError, ValueError):
        logging.warning("API: Ошибка: telegram_id не указан или некорректен.")
        return web.json_response({'error': 'telegram_id не указан или некорректен'}, status=400)

    query = users.select().where(users.c.telegram_id == telegram_id)
    
    try:
        logging.info(f"API: Выполняю запрос к БД для telegram_id: {telegram_id}...")
        user = await database.fetch_one(query)
        logging.info("API: Запрос к БД выполнен успешно.")
    except Exception as e:
        logging.error(f"API: КРИТИЧЕСКАЯ ОШИБКА при запросе к БД: {e}")
        return web.json_response({'error': 'Ошибка при обращении к базе данных'}, status=500)

    if user:
        logging.info(f"API: Пользователь {telegram_id} найден. Отправляю статус 'registered'.")
        return web.json_response({
            'status': 'registered', 'user_id': str(user['id']),
            'points': user['points'], 'referral_code': user['referral_code']
        })
    else:
        logging.info(f"API: Пользователь {telegram_id} не найден. Отправляю статус 'not_registered'.")
        return web.json_response({'status': 'not_registered'}, status=404)

async def register_user(request):
    """Регистрирует нового пользователя"""
    # ... (логика регистрации осталась прежней)
    pass

async def handle_index(request):
    """Отдает главный файл index.html"""
    try:
        with open('./index.html', 'r', encoding='utf-8') as f:
            return web.Response(text=f.read(), content_type='text/html')
    except FileNotFoundError:
        return web.Response(text="Ошибка 404: Главный файл приложения не найден.", status=404)

# --- УПРАВЛЕНИЕ ЖИЗНЕННЫМ ЦИКЛОМ ПРИЛОЖЕНИЯ ---

async def on_startup(app):
    """Выполняется при старте сервера"""
    if database:
        try:
            await database.connect()
            engine = sqlalchemy.create_engine(DATABASE_URL)
            metadata.create_all(engine)
            logging.info("Подключение к базе данных установлено и таблицы проверены.")
        except Exception as e:
            logging.critical(f"Не удалось подключиться к БД при старте: {e}")
            # Это предотвратит запуск, если БД недоступна
            app['database_connected'] = False
    else:
        app['database_connected'] = False

async def on_shutdown(app):
    """Выполняется при остановке сервера"""
    if database and database.is_connected:
        await database.disconnect()
        logging.info("Подключение к базе данных закрыто.")

# --- СБОРКА И ЗАПУСК ПРИЛОЖЕНИЯ ---

app = web.Application()
app.router.add_get('/', handle_index)
app.router.add_get('/api/user/status', get_user_status)
# ... (остальные роуты)
app.on_startup.append(on_startup)
app.on_shutdown.append(on_shutdown)

if __name__ == "__main__":
    import asyncio
    logging.info("Запуск сервера iTSAR...")
    if not BOT_TOKEN or BOT_TOKEN == 'YOUR_TELEGRAM_BOT_TOKEN':
        logging.error("КРИТИЧЕСКАЯ ОШИБКА: Токен бота не найден.")
    web.run_app(app, port=PORT, host='0.0.0.0')
