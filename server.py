# server.py
# ВЕРСИЯ 34: Финальная, полная, исправленная версия со всеми функциями

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
