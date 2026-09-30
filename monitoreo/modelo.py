"""Modelo de datos, esquemas de formularios y persistencia."""

from __future__ import annotations

import calendar
import json
import sys
import threading
import uuid
from dataclasses import asdict, dataclass, field, fields
from datetime import date, datetime, time, timedelta
from pathlib import Path

# En un .exe (PyInstaller) los datos se guardan junto al ejecutable.
CARPETA = (Path(sys.executable).parent if getattr(sys, "frozen", False)
           else Path(__file__).resolve().parent.parent)
ARCHIVO_DATOS = CARPETA / "datos_monitoreo.json"
ARCHIVO_CACHE_OSRM = CARPETA / "cache_osrm.json"
CARPETA_SALIDAS = CARPETA / "salidas"
CARPETA_PROGRAMACIONES = CARPETA / "programaciones"

UMBRAL_SNAP_M = 500  # avisar si un punto queda a más de esto de la vía más cercana
DIAS_ALERTA = 30     # anticipación de las alertas de vencimiento
MATRICES = ["agua", "aire", "suelo", "ruido", "sedimento", "biologico", "efluente"]
DIAS_SEMANA = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]
# Multiplicador de "costo" para elegir la siguiente tarea: menor = se atiende antes.
# 0 = retiro de equipos instalados (no puede quedar pendiente).
PESO_PRIORIDAD = {0: 0.3, 1: 0.5, 2: 1.0, 3: 1.5}
# Frecuencia -> (meses, días) entre monitoreos; None = monitoreo único.
FRECUENCIAS = {"unica": None, "semanal": (0, 7), "quincenal": (0, 15), "mensual": (1, 0),
               "bimestral": (2, 0), "trimestral": (3, 0), "semestral": (6, 0), "anual": (12, 0)}
ESTADOS_TAREA = {"programado": "Programado", "ejecutado": "Ejecutado", "reprogramar": "Reprogramar",
                 "no_accesible": "No accesible", "cancelado": "Cancelado"}
ESTADOS_PLAN = {"borrador": "Borrador", "aprobado": "Aprobado", "cerrado": "Cerrado"}
SI = {"si", "sí", "s", "1", "true", "x", "yes", "y"}

# Nodos especiales de la red de distancias.
BASE = "__BASE__"
LAB = "__LAB__"
PREF_ALOJ = "@"       # "@<nombre del alojamiento>"
PREF_CAMP = "CAMP:"   # "CAMP:<código de punto>" = campamento en ese punto

candado = threading.RLock()  # la interfaz web atiende peticiones en paralelo


def vigente(hasta: str, dia: date) -> bool:
    return not hasta or date.fromisoformat(hasta) >= dia


def sumar_meses(d: date, n: int) -> date:
    m = d.month - 1 + n
    y, m = d.year + m // 12, m % 12 + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def parse_hora(s: str) -> time:
    h, m = s.strip().split(":")
    return time(int(h), int(m))


def fmt_min(minutos: float) -> str:
    minutos = int(round(minutos))
    return f"{minutos // 60}h{minutos % 60:02d}"


# --------------------------------------------------------------------------- #
# Entidades
# --------------------------------------------------------------------------- #
@dataclass
class Persona:
    nombre: str
    cargo: str = "Técnico"
    competencias: list[str] = field(default_factory=list)
    conduce: bool = False
    email: str = ""
    telefono: str = ""
    activo: bool = True
    habilitado_hasta: str = ""  # vencimiento de EMO / SCTR / inducción (AAAA-MM-DD)
    no_disponible: list[str] = field(default_factory=list)  # fechas ISO

    def disponible(self, dia: date) -> bool:
        return self.activo and dia.isoformat() not in self.no_disponible and vigente(self.habilitado_hasta, dia)


@dataclass
class Vehiculo:
    placa: str
    descripcion: str = "Camioneta"
    capacidad: int = 4
    es_4x4: bool = True
    velocidad_kmh: float = 45.0
    rendimiento_km_l: float = 10.0
    costo_dia: float = 0.0          # alquiler / depreciación por día de uso
    activo: bool = True
    documentos_hasta: str = ""      # vencimiento más próximo de SOAT / revisión técnica
    no_disponible: list[str] = field(default_factory=list)

    def disponible(self, dia: date) -> bool:
        return self.activo and dia.isoformat() not in self.no_disponible and vigente(self.documentos_hasta, dia)


