# Plan: PoC de clonado de pipelines con entrada conversacional y Azure DevOps

**Qué es esto.** La segunda iteración de `poc-agentes`. La primera demostró el mecanismo (bucle de tool calling, salida estructurada, reglas antes que LLM, renderizado determinista, traza en `runs/`) sobre repos de mentira en disco. Esta segunda demuestra **el producto**: el usuario pide en lenguaje natural la pipeline que necesita, y el sistema deja un Pull Request abierto en Azure DevOps.

**Los dos cambios de fondo respecto a `poc-agentes`:**

1. **La entrada deja de ser un repo y pasa a ser una conversación.** Antes el flujo arrancaba con `fingerprint(ruta)`: señales deterministas extraídas de ficheros. Ahora arranca con un chat en el que el usuario describe requisitos. Eso mueve el LLM del *medio* del flujo (desambiguar un arquetipo) al *principio* (convertir prosa en datos estructurados), que es un trabajo distinto y más difícil.
2. **El catálogo y los destinos dejan de estar en disco y pasan a estar en Azure DevOps.** Y el artefacto final deja de ser un fichero en `runs/` para ser una rama, un commit y un PR reales.

**Decisiones ya tomadas (2026-09-16), no las re-abras sin motivo:**

| Decisión | Elegido | Descartado |
|---|---|---|
| Qué se deja en el repo destino | **Pipeline hijo con `extends`** + pin por tag al repo de plantillas | Copia física de los ficheros de plantilla |
| Organización del catálogo en ADO | **Un repo de plantillas, una carpeta por plantilla** (`catalog/<id>/manifest.yaml`) | Un repo por plantilla |
| Acceso a ADO | **Organización propia + PAT, real desde el Bloque 0** | Backend local simulado |

La primera decisión es la importante: el repo destino queda con ~15 líneas que apuntan a una plantilla versionada. Eso es lo que hace que la plataforma sea gobernable (cambias la plantilla, se actualizan todos) y es exactamente lo que `render/renderizador.py` ya sabe hacer hoy.

---

# La pregunta que esta PoC responde: ¿qué necesita LLM y qué no?

Este es el entregable conceptual del ejercicio. El flujo completo, paso a paso, con la etiqueta puesta:

| # | Paso | Naturaleza | Por qué |
|---|---|---|---|
| 1 | Recoger requisitos en chat | **LLM** | El input es prosa abierta. No hay reglas posibles. Es el único punto del sistema con entrada genuinamente no estructurada. |
| 2 | Validar que los requisitos están completos | Determinista | JSON Schema sobre el objeto `Requisitos`. Sabe qué falta sin preguntarle a nadie. |
| 3 | Descubrir el catálogo en ADO | Determinista | REST API + parseo de YAML. |
| 4 | Elegir plantilla | **Híbrido** | Reglas sobre los requisitos primero. El LLM solo entra si 0 o >1 candidatos. |
| 5 | Derivar parámetros que el requisito ya fija | Determinista | Mapeo declarado en el manifest (`derived_from`). Si el usuario dijo "Java 17", `javaVersion` no lo decide un modelo. |
| 6 | Rellenar parámetros sin requisito | **LLM** + validar-y-reintentar | Solo los huecos. Contra el `parameters.schema.json` real. |
| 7 | Renderizar el YAML | Determinista | `yaml.dump`. El modelo nunca escribe el artefacto que se despliega. |
| 8 | Elegir repo destino | Determinista + humano | Se listan los repos de ADO, elige el usuario. |
| 9 | Nombre de rama, ruta del fichero, mensaje de commit | Determinista | Convención, no creatividad. |
| 10 | Redactar la descripción del PR | **LLM** | Prosa para un revisor humano. Hereda de `catalog/justificador.py`. |
| 11 | Crear rama + commit + PR | Determinista | REST API. |
| 12 | Registrar la ejecución | Determinista | Ficheros en `runs/` + enlace al PR. |

**Dos pasos puramente LLM (1 y 10), dos híbridos (4 y 6), ocho deterministas.** Y de los dos LLM puros, uno (el 10) es texto que ningún sistema consume: si sale mal, un humano lo lee y lo corrige. El único punto donde el modelo es imprescindible y su salida alimenta al resto es el paso 1.

