# server.py
# ВЕРСИЯ 11: Полная версия кода с загрузкой вопросов из questions.json

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
            sqlalchemy.Column("username", sqlalchemy.String, nullable=True),
            sqlalchemy.Column("first_name", sqlalchemy.String, nullable=True),
            sqlalchemy.Column("points", sqlalchemy.BigInteger, default=0),
            sqlalchemy.Column("referral_code", sqlalchemy.String, unique=True, default=lambda: str(uuid.uuid4())),
            sqlalchemy.Column("invited_by_id", UUID(as_uuid=True), sqlalchemy.ForeignKey("users.id"), nullable=True),
            sqlalchemy.Column("created_at", sqlalchemy.DateTime, server_default=sqlalchemy.func.now()),
        )
    except Exception as e:
        logging.critical(f"Ошибка инициализации базы данных: {e}")
        database = None
else:
    logging.critical("Критическая ошибка: Переменная DATABASE_URL не установлена!")


# --- ОБРАБОТЧИКИ ЗАПРОСОВ (API) ---

async def get_user_status(request):
    """Проверяет, зарегистрирован ли пользователь."""
    if not database or not app.get('database_connected'):
        return web.json_response({'error': 'DB connection failed'}, status=503)
    try:
        telegram_id = int(request.query['telegram_id'])
    except (KeyError, ValueError):
        return web.json_response({'error': 'telegram_id не указан'}, status=400)
    query = users.select().where(users.c.telegram_id == telegram_id)
    try:
        user = await database.fetch_one(query)
    except Exception as e:
        logging.error(f"Ошибка БД при проверке статуса: {e}")
        return web.json_response({'error': 'Ошибка при обращении к БД'}, status=500)
    if user:
        return web.json_response({'status': 'registered', 'user_id': str(user['id']), 'points': user['points'], 'referral_code': user['referral_code']})
    else:
        return web.json_response({'status': 'not_registered'}, status=404)

async def register_user(request):
    """Регистрирует нового пользователя."""
    if not database or not app.get('database_connected'):
        return web.json_response({'error': 'DB connection failed'}, status=503)
    try:
        data = await request.json()
        telegram_id = data['telegram_id']
        username = data.get('username')
        first_name = data.get('first_name')
        inviter_code = data.get('inviter_code')
    except Exception:
        return web.json_response({'error': 'Некорректные данные'}, status=400)
    
    if await database.fetch_one(users.select().where(users.c.telegram_id == telegram_id)):
        return web.json_response({'error': 'Пользователь уже зарегистрирован'}, status=409)
    
    inviter_id = None
    if inviter_code:
        inviter = await database.fetch_one(users.select().where(users.c.referral_code == inviter_code))
        if inviter: inviter_id = inviter['id']
    
    try:
        new_user_id = uuid.uuid4()
        new_referral_code = str(uuid.uuid4())
        insert_query = users.insert().values(id=new_user_id, telegram_id=telegram_id, username=username, first_name=first_name, points=1000, referral_code=new_referral_code, invited_by_id=inviter_id)
        await database.execute(insert_query)
        logging.info(f"Зарегистрирован новый пользователь: tg_id={telegram_id}")
    except Exception as e:
        logging.error(f"Ошибка БД при регистрации: {e}")
        return web.json_response({'error': 'Ошибка при записи в БД'}, status=500)
    
    return web.json_response({'status': 'success'}, status=201)

async def get_genesis_questions(request):
    """Отдает стартовый набор вопросов из загруженных данных"""
    logging.info("API: /api/genesis_questions вызван.")
    if not GENESIS_QUESTIONS:
        return web.json_response({'error': 'Вопросы не найдены на сервере'}, status=500)
    return web.json_response(GENESIS_QUESTIONS)


