"""Unit tests for ground hazard detection."""
import pytest
from core.ground_hazard import check_person_distance


class TestCheckPersonDistance:
    def _make_tracks(self, x, y, name='张大爷'):
        return {
            0: {'bbox': (x-20, y-40, x+20, y+40), 'name': name}
        }

    def test_person_close(self):
        tracks = self._make_tracks(100, 100)
        dist, name = check_person_distance((110, 110), tracks)
        assert dist < 50
        assert name == '张大爷'

    def test_person_far(self):
        tracks = self._make_tracks(100, 100)
        dist, name = check_person_distance((500, 500), tracks)
        assert dist > 200
        assert name == '张大爷'

    def test_empty_tracks(self):
        dist, name = check_person_distance((100, 100), {})
        assert dist == 9999
        assert name is None

    def test_unknown_person(self):
        tracks = {0: {'bbox': (80, 60, 120, 140)}}
        dist, name = check_person_distance((100, 100), tracks)
        assert dist < 50
        assert name == '陌生人'

    def test_multiple_tracks_returns_closest(self):
        tracks = {
            0: {'bbox': (80, 60, 120, 140), 'name': '张大爷'},
            1: {'bbox': (400, 300, 440, 380), 'name': '李奶奶'},
        }
        dist, name = check_person_distance((100, 100), tracks)
        assert name == '张大爷'
        assert dist < 50
