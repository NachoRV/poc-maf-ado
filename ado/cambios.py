"""Bloque 4 - escribir en Azure DevOps: ramas, commits y pull requests.

Cero IA. Es fontaneria, y es la que convierte la PoC en demo.

La operacion base ya se probo en T0.3 al sembrar el escenario: la PUSHES API
crea rama y commit en UNA llamada, sin clonar nada en local, y admite varios
ficheros en el mismo commit (alli se subieron 10).

LO ESPECIFICO DE ESTE DISENO, y lo que obliga a casi todo lo de abajo: el
artefacto de un alta cae en DOS repositorios distintos.

    <repo-de-codigo>/vars/{common,dev,acc,pro}.yml   -> PR #1
    pipelines/<repo-de-codigo>/azure-pipelines.yml   -> PR #2

Luego son DOS pull requests y no hay transaccion posible. Tres reglas:

  1. ORDEN. Primero las variables, despues el pipeline. Un pipeline mergeado sin
     sus variables esta roto: referencia vars/*.yml@codigo, que no existiria.
     El orden va escrito en la descripcion de los dos.
  2. LOS DOS O NINGUNO. Si el segundo falla, se revierte el primero (se abandona
     el PR y se borra la rama). Medio alta abierta es peor que nada, porque el
     siguiente intento no sabe en que estado esta.
  3. IDEMPOTENCIA. Si la rama ya existe se reutiliza; si ya hay un PR abierto
     desde esa rama, se devuelve en vez de crear otro.

Ejecuta un simulacro (no escribe nada):  .venv/bin/python -m ado.cambios
Para escribir de verdad en ADO:          .venv/bin/python -m ado.cambios --escribir
"""
from dataclasses import dataclass, field
from datetime import datetime

from ado.cliente import ClienteAdo, ErrorAdo
from ado.git import ITEM_NO_ENCONTRADO, id_repo, listar_arbol, sha_rama

SHA_VACIO = "0" * 40
RAMA_BASE = "main"


@dataclass(frozen=True)
class Cambio:
    """Un fichero a escribir. `change_type` sale del inventario de T1.3."""

    ruta: str
    contenido: str
    change_type: str  # "add" o "edit"


@dataclass(frozen=True)
class Propuesta:
    """Todo lo que va a un repo: sus cambios, su rama y el texto de su PR."""

    repo: str
    rama: str
    cambios: list[Cambio]
    titulo: str
    descripcion: str
    mensaje_commit: str


@dataclass
class PullRequestAbierto:
    repo: str
    rama: str
    id: int
    url: str
    reutilizado: bool = False


@dataclass
class ResultadoAlta:
    prs: list[PullRequestAbierto] = field(default_factory=list)
    error: str | None = None

    @property
    def completo(self) -> bool:
        return self.error is None and len(self.prs) == 2


def nombre_rama(repo_codigo: str, marca: str | None = None) -> str:
    """Convencion, no creatividad: esto no es trabajo para un modelo."""
    marca = marca or datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"feat/pipeline-{repo_codigo}-{marca}"


def url_pull_request(cliente: ClienteAdo, repo: str, pr_id: int) -> str:
    return f"https://dev.azure.com/{cliente.org}/{cliente.proyecto}/_git/{repo}/pullrequest/{pr_id}"


