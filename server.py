import os
import hmac
import hashlib
import json
from urllib.parse import unquote
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

# ... (модели User, InviteCode, GenesisAnswer остаются без изменений) ...
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


# --- Логика Валидации Telegram (С ОТЛАДКОЙ) ---
def validate_init_data(init_data_str):
    print("--- STARTING InitData VALIDATION ---")
    
    if not BOT_TOKEN:
        print("!!! ERROR: TELEGRAM_BOT_TOKEN environment variable is NOT SET.")
        return None
    
    # Выводим часть токена для проверки, что он вообще есть
    print(f"--> Found Bot Token: ...{BOT_TOKEN[-6:]}")
    print(f"--> Received initData string: {init_data_str}")

    try:
        # Разбираем строку на параметры
        params = {k: v for k, v in [p.split('=') for p in init_data_str.split('&')]}
        
        # Хэш, который прислал Telegram
        received_hash = params.pop('hash', None)
        if not received_hash:
            print("!!! ERROR: Hash not found in initData")
            return None

        # Формируем строку для проверки из остальных параметров
        data_check_string_parts = []
        for key in sorted(params.keys()):
            data_check_string_parts.append(f"{key}={params[key]}")
        
        data_check_string = "\n".join(data_check_string_parts)
        print(f"--> String for hash check:\n{data_check_string}")
        
        # Считаем наш хэш
        secret_key = hmac.new("WebAppData".encode(), BOT_TOKEN.encode(), hashlib.sha256).digest()
        calculated_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

        print(f"--> Received hash:   {received_hash}")
        print(f"--> Calculated hash: {calculated_hash}")

        # Сравниваем хэши
        if calculated_hash == received_hash:
            print("--- VALIDATION SUCCESSFUL ---")
            user_param = params.get('user')
            return json.loads(unquote(user_param))
        else:
            print("!!! ERROR: Hashes DO NOT MATCH.")
            print("--- VALIDATION FAILED ---")
            return None
            
    except Exception as e:
        print(f"!!! CRITICAL ERROR during validation: {e}")
        print("--- VALIDATION FAILED ---")
        return None

# ... (остальной код остается без изменений) ...

@app.before_request
def before_request_func():
    if request.path == '/' or request.path.startswith('/static'):
        return
    if request.path.startswith('/api/'):
        init_data_str = request.headers.get('X-Telegram-Init-Data')
        if not init_data_str:
            return jsonify({"error": "Unauthorized: Missing InitData"}), 401
        user_data = validate_init_data(init_data_str)
        if not user_data:
            return jsonify({"error": "Unauthorized: Invalid InitData"}), 401
        request.user_data = user_data

@app.route('/api/status', methods=['POST'])
def get_user_status():
    user_data = request.user_data
    telegram_id = user_data['id']
    user = User.query.filter_by(telegram_id=telegram_id).first()
    
    if user:
        invite_codes = [ic.code for ic in InviteCode.query.filter_by(owner_id=user.id, is_used=False).all()]
        return jsonify({
            "is_new_user": False,
            "points": user.points,
            "has_completed_genesis": user.has_completed_genesis,
            "invite_codes": invite_codes
        })

    invite_code = request.json.get('invite_code')
    if not invite_code:
        return jsonify({"error": "Invite code required for new users"}), 400

    if invite_code == MASTER_INVITE_CODE:
        inviter = None
    else:
        invite = InviteCode.query.filter_by(code=invite_code, is_used=False).first()
        if not invite:
            return jsonify({"error": "Invalid or already used invite code"}), 403
        inviter = User.query.get(invite.owner_id)
        
    new_user = User(
        telegram_id=telegram_id,
        first_name=user_data.get('first_name'),
        username=user_data.get('username'),
        points=1000,
        invited_by_id=inviter.id if inviter else None
    )
    db.session.add(new_user)
    
    if inviter:
        invite.is_used = True
        invite.used_by_id = new_user.id

    db.session.commit()

    return jsonify({
        "is_new_user": True,
        "points": new_user.points,
        "has_completed_genesis": False,
        "invite_codes": []
    })

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

    if not user:
        return jsonify({"error": "User not found"}), 404
        
    if user.has_completed_genesis:
        return jsonify({"error": "Genesis profile already completed"}), 400

    answers = request.json.get('answers')
    if not answers or not isinstance(answers, list):
        return jsonify({"error": "Invalid answers format"}), 400
        
    for answer_data in answers:
        new_answer = GenesisAnswer(
            user_id=user.id,
            question_id=answer_data.get('question_id'),
            answer_text=answer_data.get('answer')
        )
        db.session.add(new_answer)

    user.points += 60000
    user.has_completed_genesis = True
    
    if user.invited_by_id:
        inviter = User.query.get(user.invited_by_id)
        if inviter:
            inviter.points += 20000

    new_invites = []
    for _ in range(5):
        new_code_str = f'FDT-{uuid.uuid4().hex[:6].upper()}'
        new_invite = InviteCode(code=new_code_str, owner_id=user.id)
        db.session.add(new_invite)
        new_invites.append(new_code_str)

    db.session.commit()

    return jsonify({
        "message": "Profile completed successfully!",
        "new_points_balance": user.points,
        "new_invite_codes": new_invites
    })

@app.route('/')
def index():
    return app.send_static_file('index.html')

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(host='0.0.0.0', port=5000)
