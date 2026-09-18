# Plan: PoC de clonado de pipelines con entrada conversacional y Azure DevOps

**Qué es esto.** La segunda iteración de `poc-agentes`. La primera demostró el mecanismo (bucle de tool calling, salida estructurada, reglas antes que LLM, renderizado determinista, traza en `runs/`) sobre repos de mentira en disco. Esta segunda demuestra **el producto**: el usuario pide en lenguaje natural la pipeline que necesita, y el sistema deja un Pull Request abierto en Azure DevOps.

**Los dos cambios de fondo respecto a `poc-agentes`:**

1. **La entrada deja de ser un repo y pasa a ser una conversación.** Antes el flujo arrancaba con `fingerprint(ruta)`: señales deterministas extraídas de ficheros. Ahora arranca con un chat en el que el usuario describe requisitos. Eso mueve el LLM del *medio* del flujo (desambiguar un arquetipo) al *principio* (convertir prosa en datos estructurados), que es un trabajo distinto y más difícil.
2. **El catálogo y los destinos dejan de estar en disco y pasan a estar en Azure DevOps.** Y el artefacto final deja de ser un fichero en `runs/` para ser una rama, un commit y un PR reales.

**Decisiones ya tomadas, no las re-abras sin motivo:**

| Decisión | Elegido | Descartado |
|---|---|---|
| Qué se deja en el repo destino | **Pipeline hijo con `extends`** + pin por tag al repo de plantillas | Copia física de los ficheros de plantilla |
| Organización del catálogo en ADO | **Un repo de plantillas, una carpeta por plantilla** (`catalog/<id>/manifest.yaml`) | Un repo por plantilla |
| Acceso a ADO | **Organización propia + PAT, real desde el Bloque 0** | Backend local simulado |
| Dónde vive el pipeline generado | **Un repo `pipelines` por team project**, con una carpeta por repo de código | Un `azure-pipelines.yml` en cada repo de código |
| Dónde viven las variables por entorno | **En el repo de código**, `vars/{common,dev,acc,pro}.yml` | Junto al pipeline, en `pipelines` |
| Contenido inicial de las variables | **El manifest declara un juego inicial**; el usuario puede ampliarlo después, y una re-ejecución **no puede pisarlo** | Esqueleto vacío / recogida libre en el chat |
| Checkout cruzado | **En alcance**: las plantillas del catálogo se actualizan para hacer `checkout` del repo de código | Dejar YAML válido que no construye nada |

Las dos primeras hacen la plataforma gobernable: el pipeline generado son ~20 líneas que apuntan a una plantilla versionada, así que cambiar la plantilla actualiza a todos.

**La consecuencia incómoda del reparto en dos repos, y hay que mirarla de frente:** el artefacto de una ejecución cae en **dos repositorios distintos**, luego son **dos Pull Requests** y **no pueden ser atómicos**. Si se mergea el pipeline y no las variables, el pipeline queda roto — referencia `vars/common.yml@codigo`, que todavía no existe. De ahí tres reglas que atraviesan todo el plan:

1. **Orden de merge: primero las variables, después el pipeline.** Escrito explícitamente en la descripción de ambos PR, con enlace cruzado entre ellos.
2. **Los dos PR se crean o no se crea ninguno.** Si el segundo falla, el primero se abandona (rama borrada), no se deja a medias.
3. **Regenerar hace merge, no sobrescribe.** El usuario puede ampliar los `vars/*.yml` a mano después del alta; una segunda ejecución que los volcara enteros destruiría ese trabajo.

**El reparto final:**

```
pipelines/                              <- PR #2 (se mergea el SEGUNDO)
└── demo-servicio-java/
    └── azure-pipelines.yml

demo-servicio-java/                     <- PR #1 (se mergea el PRIMERO)
├── pom.xml
└── vars/
    ├── common.yml
    ├── dev.yml
    ├── acc.yml
    └── pro.yml
```

Y el pipeline generado deja de ser un `extends` pelado: al vivir lejos del código necesita declararlo como recurso, tanto para construirlo como para leer sus variables.

```yaml
resources:
  repositories:
    - repository: templates
      type: git
      name: POC-MAF/plantillas-ci
      ref: refs/tags/v1.0.0
    - repository: codigo            # el repo que se construye
      type: git
      name: POC-MAF/demo-servicio-java
      ref: refs/heads/main

variables:
  - template: vars/common.yml@codigo
  - template: vars/${{ parameters.entorno }}.yml@codigo

extends:
  template: catalog/ci-java-container/template.yaml@templates
  parameters:
    isDocker: true
    javaVersion: '17'
    appVersion: 1.0.0
```

**Dos cosas de este YAML hay que verificarlas contra ADO antes de construir encima, no darlas por buenas:** que `variables:` con `- template: ...@codigo` conviva con `extends:` en el mismo fichero, y que un pipeline alojado en `pipelines` pueda dispararse por cambios en el repo `codigo` (trigger sobre recurso de repositorio). Ninguna de las dos está comprobada en la organización todavía.

---

# La pregunta que esta PoC responde: ¿qué necesita LLM y qué no?

Este es el entregable conceptual del ejercicio. El flujo completo, paso a paso, con la etiqueta puesta:

