"""T1.1 - cliente REST minimo de Azure DevOps.

Un solo sitio donde vive "como se hace una peticion a ADO y como se interpreta
la respuesta". Antes de esto el patron estaba duplicado en ado/humo.py y
ado/sembrar.py, y ya se demostro que acertarlo es dificil: el diagnostico se
escribio mal en T0.2 y hubo que corregirlo en T0.3, en los dos ficheros a la vez.

Tres cosas que este cliente hace por ti, y las tres salen de un fallo real:

1. DOS AMBITOS DE URL, no uno.
   Casi toda la API cuelga del proyecto (.../{org}/{proyecto}/_apis/...), pero
   algunos endpoints son de nivel ORGANIZACION (.../{org}/_apis/...). Mezclarlos
   es exactamente el bug de T0.3, y ADO lo castiga con un 401 (no un 404) que
   parece un problema de credenciales y te manda a mirar el PAT durante un rato.
   Por eso hay dos metodos distintos, `get`/`post`/`patch` (proyecto) y
   `get_org` (organizacion): elegir mal deja de ser algo que pase por descuido.

2. api-version SIEMPRE.
   Azure DevOps lo exige en cada llamada y olvidarlo da un error poco claro. Se
   inyecta solo; se puede sobreescribir pasandolo en `params` si algun endpoint
   necesitara otra version.

3. El PAT no se filtra.
   Ni en __repr__, ni en los mensajes de ErrorAdo. En el Bloque 5 se serializan
   objetos a runs/ y un token en una traza guardada en disco es justo lo que el
   .gitignore intenta evitar.

Ejecuta la comprobacion con: .venv/bin/python -m ado.cliente
"""
import base64
import os
from typing import Any

import httpx
from dotenv import load_dotenv

API_VERSION = "7.1"


class ErrorAdo(RuntimeError):
    """Fallo de una llamada a Azure DevOps, ya traducido a una causa concreta.

    Guarda los datos por separado (no solo el texto) para que quien la capture
    pueda decidir segun el codigo -- p.ej. T4.3 necesita distinguir "404, la rama
    no existe" de "409, ya existe" sin parsear el mensaje.
    """

    def __init__(self, mensaje: str, *, status: int | None = None,
                 metodo: str | None = None, ruta: str | None = None):
        super().__init__(mensaje)
        self.status = status
        self.metodo = metodo
        self.ruta = ruta


def cabecera_auth(pat: str) -> str:
    """Azure DevOps usa HTTP Basic con el usuario VACIO y el PAT como contrasena.

    Los dos puntos iniciales son ese campo de usuario vacio, no un adorno: sin
    ellos el token es correcto y aun asi se rechaza, sin ninguna pista del motivo.
    """
    return "Basic " + base64.b64encode(f":{pat}".encode()).decode()


