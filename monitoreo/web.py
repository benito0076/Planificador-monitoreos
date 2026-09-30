"""Interfaz gráfica: servidor web local (solo biblioteca estándar) + página interfaz.html."""

from __future__ import annotations

import json
import socket
import threading
import traceback
import webbrowser
from dataclasses import asdict
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from .consola import entidad_a_csv, importar_csv_texto
from .distancias import Distancias, nodos, nombre_nodo
from .modelo import (BASE, CARPETA_SALIDAS, DIAS_SEMANA, ESQUEMA_CONFIG, ESQUEMAS, ESTADOS_PLAN, ESTADOS_TAREA,
                     FRECUENCIAS, PREF_ALOJ, Configuracion, Repositorio, _crear, calcular_alertas, candado,
                     esquemas_json, fmt_min, normalizar)
from .planificador import (Plan, cumplimiento, evaluar_plan, optimizar_dia, plan_a_dict, planificar, resumen)
from .reportes import COLORES, exportar, paradas, trazados

PAGINA = Path(__file__).with_name("interfaz.html")
TIPOS = {".csv": "text/csv; charset=utf-8", ".html": "text/html; charset=utf-8", ".ics": "text/calendar",
         ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}


class ErrorUsuario(Exception):
    """Error que se muestra tal cual al usuario (HTTP 400)."""


# --------------------------------------------------------------------------- #
# Conversión a JSON para la página
# --------------------------------------------------------------------------- #
def datos_json(repo: Repositorio) -> dict:
    d = {ent: [asdict(x) for x in repo.lista(ent)] for ent in ESQUEMAS}
    d["config"] = asdict(repo.config)
    d["tipos_equipo"] = repo.tipos_equipo()
    return d


def vista_plan(plan: Plan, d: dict, repo: Repositorio) -> dict:
    cfg = repo.config
    dias = []
    for i, pd in enumerate(plan.dias):
        c = pd.costos(cfg)
        dias.append({
            "indice": i, "color": COLORES[i % len(COLORES)], "semana": DIAS_SEMANA[pd.dia.weekday()],
            "origen_nombre": "Base" if pd.origen == BASE else nombre_nodo(pd.origen, cfg),
            "destino_nombre": "Base" if pd.destino == BASE else nombre_nodo(pd.destino, cfg),
            "km": round(pd.km, 1), "litros": round(pd.litros, 1), "duracion": fmt_min(pd.minutos),
            "costo": round(c["total"], 2), "avisos": pd.avisos, "equipos": pd.cuadrilla.equipos,
            "eventos": [{"inicio": f"{e.inicio:%H:%M}", "fin": f"{e.fin:%H:%M}", "actividad": e.actividad,
                         "lugar": e.lugar, "codigo": e.tarea.codigo if e.tarea else ""} for e in pd.eventos],
            "etiquetas": [t.etiqueta() for t in pd.ruta], "paradas": paradas(pd, repo),
        })
    r = resumen(plan, cfg)
    r["personas"] = [{"nombre": n, "dias": v["dias"], "horas": fmt_min(v["min"]), "noches": v["noches"]}
                     for n, v in sorted(r["personas"].items())]
    r["flota"] = [{"placa": p, **{k: round(x, 1) for k, x in v.items()}} for p, v in sorted(r["flota"].items())]
    return {"plan": d, "dias": dias, "resumen": r, "cumplimiento": cumplimiento(plan),
            "fuente_tiempos": plan.fuente_tiempos, "avisos_red": plan.avisos_red,
            "pendientes": [{"codigo": t.codigo, "tipo": t.tipo, "nombre": t.punto.nombre,
                            "motivo": plan.motivos.get(t.clave, "")} for t in plan.pendientes]}


