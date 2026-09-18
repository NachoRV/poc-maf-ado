# Bitácora de sesiones — poc-agentes-maf

Registro de cierre de cada sesión: qué se hizo, en qué quedó el código y cuál es el siguiente paso exacto. Empieza a leer siempre por la entrada más reciente (arriba).

Continúa la bitácora de `poc-agentes` (sesiones 1–4, hasta 2026-09-12), que sigue viva en aquel repo para su T4.4 pendiente.

---

## Sesión 1 — 2026-09-16

### Por dónde retomar
**El plan está cerrado salvo dos cosas, y las dos son del usuario o necesitan ADO real.** Queda:
- **T5.2 — responder las tres preguntas de `CONCLUSIONES.md`.** El andamio está escrito con **todos los datos ya medidos** (reparto declarado vs medido, dónde se fueron las 5 llamadas, la lista de lo que se rompió con ADO, el acoplamiento medido con `grep`). Los tres huecos `<!-- Mi respuesta -->` los escribe el usuario en primera persona — igual que T4.4 en `poc-agentes`.
- **T4.4** — verificar contra ADO real que regenerar no destruye ediciones humanas (el código lo hace y está probado en memoria; falta la prueba de extremo a extremo: editar un `vars/pro.yml` a mano en ADO y re-ejecutar el alta).

### T5.2 — el andamio de `CONCLUSIONES.md` (los datos, no las respuestas)
El documento no se escribe entero porque la parte que vale es la de primera persona. Lo que sí se puede preparar es que las respuestas no obliguen a volver a buscar nada:

**Pregunta 1 (¿cuánto LLM de verdad?).** El plan hablaba de 18 pasos; salieron **16 estados**. Declarado: 11 deterministas, 1 híbrido, 2 LLM, 2 humanos → el modelo *puede* intervenir en **3 de 16**. Medido en la ejecución real: 14 estados recorridos, **5 llamadas** — 3 de conversación (una por turno) + 2 de descripciones de PR. **El estado híbrido gastó 0.**

**Dos arreglos que hicieron falta para que eso fuera demostrable y no una afirmación:**
1. `seleccion.json` guardaba *qué* plantilla se eligió pero no *cómo*. El `Seleccion` con `origen`/`confianza` se descartaba al salir del nodo. Ahora viaja en el `Contexto` y se vuelca: `origen: "reglas", confianza: 1.0` es la prueba de que el modelo no se llamó. Era un criterio de éxito del propio T5.1 que no se cumplía.
2. La tabla de `orquestacion/estados.py` seguía diciendo `"(Bloque 4, pendiente)"` en cinco filas ya implementadas. La tabla es la evidencia de la pregunta 1: si miente, la conclusión miente.

**Pregunta 3 (¿qué sobrevive a un cambio de framework o de proveedor Git?).** Medido con `grep`, no estimado: **1 de 25 ficheros** importa `agent_framework`, y **fuera de `ado/` solo ese mismo fichero** llama a la API de Azure DevOps — el resto importa de `ado/` únicamente el tipo Pydantic `PlantillaDisponible`, o lo hace dentro de su demo `__main__`. El paquete `ado/` son 1.358 de ~4.300 líneas.

### T5.1 completada (la traza en runs/)
`traza/registro.py`, determinista. Una carpeta por ejecución con 14 ficheros: `requisitos.json`, `transcripcion.md`, `seleccion.json`, `parametros.json`, `variables.json`, los 5 ficheros generados bajo `generado/`, las dos `descripcion-pr-*.md`, `metadata.json` y `resultado.json`.

**Lo que la distingue de la traza de `poc-agentes` es una columna: el ORIGEN de cada valor.** No basta con guardar que `replicas` valía 8; hay que poder decir si lo decidió el sistema, lo dijo una persona, o **estaba ya en el repo y se respetó**. Con variables de entorno esa distinción deja de ser un lujo.

```json
"origen": {
  "isDocker":    "requisitos.contenedor",
  "appVersion":  "requisitos.version_app",
  "javaVersion": "respuesta del usuario"      <- el modelo no lo extrajo, se preguntó
}
```

Y para las variables, cuatro orígenes distinguidos: `derivada de los requisitos`, `dictada por el usuario`, `HUECO: nadie la ha inventado`, y **`conservada del repo`** —distinguiendo además si la añadió una persona y el sistema no la conoce—.

**Se registra en los TRES finales** (completado, cancelado, revisión humana): una ejecución abandonada también merece traza — saber que alguien dijo que no, y ante qué plan, es información.

**Refactor necesario:** `descripciones` y `historial` pasaron de `ctx.set_state` al `Contexto`. El estado de un executor **no lo ve el siguiente**, y el registro final es otro nodo. Es el tipo de acoplamiento que solo se ve al añadir el consumidor.

`metadata.json` cruza la traza con la tabla de estados y el contador: backend, modelo, llamadas reales y cada estado con su naturaleza. **El reparto deja de ser una afirmación y pasa a ser un dato por ejecución.**

### T4.5 + cableado completo — la PoC ya abre Pull Requests de verdad
Tres piezas nuevas y el flujo entero conectado.

**`render/renderizador.py`** (determinista, salda la deuda de `poc-agentes`): el pipeline hijo con **dos** entradas en `resources.repositories` — `templates` anclado **por tag** (el pipeline queda clavado a la versión de plantilla con la que se decidió) y `codigo`, necesario porque el pipeline vive lejos del código y porque las variables se leen de él. Un `parameters.entorno` con lista cerrada elige qué fichero de variables se carga. Única expresión que no pasa por `yaml.dump` (`${{ parameters.entorno }}`) va con marcador y se sustituye al final, releyendo el resultado.

**`redaccion/texto.py` (determinista) + `redaccion/agente_pr.py` (LLM).** La división es la decisión que importa: **el modelo escribe SOLO el párrafo de explicación**. El orden de merge, la lista de ficheros, los huecos y el enlace al PR hermano los escribe código. Si el modelo se inventara el orden de merge, alguien mergearía el pipeline antes que sus variables y lo dejaría roto — esa frase no puede depender de que un LLM tenga un buen día. Y si el modelo falla hay respaldo determinista: el PR se abre igual.

**Observación honesta sobre el 4B:** en la prosa libre **se inventa cosas** que no están en los datos ("ejecutar pruebas unitarias y de integración", "despliegue a los entornos"), pese a que el prompt se lo prohíbe explícitamente. Es justo por eso que este es el paso de menor riesgo: su salida no la consume ningún programa y un humano la lee antes de aprobar.

**Recorrido completo verificado contra ADO real — 13 estados, 5 llamadas al modelo:**
```
llm           recogiendo_requisitos
humano        confirmando_requisitos
determinista  descubriendo_catalogo
hibrido       seleccionando_plantilla
determinista  generando_parametros
determinista  planificando_cambios
determinista  resolviendo_variables
determinista  renderizando
humano        confirmando_push
llm           redactando_prs
determinista  escribiendo_en_ado
determinista  creando_prs
determinista  completado
reparto: determinista=8  hibrido=1  llm=2  humano=2
```
Resultado: **PR #8** en `demo-servicio-java` (los 4 `vars/*.yml`) y **PR #9** en `pipelines` (el `azure-pipelines.yml`), enlazados entre sí y con el orden de merge escrito en ambos.

