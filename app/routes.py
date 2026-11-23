"""
Rotas e regras de negócio, organizadas por módulo:

    1. Autenticação          5. Contas a pagar
    2. Dashboard             6. Contas a receber
    3. Receitas              7. Relatórios
    4. Despesas              8. Configurações

Toda consulta é filtrada por current_user.familia_id: cada família só vê os
próprios dados. Exclusões e baixas são sempre POST com token CSRF.
"""

from datetime import date, timedelta
from urllib.parse import urlsplit

from flask import Response, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from app import app, bcrypt, database
from app.forms import (FormBaixaPagamento, FormBaixaRecebimento, FormCategoria, FormContaPagar,
                       FormContaReceber, FormCriarConta, FormDespesa, FormFiltro, FormLogin, FormMembro,
                       FormPerfil, FormPreferencias, FormReceita, FormRelatorio, FormSenha)
from app.models import Categoria, ContaPagar, ContaReceber, Despesa, Familia, Receita, Usuario
from app.utils import (atualizar_status_vencidos, despesas_por_categoria, gerar_csv, gerar_pdf,
                       obter_da_familia, opcoes_categorias, orcamento_vs_realizado, periodo_ciclo,
                       serie_mensal, somar_meses, total_despesas, total_receitas)

POR_PAGINA = 20

CATEGORIAS_PADRAO = [
    # (nome, tipo, cor, ícone)
    ("Salário", "receita", "#2e8b57", "💼"), ("Bolsas", "receita", "#3c9d9b", "🎓"),
    ("Pensão", "receita", "#6a8caf", "👪"), ("Rendimentos extras", "receita", "#8fbc5a", "📈"),
    ("Serviços autônomos", "receita", "#b8860b", "🧾"), ("Reembolsos", "receita", "#5f9ea0", "🔁"),
    ("Acordos e causas judiciais", "receita", "#7b68ee", "⚖️"), ("Outras receitas", "receita", "#a0a0a0", "💰"),
    ("Moradia", "despesa", "#a67c00", "🏠"), ("Alimentação", "despesa", "#e07a2f", "🛒"),
    ("Transporte", "despesa", "#4f7cac", "🚗"), ("Saúde", "despesa", "#c0392b", "🩺"),
    ("Educação", "despesa", "#6c5ce7", "📚"), ("Lazer", "despesa", "#e84393", "🎉"),
    ("Despesas fixas", "despesa", "#7f8c8d", "📌"), ("Despesas variáveis", "despesa", "#16a085", "🔀"),
    ("Impostos e tributos", "despesa", "#8e44ad", "🏛️"),
]


def criar_categorias_padrao(familia):
    for nome, tipo, cor, icone in CATEGORIAS_PADRAO:
        database.session.add(Categoria(familia=familia, nome=nome, tipo=tipo, cor=cor, icone=icone))


def url_segura(destino):
    """Só aceita redirecionar para caminhos internos (evita open redirect no ?next=)."""
    return destino if destino and not urlsplit(destino).netloc and destino.startswith("/") else None


def paginar(consulta):
    return consulta.paginate(page=request.args.get("pagina", 1, type=int), per_page=POR_PAGINA, error_out=False)


def somar_consulta(consulta, modelo):
    """Soma os valores de uma consulta já filtrada direto no banco (SUM), sem carregar as linhas."""
    return consulta.with_entities(func.coalesce(func.sum(modelo.valor), 0)).scalar()


def familia_atual():
    return current_user.familia


# =========================================================== 1. AUTENTICAÇÃO
@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))
    form = FormLogin()
    if form.validate_on_submit():
        usuario = Usuario.query.filter_by(email=form.email.data.lower().strip()).first()
        # Mensagem genérica: não revela se o e-mail existe
        if usuario and bcrypt.check_password_hash(usuario.senha, form.senha.data):
            if not usuario.ativo:
                flash("Seu acesso foi desativado pelo administrador da família.", "erro")
            else:
                login_user(usuario, remember=form.lembrar.data)
                return redirect(url_segura(request.args.get("next")) or url_for("dashboard"))
        else:
            flash("E-mail ou senha incorretos.", "erro")
    return render_template("login.html", form=form)


@app.route("/criarconta", methods=["GET", "POST"])
def criar_conta():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))
    form = FormCriarConta()
    if form.validate_on_submit():
        familia = Familia(nome=form.nome_familia.data.strip())
        usuario = Usuario(familia=familia, nome=form.nome.data.strip(), email=form.email.data.lower().strip(),
                          senha=bcrypt.generate_password_hash(form.senha.data).decode("utf-8"), papel="admin")
        database.session.add_all([familia, usuario])
        criar_categorias_padrao(familia)
        database.session.commit()
        login_user(usuario)
        flash("Conta criada! Categorias padrão já foram cadastradas — ajuste em Configurações.", "sucesso")
        return redirect(url_for("dashboard"))
    return render_template("criarconta.html", form=form)


