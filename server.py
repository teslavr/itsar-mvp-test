# server.py
# ВЕРСИЯ 9: Добавлена логика для анкеты (без сохранения в БД)

import os
import logging
import uuid
import json
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
            "users", metadata,
            sqlalchemy.Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
            sqlalchemy.Column("telegram_id", sqlalchemy.BigInteger, unique=True, nullable=False),
            sqlalchemy.Column("username", sqlalchemy.String, nullable=True),
            sqlalchemy.Column("first_name", sqlalchemy.String, nullable=True),
            sqlalchemy.Column("points", sqlalchemy.BigInteger, default=0),
            sqlalchemy.Column("referral_code", sqlalchemy.String, unique=True, default=lambda: str(uuid.uuid4())),
            sqlalchemy.Column("invited_by_id", UUID(as_uuid=True), sqlalchemy.ForeignKey("users.id"), nullable=True),
            sqlalchemy.Column("created_at", sqlalchemy.DateTime, server_default=sqlalchemy.func.now()),
        )

        # Заготовка для будущих таблиц
        questions = sqlalchemy.Table(
            "questions", metadata,
            sqlalchemy.Column("id", sqlalchemy.Integer, primary_key=True, autoincrement=True),
            sqlalchemy.Column("text", sqlalchemy.String, nullable=False),
            sqlalchemy.Column("category", sqlalchemy.String, nullable=False),
            sqlalchemy.Column("options", sqlalchemy.JSON, nullable=True), # для вопросов с вариантами
        )

        answers = sqlalchemy.Table(
            "answers", metadata,
            sqlalchemy.Column("id", sqlalchemy.Integer, primary_key=True, autoincrement=True),
            sqlalchemy.Column("user_id", UUID(as_uuid=True), sqlalchemy.ForeignKey("users.id"), nullable=False),
            sqlalchemy.Column("question_id", sqlalchemy.Integer, sqlalchemy.ForeignKey("questions.id"), nullable=False),
            sqlalchemy.Column("answer_text", sqlalchemy.String, nullable=False),
            sqlalchemy.Column("answered_at", sqlalchemy.DateTime, server_default=sqlalchemy.func.now()),
        )

    except Exception as e:
        logging.critical(f"Ошибка инициализации базы данных: {e}")
        database = None
else:
    logging.critical("Критическая ошибка: Переменная DATABASE_URL не установлена!")


# --- "ЗАГЛУШКА" С ВОПРОСАМИ ДЛЯ MVP ---
GENESIS_QUESTIONS = [
    {
        "id": 1, "category": "Демография", "text": "Ваш возрастной диапазон?",
        "options": ["18-24", "25-34", "35-44", "45-54", "55+"]
    },
    {
        "id": 2, "category": "Демография", "text": "Ваш пол?",
        "options": ["Мужской", "Женский"]
    },
    {
        "id": 3, "category": "Демография", "text": "Ваше образование?",
        "options": ["Среднее", "Среднее специальное", "Неоконченное высшее", "Высшее", "Ученая степень"]
    },
    {
        "id": 4, "category": "Интересы", "text": "Как вы предпочитаете проводить свободный вечер?",
        "options": ["Просмотр фильмов/сериалов", "Чтение книг", "Видеоигры", "Встреча с друзьями", "Хобби/творчество"]
    },
    {
        "id": 5, "category": "Потребительские привычки", "text": "Какой операционной системой на смартфоне вы пользуетесь?",
        "options": ["iOS", "Android"]
    }
]


# --- ОБРАБОТЧИКИ ЗАПРОСОВ (API) ---

async def get_genesis_questions(request):
    """Отдает стартовый набор вопросов"""
    logging.info("API: /api/genesis_questions вызван.")
    return web.json_response(GENESIS_QUESTIONS)


