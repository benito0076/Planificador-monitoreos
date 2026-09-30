"""Interfaz de consola (menús) e importación/exportación CSV genérica."""

from __future__ import annotations

import csv
import io
from dataclasses import MISSING, asdict, fields
from datetime import date, timedelta
from pathlib import Path

from .distancias import Distancias, nodos
from .modelo import (BASE, CARPETA_SALIDAS, DIAS_SEMANA, ESQUEMA_CONFIG, ESQUEMAS, ESTADOS_PLAN, ESTADOS_TAREA,
                     FRECUENCIAS, MATRICES, SI, Campo, Repositorio, _crear, calcular_alertas, normalizar,
                     parse_hora)
from .planificador import cumplimiento, evaluar_plan, plan_a_dict, planificar, resumen
from .reportes import exportar, exportar_todo, imprimir_plan


# --------------------------------------------------------------------------- #
# CSV genérico (compartido con la interfaz web)
# --------------------------------------------------------------------------- #
def _a_texto(v) -> str:
    if isinstance(v, bool):
        return "si" if v else "no"
    if isinstance(v, list):
        return "|".join(map(str, v))
    if isinstance(v, float):
        return f"{v:g}".replace(".", ",")
    return str(v)


def _convertir(valor: str, tipo: str):
    v = valor.strip()
    if tipo == "bool":
        return v.lower() in SI
    if tipo == "int":
        return int(float(v.replace(",", ".")))
    if tipo == "float":
        return float(v.replace(",", "."))
    if tipo == "list[int]":
        return [int(x) for x in v.replace(";", "|").split("|") if x.strip()]
    if tipo.startswith("list"):
        return [x.strip().lower() for x in v.replace(";", "|").split("|") if x.strip()]
    return v


def entidad_a_csv(repo: Repositorio, entidad: str) -> str:
    cls = ESQUEMAS[entidad]["cls"]
    nombres = [f.name for f in fields(cls)]
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";", lineterminator="\r\n")
    w.writerow(nombres)
    for x in repo.lista(entidad):
        w.writerow([_a_texto(getattr(x, n)) for n in nombres])
    return buf.getvalue()


def importar_csv_texto(repo: Repositorio, entidad: str, texto: str) -> dict:
    esq = ESQUEMAS[entidad]
    cls, clave, lista = esq["cls"], esq["clave"], repo.lista(entidad)
    texto = texto.lstrip("﻿")
    if not texto.strip():
        return {"nuevos": 0, "actualizados": 0, "errores": ["archivo vacío"]}
    dialecto = csv.Sniffer().sniff(texto.splitlines()[0], delimiters=",;\t")
    campos = {f.name: f for f in fields(cls)}
    obligatorios = [f.name for f in fields(cls) if f.default is MISSING and f.default_factory is MISSING]
    nuevos = actualizados = 0
    errores: list[str] = []
    for n, fila in enumerate(csv.DictReader(io.StringIO(texto), dialect=dialecto), 2):
        fila = {k.strip().lower(): (v or "") for k, v in fila.items() if k}
        try:
            faltan = [c for c in obligatorios if not fila.get(c, "").strip()]
            if faltan:
                raise ValueError(f"faltan columnas/valores: {', '.join(faltan)}")
            obj = cls(**{k: _convertir(v, campos[k].type) for k, v in fila.items() if k in campos and v.strip()})
            normalizar(obj)
        except (ValueError, TypeError) as e:
            errores.append(f"fila {n}: {e}")
            continue
        idx = next((i for i, x in enumerate(lista) if getattr(x, clave) == getattr(obj, clave)), None)
        if idx is None:
            lista.append(obj)
            nuevos += 1
        else:
            lista[idx] = obj
            actualizados += 1
    repo.guardar()
    return {"nuevos": nuevos, "actualizados": actualizados, "errores": errores}


# --------------------------------------------------------------------------- #
# Entrada por consola
# --------------------------------------------------------------------------- #
def pedir(txt: str, tipo=str, defecto=None):
    sufijo = f" [{defecto}]" if defecto not in (None, "") else ""
    while True:
        r = input(f"{txt}{sufijo}: ").strip()
        if not r and defecto is not None:
            return defecto
        try:
            if tipo is bool:
                if r.lower() in SI:
                    return True
                if r.lower() in ("n", "no", "0", "false"):
                    return False
                raise ValueError
            return tipo(r.replace(",", ".") if tipo is float else r)
        except ValueError:
            print("  Valor no válido, intente de nuevo.")


