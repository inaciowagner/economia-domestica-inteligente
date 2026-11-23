"""
Modelos relacionais (PostgreSQL via SQLAlchemy).

Regras gerais:
- Todo dado financeiro pertence a uma Familia (multiusuário: vários membros
  enxergam os mesmos lançamentos, cada família só enxerga os seus).
- Valores monetários usam Numeric(12, 2) — nunca Float.
- Índices compostos (familia_id, data) aceleram as consultas por período.
"""

from datetime import datetime, timezone

from flask_login import UserMixin

from app import data_hoje, database, login_manager

DINHEIRO = database.Numeric(12, 2)


def agora():
    return datetime.now(timezone.utc)


@login_manager.user_loader
def load_usuario(id_usuario):
    """Retorna o usuário logado (usado pelo Flask-Login)."""
    return database.session.get(Usuario, int(id_usuario))


class Familia(database.Model):
    """Agrupa os membros e guarda as preferências gerais do sistema."""
    id = database.Column(database.Integer, primary_key=True)
    nome = database.Column(database.String(120), nullable=False)
    moeda = database.Column(database.String(3), nullable=False, default="BRL")
    dia_fechamento = database.Column(database.SmallInteger, nullable=False, default=1)  # 1..28
    dias_alerta = database.Column(database.SmallInteger, nullable=False, default=5)     # aviso de vencimento
    criado_em = database.Column(database.DateTime(timezone=True), nullable=False, default=agora)

    membros = database.relationship("Usuario", back_populates="familia", lazy=True)
    categorias = database.relationship("Categoria", back_populates="familia", lazy=True)


class Usuario(database.Model, UserMixin):
    id = database.Column(database.Integer, primary_key=True)
    familia_id = database.Column(database.Integer, database.ForeignKey("familia.id", ondelete="CASCADE"),
                                 nullable=False, index=True)
    nome = database.Column(database.String(120), nullable=False)
    email = database.Column(database.String(160), nullable=False, unique=True)
    senha = database.Column(database.String(128), nullable=False)          # hash bcrypt
    papel = database.Column(database.String(10), nullable=False, default="membro")  # admin | membro
    ativo = database.Column(database.Boolean, nullable=False, default=True)
    criado_em = database.Column(database.DateTime(timezone=True), nullable=False, default=agora)

    familia = database.relationship("Familia", back_populates="membros")

    @property
    def is_active(self):  # Flask-Login bloqueia o login de membros inativados
        return self.ativo

    @property
    def eh_admin(self):
        return self.papel == "admin"


class Categoria(database.Model):
    """Categoria personalizável de receita ou despesa."""
    __table_args__ = (
        database.UniqueConstraint("familia_id", "tipo", "nome", name="uq_categoria_familia_tipo_nome"),
    )
    id = database.Column(database.Integer, primary_key=True)
    familia_id = database.Column(database.Integer, database.ForeignKey("familia.id", ondelete="CASCADE"),
                                 nullable=False, index=True)
    nome = database.Column(database.String(60), nullable=False)
    tipo = database.Column(database.String(10), nullable=False)               # receita | despesa
    cor = database.Column(database.String(7), nullable=False, default="#bf9b30")
    icone = database.Column(database.String(8), nullable=False, default="💰")
    ativa = database.Column(database.Boolean, nullable=False, default=True)
    meta_mensal = database.Column(DINHEIRO, nullable=True)                    # limite de gasto (só despesas)

    familia = database.relationship("Familia", back_populates="categorias")


class Receita(database.Model):
    """Entrada financeira realizada."""
    __table_args__ = (database.Index("ix_receita_familia_data", "familia_id", "data_recebimento"),)
    id = database.Column(database.Integer, primary_key=True)
    familia_id = database.Column(database.Integer, database.ForeignKey("familia.id", ondelete="CASCADE"), nullable=False)
    categoria_id = database.Column(database.Integer, database.ForeignKey("categoria.id"), nullable=False, index=True)
    usuario_id = database.Column(database.Integer, database.ForeignKey("usuario.id", ondelete="SET NULL"))
    conta_receber_id = database.Column(database.Integer,
                                       database.ForeignKey("conta_receber.id", ondelete="SET NULL"), unique=True)
    descricao = database.Column(database.String(160), nullable=False)
    valor = database.Column(DINHEIRO, nullable=False)
    data_recebimento = database.Column(database.Date, nullable=False, default=data_hoje)
    conta_origem = database.Column(database.String(80))
    observacoes = database.Column(database.Text)
    criado_em = database.Column(database.DateTime(timezone=True), nullable=False, default=agora)

    categoria = database.relationship("Categoria")
    usuario = database.relationship("Usuario")
    conta_receber = database.relationship("ContaReceber", back_populates="receita")


