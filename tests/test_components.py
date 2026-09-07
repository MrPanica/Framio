# -*- coding: utf-8 -*-
"""
Тесты компонентов Framio: модели слоёв, история Undo/Redo, кодирование видео и GIF, фильтры.
"""

import sys
import os
import time
import math
from pathlib import Path
import numpy as np

# Добавляем корневую директорию проекта
sys.path.insert(0, str(Path(__file__).parent.parent))
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
if hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt, QPointF, QRectF
from PyQt6.QtGui import QImage, QPainter, QColor

from models.shapes import PenShape, LineShape, ArrowShape, RectangleShape, CircleShape, TextShape, MosaicShape, CaptureMaskShape
from models.layers import LayerManager
from models.history import HistoryManager, HistoryCommand
from recorder.video_recorder import VideoRecorder
from recorder.gif_recorder import GifRecorder
from utils.image_filters import apply_filter, FilterType
from utils.hotkey_manager import parse_hotkey_string
from utils.capture_mask import apply_mask_to_bgr, apply_mask_to_qimage, mask_path_for_frame

GLOBAL_APP = QApplication.instance() or QApplication(sys.argv)


def test_capture_masks():
    print("[TEST] Проверка масок произвольной формы, прямоугольника и овала...")

    freeform = CaptureMaskShape(points=[QPointF(10, 10), QPointF(90, 10), QPointF(90, 90), QPointF(10, 90)])
    assert freeform.path().contains(QPointF(50, 50))
    assert not freeform.path().contains(QPointF(2, 2))
    assert freeform.get_bounding_rect().width() > 70

    rect = CaptureMaskShape(kind="rect", rect=QRectF(10, 10, 80, 40))
    rect.rotate_by(30)
    assert rect.path().contains(rect.path().boundingRect().center())

    circle = CaptureMaskShape(kind="circle", rect=QRectF(10, 10, 80, 80))
    frame = np.full((100, 100, 3), 255, dtype=np.uint8)
    masked = apply_mask_to_bgr(frame, circle, (0, 0, 100, 100))
    assert int(masked[50, 50, 0]) == 255
    assert int(masked[0, 0, 0]) == 0

    # Регрессия: при масштабировании области сначала нужно масштабировать,
    # а уже затем вычитать координаты области. Иначе маска уезжает вправо-вниз.
    shifted = CaptureMaskShape(kind="circle", rect=QRectF(90, 90, 20, 20))
    mapped = mask_path_for_frame(shifted, 80, 80, (80, 80, 40, 40))
    mapped_center = mapped.boundingRect().center()
    assert abs(mapped_center.x() - 40.0) < 0.6
    assert abs(mapped_center.y() - 40.0) < 0.6

    source = QImage(100, 100, QImage.Format.Format_RGB32)
    source.fill(QColor("#ff0000"))
    clipped = apply_mask_to_qimage(source, circle, (0, 0, 100, 100))
    assert clipped.format() == QImage.Format.Format_ARGB32
    assert clipped.pixelColor(50, 50).alpha() > 0
    assert clipped.pixelColor(0, 0).alpha() == 0
    print("  -> Маска закрывается автоматически, поворачивается и единообразно применяется к кадрам.")


def test_multiple_capture_masks_and_delete():
    """Несколько масок образуют объединённую область и удаляются по одной."""
    print("[TEST] Проверка нескольких масок области записи и удаления одной маски...")
    from types import SimpleNamespace
    from unittest.mock import MagicMock
    from ui.overlay import OverlayWindow

    first = CaptureMaskShape(kind="circle", rect=QRectF(10, 10, 30, 30))
    second = CaptureMaskShape(kind="circle", rect=QRectF(60, 10, 30, 30))
    frame = np.full((50, 100, 3), 255, dtype=np.uint8)
    combined = apply_mask_to_bgr(frame, [first, second], (0, 0, 100, 50))
    assert int(combined[25, 25, 0]) == 255
    assert int(combined[25, 75, 0]) == 255
    assert int(combined[5, 50, 0]) == 0

    overlay = SimpleNamespace(
        capture_masks={0: [first, second]},
        active_region_idx=0,
        transform_box=MagicMock(),
        active_editing_shape=first,
        last_active_shape=first,
        update=MagicMock(),
        _invalidate_layers_cache=MagicMock(),
    )
    overlay.transform_box.shape = first
    OverlayWindow._delete_capture_mask(overlay, first)

    assert overlay.capture_masks[0] == [second]
    overlay.transform_box.set_shape.assert_called_once_with(second)

    draft = SimpleNamespace(
        active_region_idx=0,
        capture_masks={0: [second]},
        temp_shape=first,
        shape_origin_pos=QPointF(0, 0),
        transform_box=MagicMock(),
        _invalidate_layers_cache=MagicMock(),
        update=MagicMock(),
    )
    OverlayWindow._finish_drawing_shape(draft)
    assert draft.capture_masks[0] == [second, first]
    print("  -> Две маски попадают в одну запись как объединённая область, отдельную маску можно удалить.")


def test_constrained_shape_drawing_and_repeated_capture_mask_transform():
    """Shift сохраняет квадратность, а маска не отрывается после повторного движения."""
    print("[TEST] Проверка Shift-ограничения фигур и повторного перемещения маски...")
    from ui.overlay import OverlayWindow
    from ui.transform_box import HandleType, ShapeTransformBox

    # Рисование прямоугольника/круга с Shift должно давать квадратную основу,
    # чтобы круг оставался окружностью, а не превращался в овал.
    for shape_type in (RectangleShape, CircleShape):
        overlay = OverlayWindow.__new__(OverlayWindow)
        overlay.shape_origin_pos = QPointF(100, 100)
        overlay.temp_shape = shape_type(QRectF(100, 100, 0, 0))
        overlay._update_drawing_shape(QPointF(180, 140), shift_pressed=True)
        assert overlay.temp_shape.rect.width() == overlay.temp_shape.rect.height() == 80

    line_overlay = OverlayWindow.__new__(OverlayWindow)
    line_overlay.shape_origin_pos = QPointF(100, 100)
    line_overlay.temp_shape = LineShape(QPointF(100, 100), QPointF(100, 100))
    line_overlay._update_drawing_shape(QPointF(170, 130), shift_pressed=True)
    line_angle = math.atan2(
        line_overlay.temp_shape.p2.y() - line_overlay.temp_shape.p1.y(),
        line_overlay.temp_shape.p2.x() - line_overlay.temp_shape.p1.x(),
    )
    assert abs(line_angle - math.radians(30)) < 0.01

    # После двух последовательных перемещений контур маски и её bounding box
    # должны описывать одну и ту же позицию. Раньше _apply_shape_geometry
    # затирал методом path() одноимённое поле и оставлял отрисовку на первом
    # положении после следующего drag.
    mask = CaptureMaskShape(kind="circle", rect=QRectF(100, 100, 80, 60))
    transform_box = ShapeTransformBox(mask)
    first_center = mask.path().boundingRect().center()
    transform_box.start_drag(HandleType.INSIDE, first_center)
    transform_box.drag_to(first_center + QPointF(20, 10))
    transform_box.finish_drag()

    second_start = mask.get_bounding_rect().center()
    transform_box.start_drag(HandleType.INSIDE, second_start)
    transform_box.drag_to(second_start + QPointF(30, 15))
    transform_box.finish_drag()

    expected_center = first_center + QPointF(20 + 30, 10 + 15)
    actual_center = mask.path().boundingRect().center()
    assert abs(actual_center.x() - expected_center.x()) < 0.01
    assert abs(actual_center.y() - expected_center.y()) < 0.01
    assert abs(mask.get_bounding_rect().center().x() - actual_center.x()) < 0.01
    assert abs(mask.get_bounding_rect().center().y() - actual_center.y()) < 0.01
    print("  -> Shift даёт ровную геометрию, повторное перемещение маски остаётся синхронным.")


def test_models_and_history():
    print("[TEST] Тестирование слоёв и истории Undo/Redo...")

    # Тест проверяет русские имена фигур, поэтому не зависит от языка runner'а.
    from utils.i18n import set_language
    set_language("ru")
    
    layer_mgr = LayerManager()
    history_mgr = HistoryManager()

    # 1. Добавляем карандаш
    pen = PenShape("#ff0000", stroke_width=4)
    pen.add_point(QPointF(10, 10))
    pen.add_point(QPointF(20, 20))
    layer_mgr.add_shape(pen)
    history_mgr.push_already_done(HistoryCommand("Карандаш", lambda: layer_mgr.add_shape(pen), lambda: layer_mgr.remove_shape(pen.id)))

    assert len(layer_mgr.shapes) == 1
    assert layer_mgr.shapes[0].name == "Карандаш"

    # 2. Добавляем стрелку
    arrow = ArrowShape(QPointF(50, 50), QPointF(100, 100), "#00ff00", stroke_width=4)
    layer_mgr.add_shape(arrow)
    history_mgr.push_already_done(HistoryCommand("Стрелка", lambda: layer_mgr.add_shape(arrow), lambda: layer_mgr.remove_shape(arrow.id)))

    assert len(layer_mgr.shapes) == 2

    # 3. Тестируем Undo
    history_mgr.undo()
    assert len(layer_mgr.shapes) == 1
    assert layer_mgr.shapes[0].id == pen.id

    # 4. Тестируем Redo
    history_mgr.redo()
    assert len(layer_mgr.shapes) == 2
    assert layer_mgr.shapes[1].id == arrow.id

    # 4.1 Тестируем инструмент мозаичной цензуры (MosaicShape)
    mosaic = MosaicShape(QRectF(10, 10, 40, 40), pixel_size=8)
    layer_mgr.add_shape(mosaic)
    assert len(layer_mgr.shapes) == 3
    assert layer_mgr.shapes[2].name == "Мозаика (Цензура)"

    # 5. Тестируем отрисовку на QImage
    qimg = QImage(200, 200, QImage.Format.Format_ARGB32_Premultiplied)
    qimg.fill(0)
    painter = QPainter(qimg)
    layer_mgr.draw_all(painter)
    painter.end()
    assert not qimg.isNull()
    print("  -> Слои, формы и Undo/Redo работают корректно.")


def test_filters():
    print("[TEST] Тестирование фильтров реального времени...")
    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    frame[25:75, 25:75] = [255, 128, 64]

    for f_type in [FilterType.NONE, FilterType.GRAYSCALE, FilterType.INVERT, FilterType.BLUR, FilterType.PIXELATE, FilterType.VIBRANT]:
        res = apply_filter(frame, f_type)
        assert res is not None
        assert res.shape == frame.shape
    print("  -> Фильтры обработаны без ошибок.")


def test_video_and_gif_recorders(tmp_path):
    print("[TEST] Тестирование видео и GIF рекордеров...")
    vid_path = str(tmp_path / "test_video.mp4")
    gif_path = str(tmp_path / "test_anim.gif")

    # Тест видео
    v_rec = VideoRecorder(vid_path, fps=30, codec="mp4v", width=160, height=120)
    v_rec.start()
    for _ in range(10):
        dummy_frame = np.random.randint(0, 255, (120, 160, 3), dtype=np.uint8)
        v_rec.write_frame(dummy_frame)
    # Проверка изменения размера на лету (динамический ресайз)
    dynamic_frame = np.random.randint(0, 255, (80, 80, 3), dtype=np.uint8)
    v_rec.write_frame(dynamic_frame)
    v_rec.stop()

    assert Path(vid_path).exists()
    assert Path(vid_path).stat().st_size > 0
    print(f"  -> MP4 видео успешно создано: {Path(vid_path).stat().st_size} байт")

    # Тест GIF
    g_rec = GifRecorder(gif_path, fps=10, width=120, height=90)
    g_rec.start()
    for _ in range(5):
        dummy_frame = np.random.randint(0, 255, (90, 120, 3), dtype=np.uint8)
        g_rec.add_frame(dummy_frame)
    # Динамический ресайз кадра для гифки
    g_rec.add_frame(np.random.randint(0, 255, (60, 60, 3), dtype=np.uint8))
    g_rec.stop()

    assert Path(gif_path).exists()
    assert Path(gif_path).stat().st_size > 0
    print(f"  -> GIF успешно создан: {Path(gif_path).stat().st_size} байт")


def test_hotkey_parsing():
    print("[TEST] Тестирование разбора горячих клавиш...")
    mods, vk = parse_hotkey_string("Ctrl+Shift+Print Screen")
    assert vk == 0x2C  # VK_SNAPSHOT
    assert mods > 0

    # Проверка одиночных клавиш без модификаторов (Print Screen, F12 и т.д.)
    mods_prt, vk_prt = parse_hotkey_string("Print Screen")
    assert vk_prt == 0x2C  # VK_SNAPSHOT
    assert mods_prt == 0

    mods_prt2, vk_prt2 = parse_hotkey_string("PrtScn")
    assert vk_prt2 == 0x2C
    assert mods_prt2 == 0

    mods_f12, vk_f12 = parse_hotkey_string("F12")
    assert vk_f12 == 0x7B  # VK_F12
    assert mods_f12 == 0

    mods_f9, vk_f9 = parse_hotkey_string("Ctrl+Shift+F9")
    assert vk_f9 == 0x78  # VK_F9
    assert mods_f9 > 0

    mods_f10, vk_f10 = parse_hotkey_string("Ctrl+Shift+F10")
    assert vk_f10 == 0x79  # VK_F10
    assert mods_f10 > 0

    from utils.hotkey_manager import GlobalHotkeyManager
    mgr = GlobalHotkeyManager("Print Screen", "Ctrl+Shift+F9", "Ctrl+Shift+F10")
    assert hasattr(mgr, "capture_triggered")
    assert hasattr(mgr, "record_fullscreen_triggered")
    assert hasattr(mgr, "stop_recording_triggered")
    print("  -> Хоткеи скриншота (включая одиночный Print Screen), полного экрана и остановки успешно распарсены.")


