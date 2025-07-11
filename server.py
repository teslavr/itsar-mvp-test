# server.py
# ВЕРСИЯ 45: Финальная, стабильная версия со всей логикой

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
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") 
PORT = int(os.getenv("PORT", 8080))
MASTER_INVITE_CODE = "ITSAR-GENESIS-1"

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
    sqlalchemy.Column("points", sqlalchemy.BigInteger, default=0, nullable=False),
    sqlalchemy.Column("invited_by_id", UUID(as_uuid=True), sqlalchemy.ForeignKey("users.id"), nullable=True),
    sqlalchemy.Column("has_completed_genesis", sqlalchemy.Boolean, default=False, nullable=False),
    sqlalchemy.Column("is_searchable", sqlalchemy.Boolean, default=True, nullable=False),
    sqlalchemy.Column("created_at", sqlalchemy.DateTime, server_default=sqlalchemy.func.now()),
)

invite_codes = sqlalchemy.Table(
    "invite_codes", metadata,
    sqlalchemy.Column("id", sqlalchemy.Integer, primary_key=True, autoincrement=True),
    sqlalchemy.Column("code", sqlalchemy.String, unique=True, nullable=False),
    sqlalchemy.Column("owner_id", UUID(as_uuid=True), sqlalchemy.ForeignKey("users.id"), nullable=False),
    sqlalchemy.Column("is_used", sqlalchemy.Boolean, default=False, nullable=False),
    sqlalchemy.Column("used_by_id", UUID(as_uuid=True), sqlalchemy.ForeignKey("users.id"), nullable=True),
)

questions = sqlalchemy.Table("questions", metadata, sqlalchemy.Column("id", sqlalchemy.Integer, primary_key=True, autoincrement=False), sqlalchemy.Column("text", sqlalchemy.String), sqlalchemy.Column("category", sqlalchemy.String), sqlalchemy.Column("options", sqlalchemy.JSON))
answers = sqlalchemy.Table("answers", metadata, sqlalchemy.Column("id", sqlalchemy.Integer, primary_key=True, autoincrement=True), sqlalchemy.Column("user_id", UUID), sqlalchemy.Column("question_id", sqlalchemy.Integer), sqlalchemy.Column("answer_text", sqlalchemy.String))

if DATABASE_URL:
    database = databases.Database(DATABASE_URL)
else:
    logging.critical("Критическая ошибка: Переменная DATABASE_URL не установлена!")

# --- ХЕЛПЕРЫ ---
def generate_invite_code():
    return secrets.token_hex(4).upper()

# --- ОБРАБОТЧИКИ ЗАПРОСОВ (API) ---

async def get_user_status(request):
    if not database: return web.json_response({'error': 'Database not configured'}, status=500)
    try:
        telegram_id = int(request.query['telegram_id'])
        async with database.connection() as connection:
            user = await connection.fetch_one(users.select().where(users.c.telegram_id == telegram_id))
            if user:
                invites_query = invite_codes.select().where(invite_codes.c.owner_id == user['id'], invite_codes.c.is_used == False)
                user_invites = await connection.fetch_all(invites_query)
                invite_list = [invite['code'] for invite in user_invites]
                return web.json_response({'status': 'registered', 'user_id': str(user['id']), 'points': user['points'], 'has_completed_genesis': user['has_completed_genesis'], 'is_searchable': user['is_searchable'], 'invites': invite_list})
            else:
                return web.json_response({'status': 'not_registered'}, status=404)
    except Exception as e:
        logging.error(f"API Ошибка в get_user_status: {e}")
        return web.json_response({'error': 'Ошибка сервера или БД'}, status=500)

async def register_user(request):
    if not database: return web.json_response({'error': 'Database not configured'}, status=500)
    try:
        data = await request.json()
        telegram_id, inviter_code = data['telegram_id'], data.get('invite_code')
        
        async with database.connection() as connection:
            if await connection.fetch_one(users.select().where(users.c.telegram_id == telegram_id)):
                return web.json_response({'error': 'Пользователь уже зарегистрирован'}, status=409)

            async with connection.transaction():
                user_count = await connection.fetch_val(sqlalchemy.select(sqlalchemy.func.count(users.c.id)))
                inviter_id = None
                
                if user_count == 0:
                    if not inviter_code or inviter_code.upper() != MASTER_INVITE_CODE:
                        return web.json_response({'error': 'Неверный мастер-код для первого пользователя.'}, status=403)
                else:
                    if not inviter_code: return web.json_response({'error': 'Требуется код-приглашение.'}, status=403)
                    
                    invite = await connection.fetch_one(invite_codes.select().where(invite_codes.c.code == inviter_code.upper()))
                    if not invite or invite['is_used']: return web.json_response({'error': 'Код-приглашение недействителен или уже использован.'}, status=403)
                    inviter_id = invite['owner_id']
                
                new_user_id = uuid.uuid4()
                await connection.execute(users.insert().values(id=new_user_id, telegram_id=telegram_id, username=data.get('username'), first_name=data.get('first_name'), points=1000, invited_by_id=inviter_id, is_searchable=True, has_completed_genesis=False))
                
                new_invites = [{"code": generate_invite_code(), "owner_id": new_user_id, "is_used": False} for _ in range(5)]
                await connection.execute_many(query=invite_codes.insert(), values=new_invites)

                if inviter_id:
                    await connection.execute(invite_codes.update().where(invite_codes.c.code == inviter_code.upper()).values(is_used=True, used_by_id=new_user_id))
        
        return web.json_response({'status': 'success'}, status=201)
    except Exception as e:
        logging.error(f"API Ошибка в register_user: {e}")
        return web.json_response({'error': 'Ошибка при записи в БД'}, status=500)

