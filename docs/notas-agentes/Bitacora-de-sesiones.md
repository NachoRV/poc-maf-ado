# Bitácora de sesiones — poc-agentes-maf

Registro de cierre de cada sesión: qué se hizo, en qué quedó el código y cuál es el siguiente paso exacto. Empieza a leer siempre por la entrada más reciente (arriba).

Continúa la bitácora de `poc-agentes` (sesiones 1–4, hasta 2026-09-12), que sigue viva en aquel repo para su T4.4 pendiente.

---

## Sesión 1 — 2026-09-16

### Por dónde retomar
**Siguiente tarea: T2.3 — cierre y confirmación.** Cuando `slots_que_faltan()` devuelve `[]`, mostrar el `descripcion(requisitos)` completo y pedir confirmación explícita; un "cambia X" vuelve a T2.2 sin perder lo demás. Es donde se cazan las extracciones incompletas del modelo (ver abajo). Con eso cierra el Bloque 2.

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