### Trampa de ADO: el listado de PR trunca la descripción
El endpoint que **lista** pull requests devuelve la descripción **truncada a 400 caracteres**; solo el `GET` de un PR concreto la da entera.

**Y el daño lo hice depurando, no el código.** Al verificar leí la descripción del listado, vi que faltaba el enlace y llamé a `enlazar()` a mano pasándole esa versión truncada — que se escribió de vuelta, destruyendo la descripción real. El flujo nunca hace eso: parte de `propuesta.descripcion`, su propia fuente, y jamás de lo que devuelve la API. Anotado en el docstring de `enlazar()`. Los PR corrompidos se abandonaron y se regeneró el alta limpia.

### T4.1–T4.3 completadas (escribir en Azure DevOps)
`ado/cambios.py`. Cero IA: es fontanería, y es lo que convierte la PoC en demo.

**Las tres reglas que impone el reparto en dos repos:**
1. **Orden**: variables primero, pipeline después. Un pipeline mergeado sin sus variables está roto — referencia `vars/*.yml@codigo`, que no existiría. El orden va escrito en la descripción de los dos.
2. **Los dos o ninguno**: si falla el segundo, se abandona el primero y se borra su rama. Y las variables van primero **a propósito**: si algo falla, lo que queda abierto es el PR inofensivo, no un pipeline roto.
3. **Idempotencia**: rama existente se reutiliza; PR activo desde esa rama se devuelve en vez de crear otro.

**Verificado contra ADO real** (se crearon PR de verdad y se limpiaron después): dos PR enlazados creados, segunda ejecución **reutilizando** ambos (`reutilizado=True`), y el rollback probado abandonando los cuatro PR de prueba y borrando sus ramas. Quedan 0 PR activos.

**El hallazgo, que solo aparece ejecutando dos veces:** la segunda pasada murió con `400: The path '/vars/dev.yml' specified in the add operation already exists`. Es la trampa `add`/`edit` de T1.3, con un matiz nuevo: **el inventario calcula el tipo contra `main`**, que es lo correcto para enseñárselo a un humano en la confirmación, pero en una reejecución que reutiliza la rama el fichero ya existe **en la rama** aunque siga sin existir en `main`.

Arreglado donde toca: `empujar()` **resuelve el `change_type` contra la ref a la que de verdad escribe**, listando su árbol. Dejarlo en manos del llamante sería pedirle que acierte cada vez; resolverlo ahí lo hace correcto por construcción. El `add`/`edit` del inventario pasa a ser informativo para el humano.

Otro detalle del mismo estilo: `oldObjectId` debe ser el último commit **de la rama** si ya existe, no el de la base — pasar el de la base sobre una rama existente es un rechazo de ADO.

### T3.3 completada — opción B: tampoco aquí se infiere nada
El usuario eligió: nada se infiere, ni en variables ni en parámetros. `parametros/generador.py` va **sin prefijo `agente_`**. Con esto el sistema se queda con **un solo agente de verdad**: la conversación.

A diferencia de T3.4, aquí **un hueco no vale**: el `parameters.schema.json` los declara `required` y un hueco dejaría el pipeline inválido. Lo que no se deriva se **pregunta**.

**El puente con el catálogo.** Los manifests declaran `derived_from` apuntando a señales del fingerprint de `poc-agentes` (`has_dockerfile`, `pom_java_version`) que aquí no existen. En vez de hardcodear "javaVersion sale de version_lenguaje" —que ataría el código al catálogo de hoy, justo lo que se evitó en T3.2— se traduce **señal → campo de `Requisitos`** con una tabla de puente, y **lo que no se sepa traducir no es un error: se pregunta**. El módulo queda completo sin conocer el catálogo entero.

**Verificado:** requisitos completos → se deriva todo, **0 llamadas**; a medias → pregunta solo lo que falta con las opciones del manifest; valor fuera del enum → rechazado contra el schema real.

**Efecto en la tabla de estados**, que es lo que se enseña: `generando_parametros` pasó de `HIBRIDO` a `DETERMINISTA`. Reparto declarado ahora: **11 deterministas, 1 híbrido, 2 LLM, 2 humanos**. De 16 estados, **3 pueden tocar el modelo; solo 2 lo necesitan siempre**, y uno de esos dos (las descripciones de los PR) es prosa que ningún sistema consume.

**Efecto colateral que hubo que corregir:** el aviso de la confirmación decía *"si sigues, lo decidirá el modelo"*. Con la opción B eso pasó a ser **mentira** — ya no lo decide nadie, se pregunta. Corregido en los dos sitios donde aparecía.

**El nodo se suspende y no hubo que tocar el conductor.** `GenerarParametros` es el segundo nodo con `request_info`, y `ejecutar()` no cambió ni una línea: solo ve "peticiones pendientes con su `response_type`". Esa es la ventaja de que el bucle sea genérico.

### T3.4 completada (variables por entorno) — CERO LLM
Decisión del usuario del 2026-09-18, y es la que define el módulo: *"no necesito que infiera ninguna variable, la que no esté se deja el hueco; siempre se crean los 4 ficheros, uno por entorno y el común, porque esto lo tendrán todos los proyectos y tecnologías"*.

`parametros/variables.py`, **sin prefijo `agente_`**: no hay modelo aquí. Un valor de variable de producción no es deducible —no está en los requisitos ni en el repo— y una invención plausible es **peor** que un hueco, porque el hueco se ve y el valor inventado no.

**Los cuatro ficheros siempre.** Que un entorno no tenga variables propias todavía no es motivo para no crear el suyo: el fichero es el sitio donde alguien las pondrá.

**Verificado, las tres propiedades:**
- Alta en repo limpio → `common`, `dev`, `acc`, `pro`, con `azureSubscription` como hueco (`value:` nulo + comentario `TODO`).
- Regeneración sobre un `pro.yml` editado a mano → conserva el hueco ya relleno (`SUB-PRODUCCION-REAL`), **no pisa** `replicas: 8` con el 3 calculado, y mantiene una variable que el sistema no conoce (`inventadaPorUnHumano`).
- Sin `version_app` → hueco, no un `1.0.0` "razonable".

**Bug cazado por la propia defensa:** `yaml.safe_dump` de un escalar suelto añade el marcador de fin de documento (`...`) y rompía el fichero. Lo detectó la relectura del YAML generado, que está justo para eso. Sustituido por `json.dumps` (JSON es subconjunto de YAML).

**Bug de proceso, el mismo de siempre:** una edición con `str.replace` **no casó y no dijo nada**, así que el aviso de huecos en la puerta de confirmación no llegó a existir aunque yo lo diera por escrito. Se detectó depurando, no leyendo. Regla: toda edición por sustitución de texto lleva `assert` del patrón buscado.

