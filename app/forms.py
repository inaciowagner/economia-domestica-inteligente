"""
Formulários e validações (Flask-WTF / WTForms).

- Todos herdam de FlaskForm -> token CSRF automático.
- Os validadores (DataRequired, NumberRange, Length...) também geram os
  atributos HTML5 (required, min, maxlength), validando no navegador e no servidor.
- As opções de categoria são carregadas na rota (dependem da família logada).
"""

from decimal import Decimal

from flask_wtf import FlaskForm
from wtforms import (BooleanField, DateField, DecimalField, EmailField, IntegerField, PasswordField,
                     SelectField, StringField, SubmitField, TextAreaField)
from wtforms.validators import (DataRequired, Email, EqualTo, Length, NumberRange, Optional, Regexp,
                                ValidationError)

from app import data_hoje
from app.models import Usuario


class FormBase(FlaskForm):
    """Base de todos os formulários: mensagens padrão do WTForms em português."""
    class Meta:
        locales = ["pt_BR", "pt"]

FORMAS_PAGAMENTO = ["Pix", "Dinheiro", "Cartão de débito", "Cartão de crédito", "Boleto",
                    "Transferência", "Débito automático"]
MOEDAS = [("BRL", "Real (R$)"), ("USD", "Dólar (US$)"), ("EUR", "Euro (€)")]
ICONES = ["💰", "💼", "🎓", "👪", "📈", "🧾", "🔁", "⚖️", "🏠", "🛒", "🚗", "🩺", "📚", "🎉", "📌",
          "🔀", "💡", "📱", "🐾", "👕", "✈️", "🎁", "🏛️", "💳"]

valor_validadores = [DataRequired("Informe o valor."),
                     NumberRange(min=Decimal("0.01"), max=Decimal("9999999999.99"), message="Valor inválido.")]


def campo_valor(rotulo="Valor"):
    return DecimalField(rotulo, places=2, validators=valor_validadores,
                        render_kw={"step": "0.01", "min": "0.01", "inputmode": "decimal"})


# ------------------------------------------------------------------ autenticação
class FormLogin(FormBase):
    email = EmailField("E-mail", validators=[DataRequired(), Email("E-mail inválido.")])
    senha = PasswordField("Senha", validators=[DataRequired()])
    lembrar = BooleanField("Manter conectado")
    botao = SubmitField("Entrar")


class FormCriarConta(FormBase):
    nome_familia = StringField("Nome da família", validators=[DataRequired(), Length(2, 120)],
                               render_kw={"placeholder": "Ex.: Família Silva"})
    nome = StringField("Seu nome", validators=[DataRequired(), Length(2, 120)])
    email = EmailField("E-mail", validators=[DataRequired(), Email("E-mail inválido."), Length(max=160)])
    senha = PasswordField("Senha", validators=[DataRequired(), Length(8, 72, "A senha deve ter de 8 a 72 caracteres.")])
    confirm_senha = PasswordField("Confirme a senha", validators=[DataRequired(), EqualTo("senha", "As senhas não conferem.")])
    botao = SubmitField("Criar conta")

    def validate_email(self, email):
        if Usuario.query.filter_by(email=email.data.lower().strip()).first():
            raise ValidationError("E-mail já cadastrado, faça login para continuar.")


# ------------------------------------------------------------------ lançamentos
class FormReceita(FormBase):
    descricao = StringField("Descrição", validators=[DataRequired(), Length(max=160)])
    valor = campo_valor()
    data_recebimento = DateField("Data de recebimento", default=data_hoje, validators=[DataRequired()])
    categoria_id = SelectField("Categoria", coerce=int, validators=[DataRequired("Escolha a categoria.")])
    conta_origem = StringField("Conta / origem", validators=[Optional(), Length(max=80)],
                               render_kw={"placeholder": "Ex.: Conta corrente Banco X"})
    observacoes = TextAreaField("Observações", validators=[Optional(), Length(max=2000)])
    botao = SubmitField("Salvar receita")


