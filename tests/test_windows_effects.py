import pytest

from locallens.ui.windows_effects import _rgba_to_abgr, apply_rounded_window_region


def test_acrylic_tint_is_encoded_in_windows_abgr_order():
    assert _rgba_to_abgr(0x11, 0x22, 0x33, 0x44) == 0x44332211


@pytest.mark.parametrize("channel", [-1, 256, True])
def test_acrylic_tint_rejects_invalid_channels(channel):
    with pytest.raises(ValueError):
        _rgba_to_abgr(channel, 0, 0, 255)


def test_rounded_window_region_rejects_invalid_native_geometry():
    assert apply_rounded_window_region(0, 100, 100, 20) is False
    assert apply_rounded_window_region(1, 0, 100, 20) is False
    assert apply_rounded_window_region(1, 100, 100, 0) is False
