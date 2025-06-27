# server.py
# ВЕРСИЯ 10: Вопросы теперь загружаются из questions.json

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
    """Загружает вопросы из файла questions.json."""
    try:
        with open('questions.json', 'r', encoding='utf-8') as f:
            questions_data = json.load(f)
            logging.info(f"Успешно загружено {len(questions_data)} вопросов из questions.json")
            return questions_data
    except FileNotFoundError:
        logging.error("Критическая ошибка: Файл questions.json не найден!")
        return []
    except json.JSONDecodeError:
        logging.error("Критическая ошибка: Некорректный формат JSON в файле questions.json!")
        return []

GENESIS_QUESTIONS = load_questions_from_file()


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
            # ... остальные поля таблицы users
        )
        # ... остальные таблицы
    except Exception as e:
        logging.critical(f"Ошибка инициализации базы данных: {e}")
        database = None
else:
    logging.critical("Критическая ошибка: Переменная DATABASE_URL не установлена!")


# --- ОБРАБОТЧИКИ ЗАПРОСОВ (API) ---

async def get_genesis_questions(request):
    """Отдает стартовый набор вопросов из загруженных данных"""
    logging.info("API: /api/genesis_questions вызван.")
    if not GENESIS_QUESTIONS:
        return web.json_response({'error': 'Вопросы не найдены на сервере'}, status=500)
    return web.json_response(GENESIS_QUESTIONS)


async def submit_answers(request):
    """Принимает ответы от пользователя"""
    try:
        data = await request.json()
        user_id = data.get('user_id')
        answers = data.get('answers')
        logging.info(f"API: /api/submit_answers вызван. Получены ответы от user_id: {user_id}")
        logging.info(json.dumps(answers, indent=2, ensure_ascii=False))
        # TODO: На следующем шаге здесь будет логика сохранения в БД и начисления очков
        return web.json_response({'status': 'success', 'message': 'Ответы получены!'})
    except Exception as e:
        logging.error(f"Ошибка при обработке ответов: {e}")
        return web.json_response({'error': 'Ошибка на сервере'}, status=500)


# ... (остальные обработчики: get_user_status, register_user, handle_index)
async def get_user_status(request):
    # ... код без изменений ...
    pass
async def register_user(request):
    # ... код без изменений ...
    pass
async def handle_index(request):
    # ... код без изменений ...
    pass

# --- УПРАВЛЕНИЕ ЖИЗНЕННЫМ ЦИКЛОМ ПРИЛОЖЕНИЯ ---
async def on_startup(app):
    # ... код без изменений ...
    pass
async def on_shutdown(app):
    # ... код без изменений ...
    pass

# --- СБОРКА И ЗАПУСК ПРИЛОЖЕНИЯ ---
app = web.Application()
app.router.add_get('/', handle_index)
app.router.add_get('/api/user/status', get_user_status)
app.router.add_post('/api/register', register_user)
app.router.add_get('/api/genesis_questions', get_genesis_questions)
app.router.add_post('/api/submit_answers', submit_answers)

app.on_startup.append(on_startup)
app.on_shutdown.append(on_shutdown)

if __name__ == "__main__":
    web.run_app(app, port=PORT, host='0.0.0.0')