class FormDespesa(FormBase):
    descricao = StringField("Descrição", validators=[DataRequired(), Length(max=160)])
    valor = campo_valor()
    data_pagamento = DateField("Data do pagamento", default=data_hoje, validators=[DataRequired()])
    categoria_id = SelectField("Categoria", coerce=int, validators=[DataRequired("Escolha a categoria.")])
    forma_pagamento = SelectField("Forma de pagamento", choices=FORMAS_PAGAMENTO)
    observacoes = TextAreaField("Observações", validators=[Optional(), Length(max=2000)])
    botao = SubmitField("Salvar despesa")


class FormContaPagar(FormBase):
    descricao = StringField("Descrição", validators=[DataRequired(), Length(max=160)],
                            render_kw={"placeholder": "Ex.: Conta de luz, IPTU, parcela do carro"})
    valor = campo_valor()
    data_vencimento = DateField("Vencimento", validators=[DataRequired()])
    data_competencia = DateField("Competência (mês de referência)", validators=[Optional()])
    categoria_id = SelectField("Categoria", coerce=int, validators=[DataRequired("Escolha a categoria.")])
    recorrente = BooleanField("Conta mensal recorrente (gera a do mês seguinte ao pagar)")
    total_parcelas = IntegerField("Nº de parcelas (cria uma conta por parcela)", default=1,
                                  validators=[Optional(), NumberRange(1, 360)], render_kw={"min": 1, "max": 360})
    status = SelectField("Status", choices=["Pendente", "Cancelado"])
    observacoes = TextAreaField("Observações", validators=[Optional(), Length(max=2000)])
    botao = SubmitField("Salvar conta")


class FormContaReceber(FormBase):
    descricao = StringField("Descrição", validators=[DataRequired(), Length(max=160)],
                            render_kw={"placeholder": "Ex.: Serviço de consultoria, reembolso plano de saúde"})
    devedor = StringField("Pagador / devedor", validators=[Optional(), Length(max=120)])
    valor = campo_valor()
    data_prevista = DateField("Data esperada", validators=[DataRequired()])
    categoria_id = SelectField("Categoria", coerce=int, validators=[DataRequired("Escolha a categoria.")])
    documento = StringField("Documento de referência", validators=[Optional(), Length(max=120)],
                            render_kw={"placeholder": "Nº do processo, contrato, recibo..."})
    status = SelectField("Status", choices=["Previsto", "Cancelado"])
    observacoes = TextAreaField("Observações documentais", validators=[Optional(), Length(max=2000)])
    botao = SubmitField("Salvar")


class FormBaixaPagamento(FormBase):
    """Dar baixa numa conta a pagar -> gera a Despesa."""
    data_pagamento = DateField("Data efetiva do pagamento", default=data_hoje, validators=[DataRequired()])
    valor = campo_valor("Valor pago")
    forma_pagamento = SelectField("Forma de pagamento", choices=FORMAS_PAGAMENTO)
    botao = SubmitField("Confirmar pagamento")


class FormBaixaRecebimento(FormBase):
    """Confirmar recebimento de uma conta a receber -> gera a Receita."""
    data_recebimento = DateField("Data do recebimento", default=data_hoje, validators=[DataRequired()])
    valor = campo_valor("Valor recebido")
    conta_origem = StringField("Conta de destino", validators=[Optional(), Length(max=80)])
    botao = SubmitField("Confirmar recebimento")


# ------------------------------------------------------------------ filtros (GET, sem CSRF)
class FormFiltro(FormBase):
    class Meta:
        csrf = False

    data_inicio = DateField("De", validators=[Optional()])
    data_fim = DateField("Até", validators=[Optional()])
    categoria_id = SelectField("Categoria", coerce=int, validators=[Optional()])
    forma_pagamento = SelectField("Forma de pagamento", choices=[("", "Todas")] + [(f, f) for f in FORMAS_PAGAMENTO],
                                  validators=[Optional()])
    valor_min = DecimalField("Valor mínimo", validators=[Optional()], render_kw={"step": "0.01", "min": "0"})
    valor_max = DecimalField("Valor máximo", validators=[Optional()], render_kw={"step": "0.01", "min": "0"})
    status = SelectField("Status", choices=[("", "Todos")], validators=[Optional()])
    botao = SubmitField("Filtrar")


