"""
Inicialização da aplicação Economia Doméstica Inteligente.

Segue a mesma organização do projeto de referência (FakePinterest):
    app/__init__.py  -> cria o Flask, as extensões e a configuração
    app/models.py    -> modelos do banco (SQLAlchemy)
    app/forms.py     -> formulários (Flask-WTF / WTForms)
    app/routes.py    -> rotas e regras de negócio
    app/utils.py     -> funções auxiliares (períodos, totais, exportação)

As configurações sensíveis vêm do arquivo .env (veja .env.example).
"""

import os
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from flask import Flask
from flask_bcrypt import Bcrypt
from flask_login import LoginManager
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect
from sqlalchemy.pool import NullPool

# Carrega as variáveis do arquivo .env que fica na raiz do projeto
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

EM_PRODUCAO = bool(os.environ.get("VERCEL"))      # a Vercel define VERCEL=1 automaticamente

# Fuso horário usado para "hoje" (servidores em nuvem rodam em UTC)
FUSO_HORARIO = ZoneInfo(os.environ.get("TIMEZONE", "America/Sao_Paulo"))


def data_hoje():
    """Data atual no fuso horário da aplicação (e não no do servidor)."""
    return datetime.now(FUSO_HORARIO).date()


def normalizar_url_banco(url):
    """Aceita URLs postgres:// e postgresql:// (Neon, Supabase, Heroku...) e força o driver psycopg2."""
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+psycopg2://", 1)
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg2://", 1)
    return url


app = Flask(__name__)

app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY")
if not app.config["SECRET_KEY"]:
    if EM_PRODUCAO:
        raise RuntimeError("Defina a variável de ambiente SECRET_KEY.")
    app.config["SECRET_KEY"] = "chave-apenas-para-desenvolvimento"

app.config["SQLALCHEMY_DATABASE_URI"] = normalizar_url_banco(os.environ.get(
    "DATABASE_URL",
    "postgresql+psycopg2://economia:economia@localhost:5432/economia_domestica",
))
# Em serverless cada instância é efêmera: sem pool, para não esgotar as conexões do banco
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {"poolclass": NullPool} if EM_PRODUCAO else {"pool_pre_ping": True}
app.config["WTF_I18N_ENABLED"] = False           # usa as traduções embutidas do WTForms (pt_BR)
app.config["WTF_CSRF_TIME_LIMIT"] = None          # token válido enquanto a sessão durar
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = EM_PRODUCAO  # cookies só via HTTPS em produção
app.config["REMEMBER_COOKIE_HTTPONLY"] = True
app.config["REMEMBER_COOKIE_SECURE"] = EM_PRODUCAO

database = SQLAlchemy(app)
bcrypt = Bcrypt(app)
csrf = CSRFProtect(app)                            # CSRF em TODOS os POSTs (inclusive botões de excluir)
login_manager = LoginManager(app)
login_manager.login_view = "login"
login_manager.login_message = "Faça login para acessar esta página."
login_manager.login_message_category = "alerta"


# ---------------------------------------------------------------- filtros Jinja
SIMBOLOS_MOEDA = {"BRL": "R$", "USD": "US$", "EUR": "€"}


@app.template_filter("moeda")
def formatar_moeda(valor, codigo="BRL"):
    """Formata um número no padrão brasileiro: R$ 1.234,56"""
    valor = Decimal(valor or 0)
    texto = f"{abs(valor):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    sinal = "-" if valor < 0 else ""
    return f"{sinal}{SIMBOLOS_MOEDA.get(codigo, codigo)} {texto}"


@app.template_filter("data")
def formatar_data(valor):
    return valor.strftime("%d/%m/%Y") if valor else "—"


@app.context_processor
def injetar_globais():
    return {"hoje": data_hoje()}


from app import routes  # noqa: E402,F401  (import no final evita import circular)