@app.route("/logout", methods=["POST"])
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))


# =========================================================== 2. DASHBOARD
@app.route("/")
@login_required
def dashboard():
    familia = familia_atual()
    atualizar_status_vencidos(familia.id)
    hoje = date.today()

    # ----- período selecionado: atual, anterior ou personalizado
    periodo = request.args.get("periodo", "atual")
    inicio, fim = periodo_ciclo(hoje, familia.dia_fechamento, -1 if periodo == "anterior" else 0)
    if periodo == "personalizado":
        try:
            inicio = date.fromisoformat(request.args.get("inicio", ""))
            fim = date.fromisoformat(request.args.get("fim", ""))
            if fim < inicio:
                inicio, fim = fim, inicio
        except ValueError:
            flash("Informe datas válidas para o período personalizado.", "alerta")
            periodo = "atual"

    receitas = total_receitas(familia.id, inicio, fim)
    despesas = total_despesas(familia.id, inicio, fim)
    saldo_atual = total_receitas(familia.id, fim=hoje) - total_despesas(familia.id, fim=hoje)

    # Saldo previsto = saldo atual + a receber em aberto - a pagar em aberto (até o fim do período)
    limite = max(fim, hoje)
    a_pagar = ContaPagar.query.filter(ContaPagar.familia_id == familia.id,
                                      ContaPagar.status.in_(["Pendente", "Atrasado"]),
                                      ContaPagar.data_vencimento <= limite).all()
    a_receber = ContaReceber.query.filter(ContaReceber.familia_id == familia.id,
                                          ContaReceber.status.in_(["Previsto", "Em Atraso"]),
                                          ContaReceber.data_prevista <= limite).all()
    saldo_previsto = saldo_atual + sum((c.valor for c in a_receber), 0) - sum((c.valor for c in a_pagar), 0)

    # ----- alertas: vencidas + vencendo nos próximos N dias
    limite_alerta = hoje + timedelta(days=familia.dias_alerta)
    alertas_pagar = (ContaPagar.query.filter(ContaPagar.familia_id == familia.id,
                                             ContaPagar.status.in_(["Pendente", "Atrasado"]),
                                             ContaPagar.data_vencimento <= limite_alerta)
                     .order_by(ContaPagar.data_vencimento).all())
    alertas_receber = (ContaReceber.query.filter(ContaReceber.familia_id == familia.id,
                                                 ContaReceber.status.in_(["Previsto", "Em Atraso"]),
                                                 ContaReceber.data_prevista <= limite_alerta)
                       .order_by(ContaReceber.data_prevista).all())

    por_categoria = despesas_por_categoria(familia.id, inicio, fim)
    serie = serie_mensal(familia.id, meses=6)
    orcamento, _ = orcamento_vs_realizado(familia.id, inicio, fim)
    ultimas = (Despesa.query.filter_by(familia_id=familia.id)
               .order_by(Despesa.data_pagamento.desc(), Despesa.id.desc()).limit(5).all())

    graficos = {
        "categorias": {"rotulos": [c.nome for c, _ in por_categoria], "valores": [float(v) for _, v in por_categoria],
                       "cores": [c.cor for c, _ in por_categoria]},
        "mensal": {"rotulos": [s["rotulo"] for s in serie], "receitas": [float(s["receitas"]) for s in serie],
                   "despesas": [float(s["despesas"]) for s in serie]},
    }
    return render_template("dashboard.html", periodo=periodo, inicio=inicio, fim=fim, receitas=receitas,
                           despesas=despesas, saldo_atual=saldo_atual, saldo_previsto=saldo_previsto,
                           alertas_pagar=alertas_pagar, alertas_receber=alertas_receber,
                           orcamento=[o for o in orcamento if o["planejado"]], ultimas=ultimas,
                           graficos=graficos, moeda=familia.moeda)


# =========================================================== 3. RECEITAS
@app.route("/receitas")
@login_required
def receitas():
    filtro = FormFiltro(request.args)
    filtro.categoria_id.choices = opcoes_categorias("receita", incluir_todas=True)
    consulta = Receita.query.filter_by(familia_id=current_user.familia_id)
    filtro.validate()
    if filtro.data_inicio.data:
        consulta = consulta.filter(Receita.data_recebimento >= filtro.data_inicio.data)
    if filtro.data_fim.data:
        consulta = consulta.filter(Receita.data_recebimento <= filtro.data_fim.data)
    if filtro.categoria_id.data:
        consulta = consulta.filter(Receita.categoria_id == filtro.categoria_id.data)
    if filtro.valor_min.data is not None:
        consulta = consulta.filter(Receita.valor >= filtro.valor_min.data)
    if filtro.valor_max.data is not None:
        consulta = consulta.filter(Receita.valor <= filtro.valor_max.data)
    total = somar_consulta(consulta, Receita)
    pagina = paginar(consulta.order_by(Receita.data_recebimento.desc(), Receita.id.desc()))
    return render_template("receitas/lista.html", pagina=pagina, filtro=filtro, total=total,
                           moeda=familia_atual().moeda)


