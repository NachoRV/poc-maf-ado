"""T2.2 (parte LLM) - traduce lo que dice un humano a campos de `Requisitos`.

ESTE ES EL UNICO FICHERO DEL BLOQUE 2 QUE HABLA CON UN MODELO. Todo lo demas
--llevar el historial, fundir el estado, decidir si falta algo, decidir cuando
parar-- es codigo normal y vive en requisitos/recolector.py y
requisitos/esquema.py. La separacion es fisica a proposito: se comprueba con un
grep, no leyendo.

Y ojo con la palabra "agente": esto NO lo es. Un agente decide QUE HACER (que
herramienta llamar, si seguir o parar). Aqui el modelo recibe un estado, propone
un diccionario, y termina. Quien pregunta, cuando se para y que se guarda lo
decide nuestro codigo. Un agente puede sorprenderte; esto no puede.

Al modelo se le da TODO masticado -- el estado actual serializado, que slots
bloquean y cuales solo se recomiendan -- para que su unico trabajo sea traducir
la ultima frase del humano a campos. No tiene que recordar (le pasamos el
estado), ni deducir que falta (se lo damos), ni decidir si ha terminado (no es
suyo). Cuanto mas estrecho es el trabajo, menos hay que adivinar.

Ejecuta con: .venv/bin/python -m requisitos.extractor
"""
import json
import os
import re
from typing import Any, Callable

from pydantic import BaseModel, ConfigDict, ValidationError

from llm.cliente import construir_cliente, kwargs_json
from requisitos.esquema import DESCRIPCIONES, Requisitos

MAX_INTENTOS = 2

# Presupuesto de tokens por turno. Alto a proposito: qwen3.5 es un modelo de
# RAZONAMIENTO y gasta ~1500 tokens pensando ANTES de escribir la respuesta.
# Con 2000 se quedaba sin presupuesto a mitad del razonamiento y devolvia
# content="" con finish_reason="length" -- comprobado en real. Se probaron las
# dos formas documentadas de apagar el razonamiento con este modelo en LM Studio,
# extra_body={"chat_template_kwargs": {"enable_thinking": false}} y el sufijo
# "/no_think" en el mensaje: LAS DOS SE IGNORAN. Asi que se paga el presupuesto.
#
# 8000 y no mas: subirlo a 16000 hizo que LM Studio recargara el modelo a mitad
# de peticion y acabara crasheandolo ("The model has crashed", exit code null),
# casi seguro por pasarse del contexto cargado. 8000 es el valor con el que hay
# una conversacion completa funcionando de principio a fin.
MAX_TOKENS = int(os.environ.get("LLM_MAX_TOKENS", "8000"))


class PropuestaTurno(BaseModel):
    """Lo que el modelo tiene permitido devolver. Nada mas.

    `extra="forbid"`: una clave de mas es un error de validacion, y ese error se
    le devuelve como feedback en el reintento.
    """

    model_config = ConfigDict(extra="forbid")

    requisitos_actualizados: dict[str, Any] = {}
    pregunta_al_usuario: str | None = None


def _instrucciones() -> str:
    campos = "\n".join(f"  - {nombre}: {desc}" for nombre, desc in DESCRIPCIONES.items())
    return f"""\
Eres la parte de un sistema que traduce lo que dice un usuario a campos de un
formulario. No decides nada mas: no eliges plantillas, no generas pipelines, no
declaras terminada la conversacion. Solo traduces y, si falta algo, preguntas.

Campos del formulario:
{campos}
  - entornos: lista con alguno de "dev", "acc", "pro". Por defecto los tres.
  - variables_extra: variables que el usuario quiera anadir, agrupadas por ambito
    ("common", "dev", "acc" o "pro"). Ejemplo: {{"pro": {{"replicas": "8"}}}}
  - notas: texto libre que el usuario diga y NO encaje en ningun campo anterior.

Responde SIEMPRE con un unico objeto JSON, sin texto alrededor y sin markdown:
{{"requisitos_actualizados": {{...}}, "pregunta_al_usuario": "..."}}

Reglas, sin excepciones:
1. En "requisitos_actualizados" pon SOLO los campos que el usuario acaba de
   decir o confirmar. Si no lo ha dicho, NO lo pongas. No lo deduzcas, no lo
   supongas por lo que suele ser habitual, no lo rellenes "por ayudar".
2. Una sola pregunta por turno, la mas importante de las que falten. Si no falta
   nada, pon "pregunta_al_usuario" a null.
3. Si el usuario menciona algo que no encaja en ningun campo (otro lenguaje,
   una herramienta, un requisito raro), va a "notas". NUNCA lo fuerces dentro de
   "tecnologia" ni de ninguna otra lista cerrada.
4. No inventes claves que no esten en la lista de campos.
"""


