"""T3.1 - el flujo como maquina de estados declarada con Microsoft Agent Framework.

Compara con un bucle a mano: alli el "siguiente paso" lo decide un if/else que
hay que leer entero para saber por donde puede ir la cosa. Aqui los nodos y las
transiciones se DECLARAN, y el framework mueve el mensaje y produce la traza.

Lo que hace util este fichero, y que no existia en poc-agentes:

1. LA PAUSA ES REAL, NO SIMULADA.
   `ctx.request_info(datos, tipo)` SUSPENDE el workflow: la ejecucion termina en
   estado IDLE_WITH_PENDING_REQUESTS sin haber escrito nada. Fuera se leen las
   peticiones con `result.get_request_info_events()`, se habla con el humano, y
   se reanuda con `workflow.run(responses={request_id: valor})`. La respuesta
   entra por el metodo marcado `@response_handler`.
   (Verificado contra agent-framework-core 1.18.0. `RequestInfoExecutor`, que es
   lo que dice la documentacion antigua, NO existe en esta version.)

2. EL MENSAJE ES EL ESTADO.
   No hay variables globales entre nodos: un `Contexto` viaja por el grafo y cada
   nodo devuelve una copia con lo suyo anadido, incluida su marca en `traza`. Asi
   la secuencia de estados recorridos es un dato del resultado, no un efecto
   secundario de unos prints.

3. EL REPARTO SE MIDE, NO SE AFIRMA.
   Al terminar se cruza la traza con la tabla de `orquestacion/estados.py` y con
   el contador de `llm/cliente.py` (que envuelve al cliente, asi que cuenta a
   cualquiera). El resultado es "esta ejecucion recorrio N estados e hizo M
   llamadas al modelo", que es un dato, no una promesa.

ALCANCE DE HOY: el flujo arranca con unos `Requisitos` YA CONFIRMADOS -- la
conversacion multiturno vive fuera, en requisitos/agente_recolector.py, y se
conectara en el Bloque 5. Los nodos de seleccion y parametros son provisionales
hasta T3.2/T3.3; estan declarados y marcados como tales, no ocultos.

Ejecuta con (desde la raiz del repo): .venv/bin/python -m orquestacion.flujo
"""
import asyncio

from agent_framework import (
    Executor,
    InMemoryCheckpointStorage,
    WorkflowBuilder,
    WorkflowContext,
    executor,
    handler,
    response_handler,
)
from pydantic import BaseModel, ConfigDict

from ado.catalogo import PlantillaDisponible, descubrir_plantillas
from ado.cliente import ClienteAdo
from ado.destinos import Inventario, inventario
from llm.cliente import llamadas_al_modelo, reiniciar_contador
from orquestacion.estados import PASOS, Estado, Naturaleza
from requisitos.esquema import Requisitos

# El cliente de ADO no puede viajar dentro del mensaje: el Contexto se serializa
# para el checkpointing y un cliente HTTP no es serializable. Se deja aqui, lo
# fija el runner antes de arrancar, y los nodos lo leen. Es la unica pieza de
# estado global del flujo, y esta acotada a proposito.
_cliente_ado: ClienteAdo | None = None


def _ado() -> ClienteAdo:
    if _cliente_ado is None:
        raise RuntimeError("el runner no ha fijado el cliente de ADO")
    return _cliente_ado


