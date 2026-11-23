"""
criarDB.py — cria as tabelas no PostgreSQL e (opcionalmente) popula dados de demonstração.

Uso (com o ambiente virtual ativo e o .env configurado):
    python criarDB.py           -> cria as tabelas que ainda não existem
    python criarDB.py --demo    -> APAGA TUDO e recria com uma família de demonstração
                                   login: demo@familia.com   senha: demo12345
"""

import random
import sys
from datetime import timedelta
from decimal import Decimal

from app import app, bcrypt, data_hoje, database
from app.models import Categoria, ContaPagar, ContaReceber, Despesa, Familia, Receita, Usuario
from app.routes import criar_categorias_padrao
from app.utils import atualizar_status_vencidos, somar_meses


def valor(minimo, maximo):
    return Decimal(str(round(random.uniform(minimo, maximo), 2)))


def popular_demo():
    database.drop_all()
    database.create_all()
    random.seed(7)

    familia = Familia(nome="Família Demonstração", dia_fechamento=1, dias_alerta=7)
    admin = Usuario(familia=familia, nome="Ana Demo", email="demo@familia.com", papel="admin",
                    senha=bcrypt.generate_password_hash("demo12345").decode("utf-8"))
    membro = Usuario(familia=familia, nome="Bruno Demo", email="bruno@familia.com", papel="membro",
                     senha=bcrypt.generate_password_hash("demo12345").decode("utf-8"))
    database.session.add_all([familia, admin, membro])
    criar_categorias_padrao(familia)
    database.session.flush()

    cat = {c.nome: c for c in Categoria.query.filter_by(familia_id=familia.id)}
    metas = {"Moradia": 2200, "Alimentação": 1500, "Transporte": 600, "Lazer": 400, "Saúde": 500, "Educação": 800}
    for nome, meta in metas.items():
        cat[nome].meta_mensal = Decimal(meta)

    hoje = data_hoje()
    # 7 meses de histórico (6 anteriores + atual)
    for i in range(6, -1, -1):
        mes = somar_meses(hoje.replace(day=1), -i)
        limite = hoje.day if i == 0 else 28

        def dia(d):
            return mes.replace(day=min(d, limite))

        database.session.add_all([
            Receita(familia_id=familia.id, categoria=cat["Salário"], usuario=admin, descricao="Salário Ana",
                    valor=Decimal("6200.00"), data_recebimento=dia(5), conta_origem="Conta corrente"),
            Receita(familia_id=familia.id, categoria=cat["Salário"], usuario=membro, descricao="Salário Bruno",
                    valor=Decimal("4300.00"), data_recebimento=dia(6), conta_origem="Conta corrente"),
        ])
        if i % 2 == 0:
            database.session.add(Receita(familia_id=familia.id, categoria=cat["Rendimentos extras"], usuario=admin,
                                         descricao="Rendimento CDB", valor=valor(80, 220),
                                         data_recebimento=dia(20), conta_origem="Investimentos"))
        gastos = [("Moradia", "Aluguel", 1800, 1800, "Transferência", 10),
                  ("Moradia", "Condomínio", 420, 420, "Boleto", 10),
                  ("Alimentação", "Supermercado", 450, 700, "Cartão de crédito", 8),
                  ("Alimentação", "Feira e padaria", 120, 260, "Pix", 15),
                  ("Alimentação", "Supermercado", 300, 520, "Cartão de débito", 22),
                  ("Transporte", "Combustível", 250, 380, "Cartão de crédito", 12),
                  ("Transporte", "Aplicativo de transporte", 40, 120, "Cartão de crédito", 18),
                  ("Saúde", "Farmácia", 60, 220, "Cartão de débito", 14),
                  ("Educação", "Mensalidade escolar", 780, 780, "Boleto", 5),
                  ("Lazer", "Cinema e restaurante", 120, 380, "Cartão de crédito", 24),
                  ("Despesas fixas", "Internet e celular", 189.90, 189.90, "Débito automático", 15)]
        for categoria, desc, vmin, vmax, forma, d in gastos:
            if i == 0 and d > hoje.day:
                continue
            database.session.add(Despesa(familia_id=familia.id, categoria=cat[categoria], usuario=admin,
                                         descricao=desc, valor=valor(vmin, vmax), data_pagamento=dia(d),
                                         forma_pagamento=forma))

    # Contas a pagar: atrasada, próximas, recorrente e parcelada
    database.session.add_all([
        ContaPagar(familia_id=familia.id, categoria=cat["Moradia"], descricao="Conta de luz",
                   valor=Decimal("238.45"), data_vencimento=hoje - timedelta(days=2), recorrente=True,
                   data_competencia=somar_meses(hoje.replace(day=1), -1)),
        ContaPagar(familia_id=familia.id, categoria=cat["Moradia"], descricao="Conta de água",
                   valor=Decimal("96.30"), data_vencimento=hoje + timedelta(days=3), recorrente=True),
        ContaPagar(familia_id=familia.id, categoria=cat["Impostos e tributos"], descricao="IPVA — cota única",
                   valor=Decimal("1650.00"), data_vencimento=hoje + timedelta(days=25)),
    ])
    for n in range(10):
        venc = somar_meses(hoje.replace(day=1), n).replace(day=20)
        database.session.add(ContaPagar(familia_id=familia.id, categoria=cat["Transporte"],
                                        descricao="Parcela do seguro do carro", valor=Decimal("215.90"),
                                        data_vencimento=venc, parcela_atual=n + 1, total_parcelas=10))

    database.session.add_all([
        ContaReceber(familia_id=familia.id, categoria=cat["Serviços autônomos"], descricao="Projeto de design",
                     devedor="Cliente ACME", valor=Decimal("2800.00"), data_prevista=hoje + timedelta(days=6),
                     documento="Contrato 14/2026"),
        ContaReceber(familia_id=familia.id, categoria=cat["Reembolsos"], descricao="Reembolso consulta médica",
                     devedor="Plano de saúde", valor=Decimal("350.00"), data_prevista=hoje - timedelta(days=4)),
        ContaReceber(familia_id=familia.id, categoria=cat["Acordos e causas judiciais"],
                     descricao="Ação trabalhista — acordo", devedor="Empresa XYZ", valor=Decimal("12500.00"),
                     data_prevista=hoje + timedelta(days=90), documento="Processo 0001234-56.2025.5.02.0001",
                     observacoes="Pagamento previsto após homologação do acordo."),
    ])
    database.session.commit()
    atualizar_status_vencidos(familia.id)
    print("Banco de demonstração criado.")
    print("  Login administrador: demo@familia.com / demo12345")
    print("  Login membro:        bruno@familia.com / demo12345")


with app.app_context():
    if "--demo" in sys.argv:
        popular_demo()
    else:
        database.create_all()
        print("Tabelas criadas (as existentes foram mantidas).")
