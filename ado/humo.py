"""T0.2 - llamada de humo a Azure DevOps: listar los repos del proyecto.

Comprueba de una vez que AZDO_ORG/AZDO_PROJECT existen y que el PAT tiene
alcance de LECTURA sobre Code. Lo que NO demuestra: crear pull requests (eso se
prueba en T4.2). El permiso de ESCRITURA si quedo probado, antes de lo previsto,
al sembrar el escenario por API en T0.3.

Este fichero era el doble de largo hasta T1.1: llevaba dentro su propio
diagnostico de respuestas (la redireccion al login, el HTML disfrazado de exito,
el 401 de una ruta mal formada). Todo eso vive ahora en ado/cliente.py, que es
donde tiene que estar -- se escribio mal una vez y hubo que corregirlo en dos
sitios a la vez. Lo que queda aqui es solo la comprobacion.

Ejecuta con (desde la raiz del repo): .venv/bin/python -m ado.humo
"""
import os
import sys

from ado.cliente import ClienteAdo, ErrorAdo


def main() -> None:
    try:
        cliente = ClienteAdo.desde_entorno()
    except ErrorAdo as error:
        print(f"[config] {error}")
        print("[config] Copia .env.example a .env y rellenalas (ver T0.2 del plan).")
        sys.exit(2)

    with cliente:
        print(f"[humo] {cliente!r}")
        print(f"[humo] GET {cliente.base_proyecto}/_apis/git/repositories\n")

        try:
            repos = cliente.get("/_apis/git/repositories")["value"]
        except ErrorAdo as error:
            print(f"[FALLO] {error}", file=sys.stderr)
            sys.exit(1)

        print(f"[OK] {len(repos)} repositorio(s) en el proyecto:\n")
        for repo in sorted(repos, key=lambda r: r["name"]):
            rama = (repo.get("defaultBranch") or "(sin commits todavia)").removeprefix("refs/heads/")
            print(f"  - {repo['name']:<24} rama por defecto: {rama}")

        # Enlace con T0.3: sin el repo de plantillas no hay catalogo que descubrir
        # en T1.2. Se avisa aqui, no cuando falle alli.
        nombre_plantillas = os.environ.get("AZDO_REPO_PLANTILLAS", "")
        if nombre_plantillas:
            existe = any(r["name"] == nombre_plantillas for r in repos)
            print(f"\n[{'OK' if existe else 'PENDIENTE'}] repo de plantillas "
                  f"'{nombre_plantillas}': "
                  f"{'encontrado' if existe else 'no existe -- ejecuta .venv/bin/python -m ado.sembrar'}")

        print("\n[alcance] Lectura y escritura de Code probadas (esta llamada y T0.3).")
        print("          Crear pull requests sigue sin probarse hasta T4.2.")


if __name__ == "__main__":
    main()
