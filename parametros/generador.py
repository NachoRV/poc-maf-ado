"""T3.3 - los parametros del `extends`. CERO LLM (decision del usuario, 2026-09-18).

El usuario eligio la opcion B: nada se infiere. Un parametro que el schema exige
y que no se puede derivar de los requisitos NO lo rellena un modelo -- se
pregunta. Y a diferencia de las variables de entorno (T3.4), aqui el hueco NO es
una opcion: el `parameters.schema.json` los declara `required`, asi que un hueco
deja el pipeline invalido.

Con esto el sistema entero se queda con UN SOLO agente de verdad: la
conversacion. Todo lo demas es codigo.

EL PUENTE CON EL CATALOGO, que es la parte interesante:

Los manifests declaran `derived_from` apuntando a senales del fingerprint de
poc-agentes (`has_dockerfile`, `pom_java_version`...) que en este sistema ya no
existen: alli se leian del repo, aqui salen de una conversacion. En vez de
hardcodear "javaVersion sale de version_lenguaje" -- que ataria el codigo al
catalogo de hoy, justo lo que se evito en T3.2 -- se traduce SENAL -> CAMPO DE
REQUISITOS, y lo que no se sepa traducir NO es un error:

    senal conocida + requisito con valor  -> se deriva, sin preguntar
    senal desconocida, o requisito vacio  -> se PREGUNTA

Eso hace que el modulo sea completo sin conocer el catalogo entero. Anadir una
plantilla con un parametro nuevo no rompe nada: como mucho, genera una pregunta
mas. Y si la senal se vuelve derivable, se anade una linea a la tabla y la
pregunta desaparece.

Ejecuta con (desde la raiz del repo): .venv/bin/python -m parametros.generador
"""
from dataclasses import dataclass, field
from typing import Any

from jsonschema import ValidationError, validate

from ado.catalogo import PlantillaDisponible
from requisitos.confirmacion import Veredicto, interpretar
from requisitos.esquema import Requisitos

# Traduccion de las senales que declaran los manifests a campos de `Requisitos`.
# Es una tabla de PUENTE, no el catalogo: lo que no este aqui se pregunta, no
# revienta. Por eso puede quedarse corta sin romper nada.
SENAL_A_REQUISITO: dict[str, str] = {
    "has_dockerfile": "contenedor",
    "pom_java_version": "version_lenguaje",
    "csproj_target_framework": "version_lenguaje",
    "package_json_engines_node": "version_lenguaje",
    "pom_version": "version_app",
    "csproj_version": "version_app",
    "package_json_version": "version_app",
}


@dataclass(frozen=True)
class Pendiente:
    """Un parametro que hay que preguntarle a una persona."""

    nombre: str
    tipo: str
    valores_admitidos: list[Any] | None = None
    descripcion: str = ""
    motivo: str = ""

    def pregunta(self) -> str:
        opciones = f" Opciones: {self.valores_admitidos}." if self.valores_admitidos else ""
        if self.tipo == "boolean":
            opciones = " Responde si o no."
        return f"Falta el parametro '{self.nombre}'.{opciones} {self.descripcion.strip()}".strip()


@dataclass
class Derivacion:
    valores: dict[str, Any] = field(default_factory=dict)
    pendientes: list[Pendiente] = field(default_factory=list)
    origen: dict[str, str] = field(default_factory=dict)  # nombre -> de donde salio

    @property
    def completa(self) -> bool:
        return not self.pendientes