class Contexto(BaseModel):
    """Lo que viaja por el grafo. Cada nodo devuelve una copia con lo suyo puesto."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    requisitos: Requisitos
    traza: list[Estado] = []
    plantillas: list[PlantillaDisponible] = []
    plantilla: PlantillaDisponible | None = None
    inventario: Inventario | None = None
    confirmado: bool | None = None

    def paso(self, estado: Estado, **cambios) -> "Contexto":
        """Marca el estado recorrido y aplica los cambios. Devuelve uno NUEVO:
        que los nodos no muten el mensaje que reciben es lo que hace que la traza
        sea fiable."""
        return self.model_copy(update={"traza": [*self.traza, estado], **cambios})


@executor(id=Estado.DESCUBRIENDO_CATALOGO)
async def descubrir_catalogo(ctx_flujo: Contexto, ctx: WorkflowContext[Contexto]) -> None:
    """DETERMINISTA. Lee las plantillas de plantillas-ci, ancladas al tag."""
    plantillas = descubrir_plantillas(_ado())
    await ctx.send_message(ctx_flujo.paso(Estado.DESCUBRIENDO_CATALOGO, plantillas=plantillas))


# Arquetipo que implica cada tecnologia. Provisional: T3.2 lo sustituye por la
# tabla de reglas real mas el LLM de respaldo cuando 0 o >1 plantillas encajen.
ARQUETIPO_POR_TECNOLOGIA = {"java": "java-container", "dotnet": "dotnet-service", "node": "node-container"}


@executor(id=Estado.SELECCIONANDO_PLANTILLA)
async def seleccionar_plantilla(ctx_flujo: Contexto, ctx: WorkflowContext[Contexto]) -> None:
    """HIBRIDO (hoy solo la mitad determinista; el LLM de respaldo es T3.2)."""
    arquetipo = ARQUETIPO_POR_TECNOLOGIA.get(ctx_flujo.requisitos.tecnologia or "")
    candidatas = [p for p in ctx_flujo.plantillas if arquetipo in p.applies_to]
    elegida = candidatas[0] if len(candidatas) == 1 else None
    await ctx.send_message(ctx_flujo.paso(Estado.SELECCIONANDO_PLANTILLA, plantilla=elegida))


@executor(id=Estado.PLANIFICANDO_CAMBIOS)
async def planificar_cambios(ctx_flujo: Contexto, ctx: WorkflowContext[Contexto]) -> None:
    """DETERMINISTA. Que ficheros existen ya: de ahi sale el add vs edit."""
    inv = inventario(_ado(), ctx_flujo.requisitos.repo_codigo)
    await ctx.send_message(ctx_flujo.paso(Estado.PLANIFICANDO_CAMBIOS, inventario=inv))


class ConfirmarPush(Executor):
    """HUMANO. La puerta: aqui el workflow se SUSPENDE de verdad.

    No es un `input()` disfrazado. `request_info` termina la ejecucion dejandola
    en IDLE_WITH_PENDING_REQUESTS; quien la reanuda puede ser otro proceso, otro
    dia, leyendo el checkpoint. Esa propiedad es la que hace que "nada se escribe
    sin un si" sea una garantia estructural y no una convencion.
    """

    @handler
    async def preguntar(self, ctx_flujo: Contexto, ctx: WorkflowContext[Contexto]) -> None:
        inv = ctx_flujo.inventario
        plan = "\n".join(f"      {linea}" for linea in inv.resumen()) if inv else "      (sin plan)"
        plantilla = ctx_flujo.plantilla.id if ctx_flujo.plantilla else "NINGUNA (revision humana)"
        # El Contexto NO viaja dentro de la peticion ni vuelve en la respuesta:
        # lo que sale al exterior es solo el texto y lo que entra es un bool. Se
        # guarda en el estado del executor para recuperarlo al reanudar.
        ctx.set_state("contexto", ctx_flujo.model_dump())
        await ctx.request_info(
            f"Plantilla: {plantilla}\n{plan}\n    ¿Escribo esto en Azure DevOps?", bool
        )

    @response_handler
    async def recibir(self, peticion: str, respuesta: bool, ctx: WorkflowContext[None, Contexto]) -> None:
        # Se reconstruye desde el estado que guardo `preguntar`. Para T3.1 basta
        # con registrar el veredicto: escribir de verdad es el Bloque 4.
        contexto = Contexto(**ctx.get_state("contexto"))
        final = Estado.COMPLETADO if respuesta else Estado.CANCELADO
        await ctx.yield_output(contexto.paso(Estado.CONFIRMANDO_PUSH).paso(final, confirmado=respuesta))


def construir_workflow() -> tuple:
    confirmar = ConfirmarPush(id=Estado.CONFIRMANDO_PUSH)
    workflow = (
        WorkflowBuilder(start_executor=descubrir_catalogo)
        .add_edge(descubrir_catalogo, seleccionar_plantilla)
        .add_edge(seleccionar_plantilla, planificar_cambios)
        .add_edge(planificar_cambios, confirmar)
        .build()
    )
    return workflow, confirmar


def informe(traza: list[Estado], llamadas: int) -> str:
    """Cruza la traza con la tabla de estados. El dato, no la promesa."""
    lineas = ["  estados recorridos:"]
    conteo = dict.fromkeys(Naturaleza, 0)
    for estado in traza:
        paso = PASOS[estado]
        conteo[paso.naturaleza] += 1
        lineas.append(f"    {paso.naturaleza:<13} {estado}")
    reparto = "  ".join(f"{n}={c}" for n, c in conteo.items() if c)
    lineas.append(f"\n  reparto de esta ejecucion: {reparto}")
    lineas.append(f"  llamadas reales al modelo: {llamadas}")
    return "\n".join(lineas)


async def ejecutar(requisitos: Requisitos, responder_humano) -> Contexto | None:
    """Arranca el flujo, lo deja pausar en la puerta humana y lo reanuda.

    `responder_humano(texto) -> bool` se inyecta para poder guionizar la prueba;
    en el chat real sera una pregunta por consola.
    """
    global _cliente_ado
    reiniciar_contador()
    workflow, _ = construir_workflow()
    almacen = InMemoryCheckpointStorage()

    with ClienteAdo.desde_entorno() as cliente:
        _cliente_ado = cliente

        resultado = await workflow.run(Contexto(requisitos=requisitos), checkpoint_storage=almacen)
        print(f"  [workflow] estado tras la primera ejecucion: {resultado.get_final_state()}")

        peticiones = resultado.get_request_info_events()
        if not peticiones:
            print("  [workflow] no pidio nada al humano (no deberia pasar en este flujo)")
            return None

        respuestas = {}
        for peticion in peticiones:
            print(f"\n  [humano] se le pregunta:\n    {peticion.data}")
            respuestas[peticion.request_id] = responder_humano(peticion.data)
            print(f"  [humano] responde: {respuestas[peticion.request_id]}")

        resultado = await workflow.run(responses=respuestas, checkpoint_storage=almacen)
        print(f"\n  [workflow] estado tras reanudar: {resultado.get_final_state()}")
        salidas = resultado.get_outputs()
        return salidas[0] if salidas else None


async def main() -> None:
    from dotenv import load_dotenv

    load_dotenv()

    requisitos = Requisitos(
        tecnologia="java", repo_codigo="demo-servicio-java",
        version_lenguaje="17", contenedor=True, version_app="1.0.0",
    )
    print("=== requisitos de entrada (ya confirmados; la conversacion vive fuera) ===")
    print(f"  {requisitos.model_dump(exclude_none=True)}\n")

    for veredicto, etiqueta in ((True, "el humano dice QUE SI"), (False, "el humano dice QUE NO")):
        print(f"\n=== {etiqueta} ===")
        final = await ejecutar(requisitos, lambda _texto: veredicto)
        if final is None:
            print("  (sin salida)")
            continue
        print(f"\n{informe(final.traza, llamadas_al_modelo())}")


if __name__ == "__main__":
    asyncio.run(main())
