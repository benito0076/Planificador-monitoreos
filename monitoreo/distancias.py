"""Distancias y tiempos de traslado: OSRM (rutas reales por carretera) con respaldo Haversine."""

from __future__ import annotations

import json
import math
import time as time_mod
import urllib.error
import urllib.request

from .modelo import (ARCHIVO_CACHE_OSRM, BASE, LAB, PREF_ALOJ, PREF_CAMP, UMBRAL_SNAP_M,
                     Configuracion, Punto, Repositorio, candado)

AGENTE = {"User-Agent": "planificador-monitoreo/3.0"}


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def leer_cache_osrm() -> dict:
    with candado:
        try:
            return json.loads(ARCHIVO_CACHE_OSRM.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}


def guardar_cache_osrm(cache: dict) -> None:
    with candado:
        try:
            actual = leer_cache_osrm()
            actual.update(cache)
            ARCHIVO_CACHE_OSRM.write_text(json.dumps(actual), encoding="utf-8")
        except OSError:
            pass


def nodos(repo: Repositorio, puntos: list[Punto]) -> dict[str, tuple[float, float]]:
    """Coordenadas de todos los lugares que puede visitar una ruta."""
    cfg = repo.config
    c = {p.codigo: (p.lat, p.lon) for p in puntos}
    c[BASE] = (cfg.base_lat, cfg.base_lon)
    if cfg.usar_laboratorio and (cfg.lab_lat or cfg.lab_lon):
        c[LAB] = (cfg.lab_lat, cfg.lab_lon)
    if cfg.permitir_pernocte:
        for a in repo.alojamientos:
            if a.activo:
                c[PREF_ALOJ + a.nombre] = (a.lat, a.lon)
    return c


def coord_nodo(codigo: str, repo: Repositorio) -> tuple[float, float] | None:
    cfg = repo.config
    if codigo == BASE:
        return cfg.base_lat, cfg.base_lon
    if codigo == LAB:
        return cfg.lab_lat, cfg.lab_lon
    if codigo.startswith(PREF_ALOJ):
        a = repo.alojamiento(codigo[len(PREF_ALOJ):])
        return (a.lat, a.lon) if a else None
    if codigo.startswith(PREF_CAMP):
        codigo = codigo[len(PREF_CAMP):]
    p = repo.punto(codigo)
    return (p.lat, p.lon) if p else None


def nombre_nodo(codigo: str, cfg: Configuracion) -> str:
    if codigo == BASE:
        return cfg.base_nombre
    if codigo == LAB:
        return cfg.lab_nombre
    if codigo.startswith(PREF_ALOJ):
        return codigo[len(PREF_ALOJ):]
    if codigo.startswith(PREF_CAMP):
        return f"Campamento en {codigo[len(PREF_CAMP):]}"
    return codigo


