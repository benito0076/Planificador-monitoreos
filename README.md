# Planificador de Monitoreos Ambientales

Programa jornadas de campo para monitoreos ambientales: forma cuadrillas, ordena las rutas, simula cada jornada (traslados, almuerzo, laboratorio, pernocte) y calcula costos. Funciona por consola, con interfaz web local o como `.exe` de Windows.

## Uso

```bash
python main.py           # menú de consola
python main.py --web     # interfaz gráfica en el navegador
python main.py --demo    # datos de ejemplo + programación + exportación
```

Dependencias opcionales: `openpyxl` (exportar a Excel) y `folium` (mapas). Todo lo demás usa solo la biblioteca estándar de Python 3.

## Qué hace

- **Cuadrillas**: 1 vehículo + 1 conductor + técnicos que cubran las matrices pendientes (agua, aire, suelo, ruido, sedimento, biológico, efluente), balanceando la carga y respetando el máximo de días seguidos.
- **Restricciones**: competencias, 4x4, personas mínimas, ventanas horarias, tiempo máximo de preservación de muestras, entrega al laboratorio y vigencia de habilitaciones, SOAT y calibraciones.
- **Equipos con exposición** (p. ej. PM10 24 h): se programa la instalación y el retiro posterior.
- **Pernocte**: salidas de varios días con alojamiento o campamento.
- **Rutas**: tiempos y distancias reales con OSRM (caché en `cache_osrm.json`) o por línea recta con un factor; mejora 2-opt.
- **Plan editable**: se puede modificar a mano y revalidar (los incumplimientos salen como avisos), con registro de campo (ejecutado, reprogramar, no accesible, cancelado) e indicadores de cumplimiento.
- **Alertas** de vencimientos y de puntos según su frecuencia.
- **Exportación** a Excel, CSV y calendarios `.ics` (general y por persona) en `salidas/`.

## Estructura

| Archivo | Contenido |
|---|---|
| `main.py` | Punto de entrada |
| `monitoreo/modelo.py` | Entidades, esquemas de formularios, persistencia, alertas |
| `monitoreo/planificador.py` | Cuadrillas, simulación de jornada, planificación y evaluación |
| `monitoreo/distancias.py` | Distancias y tiempos (OSRM / línea recta) |
| `monitoreo/reportes.py` | Exportación y reportes |
| `monitoreo/web.py`, `interfaz.html` | Interfaz web local |
| `monitoreo/consola.py`, `demo.py` | Menú de consola y datos de ejemplo |

## Datos

- `datos_monitoreo.json`: personal, vehículos, equipos, puntos, alojamientos y configuración.
- `programaciones/`: planes generados (un JSON por programación).
- `salidas/`: archivos exportados.

## Generar el ejecutable (Windows)

```bat
pip install pyinstaller folium openpyxl
construir_exe.bat
```

Crea `dist\PlanificadorMonitoreo.exe`; los datos se guardan junto al ejecutable.