Ese es el titular. La segunda mitad —desde que existe un `Requisitos` validado hasta que hay un PR abierto— es un programa normal, auditable línea a línea, con el modelo tocando exactamente dos cosas y ambas validadas contra un esquema que escribió una persona.

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
                    DESCUBRIENDO_CATALOGO ──► catálogo ADO (repo de plantillas)
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
               RENDERIZANDO  ──► pipeline hijo (extends + pin por tag)
                    │
                    ▼
           SELECCIONANDO_DESTINO ──► repos ADO, elige el usuario
                    │
                    ▼
              CONFIRMANDO_PUSH (humano)  ◄── última puerta antes de escribir en ADO
                    │
                    ▼
            CREANDO_RAMA_Y_COMMIT ──► ADO Pushes API (sin clonar nada en local)
                    │
                    ▼
                REDACTANDO_PR  ◄── LLM
                    │
                    ▼
                 CREANDO_PR ──► ADO Pull Requests API
                    │
                    ▼
                 COMPLETADO ──► runs/{timestamp}/ + URL del PR
```

## Estructura del repo

```
poc-agentes-maf/
├── AGENTS.md                    # reglas de trabajo (hereda de poc-agentes)
├── docs/
│   ├── Plan poc-ado.md          # este fichero
│   └── notas-agentes/Bitacora-de-sesiones.md
├── ado/                         # TODO lo que habla con Azure DevOps, aislado
│   ├── cliente.py               # auth PAT, sesión HTTP, manejo de errores
│   ├── catalogo.py              # descubrir y leer plantillas del repo de plantillas
│   ├── destinos.py              # listar repos candidatos a destino
│   └── cambios.py               # push (rama+commit) y creación de PR
├── requisitos/
│   ├── esquema.py               # modelo Pydantic `Requisitos` + JSON Schema
│   └── recolector.py            # slot filling conversacional (LLM)
├── seleccion/
│   ├── reglas.py                # requisitos → plantilla, sin modelo
│   └── hibrido.py               # reglas + LLM de respaldo + umbral
├── parametros/
│   └── generador.py             # derivación determinista + LLM para huecos
├── render/
│   └── renderizador.py          # COPIADO TAL CUAL de poc-agentes
├── orquestacion/
│   ├── estados.py               # enum de estados + tabla determinista/LLM
│   └── flujo.py                 # el workflow (MAF)
├── chat.py                      # punto de entrada: la terminal
└── runs/
```

**El límite importante es `ado/`.** Todo lo que sabe de Azure DevOps vive ahí y expone funciones de dominio (`listar_plantillas()`, `crear_pull_request(...)`). El resto del sistema no sabe que existe ADO. Si mañana hay que soportar GitHub, se escribe `github/` con la misma superficie y no se toca nada más. En la PoC anterior no hizo falta esta disciplina porque todo era disco; aquí sí.

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

## T0.1 — Esqueleto del proyecto
Estructura de carpetas de arriba, `.venv`, `requirements.txt` (`openai`, `pyyaml`, `jsonschema`, `pydantic`, `httpx`, `python-dotenv`, `agent-framework-*`), `.gitignore` con `.env` desde el primer commit. `AGENTS.md` heredando las reglas de `poc-agentes` (explicar → implementar → resumir, bitácora, teoría en Obsidian).

**Criterio de éxito:** `git status` limpio con `.env` ya creado y no trackeado.

## T0.2 — PAT y llamada de humo a ADO
PAT con los scopes mínimos: **Code (Read & Write)** y **Pull Request Contribute**. Nada de `Full access`. En `.env` como `AZDO_PAT`, junto a `AZDO_ORG` y `AZDO_PROJECT`.

Autenticación: Basic con usuario vacío y el PAT como contraseña — `Authorization: Basic <base64(":" + PAT)>`.

Script de humo: listar los repos del proyecto.
`GET https://dev.azure.com/{org}/{project}/_apis/git/repositories?api-version=7.1`

**Criterio de éxito:** el script imprime los nombres de tus repos. Si devuelve HTML de login en vez de JSON, el PAT o los scopes están mal — no sigas.

**Trampa conocida:** un PAT inválido en ADO no siempre devuelve 401; a veces devuelve 200 con la página de login. Comprueba el `Content-Type`, no solo el código de estado.

