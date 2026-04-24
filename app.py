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

class ScheduleConfig(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    start_date = db.Column(db.String(20), nullable=False)
    end_date = db.Column(db.String(20), nullable=False)
    status = db.Column(db.String(10), default='active')  # active=生效

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
    # 获取生效的选班时段
    config = ScheduleConfig.query.filter_by(status='active').first()
    dates = []
    if config:
        s = datetime.strptime(config.start_date, '%Y-%m-%d')
        e = datetime.strptime(config.end_date, '%Y-%m-%d')
        while s <= e:
            dates.append(s.strftime('%Y-%m-%d'))
            s += timedelta(days=1)
    
    my_schedules = Schedule.query.filter_by(user_id=current_user.id).all()
    requests = ShiftRequest.query.filter_by(applicant_id=current_user.id).all()
    my_schedules = Schedule.query.filter_by(user_id=current_user.id, status='已确认').all()
    all_intents = SelectIntent.query.all()
    all_schedules = Schedule.query.filter_by(status='已确认').all()
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
    import json
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

# ==================== 新增：请假/换班审批接口 ====================
@app.route('/approve/<int:req_id>/<action>')
@login_required
def approve_request(req_id, action):
    if current_user.role != 'admin':
        return redirect(url_for('staff'))
    req = ShiftRequest.query.get_or_404(req_id)
    req.status = '已同意' if action == 'ok' else '已拒绝'
    db.session.commit()
    flash('审批完成！')
    return redirect(url_for('admin'))


# -------------------------- 管理员端 --------------------------
@app.route('/admin')
@login_required
def admin():
    if current_user.role != 'admin':
        return redirect(url_for('staff'))
    
    users = User.query.all()
    user_dict = {u.id: u.name for u in users}
    requests = ShiftRequest.query.filter_by(status='待审批').all()
    schedules = Schedule.query.filter_by(status='已确认').all()
    config = ScheduleConfig.query.filter_by(status='active').first()
    dates = sorted({d.date for d in schedules})

    # 🔥 纯Python内存计算值班次数（不操作数据库，0报错，0性能影响）
    user_count = {}
    for s in schedules:
        user_count[s.user_id] = user_count.get(s.user_id, 0) + 1

    # 🔥 彻底删除 stats 参数，永不查询报错的表
    return render_template('admin.html', 
        users=users, requests=requests, schedules=schedules,
        dates=dates, config=config, user_dict=user_dict, user_count=user_dict
    )

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


# ==================== 新增：设置管理员接口 ====================
@app.route('/set_admin/<int:uid>')
@login_required
def set_admin(uid):
    if current_user.role != 'admin':
        return redirect(url_for('staff'))
    user = User.query.get_or_404(uid)
    user.role = 'admin'
    db.session.commit()
    flash(f'已设置 {user.name} 为管理员！')
    return redirect(url_for('user_list'))

# ==================== 【核心】管理员生成最终排班表 ====================
@app.route('/generate_schedule', methods=['POST'])
@login_required
def generate_schedule():
    try:
        start_date = request.form.get('start_date')
        end_date = request.form.get('end_date')
        need_num = int(request.form.get('need_num', 1))
        group = request.form.get('group', '').strip()
        max_per_day = int(request.form.get('max_per_day', 1))  # 每日最多班次

        # ========== 修复外键报错 ==========
        ShiftRequest.query.delete()
        Schedule.query.filter_by(status='已确认').delete()

        # 日期列表
        s_date = datetime.strptime(start_date, '%Y-%m-%d')
        e_date = datetime.strptime(end_date, '%Y-%m-%d')
        date_list = []
        while s_date <= e_date:
            date_list.append(s_date.strftime('%Y-%m-%d'))
            s_date += timedelta(days=1)

        # 全局计数：实现平均分配
        user_total_count = {}

        # 开始排班
        for date in date_list:
            # 每日计数
            user_day_count = {}

            for slot in range(1, 7):
                intents = SelectIntent.query.filter_by(date=date, slot=slot).all()
                candidates = []
                for i in intents:
                    u = User.query.get(i.user_id)
                    if u:
                        if group and u.group != group:
                            continue
                        # 每日上限判断
                        day_c = user_day_count.get(u.id, 0)
                        if day_c >= max_per_day:
                            continue
                        candidates.append(u)

                # 去重
                candidates = list({u.id: u for u in candidates}.values())

                # 按【总班次最少】排序 → 实现平均
                candidates.sort(key=lambda x: user_total_count.get(x.id, 0))

                # 选取需要人数
                selected = candidates[:need_num]

                for u in selected:
                    sch = Schedule(user_id=u.id, date=date, slot=slot, status='已确认')
                    db.session.add(sch)
                    user_day_count[u.id] = user_day_count.get(u.id, 0) + 1
                    user_total_count[u.id] = user_total_count.get(u.id, 0) + 1

        db.session.commit()
        flash(f"✅ 排班成功！每日每人最多{max_per_day}次，已平均分配")

    except Exception as e:
        db.session.rollback()
        flash(f"❌ 失败：{str(e)}")

    return redirect(url_for('admin'))
    
# ==================== 新增：独立页面 - 全体人员列表 ====================
@app.route('/users')
@login_required
def user_list():
    if current_user.role != 'admin':
        return redirect(url_for('staff'))
    users = User.query.all()

    # 计算值班次数
    schedules = Schedule.query.filter_by(status='已确认').all()
    count_data = {}
    for s in schedules:
        count_data[s.user_id] = count_data.get(s.user_id, 0) + 1

    return render_template('users.html', users=users, count_data=count_data)
    
# ==================== 新增：独立页面 - 值班统计 ====================
@app.route('/stats')
@login_required
def stats_page():
    if current_user.role != 'admin':
        return redirect(url_for('staff'))
    users = User.query.all()
    schedules = Schedule.query.filter_by(status='已确认').all()
    
    # 内存计算次数
    count_data = {}
    for s in schedules:
        count_data[s.user_id] = count_data.get(s.user_id, 0) + 1
        
    return render_template('stats.html', users=users, count_data=count_data)

# ==================== 新增：管理员发布选班时段 ====================
@app.route('/save_config', methods=['POST'])
@login_required
def save_config():
    if current_user.role != 'admin':
        return redirect(url_for('staff'))
    start = request.form.get('start_date')
    end = request.form.get('end_date')
    # 关闭旧配置，生效新配置
    ScheduleConfig.query.update({ScheduleConfig.status: 'inactive'})
    db.session.add(ScheduleConfig(start_date=start, end_date=end))
    db.session.commit()
    flash('选班时间段发布成功！员工页面已同步')
    return redirect(url_for('admin'))


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

# ==================== 恢复：删除员工 ====================
@app.route('/delete_user/<int:uid>')
@login_required
def delete_user(uid):
    if current_user.role != 'admin':
        return redirect(url_for('staff'))
    user = User.query.get_or_404(uid)
    if user.username == 'admin':
        flash('无法删除超级管理员！')
    else:
        db.session.delete(user)
        db.session.commit()
        flash('员工删除成功！')
    return redirect(url_for('user_list'))

# ==================== 取消管理员 ====================
@app.route('/unset_admin/<int:uid>')
@login_required
def unset_admin(uid):
    if current_user.role != 'admin':
        return redirect(url_for('staff'))
    user = User.query.get_or_404(uid)
    if user.username == 'admin':
        flash('无法取消超级管理员！')
    else:
        user.role = 'staff'
        db.session.commit()
        flash(f'已取消 {user.name} 的管理员权限！')
    return redirect(url_for('user_list'))

# ==================== 检索人员 ====================
@app.route('/search_users')
@login_required
def search_users():
    if current_user.role != 'admin':
        return redirect(url_for('staff'))
    keyword = request.args.get('keyword', '')
    users = User.query.filter(
        db.or_(
            User.name.contains(keyword),
            User.username.contains(keyword),
            User.group.contains(keyword)
        )
    ).all()
    schedules = Schedule.query.filter_by(status='已确认').all()
    count_data = {}
    for s in schedules:
        count_data[s.user_id] = count_data.get(s.user_id, 0) + 1
    return render_template('users.html', users=users, count_data=count_data, keyword=keyword)

# ==================== 检索统计 ====================
@app.route('/search_stats')
@login_required
def search_stats():
    if current_user.role != 'admin':
        return redirect(url_for('staff'))
    keyword = request.args.get('keyword', '')
    users = User.query.filter(
        db.or_(User.name.contains(keyword), User.group.contains(keyword))
    ).all()
    schedules = Schedule.query.filter_by(status='已确认').all()
    count_data = {}
    for s in schedules:
        count_data[s.user_id] = count_data.get(s.user_id, 0) + 1
    return render_template('stats.html', users=users, count_data=count_data, keyword=keyword)

# ==================== 修改分组 ====================
@app.route('/set_group/<int:uid>', methods=['POST'])
@login_required
def set_group(uid):
    if current_user.role != 'admin':
        return redirect(url_for('staff'))
    user = User.query.get_or_404(uid)
    group = request.form.get('group', '默认组')
    user.group = group
    db.session.commit()
    flash(f'已将 {user.name} 分组改为：{group}')
    return redirect(url_for('user_list'))

# ==================== 值班次数归零（清空所有已确认排班）====================
@app.route('/reset_stats')
@login_required
def reset_stats():
    if current_user.role != 'admin':
        return redirect(url_for('staff'))
    
    try:
        # 清空所有已确认排班 → 次数自动归零
        Schedule.query.filter_by(status='已确认').delete()
        db.session.commit()
        flash('✅ 所有值班次数已归零！')
    except Exception as e:
        db.session.rollback()
        flash(f'❌ 归零失败：{str(e)}')
    
    return redirect(url_for('stats_page'))
    
# 启动服务（Render端口兼容）
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
