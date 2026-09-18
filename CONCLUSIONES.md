# Conclusiones

> **Esto lo escribo yo, en primera persona, después de haberlo tocado.**
> Lo que hay debajo de cada pregunta son los datos medidos, no las respuestas:
> para no tener que volver a buscarlos. Las respuestas van en los huecos.

---

## 1. ¿Cuántos pasos necesitaron LLM de verdad, y el reparto medido coincide con el previsto?

### Lo previsto (declarado en `orquestacion/estados.py`)

El plan hablaba de 18 pasos; al implementarlo salieron **16 estados** (dos de los
previstos resultaron ser el mismo nodo, y aparecieron tres terminales).

| Naturaleza | Estados | Cuáles |
|---|---|---|
| determinista | 11 | catálogo, parámetros, variables, render, planificación, escritura, PRs, y los 3 terminales |
| híbrido | 1 | `seleccionando_plantilla` |
| llm | 2 | `recogiendo_requisitos`, `redactando_prs` |
| humano | 2 | `confirmando_requisitos`, `confirmando_push` |

Estados en los que el modelo **puede** intervenir: **3 de 16**.

### Lo medido (ejecución `runs/20260918-090930_demo-servicio-java`)

```
estados recorridos : 14   (los 2 que faltan son terminales de fallo: cancelado, requiere_revision_humana)
reparto            : determinista=9  hibrido=1  llm=2  humano=2
llamadas al modelo : 5
backend / modelo   : lmstudio / google/gemma-3-4b
```

Dónde se fueron esas 5 llamadas:

| Estado | Llamadas | Por qué |
|---|---|---|
| `recogiendo_requisitos` | 3 | una por turno de conversación (3 turnos) |
| `redactando_prs` | 2 | una descripción por Pull Request |
| `seleccionando_plantilla` | **0** | las reglas decidieron; el camino del LLM no se disparó |
| los otros 11 estados | 0 | — |

El 0 del estado híbrido no es una suposición: desde este punto la traza lo
registra (`seleccion.json` → `origen`, `confianza`). Con el catálogo actual —tres
arquetipos, una plantilla cada uno— y `tecnologia` siendo un slot bloqueante de
la conversación, las reglas deciden **siempre**.

El contador tampoco es una afirmación: `llm/cliente.py` envuelve
`chat.completions.create`, así que cuenta aunque alguien añada un agente nuevo y
se olvide de instrumentarlo.

### Mi respuesta

<!-- Aquí. Un párrafo basta.
     ¿El reparto previsto aguantó? ¿Qué estado creías que necesitaba modelo y no
     lo necesitaba —o al revés? ¿Y qué pasa con ese 0 del híbrido: es que el
     estado sobra, o es que el catálogo todavía es pequeño? -->

---

## 2. ¿Qué se rompió al pasar de disco a ADO, y qué habría hecho distinto?

Lo que costó una sesión de depuración, por orden de aparición:

| Lo que pasó | Lo que enseñó |
|---|---|
| ADO devuelve **401, no 404**, ante una ruta de API mal formada | diagnosticar por código de estado antes que por heurística de contenido |
| Una función (`crear_repo`) desapareció en una refactorización y no lo detecté: solo reejecuté el sembrador contra un escenario **ya sembrado**, así que únicamente recorrí el camino `[=]` | **una verificación que solo recorre el camino de no-hacer-nada no es una verificación** |
| `inventario(rama=...)` aplicaba una sola rama a **dos repos independientes** | el repo de código y el de pipelines no comparten nada, ni siquiera el nombre de la rama |
| `400: path already exists in the add operation` en la segunda ejecución | `add` vs `edit` se calcula contra la rama **destino real**, no contra `main` |
| El endpoint de **listado** de PRs trunca la descripción a 400 caracteres. Leí de ahí, creí que faltaba el enlace cruzado, y "arreglé" a mano dos descripciones que estaban bien — **corrompiéndolas** | el flujo era correcto; el error fue mío por leer de una fuente truncada. La fuente de verdad es `propuesta.descripcion`, nunca lo que devuelve la API |
| Un `str.replace` que no encajaba con nada: el aviso de huecos en la confirmación **nunca existió**, aunque yo lo había dado por hecho | toda sustitución de texto lleva un `assert` |
| El SDK de OpenAI sin timeout explícito: 600 s × 2 reintentos, un proceso 20 minutos al 0% de CPU sobre un socket muerto | `ado/cliente.py` tenía timeout desde el principio; `llm/cliente.py` no, y se notó |
| El bloque de estado como **último** mensaje: el modelo respondía al bloque, no al usuario | el estado va en el `system`; el mensaje del usuario, el último. Qwen 9B lo toleraba; gemma 4B no |
| `ctx.set_state` **no lo ve el nodo siguiente** | lo que necesita el registro final viaja en el mensaje, no en el estado del executor |
| `yaml.safe_dump` de un escalar suelto añade `...` y rompe el fichero | lo cazó la relectura del YAML generado |

### Mi respuesta

<!-- Aquí.
     De todo eso, ¿qué era ignorancia de ADO y qué era un fallo de método mío?
     Y la incómoda: la del PR truncado la provoqué yo "arreglando" algo que
     funcionaba. ¿Qué me habría evitado tocarlo? -->

---

## 3. ¿Qué parte de este sistema seguiría siendo mía si mañana cambio de framework o de proveedor Git?

25 ficheros Python, ~4.300 líneas. Medido con `grep`, no estimado:

**Si cambio de framework (MAF → otro):**

```
ficheros que importan agent_framework:  1 de 25   (orquestacion/agente_flujo.py)
```

La lógica no está dentro del framework: los nodos llaman a funciones que viven
fuera y que se ejecutan solas (`python -m seleccion.reglas`, `-m parametros.variables`…).
`requisitos/agente_recolector.py` es la prueba: el mismo diálogo, conducido por un
bucle en vez de por una máquina de estados, compartiendo `Sesion.responder()`.

**Si cambio de proveedor Git (ADO → GitHub/GitLab):**

```
paquete ado/ :  7 ficheros, 1.358 líneas  (≈31% del total)
fuera de ado/, ficheros que llaman a la API: 1   (orquestacion/agente_flujo.py)
```

El resto importa de `ado/` solo el **tipo** `PlantillaDisponible` (un modelo
Pydantic, sin HTTP dentro) o hace el import dentro de su demo `__main__`.

**Lo que puede alucinar:** 6 ficheros `agente_*` frente a 17 deterministas, y no
es una afirmación de confianza — `comprobar_agentes.py` recorre el grafo de
imports y falla en las dos direcciones: si un `agente_*` no llega a `llm/`, y si
uno sin prefijo sí llega.

### Mi respuesta

<!-- Aquí.
     El número dice que el acoplamiento está en un fichero. Pero, ¿qué me
     costaría de verdad el cambio: el código, o volver a aprender las rarezas
     del proveedor nuevo (los 401, los `add` vs `edit`, los campos truncados)?
     ¿Cuál de las dos cosas es el activo real de esta PoC? -->

---

## Cómo reproducir estos números

```bash
.venv/bin/python -m orquestacion.estados   # el reparto declarado
.venv/bin/python comprobar_agentes.py      # agentes vs deterministas
.venv/bin/python agente_chat.py            # una ejecución entera -> runs/
cat runs/*/metadata.json                   # el reparto y las llamadas MEDIDAS
```