## T0.3 — Sembrar el escenario en ADO
- Un repo **`plantillas-ci`** con `catalog/ci-java-container/`, `catalog/ci-dotnet/`, `catalog/ci-node-container/` (los ficheros ya existen en `poc-agentes`). Etiquétalo `v1.0.0` — el pin por tag del `extends` necesita un tag real.
- Dos o tres repos **destino** vacíos o casi (`demo-servicio-java`, `demo-api-node`), sin `azure-pipelines.yml`.

**Criterio de éxito:** el tag `v1.0.0` existe y puedes leer `catalog/ci-java-container/manifest.yaml` desde la web de ADO.

**Qué NO hacer:** no configures todavía ninguna pipeline en ADO ni intentes ejecutarla. Que el YAML sea válido y esté en su sitio es suficiente para toda la PoC. Ejecutar de verdad es el Bloque 6.

---

# Bloque 1 · El catálogo vive en ADO (3 h)

## T1.1 — Cliente REST mínimo
`ado/cliente.py`: una clase con la URL base, la cabecera de auth y un `get`/`post` que levanta excepción con el cuerpo del error incluido (los errores de ADO traen un `message` legible; perderlo te hará perder una tarde).

**Criterio de éxito:** un 404 provocado a propósito imprime el mensaje de ADO, no un `KeyError`.

## T1.2 — Descubrimiento del catálogo
`ado/catalogo.py`. Dos llamadas:
- Listar el árbol: `GET .../_apis/git/repositories/{repoId}/items?scopePath=/catalog&recursionLevel=full&versionDescriptor.version=v1.0.0&versionDescriptor.versionType=tag&api-version=7.1`
- Leer un fichero: el mismo endpoint con `path=...&includeContent=true&$format=json`

Devuelve una lista de `PlantillaDisponible` (id, versión, `applies_to`, `parameters`, `selection_rationale`, ruta del `template.yaml`), construida parseando los `manifest.yaml` reales.

**Cachea el resultado en memoria durante la sesión.** Descubrir el catálogo son N+1 llamadas HTTP y el flujo lo necesita varias veces.

**Criterio de éxito:** una función devuelve las tres plantillas leídas de ADO, y el `id` de cada una coincide con el de su carpeta. Si no coinciden, el catálogo miente: falla ruidosamente.

**Por qué importa para la oferta:** el catálogo es un dato versionado en Git, no una tabla en el código del agente. Añadir una plantilla es un PR al repo de plantillas, no un despliegue del sistema.

## T1.3 — Repos destino
`ado/destinos.py`: lista los repos del proyecto, excluye el de plantillas, y marca cuáles ya tienen `azure-pipelines.yml` en la raíz de su rama por defecto (un `GET items?path=/azure-pipelines.yml`, un 404 significa que no lo tiene).

**Criterio de éxito:** la lista distingue "repo sin pipeline" de "repo que ya tiene una" — porque el segundo caso cambia el flujo (es una actualización, no un alta) y conviene verlo desde el principio.

---

# Bloque 2 · La entrada conversacional (4–5 h)

Este es el bloque nuevo de verdad. Todo lo demás es traslado de lo que ya sabes hacer.

## T2.1 — El objeto `Requisitos`
`requisitos/esquema.py`: un modelo Pydantic con los campos que el flujo necesita para elegir plantilla y rellenar parámetros. Arranque deliberadamente corto:

```
tecnologia:        Literal["java", "dotnet", "node"]          obligatorio
version_lenguaje:  str | None       ("17", "8.0", "20")
contenedor:        bool | None      ¿se publica imagen?
repo_destino:      str | None       nombre del repo en ADO
version_app:       str | None       semver
notas:             str | None       texto libre que no encaja en ningún slot
```

Cada campo con su `description` — esas descripciones se le enseñan al modelo en T2.2, así que están escritas para que las lea él.

**La pieza clave no es el modelo, es `slots_que_faltan(requisitos) -> list[str]`:** una función determinista que dice qué falta para poder avanzar. El LLM **no** decide cuándo la conversación ha terminado. Lo decide esta función.

**Criterio de éxito:** un `Requisitos` a medias devuelve exactamente la lista de campos obligatorios vacíos, y uno completo devuelve `[]`.

## T2.2 — Recolector conversacional (slot filling)
`requisitos/recolector.py`. En cada turno:
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

## T2.3 — Cierre y confirmación
Cuando no faltan slots, se le muestra al usuario el `Requisitos` completo en texto plano y se le pide confirmación explícita. Si dice que no, vuelve a T2.2 con su corrección.

