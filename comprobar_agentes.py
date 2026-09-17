"""Comprueba la convencion de nombres: si al ejecutarse llama al LLM, se llama agente_*.

La regla, decidida el 2026-09-17:

    Un fichero cuyo nombre empieza por `agente_` es un fichero que, AL
    EJECUTARSE, acaba llamando a un modelo de lenguaje. Cualquier otro no.

"Al ejecutarse" es transitivo, no literal: requisitos/agente_recolector.py no
importa `llm/` -- importa agente_extractor.py, que si lo hace. Lo que importa es
si ejecutarlo cuesta llamadas al modelo, no de que linea sale la llamada.

Existe para que la pregunta "¿que partes de este sistema pueden alucinar?" se
responda mirando un `ls`, y para que siga siendo cierto cuando el proyecto crezca
-- una convencion que solo vive en la cabeza de alguien se rompe en la tercera
tarea. Aqui se comprueba y devuelve codigo de salida, asi que puede colgarse de
un hook o de CI.

Excepcion deliberada: el paquete `llm/` no lleva prefijo. No son agentes, son la
infraestructura que los agentes usan, y el nombre de la carpeta ya avisa.

Ejecuta con (desde la raiz del repo): .venv/bin/python comprobar_agentes.py
"""
import ast
import sys
from pathlib import Path

PREFIJO = "agente_"
PAQUETE_INFRAESTRUCTURA = "llm"
IGNORAR = (".venv", "__pycache__")

# Importar de `llm/` no es lo mismo que llamar al modelo. `llamadas_al_modelo` y
# `reiniciar_contador` son OBSERVABILIDAD: orquestacion/flujo.py las usa para
# informar de cuantas llamadas hubo, y una ejecucion suya mide cero. Solo estas
# dos funciones construyen algo con lo que se pueda llamar de verdad.
CONSTRUYEN_CLIENTE = {"construir_cliente", "kwargs_json"}


def modulos() -> dict[str, set[str]]:
    """{modulo: lo que importa}, para todo el codigo del proyecto.

    De `llm/` solo se anotan los imports que construyen un cliente; traerse el
    contador no cuenta (ver CONSTRUYEN_CLIENTE).
    """
    encontrados = {}
    for fichero in sorted(Path(".").rglob("*.py")):
        if any(parte in str(fichero) for parte in IGNORAR):
            continue
        importados = set()
        for nodo in ast.walk(ast.parse(fichero.read_text())):
            if isinstance(nodo, ast.ImportFrom) and nodo.module:
                if nodo.module.split(".")[0] == PAQUETE_INFRAESTRUCTURA:
                    if not any(a.name in CONSTRUYEN_CLIENTE for a in nodo.names):
                        continue
                importados.add(nodo.module)
            elif isinstance(nodo, ast.Import):
                importados.update(alias.name for alias in nodo.names)
        encontrados[str(fichero.with_suffix("")).replace("/", ".")] = importados
    return encontrados


def llama_al_llm(modulo: str, grafo: dict[str, set[str]], visitados: set[str] | None = None) -> bool:
    """Transitivo: True si ejecutar `modulo` puede acabar llamando al modelo."""
    visitados = visitados if visitados is not None else set()
    if modulo in visitados:
        return False
    visitados.add(modulo)
    for importado in grafo.get(modulo, ()):
        if importado.split(".")[0] == PAQUETE_INFRAESTRUCTURA:
            return True
        if importado in grafo and llama_al_llm(importado, grafo, visitados):
            return True
    return False


def main() -> None:
    grafo = modulos()
    incumplen, sobran, agentes, deterministas = [], [], [], []

    for modulo in sorted(grafo):
        if modulo.split(".")[0] == PAQUETE_INFRAESTRUCTURA:
            continue
        nombre = modulo.split(".")[-1]
        usa_llm = llama_al_llm(modulo, grafo)
        marcado = nombre.startswith(PREFIJO)

        if usa_llm and not marcado:
            incumplen.append(modulo)
        elif marcado and not usa_llm:
            sobran.append(modulo)
        elif usa_llm:
            agentes.append(modulo)
        else:
            deterministas.append(modulo)

    print(f"AGENTES ({len(agentes)}) -- ejecutarlos llama al modelo:")
    for m in agentes:
        print(f"  {m}")
    print(f"\nDETERMINISTAS ({len(deterministas)}) -- ejecutarlos no cuesta ni una llamada:")
    for m in deterministas:
        print(f"  {m}")

    if incumplen:
        print(f"\nINCUMPLEN: llaman al modelo y NO se llaman {PREFIJO}*:", file=sys.stderr)
        for m in incumplen:
            print(f"  {m}", file=sys.stderr)
    if sobran:
        print(f"\nINCUMPLEN: se llaman {PREFIJO}* pero NO llaman al modelo:", file=sys.stderr)
        for m in sobran:
            print(f"  {m}", file=sys.stderr)

    if incumplen or sobran:
        sys.exit(1)
    print("\nOK: la convencion se cumple.")


if __name__ == "__main__":
    main()