@dataclass
class Equipo:
    codigo: str
    tipo: str                       # p. ej. multiparametro, sonometro, muestreador_pm10
    descripcion: str = ""
    calibracion_vence: str = ""
    activo: bool = True
    no_disponible: list[str] = field(default_factory=list)

    def disponible(self, dia: date) -> bool:
        return self.activo and dia.isoformat() not in self.no_disponible and vigente(self.calibracion_vence, dia)


@dataclass
class Punto:
    codigo: str
    nombre: str
    lat: float
    lon: float
    matriz: str = "agua"
    duracion_min: int = 60          # muestreo, o instalación si hay exposición
    requiere_4x4: bool = False
    acceso_min: int = 0             # caminata ida+vuelta desde donde se deja el vehículo
    prioridad: int = 2              # 1 alta, 2 media, 3 baja
    personas_min: int = 2
    activo: bool = True
    hora_desde: str = ""            # ventana horaria para muestrear ("" = sin límite)
    hora_hasta: str = ""
    max_horas_preservacion: float = 0   # horas máx. desde la toma hasta la entrega (0 = sin límite)
    entrega_laboratorio: bool = False   # la muestra se entrega al laboratorio el mismo día de retorno
    equipos: list[str] = field(default_factory=list)  # tipos de equipo necesarios
    horas_exposicion: float = 0     # > 0: se instala y se retira después (p. ej. 24 h)
    duracion_retiro_min: int = 30
    frecuencia: str = "unica"
    ultimo_monitoreo: str = ""

    def proxima_fecha(self) -> date | None:
        """Fecha del siguiente monitoreo; date.min = nunca realizado; None = único ya realizado."""
        if not self.ultimo_monitoreo:
            return date.min
        f = FRECUENCIAS.get(self.frecuencia)
        if f is None:
            return None
        return sumar_meses(date.fromisoformat(self.ultimo_monitoreo), f[0]) + timedelta(days=f[1])


@dataclass
class Alojamiento:
    nombre: str
    lat: float
    lon: float
    costo_noche_persona: float = 0.0
    observaciones: str = ""
    activo: bool = True


@dataclass
class Configuracion:
    base_nombre: str = "Base de operaciones"
    base_lat: float = 7.07714
    base_lon: float = -73.85512
    hora_inicio: str = "07:00"
    hora_fin: str = "17:00"
    preparacion_min: int = 30      # carga de equipos, calibración, charla de seguridad
    descarga_min: int = 30         # descarga, preservación y registro de muestras al volver
    hora_almuerzo: str = "12:30"
    almuerzo_min: int = 45
    dias_laborables: list[int] = field(default_factory=lambda: [0, 1, 2, 3, 4, 5])
    usar_osrm: bool = True
    osrm_url: str = "https://router.project-osrm.org"
    factor_tiempo_osrm: float = 1.2  # OSRM asume auto a velocidad legal; ajuste por tráfico/camioneta
    factor_ruta: float = 1.35      # distancia real / línea recta (solo si no hay ruta OSRM)
    tamano_cuadrilla: int = 3      # personas por cuadrilla (incluye conductor)
    max_dias_consecutivos: int = 6
    usar_laboratorio: bool = False
    lab_nombre: str = "Laboratorio"
    lab_lat: float = 0.0
    lab_lon: float = 0.0
    lab_hora_desde: str = "08:00"
    lab_hora_hasta: str = "17:00"
    lab_dias: list[int] = field(default_factory=lambda: [0, 1, 2, 3, 4])
    lab_recepcion_min: int = 20
    permitir_pernocte: bool = False
    max_noches: int = 3
    max_min_a_alojamiento: int = 90
    permitir_campamento: bool = False
    costo_campamento_persona: float = 40000.0
    preparacion_campo_min: int = 20  # desayuno y alistamiento al salir del alojamiento
    moneda: str = "COP $"
    precio_combustible: float = 2850.0  # por litro (ACPM ≈ 10.800 COP/galón)
    viatico_persona_dia: float = 120000.0


