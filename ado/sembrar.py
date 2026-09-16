"""T0.3 - sembrar el escenario de la PoC en Azure DevOps.

Crea, si no existen ya:
  - plantillas-ci      : el catalogo, con catalog/<id>/{manifest.yaml,
                         parameters.schema.json, template.yaml} copiado de
                         poc-agentes, y un tag v1.0.0 al que anclar el `extends`.
  - demo-servicio-java : repo destino, con README.md y pom.xml.
  - demo-api-node      : repo destino, con README.md y package.json.

Por que por API y no a mano desde la web: la operacion que hace falta para meter
el contenido es la PUSHES API, la misma de T4.1 (rama + commit en una sola
llamada, sin clonar nada en local). Sembrar con ella deja probada contra la
organizacion real la parte dificil de T4.1 antes de llegar alli.

Por que los repos destino NO quedan vacios (cambio sobre el plan original, que
decia "vacios o casi"): para crear una rama hay que pasar el `oldObjectId` de la
rama base, que sale de GET /refs?filter=heads/main. Un repo SIN COMMITS no tiene
refs, asi que no hay main, asi que no hay oldObjectId, asi que T4.1 no tendria de
donde partir. Lo que si falta a proposito en los destinos es `azure-pipelines.yml`
-- eso es justo lo que el flujo de la PoC tiene que anadir.

Idempotente: ejecutarlo dos veces no duplica nada ni falla. Misma disciplina que
pide T4.3 para los pull requests; practicarla aqui sale gratis.

Ejecuta con (desde la raiz del repo): .venv/bin/python -m ado.sembrar
"""
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from ado.cliente import ClienteAdo, ErrorAdo

SHA_VACIO = "0" * 40  # oldObjectId para "esta rama todavia no existe"
RAMA_POR_DEFECTO = "refs/heads/main"

# Fuente de los ficheros del catalogo: la PoC anterior. Se leen en tiempo de
# ejecucion en vez de duplicarlos aqui -- una sola fuente de verdad mientras
# poc-agentes siga vivo.
CATALOGO_ORIGEN = Path(__file__).parent.parent.parent / "poc-agentes" / "catalog"
FICHEROS_PLANTILLA = ("manifest.yaml", "parameters.schema.json", "template.yaml")

README_CATALOGO = """\
# plantillas-ci

Catalogo de plantillas de pipeline de Azure DevOps.

Una carpeta por plantilla bajo `catalog/`, cada una con tres ficheros:

| Fichero | Para quien | Que es |
|---|---|---|
| `manifest.yaml` | humanos y LLM | contrato legible: parametros, de que senal sale cada uno (`derived_from`) y por que se elige esta plantilla (`selection_rationale`) |
| `parameters.schema.json` | programas | JSON Schema que valida los parametros de verdad |
| `template.yaml` | Azure Pipelines | la plantilla que los repos destino extienden |

Los repos destino no copian estos ficheros: dejan un pipeline hijo corto que
hace `extends` contra este repo, anclado por tag.

**Anadir una plantilla es un Pull Request a este repo**, no un despliegue del
sistema que la consume.
"""

REPOS_DESTINO = {
    "demo-servicio-java": {
        "README.md": "# demo-servicio-java\n\nServicio Java/Maven de ejemplo. Repo destino de la PoC: todavia no tiene pipeline.\n",
        "pom.xml": (Path(__file__).parent.parent.parent / "poc-agentes" / "repo-java" / "pom.xml"),
    },
    "demo-api-node": {
        "README.md": "# demo-api-node\n\nAPI Node de ejemplo. Repo destino de la PoC: todavia no tiene pipeline.\n",
        "package.json": (Path(__file__).parent.parent.parent / "poc-agentes" / "repo-node" / "package.json"),
    },
}


def repos_existentes(cliente: ClienteAdo) -> dict[str, dict]:
    datos = cliente.get("/_apis/git/repositories")
    return {r["name"]: r for r in datos["value"]}


