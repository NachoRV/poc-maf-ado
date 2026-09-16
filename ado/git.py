"""Primitivas de Git sobre Azure DevOps: resolver repos, leer ficheros, leer refs.

Existe porque estas tres operaciones las necesitan ya DOS modulos de dominio
(ado/catalogo.py y ado/destinos.py) y pronto un tercero (ado/cambios.py). Que
`destinos` importara de `catalogo` acoplaria cosas que no tienen nada que ver;
duplicarlas seria peor. Se extraen cuando aparece el segundo consumidor, no antes.

La division con ado/cliente.py: alli vive el TRANSPORTE (auth, ambitos de URL,
diagnostico de respuestas); aqui, las operaciones de Git expresadas sobre el.

El detalle que justifica la mitad de este fichero: un 404 de ADO puede significar
dos cosas muy distintas y hay que distinguirlas. `GitItemNotFoundException` es
"ese fichero no esta", que en este flujo es un caso NORMAL (decide add vs edit).
`GitRepositoryNotFoundException` es "ese repo no existe", que es un error de
verdad. Mirando solo el codigo de estado, lo segundo se tragaria como lo primero
y el flujo seguiria tan feliz preparando un `add` contra un repo fantasma.
"""
from ado.cliente import ClienteAdo, ErrorAdo

ITEM_NO_ENCONTRADO = "GitItemNotFoundException"

# Cache de ids de repo por (org, proyecto, nombre). Resolver un id cuesta un
# listado completo de repos y hace falta en casi cada operacion. Se clava la
# org y el proyecto en la clave -- una cache por nombre a secas devolveria el
# repo equivocado en cuanto hubiera dos clientes apuntando a proyectos distintos.
_CACHE_ID_REPO: dict[tuple[str, str, str], str] = {}


def id_repo(cliente: ClienteAdo, nombre: str) -> str:
    clave = (cliente.org, cliente.proyecto, nombre)
    if clave in _CACHE_ID_REPO:
        return _CACHE_ID_REPO[clave]

    repos = cliente.get("/_apis/git/repositories")["value"]
    for repo in repos:
        if repo["name"] == nombre:
            _CACHE_ID_REPO[clave] = repo["id"]
            return repo["id"]

    disponibles = ", ".join(sorted(r["name"] for r in repos))
    raise ErrorAdo(
        f"no existe el repo '{nombre}' en {cliente.org}/{cliente.proyecto}. Hay: {disponibles}. "
        "Ejecuta '.venv/bin/python -m ado.sembrar' si falta el escenario."
    )


def _version(tag: str | None, rama: str | None) -> dict:
    """versionDescriptor: por tag (catalogo, congelado) o por rama (repos de
    trabajo, donde se quiere el estado actual). Sin ninguno, la rama por defecto."""
    if tag:
        return {"versionDescriptor.version": tag, "versionDescriptor.versionType": "tag"}
    if rama:
        return {"versionDescriptor.version": rama, "versionDescriptor.versionType": "branch"}
    return {}


def leer_fichero(cliente: ClienteAdo, repo_id: str, ruta: str,
                 *, tag: str | None = None, rama: str | None = None) -> str:
    """Contenido de un fichero. Lanza ErrorAdo si no existe.

    `$format=json` + `includeContent=true` devuelve el texto DENTRO de un JSON,
    que es lo que queremos: sin esto ADO responde el fichero en crudo y el
    diagnostico del cliente (que exige Content-Type JSON) lo rechazaria.
    """
    respuesta = cliente.get(
        f"/_apis/git/repositories/{repo_id}/items",
        params={"path": ruta, "includeContent": "true", "$format": "json", **_version(tag, rama)},
    )
    return respuesta["content"]


def leer_fichero_si_existe(cliente: ClienteAdo, repo_id: str, ruta: str,
                           *, tag: str | None = None, rama: str | None = None) -> str | None:
    """Igual, pero "no existe" es None y no una excepcion.

    Solo se traga el 404 cuyo typeKey es GitItemNotFoundException. Cualquier otro
    404 (repo inexistente, rama inexistente) se propaga: son errores de verdad y
    esconderlos aqui haria que el flujo preparase un `add` contra algo que no esta.
    """
    try:
        return leer_fichero(cliente, repo_id, ruta, tag=tag, rama=rama)
    except ErrorAdo as error:
        if error.status == 404 and error.type_key == ITEM_NO_ENCONTRADO:
            return None
        raise


def listar_arbol(cliente: ClienteAdo, repo_id: str, scope: str,
                 *, tag: str | None = None, rama: str | None = None) -> list[dict]:
    """Items bajo `scope`, recursivo."""
    return cliente.get(
        f"/_apis/git/repositories/{repo_id}/items",
        params={"scopePath": scope, "recursionLevel": "full", **_version(tag, rama)},
    )["value"]


def sha_rama(cliente: ClienteAdo, repo_id: str, rama: str = "main") -> str | None:
    """Ultimo commit de una rama, o None si la rama no existe.

    Es el `oldObjectId` que necesita la Pushes API para partir de algo. None
    significa "no hay rama": el llamante debe usar 40 ceros para crearla.
    """
    refs = cliente.get(f"/_apis/git/repositories/{repo_id}/refs", params={"filter": f"heads/{rama}"})
    for ref in refs["value"]:
        if ref["name"] == f"refs/heads/{rama}":
            return ref["objectId"]
    return None