| # | Paso | Naturaleza | Por qué |
|---|---|---|---|
| 1 | Recoger requisitos en chat | **LLM** | El input es prosa abierta. No hay reglas posibles. Es el único punto del sistema con entrada genuinamente no estructurada. |
| 2 | Validar que los requisitos están completos | Determinista | JSON Schema sobre el objeto `Requisitos`. Sabe qué falta sin preguntarle a nadie. |
| 3 | Descubrir el catálogo en ADO | Determinista | REST API + parseo de YAML, anclado por tag. |
| 4 | Elegir plantilla | **Híbrido** | Reglas sobre los requisitos primero. El LLM solo entra si 0 o >1 candidatos. |
| 5 | Derivar parámetros que el requisito ya fija | Determinista | Mapeo declarado en el manifest (`derived_from`). Si el usuario dijo "Java 17", `javaVersion` no lo decide un modelo. |
| 6 | Rellenar parámetros sin requisito | **LLM** + validar-y-reintentar | Solo los huecos. Contra el `parameters.schema.json` real. |
| 7 | Resolver las variables de entorno con los valores por defecto del manifest | Determinista | El juego inicial está **declarado** en la plantilla. No es trabajo para un modelo. |
| 8 | Leer los `vars/*.yml` que ya existan en el repo de código | Determinista | Para poder hacer merge en vez de sobrescribir. |
| 9 | Fusionar lo declarado con lo que el humano añadió | Determinista | Unión de mapas YAML, ganando siempre el valor existente. |
| 10 | Renderizar el pipeline hijo | Determinista | `yaml.dump`. El modelo nunca escribe el artefacto que se despliega. |
| 11 | Renderizar los cuatro `vars/*.yml` | Determinista | Ídem. |
| 12 | Decidir `add` vs `edit` por cada fichero, en los dos repos | Determinista | Existe o no existe. Equivocarse es un 400 de ADO. |
| 13 | Confirmación humana antes de escribir | Humano | Última puerta. |
| 14 | Rama + commit en el repo de código (variables) | Determinista | Pushes API. |
| 15 | Rama + commit en `pipelines` | Determinista | Pushes API. |
| 16 | Redactar las descripciones de los dos PR | **LLM** | Prosa para un revisor humano, con el orden de merge y el enlace cruzado. |
| 17 | Crear los dos PR | Determinista | Pull Requests API. Los dos o ninguno. |
| 18 | Registrar la ejecución | Determinista | Ficheros en `runs/` + las dos URLs. |

**Dos pasos puramente LLM (1 y 16), dos híbridos (4 y 6), catorce deterministas.** Y de los dos LLM puros, uno (el 16) es texto que ningún sistema consume: si sale mal, un humano lo lee y lo corrige. El único punto donde el modelo es imprescindible y su salida alimenta al resto es el paso 1.

Ese es el titular, y **el reparto mejoró al concretar el diseño, no empeoró**: los pasos 7 a 12, que son los que añadió el modelo de variables por entorno, son todos deterministas. Cuanto más se concreta el contrato de la plantilla, menos queda por adivinar.

La segunda mitad —desde que existe un `Requisitos` validado hasta que hay dos PR abiertos— es un programa normal, auditable línea a línea, con el modelo tocando exactamente dos cosas y ambas validadas contra un esquema que escribió una persona.

---

# Arquitectura

```
                        ┌─ LLM ─┐
  usuario ──chat──►  RECOGIENDO_REQUISITOS ◄──┐  (multiturno, slot filling)
                             │                │
                             ▼                │ falta un slot
                     requisitos.json ─────────┘
                             │
                             ▼ (determinista de aquí en adelante, salvo donde se indique)
                    DESCUBRIENDO_CATALOGO ──► plantillas-ci @ tag
                             │
                             ▼
                    SELECCIONANDO_PLANTILLA  ◄── reglas; LLM solo si ambiguo
                             │
                    ┌────────┴────────┐
                    ▼                 ▼
          CONFIRMANDO (humano)   REQUIERE_REVISION_HUMANA (terminal)
                    │
                    ▼
            GENERANDO_PARAMETROS  ◄── derivación det. + LLM solo para huecos
                    │
                    ▼
            RESOLVIENDO_VARIABLES ──► defaults del manifest
                    │                 + lectura de los vars/*.yml existentes
                    │                 + MERGE (gana lo que ya estaba)
                    ▼
               RENDERIZANDO ──► 1 pipeline hijo + 4 vars/*.yml
                    │
                    ▼
            PLANIFICANDO_CAMBIOS ──► add vs edit por fichero, en los DOS repos
                    │
                    ▼
              CONFIRMANDO_PUSH (humano)  ◄── última puerta antes de escribir
                    │
                    ▼
        ┌───────────┴───────────┐
        ▼                       ▼
  rama+commit en           rama+commit en
  <repo-codigo>            pipelines
  (vars/*.yml)             (<repo>/azure-pipelines.yml)
        │                       │
        └───────────┬───────────┘
                    ▼
               REDACTANDO_PRS  ◄── LLM (con orden de merge y enlace cruzado)
                    │
                    ▼
               CREANDO_PRS ──► los dos, o ninguno (rollback de ramas)
                    │
                    ▼
                 COMPLETADO ──► runs/{timestamp}/ + las dos URLs
```

## Estructura del repo

