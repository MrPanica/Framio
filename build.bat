@echo off
chcp 65001 >nul
echo [Framio] Сборка приложения...
python -m PyInstaller --noconfirm Framio.spec
if %ERRORLEVEL% equ 0 (
    echo [Framio] Сборка успешно завершена: dist\Framio\Framio.exe
) else (
    echo [Framio] Ошибка при сборке!
)
pause
