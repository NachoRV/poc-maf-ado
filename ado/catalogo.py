"""T1.2 - descubrimiento del catalogo de plantillas en Azure DevOps.

El catalogo ya no es una carpeta en disco (como en poc-agentes) ni una tabla
en el codigo: es un repo de Git versionado. Anadir una plantilla es un Pull
Request a `plantillas-ci`, no un despliegue del sistema que la consume. Este
modulo es el unico que sabe como esta organizado ese repo.

Cuatro decisiones, y el porque de cada una:

1. TODO SE LEE ANCLADO AL TAG, nunca a una rama.
   Si se leyera de main, el catalogo podria cambiar entre el momento en que se
   elige la plantilla y el momento en que se renderiza el pipeline. Con el tag,
   una ejecucion ve un catalogo congelado -- y es el MISMO refs/tags/<tag> que
   acabara escrito en el `extends`, asi que lo decidido y lo generado coinciden
   por construccion, no por suerte.

2. EL MANIFEST ES ENTRADA NO CONFIABLE.
   Vive en otro repo, lo edita otra gente y llega como YAML suelto. Se valida en
   la frontera, al entrar: si a un manifest le falta `applies_to`, el fallo sale
   aqui con un mensaje claro, no tres estados mas adelante dentro de un prompt.

3. EL `id` DEL MANIFEST DEBE COINCIDIR CON EL NOMBRE DE SU CARPETA.
   Parece quisquilloso y no lo es: la seleccion de plantilla casa por `id`, pero
   la ruta del `extends` se construye con la CARPETA. Si divergen se genera un
   pipeline que apunta a una plantilla inexistente, y eso no se descubre hasta
   que Azure Pipelines intenta ejecutarlo, muy lejos de aqui.

4. CACHE EN MEMORIA, Y SCHEMAS EN DIFERIDO.
   Descubrir el catalogo cuesta N+2 llamadas HTTP: una para resolver el id del
   repo, una al arbol y una por manifest. El flujo lo necesita varias veces. Los parameters.schema.json NO se traen en
   el descubrimiento: solo hace falta el de la plantilla que se acabe eligiendo.

Ejecuta con (desde la raiz del repo): .venv/bin/python -m ado.catalogo
"""
import json
import os
from typing import Any

import yaml
from pydantic import BaseModel, Field, ValidationError

from ado.cliente import ClienteAdo, ErrorAdo

CARPETA_CATALOGO = "/catalog"
FICHERO_MANIFEST = "manifest.yaml"
FICHERO_SCHEMA = "parameters.schema.json"
FICHERO_TEMPLATE = "template.yaml"


class ErrorCatalogo(RuntimeError):
    """El catalogo existe pero no cumple su propio contrato.

    Distinto de ErrorAdo (no se pudo hablar con Azure DevOps): aqui la llamada
    fue bien y lo que vino de vuelta esta mal.
    """


class PlantillaDisponible(BaseModel):
    """Un manifest del catalogo, ya validado, mas de donde salio.

    Los campos con default son opcionales EN EL MANIFEST; `id`, `version`,
    `applies_to` y `parameters` no lo son, y su ausencia es un error.
    """

    id: str
    version: str
    applies_to: list[str] = Field(min_length=1)
    parameters: list[dict[str, Any]] = Field(min_length=1)
    kind: str | None = None
    controls: list[str] = []
    pool: dict[str, Any] = {}
    selection_rationale: str = ""

    # De donde salio: no esta en el manifest, lo pone el descubrimiento.
    carpeta: str
    tag: str

    @property
    def ruta_template(self) -> str:
        """Ruta DENTRO del repo de plantillas, la que va en el `extends`."""
        return f"catalog/{self.carpeta}/{FICHERO_TEMPLATE}"

    @property
    def ruta_schema(self) -> str:
        return f"{CARPETA_CATALOGO}/{self.carpeta}/{FICHERO_SCHEMA}"

    def resumen_para_llm(self) -> dict:
        """Version reducida para meter en un prompt.

        Minimizacion de contexto: al modelo le llega lo que necesita para elegir
        (que es, a que aplica y por que), no el manifest entero con su pool, sus
        controles y sus descripciones largas de parametros.
        """
        return {
            "id": self.id,
            "applies_to": self.applies_to,
            "selection_rationale": self.selection_rationale.strip(),
        }


