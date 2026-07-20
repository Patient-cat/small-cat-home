"""Unit tests for fall detection scoring."""
import math
import pytest
from core.fall_detection import _sigmoid, check_fall


class TestSigmoid:
    def test_zero(self):
        assert abs(_sigmoid(0) - 0.5) < 1e-6

    def test_large_positive(self):
        assert _sigmoid(100) > 0.99

    def test_large_negative(self):
        assert _sigmoid(-100) < 0.01

    def test_symmetry(self):
        assert abs(_sigmoid(5) + _sigmoid(-5) - 1.0) < 1e-6


class TestCheckFall:
    def _make_kp(self, upright=True):
        """Generate synthetic 17-keypoint array."""
        import numpy as np
        kp = np.zeros((17, 2), dtype=np.float32)
        conf = np.ones(17, dtype=np.float32) * 0.9

        if upright:
            # Standing person
            kp[5] = [200, 100]  # left shoulder
            kp[6] = [300, 100]  # right shoulder
            kp[11] = [210, 250]  # left hip
            kp[12] = [290, 250]  # right hip
            kp[13] = [215, 350]  # left knee
            kp[14] = [285, 350]  # right knee
            kp[15] = [218, 450]  # left ankle
            kp[16] = [282, 450]  # right ankle
            kp[0] = [250, 50]    # nose
        else:
            # Fallen person (horizontal)
            kp[5] = [100, 300]  # left shoulder
            kp[6] = [100, 200]  # right shoulder
            kp[11] = [300, 310]  # left hip
            kp[12] = [300, 190]  # right hip
            kp[13] = [400, 315]  # left knee
            kp[14] = [400, 185]  # right knee
            kp[15] = [480, 318]  # left ankle
            kp[16] = [480, 182]  # right ankle
            kp[0] = [50, 250]    # nose

        return kp, conf

    def test_upright_low_score(self):
        kp, conf = self._make_kp(upright=True)
        is_fall, info = check_fall(kp, conf, [], [])
        assert info is not None
        assert info['p_fall'] < 0.5

    def test_fallen_high_score(self):
        kp, conf = self._make_kp(upright=False)
        hip_hist = [(300, 250), (300, 260), (300, 270)]
        angle_hist = [80, 82, 85]
        is_fall, info = check_fall(kp, conf, hip_hist, angle_hist)
        assert info is not None
        assert info['p_fall'] > 0.3  # Should be elevated

    def test_missing_keypoints_returns_none(self):
        import numpy as np
        kp = np.zeros((17, 2), dtype=np.float32)
        conf = np.zeros(17, dtype=np.float32)  # All zero confidence
        is_fall, info = check_fall(kp, conf, [], [])
        assert info is None

    def test_velocity_contributes(self):
        kp, conf = self._make_kp(upright=True)
        # Moving downward fast
        hip_hist = [(250, 200), (250, 220), (250, 260)]
        is_fall, info = check_fall(kp, conf, hip_hist, [])
        assert info is not None
        assert info['p_vel'] > 0
