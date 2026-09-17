"""T2.2 (parte determinista) - el bucle de turnos de la conversacion.

ESTE FICHERO NO IMPORTA NADA DE `llm/`. Compruebalo: la unica pieza que habla
con un modelo es requisitos/extractor.py, y aqui se usa como una funcion mas.
Todo lo que decide el comportamiento del chat --cuando parar, que se guarda, que
pasa si el usuario se equivoca-- es codigo normal y esta aqui.

El reparto, que es lo que hace esto fiable:

    el modelo PROPONE un diccionario parcial      -> extractor.py (LLM)
    el codigo lo FUNDE sobre el estado previo     -> esquema.fusionar()
    el codigo decide SI FALTA ALGO                -> esquema.slots_que_faltan()
    el codigo decide SI SE HA TERMINADO           -> aqui

Un detalle de diseno que merece la pena: hay DOS clases de error y se tratan
distinto.
  - Si el MODELO devuelve algo invalido (campo inventado, valor fuera de la
    enumeracion, JSON roto), se reintenta contra el modelo con el error como
    feedback. Es culpa suya y puede corregirlo.
  - Si el USUARIO dice un repositorio que no existe en Azure DevOps, NO se
    reintenta contra el modelo: el modelo transcribio bien lo que oyo. Se
    descarta ese campo y se le pregunta al humano, ensenandole los repos que si
    existen. Confundir estos dos casos hace que el sistema le eche la culpa al
    modelo de los errores de la persona, y viceversa.

Ejecuta la conversacion de ejemplo con: .venv/bin/python -m requisitos.recolector
Para hablar tu:                         .venv/bin/python -m requisitos.recolector -i
"""
from dataclasses import dataclass, field

from requisitos.esquema import (
    DESCRIPCIONES,
    Requisitos,
    descripcion,
    esta_completo,
    fusionar,
    slots_que_faltan,
    slots_recomendados_vacios,
)
from requisitos.extractor import PropuestaTurno, proponer


@dataclass
class Sesion:
    """El estado completo de una conversacion. Es lo unico que hay que guardar
    para reanudarla: el modelo no tiene memoria propia."""

    requisitos: Requisitos = field(default_factory=Requisitos)
    historial: list[dict] = field(default_factory=list)
    cliente_ado: object | None = None  # opcional: si esta, se valida repo_codigo

    @property
    def completo(self) -> bool:
        return esta_completo(self.requisitos)

    def _validar_propuesta(self, propuesta: PropuestaTurno) -> None:
        """Se le pasa al extractor para que su reintento sepa que es valido.

        Vive AQUI y no alli a proposito: el extractor sabe hablar con un modelo y
        reintentar, pero no sabe que es un requisito valido. Esa definicion esta
        en requisitos/esquema.py, que no depende de ningun LLM.
        """
        fusionar(self.requisitos, propuesta.requisitos_actualizados)  # lanza si no encaja

    def _comprobar_repo(self) -> str | None:
        """Si el repo dictado por el usuario no existe, lo descarta y devuelve la
        pregunta a hacerle. None si todo bien (o si no hay cliente de ADO)."""
        if self.cliente_ado is None or self.requisitos.repo_codigo is None:
            return None

        from ado.cliente import ErrorAdo
        from requisitos.esquema import validar_repo_codigo

        try:
            validar_repo_codigo(self.cliente_ado, self.requisitos)
            return None
        except ErrorAdo as error:
            equivocado = self.requisitos.repo_codigo
            # Se descarta el campo: dejarlo puesto haria que slots_que_faltan()
            # diera el visto bueno a un destino que no existe.
            self.requisitos = Requisitos(**{**self.requisitos.model_dump(), "repo_codigo": None})
            disponibles = str(error).split("Hay: ")[-1].split(". Ejecuta")[0]
            return f"El repositorio '{equivocado}' no existe. Los disponibles son: {disponibles}. ¿Cual es?"

    def _pregunta_de_respaldo(self) -> str | None:
        """Pregunta por el primer slot vacio, formulada por CODIGO.

        Existe porque medido con gemma-3-4b: cuando ya no faltan bloqueantes, el
        modelo devuelve pregunta_al_usuario=null aunque queden recomendados
        vacios, pese a que el prompt le pide lo contrario. Insistirle en el
        prompt seria pelearse con el modelo por algo que no le corresponde:
        "¿cual es la version del lenguaje?" no necesita un LLM para formularse.
        Se deriva de DESCRIPCIONES, la misma fuente que ve el modelo en su prompt.

        Es la tesis del proyecto aplicada a un detalle: si el codigo puede
        hacerlo, no se le pide al modelo. Y de paso deja de depender de que un
        modelo pequeno obedezca una instruccion secundaria.
        """
        pendientes = slots_que_faltan(self.requisitos) or slots_recomendados_vacios(self.requisitos)
        if not pendientes:
            return None
        slot = pendientes[0]
        return f"¿{DESCRIPCIONES[slot].capitalize()}?"

    def responder(self, mensaje_usuario: str) -> str | None:
        """Un turno completo. Devuelve la pregunta para el humano, o None si el
        modelo no tiene nada mas que preguntar.

        OJO con la diferencia, que la primera version de este metodo se comio:
        "no falta nada IMPRESCINDIBLE" (`self.completo`) NO es "no hay nada que
        preguntar". Los slots recomendados no bloquean, pero cada uno vacio es
        una adivinanza que tendra que hacer el LLM en T3.3 -- que es justo lo que
        SLOTS_RECOMENDADOS existe para evitar. Asi que se sigue preguntando por
        ellos; quien decide parar es el humano (T2.3), no esta funcion.
        """
        self.historial.append({"role": "user", "content": mensaje_usuario})

        propuesta = proponer(
            self.historial,
            self.requisitos,
            slots_que_faltan(self.requisitos),
            slots_recomendados_vacios(self.requisitos),
            validar=self._validar_propuesta,
        )

        self.requisitos = fusionar(self.requisitos, propuesta.requisitos_actualizados)

        # El error del usuario se trata aqui, no reintentando contra el modelo.
        pregunta_repo = self._comprobar_repo()
        pregunta = pregunta_repo or propuesta.pregunta_al_usuario or self._pregunta_de_respaldo()

        # El historial guarda lo que el modelo DIJO, no el JSON entero: en el
        # siguiente turno el estado ya viaja aparte y repetirlo solo gastaria
        # contexto y le daria ocasion de contradecirse.
        if pregunta:
            self.historial.append({"role": "assistant", "content": pregunta})

        return pregunta


