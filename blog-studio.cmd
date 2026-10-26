@echo off
setlocal
chcp 65001 >nul
where py >nul 2>nul
if not errorlevel 1 (
    py -3 "%~dp0start.py" %*
    goto done
)
where python >nul 2>nul
if not errorlevel 1 (
    python "%~dp0start.py" %*
    goto done
)
echo Blog Studio requires Python 3.10 or newer. Install Python from https://www.python.org/downloads/
pause
exit /b 1
:done
if errorlevel 1 (
    echo.
    echo Blog Studio stopped. Review the message above.
    pause
    exit /b 1
)
endlocal