def _estado_para_prompt(requisitos: Requisitos, faltan: list[str], recomendados: list[str]) -> str:
    return (
        f"Estado actual del formulario:\n{requisitos.model_dump_json(indent=2)}\n\n"
        f"Campos IMPRESCINDIBLES que siguen vacios: {faltan or 'ninguno'}\n"
        f"Campos utiles que siguen vacios (no bloquean): {recomendados or 'ninguno'}\n\n"
        + (
            "Pregunta por uno de los imprescindibles."
            if faltan
            else "Ya no falta nada imprescindible. Si quedan campos utiles vacios, "
            "pregunta por uno; si no queda ninguno, pon pregunta_al_usuario a null."
        )
    )


def _texto_a_json(crudo: str) -> dict:
    """Limpieza DETERMINISTA de la respuesta antes de parsearla.

    Dos artefactos de formato, no alucinaciones, que conviene tolerar porque no
    aportan nada al error y son muy frecuentes:
      - bloques <think>...</think> de los modelos con razonamiento (qwen3 los emite).
      - vallas de markdown ```json ... ```
    Lo que NO se tolera es un JSON mal formado o con claves de mas: eso si es un
    problema del modelo y tiene que provocar el reintento.
    """
    texto = re.sub(r"<think>.*?</think>", "", crudo, flags=re.DOTALL).strip()
    if texto.startswith("```"):
        texto = re.sub(r"^```[a-zA-Z]*\n?|\n?```$", "", texto).strip()
    return json.loads(texto)


