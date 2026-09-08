# Framio

Framio is a portable Windows app for taking screenshots and recording screen areas as MP4 or GIF. It can work with one area, several areas, the whole desktop, or a complete application window.

The current release is `1.0.1`. See [CHANGELOG.md](CHANGELOG.md) for the release history.

## English

### Quick start

1. Download a build from [GitHub Releases](https://github.com/MrPanica/Framio/releases).
2. Run `Framio.exe`. Installation is not required; the app appears in the system tray.
3. Press `Ctrl+Shift+Print Screen` or choose the capture command from the tray menu.

For a source checkout:

```powershell
python -m pip install -r requirements.txt
python main.py
```

### What you can capture

- A rectangular area of the screen.
- Several independent areas at once.
- All connected screens.
- A complete application window.
- A scrolling page or document.

Screenshots can be saved as PNG, JPG, or WebP. Areas can be recorded as MP4 or GIF. System audio and microphone recording are available in the recording settings.

### Multiple areas

1. Select the first area.
2. Press `+` in the area toolbar.
3. Select the next area. Press `+` again before creating another area.
4. To add areas continuously, hold `Ctrl` while drawing.

Existing areas remain movable. Clicking an existing area does not create a new one.

The top multi-action bar works with all areas that are not currently recording. It can save, copy, start MP4 recording, or start GIF recording for all of them. Each area remains a separate screenshot or recording.

When several images are copied, Framio puts the complete set into its own clipboard payload and also publishes file URLs. Windows applications that support only one standard image may paste the first image; this is a limitation of the Windows clipboard format, not a combined screenshot from Framio.

### Recording a complete window

Start capture without drawing an area, then double-click the target window. Framio finds the window behind its overlay and uses that window as the capture source.

For an already selected area, open the recording frame settings and either choose a window from the list or use **Select window by clicking**. To return to normal screen-area capture, choose **Entire area under frame**.

Some windows cannot be captured by Windows, including protected or DRM content and some hardware-rendered surfaces. In that case Framio falls back to the pixels visible under the recording frame.

### Capture masks

Select **Capture area mask** and choose a freeform contour, rectangle, or oval. A freeform contour closes when the mouse button is released. You can create several masks in one area and move, resize, or rotate them.

Right-click a mask and choose **Set capture area to mask bounds** to resize the capture area to the common bounding rectangle of all masks in that area. PNG keeps transparency outside the masks; MP4 and GIF use black outside them.

### Undo and redo

`Ctrl+Z` and `Ctrl+Y` cover annotation edits, mask creation/deletion/transforms, capture-area changes, and adding or removing areas. A move or resize is stored as one history entry when the mouse button is released, not once per mouse event. Saving, copying, and starting a recording are output actions and are not added to the edit history.

### Recent media

Open **Framio — Recent Media** from the tray menu. The panel contains filters for all items, screenshots, GIFs, and videos. It shows image and video previews, the file name above each preview, and the full name and path in the tooltip.

Use **Copy** to place an image or file in the Windows clipboard. Use **View** or click the preview to open the file with the default Windows application. A preview can also be dragged into a chat, editor, or another application.

### Default hotkeys

| Action | Keys |
| --- | --- |
| Capture an area | `Ctrl+Shift+Print Screen` |
| Quick screenshot of all screens | `Ctrl+Print Screen` |
| Record all screens | `Ctrl+Shift+F9` |
| Stop active recording | `Ctrl+Shift+F10` |
| Copy selected areas | `Ctrl+C` |
| Save selected areas | `Ctrl+S` |
| Undo | `Ctrl+Z` |
| Redo | `Ctrl+Y` |
| Close capture or cancel recording | `Esc` |

Change these shortcuts in **Settings → Hotkeys**. The tray menu shows the current values.

### Portable builds

- **Single-file build** — one `Framio.exe`. PyInstaller unpacks its private files into a temporary directory at startup, so no DLL folder needs to be copied. The current full build is about 114 MiB and starts a little slower.
- **Lite single-file build** — a smaller file without the bundled FFmpeg executable. Basic MP4/GIF recording remains available; FFmpeg-dependent audio muxing and post-processing are not included.
- **Folder build** — starts faster and does not unpack anything into `%TEMP%`, but the complete `Framio` folder, including `_internal`, must be moved together. The current folder is about 302 MiB.

The app keeps `settings.json` and the `Captures` folders next to the executable. Move the whole portable build when changing computers or folders.

### Development

Run the tests:

```powershell
python -u tests/test_components.py
```

Build the folder version:

```powershell
python -m PyInstaller --noconfirm Framio.spec
```

Build the single-file version:

```powershell
python -m PyInstaller --noconfirm Framio-onefile.spec
```

The GitHub Actions workflow runs the tests, builds all three Windows packages, uploads Actions artifacts, and creates a GitHub Release for a matching `v<version>` tag. The version is read from `VERSION`. Release notes come from the matching English-then-Russian section in `CHANGELOG.md`.

## Русский

Текущая версия — `1.0.1`. История изменений находится в [CHANGELOG.md](CHANGELOG.md).

### Быстрый запуск

1. Скачайте сборку из раздела [GitHub Releases](https://github.com/MrPanica/Framio/releases).
2. Запустите `Framio.exe`. Установка не нужна — приложение появится в системном трее.
3. Нажмите `Ctrl+Shift+Print Screen` или выберите захват в меню трея.

Для запуска из исходников:

```powershell
python -m pip install -r requirements.txt
python main.py
```

### Что можно захватывать

- Прямоугольную область экрана.
- Несколько независимых областей одновременно.
- Все подключённые экраны.
- Целое окно приложения.
- Длинную страницу или документ с прокруткой.

Скриншоты можно сохранять в PNG, JPG и WebP. Области можно записывать в MP4 или GIF. Запись системного звука и микрофона включается в параметрах записи.

### Несколько областей

1. Выделите первую область.
2. Нажмите `+` на панели области.
3. Выделите следующую область. Перед созданием ещё одной снова нажмите `+`.
4. Для непрерывного добавления удерживайте `Ctrl` во время выделения.

Уже созданные области можно перемещать. Клик по существующей области не создаёт новую.

Верхняя панель массовых действий работает со всеми свободными областями. Через неё можно сохранить, скопировать, запустить MP4 или GIF сразу для всех. Каждая область остаётся отдельным изображением или отдельной записью.

При копировании нескольких изображений Framio сохраняет полный набор в собственном формате буфера обмена и добавляет ссылки на файлы. Обычные приложения Windows, которые умеют принимать только одно изображение, вставят первую область — это ограничение стандартного буфера обмена Windows.

### Запись целого окна

Начните захват, не рисуя область, и дважды щёлкните по нужному окну. Framio найдёт окно под своим overlay и будет использовать его как источник записи.

Если область уже выделена, откройте параметры рамки записи и выберите окно из списка либо нажмите **Выбрать окно кликом мыши**. Для возврата к обычной записи экрана выберите **Вся область под рамкой**.

Некоторые окна Windows нельзя захватывать напрямую: например, защищённое или DRM-содержимое и отдельные поверхности с аппаратным выводом. В таком случае Framio записывает видимые пиксели под рамкой.

### Маски области записи

Выберите **Маска области записи**, а затем произвольный контур, прямоугольник или овал. Произвольный контур замыкается после отпускания кнопки мыши. В одной области можно создать несколько масок, перемещать, масштабировать и поворачивать их.

Нажмите правой кнопкой по маске и выберите **Установить область по размеру маски**, чтобы рамка захвата стала общей ограничивающей рамкой всех масок этой области. В PNG снаружи масок остаётся прозрачность, а в MP4 и GIF внешняя часть становится чёрной.

### Отмена и повтор действий

`Ctrl+Z` и `Ctrl+Y` работают с аннотациями, созданием, удалением и трансформацией масок, изменением областей, а также добавлением и удалением областей. Перемещение или изменение размера записывается одной командой после отпускания кнопки мыши, а не по каждому событию мыши. Сохранение, копирование и запуск записи являются операциями вывода и в историю редактирования не попадают.

### Последние материалы

Откройте **Последние материалы Framio** в меню трея. В панели есть фильтры «Все», «Скриншоты», «GIF» и «Видео». Для изображений и видео показываются превью, имя файла находится над превью, а полное имя и путь доступны в подсказке.

Кнопка **Копировать** помещает изображение или файл в буфер обмена Windows. Кнопка **Просмотр** и клик по превью открывают файл стандартным приложением Windows. Превью также можно перетащить в чат, редактор или другую программу.

### Горячие клавиши по умолчанию

| Действие | Клавиши |
| --- | --- |
| Выделить область | `Ctrl+Shift+Print Screen` |
| Быстрый скриншот всех экранов | `Ctrl+Print Screen` |
| Записать все экраны | `Ctrl+Shift+F9` |
| Остановить запись | `Ctrl+Shift+F10` |
| Скопировать выбранные области | `Ctrl+C` |
| Сохранить выбранные области | `Ctrl+S` |
| Отменить действие | `Ctrl+Z` |
| Повторить отменённое действие | `Ctrl+Y` |
| Закрыть захват или отменить запись | `Esc` |

Изменить сочетания можно в разделе **Настройки → Горячие клавиши**. В меню трея показываются текущие значения.

### Портативные сборки

- **Один файл** — один `Framio.exe`. При запуске PyInstaller временно распаковывает внутренние файлы, поэтому переносить отдельную папку с DLL не нужно. Полная сборка сейчас занимает около 114 МиБ и запускается немного дольше.
- **Облегчённая сборка** — один файл без встроенного FFmpeg. Обычная запись MP4/GIF остаётся доступной, но функции, которым нужен FFmpeg, например объединение аудио и постобработка, не входят в эту сборку.
- **Папочная сборка** — запускается быстрее и ничего не распаковывает в `%TEMP%`, но переносить нужно всю папку `Framio`, включая `_internal`. Текущий размер папки — около 302 МиБ.

`settings.json` и папки `Captures` создаются рядом с exe. Для переноса на другой компьютер переносите всю выбранную portable-сборку.

### Разработка

Запустить тесты:

```powershell
python -u tests/test_components.py
```

Собрать папочную версию:

```powershell
python -m PyInstaller --noconfirm Framio.spec
```

Собрать версию одним файлом:

```powershell
python -m PyInstaller --noconfirm Framio-onefile.spec
```

GitHub Actions запускает тесты, собирает три варианта для Windows, загружает артефакты и создаёт GitHub Release для тега `v<версия>`. Версия берётся из `VERSION`. Текст релиза берётся из соответствующего раздела `CHANGELOG.md`: сначала английская часть, затем русская.

## License

[Creative Commons Attribution-NonCommercial 4.0 International (CC BY-NC 4.0)](LICENSE)