class Distancias:
    """Distancias (km) y tiempos (min) entre nodos.

    Con OSRM activo usa rutas reales por carretera (tabla OSRM, con caché en disco).
    Cualquier par sin ruta —o si el servidor no responde— cae a la estimación
    Haversine x factor de ruta con la velocidad promedio del vehículo."""

    def __init__(self, coords: dict[str, tuple[float, float]], cfg: Configuracion, verbose: bool = True):
        self.coord = dict(coords)
        self.factor = cfg.factor_ruta
        self.factor_tiempo = cfg.factor_tiempo_osrm
        self._cache: dict[tuple[str, str], float] = {}
        self.rutas: dict[tuple[str, str], tuple[float, float]] = {}  # (a, b) -> (km, min) OSRM
        self.fuente = f"estimación en línea recta x factor de ruta {cfg.factor_ruta}"
        self.avisos: list[str] = []
        if cfg.usar_osrm and len(self.coord) > 1:
            self._cargar_osrm(cfg.osrm_url.rstrip("/"), verbose)
        if verbose:
            print(f"\n  Tiempos de traslado: {self.fuente}")
            for a in self.avisos:
                print(f"  ⚠ {a}")

    # --- OSRM ------------------------------------------------------------- #
    def _clave(self, cod: str) -> str:
        lat, lon = self.coord[cod]
        return f"{lat:.5f},{lon:.5f}"

    def _cargar_osrm(self, url: str, verbose: bool) -> None:
        cache = leer_cache_osrm()
        par = lambda a, b: f"{url}|{self._clave(a)}|{self._clave(b)}"
        cods = list(self.coord)
        faltan = [(a, b) for a in cods for b in cods if a != b and par(a, b) not in cache]
        if faltan:
            fuentes = list(dict.fromkeys(a for a, _ in faltan))
            destinos = list(dict.fromkeys(b for _, b in faltan))
            if verbose:
                print(f"  Consultando OSRM ({len(faltan)} tramos)...", flush=True)
            nuevos: dict = {}
            try:
                self._consultar_osrm(url, fuentes, destinos, nuevos, par)
            except (urllib.error.URLError, TimeoutError, OSError, ValueError, KeyError) as e:
                self.avisos.append(f"OSRM no disponible ({e}); tramos sin caché usan tiempo estimado.")
            cache.update(nuevos)
            guardar_cache_osrm(nuevos)

        sin_ruta = 0
        for a in cods:
            for b in cods:
                if a == b:
                    continue
                v = cache.get(par(a, b))
                if v and v[0] is not None and v[1] is not None:
                    self.rutas[(a, b)] = (v[0], v[1])
                else:
                    sin_ruta += 1
        for c in cods:
            snap = cache.get(f"{url}|snap|{self._clave(c)}")
            if snap and snap > UMBRAL_SNAP_M:
                nombre = {BASE: "BASE", LAB: "LABORATORIO"}.get(c, c)
                self.avisos.append(f"{nombre} está a {snap / 1000:.2f} km de la vía más cercana; "
                                   "revise coordenadas o el tiempo de acceso a pie.")
        if self.rutas:
            self.fuente = f"OSRM por carretera ({url}) x{self.factor_tiempo}"
            if sin_ruta:
                self.avisos.append(f"{sin_ruta} tramo(s) sin ruta OSRM; se usa la estimación en línea recta.")

    def _consultar_osrm(self, url, fuentes, destinos, cache, par) -> None:
        bloque = 50  # 50 orígenes + 50 destinos <= 100 coordenadas por consulta
        primera = True
        for i in range(0, len(fuentes), bloque):
            for j in range(0, len(destinos), bloque):
                src, dst = fuentes[i:i + bloque], destinos[j:j + bloque]
                cods = list(dict.fromkeys(src + dst))
                idx = {c: k for k, c in enumerate(cods)}
                locs = ";".join(f"{self.coord[c][1]:.6f},{self.coord[c][0]:.6f}" for c in cods)
                q = (f"{url}/table/v1/driving/{locs}?annotations=duration,distance"
                     f"&sources={';'.join(str(idx[c]) for c in src)}"
                     f"&destinations={';'.join(str(idx[c]) for c in dst)}")
                if not primera:
                    time_mod.sleep(1)  # el servidor público admite ~1 consulta/segundo
                primera = False
                with urllib.request.urlopen(urllib.request.Request(q, headers=AGENTE), timeout=30) as r:
                    d = json.load(r)
                if d.get("code") != "Ok":
                    raise ValueError(d.get("message") or d.get("code"))
                for si, a in enumerate(src):
                    for di, b in enumerate(dst):
                        if a != b:
                            dis, dur = d["distances"][si][di], d["durations"][si][di]
                            cache[par(a, b)] = [None if dis is None else dis / 1000,
                                                None if dur is None else dur / 60]
                for wp, c in zip(d.get("sources", []), src):
                    cache[f"{url}|snap|{self._clave(c)}"] = wp.get("distance", 0)
                for wp, c in zip(d.get("destinations", []), dst):
                    cache[f"{url}|snap|{self._clave(c)}"] = wp.get("distance", 0)

    # --- Consultas -------------------------------------------------------- #
    @staticmethod
    def _n(c: str) -> str:
        return c[len(PREF_CAMP):] if c.startswith(PREF_CAMP) else c

    def conoce(self, c: str) -> bool:
        return self._n(c) in self.coord

    def km(self, a: str, b: str) -> float:
        a, b = self._n(a), self._n(b)
        if a == b:
            return 0.0
        if (a, b) in self.rutas:
            return self.rutas[(a, b)][0]
        k = (a, b) if a < b else (b, a)
        if k not in self._cache:
            self._cache[k] = haversine_km(*self.coord[a], *self.coord[b]) * self.factor
        return self._cache[k]

    def minutos(self, a: str, b: str, velocidad_kmh: float) -> float:
        a, b = self._n(a), self._n(b)
        if a == b:
            return 0.0
        if (a, b) in self.rutas:
            return self.rutas[(a, b)][1] * self.factor_tiempo
        return self.km(a, b) / max(velocidad_kmh, 1) * 60

    def km_ruta(self, codigos: list[str], origen: str = BASE, destino: str = BASE) -> float:
        seq = [origen, *codigos, destino]
        return sum(self.km(x, y) for x, y in zip(seq, seq[1:]))


_ultima_consulta_ruta = 0.0


def trazado_osrm(coords: list[tuple[float, float]], url: str, cache: dict) -> list[list[float]] | None:
    """Trazado por carretera [[lat, lon], ...] que pasa por `coords` en orden (OSRM /route).
    Devuelve None si no hay ruta o el servidor no responde."""
    global _ultima_consulta_ruta
    url = url.rstrip("/")
    # quitar paradas consecutivas repetidas (p. ej. campamento en el último punto)
    coords = [c for i, c in enumerate(coords) if i == 0 or c != coords[i - 1]]
    if len(coords) < 2:
        return None
    clave = f"{url}|route|" + ";".join(f"{lat:.5f},{lon:.5f}" for lat, lon in coords)
    if clave in cache:
        return cache[clave]
    locs = ";".join(f"{lon:.6f},{lat:.6f}" for lat, lon in coords)
    q = f"{url}/route/v1/driving/{locs}?overview=full&geometries=geojson"
    with candado:
        espera = 1.0 - (time_mod.monotonic() - _ultima_consulta_ruta)
        if espera > 0:
            time_mod.sleep(espera)  # servidor público: ~1 consulta/segundo
        _ultima_consulta_ruta = time_mod.monotonic()
    try:
        with urllib.request.urlopen(urllib.request.Request(q, headers=AGENTE), timeout=30) as r:
            d = json.load(r)
        if d.get("code") != "Ok" or not d.get("routes"):
            return None
        # GeoJSON viene como [lon, lat]; se redondea a ~1 m para aligerar caché y HTML.
        linea = [[round(lat, 5), round(lon, 5)] for lon, lat in d["routes"][0]["geometry"]["coordinates"]]
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, KeyError):
        return None
    cache[clave] = linea
    return linea
