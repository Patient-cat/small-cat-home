"""Video buffer — ring buffer for pre/post-event clip recording."""
import os
import cv2
import time
import logging
import threading
from collections import deque
from datetime import datetime

import config as cfg

log = logging.getLogger('safesight')

CLIPS_DIR = os.path.join(cfg.BASE_DIR, 'static', 'clips')


class VideoBuffer:
    """Ring buffer that stores recent frames and can save clips on alert.

    Usage:
        buffer = VideoBuffer(max_seconds=30, fps=15)
        buffer.add_frame(frame)  # called every frame in streaming loop
        buffer.save_clip("output.mp4", before_sec=15, after_sec=10)
    """

    def __init__(self, max_seconds=30, fps=15):
        self.max_frames = max_seconds * fps
        self.fps = fps
        self.buffer = deque(maxlen=self.max_frames)
        self._lock = threading.Lock()
        self._saving = False

    def add_frame(self, frame):
        """Add a frame to the ring buffer. Call this every frame."""
        if self._saving:
            return  # Don't waste memory while saving
        with self._lock:
            self.buffer.append(frame.copy())

    def save_clip(self, output_path, before_sec=15, after_sec=0):
        """Save a clip from the buffer.

        Args:
            output_path: full path to output .mp4 file
            before_sec: seconds of footage before the alert
            after_sec: seconds to wait and record after the alert (0 = immediate save)

        Returns:
            True if clip was saved successfully.
        """
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        self._saving = True

        try:
            # Wait for after_sec frames to accumulate
            if after_sec > 0:
                time.sleep(after_sec)

            with self._lock:
                frames_to_save = list(self.buffer)

            # Take only the last `before_sec` seconds worth of frames
            frames_needed = before_sec * self.fps
            if len(frames_to_save) > frames_needed:
                frames_to_save = frames_to_save[-frames_needed:]

            if not frames_to_save:
                log.warning('No frames in buffer to save clip')
                return False

            # Write to MP4
            h, w = frames_to_save[0].shape[:2]
            fourcc = cv2.VideoWriter_fourcc(*'mp4v')
            writer = cv2.VideoWriter(output_path, fourcc, self.fps, (w, h))
            for f in frames_to_save:
                writer.write(f)
            writer.release()

            file_size = os.path.getsize(output_path) / 1024 / 1024
            log.info('Saved clip: %s (%d frames, %.1fMB)', output_path, len(frames_to_save), file_size)
            return True

        except Exception as e:
            log.error('Failed to save clip: %s', e)
            return False
        finally:
            self._saving = False

    def save_clip_async(self, output_path, before_sec=15, after_sec=10):
        """Save clip in a background thread (non-blocking)."""
        t = threading.Thread(
            target=self.save_clip,
            args=(output_path, before_sec, after_sec),
            daemon=True,
            name='clip-saver'
        )
        t.start()
        return t


def generate_clip_path(cam_id, event_type='hazard'):
    """Generate a unique clip file path."""
    os.makedirs(CLIPS_DIR, exist_ok=True)
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f'{event_type}_cam{cam_id}_{ts}.mp4'
    return os.path.join(CLIPS_DIR, filename)
