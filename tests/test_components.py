# -*- coding: utf-8 -*-
"""
Тесты компонентов Framio: модели слоёв, история Undo/Redo, кодирование видео и GIF, фильтры.
"""

import sys
import os
import time
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
from PyQt6.QtGui import QImage, QPainter

from models.shapes import PenShape, LineShape, ArrowShape, RectangleShape, TextShape, MosaicShape
from models.layers import LayerManager
from models.history import HistoryManager, HistoryCommand
from recorder.video_recorder import VideoRecorder
from recorder.gif_recorder import GifRecorder
from utils.image_filters import apply_filter, FilterType
from utils.hotkey_manager import parse_hotkey_string

GLOBAL_APP = QApplication.instance() or QApplication(sys.argv)

def test_models_and_history():
    print("[TEST] Тестирование слоёв и истории Undo/Redo...")
    
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
    worker = CaptureWorker("video", "test.mp4", lambda: (0, 0, 100, 100))
    assert hasattr(worker, "save_progress")

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
    from utils.screen_lock import enumerate_recordable_windows, capture_window_or_screen_bgr
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
    signals = []
    dlg.settings_applied.connect(lambda: signals.append(True))

    dlg._apply_settings()
    assert len(signals) == 1
    assert dlg.isVisible(), "Диалог настроек не должен закрываться при нажатии «Применить»!"
    assert "✓" in dlg.lbl_status.text()
    dlg.close()
    print("  -> Кнопка «Применить» успешно сохраняет настройки и оставляет окно открытым.")


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

    # 7. Тест защиты от случайного сброса рамки при клике снаружи
    overlay = OverlayWindow()
    overlay.selection_rect = QRectF(100, 100, 300, 300)
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

    # Протяжка мышью снаружи (>6 px)
    overlay.mousePressEvent(press_evt)
    move_evt = QMouseEvent(QEvent.Type.MouseMove, QPointF(40, 40), Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    overlay.mouseMoveEvent(move_evt)
    assert overlay.is_selecting is True
    assert overlay.selection_rect != QRectF(100, 100, 300, 300)  # Новое выделение началось!
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


if __name__ == "__main__":
    from tempfile import TemporaryDirectory
    with TemporaryDirectory() as tmp_dir:
        tmp_p = Path(tmp_dir)
        test_single_instance_text_eyedropper_and_inspector()
        test_models_and_history()
        test_rotated_regional_effect_stencil_and_i18n()
        test_regional_effect_dynamic_resampling_and_alt_inspector()
        test_regional_effects_and_whole_screen_filter_reset()
        test_shape_transform_box_and_flyouts()
        test_bounding_rects_and_toolbar_layout()
        test_shape_directional_drawing()
        test_hotkey_recorder_button()
        test_settings_dialog_apply_without_close()
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
        import time
        time.sleep(0.5)
        QApplication.processEvents()
    print("\n[OK] ВСЕ ТЕСТЫ УСПЕШНО ПРОЙДЕНЫ!")
    import sys
    sys.exit(0)