def seleccionar(repo: Repositorio, sel: dict) -> list:
    activos = [p for p in repo.puntos if p.activo]
    modo = sel.get("modo", "todos")
    if modo == "vencen":
        hasta = date.fromisoformat(sel.get("hasta") or (date.today() + timedelta(days=30)).isoformat())
        return [p for p in activos if (f := p.proxima_fecha()) is not None and f <= hasta]
    if modo == "matriz":
        return [p for p in activos if p.matriz in sel.get("matrices", [])]
    if modo == "codigos":
        cods = set(sel.get("codigos", []))
        return [p for p in activos if p.codigo in cods]
    return activos


def registro_desde_json(entidad: str, datos: dict):
    obj = _crear(ESQUEMAS[entidad]["cls"], datos)
    try:
        normalizar(obj)
    except (ValueError, TypeError) as e:
        raise ErrorUsuario(f"Registro no válido: {e}") from e
    return obj


def leer_prog(repo: Repositorio, pid: str) -> dict:
    d = repo.leer_programacion(pid)
    if d is None:
        raise ErrorUsuario("La programación no existe")
    return d


# --------------------------------------------------------------------------- #
# Rutas de la API
# --------------------------------------------------------------------------- #
def atender(repo: Repositorio, metodo: str, partes: list[str], q: dict, cuerpo):
    """Devuelve un objeto JSON, o una tupla (bytes, tipo, nombre_descarga) para archivos."""
    match (metodo, partes):
        case ("GET", ["esquemas"]):
            e = esquemas_json()
            e.update(estados_tarea=ESTADOS_TAREA, estados_plan=ESTADOS_PLAN, frecuencias=list(FRECUENCIAS),
                     dias_semana=DIAS_SEMANA)
            return e
        case ("GET", ["datos"]):
            return datos_json(repo)
        case ("GET", ["alertas"]):
            return calcular_alertas(repo)
        case ("POST", ["demo"]):
            from .demo import cargar_demo
            cargar_demo(repo)
            return datos_json(repo)

        # --- Registros -------------------------------------------------------
        case ("POST", ["entidad", ent]) if ent in ESQUEMAS:
            clave, lista = ESQUEMAS[ent]["clave"], repo.lista(ent)
            obj = registro_desde_json(ent, cuerpo["registro"])
            original = cuerpo.get("original")
            idx = next((i for i, x in enumerate(lista) if getattr(x, clave) == original), None)
            choque = next((i for i, x in enumerate(lista) if getattr(x, clave) == getattr(obj, clave)), None)
            if choque is not None and choque != idx:
                raise ErrorUsuario(f"Ya existe un registro con {clave} '{getattr(obj, clave)}'")
            if idx is None:
                lista.append(obj)
            else:
                lista[idx] = obj
            repo.guardar()
            return datos_json(repo)
        case ("DELETE", ["entidad", ent, clave_valor]) if ent in ESQUEMAS:
            clave = ESQUEMAS[ent]["clave"]
            setattr(repo, ent, [x for x in repo.lista(ent) if getattr(x, clave) != clave_valor])
            repo.guardar()
            return datos_json(repo)
        case ("GET", ["csv", ent]) if ent in ESQUEMAS:
            return ("﻿" + entidad_a_csv(repo, ent)).encode("utf-8"), TIPOS[".csv"], f"{ent}.csv"
        case ("POST", ["csv", ent]) if ent in ESQUEMAS:
            r = importar_csv_texto(repo, ent, cuerpo["texto"])
            r["datos"] = datos_json(repo)
            return r
        case ("PUT", ["config"]):
            nuevo = _crear(Configuracion, {**asdict(repo.config), **cuerpo})
            for c in ESQUEMA_CONFIG:
                if c.requerido and not getattr(nuevo, c.nombre):
                    raise ErrorUsuario(f"'{c.etiqueta}' es obligatorio")
            repo.config = nuevo
            repo.guardar()
            return datos_json(repo)
        case ("GET", ["matriz"]):
            pts = [p for p in repo.puntos if p.activo]
            dist = Distancias(nodos(repo, pts), repo.config, verbose=False)
            vels = [v.velocidad_kmh for v in repo.vehiculos if v.activo] or [40.0]
            vel = sum(vels) / len(vels)
            cods = [BASE, *(p.codigo for p in pts)]
            return {"codigos": ["BASE" if c == BASE else c for c in cods], "fuente": dist.fuente,
                    "minutos": [[round(dist.minutos(a, b, vel)) for b in cods] for a in cods],
                    "km": [[round(dist.km(a, b), 1) for b in cods] for a in cods]}

        # --- Programaciones --------------------------------------------------
        case ("GET", ["programaciones"]):
            return [{"id": d["id"], "nombre": d.get("nombre", ""), "estado": d.get("estado", "borrador"),
                     "fecha_inicio": d.get("fecha_inicio", ""), "creado": d.get("creado", ""),
                     "modificado": d.get("modificado", ""), "cuadrilla_dias": len(d.get("dias", [])),
                     "pendientes": len(d.get("pendientes", []))} for d in repo.listar_programaciones()]
        case ("POST", ["planificar"]):
            puntos = seleccionar(repo, cuerpo.get("seleccion", {}))
            if not puntos:
                raise ErrorUsuario("No hay puntos que cumplan la selección")
            inicio = date.fromisoformat(cuerpo["fecha_inicio"])
            plan = planificar(repo, puntos, inicio, int(cuerpo.get("max_dias", 30)),
                              cuerpo.get("nombre") or "", verbose=False)
            d = plan_a_dict(plan)
            repo.guardar_programacion(d)
            return vista_plan(plan, d, repo)
        case ("GET", ["programacion", pid]):
            d = leer_prog(repo, pid)
            return vista_plan(evaluar_plan(repo, d), d, repo)
        case ("PUT", ["programacion", pid]):
            leer_prog(repo, pid)
            d = {**cuerpo, "id": pid}
            if d.get("estado") not in ESTADOS_PLAN:
                d["estado"] = "borrador"
            plan = evaluar_plan(repo, d)  # también ordena los días
            repo.guardar_programacion(d)
            return vista_plan(plan, d, repo)
        case ("POST", ["programacion", pid, "evaluar"]):
            d = {**cuerpo, "id": pid}  # vista previa sin guardar
            return vista_plan(evaluar_plan(repo, d), d, repo)
        case ("POST", ["programacion", pid, "optimizar", indice]):
            d = optimizar_dia(repo, {**cuerpo, "id": pid}, int(indice))
            return vista_plan(evaluar_plan(repo, d), d, repo)
        case ("POST", ["programacion", pid, "registro"]):
            leer_prog(repo, pid)
            d = {**cuerpo, "id": pid}
            repo.guardar_programacion(d)
            n = repo.aplicar_registro(d)
            v = vista_plan(evaluar_plan(repo, d), d, repo)
            v["puntos_actualizados"] = n
            return v
        case ("POST", ["programacion", pid, "duplicar"]):
            d = leer_prog(repo, pid)
            d.update(id=repo.nuevo_id(), nombre=f"{d.get('nombre', '')} (copia)", estado="borrador")
            for dia in d.get("dias", []):
                for t in dia.get("tareas", []):
                    t.update(estado="programado", hora_real="", observacion="")
            repo.guardar_programacion(d)
            return {"id": d["id"]}
        case ("DELETE", ["programacion", pid]):
            repo.eliminar_programacion(pid)
            return {"ok": True}
        case ("GET", ["programacion", pid, "trazados"]):
            return trazados(evaluar_plan(repo, leer_prog(repo, pid)), repo)
        case ("POST", ["programacion", pid, "exportar"]):
            plan = evaluar_plan(repo, leer_prog(repo, pid))
            formatos = cuerpo.get("formatos") or ["csv", "xlsx", "hojas", "mapa", "ics"]
            archivos, errores = [], []
            for f in formatos:
                try:
                    archivos.append(exportar(plan, repo, f).name)
                except RuntimeError as e:
                    errores.append(str(e))
            if cuerpo.get("ics_personas"):
                for p in sorted({x.nombre for pd in plan.dias for x in pd.cuadrilla.integrantes}):
                    archivos.append(exportar(plan, repo, "ics", p).name)
            return {"archivos": archivos, "errores": errores, "carpeta": str(CARPETA_SALIDAS)}
    raise ErrorUsuario("Ruta no encontrada")