def pedir_campo(c: Campo, actual):
    """Pide un valor según el tipo del campo. Enter conserva; '-' vacía (si aplica)."""
    et = c.etiqueta + (f" ({c.ayuda})" if c.ayuda else "")
    while True:
        if c.tipo in ("multi", "tags", "dates", "dias"):
            if c.tipo == "multi":
                print(f"  Opciones: {', '.join(map(str, c.opciones))}")
            if c.tipo == "dias":
                print("  0=Lun 1=Mar 2=Mié 3=Jue 4=Vie 5=Sáb 6=Dom")
            mostrar = ",".join(map(str, actual)) if actual else ""
            ayuda = " (AAAA-MM-DD, rango con '..')" if c.tipo == "dates" else ""
            r = input(f"{et}{ayuda} separado por comas, '-' = ninguno{f' [{mostrar}]' if mostrar else ''}: ").strip()
            if not r:
                return actual
            if r == "-":
                return []
            items = [x.strip() for x in r.split(",") if x.strip()]
            try:
                if c.tipo == "dias":
                    return sorted({int(x) for x in items if 0 <= int(x) <= 6})
                if c.tipo == "dates":
                    out = []
                    for x in items:
                        if ".." in x:
                            a, b = (date.fromisoformat(y.strip()) for y in x.split(".."))
                            while a <= b:
                                out.append(a.isoformat())
                                a += timedelta(days=1)
                        else:
                            out.append(date.fromisoformat(x).isoformat())
                    return sorted(set(out))
                items = [x.lower() for x in items]
                if c.tipo == "multi":
                    malos = [x for x in items if x not in c.opciones]
                    if malos:
                        print(f"  Ignorados: {', '.join(malos)}")
                    return [x for x in items if x in c.opciones]
                return items
            except ValueError:
                print("  Valor no válido.")
                continue
        mostrar = "" if actual in (None, "") else ("si" if actual is True else "no" if actual is False else actual)
        if c.tipo == "select":
            ops = [o[0] if isinstance(o, list) else o for o in c.opciones]
            print(f"  Opciones: {', '.join(map(str, ops))}")
        r = input(f"{et}{' (s/n)' if c.tipo == 'bool' else ''}{f' [{mostrar}]' if mostrar != '' else ''}: ").strip()
        if not r:
            if c.requerido and actual in (None, ""):
                print("  Campo obligatorio.")
                continue
            return actual
        if r == "-" and not c.requerido and c.tipo in ("text", "date", "time"):
            return ""
        try:
            if c.tipo == "bool":
                return r.lower() in SI
            if c.tipo == "int":
                return int(r)
            if c.tipo == "float":
                return float(r.replace(",", "."))
            if c.tipo == "date":
                return date.fromisoformat(r).isoformat()
            if c.tipo == "time":
                return f"{parse_hora(r):%H:%M}"
            if c.tipo == "select":
                v = type(ops[0])(r) if ops else r
                if v not in ops:
                    raise ValueError
                return v
            return r
        except (ValueError, IndexError):
            print("  Valor no válido.")


def form_generico(entidad: str, obj=None, repo: Repositorio | None = None):
    esq = ESQUEMAS[entidad]
    cls = esq["cls"]
    datos = asdict(obj) if obj else {}
    if entidad == "puntos" and repo and repo.tipos_equipo():
        print(f"  Tipos de equipo registrados: {', '.join(repo.tipos_equipo())}")
    avanzados = [c for c in esq["campos"] if c.avanzado]
    for c in esq["campos"]:
        if c.avanzado:
            continue
        datos[c.nombre] = pedir_campo(c, datos.get(c.nombre, _defecto(cls, c.nombre)))
    if avanzados and pedir("¿Editar opciones avanzadas? (s/n)", bool, False):
        for c in avanzados:
            datos[c.nombre] = pedir_campo(c, datos.get(c.nombre, _defecto(cls, c.nombre)))
    nuevo = _crear(cls, datos)
    normalizar(nuevo)
    return nuevo


def _defecto(cls, nombre):
    f = next(f for f in fields(cls) if f.name == nombre)
    if f.default is not MISSING:
        return f.default
    if f.default_factory is not MISSING:
        return f.default_factory()
    return None


def elegir(items: list, etiqueta) -> int | None:
    if not items:
        print("  (lista vacía)")
        return None
    for i, x in enumerate(items, 1):
        print(f"  {i:>3}. {etiqueta(x)}")
    n = pedir("Número (0 cancela)", int, 0)
    return n - 1 if 1 <= n <= len(items) else None


