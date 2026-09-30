#!/usr/bin/env python3
"""Planificador de Monitoreos Ambientales v3.

    python main.py           # menú de consola
    python main.py --web     # abre directamente la interfaz gráfica en el navegador
    python main.py --demo    # datos de ejemplo + programación + exportación
"""

import sys
from datetime import date, timedelta

from monitoreo.consola import main_consola
from monitoreo.modelo import Repositorio


def main() -> None:
    for flujo in (sys.stdout, sys.stderr):
        try:
            flujo.reconfigure(errors="replace")
        except AttributeError:
            pass
    repo = Repositorio()
    if "--demo" in sys.argv:
        from monitoreo.demo import cargar_demo
        from monitoreo.planificador import plan_a_dict, planificar
        from monitoreo.reportes import exportar_todo, imprimir_plan
        cargar_demo(repo)
        plan = planificar(repo, [p for p in repo.puntos if p.activo], date.today() + timedelta(days=1),
                          nombre="Demo")
        imprimir_plan(plan, repo.config)
        repo.guardar_programacion(plan_a_dict(plan))
        for r in exportar_todo(plan, repo):
            print(f"  {r}")
        return
    if "--web" in sys.argv:
        from monitoreo.web import iniciar
        iniciar(repo)
        return
    main_consola(repo)


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        print("\nHasta luego.")
