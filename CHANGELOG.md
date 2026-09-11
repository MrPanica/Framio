# Changelog

## [1.0.10] - 2026-09-12

### English

Selection tool enhancements, scrolling screenshot smoothness, and video recording finalization fixes.

- Fixed selection tool in video/GIF recording canvas: enabled rubber-band multi-shape selection, shape grouping into `ShapeGroup`, and preserved 8 transformation handles and rotation after rotating shapes.
- Fixed "protected zone" mode during recording: properly swallows background clicks so they cannot leak into underlying desktop windows.
- Fixed cursor freezes and stutters during scrolling screenshot: completely removed forced mouse cursor centering (`user32.SetCursorPos`), optimized vertical shift detection with horizontal stride for 5-10x faster matching at full 1px vertical accuracy, added reentrancy guards for frame stitching.
- Fixed video recording finalization when audio is disabled or when encountering file lock delays: added safe atomic replacement with retry, ensured `try...finally` block guarantees progress to 100% and emits `recording_finished` signal so recordings never get stuck at 22% in the system tray.

### Русский

Улучшение инструмента выделения, устранение зависаний длинного скриншота и исправление финализации видео.

- Исправлен инструмент «Выделение» при записи видео и GIF: добавлено резиновое выделение группы фигур прямоугольной рамкой, группировка в `ShapeGroup`, сохранение интерактивной рамки с 8 маркерами трансформации после вращения фигур.
- Исправлен режим «Защита зоны» (неосязаемая рамка) во время записи: фоновые клики гарантированно поглощаются холстом и не пробивают в сторонние окна рабочего стола.
- Устранены зависания и дергания курсора при длинном/скролл-скриншоте: полностью удалено принудительное перемещение курсора мыши (`SetCursorPos`), оптимизирован алгоритм поиска сдвига `_detect_vertical_shift` с горизонтальным шагом (ускорение в 5-10 раз при точности 1 px) и ранним выходом, добавлена защита от повторного входа при сшивании кадров.
- Исправлена финализация записи видео без звука и при задержках блокировки файлов: добавлена безопасная замена файлов с повторными попытками, завершение обёрнуто в `try...finally` с гарантированным сигналом `recording_finished` и прогрессом 100% (устранено зависание статуса «записывается / 22%» в системном трее).

## [1.0.9] - 2026-09-11

### English

Performance optimizations, build cleanup, and recording UI refinements.

- Added LRU caching for mosaic and blur effects, speeding up redraws by over 100x and ensuring smooth 60 FPS rendering.
- Zero-allocation memory buffer reuse in Win32 screen capture, eliminating high-rate memory allocations during video/GIF recording.
- Reusable transformation bounding boxes and rotation angle preservation for shapes and groups.
- Compact 34px sidebar in recording mode with instant tooltips.
- High-contrast dashed outlines when drawing mosaic and blur regions.
- Smooth collapse of expanded toolbar sections without vertical gaps.
- Instant elimination of transparent background breach upon stopping recording.
- Cleaned PyInstaller build configuration, excluding unused modules and eliminating compiler warnings.
- Verified dependencies against vulnerability databases with 0 known CVEs.

### Русский

Оптимизация производительности, очистка сборки и доработка интерфейса записи.

- Добавлено LRU-кэширование эффектов мозаики и размытия, ускоряющее перерисовку более чем в 100 раз (плавные 60 FPS).
- Переиспользование буфера памяти при Win32-захвате экрана с 0 повторных аллокаций во время записи видео и GIF.
- Сохранение интерактивной рамки трансформации и угла поворота для отдельных фигур и групп.
- Компактная боковая панель записи GIF (34px) с мгновенными всплывающими подсказками.
- Контрастный пунктирный контур при рисовании областей мозаики и цензуры.
- Сворачивание панели инструментов без пустого пространства сверху.
- Мгновенное закрытие бреши затемнения после остановки записи без мерцания.
- Очистка конфигурации PyInstaller от неиспользуемых модулей и полное устранение предупреждений сборщика.
- Проверка всех зависимостей по базам уязвимостей (0 известных CVE).

## [1.0.8] - 2026-09-09

### English

Improved multi-zone capture, recording controls, and recent media actions.

- Saving or copying one selected zone no longer hides the other selected zones or causes a visible dimming flicker.
- Recording one zone keeps the other selected zones and their controls available.
- Added safer multi-zone recording controls, independent effects, capture masks, and Undo/Redo coverage.
- Added direct image search actions for recent materials and a Windows “Open with...” action.
- Recent-material actions now use compact icons and remain on one line.
- Added cleanup for stale recording files and old PyInstaller temporary folders.
- Improved window capture selection, toolbar overlap handling, and live blur/mosaic controls.

### Русский

Улучшены многозонный захват, управление записью и последние материалы.

