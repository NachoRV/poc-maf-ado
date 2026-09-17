"""T3.2 (parte LLM) - el desempate, y SOLO el desempate.

El modelo es el ultimo recurso, no el primero:

    requisitos + catalogo -> seleccionar_por_reglas()   (seleccion/reglas.py)
         DECIDIDO              -> se devuelve tal cual. CERO llamadas al modelo.
         AMBIGUO / DESCONOCIDO -> desempata el LLM, con tres barandillas.

Las tres barandillas, que son las que impiden que "dejar decidir al modelo" sea
una mala idea:

  1. LISTA CERRADA. Solo puede elegir entre las candidatas que le pasamos, y esa
     lista sale del catalogo DESCUBIERTO en Azure DevOps -- no de una constante.
     Un id fuera de la lista cuenta como respuesta invalida.
  2. SALIDA ESTRUCTURADA + REINTENTO. Se pide un JSON con forma fija; si no
     valida, se reintenta una vez con el error como feedback (llm/estructurado.py).
  3. UMBRAL DE CONFIANZA (0.7). Si la propia confianza del modelo no lo supera,
     el caso se marca REQUIERE_REVISION_HUMANA y NO se continua, aunque la
     respuesta fuera formalmente valida.

Y una cuarta que no es una barandilla sino minimizacion de contexto: al modelo
le llega `resumen_para_llm()` de cada candidata (id, applies_to y el porque de
la plantilla), no el manifest entero con su pool y sus parametros.

Ejecuta con (desde la raiz del repo): .venv/bin/python -m seleccion.agente_hibrido
"""
import json

from pydantic import BaseModel, Field

from ado.catalogo import PlantillaDisponible
from llm.estructurado import pedir_json
from requisitos.esquema import Requisitos
from seleccion.reglas import EstadoSeleccion, Seleccion, seleccionar_por_reglas

UMBRAL_CONFIANZA = 0.7


class EleccionDelModelo(BaseModel):
    plantilla_id: str
    confianza: float = Field(ge=0.0, le=1.0)
    razonamiento: str


def _mensajes(requisitos: Requisitos, candidatas: list[PlantillaDisponible]) -> list[dict]:
    ids = [p.id for p in candidatas]
    catalogo = json.dumps([p.resumen_para_llm() for p in candidatas], ensure_ascii=False, indent=2)
    return [
        {
            "role": "system",
            "content": (
                "Eres un desempatador de plantillas de pipeline CI/CD. Las reglas "
                "deterministas no han podido decidir y te toca a ti.\n\n"
                f"Debes elegir EXACTAMENTE uno de estos identificadores: {ids}\n"
                "Nunca inventes un identificador fuera de esa lista.\n\n"
                f"Plantillas candidatas:\n{catalogo}\n\n"
                "Responde SOLO con un objeto JSON, sin markdown:\n"
                '{"plantilla_id": "<uno de la lista>", '
                '"confianza": <numero entre 0 y 1>, '
                '"razonamiento": "<una frase>"}\n\n'
                "Se honesto con la confianza: si los requisitos no bastan para "
                "distinguir entre las candidatas, ponla baja. Una confianza baja "
                "manda el caso a revision humana, que es el resultado correcto "
                "cuando de verdad no se puede saber."
            ),
        },
        {
            "role": "user",
            "content": f"Requisitos del usuario:\n"
                       f"{requisitos.model_dump_json(indent=2, exclude_none=True)}",
        },
    ]


def seleccionar(requisitos: Requisitos, plantillas: list[PlantillaDisponible],
                *, umbral: float = UMBRAL_CONFIANZA) -> Seleccion:
    """Reglas primero; el modelo solo si hace falta."""
    por_reglas = seleccionar_por_reglas(requisitos, plantillas)
    if por_reglas.estado is EstadoSeleccion.DECIDIDO:
        return por_reglas

    candidatas = por_reglas.candidatas
    if not candidatas:
        # Ni siquiera hay entre que elegir: no hay nada que preguntarle al modelo.
        return Seleccion(EstadoSeleccion.REQUIERE_REVISION_HUMANA, arquetipo=por_reglas.arquetipo,
                         origen="reglas", confianza=0.0,
                         razonamiento="El catalogo no tiene ninguna plantilla para este caso.")

    ids_validos = {p.id for p in candidatas}

    def validar(eleccion: EleccionDelModelo) -> None:
        # Barandilla 1: fuera de la lista cerrada = respuesta invalida, y el
        # reintento de llm/estructurado.py se lo dice al modelo con ese texto.
        if eleccion.plantilla_id not in ids_validos:
            raise ValueError(
                f"'{eleccion.plantilla_id}' no esta en la lista cerrada {sorted(ids_validos)}"
            )

    eleccion = pedir_json(_mensajes(requisitos, candidatas), EleccionDelModelo, validar=validar)
    elegida = next(p for p in candidatas if p.id == eleccion.plantilla_id)

    # Barandilla 3: el umbral se aplica DESPUES de tener una respuesta valida.
    # Una respuesta bien formada en la que el modelo no confia sigue sin servir.
    if eleccion.confianza < umbral:
        return Seleccion(EstadoSeleccion.REQUIERE_REVISION_HUMANA, plantilla=elegida,
                         candidatas=candidatas, arquetipo=por_reglas.arquetipo,
                         origen="llm", confianza=eleccion.confianza,
                         razonamiento=eleccion.razonamiento)

    return Seleccion(EstadoSeleccion.DECIDIDO, plantilla=elegida, candidatas=candidatas,
                     arquetipo=por_reglas.arquetipo, origen="llm",
                     confianza=eleccion.confianza, razonamiento=eleccion.razonamiento)


if __name__ == "__main__":
    from dotenv import load_dotenv

    from ado.catalogo import descubrir_plantillas
    from ado.cliente import ClienteAdo
    from llm.cliente import llamadas_al_modelo, reiniciar_contador

    load_dotenv()
    with ClienteAdo.desde_entorno() as cliente:
        plantillas = descubrir_plantillas(cliente)
        req = Requisitos(tecnologia="java", repo_codigo="demo-servicio-java",
                         version_lenguaje="17", contenedor=True)

        print("=== caso real: las reglas deciden, el modelo NO se llama ===")
        reiniciar_contador()
        print(f"  {seleccionar(req, plantillas).resumen()}")
        print(f"  llamadas al modelo: {llamadas_al_modelo()}   <- debe ser 0\n")

        # Catalogo fabricado: dos plantillas para el mismo arquetipo. Es el unico
        # modo de disparar el camino del LLM hoy -- con el catalogo real las
        # reglas deciden siempre, y decirlo es mas util que fingir ambiguedad.
        java = next(p for p in plantillas if "java-container" in p.applies_to)
        gemela = java.model_copy(update={
            "id": "ci-java-minimo", "carpeta": "ci-java-minimo",
            "selection_rationale": "Build de Java sin publicar imagen de contenedor. "
                                   "Para repos que no despliegan en contenedor.",
        })

        print("=== catalogo fabricado con empate: entra el LLM, umbral 0.7 ===")
        reiniciar_contador()
        resultado = seleccionar(req, [java, gemela])
        print(f"  {resultado.resumen()}")
        print(f"  razonamiento: {resultado.razonamiento}")
        print(f"  llamadas al modelo: {llamadas_al_modelo()}")

        print("\n=== el MISMO caso con umbral 0.99 -> debe escalar a revision humana ===")
        reiniciar_contador()
        resultado = seleccionar(req, [java, gemela], umbral=0.99)
        print(f"  {resultado.resumen()}")
        print(f"  llamadas al modelo: {llamadas_al_modelo()}")