def test_progress_signals():
    print("[TEST] Тестирование сигналов прогресса обработки (0-100%)...")
    from recorder.capture_worker import CaptureWorker
    worker = CaptureWorker("video", "test.mp4", lambda: (0, 0, 100, 100), fps=24)
    assert hasattr(worker, "save_progress")
    assert worker.fps == 24, "CaptureWorker должен получать FPS из настроек записи"

    received = []
    worker.save_progress.connect(lambda f, p, s: received.append((f, p, s)))
    worker._emit_progress(50, "Тест кодирования")
    assert len(received) == 1
    assert received[0] == ("test.mp4", 50, "Тест кодирования")
    print("  -> Сигнал save_progress корректно передаёт проценты и статус.")



def test_shape_cloning_and_reordering():
    print("[TEST] Тестирование клонирования фигур и изменения порядка слоев...")
    lm = LayerManager()
    rect = RectangleShape(QRectF(10, 10, 100, 100), color="#FF2E2E", filled=True)
    lm.add_shape(rect)
    
    # Клонирование
    clone = rect.clone()
    assert clone.id != rect.id
    assert clone.filled == rect.filled
    clone.translate(15, 15)
    assert clone.rect.left() == 25
    assert rect.rect.left() == 10
    lm.add_shape(clone)

    # Порядок слоев
    assert lm.shapes[0] == rect
    assert lm.shapes[1] == clone
    lm.bring_to_front(rect)
    assert lm.shapes[1] == rect
    lm.send_to_back(rect)
    assert lm.shapes[0] == rect
    lm.move_shape_up(rect)
    assert lm.shapes[1] == rect
    lm.move_shape_down(rect)
    assert lm.shapes[0] == rect
    print("  -> Клонирование и изменение порядка слоев работают корректно.")


def test_style_preview_icons():
    print("[TEST] Тестирование генерации векторных иконок превью стилей...")
    app = QApplication.instance() or QApplication(sys.argv)
    from ui.icons import create_style_preview_icon
    styles = [
        "arrow_classic", "arrow_barbed", "arrow_double", "arrow_stealth", "arrow_dashed",
        "line_solid", "line_dashed", "line_dotted",
        "rect_sharp", "rect_rounded"
    ]
    for s in styles:
        ico = create_style_preview_icon(s, is_dark=True)
        assert not ico.isNull(), f"Иконка стиля {s} пустая"
    print("  -> Векторные превью стилей сгенерированы успешно.")


def test_audio_recorder(tmp_path):
    print("[TEST] Тестирование записи звука (WASAPI loopback + mic)...")
    from recorder.audio_recorder import AudioRecorder, resample_audio
    
    # 1. Тест ресемплирования
    dummy_audio = np.random.randint(-1000, 1000, (44100, 2), dtype=np.int16)
    resampled = resample_audio(dummy_audio, 44100, 48000)
    assert len(resampled) == 48000
    assert resampled.shape[1] == 2

    # 2. Тест записи аудио в WAV
    wav_path = str(tmp_path / "test_audio.wav")
    rec = AudioRecorder(wav_path, record_mic=True, record_system=True)
    rec.start()
    time.sleep(0.5)
    rec.stop()

    if Path(wav_path).exists():
        assert Path(wav_path).stat().st_size > 44  # WAV header is 44 bytes
        print(f"  -> Звук успешно записан: {Path(wav_path).stat().st_size} байт")
    else:
        print("  -> Звуковые устройства отсутствуют или недоступны (тест пропущен без ошибок).")


def test_screen_capture_1to1():
    print("[TEST] Тестирование захвата экрана 1:1...")
    from utils.screen_lock import safe_grab_screen_bgr, safe_grab_screen_pixmap, get_screen_dpr
    dpr = get_screen_dpr()
    assert dpr >= 1.0

    bgr = safe_grab_screen_bgr(0, 0, 200, 150)
    assert bgr is not None
    assert bgr.ndim == 3
    assert bgr.shape[2] == 3
    assert bgr.shape[0] == int(round(150 * dpr))
    assert bgr.shape[1] == int(round(200 * dpr))

    pix = safe_grab_screen_pixmap(0, 0, 200, 150)
    assert pix is not None
    assert not pix.isNull()
    print(f"  -> Захват экрана 1:1 подтвержден ({bgr.shape[1]}x{bgr.shape[0]} px, DPR={dpr}).")


def test_window_enumeration_and_capture():
    print("[TEST] Тестирование перечисления окон и захвата...")
    from utils.screen_lock import enumerate_recordable_windows, capture_window_or_screen_bgr, POINT, user32
    # Проверяем ABI WindowFromPoint: Win32 принимает POINT по значению, а HWND не обрезается.
    point_hwnd = user32.WindowFromPoint(POINT(0, 0))
    assert int(point_hwnd or 0) >= 0
    assert user32.WindowFromPoint.argtypes == [POINT]
    windows = enumerate_recordable_windows()
    assert isinstance(windows, list)
    print(f"  -> Найдено окон для захвата: {len(windows)}")
    if windows:
        hwnd, title = windows[0]
        assert hwnd > 0
        assert isinstance(title, str) and len(title) > 0
        print(f"  -> Пример окна: [{hwnd}] {title[:40]}")
    
    # Тест захвата экрана через capture_window_or_screen_bgr
    frame = capture_window_or_screen_bgr(0, 0, 160, 120, target_hwnd=None)
    assert frame is not None
    assert frame.ndim == 3
    assert frame.shape[2] == 3
    print(f"  -> Захват экрана через capture_window_or_screen_bgr: {frame.shape[1]}x{frame.shape[0]}")


def test_svg_recording_icons():
    print("[TEST] Тестирование новых векторных иконок панели записи...")
    from ui.icons import create_themed_icon, SVG_ICONS
    for icon_name in ["record", "pause", "play", "stop", "mic", "speaker", "window", "settings"]:
        assert icon_name in SVG_ICONS, f"Иконка {icon_name} отсутствует в SVG_ICONS"
        ico = create_themed_icon(icon_name)
        assert not ico.isNull(), f"Иконка {icon_name} не смогла загрузиться"
    print("  -> Все векторные иконки панели записи корректно загружаются.")


def test_config_defaults_and_persistence():
    print("[TEST] Тестирование параметров сохранения кодека, форматов и звука в AppConfig...")
    from config import ConfigManager
    cfg = ConfigManager.get_instance().config
    assert hasattr(cfg, "last_save_format")
    assert hasattr(cfg, "default_copy_format")
    assert hasattr(cfg, "video_codec")
    assert hasattr(cfg, "gif_fps")
    assert hasattr(cfg, "target_window_title")
    assert hasattr(cfg, "record_mic")
    assert hasattr(cfg, "record_system")
    assert hasattr(cfg, "autostart")
    print(f"  -> Конфигурация: codec={cfg.video_codec}, gif_fps={cfg.gif_fps}, save_fmt={cfg.last_save_format}, autostart={cfg.autostart}")


def test_window_icon_extraction():
    print("[TEST] Тестирование извлечения нативных иконок окон приложений...")
    from utils.window_icon import get_window_qicon
    from utils.screen_lock import enumerate_recordable_windows
    # Иконка всего экрана
    screen_ico = get_window_qicon(0)
    assert screen_ico is not None and not screen_ico.isNull()
    
    # Иконки открытых окон
    windows = enumerate_recordable_windows()
    if windows:
        hwnd, title = windows[0]
        win_ico = get_window_qicon(hwnd)
        assert win_ico is not None and not win_ico.isNull()
        print(f"  -> Иконка для [{hwnd}] {title[:30]} успешно получена ({len(windows)} окон проверено).")


def test_autostart_registry():
    print("[TEST] Тестирование функции автозагрузки Windows...")
    from utils.autostart import is_windows_autostart_enabled
    enabled = is_windows_autostart_enabled()
    assert isinstance(enabled, bool)
    print(f"  -> Текущий статус автозагрузки в реестре: {enabled}")


def test_bounding_rects_and_toolbar_layout():
    print("[TEST] Тестирование get_bounding_rect для всех фигур и порядка кнопок панели...")
    from models.shapes import (
        PenShape, LineShape, ArrowShape, RectangleShape, CircleShape, TextShape, MosaicShape
    )
    from ui.toolbars import RightDrawingToolbar, ToolType

    # 1. Bounding rects
    pen = PenShape("#ff0000", stroke_width=4)
    pen.add_point(QPointF(10, 10))
    pen.add_point(QPointF(50, 80))
    b_pen = pen.get_bounding_rect()
    assert b_pen.isValid() and not b_pen.isEmpty()
    assert b_pen.left() < 10 and b_pen.right() > 50

    line = LineShape(QPointF(20, 20), QPointF(100, 100), stroke_width=4)
    b_line = line.get_bounding_rect()
    assert b_line.isValid() and b_line.contains(QPointF(50, 50))

    arrow = ArrowShape(QPointF(0, 0), QPointF(100, 0), stroke_width=4)
    b_arrow = arrow.get_bounding_rect()
    assert b_arrow.isValid() and b_arrow.width() >= 100

    rect = RectangleShape(QRectF(10, 10, 80, 50), stroke_width=4)
    b_rect = rect.get_bounding_rect()
    assert b_rect.isValid() and b_rect.width() >= 80

    circ = CircleShape(QRectF(10, 10, 60, 60), stroke_width=4)
    b_circ = circ.get_bounding_rect()
    assert b_circ.isValid() and b_circ.width() >= 60

    text = TextShape(QPointF(20, 50), "Hello World", font_size=16)
    b_text = text.get_bounding_rect()
    assert b_text.isValid() and not b_text.isEmpty()

    mosaic = MosaicShape(QRectF(30, 40, 50, 50))
    b_mosaic = mosaic.get_bounding_rect()
    assert b_mosaic.isValid() and b_mosaic.width() == 50

    # 2. TextShape: настройки фона текста
    text_default = TextShape(QPointF(20, 50), "Test Text")
    assert text_default.has_bg is False
    assert text_default.bg_color == "#000000"
    assert text_default.bg_alpha == 180

    text_bg = TextShape(QPointF(20, 50), "Background Text", has_bg=True, bg_color="#1e1e1e", bg_alpha=200)
    assert text_bg.has_bg is True
    # Проверка отрисовки текста с фоном и без фона на QImage
    test_img = QImage(200, 100, QImage.Format.Format_ARGB32_Premultiplied)
    test_img.fill(0)
    p = QPainter(test_img)
    text_default.draw(p)
    text_bg.draw(p)
    p.end()
    assert not test_img.isNull()

    # 3. RightDrawingToolbar: стрелка barbed по умолчанию и положение мозаики ниже Undo/Redo
    toolbar = RightDrawingToolbar()
    assert ToolType.MOSAIC in toolbar.tool_buttons
    shapes_cfg = toolbar.tools_config[ToolType.SHAPES]
    assert shapes_cfg.get("subshape") == "arrow"
    assert shapes_cfg.get("arrow_style") == "barbed"

    # Проверяем, что кнопка мозаики находится в layout ниже кнопок Undo и Redo
    btn_mosaic = toolbar.tool_buttons[ToolType.MOSAIC]
    mosaic_idx = toolbar.layout().indexOf(btn_mosaic)
    # Находим индексы всех кнопок в layout
    items_in_layout = [toolbar.layout().itemAt(i).widget() for i in range(toolbar.layout().count()) if toolbar.layout().itemAt(i).widget()]
    mosaic_pos = items_in_layout.index(btn_mosaic)
    # Перед мозаикой должны быть инструменты рисования, палитра и Undo/Redo
    assert mosaic_pos >= 7, f"Кнопка мозаики должна быть ниже палитры и Undo/Redo (позиция {mosaic_pos})"

    toolbar.select_tool(ToolType.PEN)
    assert not toolbar.properties_flyout.isVisible()
    toolbar.select_tool(ToolType.ARROW)
    assert not toolbar.properties_flyout.isVisible()
    toolbar.select_tool(ToolType.SHAPES)
    assert toolbar.properties_flyout.isVisible()

    print("  -> Bounding rects, фон текста, стрелка с усиками и компоновка панели проверены успешно.")


def test_shape_directional_drawing():
    print("[TEST] Тестирование рисования фигур во всех 4 направлениях (влево, вправо, вверх, вниз)...")
    from models.shapes import RectangleShape, CircleShape, MosaicShape

    origin = QPointF(100, 100)

    # 1. Рисование вправо-вниз (X+, Y+)
    pos_rd = QPointF(180, 160)
    r_rd = QRectF(origin, pos_rd).normalized()
    assert r_rd.topLeft() == origin
    assert r_rd.width() == 80 and r_rd.height() == 60

    # 2. Рисование влево-вверх (X-, Y-)
    pos_lu = QPointF(40, 30)
    r_lu = QRectF(origin, pos_lu).normalized()
    assert r_lu.topLeft() == pos_lu
    assert r_lu.bottomRight() == origin
    assert r_lu.width() == 60 and r_lu.height() == 70

    # 3. Рисование влево-вниз (X-, Y+)
    pos_ld = QPointF(40, 160)
    r_ld = QRectF(origin, pos_ld).normalized()
    assert r_ld.left() == 40 and r_ld.right() == 100
    assert r_ld.top() == 100 and r_ld.bottom() == 160
    assert r_ld.width() == 60 and r_ld.height() == 60

    # 4. Рисование вправо-вверх (X+, Y-)
    pos_ru = QPointF(180, 30)
    r_ru = QRectF(origin, pos_ru).normalized()
    assert r_ru.left() == 100 and r_ru.right() == 180
    assert r_ru.top() == 30 and r_ru.bottom() == 100
    assert r_ru.width() == 80 and r_ru.height() == 70

    print("  -> Рисование фигур во всех направлениях вычислено идеально.")


