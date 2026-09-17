"""T3.2 (parte determinista) - elegir plantilla por reglas, sin modelo.

Dos ideas, y la segunda es la que hace que esto escale:

1. LAS REGLAS TRADUCEN REQUISITO -> ARQUETIPO, NO REQUISITO -> PLANTILLA.
   El arquetipo ("java-container") es un concepto del dominio; la plantilla
   ("ci-java-container v1.0.0") es un artefacto concreto que puede cambiar de
   nombre, versionarse o desdoblarse. Casar directamente contra la plantilla
   ataria el codigo al catalogo de hoy.

2. EL CATALOGO ES UN DATO, NO UNA LISTA EN EL CODIGO.
   Las candidatas salen de comparar el arquetipo contra el `applies_to` de las
   plantillas DESCUBIERTAS en Azure DevOps. Anadir una plantilla es un Pull
   Request al repo de plantillas; este fichero no se toca.

Tres estados, como en poc-agentes:
  DECIDIDO    -- exactamente una candidata. Cero llamadas al modelo.
  AMBIGUO     -- varias encajan. Hace falta desempatar.
  DESCONOCIDO -- ninguna encaja.

Y aqui hay un dato honesto que conviene decir en voz alta: con el catalogo
actual (tres arquetipos, una plantilla cada uno) y `tecnologia` siendo un slot
BLOQUEANTE de la conversacion, las reglas deciden SIEMPRE. El camino del LLM no
es decorado -- es lo que hace falta cuando el catalogo crezca y dos plantillas
compitan por el mismo arquetipo -- pero hoy no se dispara con datos reales.

Ejecuta con (desde la raiz del repo): .venv/bin/python -m seleccion.reglas
"""
from dataclasses import dataclass, field
from enum import StrEnum

from ado.catalogo import PlantillaDisponible
from requisitos.esquema import Requisitos


class EstadoSeleccion(StrEnum):
    DECIDIDO = "decidido"
    AMBIGUO = "ambiguo"
    DESCONOCIDO = "desconocido"
    # Lo produce solo el hibrido (seleccion/agente_hibrido.py): hubo respuesta
    # valida del modelo pero su confianza no llego al umbral. Vive aqui, con el
    # resto del vocabulario, para que quien lea el estado no tenga que importar
    # el modulo que habla con el LLM.
    REQUIERE_REVISION_HUMANA = "requiere_revision_humana"


@dataclass(frozen=True)
class Seleccion:
    estado: EstadoSeleccion
    plantilla: PlantillaDisponible | None = None
    candidatas: list[PlantillaDisponible] = field(default_factory=list)
    arquetipo: str | None = None
    origen: str = "reglas"
    confianza: float = 1.0
    razonamiento: str = ""

    def resumen(self) -> str:
        elegida = self.plantilla.id if self.plantilla else "-"
        ids = ", ".join(p.id for p in self.candidatas) or "ninguna"
        return (f"{self.estado:<12} arquetipo={self.arquetipo or '-':<16} "
                f"elegida={elegida:<20} candidatas=[{ids}]  origen={self.origen} "
                f"confianza={self.confianza}")


# La unica tabla de reglas. Cada entrada: (arquetipo, condicion sobre Requisitos).
# Se mira `tecnologia` y no el nombre del repo ni las notas: derivar la tecnologia
# del nombre del repositorio es justo la deduccion de mas que se vio al modelo
# hacer en pruebas ("demo-servicio-java" -> java, aunque el usuario dijera dotnet).
REGLAS: list[tuple[str, object]] = [
    ("java-container", lambda r: r.tecnologia == "java"),
    ("dotnet-service", lambda r: r.tecnologia == "dotnet"),
    ("node-container", lambda r: r.tecnologia == "node"),
]

ARQUETIPOS_CONOCIDOS = [arquetipo for arquetipo, _ in REGLAS]


def arquetipo_de(requisitos: Requisitos) -> str | None:
    """Primer arquetipo cuya condicion se cumple. None si ninguna."""
    return next((a for a, condicion in REGLAS if condicion(requisitos)), None)


def seleccionar_por_reglas(requisitos: Requisitos,
                           plantillas: list[PlantillaDisponible]) -> Seleccion:
    arquetipo = arquetipo_de(requisitos)
    if arquetipo is None:
        return Seleccion(EstadoSeleccion.DESCONOCIDO, candidatas=list(plantillas), confianza=0.0)

    candidatas = [p for p in plantillas if arquetipo in p.applies_to]

    if len(candidatas) == 1:
        return Seleccion(
            EstadoSeleccion.DECIDIDO, plantilla=candidatas[0],
            candidatas=candidatas, arquetipo=arquetipo,
            razonamiento=f"La tecnologia '{requisitos.tecnologia}' implica el arquetipo "
                         f"'{arquetipo}', y solo '{candidatas[0].id}' lo declara en applies_to.",
        )
    if len(candidatas) > 1:
        # Varias plantillas compiten. Las reglas NO eligen a boleo: ceden.
        return Seleccion(EstadoSeleccion.AMBIGUO, candidatas=candidatas,
                         arquetipo=arquetipo, confianza=0.0)

    return Seleccion(EstadoSeleccion.DESCONOCIDO, candidatas=list(plantillas),
                     arquetipo=arquetipo, confianza=0.0)


if __name__ == "__main__":
    from dotenv import load_dotenv

    from ado.catalogo import descubrir_plantillas
    from ado.cliente import ClienteAdo

    load_dotenv()
    with ClienteAdo.desde_entorno() as cliente:
        plantillas = descubrir_plantillas(cliente)
        print(f"catalogo descubierto en ADO: {[p.id for p in plantillas]}\n")

        print("=== las tres tecnologias, por reglas (cero llamadas al modelo) ===")
        for tecnologia in ("java", "dotnet", "node"):
            req = Requisitos(tecnologia=tecnologia, repo_codigo="demo-servicio-java")
            print(f"  {tecnologia:<7} {seleccionar_por_reglas(req, plantillas).resumen()}")

        print("\n=== sin tecnologia -> DESCONOCIDO (no puede pasar desde el chat: es slot bloqueante) ===")
        print(f"          {seleccionar_por_reglas(Requisitos(), plantillas).resumen()}")

        print("\n=== catalogo fabricado con DOS plantillas para el mismo arquetipo -> AMBIGUO ===")
        gemela = plantillas[0].model_copy(update={"id": "ci-java-minimo", "carpeta": "ci-java-minimo",
                                                  "applies_to": ["java-container"]})
        java = next(p for p in plantillas if "java-container" in p.applies_to)
        req = Requisitos(tecnologia="java", repo_codigo="demo-servicio-java")
        print(f"          {seleccionar_por_reglas(req, [java, gemela]).resumen()}")
        print("\n  Las reglas NO eligen a boleo cuando hay empate: ceden, y ahi entra el LLM (agente_hibrido).")