```
poc-agentes-maf/
├── AGENTS.md
├── docs/
│   ├── Plan poc-ado.md
│   └── notas-agentes/Bitacora-de-sesiones.md
├── ado/                         # TODO lo que habla con Azure DevOps, aislado
│   ├── cliente.py               # ✅ T1.1 - auth PAT, dos ámbitos de URL, diagnóstico
│   ├── catalogo.py              # ✅ T1.2 - descubrir plantillas, anclado por tag
│   ├── humo.py                  # ✅ T0.2 - comprobación de acceso
│   ├── sembrar.py               # ✅ T0.3 - escenario de pruebas (idempotente)
│   ├── destinos.py              # T1.3 - repo pipelines + qué ficheros ya existen
│   └── cambios.py               # Bloque 4 - pushes y pull requests
├── llm/
│   └── cliente.py               # ✅ único sitio que construye un cliente de modelo
├── requisitos/
│   ├── esquema.py               # ✅ T2.1 - modelo Requisitos (determinista, sin red)
│   ├── agente_extractor.py      # ✅ T2.2 - texto → dict parcial (LLM)
│   └── agente_recolector.py     # ✅ T2.2 - el bucle de turnos
├── seleccion/
│   ├── reglas.py
│   └── agente_hibrido.py
├── parametros/
│   ├── agente_generador.py      # parámetros del extends
│   └── variables.py             # vars/*.yml: defaults + merge (sin LLM)
├── render/
│   ├── renderizador.py          # el pipeline hijo
│   └── vars.py                  # los cuatro ficheros de variables
├── orquestacion/
│   ├── estados.py               # enum + tabla determinista/LLM
│   └── flujo.py                 # el workflow (MAF)
├── chat.py
└── runs/
```

**Convención de nombres (2026-09-17):** un fichero `agente_*` es un fichero que, **al ejecutarse, acaba llamando a un modelo**. Es transitivo: `agente_recolector.py` no importa `llm/`, pero sí a `agente_extractor.py`. Lo comprueba `comprobar_agentes.py`, que recorre el grafo de imports y devuelve error si alguien se salta la regla. Así "¿qué partes pueden alucinar?" se responde con un `ls`.

**El límite importante es `ado/`.** Todo lo que sabe de Azure DevOps vive ahí y expone funciones de dominio (`descubrir_plantillas()`, `crear_pull_request(...)`). El resto del sistema no sabe que existe ADO. Si mañana hay que soportar GitHub, se escribe `github/` con la misma superficie y no se toca nada más.

## Qué se reutiliza de `poc-agentes` y qué se tira

**Se copia tal cual:**
- `render/renderizador.py` — ya genera exactamente el `extends` + pin por tag que necesitamos.
- El formato de `manifest.yaml` + `parameters.schema.json` + `template.yaml` de las tres plantillas (`ci-java-container`, `ci-dotnet`, `ci-node-container`). Se suben a ADO en el Bloque 0.
- El patrón `guardar_run()` — una carpeta por ejecución con la traza completa.
- `construir_cliente_openai()` y el `MODEL_BACKEND` (gemini | lmstudio). La cuota gratuita de Gemini sigue siendo de 20 peticiones/día; con un chat multiturno se agota **mucho** más rápido que antes. Aquí LM Studio deja de ser una comodidad y pasa a ser el backend de trabajo.

**Se adapta:**
- El mecanismo validar-y-reintentar de `clasificador_hibrido.py` / `parameterizer.py` — el patrón es el mismo, cambia el objeto que se valida.
- `clasificador_reglas.py` — las reglas siguen existiendo, pero operan sobre `Requisitos` (lo que dijo el usuario) en vez de sobre `context.json` (lo que había en el disco).

**Muere:**
- `context/fingerprint.py` — ya no hay repo que inspeccionar en la entrada. Vuelve como opcional en el Bloque 6.
- Los fixtures `repo-java/`, `repo-dotnet/`, `repo-node/`, `repo-ambiguo/`.

---

# Bloque 0 · Proyecto y acceso real a ADO (2–3 h)

## T0.1 — Esqueleto del proyecto  ✅
Estructura de carpetas de arriba, `.venv`, `requirements.txt` (`openai`, `pyyaml`, `jsonschema`, `pydantic`, `httpx`, `python-dotenv`, `agent-framework-*`), `.gitignore` con `.env` desde el primer commit. `AGENTS.md` heredando las reglas de `poc-agentes` (explicar → implementar → resumir, bitácora, teoría en Obsidian).

**Criterio de éxito:** `git status` limpio con `.env` ya creado y no trackeado.

## T0.2 — PAT y llamada de humo a ADO  ✅
PAT con los scopes mínimos: **Code (Read & Write)** y **Pull Request Contribute**. Nada de `Full access`. En `.env` como `AZDO_PAT`, junto a `AZDO_ORG` y `AZDO_PROJECT`.

Autenticación: Basic con usuario vacío y el PAT como contraseña — `Authorization: Basic <base64(":" + PAT)>`.

Script de humo: listar los repos del proyecto.
`GET https://dev.azure.com/{org}/{project}/_apis/git/repositories?api-version=7.1`

**Criterio de éxito:** el script imprime los nombres de tus repos. Si devuelve HTML de login en vez de JSON, el PAT o los scopes están mal — no sigas.

