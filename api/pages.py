"""Page rendering routes."""
from flask import Blueprint, render_template, session, redirect, url_for
from api.auth import login_required

pages_bp = Blueprint('pages', __name__)


@pages_bp.route('/')
@login_required
def index():
    return render_template('index.html')


@pages_bp.route('/hall')
@login_required
def hall():
    return render_template('hall.html')


@pages_bp.route('/register', methods=['GET', 'POST'])
@login_required
def register():
    from flask import request, jsonify
    import os, cv2, numpy as np
    from datetime import datetime
    from models.database import db_connection
    from core.face_recognition import extract_face_embedding

    if request.method == 'GET':
        return render_template('register.html')

    from core.state import face_app
    if face_app is None:
        return jsonify({'ok': False, 'error': 'InsightFace 未加载'}), 500

    name = request.form.get('name', '').strip()
    if not name:
        return jsonify({'ok': False, 'error': '请输入姓名'}), 400
    photos = request.files.getlist('photo')
    photos = [f for f in photos if f and f.filename]
    if len(photos) == 0:
        return jsonify({'ok': False, 'error': '请上传至少一张照片'}), 400

    with db_connection() as conn:
        row = conn.execute('SELECT id FROM persons WHERE name = ?', (name,)).fetchone()
        person_id = row['id'] if row else conn.execute(
            'INSERT INTO persons (name) VALUES (?)', (name,)).lastrowid
        saved = 0
        errors = []
        for pf in photos:
            fn = pf.filename.lower()
            if not (fn.endswith('.jpg') or fn.endswith('.jpeg') or fn.endswith('.png')):
                errors.append(f'{pf.filename}: 格式不支持')
                continue
            try:
                fb = pf.read()
                nparr = np.frombuffer(fb, np.uint8)
                img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
                if img is None:
                    errors.append(f'{pf.filename}: 无法解码')
                    continue
                emb, score = extract_face_embedding(img)
                if emb is None:
                    errors.append(f'{pf.filename}: 未检测到人脸 (det={score:.2f})')
                    continue
                sn = "".join(c for c in name if c.isalnum() or c in ('_', '-', '一-鿿'))
                ts = datetime.now().strftime('%Y%m%d_%H%M%S')
                pfn = f"{sn}_{ts}_{saved}.jpg"
                pp = os.path.join('static', 'uploads', pfn)
                cv2.imwrite(pp, img)
                conn.execute(
                    'INSERT INTO face_embeddings (person_id, embedding_blob, photo_path, det_score) '
                    'VALUES (?, ?, ?, ?)',
                    (person_id, emb.tobytes(), f'/static/uploads/{pfn}', score)
                )
                saved += 1
            except Exception as e:
                errors.append(f'{pf.filename}: {str(e)}')
        conn.commit()

    if saved == 0:
        return jsonify({'ok': False, 'error': f'全部失败: {"; ".join(errors[-3:])}'}), 400
    return jsonify({'ok': True, 'message': f'{name} 注册成功！已保存 {saved} 个面部嵌入',
                    'person_id': person_id, 'saved': saved, 'errors': errors[:3]})


@pages_bp.route('/manage')
@login_required
def manage():
    return render_template('manage.html')


@pages_bp.route('/history')
@login_required
def history():
    return render_template('history.html')


@pages_bp.route('/cameras')
@login_required
def cameras_page():
    return render_template('cameras.html')
