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

Ejecuta con (desde la raiz del repo): .venv/bin/python -m orquestacion.agente_flujo
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

from ado.catalogo import PlantillaDisponible, descubrir_plantillas, leer_schema
from ado.cliente import ClienteAdo
from ado.destinos import Inventario, inventario
from parametros.generador import Pendiente, convertir, derivar, validar
from parametros.variables import generar, huecos
from llm.cliente import llamadas_al_modelo, reiniciar_contador
from orquestacion.estados import PASOS, Estado, Naturaleza
from seleccion.agente_hibrido import seleccionar
from seleccion.reglas import EstadoSeleccion
from requisitos.agente_recolector import YA_ESTA, Sesion
from requisitos.confirmacion import Veredicto, interpretar
from requisitos.esquema import Requisitos, descripcion, slots_recomendados_vacios

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
    parametros: dict = {}
    origen_parametros: dict[str, str] = {}
    ficheros_variables: dict[str, str] = {}
    confirmado: bool | None = None

    def paso(self, estado: Estado, **cambios) -> "Contexto":
        """Marca el estado recorrido y aplica los cambios. Devuelve uno NUEVO:
        que los nodos no muten el mensaje que reciben es lo que hace que la traza
        sea fiable."""
        return self.model_copy(update={"traza": [*self.traza, estado], **cambios})


FASE_RECOGIENDO = "recogiendo"
FASE_CONFIRMANDO = "confirmando"


class RecogerRequisitos(Executor):
    """LLM + HUMANO. La conversacion, conducida DESDE DENTRO del flujo.

    Aqui esta la diferencia tecnica que importa frente a `conversar()` de
    requisitos/agente_recolector.py, que hace lo mismo fuera:

        fuera  -> un BUCLE. Llama a input(), no puede suspenderse, y por tanto
                  solo sirve en un proceso que este vivo todo el rato.
        dentro -> una MAQUINA DE ESTADOS. Cada turno termina con request_info,
                  el workflow se suspende y el estado sobrevive en el checkpoint.
                  Quien conteste el turno 4 puede ser otro proceso.

    La logica de negocio NO se duplica: las dos usan `Sesion.responder()`. Lo
    unico que cambia es el conductor. Es el mismo patron que en poc-agentes,
    donde el workflow de MAF y el pipeline a mano eran dos interfaces sobre la
    misma logica.

    El estado del dialogo (requisitos a medias, historial, fase) vive en
    ctx.set_state, no en atributos del executor: los atributos no sobreviven a
    una reanudacion desde checkpoint.
    """

    @handler
    async def empezar(self, arranque: Contexto, ctx: WorkflowContext[Contexto]) -> None:
        self._guardar(ctx, Sesion(requisitos=arranque.requisitos))
        ctx.set_state("fase", FASE_RECOGIENDO)
        await ctx.request_info("¿Que pipeline necesitas?", str)

    @response_handler
    async def turno(self, peticion: str, respuesta: str, ctx: WorkflowContext[Contexto]) -> None:
        sesion = Sesion(
            requisitos=Requisitos(**ctx.get_state("requisitos")),
            historial=list(ctx.get_state("historial")),
            cliente_ado=_ado(),
        )

        if ctx.get_state("fase") == FASE_CONFIRMANDO:
            await self._resolver_confirmacion(sesion, respuesta, ctx)
            return

        # -- fase de recogida --
        if sesion.completo and respuesta.strip().lower() in YA_ESTA:
            await self._pedir_confirmacion(sesion, ctx)
            return

        pregunta = sesion.responder(respuesta)
        self._guardar(ctx, sesion)

        if pregunta is None:
            await self._pedir_confirmacion(sesion, ctx)
        elif sesion.completo:
            await ctx.request_info(f"{pregunta}\n    (o escribe 'listo' si ya esta bien asi)", str)
        else:
            await ctx.request_info(pregunta, str)

    async def _resolver_confirmacion(self, sesion: Sesion, respuesta: str, ctx) -> None:
        veredicto = interpretar(respuesta)  # determinista: un "si" no cuesta una llamada

        if veredicto is Veredicto.CONFIRMA:
            # Los dos estados de conversacion se marcan aqui, al salir: la traza
            # tiene que reflejar por donde paso de verdad la ejecucion.
            await ctx.send_message(
                Contexto(requisitos=sesion.requisitos).paso(Estado.RECOGIENDO_REQUISITOS)
                .paso(Estado.CONFIRMANDO_REQUISITOS)
            )
            return

        if veredicto is Veredicto.RECHAZA:
            ctx.set_state("fase", FASE_RECOGIENDO)
            await ctx.request_info("¿Que quieres cambiar?", str)
            return

        # CORRIGE: lleva informacion, asi que es un turno normal. Y se vuelve a
        # confirmar -- una correccion nunca da los datos por buenos.
        sesion.responder(respuesta)
        self._guardar(ctx, sesion)
        await self._pedir_confirmacion(sesion, ctx)

    async def _pedir_confirmacion(self, sesion: Sesion, ctx) -> None:
        ctx.set_state("fase", FASE_CONFIRMANDO)
        vacios = slots_recomendados_vacios(sesion.requisitos)
        # Ya NO dice "lo decidira el modelo": desde la opcion B (T3.3) nada se
        # infiere. Lo que falte y la plantilla exija se preguntara despues.
        aviso = (f"\n    Ojo: {', '.join(vacios)} sin definir. Nada se inventa: si la "
                 "plantilla elegida lo necesita, te lo preguntare." if vacios else "")
        await ctx.request_info(
            f"Esto es lo que he entendido:\n{descripcion(sesion.requisitos)}{aviso}"
            "\n    ¿Lo confirmas? (si / no / dime que cambiar)",
            str,
        )

    @staticmethod
    def _guardar(ctx, sesion: Sesion) -> None:
        ctx.set_state("requisitos", sesion.requisitos.model_dump())
        ctx.set_state("historial", sesion.historial)


