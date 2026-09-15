@echo off
title Install QMS
echo.
echo  ==========================================
echo   QMS - SO Fulfilment  ^|  Windows installer
echo  ==========================================
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install-windows.ps1" %*
echo.
echo  Finished. If you saw errors above, read INSTALL.md.
pause
