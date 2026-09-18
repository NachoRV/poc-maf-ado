"""T3.4 - los ficheros de variables por entorno. CERO LLM, por decision explicita.

Regla del usuario (2026-09-18), y es la que define todo este modulo:

    "No necesito que infiera ninguna variable: la que no este, se deja el hueco.
     Siempre se crean los 4 ficheros, uno por entorno y el comun, porque esto lo
     tendran todos los proyectos y todas las tecnologias."

Dos consecuencias, y las dos son buenas:

1. NO HAY MODELO AQUI. Un valor de variable de entorno de PRODUCCION no es algo
   que un LLM pueda deducir: no esta en los requisitos, no esta en el repo, y
   una invencion plausible es peor que un hueco porque nadie la revisa. El hueco
   es visible, el valor inventado no.

2. LOS CUATRO FICHEROS SIEMPRE. No se generan "los que hagan falta": common,
   dev, acc y pro, siempre. Que un entorno no tenga variables propias todavia no
   es motivo para no crear su fichero -- el fichero es el sitio donde alguien las
   pondra, y que exista desde el alta evita la pregunta "¿donde va esto?".

Y la tercera, que viene del diseno y no de la peticion:

3. REGENERAR FUNDE, NO SOBRESCRIBE. El usuario puede ampliar estos ficheros a
   mano despues del alta. Una segunda ejecucion que los volcara enteros
   destruiria ese trabajo. Gana SIEMPRE lo que ya estaba.

Formato: plantilla de variables de Azure DevOps, con la forma de lista
(`- name:` / `value:`), que es la documentada para ficheros incluidos con
`variables: - template: ...`. El merge se hace por `name`.

Ejecuta con (desde la raiz del repo): .venv/bin/python -m parametros.variables
"""
import json

import yaml

from requisitos.esquema import Requisitos

ENTORNOS = ("dev", "acc", "pro")
AMBITOS = ("common", *ENTORNOS)

# Marca de hueco. Se escribe como comentario al lado de un valor nulo: el YAML
# sigue siendo valido, y quien abra el Pull Request ve exactamente que falta.
COMENTARIO_HUECO = "TODO: sin valor, rellenar antes de mergear"


def _comunes(requisitos: Requisitos) -> dict[str, str | None]:
    """Lo que se sabe sin inferir nada: sale directo de los requisitos.

    Si `version_app` no se llego a recoger en la conversacion, sale como hueco.
    NO se deduce del repo, ni se pone un 1.0.0 "razonable": eso seria inventar.
    """
    return {
        "serviceName": requisitos.repo_codigo,
        "appVersion": requisitos.version_app,
    }


def _por_entorno(entorno: str) -> dict[str, str | None]:
    """Lo que toda aplicacion necesita por entorno. Deliberadamente minimo: el
    resto lo anade una persona, aqui o mas tarde."""
    return {
        "environmentName": entorno,
        "azureSubscription": None,  # hueco: depende de la suscripcion de cada equipo
    }


def variables_previstas(requisitos: Requisitos) -> dict[str, dict[str, str | None]]:
    """{ambito: {nombre: valor o None}} para los cuatro ficheros, sin excepcion."""
    previstas = {"common": _comunes(requisitos)}
    for entorno in ENTORNOS:
        previstas[entorno] = _por_entorno(entorno)

    # Lo que el usuario dicto en el chat gana sobre los valores calculados: lo
    # dijo explicitamente, no se dedujo.
    for ambito, extra in requisitos.variables_extra.items():
        previstas.setdefault(ambito, {}).update(extra)

    return previstas


def _leer_existentes(contenido: str | None) -> dict[str, str]:
    """{nombre: valor} de un vars/*.yml ya presente en el repo. {} si no existe."""
    if not contenido:
        return {}
    datos = yaml.safe_load(contenido) or {}
    return {
        entrada["name"]: entrada.get("value")
        for entrada in datos.get("variables", [])
        if isinstance(entrada, dict) and "name" in entrada
    }


def fundir(previstas: dict[str, str | None], existentes: dict[str, str]) -> dict[str, str | None]:
    """Union de las dos, ganando SIEMPRE lo que ya estaba en el repo.

    Incluye las variables que un humano anadio y que nosotros no conocemos: no se
    borran por no estar en `previstas`. Esta funcion es la que hace que la
    herramienta sea segura de re-ejecutar.
    """
    return {**previstas, **{n: v for n, v in existentes.items()}}