El flujo ya recorre 8 estados: `determinista=4  hibrido=1  llm=1  humano=2`, **3 llamadas al modelo** (una por turno de conversación). La puerta de confirmación ahora lista los huecos **antes** de escribir.

### T3.2 completada (selección de plantilla híbrida)
`seleccion/reglas.py` (determinista) + `seleccion/agente_hibrido.py` (el desempate).

**Las reglas traducen requisito → ARQUETIPO, no requisito → plantilla.** El arquetipo es un concepto del dominio; la plantilla es un artefacto que puede versionarse o desdoblarse. Y las candidatas salen de comparar el arquetipo contra el `applies_to` de las plantillas **descubiertas en ADO** — añadir una plantilla es un PR al repo de plantillas, este fichero no se toca.

**Tres barandillas en el desempate:** lista cerrada (sale del catálogo real, un id fuera cuenta como respuesta inválida), salida estructurada con reintento, y umbral 0.7 aplicado **después** de tener una respuesta válida. Más minimización de contexto: al modelo le llega `resumen_para_llm()`, no el manifest entero.

**Verificado, los tres caminos:**
- Caso real → `decidido` por reglas, **0 llamadas al modelo**.
- Catálogo fabricado con dos plantillas para el mismo arquetipo → entra el LLM, elige `ci-java-container` con confianza 0.95 y explicación.
- El mismo caso con umbral 0.99 → `requiere_revision_humana`. Es el criterio exacto del plan.

**Dato honesto:** con el catálogo actual (tres arquetipos, una plantilla cada uno) y `tecnologia` siendo slot bloqueante, **las reglas deciden siempre**. El camino del LLM no es decorado —es lo que hará falta cuando dos plantillas compitan— pero hoy no se dispara con datos reales. Dicho en el propio docstring, en vez de fingir ambigüedad.

**`llm/estructurado.py` (nuevo):** se extrae el patrón pedir-JSON + limpiar + validar + reintentar al aparecer el **segundo** consumidor, mismo criterio que se usó para `ado/git.py`. `agente_extractor.py` migrado; el patrón ya no está duplicado.

### Bug del verificador de convención: el defecto estaba del lado inseguro
Al añadir `llm/estructurado.py`, `comprobar_agentes.py` pasó a reportar **CERO agentes sin dar ningún error**. La regla enumeraba las funciones que "sí construyen cliente" (`construir_cliente`, `kwargs_json`), y en cuanto nadie las importó directamente la detección se cayó en silencio.

**Invertido:** ahora es una lista de **excepciones** (`SOLO_OBSERVABILIDAD = {llamadas_al_modelo, reiniciar_contador}`) y todo lo demás de `llm/` cuenta. Añadir algo nuevo a `llm/` marca a sus usuarios como agentes hasta que alguien decida lo contrario a propósito. **El defecto tiene que fallar del lado seguro.** Detección restaurada: 5 agentes.

### La conversación entra DENTRO del flujo (petición del usuario)
El usuario señaló que T3.1 ejecutaba todo de golpe sin pedirle nada: el flujo arrancaba con `Requisitos` ya confirmados y la conversación vivía fuera. Estaba dicho, pero era una decisión discutible y el plan la aparcaba hasta el Bloque 5. **Adelantada y hecha.**

**Verificado antes de construir:** un `@response_handler` **sí puede volver a llamar a `request_info`**, que es lo que hace posible un diálogo de varios turnos dentro del workflow. Probado con un ejemplo mínimo: 3 ciclos de suspensión/reanudación con el estado conservado.

**`RecogerRequisitos` (nuevo nodo) y la diferencia técnica que importa:**

| | `conversar()` (fuera) | `RecogerRequisitos` (dentro) |
|---|---|---|
| Forma | un **bucle** | una **máquina de estados** |
| Turno | `input()` | termina con `request_info` → el workflow se suspende |
| Requisito | un proceso vivo todo el rato | el estado sobrevive en el checkpoint |

**La lógica de negocio no se duplica:** las dos usan `Sesion.responder()`. Lo único que cambia es el conductor. Mismo patrón que en `poc-agentes`, donde el workflow de MAF y el pipeline a mano eran dos interfaces sobre la misma lógica.

Detalle: el estado del diálogo (requisitos a medias, historial, fase) vive en `ctx.set_state`, **no** en atributos del executor — los atributos no sobreviven a una reanudación desde checkpoint.

**`ejecutar()` es un bucle genérico**: no sabe nada de requisitos ni de confirmaciones, solo ve peticiones pendientes con su `response_type` y delega en `responder_humano(texto, tipo)`. Añadir una puerta humana en cualquier nodo no obliga a tocarlo.

**`agente_chat.py`** es el punto de entrada, y es deliberadamente diminuto: el flujo no depende de que exista una terminal. La misma máquina podría contestarse desde una web o desde otro proceso dos días después; solo cambiaría ese fichero.

**Ejecución completa verificada contra ADO real** (6 suspensiones/reanudaciones):
```
llm           recogiendo_requisitos
humano        confirmando_requisitos
determinista  descubriendo_catalogo
hibrido       seleccionando_plantilla
determinista  planificando_cambios
humano        confirmando_push
determinista  completado
reparto: determinista=3  hibrido=1  llm=1  humano=2
llamadas reales al modelo: 3
```
**3 llamadas**, una por turno de conversación. Las dos confirmaciones costaron **cero**.

**Punto abierto de la convención, resuelto antes de lo previsto:** al meter la conversación dentro, ejecutar el orquestador pasa a llamar al modelo. Renombrados `flujo.py` → `orquestacion/agente_flujo.py` y `chat.py` → `agente_chat.py`. La regla queda con **una sola excepción** (el paquete `llm/`) y ninguna más: una convención con excepciones deja de ser comprobable. De paso, `agente_chat.py` coincide con cómo se llamaba el chat en `poc-agentes`.

### T3.1 completada (la orquestación como máquina de estados)
**El hallazgo que invalida el plan B del plan: MAF 1.18.0 tiene human-in-the-loop de primera clase.** Verificado con un ejemplo mínimo *antes* de construir nada encima:

| Pieza | Qué hace |
|---|---|
| `await ctx.request_info(datos, tipo)` | **suspende** el workflow |
| `WorkflowRunState.IDLE_WITH_PENDING_REQUESTS` | el estado en que queda |
| `resultado.get_request_info_events()` | las peticiones pendientes (`request_id`, `data`) |
| `workflow.run(responses={id: valor}, checkpoint_storage=...)` | **reanuda** |
| `@response_handler(peticion, respuesta, ctx)` | por donde entra la respuesta |

**`RequestInfoExecutor` NO existe en esta versión** — era un recuerdo obsoleto de la documentación. La regla de no fiarse de la memoria sobre MAF sigue pagando.

Consecuencia que va más allá de la comodidad: quien reanuda puede ser **otro proceso, otro día**, leyendo el checkpoint. "Nada se escribe sin un sí" deja de ser una convención y pasa a ser estructural.

