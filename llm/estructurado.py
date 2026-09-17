"""Pedirle al modelo un JSON con forma conocida, validarlo y reintentar una vez.

Se extrae aqui cuando aparece el SEGUNDO consumidor (requisitos/agente_extractor
y seleccion/agente_hibrido), no antes: mismo criterio que se uso para ado/git.py.

El patron, que es el que hace fiable a un agente:

    1. Se pide el JSON describiendo la forma en el prompt.
    2. Se limpia lo que es ruido de FORMATO, no alucinacion: bloques
       <think>...</think> de modelos con razonamiento y vallas ```json.
    3. Se valida contra un modelo Pydantic. Una clave de mas o un valor fuera de
       rango es un error, no un dato.
    4. Si no vale, se reintenta UNA vez metiendo el error EXACTO en el historial.
       El modelo ve por que fallo, no solo que fallo.
    5. Si el segundo intento tampoco vale, excepcion controlada -- nunca una
       alucinacion colada en silencio.

`validar` lo aporta quien llama: este modulo sabe hablar con un modelo y
reintentar, pero no sabe que es una respuesta valida en tu dominio. Esa decision
vive en el modulo determinista correspondiente.
"""
import json
import os
import re
from typing import Callable, TypeVar

from pydantic import BaseModel, ValidationError

from llm.cliente import construir_cliente, kwargs_json

MAX_INTENTOS = 2
MAX_TOKENS = int(os.environ.get("LLM_MAX_TOKENS", "8000"))

T = TypeVar("T", bound=BaseModel)


class SinRespuestaValida(RuntimeError):
    """El modelo no produjo algo utilizable tras agotar los intentos."""


def limpiar(crudo: str) -> dict:
    """Quita el ruido de formato y parsea. Lo que NO se tolera es un JSON roto."""
    texto = re.sub(r"<think>.*?</think>", "", crudo, flags=re.DOTALL).strip()
    if texto.startswith("```"):
        texto = re.sub(r"^```[a-zA-Z]*\n?|\n?```$", "", texto).strip()
    return json.loads(texto)


def pedir_json(mensajes: list[dict], forma: type[T],
               *, validar: Callable[[T], None] | None = None) -> T:
    """Una respuesta del modelo con la forma de `forma`, validada."""
    cliente, modelo = construir_cliente()
    mensajes = list(mensajes)

    for intento in range(1, MAX_INTENTOS + 1):
        respuesta = cliente.chat.completions.create(
            model=modelo, messages=mensajes, max_tokens=MAX_TOKENS, **kwargs_json()
        )
        eleccion = respuesta.choices[0]
        crudo = eleccion.message.content or ""

        # Un modelo de razonamiento que agota el presupuesto pensando devuelve
        # content="" con finish_reason="length". Sin esta rama el sintoma seria
        # "JSON invalido: Expecting value line 1 column 1", que manda a mirar el
        # prompt cuando el problema es el presupuesto. Se reintenta porque el
        # modelo es estocastico: medido, el mismo prompt oscilaba entre 1518 y
        # mas de 8000 tokens de razonamiento.
        if not crudo.strip() and eleccion.finish_reason == "length":
            if intento == MAX_INTENTOS:
                raise SinRespuestaValida(
                    f"el modelo agoto los {MAX_TOKENS} tokens y no llego a responder. "
                    "Sube LLM_MAX_TOKENS, o usa un modelo sin razonamiento."
                )
            mensajes.append({
                "role": "user",
                "content": "Te has quedado sin presupuesto de tokens antes de responder. "
                           "Responde AHORA directamente con el JSON, sin razonar en profundidad.",
            })
            continue

        try:
            resultado = forma(**limpiar(crudo))
            if validar:
                validar(resultado)
            return resultado
        except (json.JSONDecodeError, ValidationError, ValueError) as error:
            if intento == MAX_INTENTOS:
                raise SinRespuestaValida(
                    f"el modelo no devolvio algo valido tras {MAX_INTENTOS} intentos: {error}"
                ) from error
            mensajes.append({"role": "assistant", "content": crudo})
            mensajes.append({
                "role": "user",
                "content": f"Tu respuesta no es valida: {error}\nCorrigela y responde SOLO con el JSON.",
            })

    raise AssertionError("inalcanzable")