**Criterio de éxito:** el flujo no avanza sin un "sí" del usuario, y un "cambia la versión a 21" vuelve atrás y la cambia sin perder lo demás.

---

# Bloque 3 · La orquestación como máquina de estados (4 h)

## T3.1 — Estados y transiciones explícitos
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

`orquestacion/flujo.py`: el workflow con MAF (`WorkflowBuilder`, un nodo por estado, transiciones condicionadas), igual que `agente_workflow.py` pero con un objeto de estado compartido en vez de un mensaje que se va transformando.

**Aviso de diseño, verifícalo pronto:** el workflow en grafo de la PoC anterior es un DAG de un solo disparo — entra un mensaje, sale un resultado. Este flujo necesita **pausar y esperar al humano** en tres sitios (recogida de requisitos, confirmación de plantilla, confirmación de push). MAF tiene mecanismos de human-in-the-loop y de checkpointing, pero los nombres exactos de la API han ido cambiando durante el preview: **confirma contra la versión instalada antes de diseñar alrededor de ellos.** Si no encajan, el plan B es honesto y sirve igual: la máquina de estados avanza un paso por llamada y el bucle de `chat.py` la va empujando. Es menos elegante y más fácil de depurar.

**Criterio de éxito:** una ejecución imprime la secuencia de estados recorridos con su naturaleza al lado, y el recuento de llamadas al LLM.

## T3.2 — Selección de plantilla híbrida
`seleccion/reglas.py` + `seleccion/hibrido.py`. Reglas sobre `Requisitos` contra el `applies_to` de los manifests **descubiertos en ADO** (no una lista hardcodeada). Tres estados como antes: `decidido` / `ambiguo` / `desconocido`. El LLM entra solo en los dos últimos, con la lista cerrada de ids del catálogo real, salida estructurada, reintento y umbral 0.7.

**Criterio de éxito:** `tecnologia: "java"` + `contenedor: true` resuelve por reglas con cero llamadas al modelo. Un requisito raro (`notas: "es un monorepo con Java y Node"`, `tecnologia` incierta) baja por la rama del LLM, y con umbral 0.99 escala a revisión humana.

## T3.3 — Parámetros: derivar primero, preguntar al modelo después
`parametros/generador.py`. **Cambio real respecto a `poc-agentes`:** allí todos los parámetros los generaba el LLM. Aquí, primero se recorre `manifest.parameters` y se rellena de forma determinista todo lo que el `Requisitos` ya fija (vía `derived_from`). El LLM recibe **solo los parámetros que siguen vacíos**, y el resultado se valida contra el `parameters.schema.json` completo.

**Criterio de éxito:** con un `Requisitos` completo (java/17/docker/1.0.0) el LLM no se llama ni una vez, porque no queda ningún hueco. Con uno parcial, se llama solo para los que faltan y lo ves en el log.

**Por qué esto importa más de lo que parece:** es la demostración cuantificada de que el modelo se retira solo a medida que los datos de entrada mejoran. Con un usuario que sabe lo que quiere, el sistema es puro código.

---

# Bloque 4 · Escribir en Azure DevOps (4–5 h)

El bloque más "de fontanería" y el que convierte la PoC en demo. Cero IA hasta T4.4.

## T4.1 — Rama y commit en una sola llamada
`ado/cambios.py`. **El hallazgo del bloque: no hace falta clonar nada en local.** La Pushes API crea la rama y el commit de una vez:

`POST .../_apis/git/repositories/{repoId}/pushes?api-version=7.1`
```json
{
  "refUpdates": [{ "name": "refs/heads/feat/pipeline-ci", "oldObjectId": "<sha de la rama base>" }],
  "commits": [{
    "comment": "feat: añadir pipeline CI generada desde ci-java-container v1.0.0",
    "changes": [{
      "changeType": "add",
      "item": { "path": "/azure-pipelines.yml" },
      "newContent": { "content": "<el YAML>", "contentType": "rawtext" }
    }]
  }]
}
```

El `oldObjectId` sale de `GET .../refs?filter=heads/main`. Para crear una rama desde cero se usan 40 ceros, pero aquí siempre partimos de la rama por defecto.

Nombre de rama y mensaje de commit: **deterministas, por convención** (`feat/pipeline-{plantilla}-{timestamp}`). No es trabajo para un modelo.

