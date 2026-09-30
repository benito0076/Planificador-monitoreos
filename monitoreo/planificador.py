"""Formación de cuadrillas, ruteo, simulación de jornadas y evaluación de planes."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from .distancias import Distancias, nodos, nombre_nodo
from .modelo import (BASE, LAB, PESO_PRIORIDAD, PREF_ALOJ, PREF_CAMP, Configuracion, Equipo,
                     Persona, Punto, Repositorio, Vehiculo, fmt_min, parse_hora)


# --------------------------------------------------------------------------- #
# Estructuras
# --------------------------------------------------------------------------- #
@dataclass(eq=False)  # identidad: dos tareas del mismo punto (instalación/retiro) son distintas
class Tarea:
    punto: Punto
    tipo: str = "muestreo"              # muestreo | instalacion | retiro
    no_antes: datetime | None = None    # retiro: fin de la exposición
    equipos_en_campo: list[str] = field(default_factory=list)  # retiro: códigos a recoger
    estado: str = "programado"          # registro de campo
    hora_real: str = ""
    observacion: str = ""

    @property
    def codigo(self) -> str:
        return self.punto.codigo

    @property
    def clave(self) -> str:
        return f"{self.codigo}:{self.tipo}"

    @property
    def duracion(self) -> int:
        return self.punto.duracion_retiro_min if self.tipo == "retiro" else self.punto.duracion_min

    @property
    def prioridad(self) -> int:
        return 0 if self.tipo == "retiro" else self.punto.prioridad

    @property
    def recoge_muestra(self) -> bool:
        return self.tipo in ("muestreo", "retiro")

    def etiqueta(self) -> str:
        p = self.punto
        if self.tipo == "instalacion":
            return f"Instalación de equipo de {p.matriz} ({p.horas_exposicion:g} h)"
        if self.tipo == "retiro":
            eq = f" [{', '.join(self.equipos_en_campo)}]" if self.equipos_en_campo else ""
            return f"Retiro de equipo y muestra de {p.matriz}{eq}"
        return f"Muestreo de {p.matriz}"


def tareas_iniciales(puntos: list[Punto]) -> list[Tarea]:
    return [Tarea(p, "instalacion" if p.horas_exposicion > 0 else "muestreo") for p in puntos]


@dataclass
class Cuadrilla:
    id: str
    vehiculo: Vehiculo
    integrantes: list[Persona]
    portatiles: dict[str, str] = field(default_factory=dict)        # tipo -> código de equipo
    a_instalar: list[tuple[str, str]] = field(default_factory=list)  # (código equipo, código punto)

    @property
    def competencias(self) -> set[str]:
        return {c for p in self.integrantes for c in p.competencias}

    @property
    def conductor(self) -> Persona | None:
        return next((p for p in self.integrantes if p.conduce), None)

    @property
    def equipos(self) -> list[str]:
        return sorted(self.portatiles.values()) + [c for c, _ in self.a_instalar]

    def motivo_incompatible(self, t: Tarea) -> str | None:
        p = t.punto
        if p.matriz not in self.competencias:
            return f"nadie en la cuadrilla tiene competencia en '{p.matriz}'"
        if p.requiere_4x4 and not self.vehiculo.es_4x4:
            return "requiere vehículo 4x4"
        if len(self.integrantes) < p.personas_min:
            return f"requiere al menos {p.personas_min} personas"
        return None

    def puede_hacer(self, t: Tarea) -> bool:
        return self.motivo_incompatible(t) is None

    def describir(self) -> str:
        c = self.conductor
        nombres = ", ".join(f"{x.nombre}{' (cond.)' if x is c else ''}" for x in self.integrantes)
        return f"{self.id} | {self.vehiculo.placa} ({self.vehiculo.descripcion}) | {nombres}"


@dataclass
class Evento:
    actividad: str
    lugar: str
    inicio: datetime
    fin: datetime
    km: float = 0.0
    tarea: Tarea | None = None


@dataclass
class Simulacion:
    eventos: list[Evento]
    fin: datetime
    km: float
    problema: str | None
    muestras: list[tuple[Tarea, datetime]]  # muestras que siguen en la cuadrilla (pernocte)
    nodos: list[str]                         # secuencia de lugares visitados


@dataclass
class PlanDia:
    dia: date
    cuadrilla: Cuadrilla
    ruta: list[Tarea]
    origen: str
    destino: str
    viaje: str
    sim: Simulacion
    avisos: list[str] = field(default_factory=list)
    costo_noche_persona: float = 0.0
    previas: list = field(default_factory=list)  # muestras que trae de días anteriores (pernocte)

    @property
    def eventos(self) -> list[Evento]:
        return self.sim.eventos

    @property
    def km(self) -> float:
        return self.sim.km

    @property
    def minutos(self) -> float:
        return (self.eventos[-1].fin - self.eventos[0].inicio).total_seconds() / 60 if self.eventos else 0

    @property
    def litros(self) -> float:
        r = self.cuadrilla.vehiculo.rendimiento_km_l
        return self.km / r if r else 0.0

    @property
    def pernocta(self) -> bool:
        return self.destino != BASE

    def costos(self, cfg: Configuracion) -> dict[str, float]:
        n = len(self.cuadrilla.integrantes)
        c = {"combustible": self.litros * cfg.precio_combustible,
             "vehiculo": self.cuadrilla.vehiculo.costo_dia,
             "viaticos": n * cfg.viatico_persona_dia,
             "alojamiento": n * self.costo_noche_persona if self.pernocta else 0.0}
        c["total"] = sum(c.values())
        return c


@dataclass
class Plan:
    id: str
    nombre: str
    fecha_inicio: date
    dias: list[PlanDia]
    pendientes: list[Tarea]
    motivos: dict[str, str]
    fuente_tiempos: str
    estado: str = "borrador"
    creado: str = ""
    avisos_red: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Equipos y cuadrillas
# --------------------------------------------------------------------------- #
def pool_equipos(equipos: list[Equipo], dia: date, instalados: dict[str, str],
                 reservados: set[str] = frozenset()) -> dict[str, list[Equipo]]:
    pool: dict[str, list[Equipo]] = {}
    for e in sorted(equipos, key=lambda e: e.codigo):
        if e.disponible(dia) and e.codigo not in instalados and e.codigo not in reservados:
            pool.setdefault(e.tipo, []).append(e)
    return pool


def equipar(t: Tarea, c: Cuadrilla, pool: dict[str, list[Equipo]], aplicar: bool = False) -> bool:
    """¿Hay equipos para esta tarea? Con aplicar=True además los asigna a la cuadrilla.
    Muestreo: un equipo portátil por tipo sirve para todos los puntos del día.
    Instalación: cada punto deja su propio equipo en campo hasta el retiro."""
    p = t.punto
    if t.tipo == "retiro" or not p.equipos:
        return True
    tipos = list(dict.fromkeys(p.equipos))
    if t.tipo == "muestreo":
        tipos = [tp for tp in tipos if tp not in c.portatiles]
    if any(not pool.get(tp) for tp in tipos):
        return False
    if aplicar:
        for tp in tipos:
            cod = pool[tp].pop(0).codigo
            if t.tipo == "muestreo":
                c.portatiles[tp] = cod
            else:
                c.a_instalar.append((cod, p.codigo))
    return True


def formar_cuadrillas(personal: list[Persona], vehiculos: list[Vehiculo], tareas: list[Tarea],
                      cfg: Configuracion, dia: date, carga: dict[str, float],
                      racha: dict[str, tuple[date, int]], ids_usados: set[str]) -> list[Cuadrilla]:
    """Arma cuadrillas del día: 1 vehículo + 1 conductor + técnicos que maximicen la
    cobertura de matrices pendientes, prefiriendo a quien lleva menos horas acumuladas.
    Excluye a quien ya cumplió el máximo de días consecutivos."""
    ayer = dia - timedelta(days=1)

    def descansa(p: Persona) -> bool:
        ultimo, n = racha.get(p.nombre, (None, 0))
        return ultimo == ayer and n >= cfg.max_dias_consecutivos

    gente = [p for p in personal if p.disponible(dia) and not descansa(p)]
    autos = [v for v in vehiculos if v.disponible(dia)]
    puntos = [t.punto for t in tareas]
    necesita_4x4 = any(p.requiere_4x4 for p in puntos)
    autos.sort(key=lambda v: (not v.es_4x4 if necesita_4x4 else v.es_4x4, -v.velocidad_kmh))
    matrices_pend = {p.matriz for p in puntos}
    tam_req = max([cfg.tamano_cuadrilla, *(p.personas_min for p in puntos)], default=2)

    cuadrillas: list[Cuadrilla] = []
    n = 1
    for v in autos:
        conductores = [p for p in gente if p.conduce]
        if not conductores:
            break
        # Conductor con menos competencias útiles primero (reserva especialistas), luego menos carga.
        conductores.sort(key=lambda p: (len(set(p.competencias) & matrices_pend), carga.get(p.nombre, 0)))
        chofer = conductores[0]
        gente.remove(chofer)
        equipo = [chofer]
        tam = min(v.capacidad, tam_req)
        while len(equipo) < tam and gente:
            cubiertas = {c for x in equipo for c in x.competencias}
            gente.sort(key=lambda p: (-len((set(p.competencias) & matrices_pend) - cubiertas),
                                      p.conduce, carga.get(p.nombre, 0), -len(p.competencias)))
            equipo.append(gente.pop(0))
        while f"C{n}" in ids_usados:
            n += 1
        cq = Cuadrilla(f"C{n}", v, equipo)
        if cq.competencias & matrices_pend:
            cuadrillas.append(cq)
            ids_usados.add(cq.id)
        else:  # no aporta nada: liberar al personal
            gente.extend(equipo)
    return cuadrillas


# --------------------------------------------------------------------------- #
# Simulación de una jornada
# --------------------------------------------------------------------------- #
def simular(ruta: list[Tarea], cq: Cuadrilla, cfg: Configuracion, dia: date, dist: Distancias,
            origen: str = BASE, destino: str = BASE,
            previas: list[tuple[Tarea, datetime]] = ()) -> Simulacion:
    """Línea de tiempo de la jornada. `previas` son muestras tomadas en días anteriores de
    una salida con pernocte que aún no se entregan."""
    t = datetime.combine(dia, parse_hora(cfg.hora_inicio))
    h_alm = datetime.combine(dia, parse_hora(cfg.hora_almuerzo))
    alm_pendiente = cfg.almuerzo_min > 0
    vel = cq.vehiculo.velocidad_kmh
    ev: list[Evento] = []
    km_tot = 0.0
    problema: str | None = None
    muestras: list[tuple[Tarea, datetime]] = list(previas)
    visitados = [origen]
    actual = origen

    def fallo(msg: str) -> None:
        nonlocal problema
        problema = problema or msg

    def agregar(act: str, lugar: str, minutos: float, km: float = 0.0, tarea: Tarea | None = None) -> None:
        nonlocal t
        fin = t + timedelta(minutes=minutos)
        ev.append(Evento(act, lugar, t, fin, km, tarea))
        t = fin

    def quizas_almorzar(lugar: str) -> None:
        nonlocal alm_pendiente
        if alm_pendiente and t >= h_alm:
            agregar("Almuerzo", lugar, cfg.almuerzo_min)
            alm_pendiente = False

    def esperar_hasta(limite: datetime, motivo: str, lugar: str) -> None:
        nonlocal alm_pendiente
        if t >= limite:
            return
        # Si la espera abarca la hora de almuerzo, se aprovecha para almorzar.
        if alm_pendiente and limite >= h_alm + timedelta(minutes=cfg.almuerzo_min):
            if t < h_alm:
                agregar(f"Espera ({motivo})", lugar, (h_alm - t).total_seconds() / 60)
            agregar("Almuerzo", lugar, cfg.almuerzo_min)
            alm_pendiente = False
        if t < limite:
            agregar(f"Espera ({motivo})", lugar, (limite - t).total_seconds() / 60)

    def viajar(dest: str, etiqueta: str) -> None:
        nonlocal actual, km_tot
        km = dist.km(actual, dest)
        km_tot += km
        agregar(f"{etiqueta} ({km:.1f} km)", f"→ {nombre_nodo(dest, cfg)}", dist.minutos(actual, dest, vel), km)
        actual = dest
        visitados.append(dest)

    def revisar_preservacion(lista, llegada: datetime, lugar: str) -> None:
        for tr, fin_m in lista:
            lim = tr.punto.max_horas_preservacion
            if lim and llegada - fin_m > timedelta(hours=lim):
                fallo(f"{tr.codigo}: supera {lim:g} h de preservación hasta {lugar}")

    if origen == BASE:
        if cfg.preparacion_min:
            agregar("Preparación de equipos / charla de seguridad", cfg.base_nombre, cfg.preparacion_min)
    elif cfg.preparacion_campo_min:
        agregar("Desayuno y alistamiento", nombre_nodo(origen, cfg), cfg.preparacion_campo_min)

    for tr in ruta:
        p = tr.punto
        quizas_almorzar("En ruta")
        viajar(p.codigo, "Traslado")
        if p.acceso_min:
            agregar("Acceso a pie (ida y vuelta)", p.codigo, p.acceso_min)
        if p.hora_desde:
            esperar_hasta(datetime.combine(dia, parse_hora(p.hora_desde)), "ventana horaria", p.codigo)
        if tr.no_antes:
            if tr.no_antes.date() > dia:
                fallo(f"{p.codigo}: el retiro es antes de completar {p.horas_exposicion:g} h de exposición")
            else:
                esperar_hasta(tr.no_antes, f"fin de exposición {p.horas_exposicion:g} h", p.codigo)
        quizas_almorzar(p.codigo)
        agregar(tr.etiqueta(), f"{p.codigo} - {p.nombre}", tr.duracion, tarea=tr)
        if p.hora_hasta and t > datetime.combine(dia, parse_hora(p.hora_hasta)):
            fallo(f"{p.codigo}: termina fuera de la ventana {p.hora_desde or '--'}-{p.hora_hasta}")
        if tr.recoge_muestra and (p.max_horas_preservacion or (p.entrega_laboratorio and cfg.usar_laboratorio)):
            muestras.append((tr, t))
    if ruta:
        quizas_almorzar(ruta[-1].codigo)

    pendientes: list[tuple[Tarea, datetime]] = []
    if destino == BASE:
        a_lab = [m for m in muestras if cfg.usar_laboratorio and m[0].punto.entrega_laboratorio
                 and dist.conoce(LAB)]
        if a_lab:
            viajar(LAB, "Traslado al laboratorio")
            esperar_hasta(datetime.combine(dia, parse_hora(cfg.lab_hora_desde)), "recepción de laboratorio",
                          cfg.lab_nombre)
            if dia.weekday() not in cfg.lab_dias:
                fallo(f"{cfg.lab_nombre} no recibe muestras los {['lunes', 'martes', 'miércoles', 'jueves', 'viernes', 'sábados', 'domingos'][dia.weekday()]}")
            if t > datetime.combine(dia, parse_hora(cfg.lab_hora_hasta)):
                fallo(f"llega al laboratorio después de las {cfg.lab_hora_hasta}")
            revisar_preservacion(a_lab, t, "el laboratorio")
            agregar("Entrega de muestras al laboratorio", cfg.lab_nombre, cfg.lab_recepcion_min)
            quizas_almorzar(cfg.lab_nombre)
        if actual != BASE:
            viajar(BASE, "Retorno a base")
        revisar_preservacion([m for m in muestras if m not in a_lab], t, "la base")
        quizas_almorzar(cfg.base_nombre)
        if cfg.descarga_min:
            agregar("Descarga, preservación y registro de muestras", cfg.base_nombre, cfg.descarga_min)
    else:
        if destino.startswith(PREF_CAMP) and Distancias._n(destino) == Distancias._n(actual):
            visitados.append(destino)
        else:
            viajar(destino, "Traslado a alojamiento" if destino.startswith(PREF_ALOJ) else "Traslado a campamento")
        quizas_almorzar(nombre_nodo(destino, cfg))
        agregar("Pernocte", nombre_nodo(destino, cfg), 0)
        # Una muestra que ya excedió su plazo no podrá entregarse a tiempo mañana.
        revisar_preservacion(muestras, t, nombre_nodo(destino, cfg))
        pendientes = muestras
    if t > datetime.combine(dia, parse_hora(cfg.hora_fin)):
        fallo("excede la jornada")
    return Simulacion(ev, t, km_tot, problema, pendientes, visitados)


def dos_opt(ruta: list[Tarea], es_valida, km_de) -> list[Tarea]:
    """Mejora 2-opt: invierte tramos mientras reduzca kilómetros y siga siendo válida."""
    mejor = ruta[:]
    mejor_km = km_de(mejor)
    mejora = True
    while mejora:
        mejora = False
        for i in range(len(mejor) - 1):
            for j in range(i + 1, len(mejor)):
                nueva = mejor[:i] + mejor[i:j + 1][::-1] + mejor[j + 1:]
                km = km_de(nueva)
                if km < mejor_km - 1e-6 and es_valida(nueva):
                    mejor, mejor_km, mejora = nueva, km, True
    return mejor


def minutos_jornada(cfg: Configuracion) -> float:
    return (datetime.combine(date.today(), parse_hora(cfg.hora_fin))
            - datetime.combine(date.today(), parse_hora(cfg.hora_inicio))).total_seconds() / 60


def destino_noche(ruta: list[Tarea], origen: str, cq: Cuadrilla, repo: Repositorio,
                  dist: Distancias) -> str | None:
    """Alojamiento más cercano (dentro del máximo configurado) o campamento en el último punto.
    Solo se aceptan lugares desde donde se puede volver a base en una jornada."""
    cfg = repo.config
    ultimo = ruta[-1].codigo if ruta else origen
    vel = cq.vehiculo.velocidad_kmh
    jornada = minutos_jornada(cfg) - cfg.preparacion_campo_min - cfg.descarga_min
    ops = []
    for a in repo.alojamientos:
        cod = PREF_ALOJ + a.nombre
        if a.activo and dist.conoce(cod):
            m = dist.minutos(ultimo, cod, vel)
            if m <= cfg.max_min_a_alojamiento and dist.minutos(cod, BASE, vel) <= jornada:
                ops.append((m, cod))
    if cfg.permitir_campamento and ruta and dist.minutos(ultimo, BASE, vel) <= jornada:
        ops.append((cfg.max_min_a_alojamiento + 1, PREF_CAMP + ultimo))
    return min(ops)[1] if ops else None


def costo_noche(destino: str, repo: Repositorio) -> float:
    if destino.startswith(PREF_ALOJ):
        a = repo.alojamiento(destino[len(PREF_ALOJ):])
        return a.costo_noche_persona if a else 0.0
    if destino.startswith(PREF_CAMP):
        return repo.config.costo_campamento_persona
    return 0.0


# --------------------------------------------------------------------------- #
# Planificación automática
# --------------------------------------------------------------------------- #
@dataclass
class _Viaje:
    cuadrilla: Cuadrilla
    lugar: str
    noches: int
    previas: list[tuple[Tarea, datetime]]
    id: str


def planificar(repo: Repositorio, puntos: list[Punto], fecha_inicio: date, max_dias: int = 30,
               nombre: str = "", verbose: bool = True) -> Plan:
    cfg = repo.config
    dist = Distancias(nodos(repo, puntos), cfg, verbose)
    pendientes = sorted(tareas_iniciales(puntos), key=lambda t: (t.prioridad, t.codigo))
    instalados: dict[str, str] = {}              # código equipo -> código punto
    carga: dict[str, float] = {}                 # minutos acumulados por persona
    racha: dict[str, tuple[date, int]] = {}      # último día trabajado, días seguidos
    viajes: list[_Viaje] = []
    dias: list[PlanDia] = []
    dia, dias_usados, dias_sin_avance, n_viaje = fecha_inicio, 0, 0, 0

    def laborable(d: date) -> bool:
        return d.weekday() in cfg.dias_laborables

    while (pendientes or viajes) and dias_usados < max_dias:
        if not laborable(dia):
            dia += timedelta(days=1)
            continue
        hoy = [t for t in pendientes if t.no_antes is None or t.no_antes.date() <= dia]
        continuan = [v.cuadrilla for v in viajes]
        ocup_p = {x.nombre for c in continuan for x in c.integrantes}
        ocup_v = {c.vehiculo.placa for c in continuan}
        ids = {c.id for c in continuan}
        nuevas = formar_cuadrillas([p for p in repo.personal if p.nombre not in ocup_p],
                                   [v for v in repo.vehiculos if v.placa not in ocup_v],
                                   hoy, cfg, dia, carga, racha, ids) if hoy else []
        for c in continuan:
            c.a_instalar = []
        cuads = continuan + nuevas
        estado = {v.cuadrilla.id: v for v in viajes}
        origen_de = lambda c: estado[c.id].lugar if c.id in estado else BASE
        previas_de = lambda c: estado[c.id].previas if c.id in estado else []
        reservados = {cod for c in continuan for cod in c.portatiles.values()}
        pool = pool_equipos(repo.equipos, dia, instalados, reservados)
        manana = dia + timedelta(days=1)

        def puede_pernoctar(c: Cuadrilla) -> bool:
            noches = estado[c.id].noches if c.id in estado else 0
            return (cfg.permitir_pernocte and noches < cfg.max_noches and laborable(manana)
                    and c.vehiculo.disponible(manana) and all(x.disponible(manana) for x in c.integrantes))

        def valida(c: Cuadrilla, ruta: list[Tarea], destino: str | None) -> bool:
            return destino is not None and simular(ruta, c, cfg, dia, dist, origen_de(c), destino,
                                                   previas_de(c)).problema is None

        rutas: dict[str, list[Tarea]] = {c.id: [] for c in cuads}
        activas = {c.id for c in cuads if hoy}
        # Asignación intercalada (round-robin): cada cuadrilla toma su mejor tarea por turno,
        # lo que reparte el territorio de forma natural entre cuadrillas.
        while activas:
            for c in cuads:
                if c.id not in activas:
                    continue
                ruta = rutas[c.id]
                ultimo = ruta[-1].codigo if ruta else origen_de(c)
                cands = sorted((t for t in hoy if c.puede_hacer(t) and equipar(t, c, pool)),
                               key=lambda t: dist.km(ultimo, t.codigo) * PESO_PRIORIDAD.get(t.prioridad, 1))
                elegido = next((t for t in cands if valida(c, ruta + [t], BASE)), None)
                if elegido is None and puede_pernoctar(c):
                    elegido = next((t for t in cands
                                    if valida(c, ruta + [t], destino_noche(ruta + [t], origen_de(c), c, repo, dist))),
                                   None)
                if elegido:
                    equipar(elegido, c, pool, aplicar=True)
                    ruta.append(elegido)
                    hoy.remove(elegido)
                    pendientes.remove(elegido)
                else:
                    activas.discard(c.id)

        avance = False
        nuevos_viajes: list[_Viaje] = []
        for c in cuads:
            ruta, origen = rutas[c.id], origen_de(c)
            if not ruta and origen == BASE:
                continue
            avance = avance or bool(ruta)
            regresa = not ruta or valida(c, ruta, BASE)
            dest_de = (lambda r: BASE) if regresa else (lambda r: destino_noche(r, origen, c, repo, dist))
            if len(ruta) > 1:
                ruta = dos_opt(ruta, lambda r: valida(c, r, dest_de(r)),
                               lambda r: dist.km_ruta([t.codigo for t in r], origen, dest_de(r) or BASE))
            destino = dest_de(ruta) or BASE
            vid = estado[c.id].id if c.id in estado else ""
            if destino != BASE and not vid:
                n_viaje += 1
                vid = f"V{n_viaje}"
            sim = simular(ruta, c, cfg, dia, dist, origen, destino, previas_de(c))
            pd = PlanDia(dia, c, ruta, origen, destino, vid, sim,
                         [sim.problema] if sim.problema else [], costo_noche(destino, repo), previas_de(c))
            dias.append(pd)
            if destino != BASE:
                noches = (estado[c.id].noches if c.id in estado else 0) + 1
                nuevos_viajes.append(_Viaje(c, destino, noches, sim.muestras, vid))
            for cod, pcod in c.a_instalar:
                instalados[cod] = pcod
            for e in sim.eventos:
                if e.tarea and e.tarea.tipo == "instalacion":
                    p = e.tarea.punto
                    pendientes.append(Tarea(p, "retiro", e.fin + timedelta(hours=p.horas_exposicion),
                                            [cod for cod, pc in c.a_instalar if pc == p.codigo]))
                elif e.tarea and e.tarea.tipo == "retiro":
                    for cod in e.tarea.equipos_en_campo:
                        instalados.pop(cod, None)  # vuelve a base: disponible desde mañana
            for x in c.integrantes:
                ultimo_dia, n = racha.get(x.nombre, (None, 0))
                racha[x.nombre] = (dia, n + 1 if ultimo_dia == dia - timedelta(days=1) else 1)
                carga[x.nombre] = carga.get(x.nombre, 0) + pd.minutos
        viajes = nuevos_viajes
        if hoy or avance:
            dias_sin_avance = 0 if avance else dias_sin_avance + 1
        if dias_sin_avance >= 7 and not viajes:  # una semana sin asignar nada: son inviables
            break
        dia += timedelta(days=1)
        dias_usados += 1

    motivos = {t.clave: diagnosticar(t, repo, dist, fecha_inicio) for t in pendientes}
    return Plan(repo.nuevo_id(), nombre or f"Programación desde {fecha_inicio:%d/%m/%Y}", fecha_inicio,
                dias, pendientes, motivos, dist.fuente, creado=datetime.now().isoformat(timespec="seconds"),
                avisos_red=dist.avisos)


def diagnosticar(t: Tarea, repo: Repositorio, dist: Distancias, dia: date) -> str:
    cfg, p = repo.config, t.punto
    if t.tipo == "retiro":
        return (f"retiro fuera del horizonte de planificación; equipo(s) "
                f"{', '.join(t.equipos_en_campo) or '-'} quedan instalados")
    if not any(p.matriz in x.competencias and x.activo for x in repo.personal):
        return f"nadie del personal tiene competencia en '{p.matriz}'"
    if p.requiere_4x4 and not any(v.es_4x4 and v.activo for v in repo.vehiculos):
        return "requiere 4x4 y no hay vehículos 4x4 activos"
    for tp in dict.fromkeys(p.equipos):
        unidades = [e for e in repo.equipos if e.tipo == tp and e.activo]
        if not unidades:
            return f"no hay equipos de tipo '{tp}' registrados"
        if not any(e.disponible(dia) for e in unidades):
            return f"todos los equipos '{tp}' tienen la calibración vencida o no están disponibles"
    if p.hora_desde and p.hora_hasta:
        ventana = (datetime.combine(dia, parse_hora(p.hora_hasta))
                   - datetime.combine(dia, parse_hora(p.hora_desde))).total_seconds() / 60
        if ventana < t.duracion:
            return f"la ventana horaria ({fmt_min(ventana)}) es menor que la duración ({t.duracion} min)"
    vel = max((v.velocidad_kmh for v in repo.vehiculos if v.activo), default=40)
    vuelta = dist.minutos(p.codigo, BASE, vel)
    if p.max_horas_preservacion and vuelta > p.max_horas_preservacion * 60:
        return (f"el retorno a base ({fmt_min(vuelta)}) supera el tiempo de preservación "
                f"({p.max_horas_preservacion:g} h); considere envío por courier")
    jornada = minutos_jornada(cfg)
    ida = dist.minutos(BASE, p.codigo, vel)
    necesario = (cfg.preparacion_min + cfg.descarga_min + cfg.almuerzo_min + p.acceso_min
                 + t.duracion + ida + vuelta)
    if necesario > jornada:
        if not cfg.permitir_pernocte:
            return (f"no cabe en una jornada ({fmt_min(necesario)} vs {fmt_min(jornada)}); "
                    "active 'Permitir salidas de varios días' en Configuración → Pernocte")
        if not repo.alojamientos and not cfg.permitir_campamento:
            return "requiere pernocte: registre un alojamiento cercano o permita campamento"
        if ida > jornada:
            return f"la sola ida ({fmt_min(ida)}) supera la jornada"
        return ("requiere pernocte y no hay alojamiento dentro del máximo configurado "
                f"({cfg.max_min_a_alojamiento} min) ni campamento permitido")
    return ("sin cuadrilla compatible en el horizonte (personal disponible, tamaño de cuadrilla, "
            "vehículos, equipos o ventana horaria)")


# --------------------------------------------------------------------------- #
# Serialización y evaluación de planes (también tras ediciones manuales)
# --------------------------------------------------------------------------- #
def plan_a_dict(plan: Plan) -> dict:
    return {
        "id": plan.id, "nombre": plan.nombre, "estado": plan.estado, "creado": plan.creado,
        "fecha_inicio": plan.fecha_inicio.isoformat(), "fuente_tiempos": plan.fuente_tiempos,
        "dias": [{
            "fecha": pd.dia.isoformat(), "cuadrilla": pd.cuadrilla.id, "vehiculo": pd.cuadrilla.vehiculo.placa,
            "integrantes": [x.nombre for x in pd.cuadrilla.integrantes],
            "origen": pd.origen, "destino": pd.destino, "viaje": pd.viaje,
            "tareas": [{"codigo": t.codigo, "tipo": t.tipo, "estado": t.estado, "hora_real": t.hora_real,
                        "observacion": t.observacion} for t in pd.ruta],
        } for pd in plan.dias],
        "pendientes": [{"codigo": t.codigo, "tipo": t.tipo, "motivo": plan.motivos.get(t.clave, "")}
                       for t in plan.pendientes],
    }


def evaluar_plan(repo: Repositorio, d: dict, verbose: bool = False) -> Plan:
    """Reconstruye y valida un plan a partir de su estructura (días, cuadrillas, orden de tareas).
    Nunca descarta nada: los incumplimientos se reportan como avisos del día."""
    cfg = repo.config
    d["dias"] = sorted(d.get("dias", []), key=lambda x: (x["fecha"], x["cuadrilla"]))
    por_cod = {p.codigo: p for p in repo.puntos}
    personas = {p.nombre: p for p in repo.personal}
    vehs = {v.placa: v for v in repo.vehiculos}
    usados = {t["codigo"] for x in d["dias"] for t in x["tareas"]} | {t["codigo"] for t in d.get("pendientes", [])}
    dist = Distancias(nodos(repo, [por_cod[c] for c in usados if c in por_cod]), cfg, verbose)

    instalados: dict[str, str] = {}
    instalaciones: dict[str, tuple[datetime, list[str]]] = {}
    previas_viaje: dict[str, list] = {}
    pools: dict[str, dict] = {}
    ocup_p: dict[str, dict[str, str]] = {}
    ocup_v: dict[str, dict[str, str]] = {}
    dias: list[PlanDia] = []
    for x in d["dias"]:
        dia = date.fromisoformat(x["fecha"])
        avisos: list[str] = []
        veh = vehs.get(x.get("vehiculo", ""))
        if veh is None:
            avisos.append(f"vehículo '{x.get('vehiculo') or '—'}' no registrado")
            veh = Vehiculo(x.get("vehiculo") or "SIN-VEHÍCULO", "no registrado")
        integ = []
        for n in x.get("integrantes", []):
            if n in personas:
                integ.append(personas[n])
            else:
                avisos.append(f"'{n}' no está registrado en el personal")
        cq = Cuadrilla(x["cuadrilla"], veh, integ)
        if dia.weekday() not in cfg.dias_laborables:
            avisos.append("día no laborable según la configuración")
        if not veh.disponible(dia):
            avisos.append(f"{veh.placa} no disponible ese día (inactivo, mantenimiento o documentos vencidos)")
        otro = ocup_v.setdefault(x["fecha"], {}).get(veh.placa)
        if otro:
            avisos.append(f"{veh.placa} también está asignado a {otro}")
        ocup_v[x["fecha"]][veh.placa] = cq.id
        for p in integ:
            if not p.disponible(dia):
                avisos.append(f"{p.nombre} no disponible ese día (inactivo, permiso o habilitación vencida)")
            otro = ocup_p.setdefault(x["fecha"], {}).get(p.nombre)
            if otro:
                avisos.append(f"{p.nombre} también está asignado a {otro}")
            ocup_p[x["fecha"]][p.nombre] = cq.id
        if integ and cq.conductor is None:
            avisos.append("la cuadrilla no tiene conductor")
        if len(integ) > veh.capacidad:
            avisos.append(f"{len(integ)} personas superan la capacidad del vehículo ({veh.capacidad})")
        pool = pools.setdefault(x["fecha"], pool_equipos(repo.equipos, dia, instalados))
        ruta: list[Tarea] = []
        for td in x.get("tareas", []):
            p = por_cod.get(td["codigo"])
            if p is None:
                avisos.append(f"el punto {td['codigo']} ya no existe")
                continue
            t = Tarea(p, td.get("tipo", "muestreo"), estado=td.get("estado", "programado"),
                      hora_real=td.get("hora_real", ""), observacion=td.get("observacion", ""))
            if t.tipo == "retiro":
                info = instalaciones.get(p.codigo)
                if info:
                    t.no_antes = info[0] + timedelta(hours=p.horas_exposicion)
                    t.equipos_en_campo = info[1]
                else:
                    avisos.append(f"{p.codigo}: retiro sin instalación previa en esta programación")
            motivo = cq.motivo_incompatible(t)
            if motivo:
                avisos.append(f"{p.codigo}: {motivo}")
            if not equipar(t, cq, pool, aplicar=True):
                avisos.append(f"{p.codigo}: sin equipo disponible ({', '.join(p.equipos)})")
            ruta.append(t)
        origen, destino = x.get("origen") or BASE, x.get("destino") or BASE
        for nombre_campo, cod in (("origen", origen), ("destino", destino)):
            if cod != BASE and not dist.conoce(cod):
                avisos.append(f"{nombre_campo} '{nombre_nodo(cod, cfg)}' ya no existe; se usa la base")
                if nombre_campo == "origen":
                    origen = BASE
                else:
                    destino = BASE
        previas = previas_viaje.get(x.get("viaje", ""), []) if origen != BASE else []
        sim = simular(ruta, cq, cfg, dia, dist, origen, destino, previas)
        if sim.problema:
            avisos.append(sim.problema)
        for e in sim.eventos:
            if e.tarea and e.tarea.tipo == "instalacion":
                cod_p = e.tarea.codigo
                instalaciones[cod_p] = (e.fin, [c for c, pc in cq.a_instalar if pc == cod_p])
            elif e.tarea and e.tarea.tipo == "retiro":
                for c in e.tarea.equipos_en_campo:
                    instalados.pop(c, None)
        for c, pc in cq.a_instalar:
            instalados[c] = pc
        if destino != BASE:
            previas_viaje[x.get("viaje", "")] = sim.muestras
        dias.append(PlanDia(dia, cq, ruta, origen, destino, x.get("viaje", ""), sim, avisos,
                            costo_noche(destino, repo), previas))
    pendientes = [Tarea(por_cod[t["codigo"]], t.get("tipo", "muestreo"))
                  for t in d.get("pendientes", []) if t["codigo"] in por_cod]
    motivos = {f"{t['codigo']}:{t.get('tipo', 'muestreo')}": t.get("motivo", "") for t in d.get("pendientes", [])}
    return Plan(d.get("id") or repo.nuevo_id(), d.get("nombre", ""), date.fromisoformat(d["fecha_inicio"]),
                dias, pendientes, motivos, dist.fuente, d.get("estado", "borrador"), d.get("creado", ""),
                dist.avisos)


def optimizar_dia(repo: Repositorio, d: dict, indice: int) -> dict:
    """Reordena las tareas de un día para recorrer menos km, manteniendo cuadrilla y destino."""
    plan = evaluar_plan(repo, d)
    pd = plan.dias[indice]
    if len(pd.ruta) < 2:
        return d
    cfg = repo.config
    dist = Distancias(nodos(repo, [t.punto for t in pd.ruta]), cfg, verbose=False)
    valida = lambda r: simular(r, pd.cuadrilla, cfg, pd.dia, dist, pd.origen, pd.destino,
                               pd.previas).problema is None
    km_de = lambda r: dist.km_ruta([t.codigo for t in r], pd.origen, pd.destino)
    # Vecino más cercano como punto de partida; si el orden actual ya es válido se exige
    # que el nuevo también lo sea, si no, se minimizan km sin restricción.
    resto, vecino, actual = pd.ruta[:], [], pd.origen
    while resto:
        sig = min(resto, key=lambda t: dist.km(actual, t.codigo))
        vecino.append(sig)
        resto.remove(sig)
        actual = sig.codigo
    actual_valida = valida(pd.ruta)
    inicio = vecino if valida(vecino) or not actual_valida else pd.ruta
    orden = dos_opt(inicio, valida if actual_valida else (lambda r: True), km_de)
    d["dias"][indice]["tareas"] = [{"codigo": t.codigo, "tipo": t.tipo, "estado": t.estado,
                                    "hora_real": t.hora_real, "observacion": t.observacion} for t in orden]
    return d


# --------------------------------------------------------------------------- #
# Indicadores
# --------------------------------------------------------------------------- #
def resumen(plan: Plan, cfg: Configuracion) -> dict:
    r = {"puntos": 0, "retiros": 0, "dias": len({pd.dia for pd in plan.dias}), "cuadrillas_dia": len(plan.dias),
         "noches": 0, "km": 0.0, "litros": 0.0, "combustible": 0.0, "vehiculo": 0.0, "viaticos": 0.0,
         "alojamiento": 0.0, "personas": {}, "flota": {}, "avisos": sum(len(pd.avisos) for pd in plan.dias),
         "pendientes": len(plan.pendientes)}
    for pd in plan.dias:
        r["puntos"] += sum(t.tipo != "retiro" for t in pd.ruta)
        r["retiros"] += sum(t.tipo == "retiro" for t in pd.ruta)
        r["noches"] += pd.pernocta
        c = pd.costos(cfg)
        r["km"] += pd.km
        r["litros"] += pd.litros
        for k in ("combustible", "vehiculo", "viaticos", "alojamiento"):
            r[k] += c[k]
        for x in pd.cuadrilla.integrantes:
            p = r["personas"].setdefault(x.nombre, {"dias": 0, "min": 0.0, "noches": 0})
            p["dias"] += 1
            p["min"] += pd.minutos
            p["noches"] += pd.pernocta
        v = r["flota"].setdefault(pd.cuadrilla.vehiculo.placa, {"dias": 0, "km": 0.0, "litros": 0.0, "costo": 0.0})
        v["dias"] += 1
        v["km"] += pd.km
        v["litros"] += pd.litros
        v["costo"] += c["combustible"] + c["vehiculo"]
    r["total"] = r["combustible"] + r["vehiculo"] + r["viaticos"] + r["alojamiento"]
    return r


def cumplimiento(plan: Plan) -> dict:
    tareas = [(pd, t) for pd in plan.dias for t in pd.ruta]
    por_estado = Counter(t.estado for _, t in tareas)
    base = len(tareas) - por_estado.get("cancelado", 0)
    por_matriz: dict[str, dict[str, int]] = {}
    por_cuadrilla: dict[str, dict[str, int]] = {}
    for pd, t in tareas:
        for grupo, clave in ((por_matriz, t.punto.matriz), (por_cuadrilla, pd.cuadrilla.id)):
            g = grupo.setdefault(clave, {"total": 0, "ejecutado": 0})
            g["total"] += t.estado != "cancelado"
            g["ejecutado"] += t.estado == "ejecutado"
    registradas = len(tareas) - por_estado.get("programado", 0)
    return {"total": len(tareas), "por_estado": dict(por_estado), "registradas": registradas,
            "porcentaje": round(por_estado.get("ejecutado", 0) / base * 100, 1) if base else 0.0,
            "por_matriz": por_matriz, "por_cuadrilla": por_cuadrilla,
            "no_ejecutadas": [{"fecha": pd.dia.isoformat(), "cuadrilla": pd.cuadrilla.id, "codigo": t.codigo,
                               "tipo": t.tipo, "estado": t.estado, "observacion": t.observacion}
                              for pd, t in tareas if t.estado in ("reprogramar", "no_accesible", "cancelado")]}
