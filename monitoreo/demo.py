"""Datos de ejemplo (Lima y sierra de Lima)."""

from __future__ import annotations

from datetime import date, timedelta

from .modelo import Alojamiento, Configuracion, Equipo, Persona, Punto, Repositorio, Vehiculo


def cargar_demo(repo: Repositorio) -> None:
    hoy = date.today()
    en = lambda dias: (hoy + timedelta(days=dias)).isoformat()
    repo.config = Configuracion(
        base_nombre="Base Lima", base_lat=-12.0464, base_lon=-77.0428,
        usar_laboratorio=True, lab_nombre="Laboratorio acreditado (San Isidro)",
        lab_lat=-12.0970, lab_lon=-77.0365, lab_hora_desde="08:00", lab_hora_hasta="18:00",
        permitir_pernocte=True, max_noches=2)
    mail = lambda n: n.lower().replace(" ", ".").replace("í", "i").replace("á", "a") + "@example.com"
    repo.personal = [
        Persona("Ana Quispe", "Especialista", ["agua", "sedimento", "biologico"], email=mail("ana quispe"),
                habilitado_hasta=en(180)),
        Persona("Luis Rojas", "Técnico", ["agua", "suelo"], conduce=True, email=mail("luis rojas"),
                habilitado_hasta=en(12)),
        Persona("Carla Mendoza", "Especialista", ["aire", "ruido"], email=mail("carla mendoza"),
                habilitado_hasta=en(200)),
        Persona("Jorge Salas", "Conductor", ["ruido"], conduce=True, email=mail("jorge salas"),
                habilitado_hasta=en(300)),
        Persona("María Torres", "Técnico", ["agua", "efluente", "suelo"], email=mail("maria torres"),
                habilitado_hasta=en(150)),
        Persona("Pedro Huamán", "Técnico", ["aire", "suelo"], conduce=True, email=mail("pedro huaman"),
                habilitado_hasta=en(90)),
        Persona("Rosa Flores", "Técnico", ["agua", "aire"], email=mail("rosa flores"),
                no_disponible=[en(2)], habilitado_hasta=en(60)),
    ]
    repo.vehiculos = [
        Vehiculo("ABC-123", "Hilux 4x4", 5, True, 45, 9.5, 250, documentos_hasta=en(120)),
        Vehiculo("XYZ-789", "Camioneta 4x2", 4, False, 55, 11.0, 180, documentos_hasta=en(20)),
    ]
    repo.equipos = [
        Equipo("MULTI-01", "multiparametro", "YSI ProDSS", en(90)),
        Equipo("MULTI-02", "multiparametro", "Hanna HI98194", en(40)),
        Equipo("SON-01", "sonometro", "Clase 1", en(200)),
        Equipo("SON-02", "sonometro", "Clase 1", en(-5)),
        Equipo("PM10-01", "muestreador_pm10", "Hi-Vol PM10", en(100)),
        Equipo("PM10-02", "muestreador_pm10", "Hi-Vol PM10", en(25)),
        Equipo("BARR-01", "barreno", "Barreno manual acero inox.", ""),
    ]
    repo.alojamientos = [
        Alojamiento("Hotel Churín", -10.8125, -76.8747, 70, "Reserva con 48 h de anticipación"),
    ]
    agua = ["multiparametro"]
    repo.puntos = [
        Punto("AG-01", "Río Rímac - Chosica", -11.9386, -76.6970, "agua", 60, False, 10, 1, equipos=agua,
              max_horas_preservacion=8, entrega_laboratorio=True, frecuencia="mensual",
              ultimo_monitoreo=en(-35)),
        Punto("AG-02", "Río Rímac - Ricardo Palma", -11.9180, -76.6600, "agua", 60, False, 5, 1,
              equipos=agua, entrega_laboratorio=True, frecuencia="mensual", ultimo_monitoreo=en(-28)),
        Punto("AG-03", "Quebrada Santa Eulalia", -11.8950, -76.6650, "agua", 50, True, 25, 2,
              equipos=agua, frecuencia="trimestral"),
        Punto("AG-04", "Río Chillón - Trapiche", -11.8130, -76.9900, "agua", 60, True, 15, 2,
              equipos=agua, frecuencia="mensual", ultimo_monitoreo=en(-40)),
        Punto("SD-01", "Sedimento Rímac - Huachipa", -12.0170, -76.9300, "sedimento", 45, False, 10, 2,
              frecuencia="semestral"),
        Punto("EF-01", "Efluente planta industrial", -12.0600, -77.1200, "efluente", 40, False, 0, 1,
              equipos=agua, hora_desde="09:00", hora_hasta="12:00", frecuencia="mensual",
              ultimo_monitoreo=en(-31)),
        Punto("AI-01", "Calidad de aire PM10 - Ate", -12.0260, -76.9180, "aire", 45, False, 0, 1,
              equipos=["muestreador_pm10"], horas_exposicion=24, duracion_retiro_min=30,
              frecuencia="trimestral"),
        Punto("AI-02", "Calidad de aire PM10 - Callao", -12.0500, -77.1300, "aire", 45, False, 0, 2,
              equipos=["muestreador_pm10"], horas_exposicion=24, duracion_retiro_min=30,
              frecuencia="trimestral"),
        Punto("RU-01", "Ruido ambiental - Cercado", -12.0550, -77.0350, "ruido", 30, False, 0, 3,
              equipos=["sonometro"], hora_desde="07:00", hora_hasta="22:00", frecuencia="semestral"),
        Punto("RU-02", "Ruido ambiental - Villa El Salvador", -12.2130, -76.9370, "ruido", 30, False, 0, 3,
              equipos=["sonometro"], frecuencia="semestral"),
        Punto("SU-01", "Suelo - Lurín", -12.2750, -76.8700, "suelo", 60, True, 20, 2,
              equipos=["barreno"], frecuencia="anual"),
        Punto("SU-02", "Suelo - Pachacámac", -12.2300, -76.8600, "suelo", 60, False, 10, 2,
              equipos=["barreno"], frecuencia="anual"),
        Punto("AG-05", "Río Lurín - Cieneguilla", -12.1100, -76.8100, "agua", 60, False, 10, 2,
              equipos=agua, frecuencia="mensual", ultimo_monitoreo=en(-10)),
        Punto("BI-01", "Hidrobiología - Lomas de Lúcumo", -12.2400, -76.8300, "biologico", 90, True, 30, 3,
              frecuencia="anual", ultimo_monitoreo=en(-380)),
        Punto("AG-06", "Río Huaura - Churín", -10.8090, -76.8800, "agua", 60, True, 15, 2,
              equipos=agua, frecuencia="trimestral"),
        Punto("SU-03", "Suelo agrícola - Pachangara", -10.7950, -76.8600, "suelo", 60, True, 20, 2,
              equipos=["barreno"], frecuencia="anual"),
    ]
    repo.guardar()
