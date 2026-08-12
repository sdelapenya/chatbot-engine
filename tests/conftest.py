"""Arranque de los tests.

`server.py` lee toda su configuración en el import (constantes de módulo), así
que el entorno tiene que quedar montado ANTES de importarlo. Se apunta a las
fixturas de este directorio para que ningún test dependa de los datos de una
instancia real ni del `.env` del servidor.
"""

import os
import sys
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"

# ENV_FILE a una ruta que no existe (load_dotenv se lo traga sin quejarse): si
# se dejara el valor por defecto, los tests cargarían el fichero de secretos de
# la máquina donde se ejecuten. No es una fixtura vacía porque el .gitignore
# manda a paseo cualquier *.env y no llegaría al repo.
os.environ.update({
    "ENV_FILE": str(FIXTURES / "sin-env-a-proposito"),
    "PROMPT_FILE": str(FIXTURES / "prompt.txt"),
    "KEYWORDS_FILE": str(FIXTURES / "keywords.txt"),
    "FRONTEND_DIR": str(FIXTURES / "frontend"),
    "LOG_DIR": str(Path(__file__).parent / ".datos-test"),
    "COMPANY_NAME": "Industrias Ejemplo S.L.",
    "BOT_NAME": "Nora",
    "PANEL_TOKEN": "token-de-prueba",
    "GROQ_API_KEY": "clave-falsa-de-test",
    "GMAIL_APP_PASSWORD": "clave-falsa-de-test",
    "LEAD_MIN_KEYWORDS": "1",
})

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import pytest  # noqa: E402

import server  # noqa: E402


@pytest.fixture
def msgs():
    """Atajo para construir historiales: msgs(("user", "hola"), ("assistant", "…"))."""
    def _build(*pares):
        return [server.Message(role=rol, content=texto) for rol, texto in pares]
    return _build