- Сохранение или копирование одной выбранной зоны больше не скрывает остальные зоны и не вызывает мигание затемнения.
- При записи одной зоны остальные выбранные зоны и их элементы управления остаются доступными.
- Улучшено управление многозонной записью, независимыми эффектами, масками захвата и историей Undo/Redo.
- Для последних материалов добавлены прямой поиск по картинке и действие Windows «Открыть с помощью...».
- Действия последних материалов переведены на компактные иконки и размещаются в одну строку.
- Добавлена безопасная очистка старых временных файлов записи и каталогов PyInstaller.
- Улучшены выбор окон для захвата, перекрытие панелей и настройка блюра/мозаики в реальном времени.

## [1.0.7] - 2026-09-08

### English

Fixed conflicting `Print Screen` shortcuts.

- A standalone `Print Screen` shortcut no longer intercepts combinations with `Ctrl`, `Alt`, `Shift`, or `Win`.
- `Ctrl+Alt+Print Screen` now reaches the configured fullscreen screenshot action.

### Русский

Исправлен конфликт горячих клавиш с `Print Screen`.

- Одиночный `Print Screen` больше не перехватывает сочетания с `Ctrl`, `Alt`, `Shift` или `Win`.
- `Ctrl+Alt+Print Screen` теперь запускает назначенный скриншот всего экрана.

## [1.0.6] - 2026-09-08

### English

Fixed hotkey assignment in Settings.

- Keyboard events are now captured correctly by the shortcut recorder.
- `Print Screen` can be assigned inside a combination without triggering a screenshot.
- The previous shortcut is restored when assigning is cancelled.

### Русский

Исправлено назначение горячих клавиш в настройках.

- Кнопка назначения теперь корректно принимает нажатия клавиш.
- `Print Screen` можно назначить частью комбинации без запуска скриншота.
- При отмене назначения возвращается предыдущая комбинация.

## [1.0.5] - 2026-09-08

### English

Added a standalone `Print Screen` hotkey for fullscreen screenshots.

- Press `Print Screen` to save the full desktop directly to the screenshots folder.
- No area selection or save dialog is shown.
- The existing area-capture and `Ctrl+Print Screen` shortcuts remain available.

### Русский

Добавлена отдельная горячая клавиша `Print Screen` для полного скриншота.

- Нажмите `Print Screen`, чтобы сразу сохранить весь рабочий стол в папку скриншотов.
- Выбор области и диалог сохранения не открываются.
- Захват области и сочетание `Ctrl+Print Screen` продолжают работать отдельно.

## [1.0.4] - 2026-09-08

### English

Google Lens direct-upload fix.

- Replaced the clipboard fallback with a one-use local browser form.
- The browser now sends the PNG directly to Google Lens as `multipart/form-data` with the current Chromium upload parameters.
- No image-hosting intermediary is used; the temporary local form is removed automatically.

| Build | FFmpeg | Main difference |
| --- | --- | --- |
| Folder portable | Included | Fastest start; move the complete folder, including `_internal`. |
| Full single-file | Included | One EXE; unpacks private files into `%TEMP%` at startup. |
| Lite single-file | Not bundled | Smaller EXE; no FFmpeg-dependent audio muxing or post-processing. |

### Русский

Исправлена прямая загрузка в Google Lens.

- Буферный обход заменён одноразовой локальной формой браузера.
- Теперь браузер отправляет PNG напрямую в Google Lens через `multipart/form-data` с актуальными параметрами загрузки Chromium.
- Промежуточный хостинг изображений не используется, временная локальная форма удаляется автоматически.

| Сборка | FFmpeg | Главное отличие |
| --- | --- | --- |
| Папочная portable | Включён | Быстрее запускается; переносить нужно всю папку, включая `_internal`. |
| Полная одним файлом | Включён | Один EXE; при запуске распаковывает внутренние файлы в `%TEMP%`. |
| Lite одним файлом | Не включён | Меньше EXE; нет функций с FFmpeg для сведения звука и постобработки. |

## [1.0.3] - 2026-09-08

### English

Google Lens search reliability fix.

- Stopped opening Google’s short-lived private `vsrid` result links, which could show “Visual search request is no longer valid”.
- Google Lens now opens its official page, copies the screenshot to the Windows clipboard, and attempts a guarded automatic `Ctrl+V`; manual paste remains available.
- Yandex Images still receives the PNG directly, and no intermediate image-hosting service is used.

| Build | FFmpeg | Main difference |
| --- | --- | --- |
| Folder portable | Included | Fastest start; move the complete folder, including `_internal`. |
| Full single-file | Included | One EXE; unpacks private files into `%TEMP%` at startup. |
| Lite single-file | Not bundled | Smaller EXE; no FFmpeg-dependent audio muxing or post-processing. |

### Русский

Исправлена надёжность поиска через Google Lens.

- Убрано открытие короткоживущих внутренних ссылок Google с `vsrid`, из-за которых появлялось сообщение «Запрос для визуального поиска больше не действителен».
- Google Lens теперь открывается на официальной странице, снимок помещается в буфер обмена Windows, а приложение осторожно пытается выполнить `Ctrl+V`; ручная вставка остаётся доступной.
- Яндекс.Картинки по-прежнему получает PNG напрямую, промежуточные сервисы размещения изображений не используются.