def empujar(cliente: ClienteAdo, repo: str, rama: str, cambios: list[Cambio],
            mensaje: str, *, base: str = RAMA_BASE) -> str:
    """Rama + commit en una llamada. Devuelve el sha del commit.

    `oldObjectId` es el sha de la rama base cuando la rama es nueva. Si la rama
    YA existe (reejecucion), hay que partir de SU ultimo commit, no del de la
    base: pasar el de la base sobre una rama existente es un rechazo de ADO.
    """
    repo_id = id_repo(cliente, repo)
    sha_existente = sha_rama(cliente, repo_id, rama)
    old = sha_existente or sha_rama(cliente, repo_id, base) or SHA_VACIO

    # El change_type se RESUELVE aqui, contra la ref a la que de verdad se
    # escribe. El inventario de T1.3 lo calcula contra `main`, y eso es lo
    # correcto para ensenarselo a un humano en la confirmacion -- pero en una
    # reejecucion que reutiliza la rama, el fichero ya existe EN LA RAMA aunque
    # siga sin existir en main, y ADO rechaza el `add` con un 400. Paso en real
    # la primera vez que se ejecuto dos veces seguidas.
    #
    # Dejarlo en manos del llamante seria pedir que acierte cada vez; resolverlo
    # aqui lo hace correcto por construccion.
    cambios = _resolver_tipos(cliente, repo_id, cambios, rama if sha_existente else base)

    resultado = cliente.post(
        f"/_apis/git/repositories/{repo_id}/pushes",
        json={
            "refUpdates": [{"name": f"refs/heads/{rama}", "oldObjectId": old}],
            "commits": [{
                "comment": mensaje,
                "changes": [
                    {
                        "changeType": c.change_type,
                        "item": {"path": c.ruta},
                        "newContent": {"content": c.contenido, "contentType": "rawtext"},
                    }
                    for c in cambios
                ],
            }],
        },
    )
    return resultado["commits"][0]["commitId"]


def _resolver_tipos(cliente: ClienteAdo, repo_id: str, cambios: list[Cambio],
                    ref: str) -> list[Cambio]:
    """Corrige cada change_type segun si el fichero existe ya en `ref`."""
    try:
        existentes = {item["path"] for item in listar_arbol(cliente, repo_id, "/", rama=ref)}
    except ErrorAdo as error:
        if error.status == 404 and error.type_key == ITEM_NO_ENCONTRADO:
            existentes = set()  # repo o rama sin contenido: todo es `add`
        else:
            raise

    return [
        Cambio(c.ruta, c.contenido, "edit" if c.ruta in existentes else "add")
        for c in cambios
    ]


def buscar_pr_abierto(cliente: ClienteAdo, repo: str, rama: str) -> dict | None:
    """PR activo desde esa rama, si lo hay. Es la mitad de la idempotencia."""
    respuesta = cliente.get(
        f"/_apis/git/repositories/{id_repo(cliente, repo)}/pullrequests",
        params={"searchCriteria.sourceRefName": f"refs/heads/{rama}",
                "searchCriteria.status": "active"},
    )
    return respuesta["value"][0] if respuesta.get("count") else None


def crear_pull_request(cliente: ClienteAdo, propuesta: Propuesta,
                       *, base: str = RAMA_BASE) -> PullRequestAbierto:
    existente = buscar_pr_abierto(cliente, propuesta.repo, propuesta.rama)
    if existente:
        return PullRequestAbierto(propuesta.repo, propuesta.rama, existente["pullRequestId"],
                                  url_pull_request(cliente, propuesta.repo, existente["pullRequestId"]),
                                  reutilizado=True)

    creado = cliente.post(
        f"/_apis/git/repositories/{id_repo(cliente, propuesta.repo)}/pullrequests",
        json={
            "sourceRefName": f"refs/heads/{propuesta.rama}",
            "targetRefName": f"refs/heads/{base}",
            "title": propuesta.titulo,
            "description": propuesta.descripcion,
        },
    )
    return PullRequestAbierto(propuesta.repo, propuesta.rama, creado["pullRequestId"],
                              url_pull_request(cliente, propuesta.repo, creado["pullRequestId"]))