# --------------------------------------------------------------------------- #
# Servidor HTTP
# --------------------------------------------------------------------------- #
def crear_manejador(repo: Repositorio):
    class Manejador(BaseHTTPRequestHandler):
        def log_message(self, *args) -> None:  # silencioso
            pass

        def _enviar(self, codigo: int, cuerpo: bytes, tipo: str, descarga: str = "") -> None:
            self.send_response(codigo)
            self.send_header("Content-Type", tipo)
            self.send_header("Content-Length", str(len(cuerpo)))
            self.send_header("Cache-Control", "no-store")
            if descarga:
                self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{descarga}")
            self.end_headers()
            self.wfile.write(cuerpo)

        def _json(self, codigo: int, obj) -> None:
            self._enviar(codigo, json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8"),
                         "application/json; charset=utf-8")

        def _procesar(self, metodo: str) -> None:
            url = urlparse(self.path)
            ruta = unquote(url.path)
            if metodo == "GET" and ruta in ("/", "/index.html"):
                return self._enviar(200, PAGINA.read_bytes(), TIPOS[".html"])
            if metodo == "GET" and ruta.startswith("/salidas/"):
                archivo = CARPETA_SALIDAS / Path(ruta[len("/salidas/"):]).name
                if not archivo.is_file():
                    return self._json(404, {"error": "Archivo no encontrado"})
                tipo = TIPOS.get(archivo.suffix, "application/octet-stream")
                descarga = "" if archivo.suffix == ".html" else archivo.name
                return self._enviar(200, archivo.read_bytes(), tipo, descarga)
            if not ruta.startswith("/api/"):
                return self._json(404, {"error": "No encontrado"})
            try:
                n = int(self.headers.get("Content-Length") or 0)
                cuerpo = json.loads(self.rfile.read(n).decode("utf-8")) if n else {}
                with candado:
                    r = atender(repo, metodo, [p for p in ruta[5:].split("/") if p], parse_qs(url.query), cuerpo)
            except ErrorUsuario as e:
                return self._json(400, {"error": str(e)})
            except (ValueError, KeyError, TypeError) as e:
                return self._json(400, {"error": f"Datos no válidos: {e}"})
            except Exception as e:  # noqa: BLE001 - se informa en la página
                traceback.print_exc()
                return self._json(500, {"error": f"Error interno: {e}"})
            if isinstance(r, tuple):
                return self._enviar(200, *r)
            self._json(200, r)

        def do_GET(self) -> None:
            self._procesar("GET")

        def do_POST(self) -> None:
            self._procesar("POST")

        def do_PUT(self) -> None:
            self._procesar("PUT")

        def do_DELETE(self) -> None:
            self._procesar("DELETE")

    return Manejador


def puerto_libre(desde: int = 8765) -> int:
    for p in range(desde, desde + 50):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", p)) != 0:
                return p
    raise RuntimeError("No hay puertos libres")


def iniciar(repo: Repositorio, abrir: bool = True, puerto: int | None = None) -> None:
    """Sirve la interfaz en 127.0.0.1 (solo este equipo) hasta Ctrl+C."""
    puerto = puerto or puerto_libre()
    servidor = ThreadingHTTPServer(("127.0.0.1", puerto), crear_manejador(repo))
    url = f"http://127.0.0.1:{puerto}/"
    print(f"\n  Interfaz gráfica en {url}\n  (deje esta ventana abierta; Ctrl+C para cerrar)")
    if abrir:
        threading.Timer(0.8, webbrowser.open, (url,)).start()
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        print("\n  Interfaz cerrada.")
    finally:
        servidor.server_close()