**`orquestacion/estados.py`** (puro, no importa MAF ni el LLM ni ADO): 16 estados con su naturaleza declarada **en código**. Cuatro naturalezas y no dos, porque "determinista vs LLM" se queda corto: `HIBRIDO` (reglas primero, modelo solo si no deciden) y `HUMANO` (el flujo se suspende) describen cosas distintas. La tabla markdown se **genera** desde el diccionario, así que no puede quedarse desactualizada. Reparto declarado: **10 deterministas, 2 híbridos, 2 LLM, 2 humanos**.

**`llm/cliente.py` cuenta las llamadas envolviendo al cliente**, no pidiéndole a cada agente que se instrumente: así es imposible añadir un agente nuevo y olvidarse de contarlo. Y el defecto del código pasó de `qwen/qwen3.5-9b` a `google/gemma-3-4b` — seguía siendo el viejo, tapado por el `.env`.

**`orquestacion/agente_flujo.py`:** el mensaje ES el estado. Un `Contexto` (Pydantic) viaja por el grafo y cada nodo devuelve una **copia** con su marca en `traza` — nada de variables globales entre nodos, así la secuencia recorrida es un dato del resultado y no un efecto secundario de unos prints. Única excepción acotada y comentada: el `ClienteAdo`, que no es serializable y por tanto no puede viajar en el mensaje.

**Verificado en real, los dos caminos** (sí → `completado`, no → `cancelado`), contra ADO de verdad:
```
determinista  descubriendo_catalogo
hibrido       seleccionando_plantilla
determinista  planificando_cambios
humano        confirmando_push
determinista  completado
reparto: determinista=3  hibrido=1  humano=1
llamadas reales al modelo: 0
```
**Cero llamadas al modelo** en un recorrido completo desde requisitos confirmados hasta la puerta humana. Medido por el contador, no estimado.

**Alcance honesto:** el flujo arranca con `Requisitos` **ya confirmados** — la conversación multiturno vive fuera (`requisitos/agente_recolector.py`) y se conecta en el Bloque 5. Los nodos de selección y parámetros son provisionales hasta T3.2/T3.3: están **declarados y marcados como tales**, no ocultos.

### T2.3 completada — Bloque 2 cerrado
`requisitos/confirmacion.py` (**determinista, sin prefijo**) + `conversar()` en `agente_recolector.py`.

**La decisión: decir "sí" no puede costar una llamada al modelo.** Clasificar un "sí"/"no" es comparar cadenas, no razonar. El modelo solo entra cuando la respuesta no es un sí/no claro, es decir cuando de verdad hay lenguaje que interpretar ("cambia la versión a 21"). 10/10 casos correctos con **cero llamadas**, incluido `"si, pero no"` → `corrige` (se compara la frase entera, no se busca "si" dentro).

**La confirmación enseña también lo que está vacío**, y avisa: *"Ojo: version_lenguaje sin definir. Si sigues, lo decidirá el modelo."* Sin eso, la pantalla de confirmación oculta justo lo que hay que revisar.

`conversar(sesion, leer, escribir)` con entrada/salida **inyectables**: sin eso, el camino de corrección solo se prueba a mano, y es el que importa.

**Verificado en vivo, y la ejecución justifica la tarea entera:** el modelo se dejó `version_lenguaje` al extraer de "es Java 17, va en Docker, y la version es 1.0.0". La confirmación lo cazó y lo avisó; el usuario corrigió **dentro de la confirmación**; se aplicó y se volvió a confirmar (una corrección nunca da los datos por buenos); el "si" final costó cero tokens.

### Convención `agente_*` (2026-09-17)
Petición del usuario: saber de un vistazo qué ficheros llaman al LLM al ejecutarse. **Un fichero `agente_*` es un fichero que, AL EJECUTARSE, acaba llamando a un modelo.** Es transitivo: `agente_recolector.py` no importa `llm/`, importa `agente_extractor.py`, que sí. Lo que importa es si ejecutarlo cuesta llamadas, no de qué línea sale la llamada.

Renombrados: `extractor.py` → `agente_extractor.py`, `recolector.py` → `agente_recolector.py`. **Sin renombrar a propósito:** `llm/cliente.py` (no es un agente, es la infraestructura que usan; la carpeta ya avisa).

`comprobar_agentes.py` (nuevo) hace que la convención **se verifique sola**: recorre el grafo de imports y devuelve error si alguien la incumple en cualquier dirección. Una convención que solo vive en la cabeza de alguien se rompe en la tercera tarea. Estado actual: **2 agentes, 9 deterministas**.

### T2.2 completada (recolector conversacional)
Tres ficheros, con la **separación LLM/determinista hecha física, no documental**:

| Fichero | Naturaleza | Qué hace |
|---|---|---|
| `llm/cliente.py` | infraestructura | **Único sitio** que construye un cliente de modelo |
| `requisitos/agente_extractor.py` | **LLM** | Única pieza que habla con un modelo. Texto → dict parcial |
| `requisitos/agente_recolector.py` | determinista | El bucle, la fusión, cuándo parar, la recuperación de errores |

La regla queda comprobable: `grep -rl "from llm" --include="*.py" .` devuelve **dos ficheros en todo el proyecto**. "¿Qué partes pueden alucinar?" deja de ser una afirmación de confianza.

**Aclaración conceptual que conviene retener: esto NO es un agente.** Un agente decide *qué hacer* (qué herramienta llamar, si seguir o parar). Aquí el modelo recibe un estado, propone un diccionario y termina. Quién pregunta, cuándo se para y qué se guarda lo decide el código. Un agente puede sorprenderte; esto no.

**Dos clases de error, tratadas distinto** (y esto es lo que más se nota al usarlo):
- El **modelo** devuelve algo inválido → se reintenta contra el modelo con el error como feedback. Es culpa suya y puede corregirlo.
- El **usuario** dicta un repo que no existe → **no** se reintenta contra el modelo: transcribió bien lo que oyó. Se descarta el campo y se le pregunta al humano con la lista real de repos de ADO. Verificado en vivo con `demo-servicio-jaba`.

**Pregunta de respaldo determinista:** medido con gemma-3-4b, cuando ya no faltan bloqueantes el modelo devuelve `pregunta_al_usuario: null` aunque queden recomendados vacíos, pese a que el prompt le pide lo contrario. En vez de pelearse con el prompt, la pregunta se formula en código desde `DESCRIPCIONES`. "¿Cuál es la versión del lenguaje?" no necesita un LLM.

### El modelo local: tres días de lecciones en una tarde
**qwen3.5-9b era un modelo de RAZONAMIENTO y fue un error elegirlo.** Gastaba ~1500 tokens pensando para producir 130 caracteres de JSON. Las dos formas documentadas de apagarlo en LM Studio (`extra_body={"chat_template_kwargs": {"enable_thinking": false}}` y el sufijo `/no_think`) **se ignoran las dos** — comprobado. Subir el presupuesto a 16000 acabó **crasheando LM Studio entero**. Sustituido por **`google/gemma-3-4b`** (no razona): 16 tokens donde el otro gastaba 1500, y `LLM_MAX_TOKENS` baja de 8000 a 500.

