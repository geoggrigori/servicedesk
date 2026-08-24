# Servicedesk 🎫

[English](README.md) · [Português](README.pt.md) · **Español**

Un backend de tickets construido sobre la parte que la mayoría de los clones de help desk se salta: **el reloj de SLA que solo corre en horario laboral**.

Cada ticket lleva un objetivo de primera respuesta y uno de resolución. Esos plazos se calculan en minutos laborables, así que un ticket abierto a las 17:00 del viernes no llega tarde el lunes por la mañana. Cuando el ticket queda esperando al solicitante, el reloj se detiene. Si aun así el plazo se agota, una tarea programada marca el incumplimiento y escala el ticket al líder del equipo.

> Hecho con Django 5.2, Django REST Framework, Celery y PostgreSQL.

---

## ✨ Qué hay aquí

- **Motor de SLA en horario laboral** ([`tickets/sla.py`](tickets/sla.py)), sin más dependencias que la biblioteca estándar. Días hábiles, horario de apertura, festivos y zona horaria. Son funciones puras, así que las reglas tienen 14 pruebas propias.
- **Máquina de estados protegida** ([`tickets/services.py`](tickets/services.py)). Cada movimiento se comprueba contra una tabla explícita de transiciones, así que un ticket cancelado no vuelve a la vida sin que nadie lo vea. Un movimiento ilegal responde `409 Conflict` en lugar de corromper el registro.
- **Un reloj que se pausa.** Mover a *Pending requester* congela el temporizador de resolución, y al reanudar el plazo avanza exactamente los minutos laborables perdidos. Las noches y los fines de semana esperando no le cuestan nada al solicitante.
- **Escalado automático.** Una tarea de Celery beat barre los incumplimientos cada pocos minutos, los sella, sube el nivel de escalado y reasigna el ticket al líder del equipo. El barrido es idempotente, así que ejecutarlo dos veces nunca cuenta doble.
- **Rastro de auditoría append-only.** Creación, cambios de estado, asignaciones, comentarios, incumplimientos y escalados caen en `AuditEvent`, escrito solo por la capa de servicio y de solo lectura en el admin.
- **Acceso por rol.** El solicitante ve y responde sus propios tickets. El agente ve la cola de su equipo. Una nota interna nunca llega a quien abrió el ticket.
- **Recálculo por prioridad.** Subir de Normal a Urgente recalcula ambos plazos con la nueva política, en lugar de dejar objetivos obsoletos.
- **Esquema OpenAPI** servido por drf-spectacular, con Swagger UI en la raíz.
- **60 pruebas**, ruff limpio, `check --deploy` limpio, CI en GitHub Actions.

---

## 🚀 Inicio rápido

### Con Docker

```bash
git clone https://github.com/geoggrigori/servicedesk.git
cd servicedesk
docker compose up --build
```

Eso levanta PostgreSQL, Redis, la API, un worker de Celery y el beat. Migra y carga datos de demostración al arrancar. Abre <http://localhost:8000> para la documentación de la API y <http://localhost:8000/admin/> para el admin, entrando como `admin` / `demo12345`.

### Sin Docker

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env

python manage.py migrate
python manage.py seed_demo
python manage.py runserver
```

El valor por defecto es SQLite, así que no hace falta nada más corriendo. Apunta `DATABASE_URL` a PostgreSQL cuando quieras.

Para ver el barrido de SLA en acción, ejecuta el worker y el planificador en otras dos terminales:

```bash
celery -A config worker -l info
celery -A config beat -l info
```

O define `CELERY_TASK_ALWAYS_EAGER=True` y llama a la tarea desde el shell.

---

## 🕘 Cómo funciona el reloj de SLA

Una política define dos objetivos por prioridad y por equipo, en minutos:

| Prioridad | Primera respuesta | Resolución |
|---|---|---|
| Urgente | 15 min | 4 h |
| Alta | 1 h | 8 h |
| Normal | 4 h | 24 h |
| Baja | 8 h | 48 h |

Esos minutos son **laborables**. Con un calendario de lunes a viernes, de 09:00 a 18:00:

- Un ticket abierto a las **17:00 del viernes** con un objetivo de 2 horas vence a las **10:00 del lunes**, no a las 19:00 del viernes.
- Un ticket abierto a las **3 de la madrugada** empieza a contar a las 09:00, porque no había nadie en el mostrador.
- Un festivo listado en `BUSINESS_HOLIDAYS` se salta como un fin de semana.
- El tiempo en *Pending requester* se devuelve al plazo, también solo en minutos laborables.

Una política puede optar por `business_hours_only=False` y usar tiempo de reloj corrido, que es lo que quiere un equipo 24/7.

Cada equipo tiene sus políticas; una política sin equipo actúa como respaldo global.

---

## 🧱 Arquitectura

```
config/          settings, app de Celery, planificación del beat, URLs
accounts/        modelo de usuario propio con roles admin / agente / solicitante
tickets/
  models.py      Team, SlaPolicy, Ticket, Comment, AuditEvent
  sla.py         aritmética de horario laboral, funciones puras
  services.py    el único lugar donde cambia un ticket: máquina de estados + auditoría
  tasks.py       el barrido de incumplimientos y el job de escalado
  views.py       viewsets de DRF, acciones propias, informe de SLA
