"""El unico sitio del proyecto que construye un cliente de modelo de lenguaje.

Es el gemelo de ado/cliente.py: alli se aisla todo lo que habla con Azure
DevOps, aqui todo lo que habla con un LLM. La razon es la misma pero la
consecuencia es mas util:

    SOLO LOS FICHEROS QUE IMPORTAN `llm/` PUEDEN ALUCINAR.

Eso convierte "¿que partes de este sistema pueden inventarse un dato?" en algo
que se comprueba con un grep, no en una afirmacion de confianza:

    grep -rl "from llm" --include="*.py" .

En poc-agentes esta frontera no existia: construir_cliente_openai() vivia dentro
de catalog/clasificador_hibrido.py, asi que el fichero que elegia arquetipos era
tambien el que sabia de backends, y averiguar cuanto LLM habia en el sistema
obligaba a leerlo entero.

Backend por MODEL_BACKEND (lmstudio | gemini). Aqui el defecto es lmstudio, al
reves que en poc-agentes: el recolector conversacional gasta una peticion POR
TURNO y el tier gratuito de Gemini son 20 al dia -- una sola conversacion de
prueba lo agota.

Ejecuta la comprobacion con: .venv/bin/python -m llm.cliente
"""
import os

from openai import OpenAI

BACKEND_POR_DEFECTO = "lmstudio"

# Timeout y reintentos EXPLICITOS. El SDK de OpenAI, si no se le dice nada, usa
# 600 segundos con 2 reintentos: una conexion muerta se queda colgada media hora
# en silencio. Paso en real -- LM Studio recargo el modelo a mitad de una
# peticion, el socket murio, y el proceso estuvo 20 minutos al 0% de CPU
# esperando mientras el servidor respondia a todo lo demas en 2 segundos.
# ado/cliente.py si tenia timeout desde el principio; este no, y se noto.
#
# 180s porque un modelo de razonamiento local tarda minutos de verdad; no es un
# servicio web. max_retries=1 porque el reintento util (con el error como
# feedback) lo hace requisitos/agente_extractor.py, que ademas sabe que decirle al
# modelo -- reintentar aqui a ciegas solo multiplica la espera.
TIMEOUT = float(os.environ.get("LLM_TIMEOUT", "180"))
MAX_REINTENTOS_HTTP = 1


def backend_activo() -> str:
    return os.environ.get("MODEL_BACKEND", BACKEND_POR_DEFECTO)


# Contador de llamadas reales al modelo. Se incrementa envolviendo el metodo del
# cliente, no pidiendole a cada llamante que se instrumente: asi es IMPOSIBLE que
# alguien anada un agente nuevo y se olvide de contarlo, y el "de 16 estados solo
# N tocan el modelo" pasa de ser una afirmacion a un dato medido por ejecucion.
_LLAMADAS = 0


def llamadas_al_modelo() -> int:
    return _LLAMADAS


def reiniciar_contador() -> None:
    global _LLAMADAS
    _LLAMADAS = 0


def _contando(cliente: OpenAI) -> OpenAI:
    original = cliente.chat.completions.create

    def create(*args, **kwargs):
        global _LLAMADAS
        _LLAMADAS += 1
        return original(*args, **kwargs)

    cliente.chat.completions.create = create  # type: ignore[method-assign]
    return cliente


def construir_cliente() -> tuple[OpenAI, str]:
    """Devuelve (cliente, nombre_del_modelo). Ambos backends hablan el mismo
    protocolo OpenAI, asi que quien llama no se entera de cual esta usando."""
    backend = backend_activo()

    if backend == "lmstudio":
        return (
            _contando(OpenAI(
                # LM Studio no valida la key, pero el SDK exige un string.
                api_key="lm-studio",
                base_url=os.environ.get("LM_STUDIO_BASE_URL", "http://localhost:1234/v1"),
                timeout=TIMEOUT,
                max_retries=MAX_REINTENTOS_HTTP,
            )),
            # Por defecto un modelo SIN razonamiento. qwen3.5-9b estuvo aqui y
            # fue un error: gastaba ~1500 tokens pensando por cada respuesta de
            # 130 caracteres, y las dos formas documentadas de apagarlo en LM
            # Studio se ignoran.
            os.environ.get("LM_STUDIO_MODEL", "google/gemma-3-4b"),
        )

    if backend == "gemini":
        clave = os.environ.get("GEMINI_API_KEY")
        if not clave:
            raise ValueError("MODEL_BACKEND=gemini pero GEMINI_API_KEY esta vacia en .env")
        return (
            _contando(OpenAI(
                api_key=clave,
                base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
                timeout=TIMEOUT,
                max_retries=MAX_REINTENTOS_HTTP,
            )),
            os.environ.get("GEMINI_MODEL", "gemini-2.5-flash"),
        )

    raise ValueError(f"MODEL_BACKEND desconocido: {backend!r} (usa 'lmstudio' o 'gemini')")


def kwargs_json() -> dict:
    """response_format, solo donde el backend lo soporta.

    Gemini acepta {"type": "json_object"}; el Chat Completions de LM Studio lo
    rechaza con 400 ("must be 'json_schema' or 'text'") -- comprobado en real en
    poc-agentes. En vez de acoplar cada llamante a esa diferencia, se decide
    aqui: para lmstudio se confia en la instruccion del prompt mas el
    validar-y-reintentar, que existe precisamente para sobrevivir a esto.
    """
    return {"response_format": {"type": "json_object"}} if backend_activo() == "gemini" else {}


if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv()
    cliente, modelo = construir_cliente()
    print(f"[llm] backend        : {backend_activo()}")
    print(f"[llm] modelo         : {modelo}")
    print(f"[llm] response_format: {kwargs_json() or '(no soportado, se confia en prompt + reintento)'}")
    print(f"[llm] timeout        : {TIMEOUT}s, {MAX_REINTENTOS_HTTP} reintento(s) HTTP")

    respuesta = cliente.chat.completions.create(
        model=modelo,
        messages=[{"role": "user", "content": "Responde exactamente con la palabra: ok"}],
        max_tokens=2000,
    )
    print(f"[llm] respuesta      : {respuesta.choices[0].message.content!r}")