**Bug propio grave, encontrado al cambiar de modelo: el orden de los mensajes.** El bloque de estado iba como último mensaje, detrás del historial, así que **el modelo respondía al bloque de estado y no a lo que acababa de decir la persona**. Medido con el mismo turno y los dos órdenes:

| Orden | Extrae |
|---|---|
| historial + estado al final | `{"tecnologia": "java"}` — ignora al usuario |
| estado en `system`, usuario al final | `{"tecnologia": "java", "repo_codigo": "demo-servicio-jaba"}` ✓ |

Qwen 9B lo toleraba; Gemma 4B no. **Es la peor clase de bug: el que no se ve hasta que cambias de modelo.** Contrapartida anotada en el código: el `system` cambia cada turno, así que no se cachea el prefijo — irrelevante en local, a corregir si el backend pasa a ser de pago.

**Faltaba timeout en el cliente del modelo.** A `ado/cliente.py` le puse `timeout=60` desde el principio; a este, ninguno. El SDK de OpenAI usa **600 s con 2 reintentos** por defecto, y un proceso estuvo **20 minutos al 0% de CPU** contra un socket muerto mientras el servidor respondía a todo lo demás en 2 s. Ahora `LLM_TIMEOUT=180` explícito y `max_retries=1` (el reintento útil, con el error como feedback, lo hace el extractor).

**Y los bucles sobreviven a un turno fallido** (`except Exception`, no `RuntimeError`): se han visto en real tres transitorios distintos — presupuesto agotado, `400 Model reloaded` y `The model has crashed`. La lección de `poc-agentes` (un 429 mataba la sesión entera) vuelve a aplicar, y los transitorios no vienen todos de nuestro código.

**Limitación honesta del 4B:** la extracción es variable. En una ejecución cogió `version_lenguaje`, `contenedor` y `version_app` del mismo mensaje; en otra se dejó `version_lenguaje`. No es un fallo del sistema — es exactamente lo que T2.3 (confirmación humana) existe para cazar, y un argumento para un modelo mayor en producción.

**Nota sobre proveedores:** `agent-framework-anthropic` **no existe** — MAF en Python solo trae paquetes para Gemini y OpenAI. Y Anthropic no tiene endpoint compatible con OpenAI, así que un backend suyo necesitaría el SDK `anthropic` y un tercer valor de `MODEL_BACKEND`. Pendiente de decisión del usuario; el coste de desarrollo sería ~0,7 céntimos por turno con Opus 5.

### T2.1 completada (el objeto `Requisitos`)
`requisitos/esquema.py`, módulo **puro** (se prueba entero sin red; la única excepción es `validar_repo_codigo()`, que recibe el cliente explícito).

**Tres decisiones de diseño:**
1. **Los campos obligatorios no son obligatorios en el tipo.** El objeto tiene que poder existir a medias durante toda la conversación; si Pydantic exigiera `repo_codigo` no se podría ni construir en el turno 1. Lo obligatorio vive en `slots_que_faltan()`. El tipo valida **forma**; la función decide **si se puede avanzar** — y es ella, nunca el modelo, quien declara terminada la conversación.
2. **Dos niveles de falta.** `SLOTS_BLOQUEANTES` (`tecnologia`, `repo_codigo`) frente a `SLOTS_RECOMENDADOS` (`version_lenguaje`, `contenedor`, `version_app`). Los segundos no bloquean, pero cada uno vacío es un hueco que tendrá que adivinar el LLM en T3.3. Hace visible el compromiso: cada pregunta respondida por un humano es una adivinanza menos.
3. **`fusionar()` funde, nunca sustituye.** `None` significa "no sé nada de esto", no "bórralo". Verificado: un modelo que "olvida" todo y solo manda `version_app` no destruye `tecnologia` ni `repo_codigo`. `variables_extra` se funde en profundidad. Y se reconstruye el modelo entero en vez de `model_copy(update=...)`, porque `model_copy` **no valida** y un `tecnologia: "cobol"` entraría tan tranquilo; así revienta, y ese error es la entrada del reintento de T2.2.

**Dos trampas cazadas al probar:**
- **`contenedor: False` no puede confundirse con "sin dato".** El filtro compara contra `None`, no por verdad. "No, sin Docker" es una respuesta, y perderla sería peor que no haber preguntado. Verificado.
- **Cadena vacía = sin dato** (hueco encontrado en la primera versión). Un modelo que no sabe algo devuelve `""` tan a menudo como `null`, y como `slots_que_faltan()` mira `is None`, el slot habría quedado marcado como relleno y el flujo habría seguido con una `version_app` vacía. Ahora `""` y `"   "` se ignoran, y las cadenas con contenido se recortan.

**Criterio de éxito verificado:** `Requisitos` a medias → lista exacta de bloqueantes vacíos; completo → `[]`; `repo_codigo` inexistente (`demo-servicio-jaba`) → `ErrorAdo` listando los repos que sí hay, detectado **en la conversación** y no en el push. Y un campo inventado (`framework`) o un valor fuera de la enumeración (`cobol`) salen como `ValidationError`, no como dato.

### Acotación de alcance (2026-09-17): T4.0 fuera
Decisión del usuario. Que las plantillas **compilen** —checkout cruzado, rutas relativas, triggers entre repos— es diseño de plantillas y es trabajo de **otro proyecto**. El objetivo de esta PoC es más estrecho: **tener plantillas en un repositorio y clonarlas**, dejando en el repo de código sus variables de entorno (el despliegue las necesita para que las soluciones accedan a ellas).

Consecuencias, anotadas para no engañarse después:
- El pipeline generado será YAML válido y coherente; **no se comprueba que Azure Pipelines lo ejecute**. Mismo criterio que `poc-agentes`.
- Las tres `template.yaml` del catálogo **no se tocan**.
- **Supuesto asumido, no verificado:** que `variables: - template: vars/x.yml@codigo` conviva con `extends:`. Si fuera falso, es una línea del renderizador, no un rediseño.
- **Riesgo aceptado:** el trigger entre repos no se diseña ni se prueba.
- Sigue en alcance, y pasa al Bloque 3: que el renderizador emita las rutas reales (`PlantillaDisponible.ruta_template`, `POC-MAF/plantillas-ci`) en vez de los `MiOrg/MiRepoDePlantillas` heredados.

### T1.3 completada + `ado/git.py` extraído
**`ado/git.py` (nuevo):** primitivas de Git sobre ADO — `id_repo`, `leer_fichero`, `leer_fichero_si_existe`, `listar_arbol`, `sha_rama`. Se extraen ahora y no antes porque es cuando aparece el **segundo** consumidor: que `destinos` importara de `catalogo` acoplaría cosas sin relación. División de capas: `cliente.py` es transporte, `git.py` son operaciones de Git expresadas sobre él.

