import os
import hmac
import hashlib
import json
from urllib.parse import unquote, parse_qsl
import uuid

from flask import Flask, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.dialects.postgresql import UUID

# --- Конфигурация ---
app = Flask(__name__, static_folder='static')
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'postgresql://user:password@localhost/feudata')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
MASTER_INVITE_CODE = os.environ.get('MASTER_INVITE_CODE', 'FEUDATA-GENESIS-1')

# --- Модели Базы Данных ---
class User(db.Model):
    __tablename__ = 'users'
    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    telegram_id = db.Column(db.BigInteger, unique=True, nullable=False)
    first_name = db.Column(db.String, nullable=True)
    username = db.Column(db.String, nullable=True)
    points = db.Column(db.BigInteger, default=0)
    airdrop_multiplier = db.Column(db.Float, default=1.0)
    invited_by_id = db.Column(UUID(as_uuid=True), db.ForeignKey('users.id'), nullable=True)
    has_completed_genesis = db.Column(db.Boolean, default=False)
    is_searchable = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.TIMESTAMP, server_default=db.func.now())
    inviter = db.relationship('User', remote_side=[id], backref='referrals')

class InviteCode(db.Model):
    __tablename__ = 'invite_codes'
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String, unique=True, nullable=False)
    owner_id = db.Column(UUID(as_uuid=True), db.ForeignKey('users.id'), nullable=False)
    is_used = db.Column(db.Boolean, default=False)
    used_by_id = db.Column(UUID(as_uuid=True), db.ForeignKey('users.id'), nullable=True)
    owner = db.relationship('User', backref='owned_invite_codes', foreign_keys=[owner_id])
    used_by = db.relationship('User', backref='used_invite_code', foreign_keys=[used_by_id])

class GenesisAnswer(db.Model):
    __tablename__ = 'genesis_answers'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(UUID(as_uuid=True), db.ForeignKey('users.id'), nullable=False)
    question_id = db.Column(db.String, nullable=False)
    answer_text = db.Column(db.String, nullable=False)
    submitted_at = db.Column(db.TIMESTAMP, server_default=db.func.now())
    user = db.relationship('User', backref='genesis_answers')

# --- Логика Валидации (ВАЛИДАЦИЯ ОТКЛЮЧЕНА) ---
def validate_init_data(init_data_str):
    """
    !!! ВНИМАНИЕ: ПРОВЕРКА ОТКЛЮЧЕНА !!!
    Эта функция просто извлекает данные пользователя без криптографической проверки.
    """
    try:
        params = dict(parse_qsl(init_data_str, keep_blank_values=True))
        user_data = json.loads(params['user'])
        return user_data
    except Exception:
        # Возвращаем фейковые данные в случае, если строка initData пришла пустой
        return {"id": 123456789, "first_name": "Test", "username": "testuser"}

# --- Middleware ---
@app.before_request
def before_request_func():
    if request.path == '/' or request.path.startswith('/static/'):
        return
    if request.path.startswith('/api/'):
        init_data_str = request.headers.get('X-Telegram-Init-Data')
        if not init_data_str:
            # Для локального тестирования без Telegram можно передать фейковые данные
            init_data_str = 'user={"id":12345,"first_name":"Local","username":"local_user"}'

        user_data = validate_init_data(init_data_str)
        request.user_data = user_data


# --- API Эндпоинты ---
@app.route('/api/status', methods=['POST'])
def get_user_status():
    user_data = request.user_data
    if not user_data or 'id' not in user_data:
        return jsonify({"error": "Invalid user data"}), 400

    telegram_id = user_data['id']
    user = User.query.filter_by(telegram_id=telegram_id).first()
    
    if user:
        invite_codes = [ic.code for ic in InviteCode.query.filter_by(owner_id=user.id, is_used=False).all()]
        return jsonify({ "is_new_user": False, "points": user.points, "has_completed_genesis": user.has_completed_genesis, "invite_codes": invite_codes })

    # Сценарий регистрации
    invite_code = request.json.get('invite_code')
    if not invite_code:
        return jsonify({"error": "Invite code required for new users"}), 400

    inviter = None
    if invite_code != MASTER_INVITE_CODE:
        invite = InviteCode.query.filter_by(code=invite_code, is_used=False).first()
        if not invite: return jsonify({"error": "Invalid or already used invite code"}), 403
        inviter = User.query.get(invite.owner_id)
        
    new_user = User( telegram_id=telegram_id, first_name=user_data.get('first_name'), username=user_data.get('username'), points=1000, invited_by_id=inviter.id if inviter else None )
    db.session.add(new_user)
    
    if inviter:
        db.session.flush()
        invite.is_used = True
        invite.used_by_id = new_user.id
        print(f"SECURITY LOG: User {new_user.telegram_id} registered with code {invite_code} from user {inviter.telegram_id}")
    else:
        print(f"SECURITY LOG: User {new_user.telegram_id} registered with MASTER_CODE")

    db.session.commit()
    return jsonify({ "is_new_user": True, "points": new_user.points, "has_completed_genesis": False, "invite_codes": [] })

# ... (остальной код без изменений) ...
@app.route('/api/genesis_questions', methods=['GET'])
def get_genesis_questions():
    try:
        with open('questions.json', 'r', encoding='utf-8') as f:
            questions = json.load(f)
        return jsonify(questions)
    except FileNotFoundError:
        return jsonify({"error": "questions.json not found"}), 500

@app.route('/api/submit_answers', methods=['POST'])
def submit_answers():
    user_data = request.user_data
    telegram_id = user_data['id']
    user = User.query.filter_by(telegram_id=telegram_id).first()

    if not user: return jsonify({"error": "User not found"}), 404
    if user.has_completed_genesis: return jsonify({"error": "Genesis profile already completed"}), 400

    answers = request.json.get('answers')
    if not answers or not isinstance(answers, list): return jsonify({"error": "Invalid answers format"}), 400
        
    for answer_data in answers:
        new_answer = GenesisAnswer( user_id=user.id, question_id=answer_data.get('question_id'), answer_text=answer_data.get('answer') )
        db.session.add(new_answer)

    user.points += 60000
    user.has_completed_genesis = True
    
    if user.invited_by_id:
        inviter = User.query.get(user.invited_by_id)
        if inviter: 
            inviter.points += 20000
            print(f"SECURITY LOG: User {inviter.telegram_id} received 20000 points for referral of {telegram_id}")


    new_invites = []
    for _ in range(5):
        new_code_str = f'FDT-{uuid.uuid4().hex[:6].upper()}'
        new_invite = InviteCode(code=new_code_str, owner_id=user.id)
        db.session.add(new_invite)
        new_invites.append(new_code_str)

    db.session.commit()
    return jsonify({ "message": "Profile completed successfully!", "new_points_balance": user.points, "new_invite_codes": new_invites })

@app.route('/')
def index():
    return app.send_static_file('index.html')

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(host='0.0.0.0', port=5000)
