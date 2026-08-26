<!-- ══════════════════════════ PORTADA ══════════════════════════ -->
<div align="center">
  <img src="docs/title-banner.svg" width="100%" alt="Servicedesk"/>
</div>

<!-- ══════════════════════ IDIOMAS / LANGUAGES ══════════════════════ -->
<div align="center">
<a href="README.md"><img src="https://img.shields.io/badge/Português-555555?style=for-the-badge" alt="Português"/></a>
<a href="README.en.md"><img src="https://img.shields.io/badge/English-555555?style=for-the-badge" alt="English"/></a>
<a href="README.es.md"><img src="https://img.shields.io/badge/Español-1987F0?style=for-the-badge" alt="Español"/></a>
</div>

<div align="center">
<img src="https://img.shields.io/badge/Django_5.2-092E20?style=flat-square&logo=django&logoColor=white" alt="django"/>
<img src="https://img.shields.io/badge/DRF-A30000?style=flat-square" alt="drf"/>
<img src="https://img.shields.io/badge/Celery-37814A?style=flat-square&logo=celery&logoColor=white" alt="celery"/>
<img src="https://img.shields.io/badge/PostgreSQL-4169E1?style=flat-square&logo=postgresql&logoColor=white" alt="postgres"/>
<img src="https://img.shields.io/badge/License-MIT-2E7D32?style=flat-square" alt="license"/>
</div>

<div align="center">
<a href="#qué-hay-aquí"><img src="https://img.shields.io/badge/▸_QUÉ_HAY_AQUÍ-1987F0?style=for-the-badge" alt="quehay"/></a>
<a href="#inicio-rápido"><img src="https://img.shields.io/badge/▸_INICIO_RÁPIDO-000000?style=for-the-badge" alt="inicio"/></a>
<a href="#cómo-funciona-el-reloj-de-sla"><img src="https://img.shields.io/badge/▸_RELOJ_DE_SLA-1987F0?style=for-the-badge" alt="sla"/></a>
<a href="#arquitectura"><img src="https://img.shields.io/badge/▸_ARQUITECTURA-000000?style=for-the-badge" alt="arquitectura"/></a>
<a href="#api"><img src="https://img.shields.io/badge/▸_API-1987F0?style=for-the-badge" alt="api"/></a>
</div>

<br/>

> ⏱️ **Un ticket abierto a las 17h del viernes con meta de 2h vence a las 10h del lunes**, no a las 19h del viernes. El reloj solo corre en minutos hábiles.

<div align="center">
  <img src="docs/screenshot.png" width="100%" alt="Servicedesk"/>
</div>

## Qué hay aquí

- **Motor de SLA en horario laboral** ([`tickets/sla.py`](tickets/sla.py)) sin dependencias más allá de la stdlib. Días hábiles, horario de atención, feriados y zonas horarias. Funciones puras, cubiertas por 14 pruebas propias.
- **Máquina de estados protegida** ([`tickets/services.py`](tickets/services.py)). Cada movimiento se verifica contra una tabla de transición explícita — un ticket cancelado no puede volver a la vida silenciosamente. Los movimientos ilegales responden `409 Conflict`.
- **Un reloj que se pausa.** Mover un ticket a *Pending requester* detiene el temporizador de resolución, y reanudar empuja el plazo hacia adelante exactamente por los minutos hábiles perdidos.
- **Escalamiento automático.** Un job de Celery beat barre incumplimientos cada pocos minutos, los marca, sube el nivel de escalamiento y reasigna el ticket al líder del equipo. El barrido es idempotente.
- **Registro de auditoría append-only.** Creación, cambios de estado, asignaciones, comentarios, incumplimientos y escalamientos — todo en `AuditEvent`, escrito solo por la capa de servicio.
- **Acceso por rol.** Los solicitantes ven y responden solo sus propios tickets. Los agentes ven las colas de su equipo. Las notas internas nunca llegan a quien abrió el ticket.
- **Reprecio de prioridad.** Subir un ticket de Normal a Urgente recalcula ambos plazos bajo la nueva política.
- **Schema OpenAPI** servido por drf-spectacular, con Swagger UI en la raíz.
- **60 pruebas**, ruff limpio, `check --deploy` limpio, CI en GitHub Actions.

## Inicio rápido

**Con Docker:**
```bash
git clone https://github.com/geoggrigori/servicedesk.git
cd servicedesk
docker compose up --build
```
Levanta PostgreSQL, Redis, la API, un worker de Celery y el beat. Migra y siembra datos de demo. Abre <http://localhost:8000> para la doc de la API y <http://localhost:8000/admin/> para el admin — inicia sesión como `admin` / `demo12345`.

