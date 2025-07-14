# server.py
# ВЕРСИЯ 55: Финальная, самая надежная версия

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
        query = "SELECT * FROM users WHERE telegram_id = :telegram_id"
        user = await database.fetch_one(query=query, values={"telegram_id": telegram_id})
        
        if user:
            invites_query = "SELECT code FROM invite_codes WHERE owner_id = :owner_id AND is_used = FALSE"
            user_invites = await database.fetch_all(query=invites_query, values={"owner_id": user['id']})
            invite_list = [invite['code'] for invite in user_invites]
            return web.json_response({
                'status': 'registered', 'user_id': str(user['id']), 'points': user['points'], 
                'has_completed_genesis': user['has_completed_genesis'], 
                'is_searchable': user['is_searchable'], 'invites': invite_list
            })
        else:
            return web.json_response({'status': 'not_registered'}, status=404)
    except Exception as e:
        logging.error(f"API Ошибка в get_user_status: {e}")
        return web.json_response({'error': 'Ошибка сервера'}, status=500)

async def register_user(request):
    try:
        data = await request.json()
        telegram_id, inviter_code = data['telegram_id'], data.get('invite_code')
        
        async with database.transaction():
            user_count_query = "SELECT COUNT(id) FROM users"
            user_count = await database.fetch_val(query=user_count_query)
            inviter_id = None
            
            if user_count == 0:
                if not inviter_code or inviter_code.upper() != MASTER_INVITE_CODE:
                    return web.json_response({'error': 'Неверный мастер-код'}, status=403)
            else:
                if not inviter_code: return web.json_response({'error': 'Требуется приглашение'}, status=403)
                
                invite_query = "SELECT * FROM invite_codes WHERE code = :code"
                invite = await database.fetch_one(query=invite_query, values={"code": inviter_code.upper()})
                if not invite or invite['is_used']: return web.json_response({'error': 'Код недействителен'}, status=403)
                
                inviter_id = invite['owner_id']
            
            new_user_id = uuid.uuid4()
            multiplier = get_multiplier_for_user(user_count)
            
            insert_user_query = """
            INSERT INTO users (id, telegram_id, username, first_name, points, invited_by_id, airdrop_multiplier)
            VALUES (:id, :telegram_id, :username, :first_name, 1000, :invited_by_id, :airdrop_multiplier)
            """
            await database.execute(query=insert_user_query, values={
                "id": new_user_id, "telegram_id": telegram_id, "username": data.get('username'),
                "first_name": data.get('first_name'), "invited_by_id": inviter_id, "airdrop_multiplier": multiplier
            })
            
            new_invites = [{"code": generate_invite_code(), "owner_id": new_user_id} for _ in range(5)]
            insert_invites_query = "INSERT INTO invite_codes (code, owner_id) VALUES (:code, :owner_id)"
            await database.execute_many(query=insert_invites_query, values=new_invites)

            if inviter_id:
                await database.execute("UPDATE invite_codes SET is_used = TRUE, used_by_id = :used_by_id WHERE code = :code", 
                                       values={"used_by_id": new_user_id, "code": inviter_code.upper()})
        
        return web.json_response({'status': 'success'}, status=201)
    except Exception as e:
        logging.error(f"API Ошибка в register_user: {e}")
        return web.json_response({'error': 'Ошибка при записи в БД'}, status=500)

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

app.on_startup.append(on_startup)
app.on_shutdown.append(on_shutdown)

if __name__ == "__main__":
    web.run_app(app, port=int(os.getenv("PORT", 8080)), host='0.0.0.0')
