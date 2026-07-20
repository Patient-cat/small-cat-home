"""Face recognition — InsightFace embedding extraction and matching."""
import os
import cv2
import numpy as np
import logging
from datetime import datetime

import config as cfg
from models.database import db_connection

log = logging.getLogger('safesight')


def extract_face_embedding(img_bgr):
    """Extract face embedding from a BGR image.

    Returns:
        (embedding: np.ndarray | None, det_score: float)
    """
    from app import face_app
    if face_app is None:
        return None, 0.0

    try:
        # CLAHE preprocessing
        lab = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2LAB)
        l, a, b = cv2.split(lab)
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        l = clahe.apply(l)
        lab = cv2.merge([l, a, b])
        img_enhanced = cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)

        faces = face_app.get(img_enhanced)
        if not faces:
            return None, 0.0

        best = max(faces, key=lambda f: f.det_score)
        if best.det_score < cfg.FACE_DET_SCORE_THRESHOLD:
            return None, best.det_score

        return best.embedding, best.det_score
    except Exception as e:
        log.debug('Face extraction failed: %s', e)
        return None, 0.0


def recognize_face(frame_bgr, frame_no=0):
    """Recognize a face crop against stored embeddings.

    Args:
        frame_bgr: BGR image crop containing a face.
        frame_no: current frame number (for auto-learn timing).

    Returns:
        (name: str | None, similarity: float)
    """
    embedding, det_score = extract_face_embedding(frame_bgr)
    if embedding is None:
        return None, 0.0

    best_name = None
    best_sim = 0.0

    with db_connection() as conn:
        rows = conn.execute(
            'SELECT p.name, e.embedding_blob FROM face_embeddings e '
            'JOIN persons p ON p.id = e.person_id'
        ).fetchall()

        for row in rows:
            stored = np.frombuffer(row['embedding_blob'], dtype=np.float32)
            sim = np.dot(embedding, stored) / (np.linalg.norm(embedding) * np.linalg.norm(stored) + 1e-6)
            if sim > best_sim:
                best_sim = sim
                best_name = row['name']

    if best_sim < cfg.FACE_SIMILARITY_THRESHOLD:
        return None, best_sim

    # Auto-learn: periodically store new embedding for matched person
    if frame_no > 0 and frame_no % cfg.AUTO_LEARN_INTERVAL == 0 and best_sim >= cfg.FACE_AUTO_LEARN_THRESHOLD:
        _auto_learn(best_name, embedding, det_score, frame_no)

    return best_name, best_sim


def _auto_learn(name, embedding, det_score, frame_no):
    """Store a new embedding for an already-recognized person."""
    try:
        with db_connection() as conn:
            row = conn.execute('SELECT id FROM persons WHERE name = ?', (name,)).fetchone()
            if row is None:
                return
            conn.execute(
                'INSERT INTO face_embeddings (person_id, embedding_blob, det_score) VALUES (?, ?, ?)',
                (row['id'], embedding.tobytes(), det_score)
            )
            conn.commit()
            log.info('Auto-learned embedding for %s (det=%.2f)', name, det_score)
    except Exception as e:
        log.debug('Auto-learn failed for %s: %s', name, e)
