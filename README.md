# 💰 Economia Doméstica Inteligente

Sistema web de gestão financeira familiar em **Python + Flask + Jinja2 + PostgreSQL**.

| Módulo | O que faz |
|---|---|
| **Dashboard** | Receitas realizadas, despesas pagas, saldo atual, saldo previsto, alertas de vencimento, gráfico de despesas por categoria, entradas × saídas (6 meses) e progresso das metas. Período: mês atual, mês anterior ou personalizado (respeita o *dia de fechamento*). |
| **Receitas** | CRUD com categoria, data de recebimento, conta/origem e observações; filtros por período, categoria e faixa de valor. |
| **Despesas** | CRUD com categoria e forma de pagamento; filtros por período, categoria, forma de pagamento e faixa de valor. |
| **Contas a pagar** | Status `Pendente`/`Pago`/`Atrasado`/`Cancelado` (atraso automático), destaque de vencidas e próximas, parcelamento (cria N contas), recorrência mensal e **baixa rápida** que gera a despesa vinculada na data efetiva. |
| **Contas a receber** | Status `Previsto`/`Recebido`/`Em Atraso`/`Cancelado`, pagador, documento de referência, observações e **confirmação rápida** que gera a receita vinculada. |
| **Relatórios** | Filtros por período, tipo, categoria e status; orçamento planejado × realizado, fluxo de caixa mensal com saldo acumulado, balanço anual, contas do período e movimentações. Impressão A4, **CSV** (abre no Excel) e **PDF**. |
| **Configurações** | Preferências (moeda, dia de fechamento, antecedência dos alertas), categorias (criar, editar, cor, ícone, ativar/inativar), metas mensais por categoria, perfil, troca de senha e membros da família. |

> Excluir uma despesa/receita gerada por baixa **reabre** a conta correspondente — é assim que se "estorna" um pagamento.

## Estrutura

Segue a organização do projeto de referência `Python-FakePinterest-main`:

```
economia domestica inteligente/
├── main.py              # sobe o servidor
├── criarDB.py           # cria as tabelas / popula dados de demonstração
├── requirements.txt
├── docker-compose.yml   # PostgreSQL 16 para desenvolvimento
├── .env.example         # modelo das variáveis de ambiente
└── app/
    ├── __init__.py      # Flask, SQLAlchemy, Bcrypt, LoginManager, CSRFProtect, filtros Jinja
    ├── models.py        # Familia, Usuario, Categoria, Receita, Despesa, ContaPagar, ContaReceber
    ├── forms.py         # formulários Flask-WTF com validação
    ├── routes.py        # rotas dos 7 módulos + autenticação
    ├── utils.py         # períodos, somatórios, status automáticos, exportação CSV/PDF
    ├── static/css/estilo.css   # paleta e convenções do modelo pag_pokemon_modelo_css
    └── templates/
        ├── base.html             # template mestre (title, styles, content, scripts)
        ├── _macros.html  _filtro.html  formulario.html  baixa.html  erro.html
        ├── login.html  criarconta.html  dashboard.html  relatorios.html
        ├── receitas/  despesas/  contas_pagar/  contas_receber/
        └── configuracoes/
```

## Instalação passo a passo

### 1. Ambiente virtual e dependências

```bash
cd "economia domestica inteligente"
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Variáveis de ambiente

```bash
cp .env.example .env
python -c "import secrets; print(secrets.token_hex(32))"   # cole o resultado em SECRET_KEY
```

| Variável | Exemplo |
|---|---|
| `SECRET_KEY` | chave aleatória (sessões e CSRF) |
| `DATABASE_URL` | `postgresql+psycopg2://economia:economia@localhost:5432/economia_domestica` |
| `FLASK_DEBUG` | `1` em desenvolvimento, `0` em produção |

### 3. Banco PostgreSQL

**Opção A: Docker (mais simples)**
```bash
docker compose up -d
```