def fmt_registro(entidad: str, x) -> str:
    if entidad == "personal":
        return (f"{x.nombre:<22} {x.cargo:<14} {'conduce' if x.conduce else '       '}  [{', '.join(x.competencias)}]"
                f"{' | hab. hasta ' + x.habilitado_hasta if x.habilitado_hasta else ''}{'' if x.activo else ' [INACTIVO]'}")
    if entidad == "vehiculos":
        return (f"{x.placa:<10} {x.descripcion:<18} {x.capacidad} plazas {'4x4' if x.es_4x4 else '4x2'} "
                f"{x.rendimiento_km_l:g} km/L {x.costo_dia:g}/día{'' if x.activo else ' [INACTIVO]'}")
    if entidad == "equipos":
        return (f"{x.codigo:<12} {x.tipo:<18} {x.descripcion[:24]:<24} "
                f"cal. {x.calibracion_vence or '—'}{'' if x.activo else ' [INACTIVO]'}")
    if entidad == "alojamientos":
        return f"{x.nombre:<28} ({x.lat:.5f}, {x.lon:.5f}) {x.costo_noche_persona:g}/persona"
    return (f"{x.codigo:<8} {x.nombre[:28]:<28} {x.matriz:<10} ({x.lat:.5f}, {x.lon:.5f}) {x.duracion_min} min "
            f"P{x.prioridad} {x.frecuencia}{'' if x.activo else ' [INACTIVO]'}")


# --------------------------------------------------------------------------- #
# Menús
# --------------------------------------------------------------------------- #
def menu_entidad(repo: Repositorio, entidad: str) -> None:
    esq = ESQUEMAS[entidad]
    lista, clave = repo.lista(entidad), esq["clave"]
    fmt = lambda x: fmt_registro(entidad, x)
    while True:
        print(f"\n--- {esq['titulo'].upper()} ({len(lista)}) ---")
        for x in lista:
            print("  " + fmt(x))
        print("\n 1) Agregar  2) Editar  3) Eliminar  4) Importar CSV  5) Exportar CSV  0) Volver")
        op = input("> ").strip()
        try:
            if op == "1":
                nuevo = form_generico(entidad, None, repo)
                if any(getattr(x, clave) == getattr(nuevo, clave) for x in lista):
                    print("  Ya existe un registro con esa clave.")
                else:
                    lista.append(nuevo)
                    repo.guardar()
            elif op == "2":
                i = elegir(lista, fmt)
                if i is not None:
                    lista[i] = form_generico(entidad, lista[i], repo)
                    repo.guardar()
            elif op == "3":
                i = elegir(lista, fmt)
                if i is not None and pedir(f"¿Eliminar {getattr(lista[i], clave)}? (s/n)", bool, False):
                    lista.pop(i)
                    repo.guardar()
            elif op == "4":
                ruta = Path(pedir("Ruta del archivo CSV", str).strip('"'))
                if not ruta.exists():
                    print("  Archivo no encontrado.")
                    continue
                r = importar_csv_texto(repo, entidad, ruta.read_text(encoding="utf-8-sig"))
                print(f"  Nuevos: {r['nuevos']} | Actualizados: {r['actualizados']} | Con error: {len(r['errores'])}")
                for e in r["errores"]:
                    print(f"   - {e}")
            elif op == "5":
                CARPETA_SALIDAS.mkdir(exist_ok=True)
                ruta = CARPETA_SALIDAS / f"{entidad}.csv"
                ruta.write_text(entidad_a_csv(repo, entidad), encoding="utf-8-sig")
                print(f"  Exportado: {ruta}\n  Edítelo en Excel y use 'Importar CSV' (se actualiza por {clave}).")
            elif op == "0":
                return
        except ValueError as e:
            print(f"  Registro no válido: {e}")


def menu_config(repo: Repositorio) -> None:
    secciones = list(dict.fromkeys(c.seccion for c in ESQUEMA_CONFIG))
    while True:
        print("\n--- CONFIGURACIÓN ---")
        for i, s in enumerate(secciones, 1):
            print(f"  {i}) {s}")
        print("  0) Volver")
        op = pedir("Sección", int, 0)
        if not 1 <= op <= len(secciones):
            return
        for c in ESQUEMA_CONFIG:
            if c.seccion == secciones[op - 1]:
                setattr(repo.config, c.nombre, pedir_campo(c, getattr(repo.config, c.nombre)))
        repo.guardar()
        print("  Guardado.")