# Caches de sesion, explicitas (y no lru_cache) para poder inspeccionarlas y
# vaciarlas desde fuera. El id del repo se cachea aparte porque resolverlo cuesta
# un listado completo de repos y hace falta en cada lectura de fichero: sin esto,
# leer un schema en diferido pagaba ese listado otra vez.
_CACHE: dict[tuple[str, str], list[PlantillaDisponible]] = {}
_CACHE_ID_REPO: dict[str, str] = {}


def _params_tag(tag: str) -> dict:
    """Los parametros que anclan CUALQUIER lectura a un tag concreto."""
    return {"versionDescriptor.version": tag, "versionDescriptor.versionType": "tag"}


def id_repo(cliente: ClienteAdo, nombre: str) -> str:
    if nombre in _CACHE_ID_REPO:
        return _CACHE_ID_REPO[nombre]

    repos = cliente.get("/_apis/git/repositories")["value"]
    for repo in repos:
        if repo["name"] == nombre:
            _CACHE_ID_REPO[nombre] = repo["id"]
            return repo["id"]
    disponibles = ", ".join(sorted(r["name"] for r in repos))
    raise ErrorCatalogo(
        f"no existe el repo '{nombre}' en {cliente.org}/{cliente.proyecto}. "
        f"Hay: {disponibles}. Ejecuta '.venv/bin/python -m ado.sembrar' si falta el escenario."
    )


def _leer_fichero(cliente: ClienteAdo, repo_id: str, ruta: str, tag: str) -> str:
    """Contenido de un fichero del repo, en la version del tag.

    `$format=json` + `includeContent=true` devuelve el texto dentro de un JSON,
    que es lo que queremos: sin esto ADO responde el fichero en crudo y el
    diagnostico del cliente (que exige Content-Type JSON) lo rechazaria.
    """
    respuesta = cliente.get(
        f"/_apis/git/repositories/{repo_id}/items",
        params={"path": ruta, "includeContent": "true", "$format": "json", **_params_tag(tag)},
    )
    return respuesta["content"]


def _carpetas_de_plantilla(cliente: ClienteAdo, repo_id: str, tag: str) -> list[str]:
    """Nombres de carpeta bajo /catalog que contienen un manifest.yaml.

    Se filtra por la presencia del manifest, no por convencion de nombre: una
    carpeta suelta en /catalog (documentacion, un .gitkeep) no debe romper el
    descubrimiento ni colarse como plantilla.
    """
    arbol = cliente.get(
        f"/_apis/git/repositories/{repo_id}/items",
        params={"scopePath": CARPETA_CATALOGO, "recursionLevel": "full", **_params_tag(tag)},
    )
    rutas = {item["path"] for item in arbol["value"]}
    return sorted(
        ruta.removeprefix(f"{CARPETA_CATALOGO}/")
        for ruta in rutas
        if ruta.count("/") == 2 and f"{ruta}/{FICHERO_MANIFEST}" in rutas
    )


def _construir_plantilla(crudo: str, carpeta: str, tag: str) -> PlantillaDisponible:
    """YAML -> objeto validado. Aqui se rechaza un manifest malformado."""
    try:
        datos = yaml.safe_load(crudo)
    except yaml.YAMLError as error:
        raise ErrorCatalogo(f"catalog/{carpeta}/{FICHERO_MANIFEST} no es YAML valido: {error}") from error

    if not isinstance(datos, dict):
        raise ErrorCatalogo(f"catalog/{carpeta}/{FICHERO_MANIFEST} no contiene un mapa YAML")

    try:
        plantilla = PlantillaDisponible(**datos, carpeta=carpeta, tag=tag)
    except ValidationError as error:
        raise ErrorCatalogo(f"catalog/{carpeta}/{FICHERO_MANIFEST} incompleto: {error}") from error

    # Decision 3 del docstring: si el id y la carpeta divergen, la seleccion
    # (que casa por id) y el `extends` (que usa la carpeta) apuntarian a sitios
    # distintos. Se falla aqui, ruidosamente, y no en Azure Pipelines.
    if plantilla.id != carpeta:
        raise ErrorCatalogo(
            f"el manifest de catalog/{carpeta}/ declara id='{plantilla.id}', que no coincide "
            f"con el nombre de la carpeta. La seleccion casa por id y el `extends` usa la "
            f"carpeta: si divergen se genera un pipeline que apunta a una plantilla inexistente."
        )

    return plantilla


