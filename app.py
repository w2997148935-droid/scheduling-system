from flask import Flask, render_template, request, flash, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta
import json

app = Flask(__name__)
# 你的数据库链接保持不变，这里保留占位
app.config['SQLALCHEMY_DATABASE_URI'] = 'postgresql://username:password@hostname:5432/database'
app.config['SECRET_KEY'] = 'secret_key_123456'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

# ==================== 原有用户模型 ====================
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    name = db.Column(db.String(50))
    role = db.Column(db.String(20), default='staff')
    status = db.Column(db.Boolean, default=True)
    group = db.Column(db.String(50), default='默认组')

# ==================== 排班表 ====================
class Schedule(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    date = db.Column(db.String(20), nullable=False)
    slot = db.Column(db.Integer, nullable=False)
    status = db.Column(db.String(20), default='待确认')

# ==================== 请假换班申请表 ====================
class ShiftRequest(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    applicant_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    schedule_id = db.Column(db.Integer, db.ForeignKey('schedule.id'), nullable=False)
    target_user_id = db.Column(db.Integer, nullable=True)
    type = db.Column(db.String(20), nullable=False)
    status = db.Column(db.String(20), default='待审批')
    approve_user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)

# ==================== 值班统计 ====================
class ScheduleStats(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    count = db.Column(db.Integer, default=0)

# ==================== 【新增】选班意向表 ====================
class SelectIntent(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    date = db.Column(db.String(20), nullable=False)
    slot = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.now)

@login_manager.user_loader
def load_user(uid):
    return User.query.get(int(uid))

# ==================== 员工页面 ====================
@app.route('/staff')
@login_required
def staff():
    dates = [(datetime.now() + timedelta(days=i)).strftime('%Y-%m-%d') for i in range(14)]
    my_schedules = Schedule.query.filter_by(user_id=current_user.id).all()
    requests = ShiftRequest.query.filter_by(applicant_id=current_user.id).all()
    return render_template('staff.html', dates=dates, my_schedules=my_schedules, requests=requests)

# ==================== 提交申请 ====================
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
        
        # 请假/换班：提交审批
        elif req_type in ["请假", "换班"]:
            sch_id = int(request.form.get('schedule_id'))
            reason = request.form.get('reason', '')
            req = ShiftRequest(
                applicant_id=current_user.id,
                schedule_id=sch_id,
                type=f"{req_type}：{reason}",
                status="待审批"
            )
            db.session.add(req)
            flash("✅ 申请已提交，等待管理员审批")
        else:
            flash("❌ 申请类型错误")
            
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        flash(f"❌ 提交失败：{str(e)}")
    return redirect(url_for('staff'))

# ==================== 管理员页面 ====================
@app.route('/admin')
@login_required
def admin():
    if current_user.role != 'admin':
        return redirect(url_for('staff'))
    
    users = User.query.all()
    requests = ShiftRequest.query.filter_by(status='待审批').all()
    final_schedules = Schedule.query.filter_by(status='已确认').all()
    stats = ScheduleStats.query.all()
    dates = sorted(list({d.date for d in final_schedules}))
    return render_template('admin.html', users=users, requests=requests,
                         schedules=final_schedules, stats=stats, dates=dates)

# ==================== 管理员生成最终排班 ====================
@app.route('/generate_schedule', methods=['POST'])
@login_required
def generate_schedule():
    try:
        start_date = request.form.get('start_date')
        end_date = request.form.get('end_date')
        need_num = int(request.form.get('need_num', 1))

        # 清空旧排班
        Schedule.query.filter_by(status='已确认').delete()
        
        # 生成日期列表
        s_date = datetime.strptime(start_date, '%Y-%m-%d')
        e_date = datetime.strptime(end_date, '%Y-%m-%d')
        date_list = []
        while s_date <= e_date:
            date_list.append(s_date.strftime('%Y-%m-%d'))
            s_date += timedelta(days=1)
        
        # 生成排班
        for date in date_list:
            for slot in range(1,7):
                intents = SelectIntent.query.filter_by(date=date, slot=slot).all()
                user_ids = [i.user_id for i in intents]
                unique_ids = list(set(user_ids))[:need_num]
                
                for uid in unique_ids:
                    sch = Schedule(user_id=uid, date=date, slot=slot, status='已确认')
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

# ==================== 登录 ====================
@app.route('/login', methods=['GET','POST'])
def login():
    if request.method == 'POST':
        u = User.query.filter_by(username=request.form['username']).first()
        if u and check_password_hash(u.password, request.form['password']):
            login_user(u)
            return redirect(url_for('admin' if u.role=='admin' else 'staff'))
        flash('账号或密码错误')
    return render_template('login.html')

# ==================== 登出 ====================
@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

# ==================== 初始化数据库 ====================
with app.app_context():
    db.create_all()
    if not User.query.filter_by(username='admin').first():
        db.session.add(User(
            username='admin',
            password=generate_password_hash('admin123'),
            name='超级管理员',
            role='admin'
        ))
        db.session.commit()

if __name__ == '__main__':
    app.run(debug=True)