def matriz_tiempos(repo: Repositorio) -> None:
    pts = [p for p in repo.puntos if p.activo]
    if not pts:
        print("No hay puntos registrados.")
        return
    dist = Distancias(nodos(repo, pts), repo.config)
    vels = [v.velocidad_kmh for v in repo.vehiculos if v.activo] or [40.0]
    vel = sum(vels) / len(vels)
    codigos = [BASE, *(p.codigo for p in pts)]
    etiqueta = lambda c: "BASE" if c == BASE else c
    ancho = max(8, *(len(etiqueta(c)) + 1 for c in codigos))
    for titulo, valor in (("Tiempo de traslado (min), fila = origen, columna = destino",
                           lambda a, b: dist.minutos(a, b, vel)), ("Distancia (km)", dist.km)):
        print(f"\n{titulo}:")
        print(" " * ancho + "".join(f"{etiqueta(c):>{ancho}}" for c in codigos))
        for a in codigos:
            print(f"{etiqueta(a):<{ancho}}" + "".join(f"{valor(a, b):>{ancho}.0f}" for b in codigos))


def mostrar_alertas(repo: Repositorio) -> None:
    a = calcular_alertas(repo)
    print("\n--- ALERTAS DE VENCIMIENTO ---")
    for v in a["vencimientos"]:
        print(f"  {'✗' if v['nivel'] == 'vencido' else '!'} {v['grupo']:<9} {v['quien']:<20} {v['que']:<34} {v['texto']}")
    if not a["vencimientos"]:
        print("  Sin vencimientos próximos.")
    print("\n--- PUNTOS SEGÚN FRECUENCIA ---")
    for p in a["puntos"]:
        print(f"  {p['codigo']:<8} {p['frecuencia']:<11} último: {p['ultimo'] or '—':<10}  {p['texto']}")


def seleccionar_puntos(repo: Repositorio):
    activos = [p for p in repo.puntos if p.activo]
    print(f"\n  Puntos activos: {len(activos)}")
    print("  1) Todos los activos\n  2) Solo los que vencen hasta una fecha (según frecuencia)"
          "\n  3) Por matriz\n  4) Códigos específicos")
    op = pedir("Opción", str, "1")
    if op == "2":
        hasta = pedir("Incluir puntos que vencen hasta (AAAA-MM-DD)", date.fromisoformat,
                      date.today() + timedelta(days=30))
        activos = [p for p in activos if (f := p.proxima_fecha()) is not None and f <= hasta]
    elif op == "3":
        m = pedir_campo(Campo("m", "Matrices", "multi", MATRICES), [])
        activos = [p for p in activos if p.matriz in m]
    elif op == "4":
        sel = {x.strip().upper() for x in input("Códigos (separados por comas): ").split(",")}
        activos = [p for p in activos if p.codigo in sel]
    print(f"  Seleccionados: {len(activos)} punto(s)")
    return activos


def menu_planificar(repo: Repositorio) -> None:
    puntos = seleccionar_puntos(repo)
    if not puntos:
        print("  No hay puntos que programar.")
        return
    inicio = pedir("Fecha de inicio (AAAA-MM-DD)", date.fromisoformat, date.today() + timedelta(days=1))
    max_dias = pedir("Máximo de días de campo", int, 30)
    nombre = pedir("Nombre de la programación", str, f"Programación desde {inicio:%d/%m/%Y}")
    plan = planificar(repo, puntos, inicio, max_dias, nombre)
    imprimir_plan(plan, repo.config)
    if not plan.dias:
        return
    repo.guardar_programacion(plan_a_dict(plan))
    print(f"\n  Guardada como borrador (id {plan.id}). Puede ajustarla en la interfaz gráfica.")
    if pedir("¿Exportar (CSV, Excel, hojas de ruta, mapa, calendario)? (s/n)", bool, True):
        for r in exportar_todo(plan, repo):
            print(f"  {r}")