**Trampa conocida:** un PAT inválido en ADO no siempre devuelve 401; a veces devuelve 200 con la página de login. Comprueba el `Content-Type`, no solo el código de estado.

## T0.3 — Sembrar el escenario en ADO  ✅ (ampliación pendiente)
Hecho: repo **`plantillas-ci`** con las tres plantillas y tag `v1.0.0`, y los destinos **`demo-servicio-java`** y **`demo-api-node`** con commit inicial. `ado/sembrar.py`, idempotente.

**Ampliación pendiente tras el cambio de diseño:** falta crear el repo **`pipelines`** del team project, con un commit inicial (un `README.md` explicando la convención) para que tenga rama `main` y, por tanto, `oldObjectId` del que partir. Sin él, el Bloque 4 no tiene dónde escribir.

**Criterio de éxito:** `ado.humo` lista cuatro repos de trabajo — `plantillas-ci`, `pipelines`, `demo-servicio-java`, `demo-api-node` — y los tres últimos tienen rama `main`.

**Qué NO hacer:** no configures todavía ninguna pipeline en ADO ni intentes ejecutarla. Que el YAML sea válido y esté en su sitio es suficiente. Ejecutar de verdad es el Bloque 6.

---

# Bloque 1 · El catálogo vive en ADO (3 h)

## T1.1 — Cliente REST mínimo  ✅
`ado/cliente.py`: una clase con la URL base, la cabecera de auth y un `get`/`post` que levanta excepción con el cuerpo del error incluido (los errores de ADO traen un `message` legible; perderlo te hará perder una tarde).

**Criterio de éxito:** un 404 provocado a propósito imprime el mensaje de ADO, no un `KeyError`.

## T1.2 — Descubrimiento del catálogo  ✅
`ado/catalogo.py`. Dos llamadas:
- Listar el árbol: `GET .../_apis/git/repositories/{repoId}/items?scopePath=/catalog&recursionLevel=full&versionDescriptor.version=v1.0.0&versionDescriptor.versionType=tag&api-version=7.1`
- Leer un fichero: el mismo endpoint con `path=...&includeContent=true&$format=json`

Devuelve una lista de `PlantillaDisponible` (id, versión, `applies_to`, `parameters`, `selection_rationale`, ruta del `template.yaml`), construida parseando los `manifest.yaml` reales.

**Cachea el resultado en memoria durante la sesión.** Descubrir el catálogo son N+1 llamadas HTTP y el flujo lo necesita varias veces.

**Criterio de éxito:** una función devuelve las tres plantillas leídas de ADO, y el `id` de cada una coincide con el de su carpeta. Si no coinciden, el catálogo miente: falla ruidosamente.

**Por qué importa para la oferta:** el catálogo es un dato versionado en Git, no una tabla en el código del agente. Añadir una plantilla es un PR al repo de plantillas, no un despliegue del sistema.

## T1.3 — El repo `pipelines` y el inventario de ficheros
**Reescrita tras el cambio de diseño.** La versión original listaba repos candidatos a destino para que el usuario eligiera. Eso ya no aplica: el destino del pipeline es **siempre** el repo `pipelines` del team project, y el de las variables es siempre el repo de código. No hay nada que elegir.

Lo que **sí** sobrevive, y ahora importa más: saber **qué ficheros existen ya**, porque eso decide el `changeType` de cada cambio del push (`add` si es nuevo, `edit` si ya está) y equivocarse es un 400 de ADO. Ya no es un fichero, son cinco, repartidos en dos repos, y cada uno se resuelve por separado — el `common.yml` puede existir mientras el pipeline no.

`ado/destinos.py`:
- `repo_pipelines(cliente)` — resuelve el repo `pipelines`, con un error claro si no existe (igual que hace `catalogo.id_repo`).
- `inventario(cliente, repo_codigo)` — devuelve, para las cinco rutas del alta, si existen ya y con qué contenido:

| Repo | Ruta | Uso del contenido |
|---|---|---|
| `pipelines` | `/<repo_codigo>/azure-pipelines.yml` | solo `add` vs `edit` |
| código | `/vars/common.yml` | `add`/`edit` **y** merge |
| código | `/vars/dev.yml` | ídem |
| código | `/vars/acc.yml` | ídem |
| código | `/vars/pro.yml` | ídem |

Los `vars/*.yml` se traen con contenido, no solo su existencia: hacen falta para el merge del paso 9 (no pisar lo que un humano añadió). El pipeline no, porque se regenera entero desde la plantilla.

Un 404 al pedir un fichero significa "no existe", y es un caso **normal**, no un error: hay que capturarlo y traducirlo, no dejarlo subir como `ErrorAdo`.

**Criterio de éxito:** sobre el escenario recién sembrado, el inventario de `demo-servicio-java` dice que las cinco rutas faltan. Tras una ejecución del flujo, dice que las cinco existen y devuelve el contenido de las cuatro de variables.

---

# Bloque 2 · La entrada conversacional (4–5 h)  ✅ COMPLETO

Este es el bloque nuevo de verdad. Todo lo demás es traslado de lo que ya sabes hacer.

## T2.1 — El objeto `Requisitos`  ✅
`requisitos/esquema.py`: un modelo Pydantic con los campos que el flujo necesita para elegir plantilla, rellenar parámetros y resolver variables.

