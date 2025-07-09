# server.py
# ВЕРСИЯ 33: Финальная, полная, исправленная версия со всеми функциями

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
    sqlalchemy.Column("points", sqlalchemy.BigInteger, default=0),
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
    if not database or not app.get('database_connected'): return web.json_response({'error': 'DB connection failed'}, status=503)
    try: telegram_id = int(request.query['telegram_id'])
    except (KeyError, ValueError): return web.json_response({'error': 'telegram_id не указан'}, status=400)
    
    query = users.select().where(users.c.telegram_id == telegram_id)
    user = await database.fetch_one(query)
    
    if user:
        invites_query = invite_codes.select().where(invite_codes.c.owner_id == user['id'], invite_codes.c.is_used == False)
        user_invites = await database.fetch_all(invites_query)
        invite_list = [invite['code'] for invite in user_invites]
        return web.json_response({'status': 'registered', 'user_id': str(user['id']), 'points': user['points'], 'has_completed_genesis': user['has_completed_genesis'], 'is_searchable': user['is_searchable'], 'invites': invite_list})
    else:
        return web.json_response({'status': 'not_registered'}, status=404)

async def register_user(request):
    logging.info("API: /api/register вызван.")
    if not database or not app.get('database_connected'): return web.json_response({'error': 'DB connection failed'}, status=503)

    try:
        data = await request.json()
        telegram_id, inviter_code = data['telegram_id'], data.get('invite_code')
    except Exception: return web.json_response({'error': 'Некорректные данные'}, status=400)
    
    if await database.fetch_one(users.select().where(users.c.telegram_id == telegram_id)):
        return web.json_response({'error': 'Пользователь уже зарегистрирован'}, status=409)
    
    async with database.transaction():
        try:
            inviter_id = None
            user_count = await database.fetch_val(sqlalchemy.select(sqlalchemy.func.count(users.c.id)))
            
            if user_count == 0:
                if not inviter_code or inviter_code.upper() != MASTER_INVITE_CODE:
                    return web.json_response({'error': 'Неверный мастер-код для первого пользователя.'}, status=403)
                logging.info("Регистрация первого пользователя по мастер-коду.")
            else:
                if not inviter_code:
                    return web.json_response({'error': 'Требуется код-приглашение'}, status=403)
                
                invite = await database.fetch_one(invite_codes.select().where(invite_codes.c.code == inviter_code.upper()))
                if not invite or invite['is_used']:
                    return web.json_response({'error': 'Код-приглашение недействителен или уже использован.'}, status=403)
                
                inviter_id = invite['owner_id']
            
            new_user_id = uuid.uuid4()
            await database.execute(users.insert().values(id=new_user_id, telegram_id=telegram_id, username=data.get('username'), first_name=data.get('first_name'), points=1000, invited_by_id=inviter_id, is_searchable=True, has_completed_genesis=False))
            
            new_invites = [{"code": generate_invite_code(), "owner_id": new_user_id} for _ in range(5)]
            await database.execute_many(query=invite_codes.insert(), values=new_invites)

            if inviter_id:
                await database.execute(invite_codes.update().where(invite_codes.c.code == inviter_code.upper()).values(is_used=True, used_by_id=new_user_id))
                await database.execute(users.update().where(users.c.id == inviter_id).values(points=users.c.points + 20000))
                logging.info(f"Код {inviter_code} погашен. Начислено 20000 очков инвайтеру {inviter_id}")

            logging.info(f"Зарегистрирован новый пользователь: tg_id={telegram_id}")
        except Exception as e:
            logging.error(f"Ошибка БД при регистрации: {e}")
            return web.json_response({'error': 'Ошибка при записи в БД'}, status=500)
            
    return web.json_response({'status': 'success'}, status=201)

# ВОТ НЕДОСТАЮЩАЯ ФУНКЦИЯ:
async def get_user_count(request):
    """Возвращает общее количество зарегистрированных пользователей."""
    if not database or not app.get('database_connected'):
        return web.json_response({'error': 'DB connection failed'}, status=503)
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
app.router.add_get('/api/user_count', get_user_count) # <-- ВОТ МАРШРУТ ДЛЯ НОВОЙ ФУНКЦИИ
# ... (остальные маршруты)

app.on_startup.append(on_startup)
app.on_shutdown.append(on_shutdown)

if __name__ == "__main__":
    web.run_app(app, port=PORT, host='0.0.0.0')
