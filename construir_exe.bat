@echo off
rem Genera dist\PlanificadorMonitoreo.exe (requiere: pip install pyinstaller folium openpyxl)
cd /d "%~dp0"
python -m PyInstaller --onefile --console --name PlanificadorMonitoreo ^
  --distpath dist --workpath build --specpath build --noconfirm ^
  --add-data "%~dp0monitoreo\interfaz.html;monitoreo" ^
  --hidden-import folium --hidden-import openpyxl ^
  --collect-data folium --collect-data branca --collect-data xyzservices ^
  --exclude-module pandas --exclude-module matplotlib --exclude-module scipy ^
  --exclude-module PIL --exclude-module IPython --exclude-module tkinter ^
  --exclude-module geopandas --exclude-module shapely ^
  main.py
echo.
echo Listo: %~dp0dist\PlanificadorMonitoreo.exe
if "%1"=="" pause