@executor(id=Estado.DESCUBRIENDO_CATALOGO)
async def descubrir_catalogo(ctx_flujo: Contexto, ctx: WorkflowContext[Contexto]) -> None:
    """DETERMINISTA. Lee las plantillas de plantillas-ci, ancladas al tag."""
    plantillas = descubrir_plantillas(_ado())
    await ctx.send_message(ctx_flujo.paso(Estado.DESCUBRIENDO_CATALOGO, plantillas=plantillas))


@executor(id=Estado.SELECCIONANDO_PLANTILLA)
async def seleccionar_plantilla(ctx_flujo: Contexto, ctx: WorkflowContext[Contexto]) -> None:
    """HIBRIDO (T3.2). Reglas primero; el LLM SOLO si hay empate o nada encaja.

    Ya no es el mapeo provisional de T3.1: delega en seleccion/agente_hibrido.py,
    que con el catalogo real resuelve por reglas y no gasta ni una llamada.
    """
    resultado = seleccionar(ctx_flujo.requisitos, ctx_flujo.plantillas)
    print(f"  [seleccion] {resultado.resumen()}")
    if resultado.razonamiento:
        print(f"  [seleccion] {resultado.razonamiento}")
    await ctx.send_message(
        ctx_flujo.paso(Estado.SELECCIONANDO_PLANTILLA, plantilla=resultado.plantilla)
    )


