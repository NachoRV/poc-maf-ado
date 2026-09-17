"""T2.3 (parte determinista) - interpretar la respuesta a "¿lo confirmas?".

Fichero SIN prefijo `agente_` a proposito, y esa es la leccion: decidir si
alguien ha dicho "si" o "no" es comparar cadenas, no razonar. Preguntarselo a un
modelo seria pagar una llamada, anadir latencia y meter una fuente de error donde
no hacia falta ninguna.

El modelo solo entra cuando la respuesta NO es un si/no claro -- es decir, cuando
de verdad hay lenguaje que interpretar ("cambia la version a 21"). Esa frontera
es la tesis del proyecto aplicada al detalle mas pequeno del flujo.

Ejecuta la comprobacion con: .venv/bin/python -m requisitos.confirmacion
"""
import unicodedata
from enum import StrEnum

# Deliberadamente cortas y cerradas. Una lista larga de sinonimos empieza a
# adivinar, y adivinar es justo lo que este fichero existe para no hacer: ante
# la duda, la respuesta se trata como CORRECCION y la interpreta el modelo.
AFIRMACIONES = {"si", "vale", "ok", "okey", "correcto", "confirmo", "adelante", "dale", "listo", "perfecto"}
NEGACIONES = {"no", "negativo", "nope", "para", "espera"}


class Veredicto(StrEnum):
    CONFIRMA = "confirma"
    RECHAZA = "rechaza"
    CORRIGE = "corrige"


def _normalizar(mensaje: str) -> str:
    """Minusculas, sin tildes y sin puntuacion de los extremos.

    Sin quitar tildes, "sí" y "si" serian respuestas distintas y una de las dos
    caeria en CORRIGE, gastando una llamada al modelo para nada.
    """
    sin_tildes = "".join(
        c for c in unicodedata.normalize("NFD", mensaje.lower())
        if unicodedata.category(c) != "Mn"
    )
    return sin_tildes.strip().strip(".,;:!¡?¿ ")


def interpretar(mensaje: str) -> Veredicto:
    """Solo una respuesta EXACTAMENTE afirmativa o negativa se resuelve aqui.

    "si" -> CONFIRMA, pero "si pero cambia la version" -> CORRIGE, porque lleva
    informacion que hay que extraer. Comparar la frase entera y no buscar "si"
    dentro es lo que evita ese fallo: "si, pero no" empieza por "si" y no es una
    confirmacion.
    """
    normalizado = _normalizar(mensaje)
    if normalizado in AFIRMACIONES:
        return Veredicto.CONFIRMA
    if normalizado in NEGACIONES:
        return Veredicto.RECHAZA
    return Veredicto.CORRIGE


if __name__ == "__main__":
    casos = [
        ("si", Veredicto.CONFIRMA),
        ("Sí", Veredicto.CONFIRMA),
        ("  VALE. ", Veredicto.CONFIRMA),
        ("¿ok?", Veredicto.CONFIRMA),
        ("no", Veredicto.RECHAZA),
        ("No.", Veredicto.RECHAZA),
        ("si pero cambia la version a 21", Veredicto.CORRIGE),
        ("si, pero no", Veredicto.CORRIGE),
        ("cambia la version a 21", Veredicto.CORRIGE),
        ("el repo es otro", Veredicto.CORRIGE),
    ]
    fallos = 0
    for mensaje, esperado in casos:
        obtenido = interpretar(mensaje)
        marca = "OK " if obtenido == esperado else "MAL"
        fallos += obtenido != esperado
        print(f"  {marca} {mensaje!r:<38} -> {obtenido}")
    print(f"\n{len(casos) - fallos}/{len(casos)} correctos. Llamadas al modelo: 0.")
    raise SystemExit(1 if fallos else 0)
