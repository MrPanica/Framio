# Framio

Framio — portable Windows application for screenshots, screen recording and animated GIFs. It can capture one area, several independent areas, a full screen, or a selected application window. The interface is available in Russian and English.

## Русский

### Что умеет Framio

- Делать скриншот выбранной области, всего рабочего стола или нескольких областей.
- Сохранять зоны по отдельности, а не одним общим изображением.
- Копировать несколько зон как отдельные изображения/файлы Windows Clipboard. Совместимость зависит от приложения, куда выполняется вставка: обычные приложения могут читать только первое изображение, а приложения с поддержкой списка файлов получают все зоны.
- Записывать выбранную область в MP4 или GIF. Для нескольких зон запись запускается независимо: запуск одной зоны не включает соседние.
- Выбирать FPS видео в окне параметров записи (15, 24, 30 или 60).
- Захватывать всё окно приложения: наведите курсор на окно после запуска захвата, дождитесь синей рамки с названием и щёлкните по подсвеченному окну. Альтернативный способ — открыть настройки рамки записи и нажать «Выбрать окно кликом мыши».
- Добавлять к скриншотам аннотации: текст, линии, стрелки, прямоугольники, круги, маркер, мозаику и размытие.
- Делать длинные скриншоты с прокруткой.
- Работать со звуком системы и микрофоном при записи, если эти источники включены в настройках.

### Несколько зон

1. Запустите захват и выделите первую зону.
2. Нажмите `+` в панели зоны. Верхняя панель покажет, что включён режим добавления следующей зоны.
3. Выделите вторую зону. Для третьей и следующих зон снова нажимайте `+`. Для непрерывного добавления можно удерживать `Ctrl`.
4. Уже созданную зону можно выбрать и переместить. Клик по существующей зоне не создаёт новую зону.

Верхняя панель массовых действий работает только с зонами, которые сейчас свободны: сохранить все, скопировать все, запустить видео или GIF для всех свободных зон. Запись каждой зоны имеет отдельную рамку и отдельный файл.

### Запись целого окна

После запуска захвата области не начинайте тянуть рамку. Наведите курсор на нужное окно и подождите синюю рамку с его названием, затем сделайте короткий щелчок. Framio запомнит именно это окно и будет получать его содержимое через Windows, даже если другое окно временно перекрывает его.

Если подсветка не появляется, откройте настройки уже показанной рамки записи (кнопка с шестерёнкой) и выберите окно в списке либо нажмите «Выбрать окно кликом мыши», после чего щёлкните по нужному окну. В заголовке рамки появится значок и название выбранного окна. Чтобы вернуться к обычной записи области экрана, в настройках выберите «Вся область под рамкой».

Так записывается клиентская область и рамка окна, которую Windows отдаёт приложению. Свёрнутое окно перед началом записи автоматически разворачивается; защищённые окна, окна с DRM и некоторые приложения с аппаратным выводом могут не разрешать захват — в таком случае используется обычный захват экрана под рамкой.

### Последние материалы

В меню трея откройте «Последние материалы Framio». В списке есть фильтры «Все», «Скриншоты», «GIF» и «Видео», прокрутка и превью видео. Кнопки под превью копируют файл в буфер обмена Windows или открывают его приложением по умолчанию. Само превью можно открыть, а удержанием левой кнопки его можно перетащить в чат, редактор или другое приложение.

Кнопка «Вставить» намеренно не используется: меню трея забирает фокус у поля, из которого пользователь хотел вставить файл. Для вставки используйте перетаскивание превью или обычный `Ctrl+V` после кнопки «Копировать».

### Горячие клавиши по умолчанию

| Действие | Клавиши |
| --- | --- |
| Захват области | `Ctrl+Shift+Print Screen` |
| Быстрый скриншот всех экранов | `Ctrl+Print Screen` |
| Запись всего экрана | `Ctrl+Shift+F9` |
| Остановить активную запись | `Ctrl+Shift+F10` |
| Копировать выбранные зоны | `Ctrl+C` |
| Сохранить выбранные зоны | `Ctrl+S` |
| Отменить действие | `Ctrl+Z` |
| Повторить действие | `Ctrl+Y` |
| Закрыть режим захвата или отменить запись | `Esc` |

Горячие клавиши можно изменить в разделе «Настройки → Горячие клавиши». Подписи в меню трея берутся из текущих настроек.

### Маска области записи

После создания прямоугольной зоны выберите инструмент «Маска области записи» справа и выберите произвольный контур, прямоугольник или овал. Контур замыкается после отпускания мыши. Маску можно перемещать, масштабировать и поворачивать стандартными маркерами трансформации. PNG сохраняется с прозрачными пикселями снаружи маски; в MP4/GIF внешняя часть кадра заполняется чёрным.

### Портативный запуск

Доступны два варианта:

- `dist-onefile/Framio.exe` — один переносимый файл. При запуске PyInstaller временно распаковывает Qt, OpenCV и FFmpeg во внутреннюю временную папку, затем запускает приложение. Пользователю не нужно переносить DLL. Первый запуск немного дольше, а файл занимает около 126 МиБ.
- `dist-lite/Framio.exe` — облегчённый один файл размером около 97 МиБ. В него не включён большой FFmpeg: обычные MP4 и GIF работают через встроенные OpenCV/Pillow, но встроенное микширование звука и FFmpeg-постобработка недоступны.
- `dist/Framio/Framio.exe` — папочная сборка. Она запускается быстрее и удобнее для частых запусков, но переносить нужно весь каталог вместе с `_internal`.

