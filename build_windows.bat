@echo off
setlocal

set BUILD_MODE=%~1
if "%BUILD_MODE%"=="" set BUILD_MODE=portable

if /I "%BUILD_MODE%"=="standard" (
  set EXE_NAME=TwitchChannelPointsMiner
) else if /I "%BUILD_MODE%"=="portable" (
  set EXE_NAME=TwitchChannelPointsMiner-Portable
) else (
  echo Unknown build mode "%BUILD_MODE%" - expected "standard" or "portable".
  exit /b 1
)

rem Baked into the bundle at build time (read back via windows_launcher.py's
rem bundled_file()) so standard-vs-portable behavior is fixed at build time,
rem not inferred from anything the end user could change, like the exe's own
rem filename - renaming a file is trivial and would otherwise silently flip
rem behavior.
echo %BUILD_MODE%> install_mode.txt

py -m PyInstaller ^
  --clean ^
  --noconfirm ^
  --onefile ^
  --windowed ^
  --name %EXE_NAME% ^
  --icon "assets\twitch-miner.ico" ^
  --collect-all TwitchChannelPointsMiner ^
  --collect-all webview ^
  --hidden-import PIL.IcoImagePlugin ^
  --hidden-import PIL.PngImagePlugin ^
  --add-data "assets;assets" ^
  --add-data "config.example.py;." ^
  --add-data "install_mode.txt;." ^
  windows_launcher.py

set BUILD_RESULT=%ERRORLEVEL%
del install_mode.txt
if not %BUILD_RESULT%==0 exit /b %BUILD_RESULT%

echo.
echo Built dist\%EXE_NAME%.exe
