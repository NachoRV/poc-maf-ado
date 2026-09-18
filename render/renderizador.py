"""El pipeline hijo. DETERMINISTA: ningun modelo escribe el artefacto que se despliega.

Es la frase que mejor resume la arquitectura, y aqui es literal. El modelo
interviene en la conversacion y en la prosa de los pull requests; este fichero,
que produce lo unico que Azure Pipelines va a ejecutar, es codigo normal.

Que genera, y por que cada parte:

  resources.repositories  DOS entradas, no una. `templates` es el catalogo,
                          anclado POR TAG: el pipeline queda clavado a la version
                          de plantilla con la que se decidio, no a la rama. Y
                          `codigo` es el repo que se construye -- hace falta
                          declararlo porque el pipeline vive en `pipelines`, lejos
                          del codigo, y porque las variables se leen de el.
  parameters.entorno      Lista cerrada dev/acc/pro. Es lo que elige que fichero
                          de variables se carga.
  variables               El comun SIEMPRE, mas el del entorno elegido. Ambos con
                          `@codigo`: viven en el repo de codigo, no aqui.
  extends                 La plantilla del catalogo, con los parametros validados.

Construido como diccionario y serializado con yaml.dump: nunca se concatena YAML
a mano, asi la sintaxis la garantiza la libreria. La unica excepcion es la
expresion `${{ parameters.entorno }}`, que yaml.dump escaparia y hay que dejar
literal -- va con un marcador y se sustituye al final, con una comprobacion de
que el resultado se relee.

Ejecuta con (desde la raiz del repo): .venv/bin/python -m render.renderizador
"""
import yaml

from ado.catalogo import PlantillaDisponible

ALIAS_PLANTILLAS = "templates"
ALIAS_CODIGO = "codigo"
CARPETA_VARIABLES = "vars"

# Marcador para la unica expresion que no puede pasar por yaml.dump.
_MARCA_ENTORNO = "__ENTORNO__"
_EXPRESION_ENTORNO = "${{ parameters.entorno }}"


def renderizar_pipeline(plantilla: PlantillaDisponible, parametros: dict, *,
                        proyecto: str, repo_plantillas: str, repo_codigo: str,
                        entornos: list[str], rama_codigo: str = "main") -> str:
    """El YAML del pipeline hijo. `parametros` ya viene validado contra el schema."""
    pipeline = {
        "parameters": [{
            "name": "entorno",
            "type": "string",
            "default": entornos[0],
            "values": list(entornos),
        }],
        "resources": {
            "repositories": [
                {
                    "repository": ALIAS_PLANTILLAS,
                    "type": "git",
                    "name": f"{proyecto}/{repo_plantillas}",
                    # Pin por TAG, no por rama: el pipeline queda clavado a la
                    # version del catalogo con la que se tomo la decision.
                    "ref": f"refs/tags/{plantilla.tag}",
                },
                {
                    "repository": ALIAS_CODIGO,
                    "type": "git",
                    "name": f"{proyecto}/{repo_codigo}",
                    "ref": f"refs/heads/{rama_codigo}",
                },
            ]
        },
        "variables": [
            {"template": f"{CARPETA_VARIABLES}/common.yml@{ALIAS_CODIGO}"},
            {"template": f"{CARPETA_VARIABLES}/{_MARCA_ENTORNO}.yml@{ALIAS_CODIGO}"},
        ],
        "extends": {
            "template": f"{plantilla.ruta_template}@{ALIAS_PLANTILLAS}",
            "parameters": parametros,
        },
    }

    generado = yaml.dump(pipeline, sort_keys=False, default_flow_style=False, allow_unicode=True)
    generado = generado.replace(_MARCA_ENTORNO, _EXPRESION_ENTORNO)

    # Se relee lo generado en vez de confiar en que yaml.dump nunca falle. Ya
    # cazo un fallo real en parametros/variables.py (el marcador de fin de
    # documento que anade safe_dump a un escalar suelto).
    yaml.safe_load(generado)
    return generado


if __name__ == "__main__":
    import os

    from dotenv import load_dotenv

    from ado.catalogo import descubrir_plantillas
    from ado.cliente import ClienteAdo

    load_dotenv()
    with ClienteAdo.desde_entorno() as cliente:
        java = next(p for p in descubrir_plantillas(cliente) if p.id == "ci-java-container")
        print(renderizar_pipeline(
            java,
            {"isDocker": True, "javaVersion": "17", "appVersion": "1.0.0"},
            proyecto=cliente.proyecto,
            repo_plantillas=os.environ.get("AZDO_REPO_PLANTILLAS", "plantillas-ci"),
            repo_codigo="demo-servicio-java",
            entornos=["dev", "acc", "pro"],
        ))
