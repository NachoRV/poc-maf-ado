"""T3.1 - los estados del flujo y su naturaleza, DECLARADOS EN CODIGO.

Parece burocracia y es la mitad del valor del ejercicio. La afirmacion "la mayor
parte de este sistema es determinista" no vale nada dicha en una reunion; vale
mucho si se puede imprimir desde el propio codigo y contrastar con el numero de
llamadas que hizo una ejecucion concreta.

Este modulo es PURO: no importa MAF, ni el LLM, ni Azure DevOps. Solo declara.
`orquestacion/agente_flujo.py` es quien lo convierte en un workflow ejecutable. Esa
separacion importa -- la tabla tiene que poder leerse y enseñarse sin arrancar
nada, y tiene que seguir siendo cierta aunque mañana se cambie de framework.

Cuatro naturalezas, no dos. "Determinista vs LLM" se queda corto para describir
lo que de verdad pasa:
  - DETERMINISTA: codigo normal. Auditable linea a linea.
  - HIBRIDO:      reglas primero; el modelo SOLO si las reglas no deciden. En un
                  parque real la mayoria de casos no llegan al modelo, y esa
                  fraccion es un argumento de oferta.
  - LLM:          el modelo es imprescindible y su salida alimenta al resto.
  - HUMANO:       el flujo se SUSPENDE (ctx.request_info) y no sigue sin una
                  respuesta. No es un paso "lento": es una puerta.

Ejecuta con (desde la raiz del repo): .venv/bin/python -m orquestacion.estados
"""
from dataclasses import dataclass
from enum import StrEnum


class Naturaleza(StrEnum):
    DETERMINISTA = "determinista"
    HIBRIDO = "hibrido"
    LLM = "llm"
    HUMANO = "humano"


class Estado(StrEnum):
    RECOGIENDO_REQUISITOS = "recogiendo_requisitos"
    CONFIRMANDO_REQUISITOS = "confirmando_requisitos"
    DESCUBRIENDO_CATALOGO = "descubriendo_catalogo"
    SELECCIONANDO_PLANTILLA = "seleccionando_plantilla"
    GENERANDO_PARAMETROS = "generando_parametros"
    RESOLVIENDO_VARIABLES = "resolviendo_variables"
    RENDERIZANDO = "renderizando"
    PLANIFICANDO_CAMBIOS = "planificando_cambios"
    CONFIRMANDO_PUSH = "confirmando_push"
    ESCRIBIENDO_EN_ADO = "escribiendo_en_ado"
    REDACTANDO_PRS = "redactando_prs"
    CREANDO_PRS = "creando_prs"
    REGISTRANDO_RUN = "registrando_run"
    # Terminales
    COMPLETADO = "completado"
    REQUIERE_REVISION_HUMANA = "requiere_revision_humana"
    CANCELADO = "cancelado"


@dataclass(frozen=True)
class Paso:
    naturaleza: Naturaleza
    implementado_en: str
    por_que: str