@app.route("/receitas/nova", methods=["GET", "POST"])
@app.route("/receitas/<int:id_receita>/editar", methods=["GET", "POST"])
@login_required
def receita_form(id_receita=None):
    receita = obter_da_familia(Receita, id_receita) if id_receita else None
    form = FormReceita(obj=receita)
    form.categoria_id.choices = opcoes_categorias("receita", incluir_id=receita.categoria_id if receita else None)
    if form.validate_on_submit():
        if receita is None:
            receita = Receita(familia_id=current_user.familia_id, usuario_id=current_user.id)
            database.session.add(receita)
        form.populate_obj(receita)
        obter_da_familia(Categoria, form.categoria_id.data)   # garante que a categoria é da família
        database.session.commit()
        flash("Receita salva com sucesso.", "sucesso")
        return redirect(url_for("receitas"))
    return render_template("formulario.html", form=form, titulo="Editar receita" if receita else "Nova receita",
                           voltar=url_for("receitas"))


@app.route("/receitas/<int:id_receita>/excluir", methods=["POST"])
@login_required
def receita_excluir(id_receita):
    receita = obter_da_familia(Receita, id_receita)
    if receita.conta_receber:   # desfaz a baixa da conta a receber vinculada
        receita.conta_receber.status = "Previsto"
        receita.conta_receber.data_recebimento = None
    database.session.delete(receita)
    database.session.commit()
    atualizar_status_vencidos(current_user.familia_id)
    flash("Receita excluída.", "sucesso")
    return redirect(request.referrer and url_segura(urlsplit(request.referrer).path) or url_for("receitas"))


# =========================================================== 4. DESPESAS
@app.route("/despesas")
@login_required
def despesas():
    filtro = FormFiltro(request.args)
    filtro.categoria_id.choices = opcoes_categorias("despesa", incluir_todas=True)
    consulta = Despesa.query.filter_by(familia_id=current_user.familia_id)
    filtro.validate()
    if filtro.data_inicio.data:
        consulta = consulta.filter(Despesa.data_pagamento >= filtro.data_inicio.data)
    if filtro.data_fim.data:
        consulta = consulta.filter(Despesa.data_pagamento <= filtro.data_fim.data)
    if filtro.categoria_id.data:
        consulta = consulta.filter(Despesa.categoria_id == filtro.categoria_id.data)
    if filtro.forma_pagamento.data:
        consulta = consulta.filter(Despesa.forma_pagamento == filtro.forma_pagamento.data)
    if filtro.valor_min.data is not None:
        consulta = consulta.filter(Despesa.valor >= filtro.valor_min.data)
    if filtro.valor_max.data is not None:
        consulta = consulta.filter(Despesa.valor <= filtro.valor_max.data)
    total = somar_consulta(consulta, Despesa)
    pagina = paginar(consulta.order_by(Despesa.data_pagamento.desc(), Despesa.id.desc()))
    return render_template("despesas/lista.html", pagina=pagina, filtro=filtro, total=total,
                           moeda=familia_atual().moeda)


@app.route("/despesas/nova", methods=["GET", "POST"])
@app.route("/despesas/<int:id_despesa>/editar", methods=["GET", "POST"])
@login_required
def despesa_form(id_despesa=None):
    despesa = obter_da_familia(Despesa, id_despesa) if id_despesa else None
    form = FormDespesa(obj=despesa)
    form.categoria_id.choices = opcoes_categorias("despesa", incluir_id=despesa.categoria_id if despesa else None)
    if form.validate_on_submit():
        if despesa is None:
            despesa = Despesa(familia_id=current_user.familia_id, usuario_id=current_user.id)
            database.session.add(despesa)
        form.populate_obj(despesa)
        obter_da_familia(Categoria, form.categoria_id.data)
        database.session.commit()
        flash("Despesa salva com sucesso.", "sucesso")
        return redirect(url_for("despesas"))
    return render_template("formulario.html", form=form, titulo="Editar despesa" if despesa else "Nova despesa",
                           voltar=url_for("despesas"))


@app.route("/despesas/<int:id_despesa>/excluir", methods=["POST"])
@login_required
def despesa_excluir(id_despesa):
    despesa = obter_da_familia(Despesa, id_despesa)
    if despesa.conta_pagar:     # desfaz a baixa da conta a pagar vinculada
        despesa.conta_pagar.status = "Pendente"
        despesa.conta_pagar.data_pagamento = None
    database.session.delete(despesa)
    database.session.commit()
    atualizar_status_vencidos(current_user.familia_id)
    flash("Despesa excluída.", "sucesso")
    return redirect(request.referrer and url_segura(urlsplit(request.referrer).path) or url_for("despesas"))