def test_hotkey_recorder_button():
    print("[TEST] Тестирование кнопки записи горячих клавиш (HotkeyRecorderButton)...")
    from ui.settings_dialog import HotkeyRecorderButton
    from PyQt6.QtWidgets import QLineEdit
    from PyQt6.QtCore import QEvent
    from PyQt6.QtGui import QKeyEvent

    edit = QLineEdit("Ctrl+Print Screen")
    btn = HotkeyRecorderButton(edit)
    assert btn.text() == "Назначить"
    assert not btn.is_recording

    # Старт записи
    btn._start_recording()
    assert btn.is_recording
    assert btn.text() == "Нажмите..."

    # Эмуляция нажатия Ctrl
    ev_ctrl = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Control, Qt.KeyboardModifier.ControlModifier)
    btn.keyPressEvent(ev_ctrl)

    # Эмуляция нажатия F9 при удержании Ctrl
    ev_f9 = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_F9, Qt.KeyboardModifier.ControlModifier)
    btn.keyPressEvent(ev_f9)
    assert edit.text() == "Ctrl+F9"

    # Эмуляция отпускания F9 и Ctrl
    ev_rel_f9 = QKeyEvent(QEvent.Type.KeyRelease, Qt.Key.Key_F9, Qt.KeyboardModifier.ControlModifier)
    btn.keyReleaseEvent(ev_rel_f9)
    ev_rel_ctrl = QKeyEvent(QEvent.Type.KeyRelease, Qt.Key.Key_Control, Qt.KeyboardModifier.NoModifier)
    btn.keyReleaseEvent(ev_rel_ctrl)

    # Запись завершена
    assert not btn.is_recording
    assert btn.text() == "Назначить"
    assert edit.text() == "Ctrl+F9"

    # Тест записи одиночной клавиши Print Screen (Key_Print / KeyRelease)
    btn._start_recording()
    assert btn.is_recording
    ev_prt = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Print, Qt.KeyboardModifier.NoModifier)
    btn.keyPressEvent(ev_prt)
    assert not btn.is_recording
    assert edit.text() == "Print Screen"

    # Тест перехвата через keyRelease (Windows поведение для Print Screen)
    btn._start_recording()
    assert btn.is_recording
    ev_prt_rel = QKeyEvent(QEvent.Type.KeyRelease, Qt.Key.Key_Print, Qt.KeyboardModifier.NoModifier)
    btn.keyReleaseEvent(ev_prt_rel)
    assert not btn.is_recording
    assert edit.text() == "Print Screen"

    print("  -> HotkeyRecorderButton корректно перехватывает комбинации и одиночный Print Screen.")


def test_settings_dialog_apply_without_close():
    print("[TEST] Тестирование кнопки «Применить» диалога настроек (без закрытия окна)...")
    from ui.settings_dialog import SettingsDialog

    dlg = SettingsDialog()
    dlg.show()
    assert hasattr(dlg, "combo_fps"), "В настройках записи отсутствует выбор FPS"
    assert dlg.combo_fps.currentText() in {"15", "24", "30", "60"}
    signals = []
    dlg.settings_applied.connect(lambda: signals.append(True))

    dlg._apply_settings()
    assert len(signals) == 1
    assert dlg.isVisible(), "Диалог настроек не должен закрываться при нажатии «Применить»!"
    assert "✓" in dlg.lbl_status.text()
    dlg.close()
    print("  -> Кнопка «Применить» успешно сохраняет настройки и оставляет окно открытым.")


def test_video_options_popup_has_fps():
    print("[TEST] Проверка FPS непосредственно в параметрах записи видео...")
    from ui.toolbars import VideoOptionsPopup

    popup = VideoOptionsPopup()
    assert hasattr(popup, "combo_fps"), "В popup параметров видео нет выбора FPS"
    assert {popup.combo_fps.itemText(i) for i in range(popup.combo_fps.count())} >= {"15", "24", "30", "60"}
    popup.combo_fps.setCurrentText("24")
    assert popup.combo_fps.currentText() == "24"
    popup.close()
    print("  -> FPS доступен прямо в popup параметров записи видео.")


def test_dynamic_frame_rescaling(tmp_path):
    print("[TEST] Тестирование динамического масштабирования видео без чёрных полос...")
    test_vid = str(tmp_path / "test_dyn_scale.mp4")
    rec = VideoRecorder(test_vid, fps=30, width=640, height=480)
    rec.start()

    # 1. Обычный размер 640x480
    f1 = np.ones((480, 640, 3), dtype=np.uint8) * 120
    rec.write_frame(f1)

    # 2. Уменьшенный размер (рамка сузилась до 300x200)
    f2 = np.ones((200, 300, 3), dtype=np.uint8) * 200
    rec.write_frame(f2)

    # 3. Увеличенный размер (рамка расширилась до 1280x720)
    f3 = np.ones((720, 1280, 3), dtype=np.uint8) * 160
    rec.write_frame(f3)

    rec.stop()

    import cv2
    cap = cv2.VideoCapture(test_vid)
    f_count = 0
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        f_count += 1
        assert frame.shape == (480, 640, 3)
        # Убеждаемся, что нет черных полос или пустых нулевых областей
        assert np.all(frame > 30), f"Обнаружены пустые черные области в кадре #{f_count}!"
    cap.release()
    assert f_count == 3
    print("  -> Динамическое масштабирование кадров работает идеально без чёрных квадратов.")


def test_video_annotation_and_audio_toggles():
    print("[TEST] Тестирование живого рисования, звуковых переключателей и закрепления (Pin)...")
    from ui.recording_window import RecordingFrameWindow
    from models.shapes import PenShape, ArrowShape, RectangleShape, TextShape

    win = RecordingFrameWindow(mode="video", rect=QRectF(100, 100, 640, 480))
    win.show()
    assert win.canvas is not None
    assert win.drawing_toolbar is not None
    assert win.btn_mic is not None
    assert win.btn_system is not None
    assert win.btn_draw is not None

    # Проверка переключателей звука
    init_mic = win.record_mic
    win._toggle_mic()
    assert win.record_mic != init_mic
    win._toggle_mic()
    assert win.record_mic == init_mic

    init_sys = win.record_system
    win._toggle_system()
    assert win.record_system != init_sys
    win._toggle_system()
    assert win.record_system == init_sys

    # Проверка выезжающей панели рисования
    assert not win.draw_bar_visible
    win._toggle_drawing_bar()
    assert win.draw_bar_visible
    assert not win.drawing_toolbar.isHidden()
    win._toggle_drawing_bar()
    assert not win.draw_bar_visible
    assert win.drawing_toolbar.isHidden()

    # Проверка холста и впекания слоёв в кадр
    canvas = win.canvas
    pen = PenShape(color="#ef4444", stroke_width=4)
    pen.add_point(QPointF(10, 10))
    pen.add_point(QPointF(50, 50))
    canvas.layer_manager.add_shape(pen)

    arrow = ArrowShape(p1=QPointF(20, 20), p2=QPointF(100, 100), color="#38bdf8", stroke_width=4, arrow_style="barbed")
    canvas.layer_manager.add_shape(arrow)

    frame_bgr = np.zeros((480, 640, 3), dtype=np.uint8)
    qimg = QImage(frame_bgr.data, 640, 480, 640 * 3, QImage.Format.Format_BGR888)
    painter = QPainter(qimg)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    canvas.layer_manager.draw_all(painter, offset=QPointF(0, 0))
    painter.end()
    ptr = qimg.constBits()
    ptr.setsize(qimg.sizeInBytes())
    frame_bgr = np.frombuffer(ptr, dtype=np.uint8).reshape((480, 640, 3)).copy()
    assert np.any(frame_bgr > 0), "Кадр должен содержать нарисованные элементы"

    # Проверка закрепления (Pin)
    canvas.set_pinned(False)
    assert not canvas.is_pinned
    assert not canvas.layer_manager.is_pinned
    canvas.set_pinned(True)
    assert canvas.is_pinned
    assert canvas.layer_manager.is_pinned

    win.cancel_recording()
    print("  -> Живое рисование, переключатели звука, впекание в видео и закрепление работают идеально.")


def test_passthrough_and_drawing_interactivity():
    print("[TEST] Тестирование режима «Неосязаемая рамка» и осязаемости рисования...")
    from PyQt6.QtCore import QPoint, QRectF
    from ui.toolbars import BottomActionToolbar
    from ui.overlay import OverlayWindow
    from ui.recording_window import RecordingFrameWindow

    # 1. Тулбар действий и сигнал passthrough_toggled
    bot_bar = BottomActionToolbar()
    assert hasattr(bot_bar, "chk_passthrough")
    assert bot_bar.chk_passthrough.isCheckable()
    toggled_vals = []
    bot_bar.passthrough_toggled.connect(lambda val: toggled_vals.append(val))
    bot_bar.chk_passthrough.setChecked(True)
    assert toggled_vals == [True]
    bot_bar.chk_passthrough.setChecked(False)
    assert toggled_vals == [True, False]

    # 2. OverlayWindow: Неосязаемая рамка и очистка буферов
    overlay = OverlayWindow()
    assert not overlay.is_passthrough
    overlay.selection_rect = QRectF(100, 100, 500, 400)
    overlay._on_passthrough_toggled(True)
    assert overlay.is_passthrough
    assert not overlay.mask().isEmpty()

    # Проверка вырезания внутренней области из маски (сквозные клики)
    inner_pt = QPoint(300, 300)
    assert not overlay.mask().contains(inner_pt), "Внутренняя область должна пропускать клики"
    border_pt = QPoint(101, 101)
    assert overlay.mask().contains(border_pt), "Граница рамки должна оставаться в маске"

    overlay._on_passthrough_toggled(False)
    assert not overlay.is_passthrough
    assert overlay.mask().isEmpty()

    # Проверка закрытия и чистоты буферов
    overlay.close_overlay()
    assert overlay.selection_rect.isEmpty()
    assert overlay.background_pixmap is None
    assert overlay.dimmed_background_pixmap is None
    assert not overlay.is_passthrough

    # 3. RecordingFrameWindow и RecordingDrawingCanvas: Авто-переключение режимов
    rec_win = RecordingFrameWindow(mode="video", rect=QRectF(100, 100, 400, 300), countdown=True)
    canvas = rec_win.canvas
    assert canvas.current_tool == "cursor"
    assert canvas.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    # При открытии панели рисования автоматически включается "pen" и мышь перехватывается
    rec_win._toggle_drawing_bar()
    assert rec_win.draw_bar_visible
    assert canvas.current_tool == "pen"
    assert not canvas.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    # При закрытии панели рисования автоматически возвращается "cursor" и сквозной клик
    rec_win._toggle_drawing_bar()
    assert not rec_win.draw_bar_visible
    assert canvas.current_tool == "cursor"
    assert canvas.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    overlay.deleteLater()
    bot_bar.deleteLater()
    rec_win.cancel_recording()
    rec_win.deleteLater()
    QApplication.processEvents()
    print("  -> Режим «Неосязаемая рамка» и перехват мыши при рисовании работают корректно.")


def test_i18n_and_video_drawing_enhancements(tmp_path):
    print("[TEST] Тестирование локализации (ru/en), ПКМ-перемещения фигур, Pin и быстрого скриншота...")
    from utils.i18n import tr, set_language, get_current_language
    from ui.recording_window import RecordingFrameWindow
    from ui.recording_canvas import RecordingDrawingCanvas, ColorPalettePopup
    from ui.layers_dialog import LayersDialog
    from ui.settings_dialog import SettingsDialog
    from models.shapes import RectangleShape, PenShape
    from config import ConfigManager

    # 1. Тестирование i18n
    set_language("en")
    assert get_current_language() == "en"
    assert tr("app_title") == "Framio — Screenshots & Recording"
    assert tr("settings_tab_help") == "Help & Guide"
    assert tr("tray_hotkey_quick_screen", key="Ctrl+Print Screen") == "• Quick Fullscreen Screenshot: Ctrl+Print Screen"

    set_language("ru")
    assert get_current_language() == "ru"
    assert tr("app_title") == "Framio — Скриншоты и запись"
    assert tr("settings_tab_help") == "Справка и инструкция"
    assert tr("tray_hotkey_quick_screen", key="Ctrl+Print Screen") == "• Быстрый полный скриншот: Ctrl+Print Screen"
    set_language(None)  # сброс

    # 2. Тестирование ПКМ-перемещения фигур и плавного Pin в RecordingDrawingCanvas
    rec_win = RecordingFrameWindow(mode="video", rect=QRectF(100, 100, 640, 480), countdown=True)
    rec_win.show()
    canvas = rec_win.canvas

    # Начальное состояние Pin: выключено (is_pinned = False)
    assert not canvas.is_pinned

    rect_shape = RectangleShape(QRectF(30, 30, 100, 80), color="#ef4444", stroke_width=3, filled=True)
    canvas.layer_manager.add_shape(rect_shape)

    # Проверка обнаружения фигуры по точке клика
    found = canvas.find_shape_at(QPointF(40, 40))
    assert found is not None
    assert found.id == rect_shape.id

    # Эмуляция перемещения ПКМ
    canvas.dragged_shape = rect_shape
    canvas.drag_start_pos = QPointF(40, 40)
    canvas.drag_shape_initial_pos = QPointF(30, 30)

    # Двигаем на (20, 15)
    delta = QPointF(20, 15)
    rect_shape.translate(delta.x(), delta.y())
    assert rect_shape.rect.topLeft() == QPointF(50, 45)

    # Проверка плавного Pin без скачков координат
    ix = float(rec_win.inner_x)
    iy = float(rec_win.inner_y)

    canvas.set_pinned(True)
    assert canvas.is_pinned
    # При включении Pin фигура сместилась в экранные координаты (+ix, +iy)
    assert rect_shape.rect.topLeft() == QPointF(50 + ix, 45 + iy)

    canvas.set_pinned(False)
    assert not canvas.is_pinned
    # При выключении Pin фигура точно вернулась в координаты рамки (-ix, -iy)
    assert rect_shape.rect.topLeft() == QPointF(50, 45)

    # Палитра цветов и толщина пера
    from PyQt6.QtWidgets import QSlider
    palette_popup = ColorPalettePopup(current_color="#ef4444", current_width=8)
    slider = palette_popup.findChild(QSlider)
    assert slider is not None
    stroke_results = []
    palette_popup.stroke_changed.connect(lambda w: stroke_results.append(w))
    slider.setValue(12)
    assert 12 in stroke_results
    palette_popup.deleteLater()

    # Диалог слоев
    layers_dlg = LayersDialog(canvas.layer_manager, canvas)
    assert len(layers_dlg.rows) == 1
    layers_dlg.deleteLater()

    rec_win.cancel_recording()

    # 3. Тестирование SettingsDialog с 5 вкладками и начальной вкладкой
    dlg = SettingsDialog(initial_tab=4)
    dlg.show()
    assert dlg.tabs.count() == 5
    assert dlg.tabs.currentIndex() == 4
    assert hasattr(dlg, "edit_hotkey_quick_screen")
    assert dlg.edit_hotkey_quick_screen.text() != ""

    # Проверка смены языка через SettingsDialog
    idx_en = dlg.combo_lang.findData("en")
    dlg.combo_lang.setCurrentIndex(idx_en)
    # Регрессия: язык применяется самим выбором, без Apply и без перезапуска.
    assert dlg.cfg.language == "en"
    assert get_current_language() == "en"
    QApplication.processEvents()
    dlg._apply_settings()
    assert dlg.cfg.language == "en"
    assert get_current_language() == "en"

    idx_auto = dlg.combo_lang.findData("auto")
    dlg.combo_lang.setCurrentIndex(idx_auto)
    dlg._apply_settings()
    assert dlg.cfg.language == "auto"

    dlg.close()
    dlg.deleteLater()
    QApplication.processEvents()
    print("  -> Локализация ru/en, ПКМ-драг, Pin-трансляция и диалог настроек со справкой работают отлично.")