```
tecnologia:        Literal["java", "dotnet", "node"]   obligatorio
repo_codigo:       str                                 obligatorio  (repo en ADO que se construye)
version_lenguaje:  str | None       ("17", "8.0", "20")
contenedor:        bool | None      ¿se publica imagen?
version_app:       str | None       semver
entornos:          list[Literal["dev","acc","pro"]]    default ["dev","acc","pro"]
variables_extra:   dict[str, dict]  {entorno: {nombre: valor}}, lo que el usuario añada
notas:             str | None       texto libre que no encaja en ningún slot
```

`repo_codigo` es obligatorio y no tiene default: sin él no se sabe ni qué construir, ni dónde poner las variables, ni qué carpeta usar dentro de `pipelines`. Y **se valida contra ADO**, no solo contra el tipo — que el usuario escriba un nombre de repo que no existe tiene que detectarse en la conversación, no en el push.

`entornos` sí tiene default (los tres) porque es la convención de la casa; el usuario solo lo toca si su caso es distinto.

`variables_extra` recoge lo que el usuario quiera añadir por encima del juego inicial que declara el manifest. Va anidado por entorno porque el mismo nombre puede tener valores distintos en dev y en pro.

**La pieza clave no es el modelo, es `slots_que_faltan(requisitos) -> list[str]`:** una función determinista que dice qué falta para poder avanzar. El LLM **no** decide cuándo la conversación ha terminado. Lo decide esta función.

**Criterio de éxito:** un `Requisitos` a medias devuelve exactamente la lista de campos obligatorios vacíos; uno completo devuelve `[]`; y un `repo_codigo` inexistente en ADO se rechaza con un mensaje que lista los repos que sí hay.

## T2.2 — Recolector conversacional (slot filling)  ✅
`requisitos/agente_recolector.py`. En cada turno:
1. Se le pasan al modelo el historial, el estado actual del `Requisitos` y la lista de slots que faltan.
2. Devuelve un JSON: `{"requisitos_actualizados": {...}, "pregunta_al_usuario": "..."}`.
3. Se valida contra el esquema. Si no valida, se reintenta una vez con el error como feedback (patrón de T1.2 de la PoC anterior).
4. Se **fusiona** sobre el estado previo; nunca se sustituye. Un modelo que en el turno 3 "olvida" lo que dijiste en el turno 1 no puede borrártelo.

**Criterio de éxito:** una conversación de tres turnos ("necesito una pipeline para un servicio Java" → "¿versión?" → "17, y sí, va en Docker") deja un `Requisitos` correcto, y la función de T2.1 devuelve `[]`.

**Lo que aprendes aquí, y es lo que se cuenta en la reunión:** un agente conversacional fiable no es un modelo con buena memoria. Es un objeto de estado tipado que vive en tu código y un modelo al que solo se le permite proponer actualizaciones parciales sobre él, validadas antes de aplicarse. La conversación es la interfaz; el estado es tuyo.

**Trampas que vas a encontrar (anótalas cuando pasen):**
- El modelo rellena slots que el usuario no mencionó, inventándolos de forma plausible. Ya te pasó en `poc-agentes` con el arquetipo alucinado. Mitigación: pedirle explícitamente que deje `null` lo no dicho, y revisar en la confirmación de T2.3.
- El modelo hace tres preguntas a la vez. Instrucción explícita: una por turno.
- El usuario dice algo fuera de dominio ("es un proyecto de Go"). Debe salir por `notas`, no forzarse a la enumeración.

## T2.3 — Cierre y confirmación  ✅
Cuando no faltan slots, se le muestra al usuario el `Requisitos` completo en texto plano y se le pide confirmación explícita. Si dice que no, vuelve a T2.2 con su corrección.

**Criterio de éxito:** el flujo no avanza sin un "sí" del usuario, y un "cambia la versión a 21" vuelve atrás y la cambia sin perder lo demás.

---

# Bloque 3 · La orquestación como máquina de estados (4 h)

## T3.1 — Estados y transiciones explícitos  ✅
`orquestacion/estados.py`: un `Enum` con los estados del diagrama y, al lado de cada uno, **declarado en el código, no en un comentario**, si es determinista, LLM o híbrido, y quién lo consume.

```python
class Estado(StrEnum):
    RECOGIENDO_REQUISITOS = "recogiendo_requisitos"
    ...

NATURALEZA = {
    Estado.RECOGIENDO_REQUISITOS: Naturaleza.LLM,
    Estado.DESCUBRIENDO_CATALOGO: Naturaleza.DETERMINISTA,
    ...
}
```

Suena a burocracia y es la mitad del valor del ejercicio: con ese diccionario puedes imprimir automáticamente la tabla de arriba, contar cuántas llamadas a LLM hizo una ejecución concreta, y enseñar en la reunión un dato medido en vez de una afirmación.

`orquestacion/agente_flujo.py`: el workflow con MAF (`WorkflowBuilder`, un nodo por estado, transiciones condicionadas), igual que `agente_workflow.py` pero con un objeto de estado compartido en vez de un mensaje que se va transformando.

**RESUELTO el 2026-09-17, verificado contra `agent-framework-core 1.18.0`.** El plan avisaba de que el workflow en grafo de `poc-agentes` era un DAG de un disparo y que este flujo necesita pausar y esperar al humano en tres sitios, y dejaba escrito un plan B. **No hace falta: MAF tiene human-in-the-loop de primera clase.**

