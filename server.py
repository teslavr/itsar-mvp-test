# server.py
# ВЕРСИЯ 62: Финальная, самая надежная версия

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
    exit()

PORT = int(os.getenv("PORT", 8080))
MASTER_INVITE_CODE = "FEUDATA-GENESIS-1"

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

# --- НАСТРОЙКА БАЗЫ ДАННЫХ ---
database = databases.Database(DATABASE_URL)
metadata = sqlalchemy.MetaData()

# --- SQL-ЗАПРОСЫ ДЛЯ СОЗДАНИЯ ТАБЛИЦ ---
CREATE_USERS_TABLE = """
CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY,
    telegram_id BIGINT UNIQUE NOT NULL,
    username VARCHAR(255),
    first_name VARCHAR(255),
    points BIGINT NOT NULL DEFAULT 0,
    invited_by_id UUID REFERENCES users(id),
    has_completed_genesis BOOLEAN NOT NULL DEFAULT FALSE,
    is_searchable BOOLEAN NOT NULL DEFAULT TRUE,
    airdrop_multiplier REAL NOT NULL DEFAULT 1.0,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);
"""
CREATE_INVITES_TABLE = """
CREATE TABLE IF NOT EXISTS invite_codes (
    id SERIAL PRIMARY KEY,
    code VARCHAR(255) UNIQUE NOT NULL,
    owner_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    is_used BOOLEAN NOT NULL DEFAULT FALSE,
    used_by_id UUID REFERENCES users(id)
);
"""
CREATE_QUESTIONS_TABLE = """
CREATE TABLE IF NOT EXISTS questions (
    id INTEGER PRIMARY KEY,
    text VARCHAR(1024) NOT NULL,
    category VARCHAR(255) NOT NULL,
    options JSONB
);
"""
CREATE_ANSWERS_TABLE = """
CREATE TABLE IF NOT EXISTS answers (
    id SERIAL PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    question_id INTEGER NOT NULL REFERENCES questions(id),
    answer_text VARCHAR(2048) NOT NULL,
    answered_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);
"""

# --- ХЕЛПЕРЫ ---
def generate_invite_code(): return secrets.token_hex(4).upper()
def get_multiplier_for_user(user_count):
    if user_count < 100: return 10.0
    if user_count < 1000: return 5.0
    if user_count < 10000: return 2.0
    if user_count < 100000: return 1.0
    return 0.2

# --- MIDDLEWARE ДЛЯ УПРАВЛЕНИЯ ПОДКЛЮЧЕНИЕМ К БД ---
@web.middleware
async def db_connection_middleware(request, handler):
    if not database.is_connected:
        try:
            await database.connect()
        except Exception as e:
            logging.error(f"Не удалось переподключиться к БД: {e}")
            return web.json_response({'error': 'Сервис временно недоступен'}, status=503)
    return await handler(request)

# --- ОБРАБОТЧИКИ ЗАПРОСОВ (API) ---
async def get_user_status(request):
    try:
        telegram_id = int(request.query['telegram_id'])
        query = "SELECT * FROM users WHERE telegram_id = :telegram_id"
        user = await database.fetch_one(query=query, values={"telegram_id": telegram_id})
        if user:
            invites_query = "SELECT code FROM invite_codes WHERE owner_id = :owner_id AND is_used = FALSE"
            user_invites = await database.fetch_all(query=invites_query, values={"owner_id": user['id']})
            return web.json_response({'status': 'registered', 'user_id': str(user['id']), 'points': user['points'], 'has_completed_genesis': user['has_completed_genesis'], 'is_searchable': user['is_searchable'], 'invites': [i['code'] for i in user_invites]})
        else:
            return web.json_response({'status': 'not_registered'}, status=404)
    except Exception as e:
        logging.error(f"API Ошибка в get_user_status: {e}")
        return web.json_response({'error': 'Ошибка сервера'}, status=500)

async def register_user(request):
    try:
        data = await request.json()
        telegram_id, inviter_code = data['telegram_id'], data.get('invite_code')
        if await database.fetch_one("SELECT id FROM users WHERE telegram_id = :telegram_id", {"telegram_id": telegram_id}):
            return web.json_response({'error': 'Пользователь уже зарегистрирован'}, status=409)
        async with database.transaction():
            user_count = await database.fetch_val("SELECT COUNT(id) FROM users")
            inviter_id = None
            if user_count == 0:
                if not inviter_code or inviter_code.upper() != MASTER_INVITE_CODE: return web.json_response({'error': 'Неверный мастер-код'}, status=403)
            else:
                if not inviter_code: return web.json_response({'error': 'Требуется приглашение'}, status=403)
                invite = await database.fetch_one("SELECT * FROM invite_codes WHERE code = :code", {"code": inviter_code.upper()})
                if not invite or invite['is_used']: return web.json_response({'error': 'Код недействителен'}, status=403)
                inviter_id = invite['owner_id']
            new_user_id = uuid.uuid4()
            await database.execute("INSERT INTO users (id, telegram_id, username, first_name, points, invited_by_id, airdrop_multiplier) VALUES (:id, :telegram_id, :username, :first_name, 1000, :invited_by_id, :airdrop_multiplier)",
                                 {"id": new_user_id, "telegram_id": telegram_id, "username": data.get('username'), "first_name": data.get('first_name'), "invited_by_id": inviter_id, "airdrop_multiplier": get_multiplier_for_user(user_count)})
            new_invites = [{"code": generate_invite_code(), "owner_id": new_user_id} for _ in range(5)]
            await database.execute_many("INSERT INTO invite_codes (code, owner_id) VALUES (:code, :owner_id)", new_invites)
            if inviter_id:
                await database.execute("UPDATE invite_codes SET is_used = TRUE, used_by_id = :used_by_id WHERE code = :code", {"used_by_id": new_user_id, "code": inviter_code.upper()})
        return web.json_response({'status': 'success'}, status=201)
    except Exception as e:
        logging.error(f"API Ошибка в register_user: {e}")
        return web.json_response({'error': 'Ошибка при записи в БД'}, status=500)

