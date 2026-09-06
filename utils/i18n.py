# -*- coding: utf-8 -*-
"""
Модуль интернационализации (i18n) Framio.
Поддерживает автоматическое определение языка системы (русский по умолчанию для ru/uk/be систем,
английский для остальных), ручное переключение (Авто / Русский / English)
и словарь переводов для всех элементов интерфейса, уведомлений и справки.
"""

import sys
import ctypes
from PyQt6.QtCore import QLocale

# Языковые словари
TRANSLATIONS = {
    "ru": {
        # --- Системный трей и главное меню ---
        "app_title": "Framio — Скриншоты и запись",
        "tray_ready": "Готов к работе",
        "tray_recording_active": "Framio — Идёт запись: {count} активн. (видео/GIF)",
        "tray_processing": "Framio — Обработка ({count} шт.):",
        "tray_hotkey_capture": "• Захват области: {key}",
        "tray_hotkey_fullscreen": "• Запись экрана: {key}",
        "tray_hotkey_stop": "• Остановка записи: {key}",
        "tray_hotkey_quick_screen": "• Быстрый полный скриншот: {key}",
        "tray_menu_capture": "Сделать скриншот ({key})",
        "tray_menu_quick_fullscreen": "Быстрый скриншот экрана ({key})",
        "tray_menu_rec_fullscreen": "Записать весь экран ({key})",
        "tray_menu_rec_video": "Записать видео области MP4",
        "tray_menu_rec_gif": "Записать GIF области",
        "tray_menu_status_rec": "Идёт запись ({count} активн.)",
        "tray_menu_stop_all": "Остановить запись ({key})",
        "tray_menu_folder_screens": "Папка со скриншотами",
        "tray_menu_folder_videos": "Папка с видео",
        "tray_menu_folder_gifs": "Папка с GIF",
        "tray_menu_settings": "Настройки",
        "tray_menu_help": "Справка и инструкция",
        "tray_menu_exit": "Выход",

        # --- Уведомления ---
        "notif_screen_saved_title": "Скриншот сохранен",
        "notif_screen_saved_body": "Файл: {filename}\nПапка: {folder}\nНажмите сюда, чтобы открыть файл в проводнике",
        "notif_quick_screen_saved_title": "Быстрый скриншот экрана",
        "notif_quick_screen_saved_body": "Весь экран сохранен в {filename}\nНажмите сюда, чтобы открыть файл",
        "notif_video_saved_title": "Видео успешно сохранено",
        "notif_video_saved_body": "Файл: {filename}\nПапка: {folder}\nНажмите сюда, чтобы открыть файл в проводнике",
        "notif_gif_saved_title": "GIF успешно сохранена",
        "notif_gif_saved_body": "Файл: {filename}\nПапка: {folder}\nНажмите сюда, чтобы открыть файл в проводнике",
        "notif_rec_saving_video": "Сохранение видео...",
        "notif_rec_saving_gif": "Сохранение GIF...",
        "notif_rec_saving_body": "Идёт оптимизация и кодирование в высоком качестве (в фоне)...",
        "notif_rec_cancelled_title": "Запись отменена",
        "notif_rec_cancelled_body": "Запись экрана отменена без сохранения.",
        "notif_rec_started_title": "Запись всего экрана запущена",
        "notif_rec_started_body": "Запись экрана началась!\nДля остановки нажмите: {key}",
        "notif_clipboard_copied": "Скопировано в буфер обмена",

        # --- Окно записи (RecordingFrameWindow) ---
        "rec_mode_video": "REC MP4",
        "rec_mode_gif": "REC GIF",
        "rec_mode_pause": "ПАУЗА",
        "rec_drag_hint": "Зажмите шапку левой кнопкой мыши для перемещения рамки по экрану",
        "rec_target_screen": "Записывается вся область экрана под рамкой",
        "rec_target_window": "Записывается отдельное окно: {title}",
        "rec_mic_on": "Микрофон включен (клик для отключения)",
        "rec_mic_off": "Микрофон выключен (клик для включения)",
        "rec_system_on": "Системный звук включен (клик для отключения)",
        "rec_system_off": "Системный звук выключен (клик для включения)",
        "rec_timer_tip": "Текущая длительность записи и количество захваченных кадров",
        "rec_size_tip": "Текущие размеры области записи в пикселях",
        "rec_draw_btn_tip": "Панель инструментов живого рисования и заметок [Ctrl+Z отмена]",
        "rec_pause_tip": "Приостановить запись [Пробел]",
        "rec_resume_tip": "Возобновить запись [Пробел]",
        "rec_stop_tip": "Завершить запись и сохранить в файл [Enter / Space]",
        "rec_lock_tip": "Зафиксировать рамку от случайного изменения размера",
        "rec_unlock_tip": "Разблокировать рамку для изменения размера",
        "rec_settings_tip": "Параметры записи (выбор приложения / отдельного окна)",
        "rec_cancel_tip": "Отменить запись без сохранения и удалить файл [Esc]",
        "rec_source_label": "Источник захвата",
        "rec_source_all_windows": "Весь экран / Все окна",
        "rec_source_all_desc": "Записывать всю область экрана под рамкой со всеми окнами",
        "rec_refresh_windows": "Обновить список окон",
        "rec_pick_window_btn": "Выбрать окно кликом мыши",
        "rec_snap_window_tip": "Нажмите на окно для выбора и прилипания рамки",
        "rec_window_hover_badge": "{title} (Кликните для привязки)",
        "rec_mode_countdown": "ТАЙМЕР",
        "rec_mode_countdown_prefix": "СТАРТ: {sec}с",
        "rec_countdown_status": "Старт через {sec}с...",
        "rec_countdown_hint": "Приготовьтесь... (Esc для отмены)",

        # --- Длинный скриншот (Scrolling Screenshot) ---
        "action_scroll": "Длинный скриншот с автопрокруткой [S]",
        "scroll_hud_title": "Длинный скриншот",
        "scroll_hud_status": "Кадров: {frames} | Высота: {height} px",
        "scroll_hud_done": "Завершить (Enter)",
        "scroll_hud_autoscroll": "Авто-скролл",
        "scroll_hud_pause": "Пауза",
        "scroll_hud_resume": "Продолжить",
        "scroll_hud_cancel": "Отмена",
        "scroll_hud_snap": "Сделать кадр",
        "scroll_hud_hint": "Прокручивайте страницу колёсиком мыши (или используйте Авто-скролл). Кадры склеиваются автоматически!",
        "scroll_copied_toast": "Длинный скриншот скопирован в буфер обмена!",
        "scroll_saved_toast": "Длинный скриншот сохранён: {path}",

        # --- Панель живого рисования видео (RecordingDrawingToolbar) ---
        "draw_cursor": "Курсор (Взаимодействие с экраном / клики сквозь рамку)",
        "draw_pen": "Карандаш (свободное рисование)",
        "draw_arrow": "Стрелка с усиками",
        "draw_rect": "Прямоугольник",
        "draw_mosaic": "Мозаика (Цензура размытием)",
        "draw_highlighter": "Маркер-хайлайтер (полупрозрачный)",
        "draw_text": "Текст с фоном",
        "draw_undo": "Отменить последнее действие (Ctrl+Z)",
        "draw_clear": "Очистить все нарисованные фигуры",
        "draw_color": "Выбрать цвет и толщину рисования",
        "draw_layers": "Слои фигур (управление и видимость)",
        "draw_pin_on": "Рисунки закреплены на экране (не смещаются при движении рамки)",
        "draw_pin_off": "Рисунки привязаны к рамке (смещаются вместе с ней)",

        # --- Скриншотер и оверлей (OverlayWindow & Toolbars) ---
        "tool_move": "Перемещение и выделение (V)",
        "tool_pen": "Карандаш (P)",
        "tool_line": "Прямая линия (L)",
        "tool_arrow": "Стрелка с зазубринами (A)",
        "tool_rect": "Прямоугольник (R)",
        "tool_circle": "Эллипс / Окружность (C)",
        "tool_highlighter": "Маркер-выделитель (H)",
        "tool_text": "Текст (T)",
        "tool_step": "Нумератор шагов (1, 2, 3...) (S)",
        "tool_mosaic": "Цензура / Размытие (M)",
        "tool_undo": "Отменить последнее действие (Ctrl+Z)",
        "tool_redo": "Повторить отменённое действие (Ctrl+Y)",
        "tool_clear": "Очистить все фигуры",
        "action_save": "Сохранить скриншот на диск (Ctrl+S)",
        "action_copy": "Копировать в буфер обмена (Ctrl+C)",
        "action_search": "Поиск картинки в Google",
        "action_close": "Закрыть выделение (Esc)",
        "action_dynamic_bg": "Динамический фон (живое видео под рамкой)",
        "action_passthrough": "Неосязаемая рамка: клики сквозь выделение в фоновые окна",

        # --- Контекстное меню фигур и слои ---
        "shape_menu_props": "Изменить свойства ({name})...",
        "shape_menu_dup": "Дублировать (Ctrl+D)",
        "shape_menu_order": "Порядок слоёв",
        "shape_menu_front": "На передний план",
        "shape_menu_up": "Переместить выше",
        "shape_menu_down": "Переместить ниже",
        "shape_menu_back": "На задний план",
        "shape_menu_delete": "Удалить фигуру (Del)",
        "layers_dialog_title": "Слои фигур",
        "layers_visible_tip": "Вкл/Выкл видимость слоя",
        "layers_up_tip": "Переместить выше",
        "layers_down_tip": "Переместить ниже",
        "layers_del_tip": "Удалить слой",

        # --- Всплывающее окно редактирования фигуры (ShapeEditPopup) ---
        "shape_edit_title": "Свойства фигуры: {name}",
        "shape_edit_color": "Цвет:",
        "shape_edit_width": "Толщина: {val} px",
        "shape_edit_opacity": "Непрозрачность: {val}%",
        "shape_edit_style": "Стиль стрелки:",
        "shape_edit_custom_color": "Другой цвет...",
        "shape_edit_del": "Удалить",
        "shape_edit_done": "Готово",

        # --- Палитра цветов (ColorPalettePopup) ---
        "palette_title": "Выбор цвета и пера",
        "palette_thickness": "Толщина линии: {val} px",
        "palette_custom_tip": "Выбрать произвольный цвет из расширенной палитры Windows",

        # --- Диалог настроек (SettingsDialog) ---
        "settings_title": "Настройки Framio",
        "settings_tab_storage": "Папки сохранения",
        "settings_tab_media": "Запись (Видео, GIF, Звук)",
        "settings_tab_hotkeys": "Горячие клавиши",
        "settings_tab_general": "Общие и снимки",
        "settings_tab_help": "Справка и инструкция",

        "settings_lang_label": "Язык интерфейса / Language:",
        "settings_lang_auto": "Авто (Системный)",
        "settings_lang_ru": "Русский",
        "settings_lang_en": "English",

        "settings_btn_apply": "Применить",
        "settings_btn_close": "Закрыть",
        "settings_btn_record": "Назначить",
        "settings_btn_recording": "Нажмите...",
        "settings_btn_browse": "Обзор...",
        "settings_btn_reset": "Сбросить",

        "settings_storage_group": "Расположение сохраняемых файлов",
        "settings_screenshots_dir": "Папка скриншотов:",
        "settings_videos_dir": "Папка видео (MP4):",
        "settings_gifs_dir": "Папка анимаций (GIF):",
        "settings_portable_badge": "Режим: {mode} • {path}",

        "settings_video_group": "Параметры видеозаписи (MP4)",
        "settings_video_fps": "Частота кадров (FPS):",
        "settings_video_codec": "Видеокодек:",
        "settings_compress_video": "Оптимизировать размер MP4 после записи (H.264 CRF)",

        "settings_gif_group": "Параметры GIF-анимаций",
        "settings_gif_fps": "Частота кадров GIF (FPS):",
        "settings_gif_colors": "Палитра цветов GIF:",
        "settings_gif_dither": "Алгоритм дизеринга (сглаживания цветов):",
        "settings_compress_gif": "Оптимизировать размер GIF (FFmpeg PaletteGen)",

        "settings_audio_group": "Запись звука",
        "settings_record_mic": "Записывать микрофон по умолчанию",
        "settings_record_system": "Записывать системный звук (игры, видео, браузер WASAPI loopback)",

        "settings_countdown_group": "Таймер перед началом записи",
        "settings_countdown_enable": "Включить обратный отсчёт перед началом записи",
        "settings_countdown_delay": "Задержка таймера:",

        "settings_hotkeys_group": "Глобальные комбинации клавиш",
        "settings_hk_capture": "Захват области экрана (скриншот):",
        "settings_hk_quick_screen": "Быстрый скриншот всего экрана сразу в папку:",
        "settings_hk_rec_fs": "Запись всего экрана (видео):",
        "settings_hk_stop_rec": "Остановка активной записи экрана:",

        "settings_general_group": "Поведение приложения",
        "settings_auto_copy": "Автоматически копировать скриншот в буфер обмена",
        "settings_auto_copy_desc": "Сразу помещать изображение в буфер обмена после выделения",
        "settings_play_sound": "Воспроизводить звуки затвора и уведомлений",
        "settings_play_sound_desc": "Звуковой щелчок затвора при снимке экрана",
        "settings_open_folder": "Открывать папку с файлом после сохранения",
        "settings_open_folder_desc": "Показывать созданный файл в проводнике Windows",
        "settings_save_on_search": "Сохранять скриншот при поиске по картинке",
        "settings_save_on_search_desc": "Автоматически сохранять файл на диск при отправке в Яндекс / Google",
        "settings_autostart": "Запускать Framio вместе с Windows (в трей)",
        "settings_autostart_desc": "Автоматически запускать свернутым в трей при входе в систему",

        "settings_screenshots_group": "Скриншоты и буфер обмена",
        "settings_save_format": "Формат скриншотов по умолчанию:",
        "settings_copy_format": "Формат копирования в буфер обмена:",
        "settings_copy_format_dib": "DIB / Растровый (Универсально для мессенджеров)",
        "settings_copy_format_png": "PNG (С сохранением прозрачности)",
        "settings_copy_format_jpg": "JPEG (Компактный размер)",
        "settings_copy_format_data_uri": "Data URI (Base64 текст)",

        "settings_annotations_group": "Аннотирование по умолчанию",
        "settings_default_color": "Основной цвет инструментов:",
        "settings_default_stroke": "Толщина линий:",
        "settings_highlighter_alpha": "Прозрачность маркера:",

        # --- Справка и инструкция (Markdown / Rich Text) ---
        "help_title": "Руководство пользователя Framio",
        "help_content": """
<h2>Framio — Руководство пользователя</h2>
<p>Framio — это сверхбыстрый, легковесный и функциональный инструмент для создания скриншотов, аннотирования и плавной записи видео и GIF со звуком для Windows (60+ FPS).</p>

<hr/>

<h3>1. Создание скриншотов</h3>
<ul>
  <li><b>Захват области:</b> Нажмите <code>{hk_capture}</code> или кликните по иконке в трее. Зажмите левую кнопку мыши и выделите нужную область экрана.</li>
  <li><b>Быстрый скриншот всего экрана:</b> Нажмите <code>{hk_quick}</code>. Снимок всех мониторов мгновенно сохранится в папку со скриншотами и скопируется в буфер обмена — без открытия рамок и лишних кликов!</li>
  <li><b>Точная подгонка:</b> Потяните за маркеры по краям рамки для изменения размера или зажмите центр (при активном инструменте «Перемещение») для сдвига выделения.</li>
  <li><b>Быстрые действия:</b> <code>Ctrl+C</code> — копировать в буфер обмена, <code>Ctrl+S</code> — сохранить в файл, <code>Esc</code> — закрыть.</li>
</ul>

<hr/>

<h3>2. Инструменты аннотирования</h3>
<ul>
  <li><b>Карандаш (P)</b> — свободное рисование гладких векторных линий.</li>
  <li><b>Стрелка с зазубринами (A)</b> — аккуратные направляющие стрелки с заострёнными усиками.</li>
  <li><b>Прямоугольник (R) и Эллипс (C)</b> — геометрические фигуры для выделения областей.</li>
  <li><b>Маркер-хайлайтер (H)</b> — полупрозрачное выделение текста и ключевых участков.</li>
  <li><b>Текст (T)</b> — надписи с контрастным тёмным фоном для идеальной читаемости на любом фоне.</li>
  <li><b>Нумератор шагов (S)</b> — цветные кружки с цифрами (1, 2, 3...) для пошаговых инструкций.</li>
  <li><b>Мозаика / Размытие (M)</b> — сокрытие паролей, лиц и конфиденциальных данных.</li>
</ul>

<hr/>

<h3>3. Интерактивное управление фигурами</h3>
<ul>
  <li><b>Перемещение фигур:</b> Наведите курсор на любую нарисованную фигуру, <b>зажмите правую кнопку мыши (ПКМ)</b> и перетащите фигуру в любое место!</li>
  <li><b>Управление и свойства:</b> Кликните <b>ПКМ по фигуре</b> (или задержите на 0.35 сек), чтобы открыть контекстное меню: изменение цвета, толщины линии, прозрачности, стиля стрелки, дублирование (<code>Ctrl+D</code>) или удаление (<code>Del</code>).</li>
  <li><b>Панель слоёв:</b> Кнопка «Слои» позволяет переключать видимость элементов, поднимать их выше или опускать ниже других фигур.</li>
</ul>

<hr/>

<h3>4. Запись видео (MP4) и анимаций (GIF)</h3>
<ul>
  <li><b>Старт записи:</b> Нажмите <code>{hk_rec_fs}</code> для записи всего экрана или выберите «Записать видео/GIF» в нижней панели выделения.</li>
  <li><b>Динамическое масштабирование:</b> Рамку записи можно свободно перемещать по экрану и изменять её размер во время записи без чёрных полос.</li>
  <li><b>Изолированный захват окна:</b> Нажмите на иконку шестерёнки в шапке записи и выберите конкретное приложение. Записываться будет только оно, даже если поверх него открыты другие окна!</li>
  <li><b>Звук (WASAPI Loopback + Микрофон):</b> Запись звука из игр/системы и голоса с микрофона. Отключайте и включайте микрофон и системный звук прямо во время записи по клику на иконки в шапке.</li>
  <li><b>Живое рисование поверх видео:</b> Нажмите на кнопку пера в шапке записи. Вы сможете рисовать стрелки, текст и маркеры прямо по ходу записи.</li>
  <li><b>Закрепление рисунков (Pin):</b> При включенном Pin рисунки остаются на экране, а при выключенном — плавно перемещаются вместе с рамкой записи.</li>
  <li><b>Остановка записи:</b> Нажмите красную кнопку «Стоп» в шапке или горячую клавишу <code>{hk_stop}</code>. Видео или GIF мгновенно оптимизируются в фоне.</li>
</ul>

<hr/>

<h3>5. Уникальные режимы</h3>
<ul>
  <li><b>Неосязаемая рамка:</b> Кнопка в панели скриншота позволяет кликать мышью сквозь выделение в фоновые окна рабочего стола, сохраняя контур рамки.</li>
  <li><b>Динамический фон:</b> Позволяет сделать скриншот живого видеопотока без замирания рабочего стола.</li>
</ul>
"""
    },

    "en": {
        # --- System Tray & Main Menu ---
        "app_title": "Framio — Screenshots & Recording",
        "tray_ready": "Ready",
        "tray_recording_active": "Framio — Recording: {count} active (video/GIF)",
        "tray_processing": "Framio — Processing ({count} items):",
        "tray_hotkey_capture": "• Area Capture: {key}",
        "tray_hotkey_fullscreen": "• Screen Recording: {key}",
        "tray_hotkey_stop": "• Stop Recording: {key}",
        "tray_hotkey_quick_screen": "• Quick Fullscreen Screenshot: {key}",
        "tray_menu_capture": "Capture Area ({key})",
        "tray_menu_quick_fullscreen": "Quick Fullscreen Screenshot ({key})",
        "tray_menu_rec_fullscreen": "Record Entire Screen ({key})",
        "tray_menu_rec_video": "Record Area Video MP4",
        "tray_menu_rec_gif": "Record Area GIF",
        "tray_menu_status_rec": "Recording in progress ({count} active)",
        "tray_menu_stop_all": "Stop Recording ({key})",
        "tray_menu_folder_screens": "Screenshots Folder",
        "tray_menu_folder_videos": "Videos Folder",
        "tray_menu_folder_gifs": "GIFs Folder",
        "tray_menu_settings": "Settings",
        "tray_menu_help": "Help & Guide",
        "tray_menu_exit": "Exit",

        # --- Notifications ---
        "notif_screen_saved_title": "Screenshot Saved",
        "notif_screen_saved_body": "File: {filename}\nFolder: {folder}\nClick here to show in File Explorer",
        "notif_quick_screen_saved_title": "Quick Fullscreen Screenshot",
        "notif_quick_screen_saved_body": "Entire screen saved to {filename}\nClick here to open file",
        "notif_video_saved_title": "Video Saved Successfully",
        "notif_video_saved_body": "File: {filename}\nFolder: {folder}\nClick here to show in File Explorer",
        "notif_gif_saved_title": "GIF Saved Successfully",
        "notif_gif_saved_body": "File: {filename}\nFolder: {folder}\nClick here to show in File Explorer",
        "notif_rec_saving_video": "Saving Video...",
        "notif_rec_saving_gif": "Saving GIF...",
        "notif_rec_saving_body": "Optimizing and encoding high quality file in background...",
        "notif_rec_cancelled_title": "Recording Cancelled",
        "notif_rec_cancelled_body": "Screen recording was discarded without saving.",
        "notif_rec_started_title": "Fullscreen Recording Started",
        "notif_rec_started_body": "Screen recording started!\nTo stop, press: {key}",
        "notif_clipboard_copied": "Copied to clipboard",

        # --- Recording Window (RecordingFrameWindow) ---
        "rec_mode_video": "REC MP4",
        "rec_mode_gif": "REC GIF",
        "rec_mode_pause": "PAUSE",
        "rec_drag_hint": "Drag by the header to move recording frame across screen",
        "rec_target_screen": "Recording entire desktop area under frame",
        "rec_target_window": "Recording window: {title}",
        "rec_mic_on": "Microphone is ON (click to mute)",
        "rec_mic_off": "Microphone is MUTED (click to unmute)",
        "rec_system_on": "System audio is ON (click to mute)",
        "rec_system_off": "System audio is MUTED (click to unmute)",
        "rec_timer_tip": "Current recording duration and captured frame count",
        "rec_size_tip": "Current recording dimensions in pixels",
        "rec_draw_btn_tip": "Live drawing & annotation toolbar [Ctrl+Z undo]",
        "rec_pause_tip": "Pause recording [Space]",
        "rec_resume_tip": "Resume recording [Space]",
        "rec_stop_tip": "Finish recording and save file [Enter / Space]",
        "rec_lock_tip": "Lock frame from accidental resize",
        "rec_unlock_tip": "Unlock frame to enable resizing",
        "rec_settings_tip": "Recording options (select target app / single window)",
        "rec_cancel_tip": "Cancel recording without saving [Esc]",
        "rec_source_label": "Capture Source",
        "rec_source_all_windows": "Entire Screen / All Windows",
        "rec_source_all_desc": "Capture entire desktop area under frame including all windows",
        "rec_refresh_windows": "Refresh Windows List",
        "rec_pick_window_btn": "Select window by clicking",
        "rec_snap_window_tip": "Click window to snap selection frame",
        "rec_window_hover_badge": "{title} (Click to snap)",
        "rec_mode_countdown": "COUNTDOWN",
        "rec_mode_countdown_prefix": "START: {sec}s",
        "rec_countdown_status": "Starting in {sec}s...",
        "rec_countdown_hint": "Get ready... (Esc to cancel)",

        # --- Scrolling Screenshot ---
        "action_scroll": "Scrolling screenshot with auto-scroll [S]",
        "scroll_hud_title": "Scrolling Screenshot",
        "scroll_hud_status": "Frames: {frames} | Height: {height} px",
        "scroll_hud_done": "Finish (Enter)",
        "scroll_hud_autoscroll": "Auto-scroll",
        "scroll_hud_pause": "Pause",
        "scroll_hud_resume": "Resume",
        "scroll_hud_step": "Capture Step",
        "scroll_hud_cancel": "Cancel (Esc)",
        "scroll_hud_hint": "Scroll page with mouse wheel or click 'Capture Step'. Press Enter to save.",
        "scroll_guide_badge": "Scrolling Capture Area [Scroll mouse wheel]",
        "notif_scroll_saved_title": "Scrolling Screenshot Saved",
        "notif_scroll_saved_body": "File: {filename}\nHeight: {height} px\nClick here to open file in explorer",

        # --- Live Drawing Toolbar (RecordingDrawingToolbar) ---
        "draw_cursor": "Cursor (Screen interaction / clicks pass through frame)",
        "draw_pen": "Pen (freehand drawing)",
        "draw_arrow": "Barbed Arrow",
        "draw_rect": "Rectangle",
        "draw_mosaic": "Mosaic Blur (Censorship)",
        "draw_highlighter": "Highlighter marker (translucent)",
        "draw_text": "Text with background",
        "draw_undo": "Undo last action (Ctrl+Z)",
        "draw_clear": "Clear all drawn shapes",
        "draw_color": "Choose drawing color and stroke width",
        "draw_layers": "Shape layers (management & visibility)",
        "draw_pin_on": "Drawings pinned to screen (stay stationary when moving frame)",
        "draw_pin_off": "Drawings attached to frame (move along with frame)",

        # --- Screenshot Overlay & Toolbars ---
        "tool_move": "Move & select (V)",
        "tool_pen": "Pen (P)",
        "tool_line": "Straight Line (L)",
        "tool_arrow": "Barbed Arrow (A)",
        "tool_rect": "Rectangle (R)",
        "tool_circle": "Ellipse / Circle (C)",
        "tool_highlighter": "Highlighter marker (H)",
        "tool_text": "Text (T)",
        "tool_step": "Step counter (1, 2, 3...) (S)",
        "tool_mosaic": "Censorship / Mosaic blur (M)",
        "tool_undo": "Undo last action (Ctrl+Z)",
        "tool_redo": "Redo action (Ctrl+Y)",
        "tool_clear": "Clear all shapes",
        "action_save": "Save screenshot to disk (Ctrl+S)",
        "action_copy": "Copy to clipboard (Ctrl+C)",
        "action_search": "Search image on Google",
        "action_close": "Close selection (Esc)",
        "action_dynamic_bg": "Dynamic background (live video behind frame)",
        "action_passthrough": "Pass-through frame: clicks pass through selection into desktop",

        # --- Shape Context Menu & Layers ---
        "shape_menu_props": "Edit properties ({name})...",
        "shape_menu_dup": "Duplicate (Ctrl+D)",
        "shape_menu_order": "Layer Order",
        "shape_menu_front": "Bring to Front",
        "shape_menu_up": "Move Up",
        "shape_menu_down": "Move Down",
        "shape_menu_back": "Send to Back",
        "shape_menu_delete": "Delete shape (Del)",
        "layers_dialog_title": "Shape Layers",
        "layers_visible_tip": "Toggle layer visibility",
        "layers_up_tip": "Move layer up",
        "layers_down_tip": "Move layer down",
        "layers_del_tip": "Delete layer",

        # --- Shape Properties Popup (ShapeEditPopup) ---
        "shape_edit_title": "Shape Properties: {name}",
        "shape_edit_color": "Color:",
        "shape_edit_width": "Width: {val} px",
        "shape_edit_opacity": "Opacity: {val}%",
        "shape_edit_style": "Arrow style:",
        "shape_edit_custom_color": "Custom color...",
        "shape_edit_del": "Delete",
        "shape_edit_done": "Done",

        # --- Color Palette Popup (ColorPalettePopup) ---
        "palette_title": "Color & Pen Palette",
        "palette_thickness": "Stroke width: {val} px",
        "palette_custom_tip": "Pick custom color from Windows color palette",

        # --- Settings Dialog ---
        "settings_title": "Framio Settings",
        "settings_tab_storage": "Storage Locations",
        "settings_tab_media": "Recording (Video, GIF, Audio)",
        "settings_tab_hotkeys": "Hotkeys",
        "settings_tab_general": "General & Captures",
        "settings_tab_help": "Help & Guide",

        "settings_lang_label": "Interface Language / Язык интерфейса:",
        "settings_lang_auto": "Auto (System)",
        "settings_lang_ru": "Русский",
        "settings_lang_en": "English",

        "settings_btn_apply": "Apply",
        "settings_btn_close": "Close",
        "settings_btn_record": "Assign",
        "settings_btn_recording": "Press keys...",
        "settings_btn_browse": "Browse...",
        "settings_btn_reset": "Reset",

        "settings_storage_group": "File Storage Directories",
        "settings_screenshots_dir": "Screenshots folder:",
        "settings_videos_dir": "Videos folder (MP4):",
        "settings_gifs_dir": "GIFs folder:",
        "settings_portable_badge": "Mode: {mode} • {path}",

        "settings_video_group": "Video Recording Options (MP4)",
        "settings_video_fps": "Framerate (FPS):",
        "settings_video_codec": "Video codec:",
        "settings_compress_video": "Optimize MP4 file size after recording (H.264 CRF)",

        "settings_gif_group": "GIF Animation Options",
        "settings_gif_fps": "GIF Framerate (FPS):",
        "settings_gif_colors": "GIF Color Palette:",
        "settings_gif_dither": "Color dithering algorithm:",
        "settings_compress_gif": "Optimize GIF size (FFmpeg PaletteGen)",

        "settings_audio_group": "Audio Recording",
        "settings_record_mic": "Record microphone by default",
        "settings_record_system": "Record system sound (games, videos, browser WASAPI loopback)",

        "settings_countdown_group": "Pre-recording Timer",
        "settings_countdown_enable": "Enable countdown timer before recording starts",
        "settings_countdown_delay": "Timer delay:",

        "settings_hotkeys_group": "Global Hotkey Shortcuts",
        "settings_hk_capture": "Screen area capture (screenshot):",
        "settings_hk_quick_screen": "Quick fullscreen screenshot to folder:",
        "settings_hk_rec_fs": "Record entire screen (video):",
        "settings_hk_stop_rec": "Stop active screen recording:",

        "settings_general_group": "Application Behavior",
        "settings_auto_copy": "Automatically copy screenshot to clipboard",
        "settings_auto_copy_desc": "Immediately place captured image into Windows clipboard",
        "settings_play_sound": "Play camera shutter and notification sounds",
        "settings_play_sound_desc": "Audio shutter click on screenshot capture",
        "settings_open_folder": "Open destination folder after saving file",
        "settings_open_folder_desc": "Highlight newly saved file in Windows Explorer",
        "settings_save_on_search": "Save screenshot when searching by image",
        "settings_save_on_search_desc": "Automatically save file to disk before reverse image search",
        "settings_autostart": "Launch Framio on Windows startup (system tray)",
        "settings_autostart_desc": "Automatically launch minimized to tray when signing into Windows",

        "settings_screenshots_group": "Screenshots & Clipboard",
        "settings_save_format": "Default screenshot save format:",
        "settings_copy_format": "Clipboard copy format:",
        "settings_copy_format_dib": "DIB / Bitmap (Standard for messengers & apps)",
        "settings_copy_format_png": "PNG (Preserves alpha transparency)",
        "settings_copy_format_jpg": "JPEG (Compressed compact)",
        "settings_copy_format_data_uri": "Data URI (Base64 string)",

        "settings_annotations_group": "Default Annotation Settings",
        "settings_default_color": "Default tool color:",
        "settings_default_stroke": "Stroke width:",
        "settings_highlighter_alpha": "Highlighter marker opacity:",

        # --- Help & Guide Content ---
        "help_title": "Framio User Guide",
        "help_content": """
<h2>Framio — User Guide</h2>
<p>Framio is a lightweight, blazing-fast screenshot, annotation, and 60+ FPS video & GIF screen recorder for Windows.</p>

<hr/>

<h3>1. Taking Screenshots</h3>
<ul>
  <li><b>Capture Area:</b> Press <code>{hk_capture}</code> or click the tray icon. Click and drag left mouse button to select any screen area.</li>
  <li><b>Quick Fullscreen Screenshot:</b> Press <code>{hk_quick}</code>. A screenshot of all monitors will be saved directly into your screenshots folder and copied to your clipboard instantly — without showing overlay or extra clicks!</li>
  <li><b>Fine Tuning:</b> Drag handles on the borders to resize, or drag from the center (with the Move tool selected) to reposition.</li>
  <li><b>Quick Shortcuts:</b> <code>Ctrl+C</code> to copy, <code>Ctrl+S</code> to save to disk, <code>Esc</code> to cancel.</li>
</ul>

<hr/>

<h3>2. Annotation Tools</h3>
<ul>
  <li><b>Pen (P)</b> — smooth freehand vector drawing.</li>
  <li><b>Barbed Arrow (A)</b> — precise directional arrows with sharp barbed arrowheads.</li>
  <li><b>Rectangle (R) & Ellipse (C)</b> — clean geometric bounding shapes.</li>
  <li><b>Highlighter (H)</b> — translucent marker to emphasize text and key areas.</li>
  <li><b>Text (T)</b> — crisp typography with dark contrasting background for readability on any screen.</li>
  <li><b>Step Counter (S)</b> — numbered circular badges (1, 2, 3...) for tutorial walkthroughs.</li>
  <li><b>Mosaic Blur (M)</b> — censor passwords, emails, and sensitive personal information.</li>
</ul>

<hr/>

<h3>3. Interactive Shape Manipulation</h3>
<ul>
  <li><b>Move Shapes:</b> Hover any shape, <b>click and hold Right Mouse Button (RMB)</b> and drag it anywhere across the screen!</li>
  <li><b>Shape Properties:</b> Click <b>RMB on any shape</b> (or hold for 0.35s) to open the property popup: adjust color, stroke thickness, opacity, arrow style, duplicate (<code>Ctrl+D</code>) or delete (<code>Del</code>).</li>
  <li><b>Layers Dialog:</b> Click the Layers button to view, toggle visibility, reorder or remove individual shape layers.</li>
</ul>

<hr/>

<h3>4. Recording Video (MP4) and Animated GIFs</h3>
<ul>
  <li><b>Start Recording:</b> Press <code>{hk_rec_fs}</code> for fullscreen, or select "Record Video / GIF" from the bottom toolbar after selecting an area.</li>
  <li><b>Dynamic Resizing:</b> Drag and resize the recording frame freely in real-time without black letterboxing or stuttering.</li>
  <li><b>Isolated Window Capture:</b> Click the gear icon in the recording header and pick a specific application window. Framio records only that window even if other windows overlap it!</li>
  <li><b>Audio (WASAPI Loopback + Mic):</b> Capture game/system audio and microphone voice. Toggle audio on/off during live recording by clicking the header icons.</li>
  <li><b>Live Drawing:</b> Click the pen icon in the header. Draw arrows, write notes, and highlight key moments live during video recording.</li>
  <li><b>Pinning (Pin):</b> Pinned drawings stay fixed on the screen, while unpinned drawings move seamlessly with the recording frame.</li>
  <li><b>Stop & Save:</b> Click the red Stop button or press <code>{hk_stop}</code>. Files are optimized and saved automatically in background.</li>
</ul>

<hr/>

<h3>5. Advanced Modes</h3>
<ul>
  <li><b>Pass-through Frame:</b> Toggle pass-through mode in the bottom toolbar to interact with desktop apps directly through the screenshot frame.</li>
  <li><b>Dynamic Background:</b> Capture live animations and moving desktop windows without freezing the background.</li>
</ul>
"""
    }
}


