"""T1.3 - donde aterriza lo que genera el flujo, y que hay ya alli.

REESCRITA tras el cambio de diseno del 2026-09-16. La version original listaba
repos candidatos para que el usuario eligiera destino. Eso ya no aplica: el
destino esta FIJADO por la convencion del team project y no hay nada que elegir.

    pipelines/<repo-de-codigo>/azure-pipelines.yml     <- el pipeline
    <repo-de-codigo>/vars/{common,dev,acc,pro}.yml     <- las variables

Lo que SI sobrevive de aquella tarea, y ahora pesa mas: saber QUE FICHEROS
EXISTEN YA. De eso sale el `changeType` de cada cambio del push ("add" si es
nuevo, "edit" si ya esta), y equivocarse es un 400 de Azure DevOps. Ya no es un
fichero: son cinco, repartidos en DOS repos, y cada uno se resuelve por separado
-- el common.yml puede existir mientras el pipeline todavia no.

Los vars/*.yml se traen CON CONTENIDO, no solo su existencia. Hace falta para el
merge de T3.4: el usuario puede ampliarlos a mano despues del alta, y una
segunda ejecucion que los volcara enteros destruiria ese trabajo. El pipeline,
en cambio, se regenera entero desde la plantilla, asi que de el solo interesa si
esta o no.

Un 404 al pedir un fichero es un caso NORMAL aqui, no un error -- pero solo si
es un GitItemNotFoundException. Esa discriminacion la hace ado/git.py; sin ella,
"ese repo no existe" se colaria como "ese fichero no existe".

Ejecuta con (desde la raiz del repo): .venv/bin/python -m ado.destinos <repo-de-codigo>
"""
import os

from pydantic import BaseModel

from ado.cliente import ClienteAdo
from ado.git import id_repo, leer_fichero_si_existe

ENTORNOS = ("common", "dev", "acc", "pro")
FICHERO_PIPELINE = "azure-pipelines.yml"
CARPETA_VARIABLES = "vars"


class FicheroDestino(BaseModel):
    """Una de las rutas del alta, con lo que ya hay en ella."""

    repo: str
    ruta: str
    contenido: str | None = None

    @property
    def existe(self) -> bool:
        return self.contenido is not None

    @property
    def change_type(self) -> str:
        """Lo que espera la Pushes API. Es la unica razon de ser de esta clase:
        un `add` sobre un fichero existente (o un `edit` sobre uno que no) es un
        400 de ADO, no un aviso."""
        return "edit" if self.existe else "add"


class Inventario(BaseModel):
    """Las cinco rutas del alta de un repo de codigo, resueltas."""

    repo_codigo: str
    repo_pipelines: str
    pipeline: FicheroDestino
    variables: dict[str, FicheroDestino]  # {"common": ..., "dev": ..., ...}

    @property
    def es_alta(self) -> bool:
        """True si no existe nada todavia. Distinguirlo de una actualizacion
        importa para el texto de los PR y para saber si hay algo que fusionar."""
        return not self.pipeline.existe and not any(v.existe for v in self.variables.values())

    def resumen(self) -> list[str]:
        lineas = [f"{self.pipeline.change_type:>4}  {self.repo_pipelines}{self.pipeline.ruta}"]
        lineas += [
            f"{fichero.change_type:>4}  {self.repo_codigo}{fichero.ruta}"
            for _entorno, fichero in sorted(self.variables.items())
        ]
        return lineas


def nombre_repo_pipelines() -> str:
    return os.environ.get("AZDO_REPO_PIPELINES", "pipelines")


def ruta_pipeline(repo_codigo: str) -> str:
    """Dentro del repo `pipelines`, una carpeta por repo de codigo."""
    return f"/{repo_codigo}/{FICHERO_PIPELINE}"


def ruta_variables(entorno: str) -> str:
    """Dentro del repo de codigo. `common` es un fichero mas, no un caso aparte."""
    return f"/{CARPETA_VARIABLES}/{entorno}.yml"


def inventario(cliente: ClienteAdo, repo_codigo: str, *,
               entornos: tuple[str, ...] = ENTORNOS,
               rama_pipelines: str = "main", rama_codigo: str = "main") -> Inventario:
    """Estado actual de las cinco rutas del alta, en los dos repos.

    UNA RAMA POR REPO, no una compartida. Son dos repositorios independientes con
    espacios de nombres de rama independientes: una rama que existe en el repo de
    codigo no tiene por que existir en `pipelines`. La primera version de esta
    funcion tenia un unico parametro `rama` y lo aplicaba a los dos -- en el flujo
    real, que lee `main` en ambos, no se habria notado nunca; salto al probarla
    contra una rama temporal. El parametro compartido invitaba al error.

    Cuesta 5 lecturas + 2 resoluciones de id (cacheadas). Se hace de una vez, al
    entrar en el estado de planificacion, y no fichero a fichero por el camino:
    asi la confirmacion humana ve el plan COMPLETO antes de escribir nada.
    """
    repo_pipelines = nombre_repo_pipelines()
    id_pipelines = id_repo(cliente, repo_pipelines)
    id_codigo = id_repo(cliente, repo_codigo)

    pipeline = FicheroDestino(
        repo=repo_pipelines,
        ruta=ruta_pipeline(repo_codigo),
        # Sin contenido a proposito: el pipeline se regenera entero desde la
        # plantilla, asi que de el solo interesa si existe. Traerlo seria una
        # llamada mas para un dato que nadie va a mirar.
        contenido=None,
    )
    existente = leer_fichero_si_existe(cliente, id_pipelines, pipeline.ruta, rama=rama_pipelines)
    pipeline = pipeline.model_copy(update={"contenido": existente})

    variables = {
        entorno: FicheroDestino(
            repo=repo_codigo,
            ruta=ruta_variables(entorno),
            contenido=leer_fichero_si_existe(cliente, id_codigo, ruta_variables(entorno), rama=rama_codigo),
        )
        for entorno in entornos
    }

    return Inventario(
        repo_codigo=repo_codigo,
        repo_pipelines=repo_pipelines,
        pipeline=pipeline,
        variables=variables,
    )


if __name__ == "__main__":
    import sys

    from dotenv import load_dotenv

    load_dotenv()
    repo = sys.argv[1] if len(sys.argv) > 1 else "demo-servicio-java"

    with ClienteAdo.desde_entorno() as cliente:
        inv = inventario(cliente, repo)
        print(f"[inventario] {repo}  ->  {'ALTA (no existe nada)' if inv.es_alta else 'ACTUALIZACION'}\n")
        for linea in inv.resumen():
            print(f"  {linea}")
        print("\n[contenido] solo los vars/*.yml se traen con contenido (hace falta para el merge de T3.4):")
        for entorno, fichero in sorted(inv.variables.items()):
            estado = f"{len(fichero.contenido)} caracteres" if fichero.existe else "no existe"
            print(f"  {entorno:<7} {estado}")