# --------------------------------------------------------------------------- #
# Esquemas de formulario (los usan la consola y la interfaz web)
# --------------------------------------------------------------------------- #
@dataclass
class Campo:
    nombre: str
    etiqueta: str
    tipo: str = "text"   # text,int,float,bool,date,time,dates,multi,tags,select,dias
    opciones: list | None = None
    avanzado: bool = False
    requerido: bool = False
    ayuda: str = ""
    seccion: str = ""


PRIORIDADES = [[1, "1 - Alta"], [2, "2 - Media"], [3, "3 - Baja"]]

ESQUEMAS: dict[str, dict] = {
    "personal": {"titulo": "Personal", "cls": Persona, "clave": "nombre", "campos": [
        Campo("nombre", "Nombre", requerido=True),
        Campo("cargo", "Cargo"),
        Campo("competencias", "Competencias (matrices)", "multi", MATRICES),
        Campo("conduce", "Conduce", "bool"),
        Campo("email", "Correo (para el calendario)"),
        Campo("telefono", "Teléfono"),
        Campo("habilitado_hasta", "Habilitación EMO/SCTR/inducción hasta", "date"),
        Campo("no_disponible", "Días no disponible", "dates"),
        Campo("activo", "Activo", "bool"),
    ]},
    "vehiculos": {"titulo": "Vehículos", "cls": Vehiculo, "clave": "placa", "campos": [
        Campo("placa", "Placa", requerido=True),
        Campo("descripcion", "Descripción"),
        Campo("capacidad", "Capacidad (personas)", "int"),
        Campo("es_4x4", "4x4", "bool"),
        Campo("velocidad_kmh", "Velocidad promedio km/h (sin OSRM)", "float"),
        Campo("rendimiento_km_l", "Rendimiento km/L", "float"),
        Campo("costo_dia", "Costo por día de uso", "float"),
        Campo("documentos_hasta", "SOAT / revisión técnica hasta", "date"),
        Campo("no_disponible", "Días no disponible (mantenimiento)", "dates"),
        Campo("activo", "Activo", "bool"),
    ]},
    "equipos": {"titulo": "Equipos", "cls": Equipo, "clave": "codigo", "campos": [
        Campo("codigo", "Código / serie", requerido=True),
        Campo("tipo", "Tipo", requerido=True, ayuda="p. ej. multiparametro, sonometro, muestreador_pm10"),
        Campo("descripcion", "Descripción / modelo"),
        Campo("calibracion_vence", "Calibración vigente hasta", "date"),
        Campo("no_disponible", "Días no disponible", "dates"),
        Campo("activo", "Activo", "bool"),
    ]},
    "puntos": {"titulo": "Puntos de monitoreo", "cls": Punto, "clave": "codigo", "campos": [
        Campo("codigo", "Código", requerido=True),
        Campo("nombre", "Nombre / descripción", requerido=True),
        Campo("lat", "Latitud", "float", requerido=True),
        Campo("lon", "Longitud", "float", requerido=True),
        Campo("matriz", "Matriz", "select", MATRICES),
        Campo("duracion_min", "Duración muestreo / instalación (min)", "int"),
        Campo("requiere_4x4", "Requiere 4x4", "bool"),
        Campo("acceso_min", "Caminata ida+vuelta (min)", "int"),
        Campo("prioridad", "Prioridad", "select", PRIORIDADES),
        Campo("personas_min", "Personas mínimas", "int"),
        Campo("frecuencia", "Frecuencia", "select", list(FRECUENCIAS)),
        Campo("ultimo_monitoreo", "Último monitoreo", "date"),
        Campo("activo", "Activo", "bool"),
        Campo("hora_desde", "Muestrear desde", "time", avanzado=True),
        Campo("hora_hasta", "Muestrear hasta", "time", avanzado=True),
        Campo("max_horas_preservacion", "Horas máx. hasta entrega (0 = sin límite)", "float", avanzado=True),
        Campo("entrega_laboratorio", "Entregar al laboratorio", "bool", avanzado=True),
        Campo("equipos", "Tipos de equipo necesarios", "tags", avanzado=True),
        Campo("horas_exposicion", "Horas de exposición (0 = puntual)", "float", avanzado=True,
              ayuda="> 0: se instala el equipo y se retira después (p. ej. PM10 24 h)"),
        Campo("duracion_retiro_min", "Duración del retiro (min)", "int", avanzado=True),
    ]},
    "alojamientos": {"titulo": "Alojamientos", "cls": Alojamiento, "clave": "nombre", "campos": [
        Campo("nombre", "Nombre", requerido=True),
        Campo("lat", "Latitud", "float", requerido=True),
        Campo("lon", "Longitud", "float", requerido=True),
        Campo("costo_noche_persona", "Costo por persona y noche", "float"),
        Campo("observaciones", "Observaciones"),
        Campo("activo", "Activo", "bool"),
    ]},
}