| Сборка | FFmpeg | Главное отличие |
| --- | --- | --- |
| Папочная portable | Включён | Быстрее запускается; переносить нужно всю папку, включая `_internal`. |
| Полная одним файлом | Включён | Один EXE; при запуске распаковывает внутренние файлы в `%TEMP%`. |
| Lite одним файлом | Не включён | Меньше EXE; нет функций с FFmpeg для сведения звука и постобработки. |

## [1.0.2] - 2026-09-08

### English

Privacy and release documentation update.

- Reverse image search now uploads directly to Google Lens or Yandex Images.
- Removed all intermediate image-hosting services and the old FreeImage API key path.
- Documented the differences between folder portable, full single-file, and Lite builds in every release section.

| Build | FFmpeg | Main difference |
| --- | --- | --- |
| Folder portable | Included | Fastest start; move the complete folder, including `_internal`. |
| Full single-file | Included | One EXE; unpacks private files into `%TEMP%` at startup. |
| Lite single-file | Not bundled | Smaller EXE; no FFmpeg-dependent audio muxing or post-processing. |

### Русский

Обновление приватности и описания сборок.

- Поиск по картинке теперь отправляет изображение напрямую в Google Lens или Яндекс.Картинки.
- Удалены все промежуточные сервисы размещения изображений и старый путь с ключом FreeImage.
- Различия между папочной portable-, полной однофайловой и Lite-сборками указаны в каждом разделе релиза.

| Сборка | FFmpeg | Главное отличие |
| --- | --- | --- |
| Папочная portable | Включён | Быстрее запускается; переносить нужно всю папку, включая `_internal`. |
| Полная одним файлом | Включён | Один EXE; при запуске распаковывает внутренние файлы в `%TEMP%`. |
| Lite одним файлом | Не включён | Меньше EXE; нет функций с FFmpeg для сведения звука и постобработки. |

## [1.0.1] - 2026-09-08

### English

Maintenance release with recording, multi-zone, mask, startup, and release-build fixes.

- Fixed focus and z-order handling for overlapping recording frames.
- Made the single-instance guard atomic on Windows.
- Removed unused Qt PDF, software OpenGL, and Poppler runtime files from Windows builds.
- Updated the Windows release workflow to publish the matching changelog section.

| Build | FFmpeg | Main difference |
| --- | --- | --- |
| Folder portable | Included | Fastest start; move the complete folder, including `_internal`. |
| Full single-file | Included | One EXE; unpacks private files into `%TEMP%` at startup. |
| Lite single-file | Not bundled | Smaller EXE; no FFmpeg-dependent audio muxing or post-processing. |

### Русский

Технический релиз с исправлениями записи, нескольких зон, масок, запуска и сборки.

- Исправлен выбор перекрывающихся рамок записи и порядок окон.
- Блокировка повторного запуска стала атомарной на Windows.
- Из Windows-сборок убраны неиспользуемые Qt PDF, программный OpenGL и Poppler-файлы.
- Workflow релиза теперь публикует соответствующий раздел changelog.

| Сборка | FFmpeg | Главное отличие |
| --- | --- | --- |
| Папочная portable | Включён | Быстрее запускается; переносить нужно всю папку, включая `_internal`. |
| Полная одним файлом | Включён | Один EXE; при запуске распаковывает внутренние файлы в `%TEMP%`. |
| Lite одним файлом | Не включён | Меньше EXE; нет функций с FFmpeg для сведения звука и постобработки. |

## [1.0.0] - 2026-09-08

### English

First public release of Framio.

- Screenshots and MP4/GIF recording for one area, multiple areas, all screens, and selected application windows.
- Separate export and clipboard support for multiple areas.
- Freeform, rectangle, and oval capture masks, including several masks in one area.
- Recent Media panel with filters, previews, copy, open, and drag-and-drop.
- Undo and redo for annotations, masks, and capture-area editing.
- Portable folder, full single-file, and lightweight single-file Windows builds.

| Build | FFmpeg | Main difference |
| --- | --- | --- |
| Folder portable | Included | Fastest start; move the complete folder, including `_internal`. |
| Full single-file | Included | One EXE; unpacks private files into `%TEMP%` at startup. |
| Lite single-file | Not bundled | Smaller EXE; no FFmpeg-dependent audio muxing or post-processing. |

### Русский

Первый публичный релиз Framio.

- Скриншоты и запись MP4/GIF одной области, нескольких областей, всех экранов и выбранных окон.
- Раздельное сохранение и копирование нескольких областей.
- Произвольные, прямоугольные и овальные маски, в том числе несколько масок в одной области.
- Панель последних материалов с фильтрами, превью, копированием, открытием и перетаскиванием.
- Отмена и повтор действий для аннотаций, масок и областей захвата.
- Папочная, полная сборка одним файлом и облегчённая сборка одним файлом для Windows.

| Сборка | FFmpeg | Главное отличие |
| --- | --- | --- |
| Папочная portable | Включён | Быстрее запускается; переносить нужно всю папку, включая `_internal`. |
| Полная одним файлом | Включён | Один EXE; при запуске распаковывает внутренние файлы в `%TEMP%`. |
| Lite одним файлом | Не включён | Меньше EXE; нет функций с FFmpeg для сведения звука и постобработки. |
