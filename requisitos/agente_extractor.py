"""T2.2 (parte LLM) - traduce lo que dice un humano a campos de `Requisitos`.

ESTE ES EL UNICO FICHERO DEL BLOQUE 2 QUE HABLA CON UN MODELO. Todo lo demas
--llevar el historial, fundir el estado, decidir si falta algo, decidir cuando
parar-- es codigo normal y vive en requisitos/agente_recolector.py y
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

Ejecuta con: .venv/bin/python -m requisitos.agente_extractor
"""
from typing import Any, Callable

from pydantic import BaseModel, ConfigDict

from llm.estructurado import pedir_json
from requisitos.esquema import DESCRIPCIONES, Requisitos

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
    # EL ESTADO VA EN EL SYSTEM, NO COMO ULTIMO MENSAJE.
    #
    # La primera version lo ponia detras del historial, como un mensaje de
    # usuario mas. Consecuencia: lo ultimo que veia el modelo era el bloque de
    # estado, asi que respondia AL BLOQUE y no a lo que acababa de decir la
    # persona, cuyas palabras quedaban enterradas en el historial. Medido con
    # los dos ordenes y el mismo turno ("el repo se llama demo-servicio-jaba"):
    #   estado al final  -> {"tecnologia": "java"}                     (ignora al usuario)
    #   estado en system -> {"tecnologia": "java", "repo_codigo": ...} (correcto)
    # Un modelo de 9B lo toleraba y uno de 4B no, que es la peor clase de bug:
    # el que no se ve hasta que cambias de modelo.
    #
    # Contrapartida conocida: el system cambia en cada turno, asi que no se
    # cachea el prefijo. Con un modelo local da igual; si algun dia el backend
    # es de pago, conviene mover el estado a un mensaje intermedio.
    mensajes = [
        {"role": "system", "content": _instrucciones() + "\n\n"
         + _estado_para_prompt(requisitos, faltan, recomendados)},
        *historial,
    ]
    return pedir_json(mensajes, PropuestaTurno, validar=validar)

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
