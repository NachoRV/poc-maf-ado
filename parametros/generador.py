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
    requisito con valor que la plantilla NO admite -> se PREGUNTA (2026-09-18)

Esa tercera linea salio de una demo en real: alguien pidio "node 26", la senal se
supo traducir, el valor se derivo sin mirar y la ejecucion reviento con un
ValidationError crudo del schema. Derivar un valor no es lo mismo que aceptarlo:
si la plantilla declara `allowed_values` o `pattern`, el valor derivado se
comprueba contra ellos ANTES de darlo por bueno, y si no encaja se convierte en
una pregunta con las opciones a la vista. El sistema sigue sin inventar nada --
tampoco corrige por su cuenta un 26 a un 22.

Eso hace que el modulo sea completo sin conocer el catalogo entero. Anadir una
plantilla con un parametro nuevo no rompe nada: como mucho, genera una pregunta
mas. Y si la senal se vuelve derivable, se anade una linea a la tabla y la
pregunta desaparece.

Ejecuta con (desde la raiz del repo): .venv/bin/python -m parametros.generador
"""
import re
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
    # El valor que se intento y la plantilla no admite. Vacio cuando el
    # parametro simplemente falta. Cambia la pregunta: no es lo mismo "no lo se"
    # que "lo que me diste no sirve, y por esto".
    rechazado: str = ""

    def pregunta(self) -> str:
        opciones = f" Opciones: {self.valores_admitidos}." if self.valores_admitidos else ""
        if self.tipo == "boolean":
            opciones = " Responde si o no."
        if self.rechazado:
            # El motivo ya dice que se admite; repetir la lista sobraria. La
            # excepcion es el booleano, donde el motivo no dice como contestar.
            como = " Responde si o no." if self.tipo == "boolean" else ""
            return f"'{self.rechazado}' no vale para '{self.nombre}': {self.motivo}.{como}".strip()
        return f"Falta el parametro '{self.nombre}'.{opciones} {self.descripcion.strip()}".strip()


@dataclass
class Derivacion:
    valores: dict[str, Any] = field(default_factory=dict)
    pendientes: list[Pendiente] = field(default_factory=list)
    origen: dict[str, str] = field(default_factory=dict)  # nombre -> de donde salio

    @property
    def completa(self) -> bool:
        return not self.pendientes


def restriccion_incumplida(valor: Any, parametro: dict) -> str:
    """Por que la plantilla NO admite este valor. Cadena vacia si lo admite.

    Mira lo que el manifest declara, no el schema: asi el valor se rechaza en la
    conversacion (donde se puede preguntar otra vez) y no al final, donde lo
    unico que cabe es reventar.
    """
    admitidos = parametro.get("allowed_values")
    if admitidos is not None and valor not in admitidos:
        return f"la plantilla solo admite {admitidos}"
    patron = parametro.get("pattern")
    if patron and not re.fullmatch(patron, str(valor)):
        return f"la plantilla exige el formato {patron}"
    return ""


def derivar(requisitos: Requisitos, plantilla: PlantillaDisponible) -> Derivacion:
    """Rellena lo que los requisitos ya fijan Y la plantilla admite; lo demas
    queda como pendiente."""
    derivacion = Derivacion()

    for parametro in plantilla.parameters:
        nombre = parametro["name"]
        senal = parametro.get("derived_from")
        campo = SENAL_A_REQUISITO.get(senal or "")
        valor = getattr(requisitos, campo, None) if campo else None
        rechazado = ""

        if valor is not None:
            problema = restriccion_incumplida(valor, parametro)
            if not problema:
                derivacion.valores[nombre] = valor
                derivacion.origen[nombre] = f"requisitos.{campo}"
                continue
            # Se supo derivar, pero no sirve. No se corrige por nuestra cuenta
            # (un 26 no se convierte en 22 sin permiso): se pregunta.
            motivo, rechazado = problema, str(valor)
        elif campo is None:
            motivo = f"la senal '{senal}' del manifest no se sabe traducir a un requisito"
        else:
            motivo = f"requisitos.{campo} esta sin definir"

        derivacion.pendientes.append(Pendiente(
            nombre=nombre,
            tipo=parametro.get("type", "string"),
            valores_admitidos=parametro.get("allowed_values"),
            descripcion=parametro.get("description", ""),
            motivo=motivo,
            rechazado=rechazado,
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


class ErrorDeParametros(ValueError):
    """Un valor que el schema no acepta, SABIENDO CUAL.

    Que lleve el nombre del parametro es lo que separa "el flujo se cae" de "el
    flujo vuelve a preguntar". Sin ese dato, quien recibe el error solo puede
    abortar; con el, puede rehacer la pregunta exacta.
    """

    def __init__(self, mensaje: str, parametro: str | None, detalle: str = ""):
        super().__init__(mensaje)
        self.parametro = parametro
        # El motivo a secas, sin el prefijo: es lo que se le ensena a una
        # persona cuando se le vuelve a preguntar.
        self.detalle = detalle or mensaje


def validar(valores: dict[str, Any], schema: dict) -> None:
    """Contra el parameters.schema.json REAL de la plantilla, no uno inventado.

    Incluye `additionalProperties: false`, asi que un parametro de mas se
    rechaza igual que un valor fuera de un enum.

    Es la ULTIMA red, no la primera: lo que el manifest declara ya se comprueba
    en derivar(). Aqui cae lo que solo vive en el schema.
    """
    try:
        validate(instance=valores, schema=schema)
    except ValidationError as error:
        # error.path apunta al campo culpable ('nodeVersion'); viene vacia
        # cuando el fallo es del objeto entero (un required que falta, un
        # parametro de mas), y entonces no hay nada que repreguntar.
        culpable = next((str(p) for p in error.path), None)
        raise ErrorDeParametros(
            f"los parametros no cumplen el schema de la plantilla: {error.message}",
            culpable, error.message,
        ) from error


def repreguntar(plantilla: PlantillaDisponible, error: ErrorDeParametros,
                valores: dict[str, Any]) -> Pendiente | None:
    """El fallo de validacion -> la siguiente pregunta. None si no se puede.

    None significa que el error no es de un valor concreto (falta un parametro
    entero, o sobra uno): eso es un fallo del sistema, no del usuario, y
    preguntarlo no lo arreglaria. Debe propagarse.
    """
    if error.parametro is None:
        return None
    parametro = next((p for p in plantilla.parameters if p["name"] == error.parametro), None)
    if parametro is None:
        return None
    valor = valores.get(error.parametro)
    # Se prefiere el motivo del manifest al del schema: dice lo mismo, en el
    # idioma del resto de la conversacion. El del schema queda de reserva para
    # lo que solo el schema sabe.
    motivo = restriccion_incumplida(valor, parametro) if valor is not None else ""
    return Pendiente(
        nombre=error.parametro,
        tipo=parametro.get("type", "string"),
        valores_admitidos=parametro.get("allowed_values"),
        descripcion=parametro.get("description", ""),
        motivo=motivo or error.detalle,
        rechazado="" if valor is None else str(valor),
    )


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
        except ErrorDeParametros as error:
            print(f"  {error}")
            print(f"  y se sabe de QUIEN es la culpa: {error.parametro}")

        print("\n=== ...y rechazarlo NO es reventar: es otra pregunta ===")
        malos = {**d.valores, "javaVersion": "25"}
        try:
            validar(malos, schema)
        except ErrorDeParametros as error:
            print(f"  -> {repreguntar(java, error, malos).pregunta()}")

        print("\n=== un requisito que la plantilla no admite tampoco se cuela ===")
        # El caso que rompio una demo en real: el usuario pidio una version que
        # el manifest no soporta. Antes se derivaba a ciegas y reventaba al final.
        imposible = Requisitos(tecnologia="java", repo_codigo="demo-servicio-java",
                               version_lenguaje="25", contenedor=True, version_app="1.0.0")
        d = derivar(imposible, java)
        print(f"  valores derivados: {d.valores}   <- javaVersion NO esta")
        for pendiente in d.pendientes:
            print(f"  -> {pendiente.pregunta()}")

        print("\n=== falta un parametro entero: eso NO se repregunta, se propaga ===")
        try:
            validar({"isDocker": True}, schema)
        except ErrorDeParametros as error:
            print(f"  parametro culpable: {error.parametro}  ->  repreguntar da "
                  f"{repreguntar(java, error, {})}")
            print("  (es un fallo del sistema, no del usuario: preguntarlo no lo arregla)")