# =========================================================== 5. CONTAS A PAGAR
@app.route("/contas-pagar")
@login_required
def contas_pagar():
    atualizar_status_vencidos(current_user.familia_id)
    filtro = FormFiltro(request.args)
    filtro.categoria_id.choices = opcoes_categorias("despesa", incluir_todas=True)
    filtro.status.choices = [("abertas", "Em aberto"), ("", "Todos")] + [(s, s) for s in ContaPagar.STATUS]
    filtro.validate()
    status = request.args.get("status", "abertas")
    consulta = ContaPagar.query.filter_by(familia_id=current_user.familia_id)
    if status == "abertas":
        consulta = consulta.filter(ContaPagar.status.in_(["Pendente", "Atrasado"]))
    elif status:
        consulta = consulta.filter(ContaPagar.status == status)
    if filtro.data_inicio.data:
        consulta = consulta.filter(ContaPagar.data_vencimento >= filtro.data_inicio.data)
    if filtro.data_fim.data:
        consulta = consulta.filter(ContaPagar.data_vencimento <= filtro.data_fim.data)
    if filtro.categoria_id.data:
        consulta = consulta.filter(ContaPagar.categoria_id == filtro.categoria_id.data)
    total = somar_consulta(consulta, ContaPagar)
    pagina = paginar(consulta.order_by(ContaPagar.data_vencimento, ContaPagar.id))
    return render_template("contas_pagar/lista.html", pagina=pagina, filtro=filtro, status=status, total=total,
                           dias_alerta=familia_atual().dias_alerta, moeda=familia_atual().moeda)


@app.route("/contas-pagar/nova", methods=["GET", "POST"])
@app.route("/contas-pagar/<int:id_conta>/editar", methods=["GET", "POST"])
@login_required
def conta_pagar_form(id_conta=None):
    conta = obter_da_familia(ContaPagar, id_conta) if id_conta else None
    if conta and conta.status == "Pago":
        flash("Conta já paga: para alterar, exclua a despesa gerada (a conta volta a ficar pendente).", "alerta")
        return redirect(url_for("contas_pagar"))
    form = FormContaPagar(obj=conta)
    form.categoria_id.choices = opcoes_categorias("despesa", incluir_id=conta.categoria_id if conta else None)
    if conta:
        del form.total_parcelas   # parcelamento só na criação
    if form.validate_on_submit():
        obter_da_familia(Categoria, form.categoria_id.data)
        if conta:
            form.populate_obj(conta)
        else:
            # Cria uma conta por parcela, com vencimentos mensais consecutivos
            parcelas = form.total_parcelas.data or 1
            for n in range(parcelas):
                venc = somar_meses(form.data_vencimento.data, n)
                venc = venc.replace(day=min(form.data_vencimento.data.day, _ultimo_dia(venc)))
                nova = ContaPagar(familia_id=current_user.familia_id)
                form.populate_obj(nova)
                nova.data_vencimento = venc
                if parcelas > 1:
                    nova.parcela_atual, nova.total_parcelas = n + 1, parcelas
                    nova.recorrente = False
                    if form.data_competencia.data:
                        nova.data_competencia = somar_meses(form.data_competencia.data, n)
                else:
                    nova.parcela_atual = nova.total_parcelas = None
                database.session.add(nova)
        database.session.commit()
        atualizar_status_vencidos(current_user.familia_id)
        flash("Conta a pagar salva.", "sucesso")
        return redirect(url_for("contas_pagar"))
    return render_template("formulario.html", form=form,
                           titulo="Editar conta a pagar" if conta else "Nova conta a pagar",
                           voltar=url_for("contas_pagar"))


def _ultimo_dia(primeiro_do_mes):
    return (somar_meses(primeiro_do_mes, 1) - timedelta(days=1)).day


@app.route("/contas-pagar/<int:id_conta>/pagar", methods=["GET", "POST"])
@login_required
def conta_pagar_baixa(id_conta):
    """Baixa rápida: marca como Pago e gera a Despesa vinculada na data efetiva."""
    conta = obter_da_familia(ContaPagar, id_conta)
    if not conta.aberta:
        flash("Esta conta não está em aberto.", "alerta")
        return redirect(url_for("contas_pagar"))
    form = FormBaixaPagamento()
    if request.method == "GET":
        form.valor.data = conta.valor
    if form.validate_on_submit():
        descricao = conta.descricao
        if conta.total_parcelas:
            descricao += f" ({conta.parcela_atual}/{conta.total_parcelas})"
        database.session.add(Despesa(
            familia_id=conta.familia_id, categoria_id=conta.categoria_id, usuario_id=current_user.id,
            conta_pagar=conta, descricao=descricao, valor=form.valor.data,
            data_pagamento=form.data_pagamento.data, forma_pagamento=form.forma_pagamento.data,
            observacoes=f"Gerada pela baixa da conta a pagar #{conta.id}.",
        ))
        conta.status, conta.data_pagamento = "Pago", form.data_pagamento.data
        if conta.recorrente:   # agenda automaticamente a conta do próximo mês
            prox = somar_meses(conta.data_vencimento, 1)
            prox = prox.replace(day=min(conta.data_vencimento.day, _ultimo_dia(prox)))
            database.session.add(ContaPagar(
                familia_id=conta.familia_id, categoria_id=conta.categoria_id, descricao=conta.descricao,
                valor=conta.valor, data_vencimento=prox, recorrente=True, observacoes=conta.observacoes,
                data_competencia=somar_meses(conta.data_competencia, 1) if conta.data_competencia else None,
            ))
        database.session.commit()
        flash("Pagamento registrado e lançado em Despesas.", "sucesso")
        return redirect(url_for("contas_pagar"))
    return render_template("baixa.html", form=form, conta=conta, titulo="Dar baixa no pagamento",
                           voltar=url_for("contas_pagar"), moeda=familia_atual().moeda)