async def get_genesis_questions(request):
    try:
        count = await database.fetch_val("SELECT COUNT(id) FROM questions")
        if count == 0 and GENESIS_QUESTIONS:
            questions_to_insert = [{"id": q["id"], "text": q["text"], "category": q["category"], "options": json.dumps(q.get("options"))} for q in GENESIS_QUESTIONS]
            await database.execute_many("INSERT INTO questions (id, text, category, options) VALUES (:id, :text, :category, :options)", questions_to_insert)
        
        questions_from_db = await database.fetch_all("SELECT * FROM questions ORDER BY id")
        response_data = [{"id": q["id"], "text": q["text"], "category": q["category"], "options": json.loads(q["options"]) if q["options"] else []} for q in questions_from_db]
        return web.json_response(response_data)
    except Exception as e:
        logging.error(f"Ошибка при получении вопросов: {e}")
        return web.json_response({'error': 'Не удалось подготовить вопросы'}, status=500)

async def submit_answers(request):
    try:
        data = await request.json()
        user_id = uuid.UUID(data.get('user_id'))
        async with database.transaction():
            current_user = await database.fetch_one("SELECT * FROM users WHERE id = :id", {"id": user_id})
            if not current_user or current_user['has_completed_genesis']: return web.json_response({'error': 'Действие недоступно'}, status=403)
            
            answers_to_insert = [{"user_id": user_id, "question_id": int(q_id), "answer_text": ans} for q_id, ans in data.get('answers', {}).items()]
            if answers_to_insert: await database.execute_many("INSERT INTO answers (user_id, question_id, answer_text) VALUES (:user_id, :question_id, :answer_text)", answers_to_insert)
            
            points_for_genesis = 60000 * current_user['airdrop_multiplier']
            await database.execute("UPDATE users SET points = points + :points, has_completed_genesis = TRUE WHERE id = :id", {"points": points_for_genesis, "id": user_id})
            
            if current_user['invited_by_id']:
                inviter = await database.fetch_one("SELECT * FROM users WHERE id = :id", {"id": current_user['invited_by_id']})
                if inviter:
                    referral_bonus = (20000 + (60000 * 0.15)) * inviter['airdrop_multiplier']
                    await database.execute("UPDATE users SET points = points + :points WHERE id = :id", {"points": referral_bonus, "id": inviter['id']})
        return web.json_response({'status': 'success'})
    except Exception as e:
        logging.error(f"Ошибка в submit_answers: {e}")
        return web.json_response({'error': 'Ошибка на сервере'}, status=500)

async def handle_index(request):
    try:
        with open('./index.html', 'r', encoding='utf-8') as f: return web.Response(text=f.read(), content_type='text/html')
    except FileNotFoundError: return web.Response(text="404: Not Found", status=404)

# --- УПРАВЛЕНИЕ ЖИЗНЕННЫМ ЦИКЛОМ ПРИЛОЖЕНИЯ ---
async def on_startup(app):
    try:
        await database.connect()
        logging.info("Первичное подключение к базе данных установлено.")
        await database.execute(query=CREATE_USERS_TABLE)
        await database.execute(query=CREATE_INVITES_TABLE)
        await database.execute(query=CREATE_QUESTIONS_TABLE)
        await database.execute(query=CREATE_ANSWERS_TABLE)
        logging.info("Все таблицы проверены/созданы.")
    except Exception as e:
        logging.critical(f"Не удалось инициализировать БД при старте: {e}")

async def on_shutdown(app):
    if database.is_connected:
        await database.disconnect()
        logging.info("Подключение к базе данных закрыто.")

# --- СБОРКА И ЗАПУСК ПРИЛОЖЕНИЯ ---
app = web.Application(middlewares=[db_connection_middleware])
app.router.add_get('/', handle_index)
app.router.add_get('/api/user/status', get_user_status)
app.router.add_post('/api/register', register_user)
app.router.add_get('/api/genesis_questions', get_genesis_questions)
app.router.add_post('/api/submit_answers', submit_answers)

if __name__ == "__main__":
    web.run_app(app, port=int(os.getenv("PORT", 8080)), host='0.0.0.0')
