# chatbot-engine

Motor de asistentes de captación de leads para webs de pymes: un backend FastAPI
y un widget de 450 líneas de JavaScript sin dependencias, que se embebe en
cualquier página con una etiqueta `<script>`.

La gracia no es el chat —eso lo hace el modelo— sino lo que hay alrededor: **una
sola base de código levanta instancias de sectores distintos** (industrial,
clínicas, servicios) cambiando ficheros de configuración, sin tocar Python. En mi
servidor corren siete a la vez, cada una con su dominio, su prompt y su buzón.

```html
<script
  src="https://tu-dominio/widget.js"
  data-url="https://tu-dominio"
  data-color="#2563eb"
  data-bot-name="Nora"
></script>
```

## Qué hace, además de responder

| | |
|---|---|
| **Detecta el lead** | email y teléfono en el texto del usuario, con las variantes que escribe la gente de verdad (`655 33 21 09`, `+34 655332109`) |
| **Resume la conversación** | una segunda llamada al modelo saca nombre, empresa, producto, urgencia y un resumen para el comercial |
| **Avisa por email** | HTML con la ficha del lead y la transcripción; opcionalmente dispara un webhook (n8n) |
| **Panel y CSV** | `/panel?token=…` para ver los leads y bajarlos |
| **Canal WhatsApp** | webhook de la Meta Cloud API sobre la misma lógica de conversación |

## Decisiones que explican el código

**Los textos del motor se pueden reescribir por instancia.** Los mensajes de
sistema estaban escritos para un vertical industrial —«pedido», «medidas»,
«equipo comercial»— y le hablaban así a un paciente de una clínica dental,
pisando su prompt. Ahora un `TEXTS_FILE=textos.json` sobreescribe solo las claves
que hagan falta; sin esa variable el comportamiento es el de siempre.

**Dos modos de conversación.** Por defecto hay una máquina de fases: primero
cualificar (qué necesita, cuánto), y solo después pedir el contacto. Funciona en
venta industrial y estorba en una recepción de clínica, donde la urgencia va
primero; ahí se pone `SIMPLE_CHAT=true` y el prompt manda.

**La respuesta del modelo se filtra antes de enseñarla.** Con
`ALLOW_PUBLIC_PRICES=false` cualquier cifra en euros se sustituye por la
respuesta de «presupuesto personalizado», y no se cierra la conversación
—«pulsa Enviar consulta»— mientras no haya email o teléfono. Un modelo que
improvisa un precio es un problema comercial, no un detalle de estilo.

**Una portada distinta por dominio, sin tocar código.** Si existe
`frontend/hosts/<dominio>.html` se sirve esa; si no, el `index.html` de siempre.
El nombre sale de la cabecera `Host`, que la pone el cliente, así que se valida
como hostname antes de tocar el disco.

**Un worker por instancia.** Las sesiones activas viven en memoria del proceso;
con dos workers una misma conversación saltaría entre estados distintos. Para el
tráfico de un chat en la web de una pyme sobra.

## Arrancar

```bash
cp .env.example .env      # GROQ_API_KEY y GMAIL_APP_PASSWORD son obligatorias
docker compose up --build
```

En http://localhost:8000 queda la página de prueba con el widget. Sin Docker:

```bash
pip install -r requirements.txt
cd backend && uvicorn server:app --port 8000
```

## Configuración

Todo por variables de entorno; la lista completa, comentada, está en
[`.env.example`](.env.example). Las que definen una instancia:

| Variable | Para qué |
|---|---|
| `PROMPT_FILE` | el prompt de sistema del negocio |
| `KEYWORDS_FILE` | palabras que marcan intención comercial en el lead |
| `TEXTS_FILE` | JSON plano que reescribe los textos del motor |
| `FRONTEND_DIR` | la web de esa instancia (widget, portada, panel) |
| `LOG_DIR` | leads, conversaciones y sesiones persistidas |
| `SIMPLE_CHAT` | apaga la máquina de fases |
| `ALLOW_PUBLIC_PRICES` | deja que el bot dé cifras |

Una instancia nueva es un directorio con esos ficheros y una unidad de systemd
—o un `docker compose` con otro `env_file`— apuntando al mismo código. En
[`deploy/chatbot@.service`](deploy/chatbot@.service) está la unidad plantilla:
`systemctl enable --now chatbot@clinica` levanta la instancia del directorio
`clinica` sin editar nada.

## Tests

```bash
pip install -r requirements-dev.txt
ruff check . && pytest
```

59 pruebas sobre lo que decide de verdad: detección de contacto, umbral de
palabras clave, filtros de la respuesta, textos por instancia, saneado del nombre
de fichero del CSV y validación de la cabecera `Host`. Ninguna llama a Groq, a
Meta ni a SMTP: si al ejecutarlas hiciera falta una credencial, es que se ha
colado una llamada de verdad. El CI las corre y además construye la imagen.

## Lo que este repo no es

No es un SaaS multi-tenant: cada instancia es un proceso con su configuración.
No hay base de datos —los leads se guardan en JSONL— porque a este volumen una
base de datos sería infraestructura que mantener sin nada a cambio. El proveedor
de IA es Groq y está cableado; cambiarlo es tocar una función, pero hoy no es
configurable.

---

MIT — Sergio de la Peña
