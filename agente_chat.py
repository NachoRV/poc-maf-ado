"""Punto de entrada de la PoC: una conversacion que termina en un Pull Request.

    .venv/bin/python agente_chat.py

Todo el trabajo esta en orquestacion/agente_flujo.py. Este fichero solo es el puente
entre una terminal y el workflow: cuando el flujo se suspende pidiendo algo, lo
pregunta y devuelve la respuesta.

Fijate en lo poco que hay aqui, y en lo que ESO significa: el flujo no depende de
que exista una terminal. Suspende, guarda su estado y espera. La misma maquina
podria contestarse desde una web, desde un bot o desde otro proceso dos dias
despues; lo unico que habria que cambiar es este fichero.
"""
import asyncio

from dotenv import load_dotenv

from llm.cliente import llamadas_al_modelo
from orquestacion.agente_flujo import ejecutar, informe
from requisitos.confirmacion import Veredicto, interpretar

SALIR = ("salir", "exit", "quit")


def preguntar(texto: str, tipo: type):
    """Responde a una peticion del flujo. None significa "el humano se fue"."""
    print(f"\n\033[1magente\033[0m: {texto}")
    respuesta = input("\033[1mtu\033[0m: ").strip()

    if respuesta.lower() in SALIR:
        return None
    # Un nodo que pide un bool (la puerta antes de escribir en ADO) recibe un
    # bool: la traduccion la hace el mismo clasificador determinista del chat,
    # sin gastar una llamada al modelo en decidir si alguien ha dicho que si.
    return (interpretar(respuesta) is Veredicto.CONFIRMA) if tipo is bool else respuesta


async def main() -> None:
    load_dotenv()
    print("Describe la pipeline que necesitas. Escribe 'salir' para dejarlo.")

    final = await ejecutar(preguntar)

    if final is None:
        print("\n\033[1m=== sin terminar ===\033[0m No se ha escrito nada en Azure DevOps.")
        return

    print(f"\n{informe(final.traza, llamadas_al_modelo())}")
    if final.confirmado is False:
        print("\n  No se ha escrito nada en Azure DevOps.")


if __name__ == "__main__":
    asyncio.run(main())