async def submit_answers(request):
    """Принимает ответы от пользователя"""
    try:
        data = await request.json()
        user_id = data.get('user_id')
        answers = data.get('answers')
        logging.info(f"API: /api/submit_answers вызван. Получены ответы от user_id: {user_id}")
        # Просто логируем ответы для проверки
        logging.info(json.dumps(answers, indent=2, ensure_ascii=False))
        # TODO: На следующем шаге здесь будет логика сохранения в БД и начисления очков
        return web.json_response({'status': 'success', 'message': 'Ответы получены!'})
    except Exception as e:
        logging.error(f"Ошибка при обработке ответов: {e}")
        return web.json_response({'error': 'Ошибка на сервере'}, status=500)


# ... (остальные обработчики: get_user_status, register_user, handle_index)
async def get_user_status(request):
    logging.info("API: /api/user/status вызван.")
    if not database or not app.get('database_connected'): return web.json_response({'error': 'DB connection failed'}, status=503)
    try:
        telegram_id = int(request.query['telegram_id'])
    except (KeyError, ValueError): return web.json_response({'error': 'telegram_id не указан'}, status=400)
    query = users.select().where(users.c.telegram_id == telegram_id)
    try:
        user = await database.fetch_one(query)
    except Exception as e: return web.json_response({'error': 'Ошибка при обращении к БД'}, status=500)
    if user:
        return web.json_response({'status': 'registered', 'user_id': str(user['id']), 'points': user['points'], 'referral_code': user['referral_code']})
    else:
        return web.json_response({'status': 'not_registered'}, status=404)

async def register_user(request):
    logging.info("API: /api/register вызван.")
    if not database or not app.get('database_connected'): return web.json_response({'error': 'DB connection failed'}, status=503)
    try:
        data = await request.json()
        telegram_id, username, first_name, inviter_code = data['telegram_id'], data.get('username'), data.get('first_name'), data.get('inviter_code')
    except Exception: return web.json_response({'error': 'Некорректные данные'}, status=400)
    if await database.fetch_one(users.select().where(users.c.telegram_id == telegram_id)): return web.json_response({'error': 'Пользователь уже зарегистрирован'}, status=409)
    inviter_id = None
    if inviter_code:
        inviter = await database.fetch_one(users.select().where(users.c.referral_code == inviter_code))
        if inviter: inviter_id = inviter['id']
    try:
        new_user_id, new_referral_code = uuid.uuid4(), str(uuid.uuid4())
        await database.execute(users.insert().values(id=new_user_id, telegram_id=telegram_id, username=username, first_name=first_name, points=1000, referral_code=new_referral_code, invited_by_id=inviter_id))
        logging.info(f"Зарегистрирован новый пользователь: tg_id={telegram_id}")
    except Exception as e: return web.json_response({'error': 'Ошибка при записи в БД'}, status=500)
    return web.json_response({'status': 'success'}, status=201)

async def handle_index(request):
    try:
        with open('./index.html', 'r', encoding='utf-8') as f: return web.Response(text=f.read(), content_type='text/html')
    except FileNotFoundError: return web.Response(text="404: Not Found", status=404)


# --- УПРАВЛЕНИЕ ЖИЗНЕННЫМ ЦИКЛОМ ПРИЛОЖЕНИЯ ---
async def on_startup(app):
    app['database_connected'] = False
    if database:
        try:
            await database.connect()
            engine = sqlalchemy.create_engine(DATABASE_URL)
            metadata.create_all(engine)
            logging.info("Подключение к базе данных установлено.")
            app['database_connected'] = True
        except Exception as e:
            logging.critical(f"Не удалось подключиться к БД при старте: {e}")

async def on_shutdown(app):
    if database and database.is_connected:
        await database.disconnect()
        logging.info("Подключение к базе данных закрыто.")

# --- СБОРКА И ЗАПУСК ПРИЛОЖЕНИЯ ---
app = web.Application()
app.router.add_get('/', handle_index)
app.router.add_get('/api/user/status', get_user_status)
app.router.add_post('/api/register', register_user)
# Новые маршруты
app.router.add_get('/api/genesis_questions', get_genesis_questions)
app.router.add_post('/api/submit_answers', submit_answers)

app.on_startup.append(on_startup)
app.on_shutdown.append(on_shutdown)

if __name__ == "__main__":
    web.run_app(app, port=PORT, host='0.0.0.0')