ESQUEMA_CONFIG: list[Campo] = [
    Campo("base_nombre", "Nombre de la base", seccion="Base y jornada"),
    Campo("base_lat", "Latitud base", "float", seccion="Base y jornada"),
    Campo("base_lon", "Longitud base", "float", seccion="Base y jornada"),
    Campo("hora_inicio", "Hora de salida", "time", requerido=True, seccion="Base y jornada"),
    Campo("hora_fin", "Fin de jornada", "time", requerido=True, seccion="Base y jornada"),
    Campo("preparacion_min", "Preparación en base (min)", "int", seccion="Base y jornada"),
    Campo("descarga_min", "Descarga y registro al volver (min)", "int", seccion="Base y jornada"),
    Campo("hora_almuerzo", "Hora de almuerzo", "time", requerido=True, seccion="Base y jornada"),
    Campo("almuerzo_min", "Duración almuerzo (min)", "int", seccion="Base y jornada"),
    Campo("dias_laborables", "Días laborables", "dias", seccion="Base y jornada"),
    Campo("usar_osrm", "Usar OSRM (rutas reales por carretera)", "bool", seccion="Tiempos de traslado"),
    Campo("osrm_url", "Servidor OSRM", seccion="Tiempos de traslado"),
    Campo("factor_tiempo_osrm", "Factor sobre tiempos OSRM", "float", seccion="Tiempos de traslado",
          ayuda="1.0 = tal cual; 1.2–1.5 por tráfico o trocha"),
    Campo("factor_ruta", "Factor de ruta sin OSRM", "float", seccion="Tiempos de traslado",
          ayuda="1.2 llano – 1.6 sierra/trocha"),
    Campo("tamano_cuadrilla", "Personas por cuadrilla", "int", seccion="Cuadrillas"),
    Campo("max_dias_consecutivos", "Máx. días de campo seguidos", "int", seccion="Cuadrillas"),
    Campo("usar_laboratorio", "Entregar muestras al laboratorio", "bool", seccion="Laboratorio"),
    Campo("lab_nombre", "Nombre del laboratorio", seccion="Laboratorio"),
    Campo("lab_lat", "Latitud laboratorio", "float", seccion="Laboratorio"),
    Campo("lab_lon", "Longitud laboratorio", "float", seccion="Laboratorio"),
    Campo("lab_hora_desde", "Recepción desde", "time", requerido=True, seccion="Laboratorio"),
    Campo("lab_hora_hasta", "Recepción hasta", "time", requerido=True, seccion="Laboratorio"),
    Campo("lab_dias", "Días de recepción", "dias", seccion="Laboratorio"),
    Campo("lab_recepcion_min", "Tiempo de entrega (min)", "int", seccion="Laboratorio"),
    Campo("permitir_pernocte", "Permitir salidas de varios días", "bool", seccion="Pernocte"),
    Campo("max_noches", "Máximo de noches por salida", "int", seccion="Pernocte"),
    Campo("max_min_a_alojamiento", "Máx. minutos hasta el alojamiento", "int", seccion="Pernocte"),
    Campo("permitir_campamento", "Permitir campamento si no hay alojamiento", "bool", seccion="Pernocte"),
    Campo("costo_campamento_persona", "Costo campamento por persona/noche", "float", seccion="Pernocte"),
    Campo("preparacion_campo_min", "Alistamiento al salir del alojamiento (min)", "int", seccion="Pernocte"),
    Campo("moneda", "Moneda", seccion="Costos"),
    Campo("precio_combustible", "Precio combustible por litro", "float", seccion="Costos"),
    Campo("viatico_persona_dia", "Viático por persona y día", "float", seccion="Costos"),
]


