"""T0.2 - llamada de humo a Azure DevOps: listar los repos del proyecto.

No es solo un GET. Es un diagnostico, y el motivo esta en el plan (docs/Plan
poc-ado.md, T0.2): un PAT invalido, caducado o sin los scopes necesarios NO
siempre devuelve 401 en Azure DevOps. Muchas veces devuelve 200 o 203 con la
pagina HTML de inicio de sesion, o un 302 hacia ella. Si el codigo hace
response.json() a ciegas, el fallo aparece como un error de parseo de JSON que
no menciona la autenticacion por ningun lado.

Por eso aqui:
  - follow_redirects=False, para que un 302 a la pagina de login se vea como lo
    que es (un problema de credencial) y no como una respuesta HTML de 200.
  - Se comprueba el Content-Type ANTES de parsear, no el codigo de estado.

Lo que esta llamada demuestra: que AZDO_ORG/AZDO_PROJECT existen y que el PAT
tiene alcance de LECTURA sobre Code. Lo que NO demuestra: que pueda escribir ni
crear pull requests -- eso se comprueba de verdad en T4.1, empujando un commit.

Ejecuta con (desde la raiz del repo): .venv/bin/python -m ado.humo
"""
import base64
import os
import sys

import httpx
from dotenv import load_dotenv

API_VERSION = "7.1"
VARIABLES_REQUERIDAS = ("AZDO_ORG", "AZDO_PROJECT", "AZDO_PAT")


def cabecera_auth(pat: str) -> str:
    """Azure DevOps usa HTTP Basic con el usuario VACIO y el PAT como contrasena.

    Los dos puntos iniciales son ese campo de usuario vacio, no un adorno: sin
    ellos el token es correcto y aun asi se rechaza, sin ninguna pista del motivo.
    """
    credencial = base64.b64encode(f":{pat}".encode()).decode()
    return f"Basic {credencial}"


def leer_configuracion() -> dict:
    load_dotenv()
    faltan = [v for v in VARIABLES_REQUERIDAS if not os.environ.get(v)]
    if faltan:
        print(f"[config] Faltan variables en .env: {', '.join(faltan)}")
        print("[config] Copia .env.example a .env y rellenalas (ver T0.2 del plan).")
        sys.exit(2)
    return {v: os.environ[v] for v in VARIABLES_REQUERIDAS}


def diagnosticar(respuesta: httpx.Response) -> None:
    """Traduce la respuesta a una causa concreta. Sale del proceso si no es util.

    El orden importa: se descartan primero los sintomas que NO son lo que
    parecen (redirecciones y HTML disfrazado de exito), y solo despues se mira
    el codigo de estado.
    """
    content_type = respuesta.headers.get("content-type", "")

    if respuesta.is_redirect:
        destino = respuesta.headers.get("location", "(sin Location)")
        print(f"[FALLO] {respuesta.status_code} redirigiendo a {destino}")
        print("        Azure DevOps manda al login: el PAT no es valido, ha caducado,")
        print("        o la organizacion del .env no es la correcta.")
        sys.exit(1)

    if respuesta.status_code in (401, 403):
        # Comprobado en T0.3: ADO devuelve 401 (no 404) a una RUTA DE API MAL
        # FORMADA, con www-authenticate: Basic. Sin esta rama, el mensaje de
        # abajo culparia al PAT de un error que esta en la URL.
        print(f"[FALLO] {respuesta.status_code} credencial rechazada")
        print("        Causas, en orden de probabilidad:")
        print("        1. La URL de la API no es correcta (ADO responde 401, no 404, a rutas mal formadas).")
        print("        2. Al PAT le faltan scopes para esta operacion.")
        print("        3. El PAT ha caducado o es de otra organizacion.")
        sys.exit(1)

    if "application/json" not in content_type:
        print(f"[FALLO] {respuesta.status_code} con Content-Type: {content_type or '(vacio)'}")
        print("        Esta es la trampa de T0.2: codigo de exito, cuerpo HTML.")
        print("        Es la pagina de inicio de sesion, no la API. Revisa el PAT y sus scopes.")
        sys.exit(1)

    if respuesta.status_code != 200:
        # A partir de aqui el cuerpo SI es JSON, asi que ADO trae su propio
        # mensaje legible. Perderlo cuesta una tarde; se imprime tal cual.
        mensaje = respuesta.json().get("message", respuesta.text)
        print(f"[FALLO] {respuesta.status_code}: {mensaje}")
        sys.exit(1)


def main() -> None:
    config = leer_configuracion()
    org, proyecto = config["AZDO_ORG"], config["AZDO_PROJECT"]
    url = f"https://dev.azure.com/{org}/{proyecto}/_apis/git/repositories"

    print(f"[humo] organizacion : {org}")
    print(f"[humo] proyecto     : {proyecto}")
    print(f"[humo] PAT          : presente ({len(config['AZDO_PAT'])} caracteres, no se imprime)")
    print(f"[humo] GET          : {url}?api-version={API_VERSION}\n")

    respuesta = httpx.get(
        url,
        params={"api-version": API_VERSION},
        headers={"Authorization": cabecera_auth(config["AZDO_PAT"])},
        follow_redirects=False,  # ver docstring: un 302 debe verse como fallo de auth
        timeout=30.0,
    )

    diagnosticar(respuesta)

    repos = respuesta.json()["value"]
    print(f"[OK] {len(repos)} repositorio(s) en el proyecto:\n")
    for repo in sorted(repos, key=lambda r: r["name"]):
        rama = (repo.get("defaultBranch") or "(sin commits todavia)").removeprefix("refs/heads/")
        print(f"  - {repo['name']:<28} rama por defecto: {rama}")

    # Enlace con T0.3: el repo de plantillas tiene que existir antes de poder
    # descubrir el catalogo (T1.2). Se avisa ahora, no cuando falle alli.
    nombre_plantillas = os.environ.get("AZDO_REPO_PLANTILLAS", "")
    if nombre_plantillas:
        existe = any(r["name"] == nombre_plantillas for r in repos)
        marca = "OK" if existe else "PENDIENTE"
        print(f"\n[{marca}] repo de plantillas '{nombre_plantillas}': "
              f"{'encontrado' if existe else 'no existe todavia -- se crea en T0.3'}")

    print("\n[alcance] Esto demuestra lectura de Code. El permiso de ESCRITURA y de")
    print("          crear pull requests no queda probado hasta T4.1.")


if __name__ == "__main__":
    main()
