"""Unit tests for IoU tracker."""
import pytest
import numpy as np
from core.tracking import _iou, all_persons


class TestIoU:
    def test_identical_boxes(self):
        box = (10, 10, 50, 50)
        assert abs(_iou(box, box) - 1.0) < 1e-6

    def test_no_overlap(self):
        box_a = (0, 0, 10, 10)
        box_b = (20, 20, 30, 30)
        assert _iou(box_a, box_b) == 0.0

    def test_partial_overlap(self):
        box_a = (0, 0, 20, 20)
        box_b = (10, 10, 30, 30)
        iou = _iou(box_a, box_b)
        # Intersection: 10x10=100, Union: 400+400-100=700
        assert abs(iou - 100 / 700) < 1e-4

    def test_containment(self):
        box_outer = (0, 0, 100, 100)
        box_inner = (25, 25, 75, 75)
        iou = _iou(box_outer, box_inner)
        # Inner area: 2500, Outer area: 10000, Union: 10000
        assert abs(iou - 0.25) < 1e-4


class TestAllPersons:
    def test_empty_result(self):
        class MockResult:
            keypoints = None
            boxes = None
        assert all_persons(MockResult()) == []

    def test_person_extraction(self):
        class MockTensor:
            def __init__(self, data):
                self._data = np.array(data)
            def cpu(self):
                return self
            def numpy(self):
                return self._data
            def __getitem__(self, idx):
                return self._data[idx]
            def __len__(self):
                return len(self._data)
        class MockKP:
            xy = MockTensor([[[100, 200], [110, 210]]])
            conf = MockTensor([[0.9, 0.8]])
        class MockBoxes:
            xyxy = MockTensor([[50, 100, 200, 400]])
            cls = MockTensor([0])
        class MockResult:
            keypoints = MockKP()
            boxes = MockBoxes()

        persons = all_persons(MockResult())
        assert len(persons) == 1
        assert persons[0]['bbox'] == (50, 100, 200, 400)

    def test_non_person_filtered(self):
        class MockTensor:
            def __init__(self, data):
                self._data = np.array(data)
            def cpu(self):
                return self
            def numpy(self):
                return self._data
            def __getitem__(self, idx):
                return self._data[idx]
            def __len__(self):
                return len(self._data)
        class MockKP:
            xy = MockTensor([[[100, 200]]])
            conf = MockTensor([[0.9]])
        class MockBoxes:
            xyxy = MockTensor([[50, 100, 200, 400]])
            cls = MockTensor([2])
        class MockResult:
            keypoints = MockKP()
            boxes = MockBoxes()

        assert all_persons(MockResult()) == []