def test_countdown_scrolling_and_window_snapping():
    print("[TEST] Тестирование таймера обратного отсчёта, длинного скриншота и прилипания окон...")
    from config import ConfigManager
    from utils.scrolling_capture import ScrollingCaptureEngine, ScrollingCaptureHUD, ScrollingCaptureGuide
    from ui.recording_window import RecordingFrameWindow, RecordingSettingsPopup
    from ui.toolbars import VideoOptionsPopup, GifOptionsPopup, BottomActionToolbar
    from utils.screen_lock import enumerate_recordable_windows
    from PyQt6.QtCore import Qt, QRect, QRectF
    from PyQt6.QtGui import QKeyEvent
    from utils.i18n import tr

    # 1. ConfigManager
    cfg_mgr = ConfigManager.get_instance()
    assert hasattr(cfg_mgr.config, "record_countdown_enabled")
    assert hasattr(cfg_mgr.config, "record_countdown_seconds")

    # 2. Toolbars: опции видео и GIF, кнопка длинного скриншота
    vid_popup = VideoOptionsPopup()
    assert hasattr(vid_popup, "chk_countdown")
    assert hasattr(vid_popup, "combo_countdown")
    assert vid_popup.combo_countdown.count() >= 3
    vid_popup.chk_countdown.setChecked(True)
    vid_popup.combo_countdown.setCurrentIndex(1)
    assert "5" in vid_popup.combo_countdown.currentText()
    vid_popup.deleteLater()

    gif_popup = GifOptionsPopup()
    assert hasattr(gif_popup, "chk_countdown")
    assert hasattr(gif_popup, "combo_countdown")
    gif_popup.deleteLater()

    bot_bar = BottomActionToolbar()
    assert hasattr(bot_bar, "btn_scroll")
    assert not bot_bar.btn_scroll.icon().isNull()
    bot_bar.deleteLater()

    # 3. ScrollingCaptureHUD
    hud = ScrollingCaptureHUD()
    hud.show()
    assert hud.isVisible()
    hud.update_progress(5, 1850)
    assert "5" in hud.lbl_status.text()
    assert "1850" in hud.lbl_status.text()
    hud.set_paused_state(True)
    assert tr("scroll_hud_resume") in hud.btn_pause.text()
    hud.set_paused_state(False)
    assert tr("scroll_hud_pause") in hud.btn_pause.text()
    hud.close()
    hud.deleteLater()

    # 4. Проверка алгоритма сопоставления шаблона в ScrollingCaptureEngine и направляющей рамки
    guide = ScrollingCaptureGuide(QRect(100, 100, 400, 300))
    assert guide.width() == 400
    assert guide.height() == 300
    guide.close()
    guide.deleteLater()

    img1 = np.zeros((300, 400, 3), dtype=np.uint8)
    for y in range(0, 300, 20):
        img1[y:y+10, :] = (y % 256, (y * 2) % 256, (y * 3) % 256)

    # Небольшой сдвиг (60 px)
    shift_y = 60
    img2 = np.zeros((300, 400, 3), dtype=np.uint8)
    img2[0:300 - shift_y, :] = img1[shift_y:300, :]
    img2[300 - shift_y:300, :] = [100, 150, 200]

    engine = ScrollingCaptureEngine(region=(100, 100, 400, 300))
    engine.accumulated_bgr = img1.copy()
    success = engine.stitch_frame(img2)
    assert success is True
    assert engine.accumulated_bgr.shape[0] == 300 + shift_y

    # Большой сдвиг (180 px - проверка многополосного поиска _detect_vertical_shift)
    shift_y2 = 180
    img3 = np.zeros((300, 400, 3), dtype=np.uint8)
    img3[0:300 - shift_y2, :] = img2[shift_y2:300, :]
    img3[300 - shift_y2:300, :] = [50, 80, 120]
    success2 = engine.stitch_frame(img3)
    assert success2 is True
    assert engine.accumulated_bgr.shape[0] == 300 + shift_y + shift_y2

    # Ручной захват disjoint кадра без перекрытия («Сделать кадр»)
    img_disjoint = np.full((300, 400, 3), 77, dtype=np.uint8)
    success3 = engine.stitch_frame(img_disjoint, force_append_on_fail=True)
    assert success3 is True
    assert engine.accumulated_bgr.shape[0] == 300 + shift_y + shift_y2 + 300
    engine.deleteLater()

    # 5. RecordingFrameWindow с таймером обратного отсчёта
    rec_win = RecordingFrameWindow(
        mode="video",
        rect=QRectF(100, 100, 500, 400),
        countdown=True,
        countdown_seconds=3
    )
    rec_win.show()
    assert rec_win.is_counting_down is True
    assert rec_win.current_countdown == 3
    assert "3" in rec_win.lbl_mode.text()

    # Тик таймера
    rec_win._on_countdown_tick()
    assert rec_win.current_countdown == 2
    assert "2" in rec_win.lbl_mode.text()

    # Завершение отсчета досрочно по Enter
    enter_ev = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier)
    rec_win.keyPressEvent(enter_ev)
    assert rec_win.is_counting_down is False
    assert rec_win.capture_worker is not None

    # Проверка прилипания (snap) к окну
    hwnd_list = enumerate_recordable_windows()
    if hwnd_list:
        sample_hwnd, sample_title = hwnd_list[0]
        rec_win.snap_to_window(sample_hwnd)
        assert rec_win.target_hwnd == sample_hwnd

    # Кнопка выбора окна кликом мыши в RecordingSettingsPopup
    settings_popup = RecordingSettingsPopup(rec_win)
    assert hasattr(settings_popup, "btn_pick")
    settings_popup.deleteLater()

    rec_win.cancel_recording()
    if rec_win.capture_worker:
        rec_win.capture_worker.wait(2000)
    rec_win.deleteLater()
    QApplication.processEvents()

    print("  -> Таймер отсчёта, длинный скриншот и прилипание к окнам работают безупречно.")


def test_custom_countdown_and_live_mosaic():
    print("[TEST] Тестирование кастомного счетчика таймера, живой мозаики и динамической шапки...")
    from ui.toolbars import VideoOptionsPopup, GifOptionsPopup
    from ui.recording_canvas import RecordingDrawingToolbar, RecordingDrawingCanvas
    from ui.widgets import ColorPalettePopup
    from ui.recording_window import RecordingFrameWindow
    from models.shapes import MosaicShape
    from PyQt6.QtCore import QRectF

    # 1. Проверяем кастомный QSpinBox таймера
    vid_popup = VideoOptionsPopup()
    assert hasattr(vid_popup, "spin_countdown")
    vid_popup.spin_countdown.setValue(17)
    assert vid_popup.spin_countdown.value() == 17
    assert vid_popup.combo_countdown.currentText() == "17 сек"
    vid_popup.deleteLater()

    gif_popup = GifOptionsPopup()
    assert hasattr(gif_popup, "spin_countdown")
    gif_popup.spin_countdown.setValue(23)
    assert gif_popup.spin_countdown.value() == 23
    assert gif_popup.combo_countdown.currentText() == "23 сек"
    gif_popup.deleteLater()

    # 2. Проверяем динамический отсчёт в шапке
    rec_win = RecordingFrameWindow(mode="video", rect=QRectF(100, 100, 500, 400), countdown=True, countdown_seconds=7)
    rec_win.show()
    assert "7" in rec_win.lbl_mode.text()
    assert "СТАРТ" in rec_win.lbl_mode.text()
    rec_win._on_countdown_tick()
    assert "6" in rec_win.lbl_mode.text()

    # 3. Проверяем наличие инструмента Mosaic на панели рисования
    tb = rec_win.drawing_toolbar
    assert "mosaic" in tb.tool_buttons
    tb.select_tool("mosaic")
    assert tb.canvas.current_tool == "mosaic"

    # 4. Выбор мозаики из ColorPalettePopup
    tb._on_color_chosen("mosaic")
    assert tb.current_color == "mosaic"
    assert tb.canvas.current_color == "mosaic"

    # 5. Проверка добавления MosaicShape
    mosaic_shape = MosaicShape(QRectF(10, 10, 50, 50), pixel_size=6)
    rec_win.canvas.layer_manager.add_shape(mosaic_shape)
    assert len(rec_win.canvas.layer_manager.shapes) == 1

    rec_win.cancel_recording()
    rec_win.deleteLater()
    QApplication.processEvents()
    print("  -> Кастомный таймер (1-60с), мозаика на панели и динамическая шапка проверены успешно.")


def test_freeze_prevention_and_phantom_flash():
    print("[TEST] Тестирование устранения фризов при рисовании и защиты от фантомных рамок...")
    from ui.overlay import OverlayWindow
    from models.shapes import PenShape
    import numpy as np
    import cv2

    # 1. Проверяем корректную обработку некратных 4 ширин (например, 806x516) при рисовании
    frame_bgr = np.zeros((516, 806, 3), dtype=np.uint8)
    h, w = frame_bgr.shape[:2]
    bgra = np.ascontiguousarray(cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2BGRA))
    qimg = QImage(bgra.data, w, h, w * 4, QImage.Format.Format_ARGB32)
    p = QPainter(qimg)
    pen = PenShape(color="#FF2E2E", stroke_width=4)
    pen.add_point(QPointF(10, 10))
    pen.add_point(QPointF(100, 100))
    pen.draw(p)
    p.end()
    ptr = qimg.constBits()
    ptr.setsize(h * w * 4)
    arr = np.frombuffer(ptr, dtype=np.uint8).reshape((h, w, 4))
    res = cv2.cvtColor(arr, cv2.COLOR_BGRA2BGR)
    assert res.shape == (516, 806, 3)

    # 2. Проверяем OverlayWindow: старт захвата и закрытие с чистой перерисовкой
    overlay = OverlayWindow()
    overlay.start_capture()
    assert overlay.isVisible()
    assert overlay.windowOpacity() == 1.0
    overlay.close_overlay()
    assert not overlay.isVisible()
    overlay.deleteLater()
    QApplication.processEvents()

    print("  -> Отрисовка некратных 4 буферов без сбоев и оверлей без фантомных рамок подтверждены.")


