<!-- ══════════════════════════ TÍTULO ══════════════════════════ -->
<div align="center">
  <img src="docs/title-banner.svg" width="100%" alt="Servicedesk"/>
</div>

<!-- ══════════════════════ IDIOMAS / LANGUAGES ══════════════════════ -->
<div align="center">
<a href="README.md"><img src="https://img.shields.io/badge/Português-1987F0?style=for-the-badge" alt="Português"/></a>
<a href="README.en.md"><img src="https://img.shields.io/badge/English-555555?style=for-the-badge" alt="English"/></a>
<a href="README.es.md"><img src="https://img.shields.io/badge/Español-555555?style=for-the-badge" alt="Español"/></a>
</div>

<div align="center">
<img src="https://img.shields.io/badge/Django_5.2-092E20?style=flat-square&logo=django&logoColor=white" alt="django"/>
<img src="https://img.shields.io/badge/DRF-A30000?style=flat-square" alt="drf"/>
<img src="https://img.shields.io/badge/Celery-37814A?style=flat-square&logo=celery&logoColor=white" alt="celery"/>
<img src="https://img.shields.io/badge/PostgreSQL-4169E1?style=flat-square&logo=postgresql&logoColor=white" alt="postgres"/>
<img src="https://img.shields.io/badge/License-MIT-2E7D32?style=flat-square" alt="license"/>
</div>

<div align="center">
<a href="#o-que-tem-aqui"><img src="https://img.shields.io/badge/▸_O_QUE_TEM_AQUI-1987F0?style=for-the-badge" alt="oquetem"/></a>
<a href="#quickstart"><img src="https://img.shields.io/badge/▸_QUICKSTART-000000?style=for-the-badge" alt="quickstart"/></a>
<a href="#como-o-relógio-de-sla-funciona"><img src="https://img.shields.io/badge/▸_RELÓGIO_DE_SLA-1987F0?style=for-the-badge" alt="sla"/></a>
<a href="#arquitetura"><img src="https://img.shields.io/badge/▸_ARQUITETURA-000000?style=for-the-badge" alt="arquitetura"/></a>
<a href="#api"><img src="https://img.shields.io/badge/▸_API-1987F0?style=for-the-badge" alt="api"/></a>
</div>

<br/>

> ⏱️ **Um ticket aberto às 17h de sexta com meta de 2h vence às 10h de segunda**, não às 19h de sexta. O relógio só conta minutos úteis.

<div align="center">
  <img src="docs/screenshot.png" width="100%" alt="Servicedesk"/>
</div>

## O que tem aqui

- **Motor de SLA em horário comercial** ([`tickets/sla.py`](tickets/sla.py)) sem dependências além da stdlib. Dias úteis, horário de expediente, feriados e timezones. Funções puras, cobertas por 14 testes só delas.
- **Máquina de estados protegida** ([`tickets/services.py`](tickets/services.py)). Toda transição é checada contra uma tabela explícita — um ticket cancelado não pode voltar à vida silenciosamente. Movimentos ilegais respondem `409 Conflict` em vez de corromper o registro.
- **Um relógio que pausa.** Mover um ticket para *Pending requester* para o timer de resolução, e retomar empurra o prazo pra frente exatamente pelos minutos úteis perdidos.
- **Escalonamento automático.** Um job do Celery beat varre violações a cada poucos minutos, marca, sobe o nível de escalonamento e reatribui o ticket ao líder do time. A varredura é idempotente.
- **Trilha de auditoria append-only.** Criação, mudanças de status, atribuições, comentários, violações e escalonamentos — tudo em `AuditEvent`, escrito só pela camada de serviço.
- **Acesso por papel.** Solicitantes veem e respondem só seus próprios tickets. Agentes veem as filas do time. Notas internas nunca chegam a quem abriu o ticket.
- **Reprecificação de prioridade.** Subir um ticket de Normal pra Urgente recalcula os dois prazos sob a nova política.
- **Schema OpenAPI** servido por drf-spectacular, com Swagger UI na raiz.
- **60 testes**, ruff limpo, `check --deploy` limpo, CI no GitHub Actions.

## Quickstart

**Com Docker:**
```bash
git clone https://github.com/geoggrigori/servicedesk.git
cd servicedesk
docker compose up --build
```
Sobe PostgreSQL, Redis, a API, um worker Celery e o beat. Migra e semeia dados de demo. Abra <http://localhost:8000> pra docs da API e <http://localhost:8000/admin/> pro admin — login `admin` / `demo12345`.