class GenerarParametros(Executor):
    """DETERMINISTA + HUMANO (T3.3). Ni una llamada al modelo.

    Deriva de `Requisitos` todo lo que el manifest sabe de donde sacar, y lo que
    quede lo PREGUNTA. A diferencia de las variables de entorno, aqui un hueco no
    vale: el parameters.schema.json los declara `required` y un hueco dejaria el
    pipeline invalido.

    Es el segundo nodo que se suspende, y no hizo falta tocar el bucle de
    `ejecutar()` para anadirlo: ese bucle solo ve "peticiones pendientes con su
    response_type". Esa es la ventaja de que el conductor sea generico.
    """

    @handler
    async def empezar(self, ctx_flujo: Contexto, ctx: WorkflowContext[Contexto]) -> None:
        if ctx_flujo.plantilla is None:
            await ctx.send_message(ctx_flujo.paso(Estado.GENERANDO_PARAMETROS))
            return

        derivacion = derivar(ctx_flujo.requisitos, ctx_flujo.plantilla)
        ctx.set_state("contexto", ctx_flujo.model_dump())
        ctx.set_state("valores", derivacion.valores)
        ctx.set_state("origen", derivacion.origen)
        ctx.set_state("pendientes", [vars(p) for p in derivacion.pendientes])

        if derivacion.completa:
            await self._terminar(ctx)
            return
        await ctx.request_info(derivacion.pendientes[0].pregunta(), str)

    @response_handler
    async def responder(self, peticion: str, respuesta: str, ctx: WorkflowContext[Contexto]) -> None:
        pendientes = [Pendiente(**d) for d in ctx.get_state("pendientes")]
        actual, resto = pendientes[0], pendientes[1:]

        valores = {**ctx.get_state("valores"), actual.nombre: convertir(respuesta, actual)}
        ctx.set_state("valores", valores)
        ctx.set_state("origen", {**ctx.get_state("origen"), actual.nombre: "respuesta del usuario"})
        ctx.set_state("pendientes", [vars(p) for p in resto])

        if resto:
            await ctx.request_info(resto[0].pregunta(), str)
            return
        await self._terminar(ctx)

    @staticmethod
    async def _terminar(ctx) -> None:
        contexto = Contexto(**ctx.get_state("contexto"))
        valores = ctx.get_state("valores")

        # La validacion va contra el schema REAL de la plantilla, leido de ADO.
        # Se hace aqui, antes de seguir, para que un valor invalido salte en la
        # conversacion y no al escribir el fichero.
        validar(valores, leer_schema(_ado(), contexto.plantilla))

        await ctx.send_message(contexto.paso(
            Estado.GENERANDO_PARAMETROS, parametros=valores, origen_parametros=ctx.get_state("origen")
        ))


@executor(id=Estado.PLANIFICANDO_CAMBIOS)
async def planificar_cambios(ctx_flujo: Contexto, ctx: WorkflowContext[Contexto]) -> None:
    """DETERMINISTA. Que ficheros existen ya: de ahi sale el add vs edit."""
    inv = inventario(_ado(), ctx_flujo.requisitos.repo_codigo)
    await ctx.send_message(ctx_flujo.paso(Estado.PLANIFICANDO_CAMBIOS, inventario=inv))


@executor(id=Estado.RESOLVIENDO_VARIABLES)
async def resolver_variables(ctx_flujo: Contexto, ctx: WorkflowContext[Contexto]) -> None:
    """DETERMINISTA, sin una sola llamada al modelo (T3.4).

    Va DESPUES de planificar porque necesita el contenido de los vars/*.yml que
    ya existan: sin eso no se puede fundir, y regenerar destruiria lo que un
    humano hubiera anadido.
    """
    existentes = {
        ambito: fichero.contenido
        for ambito, fichero in (ctx_flujo.inventario.variables if ctx_flujo.inventario else {}).items()
    }
    ficheros = generar(ctx_flujo.requisitos, existentes)
    await ctx.send_message(ctx_flujo.paso(Estado.RESOLVIENDO_VARIABLES, ficheros_variables=ficheros))


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
        params = "\n".join(
            f"      {n} = {v!r}   ({ctx_flujo.origen_parametros.get(n, '?')})"
            for n, v in ctx_flujo.parametros.items()
        )

        # Los huecos se enseñan ANTES de escribir, no despues: son lo unico que
        # una persona tiene que rellenar a mano, y verlos aqui sale mas barato
        # que descubrirlos revisando el Pull Request.
        pendientes = {
            f"vars/{ambito}.yml": faltan
            for ambito, contenido in ctx_flujo.ficheros_variables.items()
            if (faltan := huecos(contenido))
        }
        aviso = ""
        if pendientes:
            detalle = "\n".join(f"      {fichero}: {', '.join(v)}" for fichero, v in pendientes.items())
            aviso = f"\n    Variables sin valor (quedan como hueco; nadie las inventa):\n{detalle}"

        # El Contexto NO viaja dentro de la peticion ni vuelve en la respuesta:
        # lo que sale al exterior es solo el texto y lo que entra es un bool. Se
        # guarda en el estado del executor para recuperarlo al reanudar.
        ctx.set_state("contexto", ctx_flujo.model_dump())
        await ctx.request_info(
            f"Plantilla: {plantilla}\n    Parametros:\n{params}\n{plan}{aviso}"
            "\n    ¿Escribo esto en Azure DevOps?", bool
        )

    @response_handler
    async def recibir(self, peticion: str, respuesta: bool, ctx: WorkflowContext[None, Contexto]) -> None:
        # Se reconstruye desde el estado que guardo `preguntar`. Para T3.1 basta
        # con registrar el veredicto: escribir de verdad es el Bloque 4.
        contexto = Contexto(**ctx.get_state("contexto"))
        final = Estado.COMPLETADO if respuesta else Estado.CANCELADO
        await ctx.yield_output(contexto.paso(Estado.CONFIRMANDO_PUSH).paso(final, confirmado=respuesta))


