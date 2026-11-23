"""
Funções auxiliares de regra de negócio: períodos, somatórios, atualização de
status de contas vencidas, séries para gráficos e exportação (CSV / PDF).
"""

import csv
import io
from datetime import date, timedelta
from decimal import Decimal

from flask import abort
from flask_login import current_user
from sqlalchemy import extract, func

from app import data_hoje, database, formatar_moeda
from app.models import Categoria, ContaPagar, ContaReceber, Despesa, Receita

MESES = ["Jan", "Fev", "Mar", "Abr", "Mai", "Jun", "Jul", "Ago", "Set", "Out", "Nov", "Dez"]
ZERO = Decimal("0.00")


# ------------------------------------------------------------------ segurança multi-família
def obter_da_familia(modelo, id_registro):
    """Busca um registro garantindo que ele pertence à família do usuário logado (evita IDOR)."""
    registro = database.session.get(modelo, id_registro)
    if registro is None or registro.familia_id != current_user.familia_id:
        abort(404)
    return registro


def opcoes_categorias(tipo, incluir_todas=False, incluir_id=None):
    """Lista (id, nome) das categorias ativas de um tipo; mantém a categoria atual mesmo se inativa."""
    consulta = Categoria.query.filter_by(familia_id=current_user.familia_id, tipo=tipo)
    opcoes = [(c.id, f"{c.icone} {c.nome}") for c in consulta.order_by(Categoria.nome)
              if c.ativa or c.id == incluir_id]
    return ([(0, "Todas")] if incluir_todas else []) + opcoes


# ------------------------------------------------------------------ períodos
def somar_meses(dia, meses):
    mes = dia.month - 1 + meses
    ano = dia.year + mes // 12
    return date(ano, mes % 12 + 1, 1)


def periodo_ciclo(referencia, dia_fechamento, deslocamento=0):
    """
    Período "mensal" da família respeitando o dia de fechamento.
    Ex.: fechamento dia 10 e hoje 23/11 -> 10/11 a 09/12.
    deslocamento=-1 devolve o ciclo anterior.
    """
    dia_fechamento = max(1, min(dia_fechamento or 1, 28))
    base = date(referencia.year, referencia.month, 1)
    if referencia.day < dia_fechamento:
        base = somar_meses(base, -1)
    base = somar_meses(base, deslocamento)
    inicio = base.replace(day=dia_fechamento)
    fim = somar_meses(base, 1).replace(day=dia_fechamento) - timedelta(days=1)
    return inicio, fim


# ------------------------------------------------------------------ status automáticos
def atualizar_status_vencidos(familia_id):
    """Marca como Atrasado / Em Atraso o que passou da data e continua aberto."""
    hoje = data_hoje()
    ContaPagar.query.filter(ContaPagar.familia_id == familia_id, ContaPagar.status == "Pendente",
                            ContaPagar.data_vencimento < hoje).update({"status": "Atrasado"})
    ContaReceber.query.filter(ContaReceber.familia_id == familia_id, ContaReceber.status == "Previsto",
                              ContaReceber.data_prevista < hoje).update({"status": "Em Atraso"})
    # Se o usuário corrigir o vencimento para o futuro, a conta volta a ficar em dia
    ContaPagar.query.filter(ContaPagar.familia_id == familia_id, ContaPagar.status == "Atrasado",
                            ContaPagar.data_vencimento >= hoje).update({"status": "Pendente"})
    ContaReceber.query.filter(ContaReceber.familia_id == familia_id, ContaReceber.status == "Em Atraso",
                              ContaReceber.data_prevista >= hoje).update({"status": "Previsto"})
    database.session.commit()


# ------------------------------------------------------------------ somatórios
def soma(modelo, campo_data, familia_id, inicio=None, fim=None):
    consulta = database.session.query(func.coalesce(func.sum(modelo.valor), 0)).filter(modelo.familia_id == familia_id)
    if inicio:
        consulta = consulta.filter(campo_data >= inicio)
    if fim:
        consulta = consulta.filter(campo_data <= fim)
    return Decimal(consulta.scalar() or 0)


def total_receitas(familia_id, inicio=None, fim=None):
    return soma(Receita, Receita.data_recebimento, familia_id, inicio, fim)


def total_despesas(familia_id, inicio=None, fim=None):
    return soma(Despesa, Despesa.data_pagamento, familia_id, inicio, fim)


def despesas_por_categoria(familia_id, inicio, fim):
    """[(Categoria, total)] ordenado do maior para o menor gasto."""
    linhas = (database.session.query(Categoria, func.sum(Despesa.valor))
              .join(Despesa, Despesa.categoria_id == Categoria.id)
              .filter(Despesa.familia_id == familia_id, Despesa.data_pagamento.between(inicio, fim))
              .group_by(Categoria.id).order_by(func.sum(Despesa.valor).desc()).all())
    return [(cat, Decimal(total)) for cat, total in linhas]