class ClienteAdo:
    """Cliente de una organizacion/proyecto concretos de Azure DevOps."""

    def __init__(self, org: str, proyecto: str, pat: str, *, timeout: float = 60.0):
        self.org = org
        self.proyecto = proyecto
        self._http = httpx.Client(
            headers={"Authorization": cabecera_auth(pat)},
            # Un 302 hacia el login debe verse como lo que es (credencial
            # rechazada). Siguiendolo se acabaria en un 200 con HTML y el fallo
            # aparecerria mas tarde como un error de parseo de JSON (T0.2).
            follow_redirects=False,
            timeout=timeout,
        )

    # -- construccion desde el entorno -------------------------------------

    @classmethod
    def desde_entorno(cls) -> "ClienteAdo":
        load_dotenv()
        faltan = [v for v in ("AZDO_ORG", "AZDO_PROJECT", "AZDO_PAT") if not os.environ.get(v)]
        if faltan:
            raise ErrorAdo(f"faltan variables en .env: {', '.join(faltan)}")
        return cls(os.environ["AZDO_ORG"], os.environ["AZDO_PROJECT"], os.environ["AZDO_PAT"])

    # -- las dos bases de URL ----------------------------------------------

    @property
    def base_proyecto(self) -> str:
        return f"https://dev.azure.com/{self.org}/{self.proyecto}"

    @property
    def base_org(self) -> str:
        return f"https://dev.azure.com/{self.org}"

    # -- peticiones ---------------------------------------------------------

    def _pedir(self, metodo: str, base: str, ruta: str, **kwargs) -> Any:
        params = {"api-version": API_VERSION, **(kwargs.pop("params", None) or {})}
        respuesta = self._http.request(metodo, f"{base}{ruta}", params=params, **kwargs)
        self._comprobar(respuesta, metodo, ruta)
        return respuesta.json() if respuesta.content else {}

    @staticmethod
    def _comprobar(respuesta: httpx.Response, metodo: str, ruta: str) -> None:
        """Traduce la respuesta a una causa. EL ORDEN IMPORTA: se descartan
        primero los sintomas que NO se parecen a lo que son."""
        if respuesta.is_redirect:
            raise ErrorAdo(
                f"{respuesta.status_code} redirigiendo al login en {metodo} {ruta}: "
                "el PAT no es valido, ha caducado, o la organizacion no es la correcta",
                status=respuesta.status_code, metodo=metodo, ruta=ruta,
            )

        if respuesta.status_code in (401, 403):
            # Verificado en T0.3: ADO devuelve 401 (NO 404) a una ruta de API mal
            # formada, con www-authenticate: Basic. Por eso "la ruta" va primero
            # en la lista de sospechosos, por delante del PAT.
            raise ErrorAdo(
                f"{respuesta.status_code} credencial rechazada en {metodo} {ruta}. "
                "Causas por probabilidad: (1) la ruta de la API no es correcta "
                "-- ADO responde 401, no 404, a rutas mal formadas, y es facil "
                "confundir un endpoint de organizacion con uno de proyecto; "
                "(2) al PAT le faltan scopes; (3) el PAT ha caducado.",
                status=respuesta.status_code, metodo=metodo, ruta=ruta,
            )

        if "application/json" not in respuesta.headers.get("content-type", ""):
            raise ErrorAdo(
                f"{respuesta.status_code} con cuerpo no-JSON "
                f"({respuesta.headers.get('content-type') or 'sin Content-Type'}) en {metodo} {ruta}: "
                "es la pagina de login de ADO, no la API",
                status=respuesta.status_code, metodo=metodo, ruta=ruta,
            )

        if not respuesta.is_success:
            # A partir de aqui el cuerpo SI es JSON, y los errores de ADO traen un
            # campo `message` legible. Se propaga tal cual: perderlo y quedarse con
            # "HTTP 404" es lo que convierte un fallo de cinco minutos en uno de
            # una tarde.
            cuerpo = respuesta.json()
            raise ErrorAdo(
                f"{respuesta.status_code} en {metodo} {ruta}: {cuerpo.get('message', respuesta.text)}",
                status=respuesta.status_code, metodo=metodo, ruta=ruta,
            )

    # Ambito PROYECTO -- lo normal.
    def get(self, ruta: str, **kwargs) -> Any:
        return self._pedir("GET", self.base_proyecto, ruta, **kwargs)

    def post(self, ruta: str, **kwargs) -> Any:
        return self._pedir("POST", self.base_proyecto, ruta, **kwargs)

    def patch(self, ruta: str, **kwargs) -> Any:
        return self._pedir("PATCH", self.base_proyecto, ruta, **kwargs)

    # Ambito ORGANIZACION -- la excepcion, con nombre propio para que no se use
    # por descuido (ver el bug de T0.3 en el docstring del modulo).
    def get_org(self, ruta: str, **kwargs) -> Any:
        return self._pedir("GET", self.base_org, ruta, **kwargs)

    # -- ciclo de vida ------------------------------------------------------

    def cerrar(self) -> None:
        self._http.close()

    def __enter__(self) -> "ClienteAdo":
        return self

    def __exit__(self, *_excepcion) -> None:
        self.cerrar()

    def __repr__(self) -> str:
        # Sin el PAT, a proposito: este repr puede acabar en un log o en runs/.
        return f"ClienteAdo(org={self.org!r}, proyecto={self.proyecto!r})"


if __name__ == "__main__":
    with ClienteAdo.desde_entorno() as cliente:
        print(f"[cliente] {cliente!r}")
        print(f"[cliente] base proyecto : {cliente.base_proyecto}")
        print(f"[cliente] base org      : {cliente.base_org}\n")

        repos = cliente.get("/_apis/git/repositories")["value"]
        print(f"[ok] ambito proyecto: {len(repos)} repos -> {', '.join(sorted(r['name'] for r in repos))}")

        proyecto = cliente.get_org(f"/_apis/projects/{cliente.proyecto}")
        print(f"[ok] ambito org     : proyecto {proyecto['name']} (id {proyecto['id']})")

        # Criterio de exito de T1.1: un 404 provocado a proposito tiene que
        # imprimir el MENSAJE de ADO, no reventar con un KeyError.
        print("\n[prueba] 404 a proposito sobre un repo que no existe:")
        try:
            cliente.get("/_apis/git/repositories/no-existe-este-repo")
            print("  ERROR: deberia haber fallado")
        except ErrorAdo as error:
            print(f"  status capturado: {error.status}")
            print(f"  mensaje de ADO  : {error}")