def id_proyecto(cliente: ClienteAdo) -> str:
    """El id sale del listado de repos, que YA es de ambito proyecto y ya se
    necesita de todas formas. Se evita asi /_apis/projects, que es de nivel
    ORGANIZACION -- confundir los dos ambitos fue el bug de T0.3 y ADO lo
    castiga con un 401 que parece un problema de PAT.

    Solo si el proyecto no tuviera ni un repo se cae al endpoint de organizacion,
    y entonces se usa get_org() explicitamente.
    """
    repos = repos_existentes(cliente)
    if repos:
        return next(iter(repos.values()))["project"]["id"]
    return cliente.get_org(f"/_apis/projects/{cliente.proyecto}")["id"]


def tiene_commits(cliente: ClienteAdo, repo_id: str) -> bool:
    refs = cliente.get(f"/_apis/git/repositories/{repo_id}/refs", params={"filter": "heads/"})
    return refs["count"] > 0


def empujar(cliente: ClienteAdo, repo_id: str, ficheros: dict[str, str], mensaje: str) -> str:
    """Rama + commit en UNA llamada, sin clonar nada. El corazon de T4.1.

    `oldObjectId` = 40 ceros significa "esta ref no existe todavia, creala".
    Para anadir a una rama existente iria el sha del ultimo commit de la base.
    """
    cambios = [
        {
            "changeType": "add",
            "item": {"path": ruta},
            "newContent": {"content": contenido, "contentType": "rawtext"},
        }
        for ruta, contenido in sorted(ficheros.items())
    ]
    resultado = cliente.post(
        f"/_apis/git/repositories/{repo_id}/pushes",
        json={
            "refUpdates": [{"name": RAMA_POR_DEFECTO, "oldObjectId": SHA_VACIO}],
            "commits": [{"comment": mensaje, "changes": cambios}],
        },
    )
    return resultado["commits"][0]["commitId"]


def fijar_rama_por_defecto(cliente: ClienteAdo, repo_id: str) -> None:
    """Un repo recien creado puede tener defaultBranch apuntando a master aunque
    el primer push haya creado main. Se fija explicitamente: si no, T4.1 crearia
    los PR contra una rama base que no existe."""
    cliente.patch(f"/_apis/git/repositories/{repo_id}", json={"defaultBranch": RAMA_POR_DEFECTO})


def tag_existe(cliente: ClienteAdo, repo_id: str, tag: str) -> bool:
    refs = cliente.get(f"/_apis/git/repositories/{repo_id}/refs", params={"filter": f"tags/{tag}"})
    return refs["count"] > 0


def crear_tag(cliente: ClienteAdo, repo_id: str, tag: str, commit_id: str) -> None:
    """Tag anotado (lleva mensaje y autor). El `extends` de los pipelines hijos se
    ancla a `refs/tags/<tag>`: sin un tag REAL no hay a que apuntar y T1.2/T4.1
    no tienen catalogo versionado que leer."""
    cliente.post(
        f"/_apis/git/repositories/{repo_id}/annotatedtags",
        json={
            "name": tag,
            "taggedObject": {"objectId": commit_id},
            "message": f"Catalogo de plantillas {tag} - version inicial sembrada por T0.3",
        },
    )


def leer_catalogo_local() -> dict[str, str]:
    """Ficheros del catalogo de poc-agentes, con la ruta que tendran en el repo."""
    if not CATALOGO_ORIGEN.is_dir():
        raise ErrorAdo(f"no encuentro el catalogo de origen en {CATALOGO_ORIGEN}")

    ficheros = {"/README.md": README_CATALOGO}
    for carpeta in sorted(CATALOGO_ORIGEN.iterdir()):
        if not (carpeta / "manifest.yaml").is_file():
            continue
        for nombre in FICHEROS_PLANTILLA:
            origen = carpeta / nombre
            if not origen.is_file():
                raise ErrorAdo(f"a la plantilla {carpeta.name} le falta {nombre}")
            ficheros[f"/catalog/{carpeta.name}/{nombre}"] = origen.read_text()
    return ficheros


