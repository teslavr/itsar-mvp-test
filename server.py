# server.py
# ВЕРСИЯ 12: Исправлена ошибка 'name 'questions' is not defined'

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
            questions_data = json.load(f)
            logging.info(f"Успешно загружено {len(questions_data)} вопросов из questions.json")
            return questions_data
    except Exception as e:
        logging.error(f"Критическая ошибка при чтении questions.json: {e}")
        return []

GENESIS_QUESTIONS = load_questions_from_file()

# --- НАСТРОЙКА БАЗЫ ДАННЫХ И ТАБЛИЦ ---
database = None
metadata = sqlalchemy.MetaData()

# Определяем таблицы глобально, чтобы они были доступны везде
# ЗАМЕНИТЬ ЭТОТ БЛОК В server.py

users = sqlalchemy.Table(
    "users", metadata,
    sqlalchemy.Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
    sqlalchemy.Column("telegram_id", sqlalchemy.BigInteger, unique=True, nullable=False),
    sqlalchemy.Column("username", sqlalchemy.String, nullable=True),
    sqlalchemy.Column("first_name", sqlalchemy.String, nullable=True),
    sqlalchemy.Column("points", sqlalchemy.BigInteger, default=0),
    sqlalchemy.Column("referral_code", sqlalchemy.String, unique=True, default=lambda: str(uuid.uuid4())),
    sqlalchemy.Column("invited_by_id", UUID(as_uuid=True), sqlalchemy.ForeignKey("users.id"), nullable=True),
    # НОВОЕ ПОЛЕ:
    sqlalchemy.Column("has_completed_genesis", sqlalchemy.Boolean, default=False, nullable=False),
    sqlalchemy.Column("created_at", sqlalchemy.DateTime, server_default=sqlalchemy.func.now()),
)

