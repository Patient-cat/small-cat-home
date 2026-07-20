"""Unit tests for ground hazard detection."""
import pytest
from core.ground_hazard import check_person_nearby, is_in_roi


class TestCheckPersonNearby:
    def _make_tracks(self, x, y, name='张大爷'):
        return {
            0: {'bbox': (x-20, y-40, x+20, y+40), 'name': name}
        }

    def test_person_nearby(self):
        tracks = self._make_tracks(100, 100)
        is_near, name = check_person_nearby((110, 110), tracks, threshold=50)
        assert is_near is True
        assert name == '张大爷'

    def test_person_far(self):
        tracks = self._make_tracks(100, 100)
        is_near, name = check_person_nearby((500, 500), tracks, threshold=50)
        assert is_near is False

    def test_empty_tracks(self):
        is_near, name = check_person_nearby((100, 100), {}, threshold=200)
        assert is_near is False

    def test_unknown_person(self):
        tracks = {0: {'bbox': (80, 60, 120, 140)}}
        is_near, name = check_person_nearby((100, 100), tracks, threshold=50)
        assert is_near is True
        assert name == '陌生人'


class TestIsInRoi:
    def test_no_roi_returns_true(self):
        assert is_in_roi((10, 10, 50, 50), []) is True

    def test_point_inside(self):
        roi = [[0.1, 0.1], [0.9, 0.1], [0.9, 0.9], [0.1, 0.9]]
        # Object at pixel (200, 200) in 400x400 frame → normalized (0.5, 0.5)
        assert is_in_roi((180, 180, 220, 220), roi, frame_w=400, frame_h=400) is True

    def test_point_outside(self):
        roi = [[0.3, 0.3], [0.7, 0.3], [0.7, 0.7], [0.3, 0.7]]
        # Object at pixel (10, 10) in 100x100 frame → normalized (0.1, 0.1)
        assert is_in_roi((5, 5, 15, 15), roi, frame_w=100, frame_h=100) is False

    def test_point_on_edge(self):
        roi = [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]]
        # Full frame ROI — everything inside
        assert is_in_roi((50, 50, 100, 100), roi, frame_w=200, frame_h=200) is True