@app.route("/contas-pagar/<int:id_conta>/excluir", methods=["POST"])
@login_required
def conta_pagar_excluir(id_conta):
    conta = obter_da_familia(ContaPagar, id_conta)
    database.session.delete(conta)   # a despesa gerada (se houver) é mantida, apenas desvinculada
    database.session.commit()
    flash("Conta a pagar excluída.", "sucesso")
    return redirect(url_for("contas_pagar"))


# =========================================================== 6. CONTAS A RECEBER
@app.route("/contas-receber")
@login_required
def contas_receber():
    atualizar_status_vencidos(current_user.familia_id)
    filtro = FormFiltro(request.args)
    filtro.categoria_id.choices = opcoes_categorias("receita", incluir_todas=True)
    filtro.status.choices = [("abertas", "Em aberto"), ("", "Todos")] + [(s, s) for s in ContaReceber.STATUS]
    filtro.validate()
    status = request.args.get("status", "abertas")
    consulta = ContaReceber.query.filter_by(familia_id=current_user.familia_id)
    if status == "abertas":
        consulta = consulta.filter(ContaReceber.status.in_(["Previsto", "Em Atraso"]))
    elif status:
        consulta = consulta.filter(ContaReceber.status == status)
    if filtro.data_inicio.data:
        consulta = consulta.filter(ContaReceber.data_prevista >= filtro.data_inicio.data)
    if filtro.data_fim.data:
        consulta = consulta.filter(ContaReceber.data_prevista <= filtro.data_fim.data)
    if filtro.categoria_id.data:
        consulta = consulta.filter(ContaReceber.categoria_id == filtro.categoria_id.data)
    total = somar_consulta(consulta, ContaReceber)
    pagina = paginar(consulta.order_by(ContaReceber.data_prevista, ContaReceber.id))
    return render_template("contas_receber/lista.html", pagina=pagina, filtro=filtro, status=status, total=total,
                           dias_alerta=familia_atual().dias_alerta, moeda=familia_atual().moeda)


@app.route("/contas-receber/nova", methods=["GET", "POST"])
@app.route("/contas-receber/<int:id_conta>/editar", methods=["GET", "POST"])
@login_required
def conta_receber_form(id_conta=None):
    conta = obter_da_familia(ContaReceber, id_conta) if id_conta else None
    if conta and conta.status == "Recebido":
        flash("Valor já recebido: para alterar, exclua a receita gerada (a conta volta a ficar prevista).", "alerta")
        return redirect(url_for("contas_receber"))
    form = FormContaReceber(obj=conta)
    form.categoria_id.choices = opcoes_categorias("receita", incluir_id=conta.categoria_id if conta else None)
    if form.validate_on_submit():
        obter_da_familia(Categoria, form.categoria_id.data)
        if conta is None:
            conta = ContaReceber(familia_id=current_user.familia_id)
            database.session.add(conta)
        form.populate_obj(conta)
        database.session.commit()
        atualizar_status_vencidos(current_user.familia_id)
        flash("Conta a receber salva.", "sucesso")
        return redirect(url_for("contas_receber"))
    return render_template("formulario.html", form=form,
                           titulo="Editar conta a receber" if conta else "Nova conta a receber",
                           voltar=url_for("contas_receber"))