questions = sqlalchemy.Table(
    "questions", metadata,
    sqlalchemy.Column("id", sqlalchemy.Integer, primary_key=True, autoincrement=False), # autoincrement=False, так как мы задаем ID из JSON
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
    """Проверяет, зарегистрирован ли пользователь."""
    if not database or not app.get('database_connected'): return web.json_response({'error': 'DB connection failed'}, status=503)
    try:
        telegram_id = int(request.query['telegram_id'])
    except (KeyError, ValueError): return web.json_response({'error': 'telegram_id не указан'}, status=400)
    
    query = users.select().where(users.c.telegram_id == telegram_id)
    try:
        user = await database.fetch_one(query)
    except Exception as e: return web.json_response({'error': 'Ошибка при обращении к БД'}, status=500)
    
    if user:
        return web.json_response({
            'status': 'registered', 
            'user_id': str(user['id']), 
            'points': user['points'], 
            'referral_code': user['referral_code'],
            'has_completed_genesis': user['has_completed_genesis'] # НОВОЕ ПОЛЕ В ОТВЕТЕ
        })
    else:
        return web.json_response({'status': 'not_registered'}, status=404)

async def register_user(request):
    """Регистрирует нового пользователя и начисляет бонус пригласившему."""
    logging.info("API: /api/register вызван.")
    
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
    
    # Проверяем, не зарегистрирован ли пользователь уже
    if await database.fetch_one(users.select().where(users.c.telegram_id == telegram_id)):
        return web.json_response({'error': 'Пользователь уже зарегистрирован'}, status=409)
    
    # Используем транзакцию для гарантии целостности данных
    async with database.transaction():
        try:
            # 1. Ищем инвайтера и получаем его ID
            inviter_id = None
            if inviter_code:
                inviter = await database.fetch_one(users.select().where(users.c.referral_code == inviter_code))
                if inviter:
                    inviter_id = inviter['id']
                    logging.info(f"Найден инвайтер с ID: {inviter_id}")

            # 2. Создаем нового пользователя
            new_user_id = uuid.uuid4()
            new_referral_code = str(uuid.uuid4())
            insert_query = users.insert().values(
                id=new_user_id,
                telegram_id=telegram_id,
                username=username,
                first_name=first_name,
                points=1000, # Начальные очки за регистрацию
                referral_code=new_referral_code,
                invited_by_id=inviter_id,
                has_completed_genesis=False # ИСПРАВЛЕНИЕ: Добавили недостающее поле
            )
            await database.execute(insert_query)
            logging.info(f"Зарегистрирован новый пользователь: tg_id={telegram_id}")

            # 3. Если был инвайтер, начисляем ему бонус
            if inviter_id:
                bonus_points = 20000 # Бонус за реферала по нашей математике
                update_query = users.update().where(users.c.id == inviter_id).values(
                    points=users.c.points + bonus_points
                )
                await database.execute(update_query)
                logging.info(f"Начислено {bonus_points} очков инвайтеру {inviter_id}")
        
        except Exception as e:
            logging.error(f"Ошибка БД при регистрации или начислении бонуса: {e}")
            # Транзакция автоматически откатится, если здесь произойдет ошибка
            return web.json_response({'error': 'Ошибка при записи в БД'}, status=500)
            
    return web.json_response({'status': 'success'}, status=201)

async def get_genesis_questions(request):
    logging.info("API: /api/genesis_questions вызван.")
    if not database or not app.get('database_connected'): return web.json_response({'error': 'DB connection failed'}, status=503)

    try:
        count_query = sqlalchemy.select(sqlalchemy.func.count(questions.c.id))
        count = await database.fetch_val(count_query)
        if count == 0 and GENESIS_QUESTIONS:
            logging.info("Таблица 'questions' пуста. Загружаем вопросы...")
            questions_to_insert = [
                {"id": q["id"], "text": q["text"], "category": q["category"], "options": json.dumps(q.get("options"))}
                for q in GENESIS_QUESTIONS
            ]
            insert_query = questions.insert()
            await database.execute_many(query=insert_query, values=questions_to_insert)
            logging.info(f"Успешно загружено {len(questions_to_insert)} вопросов в БД.")
    except Exception as e:
        logging.error(f"Ошибка при проверке/загрузке вопросов в БД: {e}")
        return web.json_response({'error': 'Не удалось подготовить вопросы'}, status=500)

    return web.json_response(GENESIS_QUESTIONS)


# ЗАМЕНИТЬ ЭТУ ФУНКЦИЮ В server.py

async def submit_answers(request):
    """Принимает ответы, сохраняет их в БД и начисляет очки."""
    logging.info("API: /api/submit_answers вызван.")
    if not database or not app.get('database_connected'): return web.json_response({'error': 'DB connection failed'}, status=503)

    try:
        data = await request.json()
        user_id_str, user_answers = data.get('user_id'), data.get('answers')
        if not user_id_str or not user_answers: return web.json_response({'error': 'Отсутствует ID или ответы'}, status=400)
        user_id = uuid.UUID(user_id_str)
    except Exception as e: return web.json_response({'error': 'Некорректный формат запроса'}, status=400)

    async with database.transaction():
        try:
            # Проверяем, не отвечал ли пользователь уже
            user_check = await database.fetch_one(users.select().where(users.c.id == user_id))
            if user_check and user_check['has_completed_genesis']:
                logging.warning(f"Пользователь {user_id_str} уже проходил Генезис-Профиль. Повторное начисление очков отменено.")
                return web.json_response({'error': 'Вы уже проходили эту анкету'}, status=403) # 403 Forbidden

            answers_to_insert = [{"user_id": user_id, "question_id": int(q_id), "answer_text": ans} for q_id, ans in user_answers.items()]
            if answers_to_insert:
                await database.execute_many(query=answers.insert(), values=answers_to_insert)
            
            points_to_add = 10000 # Бонус за прохождение Генезис-Профиля
            # ИЗМЕНЕНИЕ: Обновляем и баланс, и статус прохождения
            update_query = users.update().where(users.c.id == user_id).values(
                points=users.c.points + points_to_add,
                has_completed_genesis=True
            )
            await database.execute(update_query)
            logging.info(f"Начислено {points_to_add} очков пользователю {user_id_str} за Генезис-Профиль.")
        except Exception as e:
            logging.error(f"Ошибка при сохранении ответов или начислении очков: {e}")
            return web.json_response({'error': 'Ошибка при работе с БД'}, status=500)
            
    return web.json_response({'status': 'success', 'message': f'Ответы сохранены! Начислено {points_to_add} очков.'})
    async with database.transaction():
        try:
            answers_to_insert = [{"user_id": user_id, "question_id": int(q_id), "answer_text": ans} for q_id, ans in user_answers.items()]
            if answers_to_insert:
                await database.execute_many(query=answers.insert(), values=answers_to_insert)
                logging.info(f"Сохранено {len(answers_to_insert)} ответов для {user_id_str}")
            
            points_to_add = len(answers_to_insert) * 100
            await database.execute(users.update().where(users.c.id == user_id).values(points=users.c.points + points_to_add))
            logging.info(f"Начислено {points_to_add} очков пользователю {user_id_str}")
        except Exception as e:
            logging.error(f"Ошибка при сохранении ответов или начислении очков: {e}")
            return web.json_response({'error': 'Ошибка при работе с БД'}, status=500)
            
    return web.json_response({'status': 'success', 'message': f'Ответы сохранены! Начислено {points_to_add} очков.'})

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

app.on_startup.append(on_startup)
app.on_shutdown.append(on_shutdown)

if __name__ == "__main__":
    web.run_app(app, port=PORT, host='0.0.0.0')