def asegurar_repo(cliente: ClienteAdo, nombre: str, proyecto_id: str,
                  ficheros: dict[str, str], mensaje: str) -> dict:
    """Crea el repo y su commit inicial solo si hacen falta. Devuelve el repo y el
    sha del commit inicial (o None si ya los tenia de antes)."""
    existentes = repos_existentes(cliente)

    if nombre in existentes:
        repo = existentes[nombre]
        print(f"  [=] repo '{nombre}' ya existe")
    else:
        repo = crear_repo(cliente, nombre, proyecto_id)
        print(f"  [+] repo '{nombre}' creado")

    if tiene_commits(cliente, repo["id"]):
        print(f"  [=] '{nombre}' ya tiene commits, no se toca el contenido")
        return {"repo": repo, "commit": None}

    commit = empujar(cliente, repo["id"], ficheros, mensaje)
    fijar_rama_por_defecto(cliente, repo["id"])
    print(f"  [+] commit inicial {commit[:8]} con {len(ficheros)} fichero(s), rama main por defecto")
    return {"repo": repo, "commit": commit}


def main() -> None:
    load_dotenv()
    nombre_catalogo = os.environ.get("AZDO_REPO_PLANTILLAS", "plantillas-ci")
    tag = os.environ.get("AZDO_TAG_PLANTILLAS", "v1.0.0")

    with ClienteAdo.desde_entorno() as cliente:
        try:
            proyecto_id = id_proyecto(cliente)
            print(f"[sembrar] {cliente.org}/{cliente.proyecto} (id {proyecto_id})\n")

            print(f"[1/2] catalogo de plantillas -> {nombre_catalogo}")
            ficheros_catalogo = leer_catalogo_local()
            plantillas = sorted({r.split("/")[2] for r in ficheros_catalogo if r.startswith("/catalog/")})
            print(f"  [i] {len(plantillas)} plantilla(s) leidas de {CATALOGO_ORIGEN}: {', '.join(plantillas)}")

            resultado = asegurar_repo(
                cliente, nombre_catalogo, proyecto_id, ficheros_catalogo,
                "feat: catalogo inicial de plantillas (ci-java-container, ci-dotnet, ci-node-container)",
            )
            repo_catalogo = resultado["repo"]

            if tag_existe(cliente, repo_catalogo["id"], tag):
                print(f"  [=] tag {tag} ya existe")
            else:
                commit_id = resultado["commit"]
                if commit_id is None:
                    refs = cliente.get(
                        f"/_apis/git/repositories/{repo_catalogo['id']}/refs",
                        params={"filter": "heads/main"},
                    )
                    commit_id = refs["value"][0]["objectId"]
                crear_tag(cliente, repo_catalogo["id"], tag, commit_id)
                print(f"  [+] tag anotado {tag} -> {commit_id[:8]}")

            print("\n[2/2] repos destino")
            for nombre, contenido in REPOS_DESTINO.items():
                ficheros = {
                    f"/{fichero}": (valor.read_text() if isinstance(valor, Path) else valor)
                    for fichero, valor in contenido.items()
                }
                asegurar_repo(cliente, nombre, proyecto_id, ficheros,
                              "chore: commit inicial del repo de ejemplo (sin pipeline todavia)")

            print("\n[listo] Escenario sembrado. Comprueba con: .venv/bin/python -m ado.humo")
            print("[nota]  Los destinos NO tienen azure-pipelines.yml a proposito: eso es lo que")
            print("        el flujo de la PoC tiene que anadir via Pull Request.")

        except ErrorAdo as error:
            print(f"\n[FALLO] {error}", file=sys.stderr)
            print("[pista] Si es un 401: mira primero la ruta de la API, no el PAT. Si la ruta es", file=sys.stderr)
            print("        correcta, crear repositorios puede requerir el scope 'Code (Manage)',", file=sys.stderr)
            print("        que no va incluido en 'Code (Read & Write)'.", file=sys.stderr)
            sys.exit(1)

if __name__ == "__main__":
    main()
