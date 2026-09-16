# Bitácora de sesiones — poc-agentes-maf

Registro de cierre de cada sesión: qué se hizo, en qué quedó el código y cuál es el siguiente paso exacto. Empieza a leer siempre por la entrada más reciente (arriba).

Continúa la bitácora de `poc-agentes` (sesiones 1–4, hasta 2026-09-12), que sigue viva en aquel repo para su T4.4 pendiente.

---

## Sesión 1 — 2026-09-16

### Por dónde retomar
**Bloque 0 completo.** Siguiente tarea: **T1.1 — cliente REST mínimo** (`ado/cliente.py`), extrayendo a un solo sitio el patrón de petición + diagnóstico que hoy está duplicado entre `ado/humo.py` y `ado/sembrar.py`. Después T1.2 (descubrimiento del catálogo), que ya está prácticamente de-riesgado: la consulta exacta que necesita se ejecutó a mano al verificar T0.3 y funciona.

**Deuda anotada para cuando se toque el renderizado (T3.x):** `render/renderizador.py` viene de `poc-agentes` con dos valores que ya no sirven — `template: template.yaml@templates` tiene que pasar a `catalog/<id>/template.yaml@templates` (en el repo real las plantillas cuelgan de `catalog/`), y `name: MiOrg/MiRepoDePlantillas` a `POC-MAF/plantillas-ci`.

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