def proponer(
    historial: list[dict],
    requisitos: Requisitos,
    faltan: list[str],
    recomendados: list[str],
    *,
    validar: Callable[[PropuestaTurno], None] | None = None,
) -> PropuestaTurno:
    """Una llamada al modelo, con validar-y-reintentar.

    `validar` la aporta quien llama (el recolector) y es donde se comprueba que
    la propuesta encaje de verdad en `Requisitos` -- es decir, intentar fundirla.
    Se pasa como funcion en vez de hacerlo aqui a proposito: este fichero sabe
    hablar con un modelo y reintentar, pero NO sabe que es un requisito valido.
    Esa decision vive en requisitos/esquema.py, que no depende de ningun LLM.
    """
    cliente, modelo = construir_cliente()

    # EL ESTADO VA EN EL SYSTEM, NO COMO ULTIMO MENSAJE.
    #
    # La primera version lo ponia detras del historial, como un mensaje de
    # usuario mas. Consecuencia: lo ultimo que veia el modelo era el bloque de
    # estado, asi que respondia AL BLOQUE y no a lo que acababa de decir la
    # persona, cuyas palabras quedaban enterradas en el historial. Medido con
    # los dos ordenes y el mismo turno ("el repo se llama demo-servicio-jaba"):
    #   estado al final -> {"tecnologia": "java"}                    (ignora al usuario)
    #   estado en system -> {"tecnologia": "java", "repo_codigo": ...} (correcto)
    # Un modelo de 9B lo toleraba y uno de 4B no, que es la peor clase de bug:
    # el que no se ve hasta que cambias de modelo.
    #
    # Contrapartida conocida: el system cambia en cada turno, asi que no se
    # cachea el prefijo. Con un modelo local da igual; si algun dia el backend
    # es de pago, conviene mover el estado a un mensaje intermedio y dejar el
    # system fijo.
    mensajes = [
        {"role": "system", "content": _instrucciones() + "\n\n"
         + _estado_para_prompt(requisitos, faltan, recomendados)},
        *historial,
    ]

    for intento in range(1, MAX_INTENTOS + 1):
        respuesta = cliente.chat.completions.create(
            model=modelo, messages=mensajes, max_tokens=MAX_TOKENS, **kwargs_json()
        )
        eleccion = respuesta.choices[0]
        crudo = eleccion.message.content or ""

        # Diagnostico especifico ANTES de intentar parsear. Un modelo de
        # razonamiento que agota el presupuesto pensando devuelve content=""
        # con finish_reason="length", y el razonamiento en un campo aparte. Sin
        # esta rama el sintoma era "JSON invalido: Expecting value line 1
        # column 1", que manda a mirar el prompt cuando el problema es el
        # presupuesto. Mismo patron que el 401 de rutas mal formadas en ADO: el
        # fallo no se parece a lo que es.
        #
        # SI se reintenta, al contrario de lo que decia la primera version de
        # este comentario. Se escribio "reintentar daria exactamente lo mismo",
        # y es falso: el modelo es estocastico. Medido con qwen3.5-9b y el mismo
        # prompt, el razonamiento oscilo entre 1518 tokens (respondio bien) y
        # mas de 8000 (se quedo sin presupuesto). Precisamente por ser aleatorio,
        # un segundo intento -- y mas con la instruccion de no extenderse -- suele
        # salir. Solo si el ultimo intento tambien se agota se da por perdido.
        if not crudo.strip() and eleccion.finish_reason == "length":
            razono = getattr(eleccion.message, "reasoning_content", None)
            motivo = (
                f"el modelo agoto los {MAX_TOKENS} tokens "
                f"{'razonando' if razono else 'generando'} y no llego a responder"
            )
            if intento == MAX_INTENTOS:
                raise RuntimeError(
                    f"{motivo} (tras {MAX_INTENTOS} intentos). Sube LLM_MAX_TOKENS en .env, "
                    "o usa un modelo sin razonamiento: este comportamiento es aleatorio y con "
                    "un modelo de razonamiento local puede repetirse."
                )
            mensajes.append({
                "role": "user",
                "content": "Te has quedado sin presupuesto de tokens antes de responder. "
                           "Responde AHORA directamente con el JSON, sin razonar en profundidad.",
            })
            continue

        try:
            propuesta = PropuestaTurno(**_texto_a_json(crudo))
            if validar:
                validar(propuesta)
            return propuesta
        except (json.JSONDecodeError, ValidationError, ValueError) as error:
            if intento == MAX_INTENTOS:
                raise RuntimeError(
                    f"el modelo no devolvio una propuesta valida tras {MAX_INTENTOS} intentos: {error}"
                ) from error
            # Reintento con el error EXACTO como mensaje nuevo: el modelo ve por
            # que fallo, no solo que fallo. Es el patron de T1.2 de poc-agentes.
            mensajes.append({"role": "assistant", "content": crudo})
            mensajes.append({
                "role": "user",
                "content": f"Tu respuesta no es valida: {error}\nCorrigela y responde SOLO con el JSON.",
            })

    raise AssertionError("inalcanzable")


if __name__ == "__main__":
    from dotenv import load_dotenv

    from requisitos.esquema import fusionar, slots_que_faltan, slots_recomendados_vacios

    load_dotenv()

    r = Requisitos()
    historial = [{"role": "user", "content": "necesito una pipeline para un servicio en Java"}]
    print("usuario: necesito una pipeline para un servicio en Java\n")

    propuesta = proponer(historial, r, slots_que_faltan(r), slots_recomendados_vacios(r))
    print(f"[modelo] actualiza : {propuesta.requisitos_actualizados}")
    print(f"[modelo] pregunta  : {propuesta.pregunta_al_usuario}")

    r = fusionar(r, propuesta.requisitos_actualizados)
    print(f"\n[estado]  {r.model_dump(exclude_none=True)}")
    print(f"[faltan]  {slots_que_faltan(r)}")