# Cuatro turnos que ejercitan los cuatro caminos que importan:
#   1. extraccion parcial -> el chat pregunta por el bloqueante que falta
#   2. repo inexistente   -> RECUPERACION DETERMINISTA (no es culpa del modelo,
#                            asi que no se reintenta contra el; se descarta el
#                            campo y se le pregunta al humano con la lista real)
#   3. correccion         -> ya no faltan bloqueantes, pero SIGUE preguntando por
#                            los recomendados (cada uno vacio es una adivinanza
#                            que tendria que hacer el LLM en T3.3)
#   4. los tres de golpe  -> no queda nada que preguntar
CONVERSACION_DE_EJEMPLO = [
    "necesito una pipeline para un servicio en Java",
    "el repo se llama demo-servicio-jaba",          # mal escrito a proposito
    "perdon, demo-servicio-java",
    "es Java 17, va en Docker, y la version es 1.0.0",
]


def _demo_guionizada(sesion: Sesion) -> None:
    for mensaje in CONVERSACION_DE_EJEMPLO:
        print(f"\n\033[1musuario\033[0m: {mensaje}")
        try:
            pregunta = sesion.responder(mensaje)
        except Exception as error:
            print(f"\033[1magente \033[0m: [turno fallido, la sesion sigue] "
                  f"{type(error).__name__}: {error}")
            continue
        marca = "" if not sesion.completo else "  [ya se puede avanzar]"
        print(f"\033[1magente \033[0m: {pregunta or '(nada mas que preguntar)'}{marca}")
        print(f"         └─ bloqueantes={slots_que_faltan(sesion.requisitos)} "
              f"recomendados_vacios={slots_recomendados_vacios(sesion.requisitos)}")


def _demo_interactiva(sesion: Sesion) -> None:
    print("Escribe 'salir' para terminar.\n")
    pregunta = "¿Que pipeline necesitas?"
    while True:
        print(f"\033[1magente\033[0m: {pregunta}")
        mensaje = input("\033[1mtu\033[0m: ").strip()
        if mensaje.lower() in ("salir", "exit", "quit"):
            return
        if mensaje.lower() in ("listo", "ya", "adelante") and sesion.completo:
            return
        if not mensaje:
            continue
        try:
            pregunta = sesion.responder(mensaje)
        except Exception as error:
            # Un fallo de un turno no debe tirar la sesion entera: es la leccion
            # de poc-agentes (un 429 pasajero mataba toda la conversacion), y aqui
            # ya se ha visto en real dos veces -- el modelo agotando su presupuesto
            # razonando, y un 400 "Model reloaded" de LM Studio al recargar. Por
            # eso se captura Exception y no RuntimeError: los transitorios no
            # vienen todos de nuestro codigo.
            print(f"[error en este turno, la sesion sigue] {type(error).__name__}: {error}")
            continue
        if pregunta is None:
            print("\n\033[1magente\033[0m: no me queda nada por preguntar.")
            return
        if sesion.completo:
            # Los recomendados no bloquean: se ofrece salida en cada turno.
            print("       (ya tengo lo imprescindible; responde 'listo' para avanzar)")


if __name__ == "__main__":
    import sys

    from dotenv import load_dotenv

    load_dotenv()

    cliente_ado = None
    try:
        from ado.cliente import ClienteAdo

        cliente_ado = ClienteAdo.desde_entorno()
        print("[recolector] validacion de repo_codigo contra Azure DevOps: ACTIVA")
    except Exception as error:
        print(f"[recolector] sin validacion contra ADO ({error})")

    sesion = Sesion(cliente_ado=cliente_ado)
    try:
        if "-i" in sys.argv or "--interactivo" in sys.argv:
            _demo_interactiva(sesion)
        else:
            _demo_guionizada(sesion)

        print("\n\033[1m=== requisitos recogidos ===\033[0m")
        print(descripcion(sesion.requisitos))
        print(f"\ncompleto: {sesion.completo}")
    finally:
        if cliente_ado is not None:
            cliente_ado.cerrar()
