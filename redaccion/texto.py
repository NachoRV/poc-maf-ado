"""Monta la descripcion de un pull request. DETERMINISTA.

La division con redaccion/agente_pr.py es la decision que importa de esta tarea:

    el modelo escribe SOLO el parrafo de explicacion.
    todo lo que un revisor necesita para no romper nada lo escribe este fichero.

El orden de merge, la lista de ficheros, los huecos por rellenar y el enlace al
PR hermano son texto GENERADO POR CODIGO. Si el modelo se inventara el orden de
merge, alguien mergearia el pipeline antes que sus variables y lo dejaria roto.
Esa frase no puede depender de que un LLM tenga un buen dia.

Y si el modelo falla, la descripcion se monta igual sin el parrafo: el pull
request se abre y es util. La explicacion es un anadido, no un requisito.
"""
from requisitos.esquema import Requisitos

AVISO_ORDEN = (
    "> **Orden de merge — importante.** El alta de un pipeline son DOS pull requests.\n"
    "> 1. Primero el de **variables** (repo de codigo).\n"
    "> 2. Despues el del **pipeline** (repo `pipelines`).\n"
    ">\n"
    "> Al reves el pipeline queda roto: referencia `vars/*.yml@codigo`, que todavia no existiria."
)


def _tabla_parametros(parametros: dict, origen: dict[str, str]) -> str:
    if not parametros:
        return "_(sin parametros)_"
    filas = ["| Parametro | Valor | De donde sale |", "|---|---|---|"]
    filas += [f"| `{n}` | `{v}` | {origen.get(n, '?')} |" for n, v in parametros.items()]
    return "\n".join(filas)


def _lista_ficheros(rutas: list[str]) -> str:
    return "\n".join(f"- `{r}`" for r in rutas) or "_(ninguno)_"


def descripcion_variables(requisitos: Requisitos, rutas: list[str],
                          huecos_por_fichero: dict[str, list[str]], explicacion: str) -> str:
    bloque_huecos = ""
    if huecos_por_fichero:
        detalle = "\n".join(
            f"- `{f}`: {', '.join(f'`{v}`' for v in variables)}"
            for f, variables in huecos_por_fichero.items()
        )
        bloque_huecos = (
            "\n## Variables sin valor\n\n"
            "Quedan como hueco **a proposito**: nadie las inventa. "
            "Hay que rellenarlas antes de mergear.\n\n" + detalle + "\n"
        )

    return f"""\
Variables de pipeline por entorno para **{requisitos.repo_codigo}**.

{AVISO_ORDEN}

## Ficheros

{_lista_ficheros(rutas)}

Se crean los cuatro siempre (el comun y uno por entorno), porque todos los
proyectos los necesitan. Una regeneracion futura **respeta** lo que haya aqui,
incluidas las variables que anadas tu.
{bloque_huecos}
## Por que

{explicacion}
"""


def descripcion_pipeline(requisitos: Requisitos, plantilla_id: str, tag: str,
                         rutas: list[str], parametros: dict, origen: dict[str, str],
                         explicacion: str) -> str:
    return f"""\
Pipeline de CI para **{requisitos.repo_codigo}**, extendiendo `{plantilla_id}` (`{tag}`).

{AVISO_ORDEN}

## Ficheros

{_lista_ficheros(rutas)}

## Parametros aplicados

{_tabla_parametros(parametros, origen)}

Ninguno se ha inferido: o sale de lo que dijo la persona, o se le pregunto.

## Por que

{explicacion}
"""


def enlace_hermano(url: str, que_es: str) -> str:
    return f"\n\n---\n\n**PR hermano** ({que_es}): {url}"
