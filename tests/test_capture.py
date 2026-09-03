from PIL import Image, ImageDraw

from jptutor.capture import ChangeDetector, frame_difference


def blank():
    return Image.new("RGB", (400, 100), "black")


def with_text(offset=0):
    img = blank()
    ImageDraw.Draw(img).rectangle((20 + offset, 30, 220 + offset, 70), fill="white")
    return img


def test_frame_difference_zero_for_identical_and_high_for_new_text():
    assert frame_difference(blank(), blank()) == 0.0
    assert frame_difference(blank(), with_text()) > 0.1


def test_detector_waits_for_stability_then_fires_once():
    d = ChangeDetector(threshold=0.02, stability_frames=2)
    assert d.offer(blank()) is None          # first frame, not yet stable
    assert d.offer(blank()) is not None      # stable -> fire
    assert d.offer(blank()) is None          # unchanged -> nothing
    assert d.offer(with_text()) is None      # changed, wait a frame
    assert d.offer(with_text()) is not None  # stable new text -> fire
    assert d.offer(with_text()) is None


def test_detector_ignores_animation_until_it_settles():
    d = ChangeDetector(threshold=0.02, stability_frames=2)
    d.offer(blank()); d.offer(blank())
    assert d.offer(with_text(0)) is None
    assert d.offer(with_text(60)) is None    # still moving
    assert d.offer(with_text(60)) is not None