def derivar(requisitos: Requisitos, plantilla: PlantillaDisponible) -> Derivacion:
    """Rellena lo que los requisitos ya fijan; lo demas queda como pendiente."""
    derivacion = Derivacion()

    for parametro in plantilla.parameters:
        nombre = parametro["name"]
        senal = parametro.get("derived_from")
        campo = SENAL_A_REQUISITO.get(senal or "")
        valor = getattr(requisitos, campo, None) if campo else None

        if valor is not None:
            derivacion.valores[nombre] = valor
            derivacion.origen[nombre] = f"requisitos.{campo}"
            continue

        motivo = (f"la senal '{senal}' del manifest no se sabe traducir a un requisito"
                  if campo is None else f"requisitos.{campo} esta sin definir")
        derivacion.pendientes.append(Pendiente(
            nombre=nombre,
            tipo=parametro.get("type", "string"),
            valores_admitidos=parametro.get("allowed_values"),
            descripcion=parametro.get("description", ""),
            motivo=motivo,
        ))

    return derivacion


def convertir(texto: str, pendiente: Pendiente) -> Any:
    """Lo que escribe una persona -> el tipo que exige el schema.

    Para los booleanos se reutiliza el clasificador de requisitos/confirmacion.py:
    ya sabe distinguir un "si" de un "no" sin gastar una llamada al modelo, y
    tener dos formas distintas de entender "si" en el mismo sistema seria pedir
    que un dia difieran.
    """
    texto = texto.strip()
    if pendiente.tipo == "boolean":
        return interpretar(texto) is Veredicto.CONFIRMA
    if pendiente.tipo in ("number", "integer"):
        return int(texto) if pendiente.tipo == "integer" else float(texto)
    return texto


def validar(valores: dict[str, Any], schema: dict) -> None:
    """Contra el parameters.schema.json REAL de la plantilla, no uno inventado.

    Incluye `additionalProperties: false`, asi que un parametro de mas se
    rechaza igual que un valor fuera de un enum.
    """
    try:
        validate(instance=valores, schema=schema)
    except ValidationError as error:
        raise ValueError(f"los parametros no cumplen el schema de la plantilla: {error.message}") from error


if __name__ == "__main__":
    from dotenv import load_dotenv

    from ado.catalogo import descubrir_plantillas, leer_schema
    from ado.cliente import ClienteAdo
    from llm.cliente import llamadas_al_modelo

    load_dotenv()
    with ClienteAdo.desde_entorno() as cliente:
        plantillas = descubrir_plantillas(cliente)
        java = next(p for p in plantillas if p.id == "ci-java-container")
        schema = leer_schema(cliente, java)

        print("=== requisitos COMPLETOS: se deriva todo, no se pregunta nada ===")
        completos = Requisitos(tecnologia="java", repo_codigo="demo-servicio-java",
                               version_lenguaje="17", contenedor=True, version_app="1.0.0")
        d = derivar(completos, java)
        print(f"  valores   : {d.valores}")
        print(f"  origen    : {d.origen}")
        print(f"  pendientes: {[p.nombre for p in d.pendientes]}")
        validar(d.valores, schema)
        print(f"  validan contra el schema real: si")
        print(f"  llamadas al modelo: {llamadas_al_modelo()}   <- debe ser 0\n")

        print("=== requisitos A MEDIAS: lo que falta se PREGUNTA, no se inventa ===")
        medias = Requisitos(tecnologia="java", repo_codigo="demo-servicio-java", contenedor=True)
        d = derivar(medias, java)
        print(f"  valores derivados: {d.valores}")
        for p in d.pendientes:
            print(f"  PENDIENTE {p.nombre:<12} ({p.motivo})")
            print(f"     -> {p.pregunta()}")

        print("\n=== se contestan y se validan ===")
        respuestas = {"javaVersion": "17", "appVersion": "2.3.1"}
        for p in d.pendientes:
            d.valores[p.nombre] = convertir(respuestas[p.nombre], p)
            d.origen[p.nombre] = "respuesta del usuario"
        print(f"  valores: {d.valores}")
        print(f"  origen : {d.origen}")
        validar(d.valores, schema)
        print("  validan contra el schema real: si")

        print("\n=== un valor fuera del enum se rechaza (no se cuela) ===")
        try:
            validar({**d.valores, "javaVersion": "25"}, schema)
            print("  ERROR: deberia haber fallado")
        except ValueError as error:
            print(f"  {error}")