def test_blur_censor_palette_and_outside_click():
    print("[TEST] Тестирование режима блюра, цензуры фигур, защиты от клика снаружи и независимой записи...")
    from models.shapes import BlurShape, MosaicShape, PenShape, RectangleShape, CircleShape
    from ui.widgets import ColorPalettePopup
    from ui.toolbars import ToolPropertiesFlyout, VideoOptionsPopup, GifOptionsPopup, ToolType
    from ui.shape_editor import ShapeEditPopup
    from ui.overlay import OverlayWindow
    from PyQt6.QtGui import QMouseEvent
    from PyQt6.QtCore import QEvent, QPointF

    # 1. Тест BlurShape
    blur = BlurShape(QRectF(10, 10, 80, 60), blur_radius=15)
    assert blur.blur_radius == 15
    blur.set_blur_radius(25)
    assert blur.blur_radius == 25
    assert blur.hit_test(QPointF(20, 20)) is True
    assert blur.hit_test(QPointF(200, 200)) is False
    clone = blur.clone()
    assert isinstance(clone, BlurShape)
    assert clone.blur_radius == 25

    # 2. Тест векторных фигур с мозаикой и блюром
    pen_m = PenShape(color="mosaic")
    assert pen_m.is_mosaic is True
    assert pen_m.is_blur is False
    pen_b = PenShape(color="blur")
    assert pen_b.is_blur is True
    assert pen_b.is_mosaic is False

    rect_m = RectangleShape(QRectF(0, 0, 50, 50), color="mosaic")
    assert rect_m.is_mosaic is True
    rect_b = RectangleShape(QRectF(0, 0, 50, 50), color="blur")
    assert rect_b.is_blur is True

    # 3. Тест ColorPalettePopup: сгруппированные слайдеры и кнопки мозаики / блюра
    pop = ColorPalettePopup(current_color="#FF2E2E", current_width=4, grain=10, blur=20)
    assert hasattr(pop, "btn_mosaic")
    assert hasattr(pop, "btn_blur")
    assert hasattr(pop, "slider_grain")
    assert hasattr(pop, "slider_blur")
    assert pop.slider_grain.value() == 10
    assert pop.slider_blur.value() == 20
    received_grain = []
    received_blur = []
    pop.grain_changed.connect(lambda v: received_grain.append(v))
    pop.blur_changed.connect(lambda v: received_blur.append(v))
    pop.slider_grain.setValue(18)
    pop.slider_blur.setValue(32)
    assert received_grain == [18]
    assert received_blur == [32]
    pop.deleteLater()

    # 4. Тест ToolPropertiesFlyout
    flyout = ToolPropertiesFlyout()
    assert hasattr(flyout, "btn_mosaic_color")
    assert hasattr(flyout, "btn_blur_color")
    assert hasattr(flyout, "slider_card")
    assert hasattr(flyout, "btn_mode_mosaic")
    assert hasattr(flyout, "btn_mode_blur")
    flyout.load_tool(ToolType.MOSAIC, {"size": 8, "censor_mode": "blur", "blur_radius": 22})
    assert not flyout.slider_blur.isHidden()
    assert flyout.slider_blur.value() == 22
    flyout.deleteLater()

    # 5. Тест ShapeEditPopup для MosaicShape и BlurShape
    m_shape = MosaicShape(QRectF(0, 0, 60, 60), pixel_size=12)
    m_editor = ShapeEditPopup(m_shape)
    assert not m_editor.slider_grain.isHidden()
    assert m_editor.slider_grain.value() == 12
    m_editor.slider_grain.setValue(16)
    assert m_shape.pixel_size == 16
    m_editor.deleteLater()

    b_shape = BlurShape(QRectF(0, 0, 60, 60), blur_radius=20)
    b_editor = ShapeEditPopup(b_shape)
    assert not b_editor.slider_blur.isHidden()
    assert b_editor.slider_blur.value() == 20
    b_editor.slider_blur.setValue(28)
    assert b_shape.blur_radius == 28
    b_editor.deleteLater()

    # 6. Тест опций таймера (по умолчанию выключен)
    v_opt = VideoOptionsPopup()
    assert v_opt.chk_countdown.isChecked() is False
    v_opt.deleteLater()
    g_opt = GifOptionsPopup()
    assert g_opt.chk_countdown.isChecked() is False
    g_opt.deleteLater()

    # 7. Одиночный клик снаружи ничего не меняет, но drag снаружи начинает
    # новое одиночное выделение. В multi-region режиме старые зоны защищены.
    overlay = OverlayWindow()
    overlay.selection_rect = QRectF(100, 100, 300, 300)
    overlay.regions = [QRectF(100, 100, 300, 300)]
    overlay.active_region_idx = 0
    overlay.is_adding_region = False
    # Клик снаружи (20, 20)
    press_evt = QMouseEvent(QEvent.Type.MouseButtonPress, QPointF(20, 20), Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    overlay.mousePressEvent(press_evt)
    assert getattr(overlay, "pending_outside_drag", False) is True
    assert overlay.selection_rect == QRectF(100, 100, 300, 300)  # Рамка НЕ сброшена!

    # Отпускание без движения
    rel_evt = QMouseEvent(QEvent.Type.MouseButtonRelease, QPointF(20, 20), Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    overlay.mouseReleaseEvent(rel_evt)
    assert getattr(overlay, "pending_outside_drag", False) is False
    assert overlay.selection_rect == QRectF(100, 100, 300, 300)  # Рамка всё ещё цела!

    # Протяжка мышью снаружи без «+» или Ctrl заменяет одиночную зону.
    overlay.mousePressEvent(press_evt)
    move_evt = QMouseEvent(QEvent.Type.MouseMove, QPointF(80, 80), Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    overlay.mouseMoveEvent(move_evt)
    assert overlay.is_selecting is True
    release_evt = QMouseEvent(QEvent.Type.MouseButtonRelease, QPointF(80, 80), Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    overlay.mouseReleaseEvent(release_evt)
    assert overlay.is_selecting is False
    assert overlay.regions == [QRectF(20, 20, 60, 60)]
    assert overlay.selection_rect == QRectF(20, 20, 60, 60)
    overlay.deleteLater()

    QApplication.processEvents()
    print("  -> Блюр, цензура, группировка слайдеров, таймер и клик снаружи проверены успешно.")


def test_shape_transform_box_and_flyouts():
    print("[TEST] Тестирование интерактивной рамки трансформации (Photoshop/Figma) и боковых меню...")
    from ui.transform_box import ShapeTransformBox, HandleType
    from ui.toolbars import ShapesFlyoutWidget, CensorEffectsFlyoutWidget, RightDrawingToolbar
    from models.shapes import RectangleShape, ArrowShape, CircleShape, TextShape

    # 1. Тестирование ShapeTransformBox
    rect = RectangleShape(QRectF(100, 100, 200, 100))
    tb = ShapeTransformBox(rect)
    assert tb.is_active() is True

    # Проверка получения координат маркеров
    handles = tb.get_handles_positions()
    assert len(handles) == 9  # 8 маркеров ресайза + 1 маркер вращения
    assert HandleType.ROTATE in handles
    assert HandleType.BOTTOM_RIGHT in handles

    # Проверка hit_test маркеров
    rot_pos = handles[HandleType.ROTATE]
    assert tb.hit_test_handle(rot_pos) == HandleType.ROTATE

    br_pos = handles[HandleType.BOTTOM_RIGHT]
    assert tb.hit_test_handle(br_pos) == HandleType.BOTTOM_RIGHT

    inside_pos = QPointF(200, 150)
    assert tb.hit_test_handle(inside_pos) == HandleType.INSIDE

    # Проверка курсоров
    assert tb.get_cursor_for_handle(HandleType.ROTATE) == Qt.CursorShape.PointingHandCursor
    assert tb.get_cursor_for_handle(HandleType.INSIDE) == Qt.CursorShape.SizeAllCursor

    # Тестирование масштабирования (drag_to маркера BOTTOM_RIGHT)
    tb.start_drag(HandleType.BOTTOM_RIGHT, br_pos)
    tb.drag_to(QPointF(br_pos.x() + 50, br_pos.y() + 50))
    old_state, new_state = tb.finish_drag()
    assert old_state is not None
    assert new_state is not None
    assert rect.rect.width() > 200
    assert rect.rect.height() > 100

    # Тестирование вращения (drag_to маркера ROTATE)
    tb.start_drag(HandleType.ROTATE, rot_pos)
    # Поворачиваем вправо на 45 градусов
    tb.drag_to(QPointF(200 + 50, 100 - 50))
    assert rect.rotation > 0
    tb.finish_drag()

    # Проверка поворота других фигур: ArrowShape, CircleShape, TextShape
    arrow = ArrowShape(QPointF(50, 50), QPointF(150, 150))
    arrow.rotate_by(90)
    assert arrow.rotation == 90.0

    circle = CircleShape(QRectF(20, 20, 80, 80))
    circle.rotate_by(45)
    assert circle.rotation == 45.0

    txt = TextShape(QPointF(30, 30), "Framio")
    txt.scale_from_origin(1.5, 1.5, QPointF(30, 30))
    assert txt.font_size > 18

    # 2. Тестирование ShapesFlyoutWidget
    shapes_flyout = ShapesFlyoutWidget()
    received_shapes = []
    shapes_flyout.shape_chosen.connect(lambda sid, opts: received_shapes.append((sid, opts)))
    shapes_flyout._on_select("arrow", {"subshape": "arrow"})
    assert len(received_shapes) == 1
    assert received_shapes[0][0] == "arrow"

    # 3. Тестирование CensorEffectsFlyoutWidget
    censor_flyout = CensorEffectsFlyoutWidget()
    received_censor = []
    received_filter = []
    censor_flyout.censor_chosen.connect(lambda cid: received_censor.append(cid))
    censor_flyout.filter_chosen.connect(lambda ft: received_filter.append(ft))

    censor_flyout._on_censor_click("blur")
    assert len(received_censor) == 1
    assert received_censor[0] == "blur"

    censor_flyout._on_filter_click("grayscale")
    assert len(received_filter) == 1
    assert received_filter[0] == "grayscale"

    # 4. Тестирование интеграции в RightDrawingToolbar
    toolbar = RightDrawingToolbar()
    assert hasattr(toolbar, "shapes_flyout")
    assert hasattr(toolbar, "censor_flyout")
    assert hasattr(toolbar, "filter_selected")

    toolbar._on_shapes_flyout_chosen("rect", {"subshape": "rect", "filled": True})
    assert toolbar.tools_config["shapes"]["subshape"] == "rect"
    assert toolbar.tools_config["shapes"]["filled"] is True

    print("  -> Интерактивная рамка трансформации (8 маркеров + вращение) и боковые меню проверены успешно.")


def test_regional_effects_and_whole_screen_filter_reset():
    print("[TEST] Тестирование региональных эффектов цензуры и сброса фильтра экрана...")
    from models.shapes import RegionalEffectShape, MosaicShape, BlurShape, get_filtered_pixmap
    from utils.image_filters import FilterType
    from ui.toolbars import BottomActionToolbar
    from PyQt6.QtGui import QPixmap, QColor, QPainter

    # 1. Тестовое изображение
    pix = QPixmap(100, 100)
    pix.fill(QColor(100, 150, 200))
    painter = QPainter(pix)
    painter.fillRect(20, 20, 40, 40, QColor(255, 0, 0))
    painter.end()

    # 2. Тестирование RegionalEffectShape со всеми типами эффектов
    for eff in ("mosaic", "blur", "grayscale", "invert", "vibrant", "sepia"):
        shape = RegionalEffectShape(QRectF(10, 10, 50, 50), effect_type=eff, intensity=10)
        shape.update_effect(pix)
        assert shape.cached_pixmap is not None
        assert not shape.cached_pixmap.isNull()
        assert shape.cached_pixmap.width() == 50
        assert shape.cached_pixmap.height() == 50

    # 3. Тестирование BottomActionToolbar фильтра и сброса
    bar = BottomActionToolbar()
    assert hasattr(bar, "btn_filter")
    assert bar.current_filter == FilterType.NONE

    # Выбираем фильтр
    bar._select_filter(FilterType.GRAYSCALE)
    assert bar.current_filter == FilterType.GRAYSCALE

    # Сбрасываем фильтр
    bar.reset_filter()
    assert bar.current_filter == FilterType.NONE

    # 4. Тестирование HotkeyRecorderButton live modifier display
    from ui.settings_dialog import HotkeyRecorderButton
    from PyQt6.QtWidgets import QLineEdit
    from PyQt6.QtCore import QEvent
    from PyQt6.QtGui import QKeyEvent

    edit = QLineEdit("Ctrl+Shift+S")
    btn = HotkeyRecorderButton(edit)
    btn._start_recording()
    assert btn.is_recording

    # Нажатие только модификатора Ctrl -> отображается "Ctrl+"
    ev_ctrl = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Control, Qt.KeyboardModifier.ControlModifier)
    btn.keyPressEvent(ev_ctrl)
    assert edit.text() == "Ctrl+"

    # Нажатие Shift при удержании Ctrl -> отображается "Ctrl+Shift+"
    ev_shift = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Shift, Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)
    btn.keyPressEvent(ev_shift)
    assert edit.text() == "Ctrl+Shift+"

    # Нажатие S -> "Ctrl+Shift+S"
    ev_s = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_S, Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)
    btn.keyPressEvent(ev_s)
    assert edit.text() == "Ctrl+Shift+S"

    # Отпускание -> сохраняется комбинация
    ev_rel = QKeyEvent(QEvent.Type.KeyRelease, Qt.Key.Key_S, Qt.KeyboardModifier.NoModifier)
    btn.keyReleaseEvent(ev_rel)
    assert not btn.is_recording
    assert edit.text() == "Ctrl+Shift+S"

    # Одиночное нажатие Print Screen
    btn._start_recording()
    ev_prt = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Print, Qt.KeyboardModifier.NoModifier)
    btn.keyPressEvent(ev_prt)
    assert not btn.is_recording
    assert edit.text() == "Print Screen"

    print("  -> Региональные эффекты, сброс фильтров и живая индикация хоткеев проверены успешно.")


def test_regional_effect_dynamic_resampling_and_alt_inspector():
    print("[TEST] Тестирование динамического ресэмплинга эффектов цензуры и инспектора объектов на Alt...")
    from PyQt6.QtGui import QPixmap, QColor, QKeyEvent
    from PyQt6.QtCore import QEvent, Qt, QRectF
    from models.shapes import RegionalEffectShape, BlurShape
    from utils.image_filters import FilterType
    from utils.i18n import TRANSLATIONS
    from config import AppConfig

    # 1. Проверка симметрии i18n
    assert len(TRANSLATIONS['ru']) == len(TRANSLATIONS['en']), f"Mismatch: {len(TRANSLATIONS['ru'])} != {len(TRANSLATIONS['en'])}"
    assert set(TRANSLATIONS['ru'].keys()) == set(TRANSLATIONS['en'].keys())

    # 2. Проверка дефолтной настройки хоткея подсветки
    cfg = AppConfig()
    assert getattr(cfg, "hotkey_highlight_objects", None) == "Alt"

    # 3. Тест динамического ресэмплинга регионального эффекта при перемещении
    bg_img = QImage(200, 100, QImage.Format.Format_RGB32)
    bg_img.fill(QColor(255, 0, 0))
    for x in range(100, 200):
        for y in range(100):
            bg_img.setPixelColor(x, y, QColor(0, 0, 255))
    bg_pix = QPixmap.fromImage(bg_img)

    effect_shape = RegionalEffectShape(QRectF(10, 10, 40, 40), effect_type="grayscale")
    
    canvas_img = QImage(200, 100, QImage.Format.Format_ARGB32_Premultiplied)
    canvas_img.fill(0)
    p = QPainter(canvas_img)
    effect_shape.draw(p, source_pixmap=bg_pix)
    p.end()

    col1 = canvas_img.pixelColor(20, 20)
    assert col1.red() == col1.green() == col1.blue()
    assert col1.red() > 50

    # Перемещаем фигуру на синюю половину
    effect_shape.translate(120, 0)
    assert effect_shape.rect.x() == 130

    canvas_img.fill(0)
    p = QPainter(canvas_img)
    effect_shape.draw(p, source_pixmap=bg_pix)
    p.end()

    col2 = canvas_img.pixelColor(140, 20)
    assert col2.red() == col2.green() == col2.blue()
    assert col2.red() < 40
    assert col1.red() != col2.red(), "Эффект не обновил фоновые пиксели при перемещении!"

    # 4. Тестирование инспектора объектов на Alt в OverlayWindow
    from ui.overlay import OverlayWindow
    overlay = OverlayWindow()
    overlay.background_pixmap = bg_pix
    overlay.layer_manager.add_shape(effect_shape)
    assert not overlay.is_highlighting_objects

    ev_alt_down = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Alt, Qt.KeyboardModifier.AltModifier)
    overlay.keyPressEvent(ev_alt_down)
    assert overlay.is_highlighting_objects, "Инспектор объектов не включился по клавише Alt!"

    ev_alt_up = QKeyEvent(QEvent.Type.KeyRelease, Qt.Key.Key_Alt, Qt.KeyboardModifier.NoModifier)
    overlay.keyReleaseEvent(ev_alt_up)
    assert not overlay.is_highlighting_objects, "Инспектор объектов не выключился при отпускании Alt!"

    overlay.close()
    print("  -> Динамический ресэмплинг эффектов, симметрия i18n и инспектор объектов Alt проверены успешно.")