**Sin Docker:**
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env

python manage.py migrate
python manage.py seed_demo
python manage.py runserver
```
SQLite es el predeterminado. Apunta `DATABASE_URL` a PostgreSQL cuando quieras.

Para ver el sweeper de SLA funcionando, corre el worker y el scheduler en dos terminales más:
```bash
celery -A config worker -l info
celery -A config beat -l info
```

## Cómo funciona el reloj de SLA

Una política define dos metas por prioridad, por equipo, en minutos:

| Prioridad | Primera respuesta | Resolución |
|---|---|---|
| Urgente | 15 min | 4 h |
| Alta | 1 h | 8 h |
| Normal | 4 h | 24 h |
| Baja | 8 h | 48 h |

Esos minutos son **hábiles**. Con un calendario de lunes a viernes, 09:00 a 18:00:
- Un ticket abierto a las **17:00 del viernes** con meta de 2h vence a las **10:00 del lunes**, no a las 19:00 del viernes.
- Un ticket abierto a las **3am** empieza a contar a las 09:00.
- Un feriado listado en `BUSINESS_HOLIDAYS` se salta como un fin de semana.
- El tiempo en *Pending requester* se devuelve al plazo, también solo en minutos hábiles.

Una política puede optar por `business_hours_only=False` y usar el reloj de pared normal, para equipos 24/7.

## Arquitectura

```
config/          settings, app Celery, agenda del beat, URLs
accounts/        modelo de usuario custom con roles admin/agente/solicitante
tickets/
  models.py      Team, SlaPolicy, Ticket, Comment, AuditEvent
  sla.py         aritmética de horario laboral, funciones puras
  services.py    único lugar que cambia tickets: máquina de estados + auditoría
  tasks.py       el sweeper de incumplimientos y el job de escalamiento
  views.py       viewsets DRF, acciones custom, reporte de SLA
```

La regla en la que se apoya el código: **nada muta un ticket fuera de `services.py`**. La API, las acciones del admin y los jobs de Celery llaman a las mismas funciones — por eso la máquina de estados, los timestamps de SLA y el registro de auditoría nunca se desalinean.

## API

Autentica con JWT:
```bash
curl -X POST localhost:8000/api/v1/auth/token/ \
  -H 'Content-Type: application/json' \
  -d '{"username": "agent1", "password": "demo12345"}'
```

| Método | Endpoint | Qué hace |
|---|---|---|
| `GET` | `/api/v1/tickets/` | Lista, restringida a lo que el llamador puede ver |
| `POST` | `/api/v1/tickets/` | Abre un ticket, plazos calculados al instante |
| `POST` | `/api/v1/tickets/{id}/transition/` | Mueve el estado, `409` si la máquina lo prohíbe |
| `POST` | `/api/v1/tickets/{id}/assign/` | Asigna a un agente |
| `POST` | `/api/v1/tickets/{id}/priority/` | Reprecio, recalculando ambos plazos |
| `GET`/`POST` | `/api/v1/tickets/{id}/comments/` | Lee o agrega una respuesta, pública o interna |
| `GET` | `/api/v1/reports/sla/` | Contadores de salud de la cola, solo agentes |
| `GET` | `/api/schema/` | Schema OpenAPI 3 |

Filtros útiles: `?status=new&status=in_progress`, `?breached=true`, `?unassigned=true`, `?team=infra`, `?search=TCK-00042`.

## Pruebas

```bash
pytest              # 60 pruebas
ruff check .        # lint
```

La suite está deliberadamente concentrada en las reglas y no en la plumbing: la aritmética de horario laboral, la tabla de transición, el comportamiento de pausa/reanudación, y la idempotencia del sweeper.

## Configuración

Todo se lee del entorno — ver [`.env.example`](.env.example) para la lista completa.

| Variable | Predeterminado | Notas |
|---|---|---|
| `TIME_ZONE` | `America/Sao_Paulo` | El calendario se evalúa en esta zona |
| `BUSINESS_WORKDAYS` | `0,1,2,3,4` | Lunes es 0 |
| `BUSINESS_START`/`BUSINESS_END` | `09:00`/`18:00` | |
| `SLA_SWEEP_MINUTES` | `5` | Frecuencia del sweeper |

## Licencia

[MIT](LICENSE).

<div align="center">
  <img src="https://file.loading.io/color/feature/thumb/Blues-8.png?" width="100%" height="10px" alt="divider"/>
</div>

<p align="center"><sub>Desarrollado por <strong><a href="https://github.com/geoggrigori">Grigori</a></strong> · 2026</sub></p>
