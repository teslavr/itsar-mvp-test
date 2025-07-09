# server.py
# ВЕРСИЯ 35: Финальная отладочная версия с логированием DATABASE_URL

import os
import logging
import uuid
import json
import secrets
from aiohttp import web
import sqlalchemy
from sqlalchemy.dialects.postgresql import UUID
import databases
from urllib.parse import urlparse

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
# ... (все обработчики API остаются без изменений)
async def get_user_status(request): pass
async def register_user(request): pass
async def get_genesis_questions(request): pass
async def submit_answers(request): pass
async def update_user_settings(request): pass
async def delete_user(request): pass
async def get_user_count(request): pass
async def handle_index(request):
    try:
        with open('./index.html', 'r', encoding='utf-8') as f: return web.Response(text=f.read(), content_type='text/html')
    except FileNotFoundError: return web.Response(text="404: Not Found", status=404)

# --- УПРАВЛЕНИЕ ЖИЗНЕННЫМ ЦИКЛОМ ПРИЛОЖЕНИЯ ---
async def on_startup(app):
    """Выполняется при старте сервера."""
    app['database_connected'] = False
    
    # ОТЛАДКА: Логируем URL базы данных, чтобы проверить его правильность
    if DATABASE_URL:
        try:
            parsed_url = urlparse(DATABASE_URL)
            safe_url = f"{parsed_url.scheme}://{parsed_url.username}:***@{parsed_url.hostname}:{parsed_url.port}{parsed_url.path}"
            logging.info(f"Попытка подключения к БД по адресу: {safe_url}")
        except Exception:
            logging.info(f"Не удалось распарсить DATABASE_URL. Используется: {DATABASE_URL[:20]}...")
    else:
        logging.error("Переменная DATABASE_URL не найдена!")
        return

    if database:
        try:
            await database.connect()
            engine = sqlalchemy.create_engine(DATABASE_URL)
            metadata.create_all(engine)
            logging.info("Подключение к базе данных установлено.")
            app['database_connected'] = True
        except Exception as e:
            logging.critical(f"Не удалось подключиться к БД при старте: {e}")
    else:
        logging.error("Объект базы данных не был создан.")

async def on_shutdown(app):
    if database and database.is_connected:
        await database.disconnect()
        logging.info("Подключение к базе данных закрыто.")

# --- СБОРКА И ЗАПУСК ПРИЛОЖЕНИЯ ---
app = web.Application()
# ... (все роуты остаются без изменений)
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