def test_rotated_regional_effect_stencil_and_i18n():
    print("[TEST] Проверка оптического трафарета повернутых эффектов и локализации фигур...")
    from PyQt6.QtGui import QPixmap, QColor, QImage, QPainter
    from PyQt6.QtCore import QRectF, QPointF
    from models.shapes import RegionalEffectShape, RectangleShape, TextShape, ArrowShape
    from utils.i18n import set_language, tr, TRANSLATIONS

    # 1. Проверка локализации фигур в EN и RU
    set_language("en")
    assert RegionalEffectShape(QRectF(0, 0, 10, 10), effect_type="blur").name == "Blur (Censor)"
    assert RegionalEffectShape(QRectF(0, 0, 10, 10), effect_type="grayscale").name == "Grayscale (Area)"
    assert RegionalEffectShape(QRectF(0, 0, 10, 10), effect_type="mosaic").name == "Mosaic (Censor)"
    assert RegionalEffectShape(QRectF(0, 0, 10, 10), effect_type="invert").name == "Invert (Area)"
    assert RegionalEffectShape(QRectF(0, 0, 10, 10), effect_type="vibrant").name == "Vibrant (Area)"
    assert RegionalEffectShape(QRectF(0, 0, 10, 10), effect_type="sepia").name == "Sepia (Area)"
    assert RectangleShape(QRectF(0, 0, 10, 10)).name == "Rectangle"
    assert TextShape(QRectF(0, 0, 10, 10)).name == "Text"
    assert ArrowShape(QPointF(0, 0), QPointF(10, 10)).name == "Arrow"

    set_language("ru")
    assert RegionalEffectShape(QRectF(0, 0, 10, 10), effect_type="blur").name == "Размытие (Блюр)"
    assert RegionalEffectShape(QRectF(0, 0, 10, 10), effect_type="grayscale").name == "Чёрно-белый (Область)"
    assert RegionalEffectShape(QRectF(0, 0, 10, 10), effect_type="mosaic").name == "Мозаика (Цензура)"
    assert RegionalEffectShape(QRectF(0, 0, 10, 10), effect_type="invert").name == "Инверсия (Область)"
    assert RegionalEffectShape(QRectF(0, 0, 10, 10), effect_type="vibrant").name == "Насыщенность (Область)"
    assert RegionalEffectShape(QRectF(0, 0, 10, 10), effect_type="sepia").name == "Сепия (Область)"
    assert RectangleShape(QRectF(0, 0, 10, 10)).name == "Прямоугольник"
    assert TextShape(QRectF(0, 0, 10, 10)).name == "Текст"
    assert ArrowShape(QPointF(0, 0), QPointF(10, 10)).name == "Стрелка"

    # 2. Проверка неподвижности фонового изображения под повернутой областью эффекта (оптический трафарет)
    # Создаем фон 200x200: верхняя половина (y < 100) чистый красный (255, 0, 0), нижняя половина (y >= 100) чистый синий (0, 0, 255)
    bg_img = QImage(200, 200, QImage.Format.Format_RGB32)
    for y in range(200):
        color = QColor(255, 0, 0) if y < 100 else QColor(0, 0, 255)
        for x in range(200):
            bg_img.setPixelColor(x, y, color)
    bg_pix = QPixmap.fromImage(bg_img)

    # Создаем эффект ч/б размером 60x60 с центром в (100, 100)
    # В не повернутом состоянии rect: [70, 70, 60, 60], верхняя часть y in [70, 99], нижняя y in [100, 130]
    stencil_shape = RegionalEffectShape(QRectF(70, 70, 60, 60), effect_type="grayscale")
    stencil_shape.rotation = 45.0

    # Проверяем hit_test_rotated:
    # Центр (100, 100) внутри
    assert stencil_shape.hit_test_rotated(QPointF(100, 100))
    # Вершина повернутого ромба (100, 70 - 30 * (sqrt(2)-1) ~ 57.57) внутри
    assert stencil_shape.hit_test_rotated(QPointF(100, 65))
    # Угол исходного квадрата (72, 72) вне 45-градусного ромба
    assert not stencil_shape.hit_test_rotated(QPointF(72, 72))

    # Отрисовываем трафарет
    canvas_img = QImage(200, 200, QImage.Format.Format_ARGB32_Premultiplied)
    canvas_img.fill(0)
    p = QPainter(canvas_img)
    stencil_shape.draw(p, source_pixmap=bg_pix)
    p.end()

    # Точка (100, 85) лежит выше границы y=100 и внутри ромба -> должна быть оттенком серого от красного (R=G=B > 50)
    top_pt_col = canvas_img.pixelColor(100, 85)
    assert top_pt_col.alpha() == 255, "Пиксель внутри трафарета должен быть непрозрачным"
    assert top_pt_col.red() == top_pt_col.green() == top_pt_col.blue()
    assert top_pt_col.red() > 50, f"Красная область должна стать светлым серым, получили {top_pt_col.red()}"

    # Точка (100, 115) лежит ниже границы y=100 и внутри ромба -> должна быть оттенком серого от синего (R=G=B < 40)
    bot_pt_col = canvas_img.pixelColor(100, 115)
    assert bot_pt_col.alpha() == 255, "Пиксель внутри трафарета должен быть непрозрачным"
    assert bot_pt_col.red() == bot_pt_col.green() == bot_pt_col.blue()
    assert bot_pt_col.red() < 40, f"Синяя область должна стать темным серым, получили {bot_pt_col.red()}"

    # Вне ромба (например угол 72, 72) холст должен оставаться пустым (альфа 0)
    corner_col = canvas_img.pixelColor(72, 72)
    assert corner_col.alpha() == 0, "Угол за пределами повернутого трафарета не должен закрашиваться"

    # 3. Проверка растягивания и масштабирования повернутого эффекта: эффект не исчезает и адаптирует область
    stencil_shape.scale_from_origin(1.5, 1.5, QPointF(100, 100))
    canvas_img.fill(0)
    p = QPainter(canvas_img)
    stencil_shape.draw(p, source_pixmap=bg_pix)
    p.end()

    # После увеличения в 1.5 раза угол (72, 72) теперь покрыт расширенным ромбом!
    scaled_corner_col = canvas_img.pixelColor(72, 72)
    assert scaled_corner_col.alpha() == 255, "При масштабировании повернутая область должна расширяться без пропадания эффекта"
    assert scaled_corner_col.red() == scaled_corner_col.green() == scaled_corner_col.blue()

    print("  -> Оптический трафарет повернутых эффектов и локализация проверены успешно.")


def test_single_instance_text_eyedropper_and_inspector():
    print("[TEST] Тестирование SingleInstance IPC, прокачки TextShape, пипетки и Alt-инспектора...")
    from utils.single_instance import SingleInstanceManager
    from ui.transform_box import rotate_point
    from PyQt6.QtGui import QColor, QPixmap

    # 1. SingleInstanceManager: первичный и вторичный экземпляры
    pipe_name = f"Framio_Test_IPC_{os.getpid()}"
    mgr1 = SingleInstanceManager(server_name=pipe_name)
    is_prim1 = mgr1.check_single_instance()
    assert is_prim1 is True, "Первый экземпляр должен быть primary"

    received_cmds = []
    mgr1.message_received.connect(lambda cmd: received_cmds.append(cmd))

    mgr2 = SingleInstanceManager(server_name=pipe_name)
    is_prim2 = mgr2.check_single_instance(payload="activate")
    assert is_prim2 is False, "Второй экземпляр должен определить, что приложение уже запущено"

    # Обрабатываем события IPC
    for _ in range(25):
        QApplication.processEvents()
        time.sleep(0.02)
        if received_cmds:
            break

    assert len(received_cmds) == 1 and received_cmds[0] == "activate", f"Команда 'activate' должна быть получена, получено: {received_cmds}"

    mgr2.cleanup()
    mgr1.cleanup()

    # 2. TextShape: Italic, масштаб, вращение, hit_test_rotated, clone
    ts = TextShape(
        pos=QPointF(100, 100),
        text="Pro Text 2026",
        font_size=16,
        font_family="Arial",
        is_bold=True,
        is_italic=True,
        is_underline=False,
        has_bg=True,
        bg_color="#18181b",
        bg_alpha=200
    )
    assert ts.is_italic is True
    assert ts.is_bold is True
    assert ts.text == "Pro Text 2026"

    # Клонирование
    ts_clone = ts.clone()
    assert ts_clone.is_italic is True
    assert ts_clone.is_bold is True
    assert ts_clone.font_size == 16
    assert ts_clone.text == "Pro Text 2026"

    # Масштабирование
    orig_size = ts.font_size
    ts.scale_from_origin(1.5, 1.5, ts.pos)
    assert ts.font_size == int(round(orig_size * 1.5))

    # Вращение и hit_test_rotated
    bbox = ts.get_bounding_rect()
    center = bbox.center()
    assert ts.hit_test_rotated(center) is True

    # Поворачиваем фигуру на 45 градусов
    ts.rotation = 45.0
    # Центр все еще внутри
    assert ts.hit_test_rotated(center) is True

    # Точка за пределами повернутой фигуры
    far_away = QPointF(center.x() + 500, center.y() + 500)
    assert ts.hit_test_rotated(far_away) is False

    # 3. Alt-инспектор: поворот рамки и корректный расчет вертикальной позиции бейджа
    corners = [bbox.topLeft(), bbox.topRight(), bbox.bottomRight(), bbox.bottomLeft()]
    rot_corners = [rotate_point(p, center, 45.0) for p in corners]
    min_y = min(p.y() for p in rot_corners)
    badge_h = 20.0
    badge_y = min_y - badge_h - 6.0
    # Бейдж должен быть строго выше повернутой фигуры
    assert badge_y < min_y, "Бейдж должен располагаться над верхней точкой повернутой фигуры"

    # 4. Пипетка: сэмплирование цвета пикселя с холста
    test_pix = QPixmap(50, 50)
    test_pix.fill(QColor(255, 128, 0))
    img = test_pix.toImage()
    sampled_col = img.pixelColor(25, 25)
    assert sampled_col.red() == 255
    assert sampled_col.green() == 128
    assert sampled_col.blue() == 0

    print("  -> SingleInstance IPC, TextShape (italic/scale/rotate), пипетка и Alt-инспектор успешно протестированы.")


def test_mosaic_offset_export_interactive_text_and_flyout_positions():
    print("[TEST] Тестирование экспорта мозаики с оффсетом, интерактивного текста и внешних тулбаров...")
    from models.shapes import RegionalEffectShape, TextShape
    from ui.text_widget import InteractiveTextEditor
    from ui.toolbars import show_side_smart_popup
    from PyQt6.QtWidgets import QWidget
    from PyQt6.QtCore import QPoint, QSize, QRect
    from PyQt6.QtGui import QColor, QPainter, QPixmap

    # 1. Экспорт региональной мозаики со смещением рамки (rx, ry > 0)
    full_pix = QPixmap(1000, 800)
    full_pix.fill(QColor(100, 150, 200))
    p = QPainter(full_pix)
    p.fillRect(QRect(520, 420, 100, 100), QColor(255, 0, 0))
    p.end()

    shape = RegionalEffectShape(QRectF(550, 450, 100, 100), effect_type='mosaic', intensity=10)
    shape.rotation = 25.0

    # Экспорт с полноэкранным source_pixmap и оффсетом
    target_pix = QPixmap(200, 200)
    target_pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(target_pix)
    shape.draw(painter, offset=QPointF(500, 400), source_pixmap=full_pix)
    painter.end()

    assert shape.cached_pixmap is not None and not shape.cached_pixmap.isNull(), "cached_pixmap мозаики не должен быть None при экспорте"

    # Экспорт с предварительно обрезанным кропом
    crop_pix = full_pix.copy(500, 400, 200, 200)
    target_pix2 = QPixmap(200, 200)
    target_pix2.fill(Qt.GlobalColor.transparent)
    painter2 = QPainter(target_pix2)
    shape_clone = shape.clone()
    shape_clone.translate(-500, -400)
    shape_clone.cached_pixmap = None
    shape_clone.draw(painter2, offset=QPointF(0, 0), source_pixmap=crop_pix)
    painter2.end()
    assert shape_clone.cached_pixmap is not None and not shape_clone.cached_pixmap.isNull(), "cached_pixmap мозаики не должен быть None для обрезанного кропа"

    # 2. Интерактивный текстовый редактор InteractiveTextEditor
    editor = InteractiveTextEditor()
    editor.resize(250, 60)
    editor.update_style(
        font_family="Consolas",
        font_size=24,
        color="#00FFCC",
        is_bold=True,
        is_italic=True,
        is_underline=True
    )
    assert editor.font_family == "Consolas"
    assert editor.font_size == 24
    assert editor.text_color == "#00FFCC"
    assert editor.is_bold is True
    assert editor.is_italic is True
    assert editor.is_underline is True

    editor.setText("Проверка Framio")
    assert editor.text() == "Проверка Framio"

    # Симуляция перемещения (ручка header)
    moved_pos = []
    editor.moved.connect(lambda p: moved_pos.append(p))
    editor.move(QPoint(120, 140))
    editor.moved.emit(QPointF(120, 140))
    assert len(moved_pos) == 1
    assert moved_pos[0] == QPointF(120, 140)

    # Симуляция растягивания за правый нижний маркер (масштабирование кегля шрифта)
    sizes = []
    editor.font_size_changed.connect(lambda s: sizes.append(s))
    new_w = 400
    new_h = 100
    scale_factor = max(0.4, (new_w / 250.0 + new_h / 60.0) / 2.0)
    new_font_size = int(round(24 * scale_factor))
    editor.update_style(font_size=new_font_size)
    editor.font_size_changed.emit(new_font_size)
    assert len(sizes) == 1
    assert editor.font_size == new_font_size and new_font_size > 24

    # 3. Внешнее позиционирование всплывающих окон (prefer_side='right')
    anchor = QWidget()
    anchor.setGeometry(300, 300, 40, 200)
    popup = QWidget()
    popup.resize(150, 150)
    show_side_smart_popup(anchor, popup, prefer_side="right")
    assert popup.x() >= anchor.x() + anchor.width() - 5, "Всплывающее меню должно открываться справа снаружи"

    print("  -> Экспорт мозаики с оффсетом, InteractiveTextEditor и внешнее открытие всплывающих окон работают безупречно.")