El mecanismo, comprobado con un ejemplo mínimo antes de construir encima:
- Un nodo llama a `await ctx.request_info(datos, tipo_respuesta)` y el workflow **se suspende**: la ejecución termina en `WorkflowRunState.IDLE_WITH_PENDING_REQUESTS` sin haber hecho nada más.
- Fuera se leen las peticiones con `resultado.get_request_info_events()` (traen `request_id` y `data`).
- Se reanuda con `workflow.run(responses={request_id: valor}, checkpoint_storage=...)`, y la respuesta entra por el método marcado con `@response_handler(peticion, respuesta, ctx)`.

**`RequestInfoExecutor` —lo que dice la documentación antigua— NO existe en esta versión.** Confirmar contra la versión instalada antes de fiarse de cualquier ejemplo de blog sigue siendo la regla.

Consecuencia de diseño que va más allá de la comodidad: quien reanuda puede ser **otro proceso, otro día**, leyendo el checkpoint. "Nada se escribe sin un sí" pasa de ser una convención del código a una garantía estructural.

**Criterio de éxito:** una ejecución imprime la secuencia de estados recorridos con su naturaleza al lado, y el recuento de llamadas al LLM.

## T3.2 — Selección de plantilla híbrida  ✅
`seleccion/reglas.py` + `seleccion/hibrido.py`. Reglas sobre `Requisitos` contra el `applies_to` de los manifests **descubiertos en ADO** (no una lista hardcodeada). Tres estados como antes: `decidido` / `ambiguo` / `desconocido`. El LLM entra solo en los dos últimos, con la lista cerrada de ids del catálogo real, salida estructurada, reintento y umbral 0.7.

**Criterio de éxito:** `tecnologia: "java"` + `contenedor: true` resuelve por reglas con cero llamadas al modelo. Un requisito raro (`notas: "es un monorepo con Java y Node"`, `tecnologia` incierta) baja por la rama del LLM, y con umbral 0.99 escala a revisión humana.

## T3.3 — Parámetros: derivar primero, preguntar al modelo después
`parametros/generador.py`. **Cambio real respecto a `poc-agentes`:** allí todos los parámetros los generaba el LLM. Aquí, primero se recorre `manifest.parameters` y se rellena de forma determinista todo lo que el `Requisitos` ya fija (vía `derived_from`). El LLM recibe **solo los parámetros que siguen vacíos**, y el resultado se valida contra el `parameters.schema.json` completo.

**Criterio de éxito:** con un `Requisitos` completo (java/17/docker/1.0.0) el LLM no se llama ni una vez, porque no queda ningún hueco. Con uno parcial, se llama solo para los que faltan y lo ves en el log.

**Por qué esto importa más de lo que parece:** es la demostración cuantificada de que el modelo se retira solo a medida que los datos de entrada mejoran. Con un usuario que sabe lo que quiere, el sistema es puro código.

## T3.4 — Variables por entorno: declarar, leer, fusionar  ✅
**Tarea nueva**, consecuencia del modelo de variables. `parametros/variables.py`. Cero LLM: los tres pasos son deterministas.

**Decisión del usuario (2026-09-18) que define la tarea:** *"No necesito que infiera ninguna variable: la que no esté, se deja el hueco. Siempre se crean los 4 ficheros, uno por entorno y el común, porque esto lo tendrán todos los proyectos y todas las tecnologías."*

Consecuencia: **cero LLM en este paso**, y el `environment_variables` del manifest deja de ser necesario para arrancar. Un valor de variable de producción no es algo que un modelo pueda deducir —no está en los requisitos ni en el repo—, y una invención plausible es **peor** que un hueco, porque el hueco se ve y el valor inventado no.

Los cuatro ficheros se crean **siempre**. Que un entorno no tenga variables propias todavía no es motivo para no crear el suyo: el fichero es el sitio donde alguien las pondrá, y que exista desde el alta evita la pregunta "¿dónde va esto?".

**Los tres pasos, todos deterministas:**
1. **Resolver** lo que se sabe sin inferir: sale de `Requisitos` (`serviceName` ← `repo_codigo`, `appVersion` ← `version_app`) más lo que el usuario dictó en `variables_extra`. Lo que no se sepa queda como valor nulo con un comentario `TODO`.
2. **Leer** los `vars/*.yml` que ya existan en el repo de código (los trae `ado/destinos.py`).
3. **Fusionar, ganando siempre lo existente.** Incluye las variables que un humano añadió y que el sistema no conoce: no se borran por no estar previstas.

**Criterio de éxito:** (1) con el repo de código limpio se generan los cuatro ficheros con los defaults del manifest; (2) si se siembra a mano un `pro.yml` con un valor cambiado y una variable inventada, una segunda ejecución conserva **las dos cosas**; (3) una variable `secret: true` nunca aparece con valor literal, ni siquiera si el usuario lo dictó en el chat.

---

# Bloque 4 · Escribir en Azure DevOps (5–6 h)

El bloque más "de fontanería" y el que convierte la PoC en demo. Cero IA hasta T4.5.