@app.route("/contas-receber/<int:id_conta>/receber", methods=["GET", "POST"])
@login_required
def conta_receber_baixa(id_conta):
    """Confirma o recebimento e registra automaticamente a Receita correspondente."""
    conta = obter_da_familia(ContaReceber, id_conta)
    if not conta.aberta:
        flash("Esta conta não está em aberto.", "alerta")
        return redirect(url_for("contas_receber"))
    form = FormBaixaRecebimento()
    if request.method == "GET":
        form.valor.data = conta.valor
    if form.validate_on_submit():
        obs = f"Gerada pela confirmação da conta a receber #{conta.id}."
        if conta.documento:
            obs += f" Documento: {conta.documento}."
        database.session.add(Receita(
            familia_id=conta.familia_id, categoria_id=conta.categoria_id, usuario_id=current_user.id,
            conta_receber=conta, descricao=conta.descricao + (f" — {conta.devedor}" if conta.devedor else ""),
            valor=form.valor.data, data_recebimento=form.data_recebimento.data,
            conta_origem=form.conta_origem.data, observacoes=obs,
        ))
        conta.status, conta.data_recebimento = "Recebido", form.data_recebimento.data
        database.session.commit()
        flash("Recebimento confirmado e lançado em Receitas.", "sucesso")
        return redirect(url_for("contas_receber"))
    return render_template("baixa.html", form=form, conta=conta, titulo="Confirmar recebimento",
                           voltar=url_for("contas_receber"), moeda=familia_atual().moeda)


@app.route("/contas-receber/<int:id_conta>/excluir", methods=["POST"])
@login_required
def conta_receber_excluir(id_conta):
    conta = obter_da_familia(ContaReceber, id_conta)
    database.session.delete(conta)
    database.session.commit()
    flash("Conta a receber excluída.", "sucesso")
    return redirect(url_for("contas_receber"))


# =========================================================== 7. RELATÓRIOS
def _dados_relatorio(form, familia):
    hoje = date.today()
    inicio = form.data_inicio.data or date(hoje.year, 1, 1)
    fim = form.data_fim.data or hoje
    if fim < inicio:
        inicio, fim = fim, inicio
    cat = form.categoria_id.data or None
    movimentos = []
    if form.tipo.data in ("todos", "receita"):
        q = Receita.query.filter(Receita.familia_id == familia.id, Receita.data_recebimento.between(inicio, fim))
        if cat:
            q = q.filter(Receita.categoria_id == cat)
        movimentos += [{"data": r.data_recebimento, "tipo": "Receita", "categoria": r.categoria.nome,
                        "cor": r.categoria.cor, "descricao": r.descricao, "detalhe": r.conta_origem,
                        "valor": r.valor} for r in q]
    if form.tipo.data in ("todos", "despesa"):
        q = Despesa.query.filter(Despesa.familia_id == familia.id, Despesa.data_pagamento.between(inicio, fim))
        if cat:
            q = q.filter(Despesa.categoria_id == cat)
        movimentos += [{"data": d.data_pagamento, "tipo": "Despesa", "categoria": d.categoria.nome,
                        "cor": d.categoria.cor, "descricao": d.descricao, "detalhe": d.forma_pagamento,
                        "valor": d.valor} for d in q]
    movimentos.sort(key=lambda m: m["data"])
    rec = sum((m["valor"] for m in movimentos if m["tipo"] == "Receita"), 0)
    desp = sum((m["valor"] for m in movimentos if m["tipo"] == "Despesa"), 0)

    # Contas (a pagar e a receber) do período, filtráveis por status
    status = form.status.data
    cp = ContaPagar.query.filter(ContaPagar.familia_id == familia.id, ContaPagar.data_vencimento.between(inicio, fim))
    cr = ContaReceber.query.filter(ContaReceber.familia_id == familia.id, ContaReceber.data_prevista.between(inicio, fim))
    if status:
        cp, cr = cp.filter(ContaPagar.status == status), cr.filter(ContaReceber.status == status)
    if cat:
        cp, cr = cp.filter(ContaPagar.categoria_id == cat), cr.filter(ContaReceber.categoria_id == cat)
    contas = ([("A pagar", c, c.data_vencimento) for c in cp] if form.tipo.data != "receita" else []) + \
             ([("A receber", c, c.data_prevista) for c in cr] if form.tipo.data != "despesa" else [])
    contas.sort(key=lambda x: x[2])

    ano = form.ano.data or fim.year
    orcamento, n_meses = orcamento_vs_realizado(familia.id, inicio, fim)
    # fluxo mensal do período (no máximo 24 meses para manter o relatório legível)
    meses = min(24, (fim.year - inicio.year) * 12 + fim.month - inicio.month + 1)
    fluxo = [s for s in _fluxo_periodo(familia.id, inicio, meses)]
    return {"inicio": inicio, "fim": fim, "movimentos": movimentos, "contas": contas,
            "resumo": {"receitas": rec, "despesas": desp, "saldo": rec - desp},
            "orcamento": orcamento, "n_meses": n_meses, "fluxo": fluxo,
            "anual": serie_mensal(familia.id, ano=ano), "ano": ano}


def _fluxo_periodo(familia_id, inicio, meses):
    """Fluxo de caixa mês a mês a partir do mês de `inicio`, com saldo acumulado."""
    base = inicio.replace(day=1)
    acumulado = total_receitas(familia_id, fim=base - timedelta(days=1)) - \
        total_despesas(familia_id, fim=base - timedelta(days=1))
    for i in range(meses):
        m_ini = somar_meses(base, i)
        m_fim = somar_meses(base, i + 1) - timedelta(days=1)
        r, d = total_receitas(familia_id, m_ini, m_fim), total_despesas(familia_id, m_ini, m_fim)
        acumulado += r - d
        yield {"rotulo": m_ini.strftime("%m/%Y"), "receitas": r, "despesas": d, "saldo": r - d,
               "acumulado": acumulado}