def descubrir_plantillas(cliente: ClienteAdo, *, repo: str | None = None,
                         tag: str | None = None, refrescar: bool = False) -> list[PlantillaDisponible]:
    """Todas las plantillas del catalogo, leidas del tag. Cacheado por sesion."""
    repo = repo or os.environ.get("AZDO_REPO_PLANTILLAS", "plantillas-ci")
    tag = tag or os.environ.get("AZDO_TAG_PLANTILLAS", "v1.0.0")

    clave = (repo, tag)
    if not refrescar and clave in _CACHE:
        return _CACHE[clave]

    repo_id = id_repo(cliente, repo)
    carpetas = _carpetas_de_plantilla(cliente, repo_id, tag)
    if not carpetas:
        raise ErrorCatalogo(f"el repo '{repo}' en {tag} no tiene ninguna plantilla bajo {CARPETA_CATALOGO}/")

    plantillas = [
        _construir_plantilla(
            _leer_fichero(cliente, repo_id, f"{CARPETA_CATALOGO}/{c}/{FICHERO_MANIFEST}", tag), c, tag
        )
        for c in carpetas
    ]

    _CACHE[clave] = plantillas
    return plantillas


def leer_schema(cliente: ClienteAdo, plantilla: PlantillaDisponible,
                *, repo: str | None = None) -> dict:
    """parameters.schema.json de una plantilla, en diferido.

    No se trae en el descubrimiento a proposito: de N plantillas solo hace falta
    el schema de la que se acabe eligiendo (T3.3).
    """
    repo = repo or os.environ.get("AZDO_REPO_PLANTILLAS", "plantillas-ci")
    crudo = _leer_fichero(cliente, id_repo(cliente, repo), plantilla.ruta_schema, plantilla.tag)
    try:
        return json.loads(crudo)
    except json.JSONDecodeError as error:
        raise ErrorCatalogo(f"{plantilla.ruta_schema} no es JSON valido: {error}") from error


def buscar_por_arquetipo(plantillas: list[PlantillaDisponible], arquetipo: str) -> list[PlantillaDisponible]:
    """Las plantillas cuyo `applies_to` incluye ese arquetipo. Devuelve lista, no
    una sola: que haya dos candidatas es informacion (es la ambiguedad que
    justifica llamar al LLM en T3.2), no un error que haya que esconder."""
    return [p for p in plantillas if arquetipo in p.applies_to]


if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv()
    with ClienteAdo.desde_entorno() as cliente:
        try:
            plantillas = descubrir_plantillas(cliente)
        except (ErrorAdo, ErrorCatalogo) as error:
            print(f"[FALLO] {error}")
            raise SystemExit(1)

        tag = plantillas[0].tag
        print(f"[catalogo] {len(plantillas)} plantilla(s) leidas de "
              f"{os.environ.get('AZDO_REPO_PLANTILLAS', 'plantillas-ci')} @ {tag}\n")
        for p in plantillas:
            print(f"  {p.id}  v{p.version}")
            print(f"    aplica a  : {', '.join(p.applies_to)}")
            print(f"    parametros: {', '.join(par['name'] for par in p.parameters)}")
            print(f"    extends   : {p.ruta_template}")

        print("\n[cache] segunda llamada, sin trafico HTTP:")
        print(f"    misma lista en memoria: {descubrir_plantillas(cliente) is plantillas}")

        print("\n[resumen_para_llm] lo unico que veria el modelo al elegir:")
        print(json.dumps([p.resumen_para_llm() for p in plantillas], indent=2, ensure_ascii=False))

        print("\n[busqueda] buscar_por_arquetipo('java-container'):")
        print(f"    {[p.id for p in buscar_por_arquetipo(plantillas, 'java-container')]}")

        print("\n[schema en diferido] de la primera plantilla:")
        schema = leer_schema(cliente, plantillas[0])
        print(f"    propiedades: {list(schema.get('properties', {}))}  required: {schema.get('required')}")
