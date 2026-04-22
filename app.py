import os
import pandas as pd
from datetime import datetime, timedelta
from dotenv import load_dotenv
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime

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
    schedule_id = db.Column(db.Integer, db.ForeignKey('schedule.id'), nullable=True)
    target_user_id = db.Column(db.Integer, nullable=True)
    type = db.Column(db.String(20), nullable=False)
    status = db.Column(db.String(20), default='待审批')
    approve_user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)

class SelectIntent(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    date = db.Column(db.String(20), nullable=False)
    slot = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.now)

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
    dates = [(datetime.now() + timedelta(days=i)).strftime('%Y-%m-%d') for i in range(14)]
    my_schedules = Schedule.query.filter_by(user_id=current_user.id).all()
    requests = ShiftRequest.query.filter_by(applicant_id=current_user.id).all()
    return render_template('staff.html', dates=dates, my_schedules=my_schedules, requests=requests)
    
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

# ==================== 提交申请（选班=存意向，请假换班=审批）====================
@app.route('/submit_request', methods=['POST'])
@login_required
def submit_request():
    try:
        req_type = request.form.get('type')
        # 选班：提交意向
        if req_type == "选班":
            selected = json.loads(request.form.get('selected_data', '[]'))
            for item in selected:
                intent = SelectIntent(
                    user_id=current_user.id,
                    date=item['date'],
                    slot=int(item['slot'])
                )
                db.session.add(intent)
            flash(f"✅ 选班意向提交成功！等待管理员生成排班")
        
        # ========== 请假/换班：用真实排班，必过约束 ==========
        elif req_type in ["请假", "换班"]:
            sch_id = int(request.form.get('schedule_id'))
            reason = request.form.get('reason', '')
            # 把理由拼到类型里，不新增字段
            full_type = f"{req_type}：{reason}" if reason else req_type
            
            req = ShiftRequest(
                applicant_id=current_user.id,
                schedule_id=sch_id,
                type=full_type,
                status="待审批"
            )
            db.session.add(req)
            flash("申请已提交，等待管理员审批")

        else:
            flash("类型错误")
        
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        flash(f"操作失败：{str(e)}")
    return redirect(url_for('staff'))

# -------------------------- 管理员端 --------------------------
@app.route('/admin')
@login_required
def admin():
    if current_user.role != 'admin':
        return redirect(url_for('staff'))
    
    # 获取所有数据
    users = User.query.all()
    requests = ShiftRequest.query.filter_by(status='待审批').all()
    final_schedules = Schedule.query.filter_by(status='已确认').all()
    stats = ScheduleStats.query.all()
    dates = list({d.date for d in final_schedules})
    dates.sort()
    return render_template('admin.html', users=users, requests=requests,
                         schedules=final_schedules, stats=stats, dates=dates)

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

# ==================== 【核心】管理员生成最终排班表 ====================
@app.route('/generate_schedule', methods=['POST'])
@login_required
def generate_schedule():
    try:
        start_date = request.form.get('start_date')
        end_date = request.form.get('end_date')
        need_num = int(request.form.get('need_num', 1))  # 每班需求人数

        # 清空旧的已确认排班
        Schedule.query.filter_by(status='已确认').delete()
        
        # 生成日期列表
        s_date = datetime.strptime(start_date, '%Y-%m-%d')
        e_date = datetime.strptime(end_date, '%Y-%m-%d')
        date_list = []
        while s_date <= e_date:
            date_list.append(s_date.strftime('%Y-%m-%d'))
            s_date += timedelta(days=1)
        
        # 按时段+日期统计选班意向，生成排班
        for date in date_list:
            for slot in range(1,7):
                # 获取该时段所有选班员工
                intents = SelectIntent.query.filter_by(date=date, slot=slot).all()
                user_ids = [i.user_id for i in intents]
                # 去重 + 限制人数（需求人数/最大人数）
                unique_ids = list(set(user_ids))[:need_num]
                
                for uid in unique_ids:
                    sch = Schedule(
                        user_id=uid, date=date, slot=slot, status='已确认'
                    )
                    db.session.add(sch)
        
        # 更新统计
        ScheduleStats.query.delete()
        for uid in list({u.user_id for u in Schedule.query.filter_by(status='已确认').all()}):
            cnt = Schedule.query.filter_by(user_id=uid, status='已确认').count()
            db.session.add(ScheduleStats(user_id=uid, count=cnt))
        
        db.session.commit()
        flash("✅ 最终排班表生成成功！")
    except Exception as e:
        db.session.rollback()
        flash(f"❌ 生成失败：{str(e)}")
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

# 初始化数据库 + 自动创建虚拟排班（解决所有约束报错）
with app.app_context():
    db.create_all()
    db.session.commit()

    # 🔥 自动创建一个虚拟排班（ID=1，专门用于选班申请）
    if not Schedule.query.get(1):
        dummy_schedule = Schedule(
            user_id=1,  # 超级管理员ID
            date="2025-01-01",
            slot=1,
            status="虚拟班次"
        )
        db.session.add(dummy_schedule)

    # 创建超级管理员
    if not User.query.filter_by(username='admin').first():
        db.session.add(User(
            username='admin',
            password=generate_password_hash('admin123'),
            name='超级管理员',
            role='admin',
            status=True
        ))
    # 创建测试员工
    if not User.query.filter_by(username='test01').first():
        test_user = User(
            username='test01',
            password=generate_password_hash('123456'),
            name='测试员工',
            role='staff',
            status=True
        )
        db.session.add(test_user)
        db.session.flush()
        db.session.add(ScheduleStats(user_id=test_user.id, group='测试组'))
    
    db.session.commit()
    
# 启动服务（Render端口兼容）
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
