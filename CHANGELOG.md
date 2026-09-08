# Changelog

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