**`ErrorAdo` gana `type_key`**, y esa es la pieza que sostiene T1.3. Un 404 de ADO significa dos cosas muy distintas: `GitItemNotFoundException` es "ese fichero no está", caso **normal** que decide `add`/`edit`; `GitRepositoryNotFoundException` o `TF401175` son errores de verdad. `leer_fichero_si_existe()` **solo** se traga el primero. Sin esa discriminación, "ese repo no existe" se colaría como "ese fichero no existe" y el flujo prepararía un `add` contra un repo fantasma.

**`ado/destinos.py`:** reescrita respecto al plan original. Desaparece elegir destino (está fijado por convención); queda el **inventario de las cinco rutas** en los dos repos, con `change_type` derivado. Los `vars/*.yml` se traen **con contenido** (hace falta para el merge de T3.4); el pipeline no, porque se regenera entero.

**Bug de diseño encontrado al probar, y la forma de encontrarlo es lo que vale:** `inventario()` tenía un único parámetro `rama` que aplicaba **a los dos repos**. Son repositorios independientes con espacios de nombres de rama independientes. En el flujo real —que lee `main` en ambos— no se habría notado nunca; saltó al probar contra una rama temporal que solo existía en el repo de código. Corregido a `rama_pipelines` / `rama_codigo`. El parámetro compartido invitaba al error.

**Verificado contra ADO real** creando una rama temporal en `demo-api-node` con dos de los cuatro `vars`, y borrándola al terminar:
- Mezcla correcta: `add` para el pipeline, `acc` y `dev`; `edit` para `common` y `pro`.
- El contenido de los existentes vuelve íntegro (incluido el `replicas: 8   # ajustado a mano` y una variable inventada que no está en ningún manifest — justo lo que T3.4 tiene que conservar).
- `es_alta` False.
- Repo inexistente → `ErrorAdo` listando los repos que sí hay. Rama inexistente → `ErrorAdo` con el `TF401175`. **Ninguno de los dos se disfraza de "fichero ausente".**

### Bug propio: `crear_repo()` borrada en la migración de T1.1
Al ampliar `sembrar.py` saltó `NameError: crear_repo`. Se había perdido en `dec016b`: un reemplazo por rango `t.index('def id_proyecto(')` → `t.index('def tiene_commits(')` se llevó por delante la función que había en medio.

**Lo que importa no es el borrado, es por qué no se detectó.** La verificación posterior a la migración solo ejecutó `sembrar.py` contra un escenario **ya sembrado**, donde todos los repos existían: solo recorrió los caminos `[=]` y nunca llamó a `crear_repo`. Fue una prueba que no podía fallar. Regla nueva abajo.

### T0.3 ampliada: repo `pipelines`
Creado en ADO con solo su `README.md`, que documenta la convención donde se descubre: una carpeta por repo de código, las variables **no** viven ahí, y el orden de merge de los dos PR. Su contenido real lo escribe el flujo vía Pull Request — sembrarlo sería falsear la demo. Nueva variable `AZDO_REPO_PIPELINES`.

Escenario final en `royovillanovai/POC-MAF`: `plantillas-ci` (tag v1.0.0), `pipelines`, `demo-servicio-java`, `demo-api-node`.

### Cambio de diseño: repo `pipelines` centralizado + variables en el repo de código
Decisión del usuario, tras tres preguntas cerradas. Lo que cambia:

- **T1.3 se reescribe.** Desaparece "listar repos candidatos y que el usuario elija" (el destino es siempre `pipelines`). Sobrevive, y gana peso, la detección de qué ficheros existen ya: decide el `changeType` (`add`/`edit`) y equivocarse es un 400 de ADO. Ya no es un fichero sino **cinco, en dos repos**, cada uno resuelto por separado.
- **Dos repos ⇒ dos Pull Requests ⇒ no hay atomicidad.** Reglas nuevas: orden de merge (primero variables, después pipeline), los dos PR o ninguno (si el segundo falla se abandona el primero y se borra la rama), y descripciones enlazadas entre sí.
- **Regenerar hace MERGE, no sobrescribe.** El usuario puede ampliar los `vars/*.yml` a mano; una segunda ejecución que los volcara enteros destruiría trabajo humano. Nueva T3.4 para esto y nueva T4.4 para verificarlo contra ADO de verdad.
- **El pipeline deja de estar al lado del código que construye.** Las tres `template.yaml` del catálogo asumen lo contrario (`mavenPomFile: pom.xml` relativo al repo checkouteado). Hay que añadirles `checkout: codigo` y que el renderizador emita `resources.repositories` con dos entradas. Nueva T4.0, y va **primero** en el Bloque 4.
- **El manifest gana `environment_variables`**: cada plantilla declara qué variables trae de serie, su `scope` (common/per-env), sus `defaults` y si son `secret`. Sin eso el chat no sabe qué preguntar.
- **`Requisitos` gana `repo_codigo`** (obligatorio, y validado contra ADO, no solo contra el tipo), **`entornos`** (default dev/acc/pro) y **`variables_extra`**.

**Dos supuestos del YAML nuevo SIN verificar, anotados en T4.0 para comprobarlos antes de construir encima:** que `variables: - template: x@codigo` conviva con `extends:` en el mismo pipeline, y que un pipeline en `pipelines` pueda dispararse por cambios en el repo de código.

**Efecto en el reparto determinista/LLM, que mejora:** el flujo pasa de 12 a 18 pasos, y los seis nuevos (resolver defaults, leer lo existente, fusionar, renderizar variables, decidir add/edit, segundo push) son **todos deterministas**. Siguen siendo **2 pasos puramente LLM y 2 híbridos**. Cuanto más concreto es el contrato de la plantilla, menos queda por adivinar.

### T1.2 completado (descubrimiento del catálogo)
`ado/catalogo.py`: `PlantillaDisponible` (modelo Pydantic) + `descubrir_plantillas()`, `leer_schema()`, `buscar_por_arquetipo()`. Lee las tres plantillas de `plantillas-ci` en ADO y las valida.

**Cuatro decisiones de diseño:**
1. **Todo se lee anclado al tag, nunca a una rama.** Si se leyera de `main`, el catálogo podría cambiar entre elegir la plantilla y renderizar el pipeline. Con el tag, una ejecución ve un catálogo congelado — y es el **mismo** `refs/tags/<tag>` que acabará escrito en el `extends`, así que lo decidido y lo generado coinciden por construcción.
2. **El manifest es entrada no confiable** (vive en otro repo, lo edita otra gente, llega como YAML suelto). Se valida en la frontera con Pydantic: `id`, `version`, `applies_to` y `parameters` obligatorios, `applies_to` y `parameters` con `min_length=1`.
3. **El `id` del manifest debe coincidir con el nombre de su carpeta, o se revienta.** No es quisquillosidad: la selección casa por `id` pero la ruta del `extends` se construye con la **carpeta**. Si divergen se genera un pipeline que apunta a una plantilla inexistente, y no se descubre hasta que Azure Pipelines intenta ejecutarlo.
4. **Caché en memoria y schemas en diferido.** De N plantillas solo hace falta el `parameters.schema.json` de la que se acabe eligiendo (T3.3).

