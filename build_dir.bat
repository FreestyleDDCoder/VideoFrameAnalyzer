@echo off
title VideoFrameAnalyzer Build

echo ============================================
echo  Video Frame Analyzer - Build
echo ============================================
echo.

echo [1/3] Checking Python...
python --version
if errorlevel 1 goto :no_python

echo.
echo [2/3] Checking ffprobe...
where ffprobe >nul 2>&1
if errorlevel 1 (
    echo [ERROR] ffprobe not found in PATH!
    echo Install ffprobe and add to PATH first.
    pause
    exit /b 1
)

for /f "delims=" %%i in ('where ffprobe') do set FFPROBE_PATH=%%i
echo Found: %FFPROBE_PATH%

echo.
echo [3/3] Installing PyInstaller...
pip install pyinstaller
echo.

echo [4/4] Building EXE...
pyinstaller --noconfirm --onedir --windowed ^
  --add-binary "%FFPROBE_PATH%;." ^
  --name "VideoFrameAnalyzer" ^
  --distpath ./output ^
  --workpath ./build_temp ^
  --specpath ./build_temp ^
  --clean ^
  gui_analyzer.py
if errorlevel 1 goto :build_fail

echo.
echo Copying extra files...
if exist icon.ico copy icon.ico output\VideoFrameAnalyzer\icon.ico

echo.
echo ============================================
echo  DONE!
echo  Output: output\VideoFrameAnalyzer\VideoFrameAnalyzer.exe
echo  (ffmpeg bundled - no install required)
echo ============================================
echo.

if exist build_temp rd /s /q build_temp

pause
explorer output\VideoFrameAnalyzer
exit /b 0

:no_python
echo [ERROR] Python not found!
pause
exit /b 1

:build_fail
echo [ERROR] Build failed!
pause
exit /b 1
