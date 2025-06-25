# server.py
# ВЕРСИЯ 3: Добавлена база данных PostgreSQL и API для регистрации

import os
import logging
import uuid
from aiohttp import web
import sqlalchemy
from sqlalchemy.dialects.postgresql import UUID
import databases

# --- КОНФИГУРАЦИЯ ---
# Переменные окружения, которые мы настраиваем в Render
DATABASE_URL = os.getenv("DATABASE_URL")
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") 
PORT = int(os.getenv("PORT", 8080))

logging.basicConfig(level=logging.INFO)

# --- НАСТРОЙКА БАЗЫ ДАННЫХ ---
if not DATABASE_URL:
    logging.error("Критическая ошибка: Переменная DATABASE_URL не установлена!")
    # В реальном приложении здесь был бы выход, для простоты оставляем работать
    database = None
    metadata = None
else:
    database = databases.Database(DATABASE_URL)
    metadata = sqlalchemy.MetaData()

    # Определяем структуру таблицы пользователей
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

# --- ОБРАБОТЧИКИ ЗАПРОСОВ (API) ---

async def get_user_status(request):
    """Проверяет, зарегистрирован ли пользователь"""
    try:
        telegram_id = int(request.query['telegram_id'])
    except (KeyError, ValueError):
        return web.json_response({'error': 'telegram_id не указан или некорректен'}, status=400)

    query = users.select().where(users.c.telegram_id == telegram_id)
    user = await database.fetch_one(query)

    if user:
        return web.json_response({
            'status': 'registered',
            'user_id': str(user['id']),
            'points': user['points'],
            'referral_code': user['referral_code']
        })
    else:
        return web.json_response({'status': 'not_registered'}, status=404)


async def register_user(request):
    """Регистрирует нового пользователя по инвайт-коду"""
    try:
        data = await request.json()
        telegram_id = data['telegram_id']
        username = data.get('username')
        first_name = data.get('first_name')
        inviter_code = data.get('inviter_code')
    except (KeyError, ValueError):
        return web.json_response({'error': 'Некорректные данные запроса'}, status=400)

    # 1. Проверяем, не зарегистрирован ли пользователь уже
    existing_user_query = users.select().where(users.c.telegram_id == telegram_id)
    if await database.fetch_one(existing_user_query):
        return web.json_response({'error': 'Пользователь уже зарегистрирован'}, status=409)

    # 2. Проверяем инвайт-код
    inviter_id = None
    if inviter_code:
        inviter_query = users.select().where(users.c.referral_code == inviter_code)
        inviter = await database.fetch_one(inviter_query)
        if not inviter:
            # Для MVP прощаем отсутствие инвайт-кода, чтобы можно было запустить систему
            # В боевой версии здесь будет ошибка 'Инвайт-код не найден'
            logging.warning(f"Инвайт-код '{inviter_code}' не найден. Регистрируем пользователя без инвайта.")
        else:
            inviter_id = inviter['id']
    else:
        # Для MVP разрешаем регистрацию без инвайта для первого пользователя
        logging.info("Регистрация без инвайт-кода.")

    # 3. Создаем нового пользователя
    insert_query = users.insert().values(
        telegram_id=telegram_id,
        username=username,
        first_name=first_name,
        points=1000,  # Начальные очки за регистрацию
        invited_by_id=inviter_id
    )
    user_id = await database.execute(insert_query)
    logging.info(f"Зарегистрирован новый пользователь: tg_id={telegram_id}, id={user_id}")
    
    # 4. Если был инвайтер, можно начислить ему бонус (логика будет усложняться)
    # ... (добавим позже)
    
    return web.json_response({'status': 'success', 'message': 'Пользователь успешно зарегистрирован'}, status=201)


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
        await database.connect()
        # Создаем таблицу, если она не существует
        engine = sqlalchemy.create_engine(DATABASE_URL)
        metadata.create_all(engine)
        logging.info("Подключение к базе данных установлено и таблицы проверены.")

async def on_shutdown(app):
    """Выполняется при остановке сервера"""
    if database:
        await database.disconnect()
        logging.info("Подключение к базе данных закрыто.")


# --- СБОРКА И ЗАПУСК ПРИЛОЖЕНИЯ ---

app = web.Application()

# Добавляем маршруты
app.router.add_get('/', handle_index)
app.router.add_get('/api/user/status', get_user_status)
app.router.add_post('/api/register', register_user)

# Привязываем функции жизненного цикла
app.on_startup.append(on_startup)
app.on_shutdown.append(on_shutdown)

if __name__ == "__main__":
    import asyncio
    web.run_app(app, port=PORT)