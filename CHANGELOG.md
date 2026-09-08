# Changelog

## [1.0.1] - 2026-09-08

### English

Maintenance release with recording, multi-zone, mask, startup, and release-build fixes.

- Fixed focus and z-order handling for overlapping recording frames.
- Made the single-instance guard atomic on Windows.
- Removed unused Qt PDF, software OpenGL, and Poppler runtime files from Windows builds.
- Updated the Windows release workflow to publish the matching changelog section.

### Русский

Технический релиз с исправлениями записи, нескольких зон, масок, запуска и сборки.

- Исправлен выбор перекрывающихся рамок записи и порядок окон.
- Блокировка повторного запуска стала атомарной на Windows.
- Из Windows-сборок убраны неиспользуемые Qt PDF, программный OpenGL и Poppler-файлы.
- Workflow релиза теперь публикует соответствующий раздел changelog.

## [1.0.0] - 2026-09-08

### English

First public release of Framio.

- Screenshots and MP4/GIF recording for one area, multiple areas, all screens, and selected application windows.
- Separate export and clipboard support for multiple areas.
- Freeform, rectangle, and oval capture masks, including several masks in one area.
- Recent Media panel with filters, previews, copy, open, and drag-and-drop.
- Undo and redo for annotations, masks, and capture-area editing.
- Portable folder, full single-file, and lightweight single-file Windows builds.

### Русский

Первый публичный релиз Framio.

- Скриншоты и запись MP4/GIF одной области, нескольких областей, всех экранов и выбранных окон.
- Раздельное сохранение и копирование нескольких областей.
- Произвольные, прямоугольные и овальные маски, в том числе несколько масок в одной области.
- Панель последних материалов с фильтрами, превью, копированием, открытием и перетаскиванием.
- Отмена и повтор действий для аннотаций, масок и областей захвата.
- Папочная, полная сборка одним файлом и облегчённая сборка одним файлом для Windows.