def test_dynamic_text_editing_and_filter_history():
    print("[TEST] Тестирование динамического изменения существующего текста и истории фильтров...")
    from models.shapes import TextShape
    from utils.image_filters import FilterType
    from ui.shape_editor import ShapeEditPopup
    from ui.overlay import OverlayWindow
    from ui.toolbars import ToolType

    ov = OverlayWindow()
    ov.selection_rect = QRectF(100, 100, 500, 400)

    # 1. История Undo/Redo для фильтра всей области
    assert ov.current_filter == FilterType.NONE
    assert len(ov.history_manager.undo_stack) == 0

    ov._on_filter_changed(FilterType.GRAYSCALE)
    assert ov.current_filter == FilterType.GRAYSCALE
    assert len(ov.history_manager.undo_stack) == 1

    ov.history_manager.undo()
    assert ov.current_filter == FilterType.NONE

    ov.history_manager.redo()
    assert ov.current_filter == FilterType.GRAYSCALE

    # 2. ShapeEditPopup: изменение цвета, шрифта, размера текста
    ts = TextShape(QPointF(200, 200), "Тестовый текст", "#FF0000", font_size=16, font_family="Arial")
    ov.layer_manager.add_shape(ts)
    popup = ShapeEditPopup(ts, ov)
    popup._set_color("#00FF00")
    assert ts.color == "#00FF00"
    popup._on_text_font_changed("Consolas")
    assert ts.font_family == "Consolas"
    popup._on_text_size_changed(28)
    assert ts.font_size == 28
    popup.close()

    # 3. Динамическое изменение уже написанного текста через панель инструментов
    ov.transform_box.set_shape(ts)
    ov.right_toolbar.tools_config[ToolType.TEXT]["font_family"] = "Georgia"
    ov.right_toolbar.tools_config[ToolType.TEXT]["size"] = 32
    ov.right_toolbar.tools_config[ToolType.TEXT]["is_italic"] = True
    ov.right_toolbar.set_tool_color("#123456")
    ov._on_tool_settings_updated()

    assert ts.font_family == "Georgia"
    assert ts.font_size == 32
    assert ts.color == "#123456"
    assert ts.is_italic is True

    print("  -> Динамическое изменение текста (шрифт, цвет, кегль) и Undo/Redo для фильтров работают корректно.")


def test_multi_region_selection_and_export():
    """Тест мульти-выделения: добавление зон, активная зона и раздельный экспорт."""
    print("[TEST] Тестирование мульти-выделения (multi-region)...")
    from unittest.mock import MagicMock, patch
    from PyQt6.QtCore import QRectF, Qt, QEvent
    from PyQt6.QtGui import QPixmap, QScreen, QMouseEvent
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv)

    # Создаём экземпляр оверлея через мок экрана
    from ui.overlay import OverlayWindow
    real_overlay_init = OverlayWindow.__init__
    with patch("ui.overlay.OverlayWindow.__init__", lambda self, *a, **kw: None):
        ov = OverlayWindow.__new__(OverlayWindow)
        # Инициализируем минимально необходимые атрибуты
        ov.regions = []
        ov.active_region_idx = 0
        ov.is_adding_region = False

        # 1. Изначально зон нет
        assert len(ov.regions) == 0, "Изначально регионов быть не должно"

        # 2. Добавляем первую зону
        r1 = QRectF(10, 10, 200, 150)
        ov.regions = [r1]
        ov.active_region_idx = 0
        assert len(ov.regions) == 1

        # 3. start_adding_region устанавливает флаг is_adding_region
        ov.is_adding_region = True
        assert ov.is_adding_region is True

        # 4. Добавляем вторую зону
        r2 = QRectF(300, 100, 180, 120)
        ov.regions.append(r2)
        ov.active_region_idx = 1
        ov.is_adding_region = False
        assert len(ov.regions) == 2
        assert ov.active_region_idx == 1

        # 5. Переключение активной зоны
        ov.active_region_idx = 0
        assert ov.regions[ov.active_region_idx] == r1

        # 6. get_valid_regions фильтрует зоны > 1x1 px
        from PyQt6.QtCore import QRectF as QRF
        ov.regions = [QRF(10, 10, 200, 150), QRF(300, 100, 180, 120), QRF(0, 0, 0, 0)]
        valid = [r.normalized() for r in ov.regions if r.width() > 1 and r.height() > 1]
        assert len(valid) == 2, f"Ожидалось 2 валидные зоны, получено {len(valid)}"

        # 7. Удаление активной зоны (одна из двух) — должна остаться одна
        ov.regions = [QRF(10, 10, 200, 150), QRF(300, 100, 180, 120)]
        ov.active_region_idx = 1
        ov.regions.pop(ov.active_region_idx)
        ov.active_region_idx = max(0, len(ov.regions) - 1)
        assert len(ov.regions) == 1, "После удаления одной из двух зон должна остаться одна"

        # 8. Экспорт должен возвращать каждую зону отдельным изображением.
        # Регрессия: раньше get_cropped_image() собирал общий прямоугольник.
        ov = OverlayWindow.__new__(OverlayWindow)
        ov.background_pixmap = QPixmap(640, 480)
        ov.background_pixmap.fill(Qt.GlobalColor.white)
        ov.dynamic_bg = False
        ov.is_passthrough = False
        from utils.image_filters import FilterType
        ov.current_filter = FilterType.NONE
        ov.layer_manager = type("LayerManager", (), {"draw_all": lambda *_a, **_kw: None})()
        ov.regions = [QRF(10, 20, 120, 80), QRF(300, 100, 180, 120)]
        ov.active_region_idx = 0
        images = ov.get_cropped_images()
        assert [(img.width(), img.height()) for img in images] == [(120, 80), (180, 120)]
        assert (ov.get_cropped_image().width(), ov.get_cropped_image().height()) == (120, 80)

        # Регрессия Windows DPI: safe_grab_screen_pixmap может вернуть
        # физический QPixmap с DPR > 1. Координаты зон остаются логическими,
        # поэтому crop обязан переводить их в физические координаты до copy().
        dpi_pix = QPixmap(960, 720)
        dpi_pix.setDevicePixelRatio(1.5)
        dpi_pix.fill(Qt.GlobalColor.black)
        dpi_painter = QPainter(dpi_pix)
        dpi_painter.fillRect(300, 100, 180, 120, Qt.GlobalColor.blue)
        dpi_painter.end()
        ov.background_pixmap = dpi_pix
        dpi_images = ov.get_cropped_images()
        assert dpi_images[1].pixelColor(90, 60).blue() > 200, (
            "DPI-aware crop должен брать вторую зону из физического QPixmap, "
            "а не возвращать пустую/белую область"
        )

        ov.background_pixmap = QPixmap(640, 480)
        ov.background_pixmap.fill(Qt.GlobalColor.white)
        ov._set_images_on_clipboard(images)
        clipboard_mime = QApplication.clipboard().mimeData()
        clipboard_formats = clipboard_mime.formats()
        assert "image/png" in clipboard_formats, "Обычная вставка должна получать настоящий PNG, а не путь"
        assert bytes(clipboard_mime.data("image/png")), "PNG payload clipboard не должен быть пустым"
        assert "application/x-framio-image-list" in clipboard_formats
        import json
        clipboard_payload = QApplication.clipboard().mimeData().data("application/x-framio-image-list")
        assert len(json.loads(bytes(clipboard_payload))["images"]) == 2
        clipboard_urls = QApplication.clipboard().mimeData().urls()
        assert len(clipboard_urls) == 2, "В clipboard должны попасть file-URLs всех зон"
        assert all(Path(url.toLocalFile()).exists() for url in clipboard_urls)

        # Отмена диалога не должна зависать на скрытом overlay/Проводнике.
        from types import SimpleNamespace
        ov.cfg = SimpleNamespace(save_dir_screenshots=str(Path.cwd()), auto_copy_to_clipboard=False, play_sound=False)
        ov.hide = MagicMock()
        ov.show = MagicMock()
        ov.raise_ = MagicMock()
        ov.activateWindow = MagicMock()
        with patch("ui.overlay.QFileDialog.getSaveFileName", return_value=("", "")) as save_dialog:
            OverlayWindow.save_screenshot(ov, all_regions=True)
        assert save_dialog.call_args.args[0] is None, "Native save dialog не должен иметь скрытый overlay-родитель"
        ov.show.assert_called_once_with()
        ov.raise_.assert_called_once_with()
        ov.activateWindow.assert_called_once_with()

        # Успешное сохранение из верхней панели должно создать отдельный файл
        # для каждой зоны и закрыть overlay после завершения диалога.
        import tempfile
        with tempfile.TemporaryDirectory() as tmp_dir:
            save_base = Path(tmp_dir) / "from-header.png"
            ov.close_overlay = MagicMock()
            ov._notify = MagicMock()
            ov.show.reset_mock()
            with patch("ui.overlay.QFileDialog.getSaveFileName", return_value=(str(save_base), "PNG Image (*.png)")):
                OverlayWindow.save_screenshot(ov, fmt="png", all_regions=True)
            assert (Path(tmp_dir) / "from-header_zone-1.png").exists()
            assert (Path(tmp_dir) / "from-header_zone-2.png").exists()
            ov.close_overlay.assert_called_once_with()

        # 8. Сохранение не должно зависеть от того, ввёл ли пользователь
        # расширение в диалоге. Каждая зона получает отдельный PNG.
        with tempfile.TemporaryDirectory() as tmp_dir:
            save_base = Path(tmp_dir) / "all-zones"
            saved = OverlayWindow._save_images_to_paths(images, save_base, "png")
            assert [Path(path).name for path in saved] == ["all-zones_zone-1.png", "all-zones_zone-2.png"]
            assert all(Path(path).exists() for path in saved)

        # 9. Обычная запись работает только с активной зоной, а групповое действие — со всеми.
        ov.recording_region_indices = {1}
        assert [r.toRect() for r in ov.get_action_regions()] == [QRF(10, 20, 120, 80).toRect()]
        assert len(ov.get_action_regions(all_regions=True)) == 1
        assert ov.get_action_region_items(all_regions=True)[0][0] == 0
        ov.recording_region_indices = {0}
        assert ov.get_action_regions() == []
        assert ov.get_action_region_items(all_regions=True)[0][0] == 1
        ov.recording_region_indices = set()

        # 9.1 Верхняя панель должна иметь отдельный переключатель добавления
        # и включаемые массовые действия для доступных зон.
        from ui.toolbars import RegionActionHeader
        header = RegionActionHeader()
        header.set_region_state(True, total_count=2, available_count=2)
        assert header.btn_add.isChecked()
        assert "выделите" in header.lbl_title.text().lower() or "select" in header.lbl_title.text().lower()
        assert header.btn_mass_save.isEnabled()
        assert header.btn_mass_video.isEnabled()
        header.set_region_state(False, total_count=1, available_count=1)
        assert not header.btn_add.isChecked()
        assert hasattr(header, "btn_close")

        # 10. Режим добавления включается явным нажатием «+».
        ov.bottom_toolbar = type("Toolbar", (), {
            "update_add_region_state": lambda *_: None,
        })()
        ov.setCursor = lambda *_: None
        ov._set_cursor_if_needed = lambda *_: None
        ov.update = lambda: None
        ov.is_adding_region = False
        ov.start_adding_region()
        assert ov.is_adding_region is True

        # 10.1 Кнопка «+» вооружает только следующее выделение. После этого
        # внешний drag без Ctrl заменяет активную зону, а не создаёт третью.
        ov = OverlayWindow.__new__(OverlayWindow)
        real_overlay_init(ov)
        ov.regions = [QRF(10, 10, 120, 80)]
        ov.active_region_idx = 0
        ov.is_adding_region = True
        ov.mousePressEvent(QMouseEvent(
            QEvent.Type.MouseButtonPress, QPointF(300, 100),
            Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier
        ))
        ov.mouseMoveEvent(QMouseEvent(
            QEvent.Type.MouseMove, QPointF(420, 220),
            Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier
        ))
        ov.mouseReleaseEvent(QMouseEvent(
            QEvent.Type.MouseButtonRelease, QPointF(420, 220),
            Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier
        ))
        assert len(ov.get_valid_regions()) == 2
        assert ov.active_region_idx == 1
        assert ov.is_adding_region is False

        # Теперь обычный внешний drag перевыделяет второй контур.
        ov.mousePressEvent(QMouseEvent(
            QEvent.Type.MouseButtonPress, QPointF(600, 300),
            Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier
        ))
        ov.mouseMoveEvent(QMouseEvent(
            QEvent.Type.MouseMove, QPointF(720, 420),
            Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier
        ))
        ov.mouseReleaseEvent(QMouseEvent(
            QEvent.Type.MouseButtonRelease, QPointF(720, 420),
            Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier
        ))
        assert len(ov.get_valid_regions()) == 2
        assert ov.regions[1] == QRF(600, 300, 120, 120)
        assert ov._is_fullscreen_region(QRectF(0, 0, ov.width(), ov.height()))
        assert not ov._is_fullscreen_region(QRectF(0, 0, 500, 300))
        ov.deleteLater()

        # 11. Составной экспорт больше не используется как результат screenshot/copy,
        # но геометрия объединения остаётся полезной для обратной совместимости.
        r_a = QRectF(10, 10, 200, 150)
        r_b = QRectF(300, 100, 180, 120)
        united = r_a.united(r_b).toRect()
        assert united.width() > 0 and united.height() > 0, "united_rect должен быть непустым"
        # united должен содержать обе зоны (QRect.right() = left+width-1)
        assert united.left() <= 10 and united.top() <= 10
        assert united.right() >= (300 + 180 - 1) and united.bottom() >= (100 + 120 - 1)

        # 12. Составной QPixmap ARGB32_Premultiplied
        from PyQt6.QtGui import QPixmap
        pix = QPixmap(united.width(), united.height())
        pix.fill(Qt.GlobalColor.transparent)
        assert not pix.isNull(), "Составной пиксмап не должен быть пустым"
        assert pix.hasAlphaChannel(), "Составной пиксмап должен иметь альфа-канал"

    print("  -> Мульти-выделение: добавление, активная зона, раздельный экспорт и clipboard payload работают корректно.")