## T4.0 — ~~Actualizar las plantillas para el checkout cruzado~~ · FUERA DE ALCANCE
**Descartada el 2026-09-17 por decisión del usuario.** Que las plantillas compilen —el `checkout` cruzado, las rutas relativas al repo checkouteado, los triggers entre repos— es **diseño de plantillas, y es trabajo de otro proyecto**.

El objetivo de esta PoC es más estrecho y conviene no perderlo de vista: **tener plantillas en un repositorio y clonarlas**, dejando en el repo de código sus variables de entorno (el despliegue necesita variables por entorno para que las soluciones accedan a ellas).

**Qué implica, dicho explícitamente para no engañarse:**
- El pipeline generado será **YAML válido y coherente**, con su `resources.repositories` y sus referencias a `vars/*.yml@codigo`. **No se comprueba que Azure Pipelines lo ejecute.** Mismo criterio que `poc-agentes` ("no tiene que ejecutarse en ningún sitio: tiene que ser YAML válido y coherente").
- Las tres `template.yaml` del catálogo **se dejan como están**. Siguen asumiendo que el pipeline vive junto al código, y eso es correcto en su proyecto de origen.
- **Supuesto asumido, no verificado:** que `variables: - template: vars/x.yml@codigo` conviva con `extends:` en el mismo pipeline. Si resultara falso, la corrección es una línea del renderizador (cargar las variables desde dentro de la plantilla), no un rediseño. Por eso se asume en vez de bloquear.
- **Riesgo aceptado:** el disparo automático del pipeline por cambios en el repo de código no se diseña ni se prueba.

**Lo que SÍ sigue en alcance** y estaba mezclado en esta tarea: el renderizador tiene que emitir las rutas reales del escenario — `catalog/<id>/template.yaml@templates` (ya resuelto, es `PlantillaDisponible.ruta_template`) y `POC-MAF/plantillas-ci` en vez de los `MiOrg/MiRepoDePlantillas` heredados. Eso no es compatibilidad de plantillas, es que lo que generamos apunte a donde de verdad está. Pasa al Bloque 3, con el renderizado.

## T4.1 — Rama y commit en una sola llamada
`ado/cambios.py`. **Ya probado en T0.3:** la Pushes API crea rama y commit de una vez, sin clonar nada, y admite varios ficheros en el mismo commit (allí se subieron 10).

`POST .../_apis/git/repositories/{repoId}/pushes?api-version=7.1`
```json
{
  "refUpdates": [{ "name": "refs/heads/feat/pipeline-ci", "oldObjectId": "<sha de la rama base>" }],
  "commits": [{
    "comment": "feat: variables de pipeline por entorno",
    "changes": [{
      "changeType": "add",
      "item": { "path": "/vars/common.yml" },
      "newContent": { "content": "<el YAML>", "contentType": "rawtext" }
    }]
  }]
}
```

El `oldObjectId` sale de `GET .../refs?filter=heads/main`. El `changeType` de **cada** cambio sale del inventario de T1.3.

Nombre de rama y mensaje de commit: **deterministas, por convención**. No es trabajo para un modelo.

**Criterio de éxito:** una rama nueva en cada uno de los dos repos, con sus ficheros. Comprobado en la web de ADO, no solo por el 201.

## T4.2 — Los dos Pull Requests, o ninguno
`POST .../_apis/git/repositories/{repoId}/pullrequests?api-version=7.1` con `sourceRefName`, `targetRefName`, `title`, `description`. La URL para un humano se compone como `https://dev.azure.com/{org}/{project}/_git/{repo}/pullrequest/{id}`.

**Lo específico de este diseño:** son dos PR en dos repos y no hay transacción posible. Reglas:
- **Orden de creación: primero las variables (repo de código), después el pipeline.** Así, si el segundo falla, lo que queda abierto es el PR inofensivo.
- **Si el segundo falla, se revierte el primero**: se abandona el PR y se borra la rama. Dejar medio alta abierta es peor que no haber hecho nada, porque el siguiente intento no sabe en qué estado está.
- **Las descripciones se enlazan entre sí** y dicen el orden de merge: primero variables, después pipeline. Un pipeline mergeado sin sus variables está roto.

**Criterio de éxito:** los dos PR se abren enlazados; y forzando un fallo en el segundo (p.ej. un nombre de rama inválido), el primero desaparece y ADO queda como estaba.

## T4.3 — La puerta humana y la idempotencia
- **Confirmación explícita antes de escribir.** El estado `CONFIRMANDO_PUSH` muestra los cinco ficheros, los dos repos y los nombres de rama, y espera un "sí". Ninguna escritura ocurre sin ella.
- **Idempotencia sobre dos repos.** Si las ramas ya existen, reutilizarlas o sufijar. Si ya hay PR abiertos de esas ramas, devolverlos en vez de crear otros.

**Criterio de éxito:** ejecutar el flujo dos veces con los mismos requisitos no deja PR duplicados en ninguno de los dos repos, y responder "no" en la confirmación no deja **nada** en ADO.

## T4.4 — Verificar el merge no destructivo, de verdad
**Tarea nueva.** El criterio de T3.4 se prueba con ficheros en memoria; esto lo prueba contra ADO.

Procedimiento: ejecutar el alta completa, editar a mano en ADO un `vars/pro.yml` (cambiar un valor y añadir una variable que no está en el manifest), y **volver a ejecutar el flujo entero**. El PR resultante debe conservar las dos ediciones.

