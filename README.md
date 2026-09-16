# poc-agentes-maf

Segunda iteración de [`poc-agentes`](../poc-agentes). El usuario describe en un chat de terminal la pipeline que necesita; el sistema elige plantilla del catálogo en Azure DevOps, genera el pipeline hijo y abre un Pull Request en el repo destino.

- **Plan completo:** [docs/Plan poc-ado.md](docs/Plan%20poc-ado.md)
- **Reglas de trabajo:** [AGENTS.md](AGENTS.md)
- **Dónde retomar:** [docs/notas-agentes/Bitacora-de-sesiones.md](docs/notas-agentes/Bitacora-de-sesiones.md)

## Puesta en marcha

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env   # y rellenar
```
