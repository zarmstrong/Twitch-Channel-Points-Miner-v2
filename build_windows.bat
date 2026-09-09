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

rem Also baked into the bundle - read back the same way, to show which
rem commit a running build was made from (startup log line, dashboard
rem footer). Always written (unlike being left absent) so the --add-data
rem source path below always exists, even when git isn't available or this
rem isn't a git checkout (e.g. a source archive) - falls back to "unknown"
rem in that case rather than failing the build.
set COMMIT_HASH=unknown
for /f %%i in ('git rev-parse --short HEAD 2^>nul') do set COMMIT_HASH=%%i
echo %COMMIT_HASH%> commit_hash.txt

py -m PyInstaller ^
  --clean ^
  --noconfirm ^
  --onefile ^
  --windowed ^
  --name %EXE_NAME% ^
  --icon "assets\twitch-miner.ico" ^
  --collect-all TwitchChannelPointsMiner ^
  --collect-all webview ^
  --collect-all pystray ^
  --hidden-import PIL.IcoImagePlugin ^
  --hidden-import PIL.PngImagePlugin ^
  --add-data "assets;assets" ^
  --add-data "config.example.py;." ^
  --add-data "install_mode.txt;." ^
  --add-data "commit_hash.txt;." ^
  windows_launcher.py

set BUILD_RESULT=%ERRORLEVEL%
del install_mode.txt
del commit_hash.txt
if not %BUILD_RESULT%==0 exit /b %BUILD_RESULT%

echo.
echo Built dist\%EXE_NAME%.exe
