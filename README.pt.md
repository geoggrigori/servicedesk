# Servicedesk 🎫

[English](README.md) · **Português** · [Español](README.es.md)

Um backend de chamados construído em cima da parte que a maioria dos clones de help desk pula: **o relógio de SLA que só corre em horário comercial**.

Cada chamado carrega uma meta de primeira resposta e uma meta de resolução. Esses prazos são calculados em minutos úteis, então um chamado aberto às 17h de sexta não chega atrasado na segunda de manhã. Quando o chamado fica parado esperando o solicitante, o relógio para. Se mesmo assim o prazo estoura, uma tarefa agendada marca a violação e escala o chamado para o líder do time.

> Feito com Django 5.2, Django REST Framework, Celery e PostgreSQL.

---

## ✨ O que tem aqui

- **Motor de SLA em horário comercial** ([`tickets/sla.py`](tickets/sla.py)), sem nenhuma dependência além da biblioteca padrão. Dias úteis, horário de expediente, feriados e fuso. São funções puras, então as regras têm 14 testes só para elas.
- **Máquina de estados protegida** ([`tickets/services.py`](tickets/services.py)). Toda mudança passa por uma tabela explícita de transições, então um chamado cancelado não volta à vida sem ninguém ver. Movimento inválido responde `409 Conflict` em vez de corromper o registro.
- **Relógio que pausa.** Mover para *Pending requester* congela o timer de resolução, e ao retomar o prazo é empurrado exatamente pelos minutos úteis perdidos. Noite e fim de semana esperando não custam nada ao solicitante.
- **Escalonamento automático.** Uma tarefa do Celery beat varre as violações a cada poucos minutos, carimba, sobe o nível de escalonamento e passa o chamado para o líder do time. A varredura é idempotente, então rodar duas vezes nunca conta em dobro.
- **Trilha de auditoria append-only.** Criação, mudanças de status, atribuições, comentários, violações e escalonamentos caem em `AuditEvent`, escrito só pela camada de serviço e somente leitura no admin.
- **Acesso por papel.** Solicitante vê e responde os próprios chamados. Agente vê a fila do seu time. Nota interna nunca chega em quem abriu o chamado.
- **Reprecificação de prioridade.** Subir de Normal para Urgente recalcula os dois prazos pela nova política, em vez de deixar metas velhas para trás.
- **Schema OpenAPI** servido pelo drf-spectacular, com Swagger UI na raiz.
- **60 testes**, ruff limpo, `check --deploy` limpo, CI no GitHub Actions.

---

## 🚀 Começando

### Com Docker

```bash
git clone https://github.com/geoggrigori/servicedesk.git
cd servicedesk
docker compose up --build
```

Isso sobe PostgreSQL, Redis, a API, um worker Celery e o beat. Ele migra e popula dados de demonstração na subida. Abra <http://localhost:8000> para a documentação da API e <http://localhost:8000/admin/> para o admin, entrando com `admin` / `demo12345`.

### Sem Docker

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env

python manage.py migrate
python manage.py seed_demo
python manage.py runserver
```

O padrão é SQLite, então não precisa de mais nada rodando. Aponte `DATABASE_URL` para o PostgreSQL quando quiser.

Para ver a varredura de SLA funcionando, rode o worker e o agendador em outros dois terminais:

```bash
celery -A config worker -l info
celery -A config beat -l info
```

Ou defina `CELERY_TASK_ALWAYS_EAGER=True` e chame a tarefa direto do shell.

---

## 🕘 Como o relógio de SLA funciona

Uma política define duas metas por prioridade, por time, em minutos:

| Prioridade | Primeira resposta | Resolução |
|---|---|---|
| Urgente | 15 min | 4 h |
| Alta | 1 h | 8 h |
| Normal | 4 h | 24 h |
| Baixa | 8 h | 48 h |

Esses minutos são **úteis**. Com um calendário de segunda a sexta, 09:00 às 18:00:

- Um chamado aberto às **17:00 de sexta** com meta de 2 horas vence às **10:00 de segunda**, não às 19:00 de sexta.
- Um chamado aberto às **3 da manhã** começa a contar às 09:00, porque não havia ninguém no balcão.
- Um feriado listado em `BUSINESS_HOLIDAYS` é pulado como um fim de semana.
- O tempo em *Pending requester* é devolvido ao prazo, também só em minutos úteis.

Uma política pode optar por `business_hours_only=False` e usar tempo de relógio corrido, que é o que um time 24/7 quer.

Cada time tem suas políticas; uma política sem time funciona como fallback global.

---

## 🧱 Arquitetura

```
config/          settings, app do Celery, agendamento do beat, URLs
accounts/        modelo de usuário próprio com papéis admin / agente / solicitante
tickets/
  models.py      Team, SlaPolicy, Ticket, Comment, AuditEvent
  sla.py         aritmética de horário comercial, funções puras
  services.py    o único lugar onde chamado muda: máquina de estados + auditoria
  tasks.py       a varredura de violações e o job de escalonamento
  views.py       viewsets do DRF, ações customizadas, relatório de SLA