def construir_workflow() -> tuple:
    recoger = RecogerRequisitos(id=Estado.RECOGIENDO_REQUISITOS)
    generar_parametros = GenerarParametros(id=Estado.GENERANDO_PARAMETROS)
    confirmar = ConfirmarPush(id=Estado.CONFIRMANDO_PUSH)
    workflow = (
        WorkflowBuilder(start_executor=recoger)
        .add_edge(recoger, descubrir_catalogo)
        .add_edge(descubrir_catalogo, seleccionar_plantilla)
        .add_edge(seleccionar_plantilla, generar_parametros)
        .add_edge(generar_parametros, planificar_cambios)
        .add_edge(planificar_cambios, resolver_variables)
        .add_edge(resolver_variables, confirmar)
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


async def ejecutar(responder_humano) -> Contexto | None:
    """Conduce el workflow: arranca, y cada vez que se suspende pidiendo algo,
    lo pregunta y reanuda. Termina cuando ya no pide nada.

    El bucle es generico a proposito: no sabe nada de requisitos ni de
    confirmaciones. Solo ve "peticiones pendientes" con su `response_type`, y
    delega en `responder_humano(texto, tipo)`. Anadir una puerta humana nueva en
    cualquier nodo no obliga a tocar este bucle.
    """
    global _cliente_ado
    reiniciar_contador()
    workflow, _ = construir_workflow()
    almacen = InMemoryCheckpointStorage()

    with ClienteAdo.desde_entorno() as cliente:
        _cliente_ado = cliente
        resultado = await workflow.run(Contexto(requisitos=Requisitos()), checkpoint_storage=almacen)

        vueltas = 0
        while True:
            peticiones = resultado.get_request_info_events()
            if not peticiones:
                break
            respuestas = {
                p.request_id: responder_humano(p.data, p.response_type) for p in peticiones
            }
            if any(v is None for v in respuestas.values()):
                return None  # el humano se fue
            vueltas += 1
            resultado = await workflow.run(responses=respuestas, checkpoint_storage=almacen)

        print(f"\n  [workflow] estado final: {resultado.get_final_state()}  "
              f"(se suspendio y reanudo {vueltas} veces)")
        salidas = resultado.get_outputs()
        return salidas[0] if salidas else None


async def main() -> None:
    """Demostracion guionizada. Para hablar tu: .venv/bin/python agente_chat.py"""
    from dotenv import load_dotenv

    load_dotenv()

    guion = iter([
        "necesito una pipeline para un servicio en Java",
        "el repo es demo-servicio-java",
        "Java 17, va en Docker, version 1.0.0",
        "listo",    # corta la recogida de los recomendados y pasa a confirmar
        "si",       # confirmacion de REQUISITOS -- cero llamadas al modelo
        "17",       # el modelo no extrajo version_lenguaje: T3.3 lo PREGUNTA
        "si",       # confirmacion del PUSH -- la puerta antes de escribir
    ])

    def responder(texto: str, tipo: type):
        print(f"\n  \033[1magente\033[0m: {texto}")
        try:
            respuesta = next(guion)
        except StopIteration:
            return None
        print(f"  \033[1mtu\033[0m: {respuesta}")
        return (interpretar(respuesta) is Veredicto.CONFIRMA) if tipo is bool else respuesta

    final = await ejecutar(responder)
    if final is None:
        print("\n  (la conversacion no llego al final)")
        return
    print(f"\n{informe(final.traza, llamadas_al_modelo())}")


if __name__ == "__main__":
    asyncio.run(main())