`PlantillaDisponible.resumen_para_llm()` devuelve solo `id` + `applies_to` + `selection_rationale`: minimización de contexto, al modelo no le llega el manifest entero con su pool, sus controles y las descripciones largas de parámetros.

**Verificado contra ADO real — camino bueno:** las 3 plantillas con sus parámetros y su `ruta_template` correcta; la caché devuelve el mismo objeto sin tráfico HTTP; `buscar_por_arquetipo('java-container')` → `['ci-java-container']`; schema en diferido leído bien.

**Verificado — seis caminos de fallo**, cada uno con su mensaje propio:
| Caso | Excepción |
|---|---|
| `id` ≠ nombre de carpeta | `ErrorCatalogo` explicando la divergencia selección/`extends` |
| manifest sin `applies_to` | `ErrorCatalogo` + el error de validación de Pydantic |
| `applies_to: []` (lista vacía) | `ErrorCatalogo` (por eso el `min_length=1`) |
| manifest que no es YAML | `ErrorCatalogo` con el error del parser |
| repo de plantillas inexistente | `ErrorCatalogo` listando los repos que sí hay + cómo sembrar |
| tag inexistente | `ErrorAdo` con el `TF401175` de ADO íntegro |

**Corrección de un dato que yo mismo había repetido del plan:** descubrir el catálogo **no** son N+1 llamadas, son **N+2** (listar repos para resolver el id + árbol + una por manifest). Medido, no estimado. Y había una ineficiencia peor: `leer_schema()` volvía a llamar a `id_repo()`, pagando otro listado completo de repos en cada lectura. Corregido con una caché aparte para el id del repo — `leer_schema` pasa de 2 llamadas a 1, y el segundo descubrimiento a 0.

### T1.1 completado (cliente REST mínimo)
`ado/cliente.py`: `ClienteAdo` + `ErrorAdo`. Un solo sitio donde vive "cómo se hace una petición a ADO y cómo se interpreta la respuesta". Motivo concreto, no estético: ese diagnóstico se escribió **mal** en T0.2 y hubo que corregirlo en T0.3 **en dos ficheros a la vez**. Con `catalogo.py`, `destinos.py` y `cambios.py` por venir, serían cinco copias de una lógica sutil y la siguiente lección aprendida solo se aplicaría donde tocara ese día.

**La decisión de diseño que importa: dos ámbitos de URL, con métodos distintos.** `get`/`post`/`patch` van contra `{org}/{proyecto}`; `get_org` contra `{org}`. Es la respuesta arquitectónica al bug de T0.3 (confundir `/_apis/projects`, que es de organización, con el resto): en vez de confiar en acordarse, elegir mal deja de ser algo que pase por descuido.

Otras dos, también sacadas de fallos reales:
- **`api-version` se inyecta siempre.** ADO lo exige en cada llamada y olvidarlo da un error poco claro. Se puede sobreescribir por `params` si algún endpoint pidiera otra versión.
- **El PAT no se filtra**, ni en `__repr__` ni en los mensajes de `ErrorAdo`. En el Bloque 5 se serializan objetos a `runs/`, y un token en una traza en disco es justo lo que el `.gitignore` intenta evitar.

`ErrorAdo` guarda `status`/`metodo`/`ruta` por separado, no solo el texto: T4.3 necesita distinguir "404, la rama no existe" de "409, ya existe" sin parsear un mensaje.

**Criterio de éxito verificado:** `GET /_apis/git/repositories/no-existe-este-repo` devuelve el mensaje de ADO íntegro (`TF401019: The Git repository with name or identifier ... does not exist or you do not have permissions...`), capturado como `ErrorAdo` con `status=404`. No un `KeyError`.

**Migración de los dos consumidores** (la prueba real de que la abstracción sirve): `ado/humo.py` 133 → **59** líneas, `ado/sembrar.py` 333 → **246**. Los tres puntos de entrada ejecutan correctamente contra ADO real, y `sembrar.py` sigue siendo idempotente. Honestidad sobre el número: el total de líneas **sube** (466 → 507) porque `cliente.py` lleva bastante docstring; la ganancia no es tamaño, es que `grep -l 'is_redirect|content-type|www-authenticate' ado/*.py` devuelve **un solo fichero**.

Limpieza de paso: borrado `ado/.gitkeep`, que ya sobraba.

### T0.3 completado (escenario sembrado en Azure DevOps)
`ado/sembrar.py`, ejecutable con `.venv/bin/python -m ado.sembrar`. Crea en `royovillanovai/POC-MAF`:
- **`plantillas-ci`** — commit inicial con 10 ficheros (`README.md` + `catalog/<id>/{manifest.yaml,parameters.schema.json,template.yaml}` × 3), leídos en tiempo de ejecución de `poc-agentes/catalog/` para no duplicar la fuente de verdad. **Tag anotado `v1.0.0`** apuntando a ese commit.
- **`demo-servicio-java`** (`README.md` + `pom.xml`) y **`demo-api-node`** (`README.md` + `package.json`).

**Por qué por API y no a mano:** la operación necesaria es la **Pushes API**, la misma de T4.1 (rama + commit en una llamada, sin clonar). Sembrar con ella deja probada contra la organización real la parte difícil de T4.1 antes de llegar allí. Funciona: `oldObjectId` de 40 ceros crea la rama desde cero, y `changeType: "add"` con `contentType: "rawtext"` sube el contenido.

**Cambio deliberado sobre el plan:** el plan decía repos destino "vacíos o casi". Se les puso un commit inicial porque **un repo sin commits no tiene refs**, luego no tiene `main`, luego no hay `oldObjectId` del que partir, luego T4.1 no tendría rama base. Lo que sí falta a propósito en los destinos es `azure-pipelines.yml` — verificado ausente en ambos.

**Bug propio, y la lección es la parte que vale.** La primera ejecución falló con `401 con cuerpo no-JSON`. Se culpó al PAT (los scopes de creación de repos, que era la hipótesis anotada en el plan). Era mentira: la API `/_apis/projects` es de nivel **organización**, no cuelga del proyecto, y el `base_url` del cliente sí incluía el proyecto. Comprobado en real contra los tres endpoints:

| URL | Respuesta |
|---|---|
| `dev.azure.com/{org}/_apis/projects/{proy}` | 200 |
| `dev.azure.com/{org}/{proy}/_apis/projects/{proy}` | **401**, `www-authenticate: Basic`, cuerpo vacío |
| `dev.azure.com/{org}/{proy}/_apis/git/repositories` | 200 |