**Criterio de éxito:** aparece una rama nueva en el repo destino con el fichero. Compruébalo en la web de ADO, no solo por el 201.

**Detalle que te va a morder:** `changeType` debe ser `"edit"` si el fichero ya existe. De ahí la comprobación de T1.3.

## T4.2 — Pull Request
`POST .../_apis/git/repositories/{repoId}/pullrequests?api-version=7.1` con `sourceRefName`, `targetRefName`, `title`, `description`. La respuesta trae `pullRequestId`; la URL para un humano se compone como `https://dev.azure.com/{org}/{project}/_git/{repo}/pullrequest/{id}`.

**Criterio de éxito:** el PR se abre y la URL que imprime el sistema es clicable y correcta.

## T4.3 — La puerta humana y la idempotencia
Dos cosas que separan una demo de un juguete:
- **Confirmación explícita antes de escribir.** El estado `CONFIRMANDO_PUSH` muestra el YAML final, el repo destino y el nombre de la rama, y espera un "sí". Ninguna escritura en ADO ocurre sin ella.
- **Idempotencia.** Si la rama ya existe, no revientes: reutilízala o añade sufijo. Si ya hay un PR abierto de esa rama al destino, devuélvelo en vez de crear otro.

**Criterio de éxito:** ejecutar el flujo dos veces seguidas con los mismos requisitos no deja dos PRs duplicados, y responder "no" en la confirmación no deja **nada** en ADO.

## T4.4 — La descripción del PR (aquí sí, LLM)
Hereda de `catalog/justificador.py`. Markdown corto para el revisor: qué plantilla se eligió y por qué, qué parámetros se aplicaron y de qué requisito sale cada uno, qué controles incluye la plantilla, qué se asumió y qué vigilar. Texto libre, sin validar-y-reintentar: no hay esquema que cumplir y un humano lo lee antes de aprobar.

**Criterio de éxito:** abres el PR en ADO y la descripción se sostiene sola: un compañero que no estuvo en la conversación entiende qué pasó.

---

# Bloque 5 · Traza y conclusión (2 h)

## T5.1 — `runs/` con el PR dentro
Una carpeta por ejecución: `requisitos.json`, `transcripcion.md` (la conversación entera), `seleccion.json` (plantilla, origen reglas/LLM, confianza), `parametros.json` con **el origen de cada valor** (derivado del requisito / generado por el LLM), `pipeline.yml`, `descripcion-pr.md`, `metadata.json` (backend, modelo, nº de llamadas al LLM, estados recorridos) y `resultado.json` con la URL del PR.

**Criterio de éxito:** abres una carpeta y reconstruyes la ejecución entera sin volver a ejecutar nada — incluido *de dónde salió cada valor*. Esa columna de origen es lo que no tenías en la PoC anterior.

## T5.2 — `CONCLUSIONES.md`
Tres preguntas, respondidas en primera persona después de haberlo tocado:
1. ¿Cuántos de los 12 pasos necesitaron LLM de verdad, y el reparto medido coincide con el previsto en este plan?
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

| Sesión | Bloques | Horas |
|---|---|---|
| 1 | T0.1 – T0.3 | 3 |
| 2 | T1.1 – T1.3 | 3 |
| 3 | **T2.1 – T2.3** | 5 |
| 4 | T3.1 – T3.3 | 4 |
| 5 | **T4.1 – T4.3** | 4 |
| 6 | T4.4 + T5.1 – T5.2 | 3 |

**Si solo hay tiempo para tres sesiones antes de la reunión: la 3, la 5 y la 6.** Con el recolector conversacional, el PR real y la traza puedes enseñar el recorrido completo de punta a punta.

# Qué no hacer en esta fase

- **Nada de multiagente.** Un solo agente con estado tipado. La tentación de poner "un agente que recoge requisitos y otro que elige plantilla" es fuerte y no aporta nada aquí: son dos funciones, no dos agentes.
- **Nada de ejecutar pipelines en ADO.** Generar y abrir el PR es el alcance. Que la pipeline corra de verdad depende de agentes de build, service connections y permisos que no están en el camino crítico.
- **Nada de UI.** La terminal es la interfaz.
- **No más de tres plantillas.** Con tres ya hay ambigüedad, que es lo único que justifica el LLM en el paso 4.
- **Nunca un PAT en el código, ni en un `runs/`.** Repasa que la traza no se lleve cabeceras de auth dentro.
