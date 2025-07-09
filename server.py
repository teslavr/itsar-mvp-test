# server.py
# ВЕРСИЯ 29: Финальная, полная, исправленная версия

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

# --- ЗАГРУЗКА ВОПРОСОВ ИЗ ФАЙЛА ---
def load_questions_from_file():
    try:
        with open('questions.json', 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        logging.error(f"Критическая ошибка при чтении questions.json: {e}")
        return []

GENESIS_QUESTIONS = load_questions_from_file()

# --- НАСТРОЙКА БАЗЫ ДАННЫХ И ТАБЛИЦ ---
database = None
metadata = sqlalchemy.MetaData()

users = sqlalchemy.Table(
    "users", metadata,
    sqlalchemy.Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
    sqlalchemy.Column("telegram_id", sqlalchemy.BigInteger, unique=True, nullable=False),
    sqlalchemy.Column("username", sqlalchemy.String, nullable=True),
    sqlalchemy.Column("first_name", sqlalchemy.String, nullable=True),
    sqlalchemy.Column("points", sqlalchemy.BigInteger, default=0),
    sqlalchemy.Column("referral_code", sqlalchemy.String, unique=True, default=lambda: str(uuid.uuid4())),
    sqlalchemy.Column("invited_by_id", UUID(as_uuid=True), sqlalchemy.ForeignKey("users.id"), nullable=True),
    sqlalchemy.Column("has_completed_genesis", sqlalchemy.Boolean, default=False, nullable=False),
    sqlalchemy.Column("is_searchable", sqlalchemy.Boolean, default=True, nullable=False),
    sqlalchemy.Column("invites_left", sqlalchemy.Integer, default=5, nullable=False),
    sqlalchemy.Column("created_at", sqlalchemy.DateTime, server_default=sqlalchemy.func.now()),
)

questions = sqlalchemy.Table(
    "questions", metadata,
    sqlalchemy.Column("id", sqlalchemy.Integer, primary_key=True, autoincrement=False),
    sqlalchemy.Column("text", sqlalchemy.String, nullable=False),
    sqlalchemy.Column("category", sqlalchemy.String, nullable=False),
    sqlalchemy.Column("options", sqlalchemy.JSON, nullable=True),
)

answers = sqlalchemy.Table(
    "answers", metadata,
    sqlalchemy.Column("id", sqlalchemy.Integer, primary_key=True, autoincrement=True),
    sqlalchemy.Column("user_id", UUID(as_uuid=True), sqlalchemy.ForeignKey("users.id"), nullable=False),
    sqlalchemy.Column("question_id", sqlalchemy.Integer, sqlalchemy.ForeignKey("questions.id"), nullable=False),
    sqlalchemy.Column("answer_text", sqlalchemy.String, nullable=False),
    sqlalchemy.Column("answered_at", sqlalchemy.DateTime, server_default=sqlalchemy.func.now()),
)

if DATABASE_URL:
    database = databases.Database(DATABASE_URL)
else:
    logging.critical("Критическая ошибка: Переменная DATABASE_URL не установлена!")

# --- ОБРАБОТЧИКИ ЗАПРОСОВ (API) ---

async def get_user_status(request):
    if not database or not app.get('database_connected'): return web.json_response({'error': 'DB connection failed'}, status=503)
    try: telegram_id = int(request.query['telegram_id'])
    except (KeyError, ValueError): return web.json_response({'error': 'telegram_id не указан'}, status=400)
    
    query = users.select().where(users.c.telegram_id == telegram_id)
    try: user = await database.fetch_one(query)
    except Exception as e: return web.json_response({'error': f'Ошибка при обращении к БД: {e}'}, status=500)
    
    if user:
        return web.json_response({'status': 'registered', 'user_id': str(user['id']), 'points': user['points'], 'referral_code': user['referral_code'], 'has_completed_genesis': user['has_completed_genesis'], 'is_searchable': user['is_searchable'], 'invites_left': user['invites_left']})
    else:
        return web.json_response({'status': 'not_registered'}, status=404)

async def register_user(request):
    if not database or not app.get('database_connected'): return web.json_response({'error': 'DB connection failed'}, status=503)
    try:
        data = await request.json()
        telegram_id, inviter_code = data['telegram_id'], data.get('inviter_code')
    except Exception: return web.json_response({'error': 'Некорректные данные'}, status=400)
    
    if await database.fetch_one(users.select().where(users.c.telegram_id == telegram_id)):
        return web.json_response({'error': 'Пользователь уже зарегистрирован'}, status=409)
    
    async with database.transaction():
        try:
            inviter_id = None
            user_count = await database.fetch_val(sqlalchemy.select(sqlalchemy.func.count(users.c.id)))
            
            if user_count > 0:
                if not inviter_code: return web.json_response({'error': 'Для вступления требуется приглашение.'}, status=403)
                
                inviter = await database.fetch_one(users.select().where(users.c.referral_code == inviter_code))
                if not inviter: return web.json_response({'error': 'Код-приглашение недействителен или устарел.'}, status=403)
                if inviter['invites_left'] <= 0: return web.json_response({'error': 'У пригласившего закончились инвайты.'}, status=403)
                
                inviter_id = inviter['id']
                await database.execute(users.update().where(users.c.id == inviter_id).values(invites_left=users.c.invites_left - 1))
                logging.info(f"Списан 1 инвайт у пользователя {inviter_id}")

            await database.execute(users.insert().values(id=uuid.uuid4(), telegram_id=telegram_id, username=data.get('username'), first_name=data.get('first_name'), points=1000, referral_code=str(uuid.uuid4()), invited_by_id=inviter_id, is_searchable=True, has_completed_genesis=False, invites_left=5))
            logging.info(f"Зарегистрирован новый пользователь: tg_id={telegram_id}")
        except Exception as e:
            logging.error(f"Ошибка БД при регистрации: {e}")
            return web.json_response({'error': 'Ошибка при записи в БД'}, status=500)
            
    return web.json_response({'status': 'success'}, status=201)

async def get_genesis_questions(request):
    logging.info("API: /api/genesis_questions вызван.")
    if not database or not app.get('database_connected'): return web.json_response({'error': 'DB connection failed'}, status=503)

    try:
        questions_from_db = await database.fetch_all(questions.select())
        
        if not questions_from_db and GENESIS_QUESTIONS:
            logging.info("Таблица 'questions' пуста. Загружаем вопросы из questions.json...")
            questions_to_insert = [{"id": q["id"], "text": q["text"], "category": q["category"], "options": q.get("options")} for q in GENESIS_QUESTIONS]
            await database.execute_many(query=questions.insert(), values=questions_to_insert)
            logging.info(f"Успешно загружено {len(questions_to_insert)} вопросов в БД.")
            return web.json_response(GENESIS_QUESTIONS)
        
        response_data = [{"id": q["id"], "text": q["text"], "category": q["category"], "options": q["options"]} for q in questions_from_db]
        return web.json_response(response_data)
    except Exception as e:
        logging.error(f"Ошибка при проверке/загрузке вопросов в БД: {e}")
        return web.json_response({'error': 'Не удалось подготовить вопросы'}, status=500)

async def submit_answers(request):
    if not database or not app.get('database_connected'): return web.json_response({'error': 'DB connection failed'}, status=503)
    try:
        data = await request.json()
        user_id_str, user_answers = data.get('user_id'), data.get('answers')
        if not user_id_str or not user_answers: return web.json_response({'error': 'Отсутствует ID или ответы'}, status=400)
        user_id = uuid.UUID(user_id_str)
    except Exception: return web.json_response({'error': 'Некорректный формат запроса'}, status=400)

    async with database.transaction():
        try:
            current_user = await database.fetch_one(users.select().where(users.c.id == user_id))
            if not current_user: return web.json_response({'error': 'Пользователь не найден'}, status=404)
            if current_user['has_completed_genesis']: return web.json_response({'error': 'Вы уже проходили эту анкету'}, status=403)

            answers_to_insert = [{"user_id": user_id, "question_id": int(q_id), "answer_text": ans} for q_id, ans in user_answers.items()]
            if answers_to_insert: await database.execute_many(query=answers.insert(), values=answers_to_insert)
            
            points_for_genesis = 60000
            await database.execute(users.update().where(users.c.id == user_id).values(points=users.c.points + points_for_genesis, has_completed_genesis=True))
            logging.info(f"Начислено {points_for_genesis} очков пользователю {user_id_str} за Генезис-Профиль.")

            if current_user['invited_by_id']:
                inviter_id = current_user['invited_by_id']
                referral_bonus = 20000
                await database.execute(users.update().where(users.c.id == inviter_id).values(points=users.c.points + referral_bonus))
                logging.info(f"Начислено {referral_bonus} реферальных очков инвайтеру {inviter_id}")
        except Exception as e:
            logging.error(f"Ошибка при сохранении ответов или начислении очков: {e}")
            return web.json_response({'error': 'Ошибка при работе с БД'}, status=500)
            
    return web.json_response({'status': 'success', 'message': f'Ответы сохранены! Начислено {points_for_genesis} очков.'})

async def update_user_settings(request):
    if not database or not app.get('database_connected'): return web.json_response({'error': 'DB connection failed'}, status=503)
    try:
        data = await request.json()
        user_id_str, is_searchable = data.get('user_id'), data.get('is_searchable')
        if user_id_str is None or is_searchable is None: return web.json_response({'error': 'Отсутствуют параметры'}, status=400)
        user_id = uuid.UUID(user_id_str)
        await database.execute(users.update().where(users.c.id == user_id).values(is_searchable=is_searchable))
        logging.info(f"Настройки для {user_id_str} обновлены: is_searchable = {is_searchable}")
        return web.json_response({'status': 'success'})
    except Exception as e:
        logging.error(f"Ошибка при обновлении настроек: {e}")
        return web.json_response({'error': 'Ошибка на сервере'}, status=500)

async def delete_user(request):
    if not database or not app.get('database_connected'): return web.json_response({'error': 'DB connection failed'}, status=503)
    try:
        data = await request.json()
        user_id_str = data.get('user_id')
        if not user_id_str: return web.json_response({'error': 'Отсутствует ID пользователя'}, status=400)
        user_id = uuid.UUID(user_id_str)
        async with database.transaction():
            await database.execute(answers.delete().where(answers.c.user_id == user_id))
            await database.execute(users.delete().where(users.c.id == user_id))
        logging.info(f"Пользователь {user_id_str} и его данные удалены.")
        return web.json_response({'status': 'success'})
    except Exception as e:
        logging.error(f"Ошибка при удалении пользователя: {e}")
        return web.json_response({'error': 'Ошибка на сервере'}, status=500)

async def get_user_count(request):
    if not database or not app.get('database_connected'): return web.json_response({'error': 'DB connection failed'}, status=503)
    try:
        query = sqlalchemy.select(sqlalchemy.func.count(users.c.id))
        count = await database.fetch_val(query)
        return web.json_response({'count': count})
    except Exception as e:
        logging.error(f"Ошибка при подсчете пользователей: {e}")
        return web.json_response({'error': 'Ошибка на сервере'}, status=500)

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
app.router.add_get('/api/genesis_questions', get_genesis_questions)
app.router.add_post('/api/submit_answers', submit_answers)
app.router.add_post('/api/user/settings', update_user_settings)
app.router.add_post('/api/user/delete', delete_user)
app.router.add_get('/api/user_count', get_user_count)

app.on_startup.append(on_startup)
app.on_shutdown.append(on_shutdown)

if __name__ == "__main__":
    web.run_app(app, port=PORT, host='0.0.0.0')