def abandonar(cliente: ClienteAdo, pr: PullRequestAbierto) -> None:
    """Abandona el PR y borra su rama. El rollback de la regla 2."""
    repo_id = id_repo(cliente, pr.repo)
    cliente.patch(f"/_apis/git/repositories/{repo_id}/pullrequests/{pr.id}",
                  json={"status": "abandoned"})
    sha = sha_rama(cliente, repo_id, pr.rama)
    if sha:
        cliente.post(f"/_apis/git/repositories/{repo_id}/refs",
                     json=[{"name": f"refs/heads/{pr.rama}", "oldObjectId": sha,
                            "newObjectId": SHA_VACIO}])


def abrir_alta(cliente: ClienteAdo, variables: Propuesta, pipeline: Propuesta) -> ResultadoAlta:
    """Los dos pull requests, en orden, o ninguno.

    Las variables PRIMERO a proposito: si el segundo falla, lo que queda abierto
    es el PR inofensivo (unas variables que nadie usa todavia), no un pipeline
    roto apuntando a ficheros que no existen.
    """
    resultado = ResultadoAlta()

    empujar(cliente, variables.repo, variables.rama, variables.cambios, variables.mensaje_commit)
    pr_variables = crear_pull_request(cliente, variables)
    resultado.prs.append(pr_variables)

    try:
        empujar(cliente, pipeline.repo, pipeline.rama, pipeline.cambios, pipeline.mensaje_commit)
        resultado.prs.append(crear_pull_request(cliente, pipeline))
    except ErrorAdo as error:
        # Regla 2: medio alta abierta es peor que nada.
        abandonar(cliente, pr_variables)
        resultado.prs.clear()
        resultado.error = f"fallo el PR del pipeline, se revirtio el de variables: {error}"

    return resultado


if __name__ == "__main__":
    import sys

    from dotenv import load_dotenv

    load_dotenv()
    escribir = "--escribir" in sys.argv

    with ClienteAdo.desde_entorno() as cliente:
        marca = datetime.now().strftime("%Y%m%d-%H%M%S")
        rama = nombre_rama("demo-api-node", marca)

        variables = Propuesta(
            repo="demo-api-node", rama=rama,
            cambios=[Cambio("/vars/dev.yml", "variables:\n  - name: environmentName\n    value: \"dev\"\n", "add")],
            titulo="feat: variables de pipeline por entorno",
            descripcion="PRUEBA de ado/cambios.py. **Mergear este PR ANTES que el del pipeline.**",
            mensaje_commit="feat: variables de pipeline por entorno",
        )
        pipeline = Propuesta(
            repo="pipelines", rama=rama,
            cambios=[Cambio("/demo-api-node/azure-pipelines.yml", "# prueba de ado/cambios.py\n", "add")],
            titulo="feat: pipeline para demo-api-node",
            descripcion="PRUEBA de ado/cambios.py. **Mergear DESPUES del PR de variables.**",
            mensaje_commit="feat: pipeline para demo-api-node",
        )

        if not escribir:
            print("SIMULACRO (no se escribe nada). Usa --escribir para hacerlo de verdad.\n")
            for p in (variables, pipeline):
                print(f"  repo {p.repo}  rama {p.rama}")
                for c in p.cambios:
                    print(f"    {c.change_type:>4}  {c.ruta}")
            print("\n  Orden: primero las variables, despues el pipeline.")
            raise SystemExit(0)

        print(f"Escribiendo de verdad en {cliente.org}/{cliente.proyecto}...\n")
        resultado = abrir_alta(cliente, variables, pipeline)
        if resultado.error:
            print(f"  FALLO: {resultado.error}")
            raise SystemExit(1)
        for pr in resultado.prs:
            print(f"  PR #{pr.id:<4} {pr.repo:<20} {'(reutilizado)' if pr.reutilizado else '(nuevo)'}")
            print(f"           {pr.url}")

        print("\n  Segunda ejecucion con la MISMA rama (idempotencia):")
        repetido = abrir_alta(cliente, variables, pipeline)
        for pr in repetido.prs:
            print(f"    PR #{pr.id:<4} {pr.repo:<20} reutilizado={pr.reutilizado}   <- debe ser True")