**Azure DevOps devuelve 401 a una ruta de API mal formada, no 404.** Es la misma familia de trampa que T0.2 (el fallo no se parece a lo que es) y el diagnóstico escrito en T0.2 picó de lleno: etiquetó un 401 legítimo como "página de login" y mandó a revisar el token. Dos arreglos:
- `ado/humo.py` y `ado/sembrar.py` tratan ahora 401/403 **antes** de la heurística de `Content-Type`, y el mensaje lista las causas en orden de probabilidad real: **1) la ruta**, 2) los scopes, 3) el token caducado.
- `ado/sembrar.py` ya no llama a `/_apis/projects`: saca el id del proyecto del listado de repos, que ya es de ámbito proyecto y ya se necesitaba. Una llamada menos y un scope menos (`Project & Team` deja de hacer falta). Queda un respaldo al endpoint de organización por si el proyecto no tuviera ningún repo.

**Verificado leyendo de vuelta desde ADO, no fiándose del 200:**
- Árbol de `/catalog` **consultado con `versionDescriptor.versionType=tag&version=v1.0.0`**: las 3 carpetas con sus 3 ficheros cada una. Esa consulta es literalmente la que necesita T1.2, así que T1.2 queda de-riesgada.
- Contenido real de `ci-java-container/manifest.yaml` traído del tag: `id`, `version` y `applies_to` correctos.
- Ambos destinos: `defaultBranch: refs/heads/main`, una sola rama, y `azure-pipelines.yml` **ausente**.
- **Idempotencia:** segunda ejecución completa sin crear nada (`[=]` en los 7 pasos), exit 0.

**Lo que esto sí demuestra y el plan daba por no demostrado hasta T4.1:** el PAT tiene permiso de **escritura** y de creación de repos. Lo único del alcance del PAT que sigue sin probarse es **crear pull requests**.

### T0.2 completado (PAT y llamada de humo a ADO)
`ado/humo.py`, ejecutable con `.venv/bin/python -m ado.humo`. No es un GET de tres líneas a propósito: es un diagnóstico, porque el fallo típico de ADO no se parece a un fallo.

**La trampa, verificada en real y no dada por supuesta.** Se probó con un PAT inventado contra la organización de verdad: Azure DevOps responde **302 con `Content-Type: text/html`**, redirigiendo a `spsprodweu5.vssps.visualstudio.com/_signin`. No devuelve 401. Consecuencias de diseño que están en el código:
- `follow_redirects=False` en la llamada. Si se siguiera la redirección se acabaría en un **200 con la página HTML de login**, y `response.json()` fallaría con un error de parseo que no menciona la autenticación por ningún lado.
- El diagnóstico comprueba, **en este orden**: primero `is_redirect`, después que el `Content-Type` sea JSON, y solo al final el código de estado. El orden importa: los dos primeros son los síntomas que no se parecen a lo que son.
- Cuando el cuerpo sí es JSON, se imprime el campo `message` de ADO tal cual (sus errores traen un texto legible; perderlo cuesta una tarde).

**Autenticación:** HTTP Basic con usuario **vacío** y el PAT como contraseña — `Basic base64(":" + PAT)`. Los dos puntos iniciales son el campo de usuario vacío; sin ellos el token es correcto y se rechaza igual, sin pista del motivo.

**Verificado:**
- Camino bueno: lista el único repo del proyecto (`POC-MAF`, sin commits) y avisa de que `plantillas-ci` no existe todavía (enlace explícito con T0.3, para que el fallo no aparezca más tarde en T1.2).
- PAT inválido → salida 1 con la causa nombrada, no un traceback de JSON.
- `.env` incompleto → salida 2 diciendo qué variables faltan.

**Alcance honesto, anotado en el propio script:** esta llamada solo demuestra **lectura** de Code. Que el PAT pueda escribir y abrir pull requests no queda probado hasta T4.1, cuando se empuje un commit de verdad. No dar por bueno más de lo comprobado.

### Qué se hizo hoy
- **Exploración de `poc-agentes`** para decidir qué se recicla. Conclusión: `render/renderizador.py` se copia tal cual (ya genera el `extends` + pin por tag que necesita el clonado), el contrato `manifest.yaml`/`parameters.schema.json`/`template.yaml` de las tres plantillas se sube a ADO como catálogo, y `context/fingerprint.py` muere (ya no hay repo que inspeccionar en la entrada).
- **Plan escrito:** [docs/Plan poc-ado.md](../Plan%20poc-ado.md). Tres decisiones cerradas antes de escribirlo: pipeline hijo con `extends` (no copia física de ficheros), catálogo como un repo con carpeta por plantilla, y acceso real a ADO con org propia + PAT desde el Bloque 0.
- **Desglose determinista vs LLM** (el entregable conceptual del ejercicio): 12 pasos, de los cuales **2 puramente LLM, 2 híbridos y 8 deterministas**. De los dos LLM puros, uno es la descripción del PR — prosa que ningún sistema consume. El único punto donde el modelo es estructuralmente imprescindible es la recolección de requisitos.
- **T0.1 completado:** repo inicializado, estructura de carpetas (`ado/`, `requisitos/`, `seleccion/`, `parametros/`, `render/`, `orquestacion/`, `runs/`), `.gitignore`, `requirements.txt`, `.env` + `.env.example`, `AGENTS.md`.

### Detalles de T0.1 que no son obvios
- **Bug heredado y NO arrastrado:** el `.gitignore` de `poc-agentes` tiene `.env.DS_Store` en una sola línea (dos entradas pegadas sin salto), así que `.DS_Store` nunca se ha estado ignorando allí. Aquí están separadas.
- **Dos dependencias usadas y no declaradas en `poc-agentes`:** `pydantic` (en `clasificador_hibrido.py`) y `python-dotenv` (en casi todos los `__main__`). Entraron como transitivas de `openai`/`agent-framework` y funcionan por accidente. Aquí van declaradas en `requirements.txt`.
- **`runs/` se ignora con `runs/*` + `!runs/.gitkeep`**, no con `runs/` a secas — así la carpeta existe al clonar sin versionar las ejecuciones.
- **`MODEL_BACKEND=lmstudio` es el valor por defecto de `.env.example`**, al revés que en `poc-agentes` (donde Gemini era el documentado y LM Studio la alternativa). Motivo: el recolector conversacional de T2.2 gasta una petición de Gemini por turno y el tier gratuito son 20 al día — una sola conversación de prueba la agota.
- `.env` se creó como copia de `.env.example` con los valores vacíos. **Las claves del modelo no se copiaron desde `poc-agentes/.env`**: el usuario decide si las duplica o usa otras.

### Excepción aplicada a la regla explicar → implementar → resumir
T0.1 se hizo de una pasada en vez de fichero a fichero: carpetas vacías, `.gitignore` y `requirements.txt` no tienen concepto que aprender. La cadencia normal (teoría → confirmar → un fichero → confirmar) aplica desde T0.2. Anotado también en `AGENTS.md`.

### Pendiente
- `.env`: ADO ya funcionando. **Faltan las del modelo** (`GEMINI_API_KEY` vacía; `MODEL_BACKEND=lmstudio` por defecto, así que no bloquea hasta el Bloque 2).
- Primer commit: no hecho todavía, se deja a decisión del usuario.