def get_system_language() -> str:
    """Определяет язык операционной системы Windows."""
    try:
        lang_id = ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0xFF
        if lang_id in (0x19, 0x22, 0x23):  # 0x19 = Russian, 0x22 = Ukrainian, 0x23 = Belarusian
            return "ru"
    except Exception:
        pass
    try:
        name = QLocale.system().name().lower()
        if name.startswith(("ru", "uk", "be")):
            return "ru"
    except Exception:
        pass
    return "en"


_LANGUAGE_OVERRIDE = None


def set_language(lang: str = None):
    """Принудительно устанавливает язык интерфейса ('ru', 'en' или None/'auto' для сброса)."""
    global _LANGUAGE_OVERRIDE
    if lang in ("ru", "en"):
        _LANGUAGE_OVERRIDE = lang
    else:
        _LANGUAGE_OVERRIDE = None


def get_current_language() -> str:
    """Возвращает текущий активный язык интерфейса с учетом настроек пользователя."""
    global _LANGUAGE_OVERRIDE
    if _LANGUAGE_OVERRIDE in ("ru", "en"):
        return _LANGUAGE_OVERRIDE
    try:
        from config import ConfigManager
        cfg = ConfigManager.get_instance().config
        pref = getattr(cfg, "language", "auto")
        if pref in ("ru", "en"):
            return pref
    except Exception:
        pass
    return get_system_language()


def tr(_key: str, _default: str = None, **kwargs) -> str:
    """
    Возвращает локализованную строку по ключу для текущего языка.
    Поддерживает подстановку именованных параметров через str.format().
    """
    lang = get_current_language()
    dictionary = TRANSLATIONS.get(lang, TRANSLATIONS["ru"])
    fallback_dict = TRANSLATIONS["ru"]

    text = dictionary.get(_key)
    if text is None:
        text = fallback_dict.get(_key, _default or _key)

    if kwargs and isinstance(text, str):
        try:
            return text.format(**kwargs)
        except Exception:
            return text
    return text