def renderizar(variables: dict[str, str | None], ambito: str) -> str:
    """El YAML del fichero. Los huecos salen como valor nulo con su comentario."""
    lineas = [
        f"# Variables de pipeline para el ambito '{ambito}'.",
        "# Generado en el alta del pipeline y AMPLIABLE a mano: una regeneracion",
        "# respeta lo que ya este aqui, incluidas las variables que anadas tu.",
        "variables:",
    ]
    for nombre, valor in variables.items():
        if valor is None:
            lineas.append(f"  - name: {nombre}")
            lineas.append(f"    value:   # {COMENTARIO_HUECO}")
        else:
            lineas.append(f"  - name: {nombre}")
            # json.dumps y no yaml.safe_dump: para un escalar suelto, safe_dump
            # anade el marcador de fin de documento ("...") y rompe el fichero.
            # Lo cazo la relectura de abajo, que esta justo para eso. JSON es un
            # subconjunto de YAML, asi que la cita resultante siempre es valida.
            lineas.append(f"    value: {json.dumps(valor)}")
    texto = "\n".join(lineas) + "\n"

    yaml.safe_load(texto)  # el YAML generado se relee: no se confia en yaml.dump
    return texto


def generar(requisitos: Requisitos, existentes_por_ambito: dict[str, str | None]) -> dict[str, str]:
    """{ambito: contenido YAML} para los cuatro ficheros. Logica pura, sin red."""
    previstas = variables_previstas(requisitos)
    return {
        ambito: renderizar(fundir(previstas.get(ambito, {}),
                                  _leer_existentes(existentes_por_ambito.get(ambito))), ambito)
        for ambito in AMBITOS
    }


def origen_de_cada_variable(requisitos: Requisitos,
                            existentes_por_ambito: dict[str, str | None]) -> dict[str, dict[str, str]]:
    """{ambito: {variable: de donde salio}}. Para la traza de runs/.

    Es la diferencia entre "el sistema puso 3 replicas" y "el sistema RESPETO las
    8 que alguien habia puesto a mano". Sin esta columna, revisar una ejecucion
    obliga a comparar ficheros a ojo.
    """
    previstas = variables_previstas(requisitos)
    extra = requisitos.variables_extra
    origenes: dict[str, dict[str, str]] = {}

    for ambito in AMBITOS:
        ya_estaban = _leer_existentes(existentes_por_ambito.get(ambito))
        detalle = {}
        for nombre in fundir(previstas.get(ambito, {}), ya_estaban):
            if nombre in ya_estaban:
                detalle[nombre] = ("conservada del repo (no se ha tocado)"
                                   if nombre in previstas.get(ambito, {})
                                   else "conservada del repo (anadida por una persona)")
            elif nombre in extra.get(ambito, {}):
                detalle[nombre] = "dictada por el usuario en la conversacion"
            elif previstas.get(ambito, {}).get(nombre) is None:
                detalle[nombre] = "HUECO: nadie la ha inventado"
            else:
                detalle[nombre] = "derivada de los requisitos"
        origenes[ambito] = detalle

    return origenes


def huecos(contenido: str) -> list[str]:
    """Nombres de variable que quedaron sin valor. Para avisar en el PR."""
    datos = yaml.safe_load(contenido) or {}
    return [e["name"] for e in datos.get("variables", []) if e.get("value") is None]


if __name__ == "__main__":
    requisitos = Requisitos(tecnologia="java", repo_codigo="demo-servicio-java",
                            version_lenguaje="17", contenedor=True, version_app="1.0.0",
                            variables_extra={"pro": {"replicas": "3"}})

    print("=== alta: repo limpio, se generan LOS CUATRO ficheros ===")
    ficheros = generar(requisitos, {})
    for ambito, contenido in ficheros.items():
        pendientes = huecos(contenido)
        print(f"\n--- vars/{ambito}.yml{'   huecos: ' + str(pendientes) if pendientes else ''}")
        print(contenido.rstrip())

    print("\n\n=== regeneracion: alguien edito pro.yml a mano ===")
    editado_a_mano = """\
variables:
  - name: environmentName
    value: pro
  - name: azureSubscription
    value: SUB-PRODUCCION-REAL
  - name: replicas
    value: '8'
  - name: inventadaPorUnHumano
    value: no-la-toques
"""
    ficheros2 = generar(requisitos, {"pro": editado_a_mano})
    print(ficheros2["pro"].rstrip())

    print("\n  Comprobaciones:")
    resultado = _leer_existentes(ficheros2["pro"])
    print(f"    azureSubscription conservado : {resultado['azureSubscription']!r}  (era un hueco)")
    print(f"    replicas NO pisado por el 3  : {resultado['replicas']!r}")
    print(f"    variable ajena conservada    : {resultado.get('inventadaPorUnHumano')!r}")

    print("\n=== sin version_app: sale HUECO, no un valor inventado ===")
    sin_version = Requisitos(tecnologia="java", repo_codigo="demo-api-node")
    print(f"    huecos en common.yml: {huecos(generar(sin_version, {})['common'])}")