class FormRelatorio(FormBase):
    class Meta:
        csrf = False

    data_inicio = DateField("De", validators=[Optional()])
    data_fim = DateField("Até", validators=[Optional()])
    tipo = SelectField("Movimentação", choices=[("todos", "Receitas e despesas"), ("receita", "Somente receitas"),
                                                ("despesa", "Somente despesas")], default="todos")
    categoria_id = SelectField("Categoria", coerce=int, validators=[Optional()])
    status = SelectField("Status das contas", choices=[("", "Todos"), ("Pendente", "Pendente"), ("Pago", "Pago"),
                                                       ("Atrasado", "Atrasado"), ("Previsto", "Previsto"),
                                                       ("Recebido", "Recebido"), ("Em Atraso", "Em Atraso"),
                                                       ("Cancelado", "Cancelado")], validators=[Optional()])
    ano = IntegerField("Ano do balanço", validators=[Optional(), NumberRange(2000, 2100)])
    botao = SubmitField("Gerar relatório")


# ------------------------------------------------------------------ configurações
class FormPreferencias(FormBase):
    nome = StringField("Nome da família", validators=[DataRequired(), Length(2, 120)])
    moeda = SelectField("Moeda", choices=MOEDAS)
    dia_fechamento = IntegerField("Dia de fechamento do mês", validators=[DataRequired(), NumberRange(1, 28)],
                                  render_kw={"min": 1, "max": 28})
    dias_alerta = IntegerField("Alertar vencimentos com quantos dias de antecedência",
                               validators=[DataRequired(), NumberRange(0, 60)], render_kw={"min": 0, "max": 60})
    botao = SubmitField("Salvar preferências")


class FormCategoria(FormBase):
    nome = StringField("Nome", validators=[DataRequired(), Length(max=60)])
    tipo = SelectField("Tipo", choices=[("despesa", "Despesa"), ("receita", "Receita")])
    cor = StringField("Cor", default="#bf9b30",
                      validators=[DataRequired(), Regexp(r"^#[0-9a-fA-F]{6}$", message="Cor inválida.")],
                      render_kw={"type": "color"})
    icone = SelectField("Ícone", choices=[(i, i) for i in ICONES])
    ativa = BooleanField("Ativa", default=True)
    meta_mensal = DecimalField("Meta mensal de gasto (opcional, só despesas)", places=2,
                               validators=[Optional(), NumberRange(min=0)], render_kw={"step": "0.01", "min": "0"})
    botao = SubmitField("Salvar categoria")


class FormPerfil(FormBase):
    nome = StringField("Nome", validators=[DataRequired(), Length(2, 120)])
    email = EmailField("E-mail", validators=[DataRequired(), Email("E-mail inválido."), Length(max=160)])
    botao = SubmitField("Salvar perfil")


class FormSenha(FormBase):
    senha_atual = PasswordField("Senha atual", validators=[DataRequired()])
    nova_senha = PasswordField("Nova senha", validators=[DataRequired(), Length(8, 72, "A senha deve ter de 8 a 72 caracteres.")])
    confirm_senha = PasswordField("Confirme a nova senha",
                                  validators=[DataRequired(), EqualTo("nova_senha", "As senhas não conferem.")])
    botao_senha = SubmitField("Alterar senha")


class FormMembro(FormBase):
    nome = StringField("Nome", validators=[DataRequired(), Length(2, 120)])
    email = EmailField("E-mail", validators=[DataRequired(), Email("E-mail inválido."), Length(max=160)])
    senha = PasswordField("Senha inicial", validators=[DataRequired(), Length(8, 72, "A senha deve ter de 8 a 72 caracteres.")])
    papel = SelectField("Papel", choices=[("membro", "Membro"), ("admin", "Administrador")])
    botao = SubmitField("Adicionar membro")

    def validate_email(self, email):
        if Usuario.query.filter_by(email=email.data.lower().strip()).first():
            raise ValidationError("E-mail já cadastrado.")
