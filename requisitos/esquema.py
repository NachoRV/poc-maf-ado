"""T2.1 - el objeto `Requisitos`: lo que el chat tiene que averiguar.

Este fichero es la memoria del sistema. El modelo de lenguaje NO recuerda: en
cada turno se le pasa este objeto, propone actualizaciones parciales sobre el, y
esas actualizaciones se validan y se FUNDEN aqui antes de aplicarse. La
conversacion es la interfaz; el estado es nuestro.

Tres decisiones, y las tres son el motivo de que un chat con LLM sea fiable:

1. LOS CAMPOS OBLIGATORIOS NO PUEDEN SER OBLIGATORIOS EN EL TIPO.
   Suena contradictorio y es el centro del diseno. El objeto tiene que poder
   existir A MEDIAS durante toda la conversacion -- en el turno 1 solo se sabe la
   tecnologia. Si Pydantic exigiera `repo_codigo`, no se podria ni construir. Lo
   obligatorio vive en `slots_que_faltan()`, no en el tipo. El tipo valida FORMA;
   la funcion decide SI SE PUEDE AVANZAR. Y quien decide que la conversacion ha
   terminado es esa funcion, nunca el modelo.

2. DOS NIVELES DE "FALTA", NO UNO.
   `tecnologia` y `repo_codigo` son BLOQUEANTES: sin ellos no hay nada que hacer.
   `version_lenguaje`, `contenedor` y `version_app` son RECOMENDADOS: se puede
   seguir sin ellos, pero entonces los tendra que adivinar el LLM en T3.3.
   Distinguirlos le da al chat motivo para preguntar sin obligarle a bloquear, y
   hace visible el compromiso: cada pregunta que el humano responde es una
   adivinanza que el modelo no tiene que hacer.

3. `fusionar()` FUNDE, NUNCA SUSTITUYE.
   Un `None` en la actualizacion parcial significa "no se nada de esto", NO
   "borralo". Si en el turno 3 el modelo se olvida de lo que se dijo en el turno
   1, no puede quitarlo. Y `extra="forbid"` convierte un campo inventado por el
   modelo en un error de validacion, que es justo lo que alimenta el
   validar-y-reintentar de T2.2.

Este modulo es PURO: no habla con Azure DevOps ni con ningun modelo, asi que se
puede probar entero sin red. La unica excepcion es `validar_repo_codigo()`, que
recibe el cliente explicitamente.

Ejecuta la demostracion con: .venv/bin/python -m requisitos.esquema
"""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Tecnologia = Literal["java", "dotnet", "node"]
Entorno = Literal["dev", "acc", "pro"]

ENTORNOS_POR_DEFECTO: list[Entorno] = ["dev", "acc", "pro"]
# "common" no es un entorno, es un ambito de variables: lo comun a los tres.
AMBITOS_VARIABLES = ("common", "dev", "acc", "pro")

# Sin esto no se puede ni empezar: `tecnologia` elige la plantilla y
# `repo_codigo` decide donde aterriza TODO (la carpeta dentro de `pipelines` y
# el repo donde van los vars/*.yml).
SLOTS_BLOQUEANTES = ("tecnologia", "repo_codigo")

# Se puede avanzar sin ellos, pero cada uno que falte es un hueco que tendra que
# rellenar el LLM en T3.3, adivinando. Preguntarlos es barato; adivinarlos, no.
SLOTS_RECOMENDADOS = ("version_lenguaje", "contenedor", "version_app")

DESCRIPCIONES = {
    "tecnologia": "tecnologia principal del repositorio (java, dotnet o node)",
    "repo_codigo": "nombre del repositorio de codigo en Azure DevOps",
    "version_lenguaje": "version del lenguaje o runtime (p.ej. 17 para Java, 8.0 para .NET, 20 para Node)",
    "contenedor": "si la aplicacion se publica como imagen de contenedor",
    "version_app": "version semantica de la aplicacion (p.ej. 1.0.0)",
}