**Opção B: PostgreSQL instalado na máquina**
```bash
sudo -u postgres psql
CREATE USER economia WITH PASSWORD 'economia';
CREATE DATABASE economia_domestica OWNER economia;
\q
```

### 4. Criar as tabelas

```bash
python criarDB.py           # cria as tabelas
python criarDB.py --demo    # (opcional) APAGA tudo e cria uma família de demonstração
```

Logins da demonstração: `demo@familia.com` / `demo12345` (admin) e `bruno@familia.com` / `demo12345` (membro).

### 5. Executar

```bash
python main.py              # http://127.0.0.1:5000
```

Sem a demonstração, acesse **Criar conta**: você cadastra a família, vira o administrador e recebe as categorias padrão
(Salário, Bolsas, Pensão, Rendimentos extras… / Moradia, Alimentação, Transporte, Saúde, Educação, Lazer, Despesas fixas e variáveis…).

Em produção: `FLASK_DEBUG=0` e `gunicorn -w 3 -b 0.0.0.0:8000 main:app` (instale o `gunicorn`) atrás de um proxy HTTPS.

## Segurança

- **CSRF** em todos os formulários e em todas as ações POST (excluir, baixar, sair), via `CSRFProtect`.
- Senhas com hash **bcrypt**; login com mensagem genérica; membros inativados não conseguem entrar.
- Apenas **ORM (SQLAlchemy)**, com consultas parametrizadas.
- **Isolamento por família**: todo registro é buscado com `obter_da_familia()`, e um ID de outra família devolve 404.
- Validação no navegador (atributos HTML5 gerados pelos validadores) **e** no servidor (WTForms).
- Valores monetários em `NUMERIC(12,2)`, sem `float`.
- Redirecionamento `?next=` aceita apenas caminhos internos.

## Modelo de dados (resumo)

```
familia 1─N usuario
familia 1─N categoria (tipo receita|despesa, cor, ícone, ativa, meta_mensal)
familia 1─N receita ──N─1 categoria      receita.conta_receber_id ─1:1─ conta_receber
familia 1─N despesa ──N─1 categoria      despesa.conta_pagar_id   ─1:1─ conta_pagar
familia 1─N conta_pagar   (vencimento, competência, status, recorrente, parcela_atual/total_parcelas)
familia 1─N conta_receber (data_prevista, status, devedor, documento)
```

Índices: `(familia_id, data)` em receitas/despesas/contas e `(familia_id, status)` nas contas.

Para evoluir o esquema depois de colocar dados reais, recomenda-se adicionar o **Flask-Migrate** (Alembic):
`pip install Flask-Migrate`, `Migrate(app, database)` no `__init__.py`, depois `flask db init`, `flask db migrate` e `flask db upgrade`.

## Deploy na Vercel

O projeto já está preparado para rodar em ambiente serverless: quando a variável `VERCEL` existe, o app
usa conexões sem pool (`NullPool`), cookies apenas via HTTPS e exige `SECRET_KEY` definida.
Datas são calculadas no fuso de `TIMEZONE` (padrão `America/Sao_Paulo`), já que os servidores rodam em UTC.

1. Crie um banco PostgreSQL gerenciado (ex.: [Neon](https://neon.tech) ou [Supabase](https://supabase.com)).
   No Neon, prefira a URL *pooled* (host com `-pooler`). URLs `postgres://` e `postgresql://` são aceitas.
2. Crie as tabelas a partir da sua máquina, apontando para o banco remoto:
   ```bash
   DATABASE_URL="<url do banco>" python criarDB.py --demo   # ou sem --demo para começar vazio
   ```
3. Importe o repositório na Vercel e defina as variáveis de ambiente:
   `SECRET_KEY` (gere com `python -c "import secrets; print(secrets.token_hex(32))"`), `DATABASE_URL` e,
   opcionalmente, `TIMEZONE`.
4. Faça o deploy. A Vercel detecta o Flask pela variável `app` exposta em `main.py`.

## Licença

Distribuído sob a licença MIT. Veja [LICENSE](LICENSE) para mais detalhes.