tests/           60 testes cobrindo relógio, serviços, tarefas e API
```

A regra que sustenta o código: **nada altera um chamado fora de `services.py`**. A API, as ações do admin e os jobs do Celery chamam as mesmas funções, e é por isso que a máquina de estados, os carimbos de SLA e a auditoria não conseguem se desencontrar.

---

## 🔌 API

Autenticação por JWT:

```bash
curl -X POST localhost:8000/api/v1/auth/token/ \
  -H 'Content-Type: application/json' \
  -d '{"username": "agent1", "password": "demo12345"}'
```

| Método | Endpoint | O que faz |
|---|---|---|
| `GET` | `/api/v1/tickets/` | Lista, limitada ao que o chamador pode ver |
| `POST` | `/api/v1/tickets/` | Abre um chamado, prazos calculados na hora |
| `GET` | `/api/v1/tickets/{id}/` | Detalhe, com comentários e histórico completo |
| `POST` | `/api/v1/tickets/{id}/transition/` | Muda status, `409` se a máquina proibir |
| `POST` | `/api/v1/tickets/{id}/assign/` | Atribui a um agente |
| `POST` | `/api/v1/tickets/{id}/priority/` | Reprecifica, recalculando os dois prazos |
| `GET` `POST` | `/api/v1/tickets/{id}/comments/` | Lê ou adiciona resposta, pública ou interna |
| `GET` | `/api/v1/reports/sla/` | Indicadores da fila, só para agentes |
| `GET` | `/api/schema/` | Schema OpenAPI 3 |

Filtros úteis: `?status=new&status=in_progress`, `?breached=true`, `?unassigned=true`, `?team=infra`, `?assignee=agent1`, `?search=TCK-00042`, `?ordering=-resolution_due_at`.

---

## 🧪 Testes

```bash
pytest              # 60 testes
ruff check .        # lint
ruff format --check .
```

A suíte pesa de propósito para o lado das regras, não do encanamento: a aritmética de horário comercial, a tabela de transições, o comportamento de pausar e retomar, o que conta como primeira resposta e a idempotência da varredura.

---

## ⚙️ Configuração

Tudo vem do ambiente. A lista completa está em [`.env.example`](.env.example).

| Variável | Padrão | Observação |
|---|---|---|
| `DATABASE_URL` | arquivo SQLite | Qualquer URL que o django-environ entenda |
| `TIME_ZONE` | `America/Sao_Paulo` | O calendário é avaliado neste fuso |
| `BUSINESS_WORKDAYS` | `0,1,2,3,4` | Segunda é 0 |
| `BUSINESS_START` / `BUSINESS_END` | `09:00` / `18:00` | |
| `BUSINESS_HOLIDAYS` | vazio | Datas ISO separadas por vírgula |
| `SLA_SWEEP_MINUTES` | `5` | De quanto em quanto tempo a varredura roda |
| `CELERY_TASK_ALWAYS_EAGER` | `False` | Roda tarefas inline durante o desenvolvimento |

Com `DEBUG=False` a aplicação liga sozinha redirecionamento HTTPS, HSTS e cookies seguros.

---

## 📄 Licença

MIT. Veja [LICENSE](LICENSE).