class Requisitos(BaseModel):
    """Todos los campos admiten None a proposito: ver decision 1 del docstring."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tecnologia: Tecnologia | None = None
    repo_codigo: str | None = None
    version_lenguaje: str | None = None
    contenedor: bool | None = None
    version_app: str | None = None

    # Tiene default porque es la convencion de la casa: el usuario solo lo toca
    # si su caso es distinto. No es un slot que el chat tenga que perseguir.
    entornos: list[Entorno] = Field(default_factory=lambda: list(ENTORNOS_POR_DEFECTO))

    # Lo que el usuario anada POR ENCIMA del juego inicial que declara el
    # manifest. Anidado por ambito porque la misma variable puede valer distinto
    # en dev y en pro: {"pro": {"replicas": "8"}}.
    variables_extra: dict[str, dict[str, str]] = Field(default_factory=dict)

    # Valvula de escape: lo que el usuario dice y no encaja en ningun slot. Es
    # mejor que quede aqui a que el modelo lo fuerce dentro de una enumeracion
    # ("es un proyecto de Go" no puede acabar en `tecnologia`).
    notas: str | None = None


def slots_que_faltan(requisitos: Requisitos) -> list[str]:
    """Los BLOQUEANTES que siguen vacios. Lista vacia = se puede avanzar.

    Esta funcion, y no el modelo, es quien decide que la conversacion ha
    terminado. Un LLM al que se le pregunta "¿ya tienes todo?" contesta que si
    con una seguridad admirable y datos a medias.
    """
    return [s for s in SLOTS_BLOQUEANTES if getattr(requisitos, s) is None]


def slots_recomendados_vacios(requisitos: Requisitos) -> list[str]:
    """Los que no bloquean pero, si faltan, los tendra que adivinar el LLM."""
    return [s for s in SLOTS_RECOMENDADOS if getattr(requisitos, s) is None]


def esta_completo(requisitos: Requisitos) -> bool:
    return not slots_que_faltan(requisitos)


def fusionar(requisitos: Requisitos, parcial: dict) -> Requisitos:
    """Aplica una actualizacion parcial y devuelve un objeto NUEVO.

    Reglas, en orden de importancia:
    - Un valor None se IGNORA. Significa "no se nada de esto", no "borralo". Es
      lo que impide que un modelo olvidadizo destruya lo dicho en turnos previos.
    - Una cadena VACIA o de solo espacios cuenta como None. Un modelo que no sabe
      algo devuelve "" tan a menudo como null, y sin esta regla el slot quedaria
      marcado como relleno (slots_que_faltan mira `is None`) y el flujo seguiria
      con una version_app vacia. Las cadenas con contenido se recortan.
    - `contenedor: False` NO es "sin dato": el filtro compara contra None, no por
      verdad. "No, sin Docker" es una respuesta, y perderla seria peor que no
      haberla preguntado.
    - `variables_extra` se funde en profundidad (por ambito y por nombre); el
      resto de campos se sustituyen.
    - Se reconstruye el modelo entero en vez de usar model_copy(update=...),
      porque model_copy NO valida: un `tecnologia: "cobol"` entraria tan tranquilo.
      Aqui revienta, y ese error es la entrada del reintento de T2.2.
    """
    cambios = {}
    for clave, valor in parcial.items():
        if valor is None:
            continue
        if isinstance(valor, str):
            valor = valor.strip()
            if not valor:
                continue
        cambios[clave] = valor

    if "variables_extra" in cambios:
        fundidas = {ambito: dict(vars_) for ambito, vars_ in requisitos.variables_extra.items()}
        for ambito, nuevas in cambios["variables_extra"].items():
            fundidas.setdefault(ambito, {}).update(nuevas)
        cambios["variables_extra"] = fundidas

    return Requisitos(**{**requisitos.model_dump(), **cambios})


def descripcion(requisitos: Requisitos) -> str:
    """Texto plano para que el humano confirme en T2.3. Se muestra TODO, incluido
    lo que sigue vacio: la confirmacion sirve para cazar lo que el modelo
    rellenara por su cuenta, y eso no se ve si solo se enseña lo relleno."""
    lineas = []
    for campo in (*SLOTS_BLOQUEANTES, *SLOTS_RECOMENDADOS):
        valor = getattr(requisitos, campo)
        lineas.append(f"  {campo:<18} {valor if valor is not None else '(sin definir)'}")
    lineas.append(f"  {'entornos':<18} {', '.join(requisitos.entornos)}")
    if requisitos.variables_extra:
        for ambito, vars_ in sorted(requisitos.variables_extra.items()):
            lineas.append(f"  {'variables/' + ambito:<18} {vars_}")
    if requisitos.notas:
        lineas.append(f"  {'notas':<18} {requisitos.notas}")
    return "\n".join(lineas)


def validar_repo_codigo(cliente, requisitos: Requisitos) -> None:
    """Unica funcion de este modulo que toca la red; el cliente entra explicito.

    Que el usuario dicte un repo que no existe tiene que detectarse EN LA
    CONVERSACION, donde puede corregirlo, y no en el push -- alli ya se habrian
    generado cinco ficheros para un destino inexistente. Comprobar el tipo no
    basta: "demo-servicio-jaba" es un str perfectamente valido.
    """
    from ado.git import id_repo  # import local: esquema.py no depende de ado/ al cargarse

    if requisitos.repo_codigo is None:
        raise ValueError("no hay repo_codigo que validar todavia")
    id_repo(cliente, requisitos.repo_codigo)  # lanza ErrorAdo con la lista de los que si hay


if __name__ == "__main__":
    print("=== una conversacion de tres turnos, simulada ===\n")
    r = Requisitos()
    print(f"turno 0 (vacio)       bloqueantes={slots_que_faltan(r)}")

    r = fusionar(r, {"tecnologia": "java", "repo_codigo": None})
    print(f'turno 1 "algo en Java" bloqueantes={slots_que_faltan(r)}  -> el chat pregunta por el repo')

    r = fusionar(r, {"repo_codigo": "demo-servicio-java"})
    print(f"turno 2 el repo        bloqueantes={slots_que_faltan(r)}  completo={esta_completo(r)}")
    print(f"                       recomendados vacios={slots_recomendados_vacios(r)}")

    r = fusionar(r, {"version_lenguaje": "17", "contenedor": True, "version_app": "1.0.0"})
    print(f"turno 3 los detalles   recomendados vacios={slots_recomendados_vacios(r)}  <- cero adivinanzas\n")

    print("=== la propiedad que importa: fusionar NO borra ===")
    olvidadizo = fusionar(r, {"tecnologia": None, "repo_codigo": None, "version_app": "2.0.0"})
    print(f"  el modelo 'olvida' todo y solo manda version_app=2.0.0")
    print(f"  -> tecnologia sigue siendo {olvidadizo.tecnologia!r}, repo {olvidadizo.repo_codigo!r}, "
          f"version_app ahora {olvidadizo.version_app!r}")

    print("\n=== variables_extra se funde en profundidad ===")
    v = fusionar(r, {"variables_extra": {"pro": {"replicas": "8"}}})
    v = fusionar(v, {"variables_extra": {"pro": {"timeout": "30"}, "dev": {"replicas": "1"}}})
    print(f"  {v.variables_extra}   <- 'replicas' de pro sobrevivio al segundo turno")

    print("\n=== un campo o un valor inventado por el modelo es un error, no un dato ===")
    for parcial, que in (({"framework": "spring"}, "campo inventado"),
                         ({"tecnologia": "cobol"}, "valor fuera de la enumeracion")):
        try:
            fusionar(r, parcial)
            print(f"  !!! {que} paso sin error")
        except Exception as error:
            print(f"  {que:<28} -> {type(error).__name__}: {str(error).splitlines()[1].strip()}")

    print("\n=== lo que ve el humano al confirmar (T2.3) ===")
    print(descripcion(v))