**Criterio de éxito:** el diff del segundo PR no toca lo que el humano cambió. Si lo toca, la herramienta no es segura de re-ejecutar y eso es un defecto de diseño, no un detalle.

## T4.5 — Las descripciones de los PR (aquí sí, LLM)
Hereda de `catalog/justificador.py`. Markdown corto para el revisor: qué plantilla se eligió y por qué, qué parámetros se aplicaron y de qué requisito sale cada uno, qué variables se generaron y cuáles se respetaron por existir ya, qué controles incluye la plantilla, qué se asumió y qué vigilar. Más el enlace al PR hermano y el orden de merge.

Texto libre, sin validar-y-reintentar: no hay esquema que cumplir y un humano lo lee antes de aprobar.

**Criterio de éxito:** abres cualquiera de los dos PR en ADO y la descripción se sostiene sola, incluida la advertencia de en qué orden mergear.

---

# Bloque 5 · Traza y conclusión (2 h)

## T5.1 — `runs/` con los dos PR dentro
Una carpeta por ejecución: `requisitos.json`, `transcripcion.md` (la conversación entera), `seleccion.json` (plantilla, origen reglas/LLM, confianza), `parametros.json` con **el origen de cada valor** (derivado del requisito / generado por el LLM), `variables.json` con el origen de cada variable (**default del manifest / dictada por el usuario / conservada porque ya existía**), los cinco ficheros generados, `descripcion-pr-*.md` y `metadata.json` (backend, modelo, nº de llamadas al LLM, estados recorridos). Y `resultado.json` con **las dos URLs** y el orden de merge.

**Criterio de éxito:** abres una carpeta y reconstruyes la ejecución entera sin volver a ejecutar nada — incluido *de dónde salió cada valor*. Esa columna de origen es lo que no tenías en la PoC anterior, y con variables por entorno pasa de ser un lujo a ser necesaria: es la diferencia entre "el sistema puso 3 réplicas" y "el sistema respetó las 8 réplicas que alguien había puesto a mano".

## T5.2 — `CONCLUSIONES.md`
Tres preguntas, respondidas en primera persona después de haberlo tocado:
1. ¿Cuántos de los 18 pasos necesitaron LLM de verdad, y el reparto medido coincide con el previsto en este plan?
2. ¿Qué se rompió al pasar de disco a ADO, y qué habría hecho distinto si lo hubiera sabido?
3. ¿Qué parte de este sistema seguiría siendo mía si mañana cambio de framework o de proveedor Git?

---

# Bloque 6 · Opcionales, por valor

- **Enriquecer requisitos inspeccionando el repo destino.** Recuperar `fingerprint()` de `poc-agentes` y aplicarlo sobre el repo destino leído por API: si el usuario dice "Java" y el repo tiene `pom.xml` con `<java.version>17</java.version>`, el slot se rellena solo y el chat pregunta una cosa menos. Cierra el círculo entre las dos PoCs.
- **Actualización, no solo alta.** Repos que ya tienen `azure-pipelines.yml`: leerlo, detectar de qué plantilla/versión viene y proponer un bump de tag. Es el caso de uso con más valor real en un parque grande.
- **Batería de evaluación.** Diez conversaciones grabadas con su `Requisitos` esperado; medir el acierto del recolector. El paso 1 es el único punto donde el LLM es imprescindible, así que es el único que merece un eval de verdad.
- **Servidor MCP** exponiendo `consultar_catalogo`, `proponer_pipeline`, `abrir_pull_request`.

---

# Ritmo sugerido

| Sesión | Bloques | Horas | Estado |
|---|---|---|---|
| 1 | T0.1 – T0.3 | 3 | ✅ (falta crear el repo `pipelines`) |
| 2 | T1.1 – T1.2 | 3 | ✅ |
| 3 | T1.3 + T4.0 | 3 | inventario de ficheros + plantillas con checkout |
| 4 | **T2.1 – T2.3** | 5 | la entrada conversacional |
| 5 | T3.1 – T3.4 | 5 | máquina de estados + parámetros + variables |
| 6 | **T4.1 – T4.4** | 5 | los dos PR, idempotencia y merge no destructivo |
| 7 | T4.5 + T5.1 – T5.2 | 3 | descripciones, traza y conclusión |

**Si solo hay tiempo para tres sesiones antes de la reunión: la 4, la 6 y la 7.** Con el recolector conversacional, los dos PR reales y la traza puedes enseñar el recorrido completo de punta a punta.

# Qué no hacer en esta fase

- **Nada de multiagente.** Un solo agente con estado tipado. La tentación de poner "un agente que recoge requisitos y otro que elige plantilla" es fuerte y no aporta nada aquí: son dos funciones, no dos agentes.
- **Nada de ejecutar pipelines en ADO.** Generar y abrir los PR es el alcance. Que la pipeline corra de verdad depende de agentes de build, service connections y permisos que no están en el camino crítico.
- **Nada de UI.** La terminal es la interfaz.
- **No más de tres plantillas.** Con tres ya hay ambigüedad, que es lo único que justifica el LLM en el paso 4.
- **Nunca un PAT en el código, ni en un `runs/`.** Repasa que la traza no se lleve cabeceras de auth dentro.