def menu_programaciones(repo: Repositorio) -> None:
    while True:
        progs = repo.listar_programaciones()
        print("\n--- PROGRAMACIONES ---")
        i = elegir(progs, lambda d: f"{d['nombre']:<40} inicio {d['fecha_inicio']}  "
                                    f"{ESTADOS_PLAN.get(d.get('estado'), '')}  {len(d['dias'])} cuadrilla-días")
        if i is None:
            return
        d = progs[i]
        plan = evaluar_plan(repo, d)
        cu = cumplimiento(plan)
        print(f"\n  {plan.nombre} | cumplimiento {cu['porcentaje']}% | "
              + ", ".join(f"{ESTADOS_TAREA[k]}: {v}" for k, v in cu["por_estado"].items()))
        print(" 1) Ver detalle  2) Registrar ejecución en campo  3) Exportar  4) Cambiar estado  5) Eliminar  0) Volver")
        op = input("> ").strip()
        if op == "1":
            imprimir_plan(plan, repo.config)
        elif op == "2":
            registrar_campo(repo, d)
        elif op == "3":
            for r in exportar_todo(plan, repo):
                print(f"  {r}")
        elif op == "4":
            print(f"  Estados: {', '.join(ESTADOS_PLAN)}")
            e = pedir("Nuevo estado", str, d.get("estado", "borrador"))
            if e in ESTADOS_PLAN:
                d["estado"] = e
                repo.guardar_programacion(d)
        elif op == "5" and pedir("¿Eliminar esta programación? (s/n)", bool, False):
            repo.eliminar_programacion(d["id"])


def registrar_campo(repo: Repositorio, d: dict) -> None:
    tareas = [(dia, t) for dia in d["dias"] for t in dia["tareas"]]
    while True:
        print()
        i = elegir(tareas, lambda x: f"{x[0]['fecha']} {x[0]['cuadrilla']:<4} {x[1]['codigo']:<8} "
                                     f"{x[1]['tipo']:<11} {ESTADOS_TAREA.get(x[1].get('estado', 'programado'))}"
                                     f"{' ' + x[1]['hora_real'] if x[1].get('hora_real') else ''}")
        if i is None:
            break
        t = tareas[i][1]
        print(f"  Estados: {', '.join(ESTADOS_TAREA)}")
        e = pedir("Estado", str, t.get("estado", "programado"))
        if e not in ESTADOS_TAREA:
            print("  Estado no válido.")
            continue
        t["estado"] = e
        t["hora_real"] = pedir_campo(Campo("h", "Hora real (HH:MM)", "time"), t.get("hora_real", ""))
        t["observacion"] = pedir_campo(Campo("o", "Observación"), t.get("observacion", ""))
    repo.guardar_programacion(d)
    n = repo.aplicar_registro(d)
    print(f"  Registro guardado. {n} punto(s) actualizados con su fecha de último monitoreo.")


def main_consola(repo: Repositorio) -> None:
    acciones = {
        "1": lambda: menu_entidad(repo, "personal"),
        "2": lambda: menu_entidad(repo, "vehiculos"),
        "3": lambda: menu_entidad(repo, "equipos"),
        "4": lambda: menu_entidad(repo, "puntos"),
        "5": lambda: menu_entidad(repo, "alojamientos"),
        "6": lambda: menu_config(repo),
        "7": lambda: matriz_tiempos(repo),
        "8": lambda: mostrar_alertas(repo),
        "9": lambda: menu_planificar(repo),
        "10": lambda: menu_programaciones(repo),
    }
    while True:
        print(f"""
╔══════════════════════════════════════════════╗
║   PLANIFICADOR DE MONITOREOS AMBIENTALES     ║
╚══════════════════════════════════════════════╝
 Personal: {len(repo.personal)} | Vehículos: {len(repo.vehiculos)} | Equipos: {len(repo.equipos)} | Puntos: {len(repo.puntos)} | Alojamientos: {len(repo.alojamientos)}
  1) Personal              6) Configuración
  2) Vehículos             7) Matriz de tiempos
  3) Equipos               8) Alertas y vencimientos
  4) Puntos de monitoreo   9) Generar programación
  5) Alojamientos         10) Programaciones (registro de campo, exportar)
 11) Abrir interfaz gráfica
 12) Cargar datos de ejemplo
  0) Salir""")
        op = input("> ").strip()
        if op == "0":
            break
        try:
            if op == "11":
                from .web import iniciar
                iniciar(repo)
            elif op == "12":
                if pedir("Esto reemplaza los datos actuales. ¿Continuar? (s/n)", bool, False):
                    from .demo import cargar_demo
                    cargar_demo(repo)
                    print("  Datos de ejemplo cargados.")
            elif op in acciones:
                acciones[op]()
        except (KeyboardInterrupt, EOFError):
            print("\n  Operación cancelada.")