async def submit_answers(request):
    """Принимает ответы, сохраняет их в БД и начисляет очки."""
    logging.info("API: /api/submit_answers вызван.")
    
    if not database or not app.get('database_connected'):
        return web.json_response({'error': 'DB connection failed'}, status=503)

    try:
        data = await request.json()
        user_id_str = data.get('user_id')
        user_answers = data.get('answers') # Это словарь {question_id: answer_text}
        
        if not user_id_str or not user_answers:
            return web.json_response({'error': 'Отсутствует ID пользователя или ответы'}, status=400)
        
        user_id = uuid.UUID(user_id_str)

    except Exception as e:
        logging.error(f"Ошибка парсинга JSON: {e}")
        return web.json_response({'error': 'Некорректный формат запроса'}, status=400)

    # Используем транзакцию, чтобы все операции были атомарными
    async with database.transaction():
        try:
            # 1. Готовим ответы для пакетной вставки в БД
            answers_to_insert = []
            for q_id, answer_text in user_answers.items():
                answers_to_insert.append({
                    "user_id": user_id,
                    "question_id": int(q_id),
                    "answer_text": answer_text
                })
            
            if answers_to_insert:
                query = answers.insert()
                await database.execute_many(query=query, values=answers_to_insert)
                logging.info(f"Сохранено {len(answers_to_insert)} ответов для пользователя {user_id_str}")

            # 2. Начисляем очки за ответы (по 100 очков за каждый ответ)
            points_to_add = len(answers_to_insert) * 100
            
            # 3. Обновляем баланс пользователя
            update_query = users.update().where(users.c.id == user_id).values(
                points=users.c.points + points_to_add
            )
            await database.execute(update_query)
            logging.info(f"Начислено {points_to_add} очков пользователю {user_id_str}")

        except Exception as e:
            logging.error(f"Критическая ошибка при сохранении ответов или начислении очков: {e}")
            # Транзакция автоматически откатится в случае ошибки
            return web.json_response({'error': 'Ошибка при работе с базой данных'}, status=500)
            
    return web.json_response({'status': 'success', 'message': f'Ответы сохранены! Начислено {points_to_add} очков.'})

async def handle_index(request):
    """Отдает главный HTML файл"""
    try:
        with open('./index.html', 'r', encoding='utf-8') as f:
            return web.Response(text=f.read(), content_type='text/html')
    except FileNotFoundError:
        return web.Response(text="404: Not Found", status=404)

# --- УПРАВЛЕНИЕ ЖИЗНЕННЫМ ЦИКЛОМ ПРИЛОЖЕНИЯ ---
async def on_startup(app):
    """Выполняется при старте сервера и заполняет таблицу с вопросами, если она пуста."""
    app['database_connected'] = False
    if database:
        try:
            await database.connect()
            engine = sqlalchemy.create_engine(DATABASE_URL)
            metadata.create_all(engine)
            logging.info("Подключение к базе данных установлено.")
            
            # Проверяем и заполняем таблицу с вопросами
            count_query = sqlalchemy.select(sqlalchemy.func.count(questions.c.id))
            count = await database.fetch_val(count_query)
            if count == 0 and GENESIS_QUESTIONS:
                logging.info("Таблица 'questions' пуста. Загружаем вопросы из questions.json...")
                # Убираем лишние ключи, которых нет в таблице
                questions_to_insert = [
                    {"id": q["id"], "text": q["text"], "category": q["category"], "options": q.get("options")} 
                    for q in GENESIS_QUESTIONS
                ]
                insert_query = questions.insert()
                await database.execute_many(query=insert_query, values=questions_to_insert)
                logging.info(f"Успешно загружено {len(questions_to_insert)} вопросов в БД.")

            app['database_connected'] = True
        except Exception as e:
            logging.critical(f"Не удалось подключиться к БД или загрузить вопросы при старте: {e}")
            
async def on_shutdown(app):
    """Выполняется при остановке сервера"""
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

app.on_startup.append(on_startup)
app.on_shutdown.append(on_shutdown)

if __name__ == "__main__":
    import asyncio
    web.run_app(app, port=PORT, host='0.0.0.0')
