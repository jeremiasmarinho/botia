from __future__ import annotations

from agent.vision_yolo import EmulatorWindow, VisionYolo


def test_calculate_game_area_removes_chrome() -> None:
    emu = EmulatorWindow(chrome_top=35, chrome_bottom=0, chrome_left=0, chrome_right=38)

    emu._win_left = 100
    emu._win_top = 50
    emu._win_width = 900
    emu._win_height = 1600
    emu._calculate_game_area()

    assert emu.offset_x == 100
    assert emu.offset_y == 85
    assert emu.canvas_width == 862
    assert emu.canvas_height == 1565


def test_to_screen_coords_applies_offsets() -> None:
    vision = VisionYolo()
    vision.offset_x = 320
    vision.offset_y = 140

    x_abs, y_abs = vision.to_screen_coords(45, 60)
    assert x_abs == 365
    assert y_abs == 200
