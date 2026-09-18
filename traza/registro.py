"""T5.1 - la traza de una ejecucion en runs/. DETERMINISTA.

El criterio: abrir una carpeta de `runs/` y poder reconstruir QUE paso y POR QUE
sin volver a ejecutar nada, y sin mirar el codigo.

Lo que distingue esta traza de la de poc-agentes es una columna: **el ORIGEN de
cada valor**. No basta con guardar que `replicas` valia 8; hay que poder decir si
lo decidio el sistema, lo dijo una persona en la conversacion, o estaba ya en el
repo y se RESPETO. Con variables de entorno esa distincion deja de ser un lujo:
es la diferencia entre "el sistema puso 3 replicas" y "el sistema respeto las 8
que alguien habia puesto a mano".

Y el otro dato que solo se sabe ejecutando: cuantas llamadas al modelo costo de
verdad esta ejecucion, cruzado con la tabla de estados. Eso convierte "la mayor
parte es determinista" en un numero medido.

Ejecuta con (desde la raiz del repo): .venv/bin/python -m traza.registro
"""
import json
import os
from datetime import datetime
from pathlib import Path

CARPETA_RUNS = Path(__file__).parent.parent / "runs"


def _escribir(carpeta: Path, nombre: str, contenido) -> None:
    ruta = carpeta / nombre
    ruta.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(contenido, str):
        ruta.write_text(contenido)
    else:
        ruta.write_text(json.dumps(contenido, indent=2, ensure_ascii=False, default=str))


def transcripcion(historial: list[dict]) -> str:
    """La conversacion entera, legible. Es la entrada del sistema: sin ella, el
    resto de la traza explica el COMO pero no el POR QUE."""
    lineas = ["# Transcripcion de la conversacion\n"]
    for mensaje in historial:
        quien = "Usuario" if mensaje["role"] == "user" else "Agente"
        lineas.append(f"**{quien}:** {mensaje['content']}\n")
    return "\n".join(lineas)


def guardar(contexto, *, llamadas_modelo: int, naturaleza_por_estado: dict) -> Path:
    """Vuelca todo lo de una ejecucion. Devuelve la carpeta creada."""
    req = contexto.requisitos
    marca = datetime.now().strftime("%Y%m%d-%H%M%S")
    carpeta = CARPETA_RUNS / f"{marca}_{req.repo_codigo or 'sin-repo'}"
    carpeta.mkdir(parents=True, exist_ok=True)

    _escribir(carpeta, "requisitos.json", req.model_dump())
    _escribir(carpeta, "transcripcion.md", transcripcion(contexto.historial))

    _escribir(carpeta, "seleccion.json", {
        "plantilla": contexto.plantilla.id if contexto.plantilla else None,
        "tag_del_catalogo": contexto.plantilla.tag if contexto.plantilla else None,
        "candidatas_del_catalogo": [p.id for p in contexto.plantillas],
    })

    # Las dos columnas de origen: sin esto la traza dice QUE, pero no DE DONDE.
    _escribir(carpeta, "parametros.json", {
        "valores": contexto.parametros,
        "origen": contexto.origen_parametros,
    })
    _escribir(carpeta, "variables.json", {"origen": contexto.origen_variables})

    for ambito, contenido in contexto.ficheros_variables.items():
        _escribir(carpeta, f"generado/vars/{ambito}.yml", contenido)
    if contexto.pipeline_yaml:
        _escribir(carpeta, "generado/azure-pipelines.yml", contexto.pipeline_yaml)
    for cual, texto in (contexto.descripciones or {}).items():
        _escribir(carpeta, f"descripcion-pr-{cual}.md", texto)

    recorridos = [{"estado": e, "naturaleza": naturaleza_por_estado[e]} for e in contexto.traza]
    _escribir(carpeta, "metadata.json", {
        "momento": marca,
        "backend": os.environ.get("MODEL_BACKEND", "lmstudio"),
        "modelo": os.environ.get("LM_STUDIO_MODEL") if os.environ.get("MODEL_BACKEND", "lmstudio") == "lmstudio"
                  else os.environ.get("GEMINI_MODEL"),
        "llamadas_al_modelo": llamadas_modelo,
        "estados_recorridos": recorridos,
        "turnos_de_conversacion": sum(1 for m in contexto.historial if m["role"] == "user"),
    })

    _escribir(carpeta, "resultado.json", {
        "confirmado_por_humano": contexto.confirmado,
        "pull_requests": contexto.urls_prs,
        "orden_de_merge": [
            "1. El del repo de codigo (vars/*.yml)",
            "2. El del repo pipelines (azure-pipelines.yml)",
        ] if contexto.urls_prs else [],
    })

    return carpeta


if __name__ == "__main__":
    print(f"Carpetas de ejecucion en {CARPETA_RUNS}:")
    for c in sorted(CARPETA_RUNS.iterdir()):
        if c.is_dir():
            ficheros = sorted(str(f.relative_to(c)) for f in c.rglob("*") if f.is_file())
            print(f"\n  {c.name}")
            for f in ficheros:
                print(f"    {f}")
