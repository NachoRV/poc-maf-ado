"""El parrafo de explicacion de los pull requests. El ULTIMO LLM del sistema.

Es el unico sitio donde el modelo escribe texto que va a leer una persona, y es
deliberadamente el paso de menor riesgo del flujo:

  - Su salida no la consume ningun programa. Si sale mal, un humano lo lee y lo
    corrige en el propio pull request.
  - No decide nada. La plantilla, los parametros y los ficheros ya estan fijados
    cuando esto se ejecuta.
  - Si falla, hay respaldo determinista y el PR se abre igual. La explicacion es
    un anadido, no un requisito.

Lo que este fichero NO escribe, y es lo importante: el orden de merge, la lista
de ficheros, los huecos y el enlace al PR hermano. Eso lo monta redaccion/texto.py
con codigo. Si el modelo se inventara el orden de merge, alguien mergearia el
pipeline antes que sus variables y lo dejaria roto.

Ejecuta con (desde la raiz del repo): .venv/bin/python -m redaccion.agente_pr
"""
import json

from pydantic import BaseModel, Field

from llm.estructurado import SinRespuestaValida, pedir_json
from requisitos.esquema import Requisitos

RESPALDO = ("Pipeline generado a partir de los requisitos recogidos en la conversacion. "
            "(No se pudo redactar la explicacion automaticamente.)")


class Explicacion(BaseModel):
    texto: str = Field(min_length=20)


def _mensajes(resumen: dict, foco: str) -> list[dict]:
    return [
        {
            "role": "system",
            "content": (
                "Redactas el parrafo de justificacion de un pull request que da de alta "
                "un pipeline de CI/CD, para que un revisor entienda que paso sin ejecutar nada.\n\n"
                "Reglas:\n"
                "- Entre 3 y 5 frases, en prosa, sin listas ni titulos.\n"
                "- Usa SOLO los datos que te doy. No inventes controles, ni pasos, ni "
                "tecnologias que no aparezcan.\n"
                "- Menciona que supuestos se asumieron y que conviene vigilar.\n"
                "- No repitas el orden de merge ni la lista de ficheros: eso ya va aparte.\n\n"
                'Responde SOLO con: {"texto": "<el parrafo>"}'
            ),
        },
        {
            "role": "user",
            "content": f"Foco de este pull request: {foco}\n\nDatos:\n"
                       f"{json.dumps(resumen, ensure_ascii=False, indent=2)}",
        },
    ]


def explicar(requisitos: Requisitos, *, foco: str, plantilla_id: str | None = None,
             parametros: dict | None = None, huecos: dict | None = None) -> str:
    """El parrafo, o el respaldo determinista si el modelo no colabora."""
    resumen = {
        "repositorio": requisitos.repo_codigo,
        "tecnologia": requisitos.tecnologia,
        "version_lenguaje": requisitos.version_lenguaje,
        "se_publica_como_contenedor": requisitos.contenedor,
        "entornos": requisitos.entornos,
        "plantilla_elegida": plantilla_id,
        "parametros": parametros or {},
        "variables_sin_valor": huecos or {},
        "notas_del_usuario": requisitos.notas,
    }
    try:
        return pedir_json(_mensajes(resumen, foco), Explicacion).texto.strip()
    except SinRespuestaValida:
        # El PR se abre igual. Que la explicacion falle no puede bloquear el alta.
        return RESPALDO


if __name__ == "__main__":
    from dotenv import load_dotenv

    from llm.cliente import llamadas_al_modelo

    load_dotenv()
    requisitos = Requisitos(tecnologia="java", repo_codigo="demo-servicio-java",
                            version_lenguaje="17", contenedor=True, version_app="1.0.0")

    print("=== explicacion del PR del pipeline ===")
    print(explicar(requisitos, foco="el pipeline que extiende la plantilla",
                   plantilla_id="ci-java-container",
                   parametros={"isDocker": True, "javaVersion": "17", "appVersion": "1.0.0"}))

    print("\n=== explicacion del PR de variables ===")
    print(explicar(requisitos, foco="los ficheros de variables por entorno",
                   huecos={"vars/pro.yml": ["azureSubscription"]}))

    print(f"\nllamadas al modelo: {llamadas_al_modelo()}")