tests/           60 pruebas sobre el reloj, los servicios, las tareas y la API
```

La regla que sostiene el código: **nada modifica un ticket fuera de `services.py`**. La API, las acciones del admin y los jobs de Celery llaman a las mismas funciones, y por eso la máquina de estados, las marcas de SLA y la auditoría no pueden desincronizarse.

---

## 🔌 API

Autenticación con JWT:

```bash
curl -X POST localhost:8000/api/v1/auth/token/ \
  -H 'Content-Type: application/json' \
  -d '{"username": "agent1", "password": "demo12345"}'
```

| Método | Endpoint | Qué hace |
|---|---|---|
| `GET` | `/api/v1/tickets/` | Lista, limitada a lo que el llamador puede ver |
| `POST` | `/api/v1/tickets/` | Abre un ticket, plazos calculados al momento |
| `GET` | `/api/v1/tickets/{id}/` | Detalle, con comentarios e historial completo |
| `POST` | `/api/v1/tickets/{id}/transition/` | Cambia el estado, `409` si la máquina lo prohíbe |
| `POST` | `/api/v1/tickets/{id}/assign/` | Asigna a un agente |
| `POST` | `/api/v1/tickets/{id}/priority/` | Recalcula ambos plazos con la nueva prioridad |
| `GET` `POST` | `/api/v1/tickets/{id}/comments/` | Lee o añade una respuesta, pública o interna |
| `GET` | `/api/v1/reports/sla/` | Indicadores de la cola, solo agentes |
| `GET` | `/api/schema/` | Esquema OpenAPI 3 |

Filtros útiles: `?status=new&status=in_progress`, `?breached=true`, `?unassigned=true`, `?team=infra`, `?assignee=agent1`, `?search=TCK-00042`, `?ordering=-resolution_due_at`.

---

## 🧪 Pruebas

```bash
pytest              # 60 pruebas
ruff check .        # lint
ruff format --check .
```

La suite pesa a propósito del lado de las reglas y no de la fontanería: la aritmética de horario laboral, la tabla de transiciones, el comportamiento de pausar y reanudar, qué cuenta como primera respuesta y la idempotencia del barrido.

---

## ⚙️ Configuración

Todo se lee del entorno. La lista completa está en [`.env.example`](.env.example).

| Variable | Por defecto | Nota |
|---|---|---|
| `DATABASE_URL` | archivo SQLite | Cualquier URL que entienda django-environ |
| `TIME_ZONE` | `America/Sao_Paulo` | El calendario se evalúa en esta zona |
| `BUSINESS_WORKDAYS` | `0,1,2,3,4` | El lunes es 0 |
| `BUSINESS_START` / `BUSINESS_END` | `09:00` / `18:00` | |
| `BUSINESS_HOLIDAYS` | vacío | Fechas ISO separadas por comas |
| `SLA_SWEEP_MINUTES` | `5` | Cada cuánto corre el barrido |
| `CELERY_TASK_ALWAYS_EAGER` | `False` | Ejecuta tareas en línea durante el desarrollo |

Con `DEBUG=False` la aplicación activa por su cuenta las redirecciones HTTPS, HSTS y cookies seguras.

---

## 📄 Licencia

MIT. Ver [LICENSE](LICENSE).