async def get_genesis_questions(request):
    if not database: return web.json_response({'error': 'Database not configured'}, status=500)
    try:
        async with database.connection() as connection:
            questions_from_db = await connection.fetch_all(questions.select())
            if not questions_from_db and GENESIS_QUESTIONS:
                logging.info("Таблица 'questions' пуста. Заполняем...")
                questions_to_insert = [{"id": q["id"], "text": q["text"], "category": q["category"], "options": q.get("options")} for q in GENESIS_QUESTIONS]
                await connection.execute_many(query=questions.insert(), values=questions_to_insert)
                return web.json_response(GENESIS_QUESTIONS)
            
            response_data = [{"id": q["id"], "text": q["text"], "category": q["category"], "options": q["options"]} for q in questions_from_db]
            return web.json_response(response_data)
    except Exception as e:
        logging.error(f"Ошибка при получении вопросов: {e}")
        return web.json_response({'error': 'Не удалось подготовить вопросы'}, status=500)

async def submit_answers(request):
    if not database: return web.json_response({'error': 'Database not configured'}, status=500)
    try:
        data = await request.json()
        user_id_str, user_answers = data.get('user_id'), data.get('answers')
        if not user_id_str or not user_answers: return web.json_response({'error': 'Отсутствует ID или ответы'}, status=400)
        user_id = uuid.UUID(user_id_str)
    except Exception: return web.json_response({'error': 'Некорректный формат запроса'}, status=400)

    async with database.connection() as connection:
        async with connection.transaction():
            try:
                current_user = await connection.fetch_one(users.select().where(users.c.id == user_id))
                if not current_user: return web.json_response({'error': 'Пользователь не найден'}, status=404)
                if current_user['has_completed_genesis']: return web.json_response({'error': 'Вы уже проходили эту анкету'}, status=403)

                answers_to_insert = [{"user_id": user_id, "question_id": int(q_id), "answer_text": ans} for q_id, ans in user_answers.items()]
                if answers_to_insert: await connection.execute_many(query=answers.insert(), values=answers_to_insert)
                
                points_for_genesis = 60000
                new_total_points = current_user['points'] + points_for_genesis
                
                await connection.execute(users.update().where(users.c.id == user_id).values(points=new_total_points, has_completed_genesis=True))
                logging.info(f"Начислено {points_for_genesis} очков пользователю {user_id_str}.")

                if current_user['invited_by_id']:
                    inviter_id = current_user['invited_by_id']
                    inviter = await connection.fetch_one(users.select().where(users.c.id == inviter_id))
                    if inviter:
                        referral_bonus = 20000
                        inviter_new_points = inviter['points'] + referral_bonus
                        await connection.execute(users.update().where(users.c.id == inviter_id).values(points=inviter_new_points))
                        logging.info(f"Начислено {referral_bonus} реферальных очков инвайтеру {inviter_id}")
            except Exception as e:
                logging.error(f"Ошибка при сохранении ответов: {e}")
                return web.json_response({'error': 'Ошибка при работе с БД'}, status=500)
                
    return web.json_response({'status': 'success'})

async def handle_index(request):
    try:
        with open('./index.html', 'r', encoding='utf-8') as f: return web.Response(text=f.read(), content_type='text/html')
    except FileNotFoundError: return web.Response(text="404: Not Found", status=404)

# --- УПРАВЛЕНИЕ ЖИЗНЕННЫМ ЦИКЛОМ ПРИЛОЖЕНИЯ ---
async def on_startup(app):
    if database:
        try:
            await database.connect()
            engine = sqlalchemy.create_engine(DATABASE_URL)
            metadata.create_all(engine)
            logging.info("Подключение к базе данных установлено.")
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
# ... и другие роуты

if __name__ == "__main__":
    web.run_app(app, port=int(os.getenv("PORT", 8080)), host='0.0.0.0')