# La tabla. Si un estado cambia de naturaleza, se cambia AQUI y el informe de
# cualquier ejecucion lo refleja solo -- no hay un documento aparte que se quede
# desactualizado.
PASOS: dict[Estado, Paso] = {
    Estado.RECOGIENDO_REQUISITOS: Paso(
        Naturaleza.LLM, "orquestacion/agente_flujo.py + requisitos/agente_extractor.py",
        "El input es prosa abierta. Unico punto del sistema con entrada no estructurada.",
    ),
    Estado.CONFIRMANDO_REQUISITOS: Paso(
        Naturaleza.HUMANO, "requisitos/confirmacion.py (clasificador determinista)",
        "Caza lo que el modelo dedujo de mas o se dejo. Clasificar el si/no no cuesta una llamada.",
    ),
    Estado.DESCUBRIENDO_CATALOGO: Paso(
        Naturaleza.DETERMINISTA, "ado/catalogo.py",
        "REST + YAML, anclado por tag. No hay nada que interpretar.",
    ),
    Estado.SELECCIONANDO_PLANTILLA: Paso(
        Naturaleza.HIBRIDO, "seleccion/reglas.py + seleccion/agente_hibrido.py",
        "Reglas sobre los requisitos primero; el modelo solo si 0 o >1 candidatos.",
    ),
    Estado.GENERANDO_PARAMETROS: Paso(
        # Era HIBRIDO en el plan original. Dejo de serlo el 2026-09-18, cuando el
        # usuario eligio que NADA se infiera: lo que no se deriva se pregunta. Y
        # aqui un hueco no vale, porque el schema los declara required.
        Naturaleza.DETERMINISTA, "parametros/generador.py",
        "Se deriva de los requisitos; lo que falte se PREGUNTA, no se inventa. "
        "Puede suspenderse, pero con requisitos completos no pregunta nada.",
    ),
    Estado.RESOLVIENDO_VARIABLES: Paso(
        Naturaleza.DETERMINISTA, "parametros/variables.py",
        "Los cuatro ficheros siempre. Lo que no se sepa queda como HUECO, nunca "
        "inventado, y regenerar funde sin pisar lo que un humano anadio.",
    ),
    Estado.RENDERIZANDO: Paso(
        Naturaleza.DETERMINISTA, "render/renderizador.py",
        "yaml.dump. El modelo NUNCA escribe el artefacto que se despliega.",
    ),
    Estado.PLANIFICANDO_CAMBIOS: Paso(
        Naturaleza.DETERMINISTA, "ado/destinos.py",
        "Existe o no existe: add vs edit. Equivocarse es un 400 de ADO.",
    ),
    Estado.CONFIRMANDO_PUSH: Paso(
        Naturaleza.HUMANO, "orquestacion/agente_flujo.py",
        "Ultima puerta antes de escribir en repos reales. Nada se escribe sin un si.",
    ),
    Estado.ESCRIBIENDO_EN_ADO: Paso(
        Naturaleza.DETERMINISTA, "ado/cambios.py",
        "Pushes API. Rama y commit en una llamada, en los dos repos.",
    ),
    Estado.REDACTANDO_PRS: Paso(
        Naturaleza.LLM, "redaccion/agente_pr.py + redaccion/texto.py",
        "Prosa para un revisor humano. Si sale mal, alguien la lee y la corrige.",
    ),
    Estado.CREANDO_PRS: Paso(
        Naturaleza.DETERMINISTA, "ado/cambios.py",
        "Pull Requests API. Los dos, o ninguno.",
    ),
    Estado.REGISTRANDO_RUN: Paso(
        Naturaleza.DETERMINISTA, "traza/registro.py",
        "Ficheros en runs/, con el origen de cada valor.",
    ),
    Estado.COMPLETADO: Paso(Naturaleza.DETERMINISTA, "-", "Terminal."),
    Estado.REQUIERE_REVISION_HUMANA: Paso(Naturaleza.DETERMINISTA, "-", "Terminal: no se pudo decidir."),
    Estado.CANCELADO: Paso(Naturaleza.DETERMINISTA, "-", "Terminal: el humano dijo que no."),
}


def reparto() -> dict[Naturaleza, int]:
    """Cuantos estados hay de cada naturaleza. El titular, calculado."""
    conteo = dict.fromkeys(Naturaleza, 0)
    for paso in PASOS.values():
        conteo[paso.naturaleza] += 1
    return conteo


def tabla() -> str:
    """La tabla en markdown, generada desde PASOS -- nunca escrita a mano."""
    filas = ["| Estado | Naturaleza | Implementado en |", "|---|---|---|"]
    filas += [
        f"| `{estado}` | **{paso.naturaleza}** | {paso.implementado_en} |"
        for estado, paso in PASOS.items()
    ]
    return "\n".join(filas)


if __name__ == "__main__":
    print(tabla())

    conteo = reparto()
    total = sum(conteo.values())
    print(f"\n{total} estados declarados:")
    for naturaleza, n in conteo.items():
        print(f"  {naturaleza:<14} {n:>2}   {'#' * n}")

    tocan_modelo = conteo[Naturaleza.LLM] + conteo[Naturaleza.HIBRIDO]
    print(f"\nEstados en los que el modelo puede intervenir: {tocan_modelo} de {total}.")
    print(f"De esos, solo {conteo[Naturaleza.LLM]} lo necesitan siempre; "
          f"{conteo[Naturaleza.HIBRIDO]} lo usan solo si las reglas no deciden.")