**Sem Docker:**
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env

python manage.py migrate
python manage.py seed_demo
python manage.py runserver
```
SQLite é o padrão. Aponte `DATABASE_URL` pro Postgres quando quiser.

Pra ver o sweeper de SLA funcionando, rode o worker e o scheduler em dois terminais:
```bash
celery -A config worker -l info
celery -A config beat -l info
```

## Como o relógio de SLA funciona

Uma política define dois prazos por prioridade, por time, em minutos:

| Prioridade | Primeira resposta | Resolução |
|---|---|---|
| Urgente | 15 min | 4 h |
| Alta | 1 h | 8 h |
| Normal | 4 h | 24 h |
| Baixa | 8 h | 48 h |

Esses minutos são **úteis**. Com um calendário de segunda a sexta, 09:00–18:00:
- Um ticket aberto às **17h de sexta** com meta de 2h vence às **10h de segunda**, não às 19h de sexta.
- Um ticket aberto às **3h da manhã** começa a contar às 09:00.
- Um feriado listado em `BUSINESS_HOLIDAYS` é pulado como um fim de semana.
- Tempo em *Pending requester* é devolvido ao prazo, também só em minutos úteis.

Uma política pode desligar isso com `business_hours_only=False` e usar o relógio de parede normal, pra times 24/7.

## Arquitetura

```
config/          settings, app Celery, agenda do beat, URLs
accounts/        modelo de usuário custom com papéis admin/agente/solicitante
tickets/
  models.py      Team, SlaPolicy, Ticket, Comment, AuditEvent
  sla.py         aritmética de horário comercial, funções puras
  services.py    único lugar que muda tickets: máquina de estados + auditoria
  tasks.py       o sweeper de violações e o job de escalonamento
  views.py       viewsets DRF, ações custom, relatório de SLA
```

A regra que sustenta o código: **nada muda um ticket fora de `services.py`**. API, ações do admin e jobs do Celery chamam as mesmas funções — por isso a máquina de estados, os timestamps de SLA e a trilha de auditoria nunca desalinham.

## API

Autentica com JWT:
```bash
curl -X POST localhost:8000/api/v1/auth/token/ \
  -H 'Content-Type: application/json' \
  -d '{"username": "agent1", "password": "demo12345"}'
```

| Método | Endpoint | O que faz |
|---|---|---|
| `GET` | `/api/v1/tickets/` | Lista, restrita ao que o chamador pode ver |
| `POST` | `/api/v1/tickets/` | Abre um ticket, prazos calculados na hora |
| `POST` | `/api/v1/tickets/{id}/transition/` | Move status, `409` se a máquina proibir |
| `POST` | `/api/v1/tickets/{id}/assign/` | Atribui a um agente |
| `POST` | `/api/v1/tickets/{id}/priority/` | Reprecifica, recalculando os dois prazos |
| `GET`/`POST` | `/api/v1/tickets/{id}/comments/` | Lê ou adiciona resposta, pública ou interna |
| `GET` | `/api/v1/reports/sla/` | Contadores de saúde da fila, só agentes |
| `GET` | `/api/schema/` | Schema OpenAPI 3 |

Filtros úteis: `?status=new&status=in_progress`, `?breached=true`, `?unassigned=true`, `?team=infra`, `?search=TCK-00042`.

## Testes

```bash
pytest              # 60 testes
ruff check .        # lint
```

A suíte é propositalmente concentrada nas regras, não na plumbing: aritmética de horário comercial, tabela de transição, comportamento de pausa/retomada, e a idempotência do sweeper.

## Configuração

Tudo vem do ambiente — veja [`.env.example`](.env.example) pra lista completa.

| Variável | Padrão | Nota |
|---|---|---|
| `TIME_ZONE` | `America/Sao_Paulo` | Calendário avaliado nesse fuso |
| `BUSINESS_WORKDAYS` | `0,1,2,3,4` | Segunda é 0 |
| `BUSINESS_START`/`BUSINESS_END` | `09:00`/`18:00` | |
| `SLA_SWEEP_MINUTES` | `5` | Frequência do sweeper |

## Licença

[MIT](LICENSE).

<div align="center">
  <img src="https://file.loading.io/color/feature/thumb/Blues-8.png?" width="100%" height="10px" alt="divider"/>
</div>

<p align="center"><sub>Desenvolvido por <strong><a href="https://github.com/geoggrigori">Grigori</a></strong> · 2026</sub></p>