def serie_mensal(familia_id, ano=None, meses=6):
    """
    Entradas x saídas por mês (calendário). Com `ano`, devolve os 12 meses do ano;
    sem `ano`, devolve os últimos `meses` meses até o mês atual.
    """
    if ano:
        lista = [date(ano, m, 1) for m in range(1, 13)]
    else:
        atual = data_hoje().replace(day=1)
        lista = [somar_meses(atual, -i) for i in range(meses - 1, -1, -1)]
    inicio, fim = lista[0], somar_meses(lista[-1], 1) - timedelta(days=1)

    def agrupar(modelo, campo):
        linhas = (database.session.query(extract("year", campo), extract("month", campo), func.sum(modelo.valor))
                  .filter(modelo.familia_id == familia_id, campo.between(inicio, fim))
                  .group_by(extract("year", campo), extract("month", campo)).all())
        return {(int(a), int(m)): Decimal(t) for a, m, t in linhas}

    rec = agrupar(Receita, Receita.data_recebimento)
    desp = agrupar(Despesa, Despesa.data_pagamento)
    serie = []
    for d in lista:
        r, s = rec.get((d.year, d.month), ZERO), desp.get((d.year, d.month), ZERO)
        serie.append({"rotulo": f"{MESES[d.month - 1]}/{str(d.year)[2:]}", "mes": d,
                      "receitas": r, "despesas": s, "saldo": r - s})
    return serie


def orcamento_vs_realizado(familia_id, inicio, fim):
    """Compara a meta mensal de cada categoria de despesa com o gasto real no período."""
    # nº de meses do período (proporcional às metas mensais)
    n_meses = max(1, (fim.year - inicio.year) * 12 + fim.month - inicio.month + (1 if fim.day >= inicio.day else 0))
    gastos = {cat.id: total for cat, total in despesas_por_categoria(familia_id, inicio, fim)}
    linhas = []
    for cat in Categoria.query.filter_by(familia_id=familia_id, tipo="despesa").order_by(Categoria.nome):
        realizado = gastos.get(cat.id, ZERO)
        if not cat.meta_mensal and not realizado:
            continue
        planejado = (cat.meta_mensal or ZERO) * n_meses
        percentual = float(realizado / planejado * 100) if planejado else None
        linhas.append({"categoria": cat, "planejado": planejado, "realizado": realizado,
                       "diferenca": planejado - realizado, "percentual": percentual})
    return linhas, n_meses


# ------------------------------------------------------------------ exportação
def gerar_csv(movimentos):
    """CSV com separador ';' e BOM — abre direto no Excel em português."""
    saida = io.StringIO()
    escritor = csv.writer(saida, delimiter=";")
    escritor.writerow(["Data", "Tipo", "Categoria", "Descrição", "Forma/Origem", "Valor"])
    for m in movimentos:
        escritor.writerow([m["data"].strftime("%d/%m/%Y"), m["tipo"], m["categoria"], m["descricao"],
                           m["detalhe"] or "", f"{m['valor']:.2f}".replace(".", ",")])
    return "﻿" + saida.getvalue()


def gerar_pdf(titulo, periodo, resumo, movimentos, orcamento, serie, moeda):
    """PDF A4 com resumo, orçamento x realizado, fluxo mensal e movimentações (ReportLab)."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=1.5 * cm, rightMargin=1.5 * cm,
                            topMargin=1.5 * cm, bottomMargin=1.5 * cm, title=titulo)
    estilos = getSampleStyleSheet()
    dourado = colors.HexColor("#a67c00")
    m = lambda v: formatar_moeda(v, moeda)  # noqa: E731

    def tabela(dados, larguras):
        t = Table(dados, colWidths=larguras, repeatRows=1)
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), dourado), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTSIZE", (0, 0), (-1, -1), 8), ("ALIGN", (-1, 1), (-1, -1), "RIGHT"),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#fff6dc")]),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#d9c48a")),
        ]))
        return t

    h2 = estilos["Heading2"]
    h2.textColor = dourado
    conteudo = [Paragraph(titulo, estilos["Title"]), Paragraph(periodo, estilos["Normal"]), Spacer(1, 10),
                tabela([["Receitas", "Despesas", "Saldo do período"],
                        [m(resumo["receitas"]), m(resumo["despesas"]), m(resumo["saldo"])]], [6 * cm] * 3)]
    if orcamento:
        conteudo += [Paragraph("Orçamento planejado x realizado", h2),
                     tabela([["Categoria", "Planejado", "Realizado", "Diferença"]] +
                            [[o["categoria"].nome, m(o["planejado"]), m(o["realizado"]), m(o["diferenca"])]
                             for o in orcamento], [7 * cm, 3.6 * cm, 3.6 * cm, 3.6 * cm])]
    conteudo += [Paragraph("Fluxo de caixa mensal", h2),
                 tabela([["Mês", "Entradas", "Saídas", "Saldo"]] +
                        [[s["rotulo"], m(s["receitas"]), m(s["despesas"]), m(s["saldo"])] for s in serie],
                        [4.5 * cm, 4.4 * cm, 4.4 * cm, 4.5 * cm])]
    conteudo += [Paragraph(f"Movimentações ({len(movimentos)})", h2),
                 tabela([["Data", "Tipo", "Categoria", "Descrição", "Valor"]] +
                        [[x["data"].strftime("%d/%m/%Y"), x["tipo"], x["categoria"][:22], x["descricao"][:40],
                          m(x["valor"])] for x in movimentos],
                        [2.2 * cm, 1.8 * cm, 3.6 * cm, 7 * cm, 3.2 * cm])]
    doc.build(conteudo)
    return buffer.getvalue()
