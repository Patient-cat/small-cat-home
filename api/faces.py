"""Face management API routes."""
import os
import cv2
import numpy as np
import logging
from datetime import datetime
from flask import Blueprint, request, jsonify
from api.auth import login_required
from models.database import db_connection
from core.face_recognition import extract_face_embedding

log = logging.getLogger('safesight')

faces_bp = Blueprint('faces', __name__)


@faces_bp.route('/api/faces')
@login_required
def api_faces():
    """List all registered persons with embedding counts."""
    with db_connection() as conn:
        rows = conn.execute(
            'SELECT p.id, p.name, p.created_at, COUNT(e.id) AS embedding_count '
            'FROM persons p LEFT JOIN face_embeddings e ON e.person_id = p.id '
            'GROUP BY p.id ORDER BY p.name'
        ).fetchall()
    persons = []
    for r in rows:
        with db_connection() as conn:
            photos = conn.execute(
                'SELECT photo_path FROM face_embeddings WHERE person_id = ? AND photo_path IS NOT NULL',
                (r['id'],)
            ).fetchall()
        persons.append({
            'id': r['id'], 'name': r['name'],
            'embedding_count': r['embedding_count'],
            'created_at': r['created_at'],
            'photos': [p['photo_path'] for p in photos],
        })
    return jsonify(persons)


@faces_bp.route('/api/faces/<int:face_id>', methods=['DELETE'])
@login_required
def api_delete_face(face_id):
    """Delete a person and all their face embeddings."""
    with db_connection() as conn:
        row = conn.execute('SELECT name FROM persons WHERE id = ?', (face_id,)).fetchone()
        if not row:
            return jsonify({'ok': False, 'error': '人员不存在'}), 404
        conn.execute('DELETE FROM face_embeddings WHERE person_id = ?', (face_id,))
        conn.execute('DELETE FROM persons WHERE id = ?', (face_id,))
        conn.commit()
    log.info('Deleted person: %s (id=%d)', row['name'], face_id)
    return jsonify({'ok': True, 'message': f'{row["name"]} 已删除'})


@faces_bp.route('/api/faces/<int:face_id>/photo', methods=['PUT'])
@login_required
def api_add_face_photo(face_id):
    """Add photos to an existing person."""
    photos = request.files.getlist('photo')
    photos = [f for f in photos if f and f.filename]
    if len(photos) == 0:
        return jsonify({'ok': False, 'error': '请上传至少一张照片'}), 400

    with db_connection() as conn:
        row = conn.execute('SELECT id, name FROM persons WHERE id = ?', (face_id,)).fetchone()
        if row is None:
            return jsonify({'ok': False, 'error': '人员记录不存在'}), 404

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
                sn = "".join(c for c in row['name'] if c.isalnum() or c in ('_', '-', '一-鿿'))
                ts = datetime.now().strftime('%Y%m%d_%H%M%S')
                pfn = f"{sn}_{ts}_{saved}.jpg"
                pp = os.path.join('static', 'uploads', pfn)
                cv2.imwrite(pp, img)
                conn.execute(
                    'INSERT INTO face_embeddings (person_id, embedding_blob, photo_path, det_score) '
                    'VALUES (?, ?, ?, ?)',
                    (face_id, emb.tobytes(), f'/static/uploads/{pfn}', score)
                )
                saved += 1
            except Exception as e:
                errors.append(f'{pf.filename}: {str(e)}')
        conn.commit()

    if saved == 0:
        return jsonify({'ok': False, 'error': f'全部失败: {"; ".join(errors[-3:])}'}), 400
    msg = f"已为 {row['name']} 添加 {saved} 个面部嵌入"
    if errors:
        msg += f'（{len(errors)} 张跳过）'
    return jsonify({'ok': True, 'message': msg, 'saved': saved, 'errors': errors[:3]})


@faces_bp.route('/api/register_face', methods=['POST'])
@login_required
def api_register_face():
    """Register a face via base64 image (API)."""
    data = request.get_json(force=True) or {}
    name = (data.get('name') or '').strip()
    image_b64 = data.get('image_base64', '')
    if not name:
        return jsonify({'ok': False, 'error': '请输入姓名'}), 400
    if not image_b64:
        return jsonify({'ok': False, 'error': '请提供图片'}), 400

    try:
        import base64
        if ',' in image_b64:
            image_b64 = image_b64.split(',')[1]
        img_bytes = base64.b64decode(image_b64)
        nparr = np.frombuffer(img_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img is None:
            return jsonify({'ok': False, 'error': '无法解码 base64 图片'}), 400
        emb, score = extract_face_embedding(img)
        if emb is None:
            return jsonify({'ok': False, 'error': f'未检测到人脸 (det={score:.2f})'}), 400

        with db_connection() as conn:
            row = conn.execute('SELECT id FROM persons WHERE name = ?', (name,)).fetchone()
            person_id = row['id'] if row else conn.execute(
                'INSERT INTO persons (name) VALUES (?)', (name,)
            ).lastrowid
            sn = "".join(c for c in name if c.isalnum() or c in ('_', '-', '一-鿿'))
            ts = datetime.now().strftime('%Y%m%d_%H%M%S')
            pfn = f"{sn}_{ts}_api.jpg"
            pp = os.path.join('static', 'uploads', pfn)
            cv2.imwrite(pp, img)
            conn.execute(
                'INSERT INTO face_embeddings (person_id, embedding_blob, photo_path, det_score) '
                'VALUES (?, ?, ?, ?)',
                (person_id, emb.tobytes(), f'/static/uploads/{pfn}', score)
            )
            conn.commit()
        return jsonify({
            'ok': True, 'message': f'{name} 已注册', 'person_id': person_id,
            'det_score': round(score, 3), 'photo_url': f'/static/uploads/{pfn}',
        })
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500