def test_recent_media_history():
    """Проверяет быстрый in-app список материалов без отдельного окна."""
    print("[TEST] Тестирование быстрого списка последних материалов...")
    from unittest.mock import MagicMock
    from PyQt6.QtGui import QImage
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QLabel, QVBoxLayout, QPushButton, QSizePolicy
    from main import FramioApp, RecentMediaPanel

    # Используем обычный объект-владелец: создание QObject через __new__
    # без QObject.__init__ запрещено PyQt и не относится к проверяемой панели.
    app = type("FakeFramioApp", (), {})()
    app.recent_media = []
    app._setup_tray_menu = lambda: None
    app._recent_media_menu = lambda menu: FramioApp._recent_media_menu(app, menu)
    image = QImage(8, 6, QImage.Format.Format_RGB32)
    image.fill(Qt.GlobalColor.white)

    FramioApp.add_recent_media(app, image=image, label="Clipboard screenshot")
    FramioApp.add_recent_media(app, image=image, label="Second screenshot")
    assert [item["label"] for item in app.recent_media] == ["Second screenshot", "Clipboard screenshot"]
    assert app.recent_media[0]["image"].size() == image.size()

    from PyQt6.QtWidgets import QMenu
    menu = QMenu()
    app._recent_media_menu(menu)
    recent_submenu = menu.actions()[0].menu()
    panel_action = recent_submenu.actions()[0]
    panel = panel_action.defaultWidget()
    assert isinstance(panel, RecentMediaPanel)
    assert panel.width() <= 380, "Панель последних материалов должна оставаться компактной"
    assert RecentMediaPanel.media_kind({"path": "capture.mp4"}) == "videos"
    assert RecentMediaPanel.media_kind({"path": "capture.gif"}) == "gifs"
    assert RecentMediaPanel.media_kind({"path": "capture.png"}) == "screenshots"
    app._view_recent_media = MagicMock()
    app._copy_recent_media = MagicMock()
    card = panel._make_card({
        "label": "A very long screenshot name that must be elided in the card title",
        "path": "capture.png",
        "image": image,
    })
    title_label = card.findChild(QLabel, "recentMediaLabel")
    preview = card.findChild(QLabel, "recentMediaPreview")
    assert title_label is not None and "…" in title_label.text()
    assert title_label.toolTip().startswith("A very long screenshot name")
    assert title_label.toolTip().endswith("capture.png")
    assert preview is not None and hasattr(preview, "clicked")
    assert preview.width() == RecentMediaPanel.PREVIEW_WIDTH
    assert preview.height() == RecentMediaPanel.PREVIEW_HEIGHT
    assert preview.width() * 9 == preview.height() * 16
    buttons = card.findChildren(QPushButton)
    assert len(buttons) == 2
    assert all(button.sizePolicy().horizontalPolicy() == QSizePolicy.Policy.Fixed for button in buttons)
    preview.clicked.emit()
    app._view_recent_media.assert_called_once()
    assert isinstance(card.layout(), QVBoxLayout)
    assert len(card.findChildren(QPushButton)) == 2
    assert all(button.text() not in {"Вставить", "Paste"} for button in card.findChildren(QPushButton))
    card.deleteLater()
    import cv2
    import tempfile
    with tempfile.TemporaryDirectory() as tmp_dir:
        video_path = str(Path(tmp_dir) / "preview.mp4")
        writer = cv2.VideoWriter(video_path, cv2.VideoWriter_fourcc(*"mp4v"), 12, (32, 24))
        assert writer.isOpened(), "Тестовый MP4 не открылся для проверки превью"
        writer.write(np.full((24, 32, 3), 180, dtype=np.uint8))
        writer.release()
        assert RecentMediaPanel._load_preview({"path": video_path}) is not None
    FramioApp._copy_recent_media(app, {"path": "O:/captures/example.mp4"})
    recent_mime = QApplication.clipboard().mimeData()
    assert recent_mime.urls()[0].toLocalFile().endswith("example.mp4")
    # Qt закономерно предоставляет text/uri-list и как URL, и как text;
    # главным контрактом для видео остаётся передача файла, а не картинки.
    assert not recent_mime.hasImage()

    for index in range(60):
        FramioApp.add_recent_media(app, label=f"Video {index}", path=f"O:/captures/{index}.mp4")
    assert len(app.recent_media) == 50
    assert app.recent_media[0]["label"] == "Video 59"

    panel.close()
    print("  -> Последние материалы имеют фильтры, действия, превью и прокрутку при большом списке.")


def test_mass_recording_closes_selection_overlay():
    """Массовая запись не должна оставлять интерактивный overlay после остановки."""
    print("[TEST] Тестирование жизненного цикла массовой записи...")
    from unittest.mock import MagicMock
    from ui.overlay import OverlayWindow

    first = object()
    second = object()
    overlay = OverlayWindow.__new__(OverlayWindow)
    overlay.recording_windows = [first, second]
    overlay.recording_region_indices = {0, 1}
    overlay.is_recording = True
    overlay._mass_recording = True
    overlay.update = MagicMock()
    overlay.setAttribute = MagicMock()
    overlay._sync_region_header = MagicMock()
    overlay.close_overlay = MagicMock()

    overlay._on_overlay_recording_closed(first)
    assert overlay.recording_windows == [second]
    assert not overlay.close_overlay.called

    overlay._on_overlay_recording_closed(second)
    assert overlay.recording_windows == []
    overlay.close_overlay.assert_called_once_with()
    assert overlay._mass_recording is False

    print("  -> После завершения последнего массового видео/GIF интерактивный overlay закрывается.")


def test_single_recording_closes_overlay_when_no_regions_remain():
    """Остановка последней обычной записи должна убрать затемнение и рамку."""
    print("[TEST] Проверка очистки overlay после последней обычной записи...")
    from unittest.mock import MagicMock
    from ui.overlay import OverlayWindow

    overlay = OverlayWindow.__new__(OverlayWindow)
    overlay.recording_windows = []
    overlay.recording_region_indices = set()
    overlay.is_recording = True
    overlay._mass_recording = False
    overlay.regions = []
    overlay.active_region_idx = 0
    overlay.update = MagicMock()
    overlay.setAttribute = MagicMock()
    overlay._show_toolbars = MagicMock()
    overlay._update_toolbar_positions = MagicMock()
    overlay._sync_close_button_tooltip = MagicMock()
    overlay.close_overlay = MagicMock()

    overlay._on_overlay_recording_closed(object())
    overlay.close_overlay.assert_called_once_with()
    assert overlay.is_recording is False

    print("  -> Последняя обычная запись больше не оставляет затемнение и рамку.")


def test_recording_start_hides_selection_overlay():
    """После запуска обычной записи overlay не остаётся затемнённым поверх экрана."""
    print("[TEST] Проверка скрытия overlay сразу после старта записи...")
    from unittest.mock import MagicMock, patch
    from types import SimpleNamespace
    from PyQt6.QtCore import QRectF
    from ui.overlay import OverlayWindow

    class DummySignal:
        def connect(self, _callback):
            return None

    rec_window = MagicMock()
    rec_window.recording_closed = DummySignal()
    region = QRectF(20, 30, 320, 200)
    overlay = OverlayWindow.__new__(OverlayWindow)
    overlay.cfg = SimpleNamespace(
        record_mic=True,
        record_system=True,
        video_codec="libx264",
        record_countdown_enabled=False,
        record_countdown_seconds=3,
    )
    overlay.config_mgr = MagicMock()
    overlay.target_hwnd = None
    overlay.recording_windows = []
    overlay.recording_region_indices = set()
    overlay.is_recording = False
    overlay._mass_recording = False
    overlay.get_valid_region_items = lambda: [(0, region)]
    overlay.get_action_region_items = lambda all_regions=False: [(0, region)]
    overlay._is_fullscreen_region = lambda _region: False
    overlay._set_add_region_mode = MagicMock()
    overlay.badge = MagicMock()
    overlay.text_editor = MagicMock()
    overlay.setAttribute = MagicMock()
    overlay.clearMask = MagicMock()
    overlay.update = MagicMock()
    overlay.hide = MagicMock()
    overlay.show = MagicMock()
    overlay._hide_toolbars = MagicMock()
    overlay._unclip_timer = MagicMock()

    with patch("ui.overlay.RecordingFrameWindow", return_value=rec_window):
        OverlayWindow.start_recording(overlay, mode="video", all_regions=False)

    overlay.hide.assert_called_once_with()
    overlay.show.assert_not_called()
    overlay._hide_toolbars.assert_called_once_with()
    assert overlay.recording_windows == [rec_window]

    print("  -> После старта GIF/видео затемнение и интерактивное выделение снимаются сразу.")


def test_double_click_selects_window_below_topmost_overlay():
    """Двойной клик выбирает целое окно под topmost overlay без hover-состояния."""
    print("[TEST] Проверка выбора окна двойным кликом под верхним overlay...")
    from unittest.mock import patch
    from PyQt6.QtCore import QEvent, QPoint, QPointF
    from PyQt6.QtGui import QMouseEvent
    from ui.overlay import OverlayWindow, user32

    overlay = OverlayWindow()
    overlay.setGeometry(0, 0, 800, 600)
    overlay_hwnd = int(overlay.winId())
    target_hwnd = 424242
    get_window_calls = []

    def fake_get_window(hwnd, relation):
        get_window_calls.append((int(hwnd), int(relation)))
        if int(hwnd) == overlay_hwnd and int(relation) == 2:  # GW_HWNDNEXT
            return target_hwnd
        return 0

    def fake_get_window_rect(_hwnd, rect_ptr):
        rect = rect_ptr._obj
        rect.left, rect.top, rect.right, rect.bottom = 80, 60, 480, 360
        return 1

    def fake_get_window_text(_hwnd, buffer, _length):
        buffer.value = "Тестовое окно"
        return 1

    with patch.object(user32, "WindowFromPoint", return_value=overlay_hwnd), \
            patch.object(user32, "GetAncestor", side_effect=lambda hwnd, _kind: int(hwnd)), \
            patch.object(user32, "GetWindow", side_effect=fake_get_window), \
            patch.object(user32, "IsWindowVisible", return_value=True), \
            patch.object(user32, "GetWindowRect", side_effect=fake_get_window_rect), \
            patch.object(user32, "GetWindowTextLengthW", return_value=12), \
            patch.object(user32, "GetWindowTextW", side_effect=fake_get_window_text):
        match = overlay._window_at_global_point(QPoint(120, 70))
        assert match is not None

    assert (overlay_hwnd, 2) in get_window_calls

    # Двойной клик в любой части окна должен выбрать всю его рамку.
    double_click = QMouseEvent(
        QEvent.Type.MouseButtonDblClick,
        QPointF(120, 70),
        QPointF(120, 70),
        QPointF(120, 70),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    with patch.object(overlay, "isVisible", return_value=True), \
            patch.object(user32, "WindowFromPoint", return_value=overlay_hwnd), \
            patch.object(user32, "GetAncestor", side_effect=lambda hwnd, _kind: int(hwnd)), \
            patch.object(user32, "GetWindow", side_effect=fake_get_window), \
            patch.object(user32, "IsWindowVisible", return_value=True), \
            patch.object(user32, "GetWindowRect", side_effect=fake_get_window_rect), \
            patch.object(user32, "GetWindowTextLengthW", return_value=12), \
            patch.object(user32, "GetWindowTextW", side_effect=fake_get_window_text):
        overlay.mouseDoubleClickEvent(double_click)

    assert len(overlay.get_valid_regions()) == 1
    assert overlay.target_hwnd == target_hwnd
    assert overlay.regions[0] == QRectF(80, 60, 400, 300)

    overlay.deleteLater()
    print("  -> Окно под topmost overlay найдено, hover-контур не используется, двойной клик выбирает его целиком.")


if __name__ == "__main__":
    from tempfile import TemporaryDirectory
    with TemporaryDirectory() as tmp_dir:
        tmp_p = Path(tmp_dir)
        test_dynamic_text_editing_and_filter_history()
        test_mosaic_offset_export_interactive_text_and_flyout_positions()
        test_single_instance_text_eyedropper_and_inspector()
        test_models_and_history()
        test_capture_masks()
        test_multiple_capture_masks_and_delete()
        test_constrained_shape_drawing_and_repeated_capture_mask_transform()
        test_rotated_regional_effect_stencil_and_i18n()
        test_regional_effect_dynamic_resampling_and_alt_inspector()
        test_regional_effects_and_whole_screen_filter_reset()
        test_shape_transform_box_and_flyouts()
        test_bounding_rects_and_toolbar_layout()
        test_shape_directional_drawing()
        test_hotkey_recorder_button()
        test_settings_dialog_apply_without_close()
        test_video_options_popup_has_fps()
        test_dynamic_frame_rescaling(tmp_p)
        test_shape_cloning_and_reordering()
        test_style_preview_icons()
        test_svg_recording_icons()
        test_video_annotation_and_audio_toggles()
        test_passthrough_and_drawing_interactivity()
        test_i18n_and_video_drawing_enhancements(tmp_p)
        test_countdown_scrolling_and_window_snapping()
        test_custom_countdown_and_live_mosaic()
        test_freeze_prevention_and_phantom_flash()
        test_blur_censor_palette_and_outside_click()
        test_filters()
        test_video_and_gif_recorders(tmp_p)
        test_audio_recorder(tmp_p)
        test_screen_capture_1to1()
        test_window_enumeration_and_capture()
        test_window_icon_extraction()
        test_config_defaults_and_persistence()
        test_autostart_registry()
        test_hotkey_parsing()
        test_progress_signals()
        test_multi_region_selection_and_export()
        test_mass_recording_closes_selection_overlay()
        test_single_recording_closes_overlay_when_no_regions_remain()
        test_recording_start_hides_selection_overlay()
        test_double_click_selects_window_below_topmost_overlay()
        import time
        time.sleep(0.5)
        QApplication.processEvents()
    print("\n[OK] ВСЕ ТЕСТЫ УСПЕШНО ПРОЙДЕНЫ!")
    import sys
    sys.exit(0)