def esquemas_json() -> dict:
    return {"entidades": {k: {"titulo": v["titulo"], "clave": v["clave"],
                              "campos": [asdict(c) for c in v["campos"]]} for k, v in ESQUEMAS.items()},
            "config": [asdict(c) for c in ESQUEMA_CONFIG]}


def _crear(cls, d: dict):
    """Crea el objeto ignorando campos desconocidos (compatibilidad entre versiones)."""
    nombres = {f.name for f in fields(cls)}
    return cls(**{k: v for k, v in d.items() if k in nombres})


def normalizar(obj) -> None:
    """Valida y normaliza un registro; lanza ValueError si no es válido."""
    if isinstance(obj, Punto):
        obj.codigo, obj.matriz = obj.codigo.strip().upper(), obj.matriz.lower()
        if not obj.codigo or obj.codigo.startswith((PREF_ALOJ, PREF_CAMP)) or obj.codigo.startswith("__"):
            raise ValueError(f"código de punto no válido: '{obj.codigo}'")
        if obj.matriz not in MATRICES:
            raise ValueError(f"matriz '{obj.matriz}' no válida")
        if obj.frecuencia not in FRECUENCIAS:
            raise ValueError(f"frecuencia '{obj.frecuencia}' no válida")
        obj.lat, obj.lon = float(obj.lat), float(obj.lon)
        obj.equipos = [e.strip().lower() for e in obj.equipos if e.strip()]
        for h in (obj.hora_desde, obj.hora_hasta):
            if h:
                parse_hora(h)
    elif isinstance(obj, Vehiculo):
        obj.placa = obj.placa.strip().upper()
    elif isinstance(obj, Equipo):
        obj.codigo, obj.tipo = obj.codigo.strip().upper(), obj.tipo.strip().lower()
    elif isinstance(obj, Persona):
        obj.nombre = obj.nombre.strip()
        obj.competencias = [c for c in obj.competencias if c in MATRICES]
    elif isinstance(obj, Alojamiento):
        obj.nombre = obj.nombre.strip()
        obj.lat, obj.lon = float(obj.lat), float(obj.lon)
    for f in fields(obj):
        v = getattr(obj, f.name)
        if f.type in ("str",) and isinstance(v, str):
            setattr(obj, f.name, v.strip())
    for fecha in [getattr(obj, n) for n in ("habilitado_hasta", "documentos_hasta", "calibracion_vence",
                                            "ultimo_monitoreo") if hasattr(obj, n)]:
        if fecha:
            date.fromisoformat(fecha)