class Despesa(database.Model):
    """Saída financeira realizada (paga)."""
    __table_args__ = (database.Index("ix_despesa_familia_data", "familia_id", "data_pagamento"),)
    id = database.Column(database.Integer, primary_key=True)
    familia_id = database.Column(database.Integer, database.ForeignKey("familia.id", ondelete="CASCADE"), nullable=False)
    categoria_id = database.Column(database.Integer, database.ForeignKey("categoria.id"), nullable=False, index=True)
    usuario_id = database.Column(database.Integer, database.ForeignKey("usuario.id", ondelete="SET NULL"))
    conta_pagar_id = database.Column(database.Integer,
                                     database.ForeignKey("conta_pagar.id", ondelete="SET NULL"), unique=True)
    descricao = database.Column(database.String(160), nullable=False)
    valor = database.Column(DINHEIRO, nullable=False)
    data_pagamento = database.Column(database.Date, nullable=False, default=data_hoje)
    forma_pagamento = database.Column(database.String(30), nullable=False, default="Pix")
    observacoes = database.Column(database.Text)
    criado_em = database.Column(database.DateTime(timezone=True), nullable=False, default=agora)

    categoria = database.relationship("Categoria")
    usuario = database.relationship("Usuario")
    conta_pagar = database.relationship("ContaPagar", back_populates="despesa")


class ContaPagar(database.Model):
    """Obrigação futura: boleto, fatura, parcela, tributo, conta mensal..."""
    STATUS = ("Pendente", "Pago", "Atrasado", "Cancelado")
    __table_args__ = (
        database.Index("ix_conta_pagar_familia_venc", "familia_id", "data_vencimento"),
        database.Index("ix_conta_pagar_familia_status", "familia_id", "status"),
    )
    id = database.Column(database.Integer, primary_key=True)
    familia_id = database.Column(database.Integer, database.ForeignKey("familia.id", ondelete="CASCADE"), nullable=False)
    categoria_id = database.Column(database.Integer, database.ForeignKey("categoria.id"), nullable=False)
    descricao = database.Column(database.String(160), nullable=False)
    valor = database.Column(DINHEIRO, nullable=False)
    data_competencia = database.Column(database.Date)            # mês de referência da obrigação
    data_vencimento = database.Column(database.Date, nullable=False)
    status = database.Column(database.String(10), nullable=False, default="Pendente")
    recorrente = database.Column(database.Boolean, nullable=False, default=False)  # gera a do mês seguinte ao pagar
    parcela_atual = database.Column(database.SmallInteger)
    total_parcelas = database.Column(database.SmallInteger)
    data_pagamento = database.Column(database.Date)
    observacoes = database.Column(database.Text)
    criado_em = database.Column(database.DateTime(timezone=True), nullable=False, default=agora)

    categoria = database.relationship("Categoria")
    despesa = database.relationship("Despesa", back_populates="conta_pagar", uselist=False)

    @property
    def dias_para_vencer(self):
        return (self.data_vencimento - data_hoje()).days

    @property
    def aberta(self):
        return self.status in ("Pendente", "Atrasado")


class ContaReceber(database.Model):
    """Valor futuro previsto: serviço autônomo, reembolso, acordo, causa judicial, herança..."""
    STATUS = ("Previsto", "Recebido", "Em Atraso", "Cancelado")
    __table_args__ = (
        database.Index("ix_conta_receber_familia_prev", "familia_id", "data_prevista"),
        database.Index("ix_conta_receber_familia_status", "familia_id", "status"),
    )
    id = database.Column(database.Integer, primary_key=True)
    familia_id = database.Column(database.Integer, database.ForeignKey("familia.id", ondelete="CASCADE"), nullable=False)
    categoria_id = database.Column(database.Integer, database.ForeignKey("categoria.id"), nullable=False)
    descricao = database.Column(database.String(160), nullable=False)
    devedor = database.Column(database.String(120))               # quem deve pagar
    valor = database.Column(DINHEIRO, nullable=False)
    data_prevista = database.Column(database.Date, nullable=False)
    status = database.Column(database.String(10), nullable=False, default="Previsto")
    data_recebimento = database.Column(database.Date)
    documento = database.Column(database.String(120))             # nº de processo, contrato, recibo...
    observacoes = database.Column(database.Text)
    criado_em = database.Column(database.DateTime(timezone=True), nullable=False, default=agora)

    categoria = database.relationship("Categoria")
    receita = database.relationship("Receita", back_populates="conta_receber", uselist=False)

    @property
    def dias_para_vencer(self):
        return (self.data_prevista - data_hoje()).days

    @property
    def aberta(self):
        return self.status in ("Previsto", "Em Atraso")
