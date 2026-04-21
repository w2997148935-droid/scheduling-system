import os
import pandas as pd
from datetime import datetime, timedelta
from dotenv import load_dotenv
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash

# 加载环境变量
load_dotenv()
app = Flask(__name__)

# 核心配置（自动修复PostgreSQL协议，适配Python3.14）
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'test123456')
# 🔥 关键修复：自动替换数据库协议，解决psycopg2缺失报错
raw_db_url = os.getenv('DATABASE_URL')
if raw_db_url and raw_db_url.startswith('postgresql://'):
    raw_db_url = raw_db_url.replace('postgresql://', 'postgresql+psycopg://')
app.config['SQLALCHEMY_DATABASE_URI'] = raw_db_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# 环境变量默认值，防止None报错
app.config['MAX_WORK_SLOTS'] = int(os.getenv('MAX_WORK_HOURS_PER_DAY', '2'))
app.config['TIME_SLOTS'] = int(os.getenv('TOTAL_TIME_SLOTS', '6'))

# 初始化组件
db = SQLAlchemy(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

# 模板过滤器（修复管理员页显示）
@app.template_filter('user_name')
def get_user_name(user_id):
    user = User.query.get(user_id)
    return user.name if user else '未知用户'

# -------------------------- 数据库模型 --------------------------
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    name = db.Column(db.String(50), nullable=False)
    group = db.Column(db.String(50), default='默认组')
    role = db.Column(db.String(20), default='staff')
    status = db.Column(db.Boolean, default=True)

class FreeTime(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    date = db.Column(db.Date, nullable=False)
    slot = db.Column(db.Integer, nullable=False)
    is_free = db.Column(db.Boolean, default=True)
    __table_args__ = (db.UniqueConstraint('user_id', 'date', 'slot'),)

class Schedule(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    date = db.Column(db.Date, nullable=False)
    slot = db.Column(db.Integer, nullable=False)
    status = db.Column(db.String(20), default='正常')
    __table_args__ = (db.UniqueConstraint('user_id', 'date', 'slot'),)

class ScheduleStats(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), unique=True, nullable=False)
    total_count = db.Column(db.Integer, default=0)
    group = db.Column(db.String(50))

class ShiftRequest(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    applicant_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    schedule_id = db.Column(db.Integer, db.ForeignKey('schedule.id'), nullable=False)
    target_user_id = db.Column(db.Integer, nullable=True)
    type = db.Column(db.String(20), nullable=False)
    status = db.Column(db.String(20), default='待审批')
    approve_user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)

# -------------------------- 登录管理 --------------------------
@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# -------------------------- 公共路由 --------------------------
@app.route('/')
def index():
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username'].strip()
        password = request.form['password'].strip()
        
        # 🔍 仅查询账号，不限制角色/状态
        user = User.query.filter_by(username=username).first()
        
        # 校验密码
        if user and check_password_hash(user.password, password):
            login_user(user)
            # 管理员跳后台，员工跳员工页
            if user.role == 'admin':
                return redirect(url_for('admin'))
            else:
                return redirect(url_for('staff'))
        else:
            flash('账号或密码错误！')
            
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

# -------------------------- 员工端 --------------------------
@app.route('/staff')
@login_required
def staff():
    if current_user.role != 'staff':
        return redirect(url_for('admin'))
    dates = [datetime.now().date() + timedelta(days=i) for i in range(30)]
    free_times = FreeTime.query.filter_by(user_id=current_user.id).all()
    free_data = {(f.date, f.slot): f.is_free for f in free_times}
    schedules = Schedule.query.filter_by(user_id=current_user.id).all()
    requests = ShiftRequest.query.filter_by(applicant_id=current_user.id).all()
    return render_template('staff.html', user=current_user, dates=dates, slots=range(1, app.config['TIME_SLOTS']+1), free_data=free_data, schedules=schedules, requests=requests)

@app.route('/submit_free', methods=['POST'])
@login_required
def submit_free():
    data = request.json
    date = datetime.strptime(data['date'], '%Y-%m-%d').date()
    slot = int(data['slot'])
    is_free = data['is_free']
    free = FreeTime.query.filter_by(user_id=current_user.id, date=date, slot=slot).first()
    if free:
        free.is_free = is_free
    else:
        free = FreeTime(user_id=current_user.id, date=date, slot=slot, is_free=is_free)
        db.session.add(free)
    db.session.commit()
    return jsonify({'success': True})

@app.route('/submit_request', methods=['POST'])
@login_required
def submit_request():
    schedule_id = request.form['schedule_id']
    req_type = request.form['type']
    target_user_id = request.form.get('target_user_id')
    req = ShiftRequest(applicant_id=current_user.id, schedule_id=schedule_id, target_user_id=target_user_id, type=req_type)
    db.session.add(req)
    db.session.commit()
    flash('申请提交成功，等待管理员审批')
    return redirect(url_for('staff'))

# -------------------------- 管理员端 --------------------------
@app.route('/admin')
@login_required
def admin():
    if current_user.role != 'admin':
        flash('无管理员权限')
        return redirect(url_for('staff'))
    users = User.query.all()
    stats = ScheduleStats.query.all()
    requests = ShiftRequest.query.filter_by(status='待审批').all()
    schedules = Schedule.query.all()
    return render_template('admin.html', user=current_user, users=users, stats=stats, requests=requests, schedules=schedules)

@app.route('/import_users', methods=['POST'])
@login_required
def import_users():
    file = request.files['file']
    df = pd.read_excel(file, engine='openpyxl')
    df.columns = df.columns.str.strip()

    for _, row in df.iterrows():
        # 强制清理所有空白字符
        username = str(row['账号']).strip()
        password_raw = str(row['密码']).strip()
        name = str(row['姓名']).strip()
        group = str(row.get('组别', '默认组')).strip()

        # 跳过空数据
        if not username or len(username) < 1 or not password_raw:
            continue

        # 检查重复
        if User.query.filter_by(username=username).first():
            continue

        # ✅ 强制创建正常员工（状态启用 + 普通员工角色）
        user = User()
        user.username = username
        user.password = generate_password_hash(password_raw)  # 强制加密
        user.name = name
        user.group = group
        user.role = "staff"
        user.status = True  # 启用账号（关键！）

        db.session.add(user)
        db.session.flush()
        # 创建统计
        db.session.add(ScheduleStats(user_id=user.id, group=user.group))

    db.session.commit()
    flash('员工导入完成！账号密码均正常可用！')
    return redirect(url_for('admin'))

@app.route('/manage_user', methods=['POST'])
@login_required
def manage_user():
    action = request.form['action']
    if action == 'add':
        # 创建用户
        user = User(
            username=request.form['username'],
            password=generate_password_hash(request.form['password']),
            name=request.form['name'],
            group=request.form['group'],
            role=request.form['role']
        )
        db.session.add(user)
        db.session.flush()
        # 添加统计
        db.session.add(ScheduleStats(user_id=user.id, group=user.group))

    elif action == 'edit':
        user = User.query.get(request.form['id'])
        user.name = request.form['name']
        user.group = request.form['group']
        user.role = request.form['role']
        if request.form['password']:
            user.password = generate_password_hash(request.form['password'])

    elif action == 'delete':
        user_id = int(request.form['id'])
        # 🔥 核心保护：禁止删除超级管理员（ID=1）
        if user_id == 1:
            flash('错误：超级管理员无法被删除！')
            return redirect(url_for('admin'))
        
        # 级联删除普通员工数据
        FreeTime.query.filter_by(user_id=user_id).delete()
        Schedule.query.filter_by(user_id=user_id).delete()
        ScheduleStats.query.filter_by(user_id=user_id).delete()
        ShiftRequest.query.filter_by(applicant_id=user_id).delete()
        ShiftRequest.query.filter_by(approve_user_id=user_id).delete()
        User.query.filter_by(id=user_id).delete()
        
    db.session.commit()
    return redirect(url_for('admin'))

@app.route('/approve_request/<int:req_id>/<status>')
@login_required
def approve_request(req_id, status):
    req = ShiftRequest.query.get(req_id)
    req.status = status
    req.approve_user_id = current_user.id
    if status == '通过':
        schedule = Schedule.query.get(req.schedule_id)
        schedule.status = req.type
        if req.type == '换班' and req.target_user_id:
            schedule.user_id = req.target_user_id
    db.session.commit()
    return redirect(url_for('admin'))

@app.route('/generate_schedule')
@login_required
def generate_schedule():
    if FreeTime.query.count() == 0:
        flash('请等待员工提交空闲时间后再生成排班')
        return redirect(url_for('admin'))
    Schedule.query.delete()
    dates = [datetime.now().date() + timedelta(days=i) for i in range(30)]
    slots = range(1, app.config['TIME_SLOTS']+1)
    group_users = {}
    for user in User.query.filter_by(role='staff', status=True).all():
        group = user.group
        if group not in group_users:
            group_users[group] = []
        group_users[group].append(user)
    for date in dates:
        for slot in slots:
            for group, users in group_users.items():
                candidates = []
                for user in users:
                    free = FreeTime.query.filter_by(user_id=user.id, date=date, slot=slot, is_free=True).first()
                    if not free: continue
                    if Schedule.query.filter_by(user_id=user.id, date=date).count() >= app.config['MAX_WORK_SLOTS']: continue
                    stat = ScheduleStats.query.filter_by(user_id=user.id).first()
                    candidates.append((user, stat.total_count if stat else 0))
                if candidates:
                    candidates.sort(key=lambda x: x[1])
                    target = candidates[0][0]
                    db.session.add(Schedule(user_id=target.id, date=date, slot=slot))
                    stat = ScheduleStats.query.filter_by(user_id=target.id).first()
                    stat.total_count += 1
    db.session.commit()
    flash('排班表生成成功！')
    return redirect(url_for('admin'))

# 批量删除员工
# 批量删除员工
@app.route('/batch_delete_users', methods=['POST'])
@login_required
def batch_delete_users():
    if current_user.role != 'admin':
        flash('无权限')
        return redirect(url_for('admin'))
    
    user_ids = request.form.getlist('user_ids')
    deleted_count = 0
    
    if user_ids:
        for user_id_str in user_ids:
            user_id = int(user_id_str)
            # 🔥 核心保护：跳过超级管理员，不执行删除
            if user_id == 1:
                continue
            
            # 删除普通员工关联数据
            FreeTime.query.filter_by(user_id=user_id).delete()
            Schedule.query.filter_by(user_id=user_id).delete()
            ScheduleStats.query.filter_by(user_id=user_id).delete()
            ShiftRequest.query.filter_by(applicant_id=user_id).delete()
            ShiftRequest.query.filter_by(approve_user_id=user_id).delete()
            User.query.filter_by(id=user_id).delete()
            deleted_count += 1
        
        db.session.commit()
        flash(f'成功删除 {deleted_count} 名员工！（超级管理员已自动保护）')
    else:
        flash('请选择要删除的员工')
    return redirect(url_for('admin'))

# 初始化数据库
with app.app_context():
    db.create_all()
    if not User.query.filter_by(username='admin').first():
        db.session.add(User(username='admin', password=generate_password_hash('admin123'), name='超级管理员', role='admin'))
        db.session.commit()

# 启动服务（Render端口兼容）
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