def _form_relatorio():
    form = FormRelatorio(request.args)
    form.categoria_id.choices = [(0, "Todas")] + \
        [(c.id, f"{'↑' if c.tipo == 'receita' else '↓'} {c.nome}") for c in
         Categoria.query.filter_by(familia_id=current_user.familia_id).order_by(Categoria.tipo, Categoria.nome)]
    form.validate()
    return form


@app.route("/relatorios")
@login_required
def relatorios():
    familia = familia_atual()
    atualizar_status_vencidos(familia.id)
    form = _form_relatorio()
    dados = _dados_relatorio(form, familia)
    if not form.data_inicio.data:
        form.data_inicio.data = dados["inicio"]
    if not form.data_fim.data:
        form.data_fim.data = dados["fim"]
    total_anual = {k: sum((s[k] for s in dados["anual"]), 0) for k in ("receitas", "despesas", "saldo")}
    return render_template("relatorios.html", form=form, d=dados, total_anual=total_anual, moeda=familia.moeda,
                           query=request.query_string.decode())


@app.route("/relatorios/csv")
@login_required
def relatorio_csv():
    dados = _dados_relatorio(_form_relatorio(), familia_atual())
    nome = f"movimentacoes_{dados['inicio']:%Y%m%d}_{dados['fim']:%Y%m%d}.csv"
    return Response(gerar_csv(dados["movimentos"]), mimetype="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f"attachment; filename={nome}"})


@app.route("/relatorios/pdf")
@login_required
def relatorio_pdf():
    familia = familia_atual()
    dados = _dados_relatorio(_form_relatorio(), familia)
    periodo = f"Período: {dados['inicio']:%d/%m/%Y} a {dados['fim']:%d/%m/%Y} — {familia.nome}"
    pdf = gerar_pdf("Relatório financeiro — Economia Doméstica Inteligente", periodo, dados["resumo"],
                    dados["movimentos"], dados["orcamento"], dados["fluxo"], familia.moeda)
    nome = f"relatorio_{dados['inicio']:%Y%m%d}_{dados['fim']:%Y%m%d}.pdf"
    return Response(pdf, mimetype="application/pdf", headers={"Content-Disposition": f"attachment; filename={nome}"})


# =========================================================== 8. CONFIGURAÇÕES
def exigir_admin():
    if not current_user.eh_admin:
        flash("Apenas o administrador da família pode alterar esta configuração.", "erro")
        return redirect(url_for("configuracoes"))
    return None


@app.route("/configuracoes", methods=["GET", "POST"])
@login_required
def configuracoes():
    familia = familia_atual()
    form = FormPreferencias(obj=familia)
    if form.validate_on_submit():
        if (resp := exigir_admin()):
            return resp
        form.populate_obj(familia)
        database.session.commit()
        flash("Preferências salvas.", "sucesso")
        return redirect(url_for("configuracoes"))
    return render_template("configuracoes/geral.html", form=form, aba="geral")


@app.route("/configuracoes/categorias")
@login_required
def categorias():
    lista = (Categoria.query.filter_by(familia_id=current_user.familia_id)
             .order_by(Categoria.tipo.desc(), Categoria.ativa.desc(), Categoria.nome).all())
    return render_template("configuracoes/categorias.html", categorias=lista, aba="categorias",
                           moeda=familia_atual().moeda)


@app.route("/configuracoes/categorias/nova", methods=["GET", "POST"])
@app.route("/configuracoes/categorias/<int:id_categoria>/editar", methods=["GET", "POST"])
@login_required
def categoria_form(id_categoria=None):
    categoria = obter_da_familia(Categoria, id_categoria) if id_categoria else None
    form = FormCategoria(obj=categoria)
    if request.method == "GET" and not categoria and request.args.get("tipo") in ("receita", "despesa"):
        form.tipo.data = request.args["tipo"]
    if form.validate_on_submit():
        if categoria is None:
            categoria = Categoria(familia_id=current_user.familia_id)
            database.session.add(categoria)
        form.populate_obj(categoria)
        if categoria.tipo == "receita":
            categoria.meta_mensal = None
        try:
            database.session.commit()
            flash("Categoria salva.", "sucesso")
            return redirect(url_for("categorias"))
        except IntegrityError:
            database.session.rollback()
            form.nome.errors.append("Já existe uma categoria com este nome e tipo.")
    return render_template("formulario.html", form=form, titulo="Editar categoria" if categoria else "Nova categoria",
                           voltar=url_for("categorias"), aba="categorias", config=True)


@app.route("/configuracoes/categorias/<int:id_categoria>/alternar", methods=["POST"])
@login_required
def categoria_alternar(id_categoria):
    categoria = obter_da_familia(Categoria, id_categoria)
    categoria.ativa = not categoria.ativa
    database.session.commit()
    flash(f"Categoria {'ativada' if categoria.ativa else 'inativada'}.", "sucesso")
    return redirect(url_for("categorias"))


@app.route("/configuracoes/metas", methods=["GET", "POST"])
@login_required
def metas():
    """Edição em lote das metas mensais de gasto por categoria de despesa."""
    familia = familia_atual()
    cats = Categoria.query.filter_by(familia_id=familia.id, tipo="despesa", ativa=True).order_by(Categoria.nome).all()
    if request.method == "POST":   # CSRF validado globalmente pelo CSRFProtect
        from decimal import Decimal, InvalidOperation
        erros = 0
        for cat in cats:
            bruto = request.form.get(f"meta_{cat.id}", "").strip().replace(",", ".")
            try:
                valor = Decimal(bruto).quantize(Decimal("0.01")) if bruto else None
                if valor is not None and valor < 0:
                    raise InvalidOperation
                cat.meta_mensal = valor or None
            except InvalidOperation:
                erros += 1
        database.session.commit()
        flash("Metas salvas." if not erros else f"Metas salvas; {erros} valor(es) inválido(s) ignorado(s).",
              "sucesso" if not erros else "alerta")
        return redirect(url_for("metas"))
    inicio, fim = periodo_ciclo(date.today(), familia.dia_fechamento)
    gastos = {c.id: v for c, v in despesas_por_categoria(familia.id, inicio, fim)}
    return render_template("configuracoes/metas.html", categorias=cats, gastos=gastos, aba="metas",
                           moeda=familia.moeda, inicio=inicio, fim=fim)


@app.route("/configuracoes/perfil", methods=["GET", "POST"])
@login_required
def perfil():
    form = FormPerfil(obj=current_user, prefix="perfil")
    form_senha = FormSenha(prefix="senha")
    if form.botao.data and form.validate_on_submit():
        email = form.email.data.lower().strip()
        outro = Usuario.query.filter(Usuario.email == email, Usuario.id != current_user.id).first()
        if outro:
            form.email.errors.append("E-mail já usado por outro usuário.")
        else:
            current_user.nome, current_user.email = form.nome.data.strip(), email
            database.session.commit()
            flash("Perfil atualizado.", "sucesso")
            return redirect(url_for("perfil"))
    if form_senha.botao_senha.data and form_senha.validate_on_submit():
        if not bcrypt.check_password_hash(current_user.senha, form_senha.senha_atual.data):
            form_senha.senha_atual.errors.append("Senha atual incorreta.")
        else:
            current_user.senha = bcrypt.generate_password_hash(form_senha.nova_senha.data).decode("utf-8")
            database.session.commit()
            flash("Senha alterada com sucesso.", "sucesso")
            return redirect(url_for("perfil"))
    return render_template("configuracoes/perfil.html", form=form, form_senha=form_senha, aba="perfil")


@app.route("/configuracoes/membros", methods=["GET", "POST"])
@login_required
def membros():
    familia = familia_atual()
    form = FormMembro()
    if form.validate_on_submit():
        if (resp := exigir_admin()):
            return resp
        database.session.add(Usuario(
            familia_id=familia.id, nome=form.nome.data.strip(), email=form.email.data.lower().strip(),
            senha=bcrypt.generate_password_hash(form.senha.data).decode("utf-8"), papel=form.papel.data))
        database.session.commit()
        flash("Membro adicionado. Compartilhe o e-mail e a senha inicial com ele.", "sucesso")
        return redirect(url_for("membros"))
    lista = Usuario.query.filter_by(familia_id=familia.id).order_by(Usuario.nome).all()
    return render_template("configuracoes/membros.html", form=form, membros=lista, aba="membros")


@app.route("/configuracoes/membros/<int:id_usuario>/alternar", methods=["POST"])
@login_required
def membro_alternar(id_usuario):
    if (resp := exigir_admin()):
        return resp
    membro = obter_da_familia(Usuario, id_usuario)
    if membro.id == current_user.id:
        flash("Você não pode desativar o próprio acesso.", "erro")
    else:
        membro.ativo = not membro.ativo
        database.session.commit()
        flash(f"Acesso de {membro.nome} {'reativado' if membro.ativo else 'desativado'}.", "sucesso")
    return redirect(url_for("membros"))


# =========================================================== erros
@app.errorhandler(404)
def nao_encontrado(_erro):
    return render_template("erro.html", codigo=404, mensagem="Página ou registro não encontrado."), 404


@app.errorhandler(400)
def requisicao_invalida(erro):
    return render_template("erro.html", codigo=400,
                           mensagem=getattr(erro, "description", "Requisição inválida (o formulário pode ter expirado).")), 400
