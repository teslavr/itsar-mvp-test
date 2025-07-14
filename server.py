# server.py
# ВЕРСИЯ 53: Финальная, самая надежная версия

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
MASTER_INVITE_CODE = "ITSAR-GENESIS-1"

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- НАСТРОЙКА БАЗЫ ДАННЫХ И ТАБЛИЦ ---
database = databases.Database(DATABASE_URL)
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
    sqlalchemy.Column("airdrop_multiplier", sqlalchemy.Float, default=1.0, nullable=False),
    sqlalchemy.Column("created_at", sqlalchemy.DateTime, server_default=sqlalchemy.func.now()),
)

invite_codes = sqlalchemy.Table(
    "invite_codes", metadata,
    sqlalchemy.Column("id", sqlalchemy.Integer, primary_key=True, autoincrement=True),
    sqlalchemy.Column("code", sqlalchemy.String, unique=True, nullable=False),
    sqlalchemy.Column("owner_id", UUID(as_uuid=True), sqlalchemy.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    sqlalchemy.Column("is_used", sqlalchemy.Boolean, default=False, nullable=False),
    sqlalchemy.Column("used_by_id", UUID(as_uuid=True), sqlalchemy.ForeignKey("users.id"), nullable=True),
)

# --- ХЕЛПЕРЫ ---
def generate_invite_code():
    return secrets.token_hex(4).upper()

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
        query = users.select().where(users.c.telegram_id == telegram_id)
        user = await database.fetch_one(query)
        
        if user:
            invites_query = invite_codes.select().where(invite_codes.c.owner_id == user['id'], invite_codes.c.is_used == False)
            user_invites = await database.fetch_all(invites_query)
            invite_list = [invite['code'] for invite in user_invites]
            return web.json_response({'status': 'registered', 'user_id': str(user['id']), 'points': user['points'], 'has_completed_genesis': user['has_completed_genesis'], 'is_searchable': user['is_searchable'], 'invites': invite_list})
        else:
            return web.json_response({'status': 'not_registered'}, status=404)
    except Exception as e:
        logging.error(f"API Ошибка в get_user_status: {e}")
        return web.json_response({'error': 'Ошибка сервера'}, status=500)

async def register_user(request):
    try:
        data = await request.json()
        telegram_id, inviter_code = data['telegram_id'], data.get('invite_code')
        
        if await database.fetch_one(users.select().where(users.c.telegram_id == telegram_id)):
            return web.json_response({'error': 'Пользователь уже зарегистрирован'}, status=409)

        async with database.transaction():
            user_count = await database.fetch_val(sqlalchemy.select(sqlalchemy.func.count(users.c.id)))
            inviter_id = None
            
            if user_count == 0:
                if not inviter_code or inviter_code.upper() != MASTER_INVITE_CODE:
                    return web.json_response({'error': 'Неверный мастер-код'}, status=403)
            else:
                if not inviter_code: return web.json_response({'error': 'Требуется приглашение'}, status=403)
                invite = await database.fetch_one(invite_codes.select().where(invite_codes.c.code == inviter_code.upper()))
                if not invite or invite['is_used']: return web.json_response({'error': 'Код недействителен'}, status=403)
                inviter_id = invite['owner_id']
            
            new_user_id = uuid.uuid4()
            multiplier = get_multiplier_for_user(user_count)
            
            await database.execute(users.insert().values(
                id=new_user_id, telegram_id=telegram_id, username=data.get('username'), 
                first_name=data.get('first_name'), points=1000, invited_by_id=inviter_id,
                airdrop_multiplier=multiplier, is_searchable=True, has_completed_genesis=False
            ))
            
            new_invites = [{"code": generate_invite_code(), "owner_id": new_user_id, "is_used": False} for _ in range(5)]
            await database.execute_many(query=invite_codes.insert(), values=new_invites)

            if inviter_id:
                await database.execute(invite_codes.update().where(invite_codes.c.code == inviter_code.upper()).values(is_used=True, used_by_id=new_user_id))
        
        return web.json_response({'status': 'success'}, status=201)
    except Exception as e:
        logging.error(f"API Ошибка в register_user: {e}")
        return web.json_response({'error': 'Ошибка при записи в БД'}, status=500)

async def handle_index(request):
    try:
        with open('./index.html', 'r', encoding='utf-8') as f: return web.Response(text=f.read(), content_type='text/html')
    except FileNotFoundError: return web.Response(text="404: Not Found", status=404)

# --- УПРАВЛЕНИЕ ЖИЗНЕННЫМ ЦИКЛОМ ПРИЛОЖЕНИЯ ---
async def startup_event():
    try:
        engine = sqlalchemy.create_engine(DATABASE_URL)
        metadata.create_all(engine)
        logging.info("Структура таблиц в БД проверена/создана.")
        await database.connect()
        logging.info("Подключение к базе данных установлено.")
    except Exception as e:
        logging.critical(f"Не удалось инициализировать БД при старте: {e}")

async def shutdown_event():
    if database.is_connected:
        await database.disconnect()
        logging.info("Подключение к базе данных закрыто.")

# --- СБОРКА И ЗАПУСК ПРИЛОЖЕНИЯ ---
app = web.Application(middlewares=[db_connection_middleware])
app.router.add_get('/', handle_index)
app.router.add_get('/api/user/status', get_user_status)
app.router.add_post('/api/register', register_user)
# ... и другие роуты

app.on_startup.append(lambda _: startup_event())
app.on_shutdown.append(lambda _: shutdown_event())

if __name__ == "__main__":
    web.run_app(app, port=int(os.getenv("PORT", 8080)), host='0.0.0.0')