# --------------------------------------------------------------------------- #
# Repositorio
# --------------------------------------------------------------------------- #
class Repositorio:
    def __init__(self, ruta: Path = ARCHIVO_DATOS):
        self.ruta = ruta
        self.personal: list[Persona] = []
        self.vehiculos: list[Vehiculo] = []
        self.equipos: list[Equipo] = []
        self.puntos: list[Punto] = []
        self.alojamientos: list[Alojamiento] = []
        self.config = Configuracion()
        self.cargar()

    def lista(self, entidad: str) -> list:
        return getattr(self, entidad)

    def cargar(self) -> None:
        if not self.ruta.exists():
            return
        d = json.loads(self.ruta.read_text(encoding="utf-8"))
        for ent, esq in ESQUEMAS.items():
            setattr(self, ent, [_crear(esq["cls"], x) for x in d.get(ent, [])])
        self.config = _crear(Configuracion, d.get("config", {}))

    def guardar(self) -> None:
        d = {ent: [asdict(x) for x in self.lista(ent)] for ent in ESQUEMAS}
        d["config"] = asdict(self.config)
        tmp = self.ruta.with_suffix(".tmp")
        tmp.write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.ruta)

    def tipos_equipo(self) -> list[str]:
        return sorted({e.tipo for e in self.equipos})

    def punto(self, codigo: str) -> Punto | None:
        return next((p for p in self.puntos if p.codigo == codigo), None)

    def alojamiento(self, nombre: str) -> Alojamiento | None:
        return next((a for a in self.alojamientos if a.nombre == nombre), None)

    # --- Programaciones ------------------------------------------------------
    @staticmethod
    def nuevo_id() -> str:
        return datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]

    def listar_programaciones(self) -> list[dict]:
        if not CARPETA_PROGRAMACIONES.exists():
            return []
        salida = []
        for a in CARPETA_PROGRAMACIONES.glob("*.json"):
            try:
                salida.append(json.loads(a.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                pass
        return sorted(salida, key=lambda d: d.get("creado", ""), reverse=True)

    def leer_programacion(self, pid: str) -> dict | None:
        ruta = CARPETA_PROGRAMACIONES / f"{Path(pid).name}.json"
        return json.loads(ruta.read_text(encoding="utf-8")) if ruta.exists() else None

    def guardar_programacion(self, d: dict) -> None:
        CARPETA_PROGRAMACIONES.mkdir(exist_ok=True)
        d["modificado"] = datetime.now().isoformat(timespec="seconds")
        ruta = CARPETA_PROGRAMACIONES / f"{Path(d['id']).name}.json"
        ruta.write_text(json.dumps(d, indent=1, ensure_ascii=False), encoding="utf-8")

    def eliminar_programacion(self, pid: str) -> None:
        (CARPETA_PROGRAMACIONES / f"{Path(pid).name}.json").unlink(missing_ok=True)

    def aplicar_registro(self, d: dict) -> int:
        """Registra como último monitoreo la fecha de cada tarea ejecutada. Devuelve cuántos puntos cambió."""
        cambios = 0
        for dia in d.get("dias", []):
            for t in dia.get("tareas", []):
                if t.get("estado") != "ejecutado" or t.get("tipo") == "retiro":
                    continue
                p = self.punto(t["codigo"])
                if p and (not p.ultimo_monitoreo or p.ultimo_monitoreo < dia["fecha"]):
                    p.ultimo_monitoreo = dia["fecha"]
                    cambios += 1
        if cambios:
            self.guardar()
        return cambios


def calcular_alertas(repo: Repositorio, hoy: date | None = None) -> dict:
    hoy = hoy or date.today()
    limite = hoy + timedelta(days=DIAS_ALERTA)

    def estado(hasta: str) -> tuple[str, str] | None:
        if not hasta:
            return None
        f = date.fromisoformat(hasta)
        if f < hoy:
            return "vencido", f"VENCIDO el {f:%d/%m/%Y}"
        if f <= limite:
            return "proximo", f"vence en {(f - hoy).days} días ({f:%d/%m/%Y})"
        return None

    venc = []
    for grupo, lista, que, attr, ident in (
            ("Personal", repo.personal, "Habilitación (EMO/SCTR/inducción)", "habilitado_hasta", "nombre"),
            ("Vehículo", repo.vehiculos, "SOAT / revisión técnica", "documentos_hasta", "placa"),
            ("Equipo", repo.equipos, "Calibración", "calibracion_vence", "codigo")):
        for x in lista:
            if x.activo and (e := estado(getattr(x, attr))):
                venc.append({"grupo": grupo, "quien": getattr(x, ident), "que": que,
                             "nivel": e[0], "texto": e[1]})
    puntos = []
    for p in repo.puntos:
        if not p.activo:
            continue
        f = p.proxima_fecha()
        if f is None:
            nivel, texto, orden = "ok", "realizado (monitoreo único)", date.max
        elif f == date.min:
            nivel, texto, orden = "vencido", "PENDIENTE (sin registro previo)", f
        else:
            d = (f - hoy).days
            orden = f
            if d < 0:
                nivel, texto = "vencido", f"VENCIDO hace {-d} días"
            elif d == 0:
                nivel, texto = "vencido", "vence HOY"
            elif d <= DIAS_ALERTA:
                nivel, texto = "proximo", f"vence en {d} días ({f:%d/%m/%Y})"
            else:
                nivel, texto = "ok", f"vence el {f:%d/%m/%Y}"
        puntos.append({"codigo": p.codigo, "nombre": p.nombre, "matriz": p.matriz, "frecuencia": p.frecuencia,
                       "ultimo": p.ultimo_monitoreo, "nivel": nivel, "texto": texto, "_orden": orden})
    puntos.sort(key=lambda x: x.pop("_orden"))
    return {"vencimientos": venc, "puntos": puntos}