В обоих вариантах `settings.json` и папки `Captures/Screenshots`, `Captures/Videos`, `Captures/GIFs` создаются рядом с exe. Пути автоматически перепривязываются при переносе каталога.

Workflow `.github/workflows/build.yml` собирает три варианта на Windows и публикует артефакты `Framio-windows`, `Framio-windows-single-file` и `Framio-windows-single-file-lite`.

Для запуска из исходников:

```powershell
pip install -r requirements.txt
python main.py
```

Тесты:

```powershell
python -u tests/test_components.py
```

Сборка:

```powershell
python -m PyInstaller --noconfirm Framio.spec
```

Результат появляется в `dist/Framio/`.

## English

### What Framio does

- Captures a selected area, the whole desktop, or several independent areas.
- Saves each selected zone as its own image.
- Copies multiple zones as separate Windows Clipboard images/files. The receiving application decides how many clipboard items it can read; applications with file-list support receive all zones.
- Records an area as MP4 or GIF. Multi-zone recording is independent: starting one zone does not record its neighbours.
- Lets you choose video FPS in the recording options (15, 24, 30 or 60).
- Selects a complete application window: start capture, move over a window, wait for the blue border and title, then click the highlighted window. You can also open the recording frame settings and use “Select window by clicking”.
- Adds annotations such as text, lines, arrows, rectangles, circles, marker, mosaic and blur.
- Creates scrolling screenshots.
- Records system audio and microphone input when enabled in settings.

### Multiple zones

Select the first zone, press `+`, and select the next one. Press `+` again before creating another zone. Holding `Ctrl` can be used for continuous zone creation. Existing zones remain selectable and movable; clicking an existing zone does not create a new one.

The top multi-action bar affects only zones that are not already recording. It can save, copy, start MP4 recording, or start GIF recording for all available zones. Every recording has its own frame and output file.

### Recent media

Open “Framio — Recent Media” from the tray menu. It provides filters for all items, screenshots, GIFs and videos, a scrollable list, and video thumbnails. The buttons below a preview copy the file to the Windows clipboard or open it with the default application. You can also drag a preview into a chat, editor, or another application.

The old “Paste” button is intentionally not shown because opening a tray menu moves focus away from the intended text field. Use drag-and-drop or click “Copy” and press `Ctrl+V` in the destination application.

### Recording a complete window

After starting area capture, move the pointer over the target window without dragging. Wait for the blue outline and title, then click once. Framio stores that window as the capture target and reads its content through Windows instead of simply recording whatever happens to be visible underneath the frame.

If the outline does not appear, open the gear button on the recording frame and either choose a window from the list or use “Select window by clicking”. The recording frame then shows the selected window's icon and title. Choose “Entire area under frame” in the same menu to return to normal region capture. Minimized windows are restored before recording; DRM/protected or hardware-only windows may reject this method, in which case Framio falls back to the screen region under the frame.

### Default hotkeys

| Action | Keys |
| --- | --- |
| Capture an area | `Ctrl+Shift+Print Screen` |
| Quick screenshot of all screens | `Ctrl+Print Screen` |
| Record the whole desktop | `Ctrl+Shift+F9` |
| Stop active recording | `Ctrl+Shift+F10` |
| Copy selected zones | `Ctrl+C` |
| Save selected zones | `Ctrl+S` |
| Undo | `Ctrl+Z` |
| Redo | `Ctrl+Y` |
| Close capture or cancel recording | `Esc` |

Change hotkeys in “Settings → Hotkeys”. Tray menu labels use the current settings.

### Capture mask

After creating a rectangular zone, choose “Capture area mask” on the right and select a freeform contour, rectangle, or oval. A freeform contour closes automatically when the mouse is released. The mask can be moved, resized, and rotated with the standard transform handles. PNG keeps transparent pixels outside the mask; MP4/GIF use black pixels outside it.

### Portable build

There are two portable formats:

- `dist-onefile/Framio.exe` — one self-contained file. PyInstaller extracts Qt, OpenCV and FFmpeg to a temporary internal directory at startup and removes it later. No DLL copying is required. Startup is a little slower and the file is about 126 MiB.
- `dist-lite/Framio.exe` — a smaller one-file build of about 97 MiB. It omits the large FFmpeg binary; regular MP4/GIF recording still uses OpenCV/Pillow, while built-in audio muxing and FFmpeg post-processing are unavailable.
- `dist/Framio/Framio.exe` — a folder build. It starts faster, but the complete directory including `_internal` must be moved together.

Both formats create `settings.json` and `Captures/Screenshots`, `Captures/Videos`, `Captures/GIFs` next to the executable. Portable paths are rewritten when the directory is moved.

The `.github/workflows/build.yml` workflow builds all three formats on Windows and publishes `Framio-windows`, `Framio-windows-single-file`, and `Framio-windows-single-file-lite` artifacts.

Run from source:

```powershell
pip install -r requirements.txt
python main.py
```

Run tests:

```powershell
python -u tests/test_components.py
```

Build the portable package:

```powershell
python -m PyInstaller --noconfirm Framio.spec
```

The result is written to `dist/Framio/`.

## License

[Creative Commons Attribution-NonCommercial 4.0 International (CC BY-NC 4.0)](LICENSE)
