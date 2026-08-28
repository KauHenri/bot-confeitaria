import sqlite3
import json
from datetime import datetime
import os

DB_NAME = os.getenv('DB_PATH', 'confeitaria.db')

def get_db():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """Inicializa as tabelas do banco de dados se não existirem."""
    with get_db() as conn:
        cursor = conn.cursor()
        
        # Tabela de Estoque
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS estoque (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item TEXT UNIQUE NOT NULL,
            preco REAL NOT NULL DEFAULT 0.0,
            disponivel BOOLEAN NOT NULL DEFAULT 1
        )
        ''')
        
        # Tabela de Clientes
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS clientes (
            telefone TEXT PRIMARY KEY,
            nome TEXT NOT NULL,
            total_comprado REAL DEFAULT 0.0,
            total_pago REAL DEFAULT 0.0,
            saldo_devedor REAL DEFAULT 0.0,
            data_nascimento TEXT
        )
        ''')
        
        # Tabela de Vendas
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS vendas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            data_hora TEXT NOT NULL,
            telefone TEXT,
            nome_cliente TEXT,
            pedido TEXT,
            valor REAL DEFAULT 0.0,
            local TEXT,
            status_pagamento TEXT,
            itens_str TEXT,
            FOREIGN KEY(telefone) REFERENCES clientes(telefone)
        )
        ''')
        
        # Tabela de Encomendas
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS encomendas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            data_hora TEXT NOT NULL,
            data_entrega TEXT,
            telefone TEXT,
            nome_cliente TEXT,
            pedido TEXT,
            status TEXT,
            FOREIGN KEY(telefone) REFERENCES clientes(telefone)
        )
        ''')
        
        # Tabela de Finanças
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS financas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            data TEXT NOT NULL,
            tipo TEXT,
            descricao TEXT,
            valor REAL DEFAULT 0.0,
            categoria TEXT
        )
        ''')
        
        # Tabela de Histórico de Preços
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS historico_precos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            data TEXT NOT NULL,
            supermercado TEXT,
            item TEXT,
            quantidade TEXT,
            preco REAL
        )
        ''')
        
        # Tabela de Tarefas
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS tarefas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            data TEXT NOT NULL,
            tarefa TEXT NOT NULL,
            status TEXT DEFAULT 'Pendente'
        )
        ''')
        
        conn.commit()

# --- FUNÇÕES DE ESTOQUE ---

def obter_estoque_atual_db():
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT item, preco FROM estoque WHERE disponivel = 1")
        registros = cursor.fetchall()
        
        if not registros:
            return "O cardápio está vazio no sistema."
            
        texto = "Lista de produtos disponíveis para hoje:\n"
        for reg in registros:
            preco_fmt = f"{reg['preco']:.2f}".replace('.', ',')
            texto += f"- {reg['item']} (R$ {preco_fmt})\n"
        return texto

def obter_cardapio_completo_db():
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT item, disponivel FROM estoque")
        registros = cursor.fetchall()
        
        if not registros:
            return "O cardápio está vazio no sistema."
            
        texto = "CARDÁPIO COMPLETO (Todos os itens cadastrados no banco):\n"
        for reg in registros:
            disp = "Sim" if reg['disponivel'] else "Não"
            texto += f"- {reg['item']} (Status atual: {disp})\n"
        return texto

def atualizar_estoque_db(itens):
    with get_db() as conn:
        cursor = conn.cursor()
        for novo_item in itens:
            nome = novo_item.get("item", "").strip()
            disponivel = 1 if novo_item.get("disponivel", True) else 0
            preco = float(novo_item.get("preco", 0))
            
            cursor.execute("SELECT id FROM estoque WHERE lower(item) = lower(?)", (nome,))
            row = cursor.fetchone()
            
            if row:
                if preco > 0:
                    cursor.execute("UPDATE estoque SET disponivel = ?, preco = ? WHERE id = ?", (disponivel, preco, row['id']))
                else:
                    cursor.execute("UPDATE estoque SET disponivel = ? WHERE id = ?", (disponivel, row['id']))
            else:
                cursor.execute("INSERT INTO estoque (item, preco, disponivel) VALUES (?, ?, ?)", (nome, preco, disponivel))
        conn.commit()
    return True

def zerar_estoque_completo_db():
    with get_db() as conn:
        conn.execute("UPDATE estoque SET disponivel = 0")
        conn.commit()
    return True

def calcular_total_seguro_db(itens_pedidos):
    valor_final = 0.0
    with get_db() as conn:
        cursor = conn.cursor()
        for item in itens_pedidos:
            nome_item = str(item.get("item", "")).strip().lower()
            qtd = int(item.get("quantidade", 0))
            
            cursor.execute("SELECT preco FROM estoque WHERE lower(item) = ?", (nome_item,))
            row = cursor.fetchone()
            if row:
                valor_final += (qtd * row['preco'])
    return valor_final

def verificar_disponibilidade_db(itens_pedidos):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT lower(item) as nome_lower FROM estoque WHERE disponivel = 1")
        itens_disponiveis = [row['nome_lower'] for row in cursor.fetchall()]
        
        for pedido in itens_pedidos:
            nome_pedido = str(pedido.get("item", "")).strip().lower()
            encontrou = False
            for item_disp in itens_disponiveis:
                if nome_pedido in item_disp or item_disp in nome_pedido:
                    encontrou = True
                    break
            if not encontrou:
                nome_bonito = str(pedido.get("item", "")).title()
                return False, f"Poxa, o item '{nome_bonito}' não está disponível no cardápio de hoje."
    return True, ""

# --- FUNÇÕES DE CLIENTES E VENDAS ---

def atualizar_compra_cliente_db(telefone, nome, valor_compra):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT total_comprado, total_pago FROM clientes WHERE telefone = ?", (telefone,))
        row = cursor.fetchone()
        
        if row:
            novo_total_comp = row['total_comprado'] + float(valor_compra)
            saldo_devedor = novo_total_comp - row['total_pago']
            cursor.execute('''UPDATE clientes 
                              SET total_comprado = ?, saldo_devedor = ? 
                              WHERE telefone = ?''', (novo_total_comp, saldo_devedor, telefone))
        else:
            valor = float(valor_compra)
            cursor.execute('''INSERT INTO clientes (telefone, nome, total_comprado, total_pago, saldo_devedor) 
                              VALUES (?, ?, ?, 0.0, ?)''', (telefone, nome, valor, valor))
        conn.commit()
    return True

def verificar_saldo_cliente_db(telefone):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT saldo_devedor FROM clientes WHERE telefone = ?", (telefone,))
        row = cursor.fetchone()
        if row:
            saldo = row['saldo_devedor']
            return f"R$ {saldo:.2f}".replace('.', ',')
        return "R$ 0,00"

def registrar_venda_db(telefone, nome_cliente, pedido, valor, local, itens_vendidos, status_pagamento="Pendente ⏳"):
    data_hora = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    itens_str = json.dumps(itens_vendidos, ensure_ascii=False)
    valor_float = float(valor)
    
    with get_db() as conn:
        conn.execute('''INSERT INTO vendas (data_hora, telefone, nome_cliente, pedido, valor, local, status_pagamento, itens_str)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
                     (data_hora, telefone, nome_cliente, pedido, valor_float, local, status_pagamento, itens_str))
        conn.commit()
    return True

def cancelar_ultimo_pedido_db(telefone, tipo_alvo="qualquer"):
    with get_db() as conn:
        cursor = conn.cursor()
        
        if tipo_alvo in ["venda", "qualquer"]:
            cursor.execute('''SELECT id, valor, status_pagamento FROM vendas 
                              WHERE telefone = ? AND status_pagamento NOT LIKE '%Cancelado%' 
                              ORDER BY id DESC LIMIT 1''', (telefone,))
            row = cursor.fetchone()
            
            if row:
                cursor.execute("UPDATE vendas SET status_pagamento = 'Cancelado ❌' WHERE id = ?", (row['id'],))
                valor_cancelado = row['valor']
                
                cursor.execute("SELECT total_comprado, total_pago FROM clientes WHERE telefone = ?", (telefone,))
                cli = cursor.fetchone()
                if cli:
                    novo_comp = max(0, cli['total_comprado'] - valor_cancelado)
                    novo_saldo = max(0, novo_comp - cli['total_pago'])
                    cursor.execute("UPDATE clientes SET total_comprado = ?, saldo_devedor = ? WHERE telefone = ?",
                                   (novo_comp, novo_saldo, telefone))
                conn.commit()
                return True, "Prontinho! Pedido cancelado e valor retirado da conta."
                
        if tipo_alvo in ["encomenda", "qualquer"]:
            cursor.execute('''SELECT id, status FROM encomendas 
                              WHERE telefone = ? AND status NOT LIKE '%Cancelada%' 
                              ORDER BY id DESC LIMIT 1''', (telefone,))
            row = cursor.fetchone()
            if row:
                cursor.execute("UPDATE encomendas SET status = 'Cancelada ❌' WHERE id = ?", (row['id'],))
                conn.commit()
                if "Confirmada" in row['status']:
                    return True, "Encomenda cancelada. Fale com a chefe sobre possíveis sinais pagos."
                return True, "Sua encomenda foi cancelada!"
                
    return False, "Nenhum pedido recente encontrado."

def cancelar_pedido_admin_db(nome_buscado):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('''SELECT id, telefone, valor FROM vendas 
                          WHERE lower(nome_cliente) LIKE lower(?) AND status_pagamento NOT LIKE '%Cancelado%' 
                          ORDER BY id DESC LIMIT 1''', (f"%{nome_buscado}%",))
        row = cursor.fetchone()
        
        if row:
            telefone = row['telefone']
            return cancelar_ultimo_pedido_db(telefone, "venda")
    return False, f"Nenhuma venda recente para '{nome_buscado}'."

def listar_todos_devedores_db():
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT nome, saldo_devedor FROM clientes WHERE saldo_devedor > 0.01 ORDER BY saldo_devedor DESC")
        registros = cursor.fetchall()
        
        if not registros:
            return "Chefe, não tem ninguém devendo! Todo mundo com a conta em dia. 🎉"
            
        texto = "💸 *LISTA DE QUEM ESTÁ DEVENDO* 💸\n\n"
        valor_total = 0.0
        
        for reg in registros:
            saldo = reg['saldo_devedor']
            texto += f"▫️ *{reg['nome']}*: R$ {saldo:.2f}\n".replace('.', ',')
            valor_total += saldo
            
        texto += f"\n💰 *Total na rua:* R$ {valor_total:.2f}".replace('.', ',')
        return texto

def atualizar_status_pagamento_db(nome_buscado):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('''SELECT id, status_pagamento FROM vendas 
                          WHERE lower(nome_cliente) LIKE lower(?) 
                          ORDER BY id DESC LIMIT 1''', (f"%{nome_buscado}%",))
        row = cursor.fetchone()
        
        if row:
            if "Pendente" in row['status_pagamento']:
                cursor.execute("UPDATE vendas SET status_pagamento = 'Pago ✅' WHERE id = ?", (row['id'],))
                conn.commit()
                return True, f"Prontinho! Baixa do pagamento de {nome_buscado} concluída."
            else:
                return False, f"O pedido mais recente de {nome_buscado} já estava Pago."
        return False, f"Não achei pedido pendente para {nome_buscado}."

def registrar_pagamento_fiado_db(nome_buscado, valor_pago):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT telefone, nome, total_comprado, total_pago FROM clientes WHERE lower(nome) LIKE lower(?)", (f"%{nome_buscado}%",))
        row = cursor.fetchone()
        
        if row:
            novo_total_pago = row['total_pago'] + float(valor_pago)
            saldo_devedor = max(0, row['total_comprado'] - novo_total_pago)
            
            cursor.execute("UPDATE clientes SET total_pago = ?, saldo_devedor = ? WHERE telefone = ?", 
                           (novo_total_pago, saldo_devedor, row['telefone']))
                           
            if saldo_devedor <= 0.01:
                cursor.execute("UPDATE vendas SET status_pagamento = 'Pago ✅' WHERE telefone = ? AND status_pagamento LIKE '%Pendente%'", (row['telefone'],))
                conn.commit()
                return True, f"Pronto! Pagamento de R$ {valor_pago:.2f} quitou a dívida de {row['nome']}. Saldo zerado!".replace('.', ',')
            
            conn.commit()
            return True, f"Anotado! {row['nome']} pagou R$ {valor_pago:.2f}. Restam R$ {saldo_devedor:.2f}.".replace('.', ',')
            
        return False, f"Cliente '{nome_buscado}' não encontrado."

def gerar_extrato_fiado_db(busca, por_telefone=False):
    with get_db() as conn:
        cursor = conn.cursor()
        if por_telefone:
            cursor.execute("SELECT telefone, nome, total_comprado, total_pago, saldo_devedor FROM clientes WHERE telefone = ?", (busca,))
        else:
            cursor.execute("SELECT telefone, nome, total_comprado, total_pago, saldo_devedor FROM clientes WHERE lower(nome) LIKE lower(?)", (f"%{busca}%",))
        
        cli = cursor.fetchone()
        if not cli:
            return False, "Registro não encontrado."
            
        if cli['saldo_devedor'] <= 0.01:
            nome_exibir = "A sua conta" if por_telefone else f"A conta de {cli['nome']}"
            return True, f"{nome_exibir} está zerada! ✅"
            
        extrato = f"🧾 *SEU EXTRATO DE COMPRAS*\n\n" if por_telefone else f"🧾 *EXTRATO - {cli['nome']}*\n\n"
        
        cursor.execute('''SELECT data_hora, pedido, valor, itens_str FROM vendas 
                          WHERE telefone = ? AND status_pagamento LIKE '%Pendente%' 
                          ORDER BY id DESC LIMIT 10''', (cli['telefone'],))
        compras = cursor.fetchall()
        
        if compras:
            extrato += "*Últimas movimentações pendentes:*\n"
            for c in compras:
                data = c['data_hora'].split(' ')[0]
                pedido_exibir = c['pedido']
                if c['itens_str']:
                    try:
                        itens = json.loads(c['itens_str'])
                        lista = [f"{i.get('quantidade', '')} {i.get('item', '')}" for i in itens]
                        if lista: pedido_exibir = ", ".join(lista)
                    except: pass
                extrato += f"▫️ {data}: {pedido_exibir} -> R$ {c['valor']:.2f}\n".replace('.', ',')
                
        cursor.execute('''SELECT data_hora, data_entrega, pedido FROM encomendas 
                          WHERE telefone = ? AND status LIKE '%Confirmada%' ''', (cli['telefone'],))
        encomendas = cursor.fetchall()
        if encomendas:
            extrato += "\n*Encomendas inclusas:*\n"
            for e in encomendas:
                data = e['data_hora'].split(' ')[0]
                extrato += f"🎂 {data} (Entrega: {e['data_entrega']}) -> {e['pedido']}\n"
                
        extrato += "\n📊 *RESUMO DA CONTA:*\n"
        extrato += f"🛒 Total Comprado (Histórico): R$ {cli['total_comprado']:.2f}\n".replace('.', ',')
        extrato += f"✅ Valor Abatido/Pago: R$ {cli['total_pago']:.2f}\n".replace('.', ',')
        extrato += f"💰 *SALDO DEVEDOR ATUAL:* R$ {cli['saldo_devedor']:.2f}".replace('.', ',')
        
        return True, extrato

# --- FUNÇÕES DE ENCOMENDAS ---

def solicitar_encomenda_db(telefone, nome_cliente, pedido, data_entrega):
    data_hoje = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    with get_db() as conn:
        conn.execute('''INSERT INTO encomendas (data_hora, data_entrega, telefone, nome_cliente, pedido, status)
                        VALUES (?, ?, ?, ?, ?, 'Aguardando Aprovação 🟡')''',
                     (data_hoje, data_entrega, telefone, nome_cliente, pedido))
        conn.commit()
    return True

def confirmar_encomenda_admin_db(nome_buscado, valor_final):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('''SELECT id, telefone, nome_cliente FROM encomendas 
                          WHERE lower(nome_cliente) LIKE lower(?) AND status LIKE '%Aguardando%' 
                          ORDER BY id DESC LIMIT 1''', (f"%{nome_buscado}%",))
        row = cursor.fetchone()
        
        if row:
            cursor.execute("UPDATE encomendas SET status = 'Confirmada ✅' WHERE id = ?", (row['id'],))
            conn.commit()
            atualizar_compra_cliente_db(row['telefone'], row['nome_cliente'], valor_final)
            return True, f"Feito, chefe! A encomenda de {row['nome_cliente']} foi confirmada e lançada no Livro Caixa."
            
    return False, f"Não achei encomenda pendente para '{nome_buscado}'."

# --- FUNÇÕES FINANCEIRAS ---

def registrar_gasto_admin_db(tipo, descricao, valor, categoria_aba="Financas_Empresa"):
    data_atual = datetime.now().strftime("%d/%m/%Y")
    with get_db() as conn:
        conn.execute('''INSERT INTO financas (data, tipo, descricao, valor, categoria)
                        VALUES (?, ?, ?, ?, ?)''',
                     (data_atual, tipo, descricao, float(valor), categoria_aba))
        conn.commit()
    return True

def gerar_relatorio_financeiro_db(mes_ano=None):
    if not mes_ano:
        mes_ano = datetime.now().strftime("%m/%Y")
        
    try:
        nome_mes = datetime.strptime(mes_ano, "%m/%Y").strftime("%B").capitalize()
    except:
        nome_mes = mes_ano

    with get_db() as conn:
        cursor = conn.cursor()
        
        cursor.execute("SELECT valor, status_pagamento FROM vendas WHERE data_hora LIKE ? AND status_pagamento NOT LIKE '%Cancelado%'", (f"%{mes_ano}%",))
        vendas = cursor.fetchall()
        
        total_vendido = 0.0
        total_recebido = 0.0
        for v in vendas:
            total_vendido += v['valor']
            if "Pago" in v['status_pagamento']:
                total_recebido += v['valor']
                
        cursor.execute("SELECT valor FROM financas WHERE data LIKE ? AND categoria = 'Financas_Empresa'", (f"%{mes_ano}%",))
        gastos = cursor.fetchall()
        total_gasto = sum(g['valor'] for g in gastos)
        
        lucro_liquido = total_vendido - total_gasto
        saldo_em_caixa = total_recebido - total_gasto
        
        relatorio = f"📊 *FECHAMENTO MENSAL - {nome_mes}*\n\n"
        relatorio += f"📈 *Total Vendido:* R$ {total_vendido:.2f}\n".replace('.', ',')
        relatorio += f"✅ *Total Recebido (Pix/Dinheiro):* R$ {total_recebido:.2f}\n".replace('.', ',')
        relatorio += f"⏳ *A Receber (Fiado):* R$ {(total_vendido - total_recebido):.2f}\n\n".replace('.', ',')
        
        relatorio += f"📉 *Despesas/Insumos:* R$ {total_gasto:.2f}\n".replace('.', ',')
        relatorio += "------------------------\n"
        
        if lucro_liquido > 0:
            relatorio += f"💰 *LUCRO LÍQUIDO:* R$ {lucro_liquido:.2f} 🥳\n\n".replace('.', ',')
            caixa_empresa = lucro_liquido * 0.10
            pro_labore = lucro_liquido * 0.90
            relatorio += "🍯 *DIVISÃO DO LUCRO (Regra 10/90):*\n"
            relatorio += f"🏢 *Caixa da Empresa (10%):* R$ {caixa_empresa:.2f} (Para repor estoque)\n".replace('.', ',')
            relatorio += f"👩‍🍳 *Seu Pró-Labore (90%):* R$ {pro_labore:.2f} (Livre!)\n\n".replace('.', ',')
        else:
            relatorio += f"⚠️ *PREJUÍZO/EMPATE:* R$ {lucro_liquido:.2f} 🛑\n\n".replace('.', ',')
            
        relatorio += f"🏦 *Saldo Real no Caixa:* R$ {saldo_em_caixa:.2f}".replace('.', ',')
        return True, relatorio

def registrar_nota_fiscal_db(supermercado, valor_empresa, valor_pessoal, itens_empresa):
    data_atual = datetime.now().strftime("%d/%m/%Y")
    with get_db() as conn:
        cursor = conn.cursor()
        
        if valor_empresa > 0:
            cursor.execute("INSERT INTO financas (data, tipo, descricao, valor, categoria) VALUES (?, 'Saída', ?, ?, 'Financas_Empresa')",
                           (data_atual, f"Insumos - {supermercado}", float(valor_empresa)))
                           
        if valor_pessoal > 0:
            cursor.execute("INSERT INTO financas (data, tipo, descricao, valor, categoria) VALUES (?, 'Saída', ?, ?, 'Financas_Pessoal')",
                           (data_atual, f"Supermercado - {supermercado}", float(valor_pessoal)))
                           
        if itens_empresa:
            for item in itens_empresa:
                if isinstance(item, dict):
                    nome = item.get("item", "")
                    qtd = str(item.get("quantidade", ""))
                    preco = float(item.get("preco_unitario", 0))
                    if nome and preco > 0:
                        cursor.execute("INSERT INTO historico_precos (data, supermercado, item, quantidade, preco) VALUES (?, ?, ?, ?, ?)",
                                       (data_atual, supermercado, nome, qtd, preco))
        conn.commit()
    return True

# --- TAREFAS E RELATÓRIOS ---

def registrar_tarefa_lista_db(tarefa):
    data_hoje = datetime.now().strftime("%d/%m/%Y")
    with get_db() as conn:
        conn.execute("INSERT INTO tarefas (data, tarefa, status) VALUES (?, ?, 'Pendente ⬜')", (data_hoje, tarefa))
        conn.commit()
    return True

def relatorio_pedidos_admin_db():
    hoje = datetime.now().strftime("%d/%m/%Y")
    with get_db() as conn:
        cursor = conn.cursor()
        
        texto = f"📋 *RESUMO DE PEDIDOS - {hoje}*\n\n📦 *PRONTA ENTREGA:*\n"
        
        cursor.execute('''SELECT nome_cliente, pedido, valor, local FROM vendas 
                          WHERE data_hora LIKE ? AND status_pagamento NOT LIKE '%Cancelado%' ''', (f"%{hoje}%",))
        vendas = cursor.fetchall()
        
        if not vendas:
            texto += "Nenhum pedido finalizado hoje.\n"
        else:
            faturamento = 0.0
            agrupados = {}
            for v in vendas:
                faturamento += v['valor']
                cli = v['nome_cliente']
                if cli in agrupados:
                    agrupados[cli]['pedido'] += f" + {v['pedido']}"
                    agrupados[cli]['valor'] += v['valor']
                else:
                    agrupados[cli] = {'pedido': v['pedido'], 'valor': v['valor'], 'local': v['local']}
            
            for cli, dados in agrupados.items():
                texto += f"▫️ *{cli}*: {dados['pedido']} (R$ {dados['valor']:.2f} - {dados['local']})\n".replace('.', ',')
            
            texto += f"\n💰 *Faturamento do Dia:* R$ {faturamento:.2f}\n".replace('.', ',')
            
        texto += "\n🎂 *ENCOMENDAS ATIVAS:*\n"
        cursor.execute("SELECT data_entrega, nome_cliente, pedido, status FROM encomendas WHERE status NOT LIKE '%Cancelada%'")
        encomendas = cursor.fetchall()
        
        if not encomendas:
            texto += "Nenhuma encomenda pendente.\n"
        else:
            for e in encomendas:
                if "Aguardando" in e['status'] or "Confirmada" in e['status']:
                    texto += f"▫️ *{e['nome_cliente']}* (Para {e['data_entrega']}): {e['pedido']} - {e['status']}\n"
                    
        return True, texto

# Executa ao importar
init_db()

def calcular_preco_em_doces_db(item_desejado, valor_item):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT item, preco FROM estoque WHERE lower(item) LIKE '%bolo de fubá (maior)%'")
        row = cursor.fetchone()
        if not row or row['preco'] <= 0:
            cursor.execute("SELECT item, preco FROM estoque WHERE preco > 0 LIMIT 1")
            row = cursor.fetchone()
        if row and row['preco'] > 0:
            preco_ref = row['preco']
            produto_ref = row['item']
            faturamento_necessario = valor_item * 5
            qtd_real = int(faturamento_necessario / preco_ref)
            msg = f"🤔 *Análise de Compra: {item_desejado.title()}*\n\n"
            msg += f"Chefe, esse item custa R$ {valor_item:.2f}.\n\n".replace('.', ',')
            msg += f"Pela nossa *Regra dos Potes*, para você colocar esse valor limpo no bolso sem tirar o dinheiro de repor ingredientes da empresa, a confeitaria precisa faturar R$ {faturamento_necessario:.2f}!\n\n".replace('.', ',')
            msg += f"🥵 Na prática, você vai precisar assar e vender **{qtd_real} {produto_ref}s** só para pagar isso.\n\n"
            msg += "Vale a pena o esforço ou deixamos para o mês que vem? 😅"
            return True, msg
        return False, "Chefe, não consegui calcular o suor porque não achei o preço dos produtos."

def verificar_aniversariantes_db():
    from datetime import datetime
    data_hoje = datetime.now().strftime("%d/%m")
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT nome, telefone FROM clientes WHERE data_nascimento LIKE ?", (f"{data_hoje}%",))
        aniversariantes = cursor.fetchall()
        if aniversariantes:
            msg = "🎉 *ANIVERSARIANTES DO DIA!* 🎉\n\n"
            for a in aniversariantes:
                tel = a['telefone'].split('@')[0] if '@' in a['telefone'] else a['telefone']
                msg += f"▫️ {a['nome']} ({tel})\n"
            msg += "\nChefe, que tal mandar uma mensagem de parabéns ou oferecer um mimo?"
            return msg
        return None

def gerar_relatorio_semanal_db():
    from datetime import datetime, timedelta
    agora = datetime.now()
    inicio_semana = agora - timedelta(days=7)
    
    with get_db() as conn:
        cursor = conn.cursor()
        
        # Puxa vendas dos últimos 7 dias (simplificado, comparando string de data)
        # Como a data está em DD/MM/YYYY, o ideal seria parsear, mas SQLite string compare não funciona bem para DD/MM.
        # Solução alternativa: Puxar tudo e filtrar em Python para ser exato.
        cursor.execute("SELECT data_hora, valor, status_pagamento FROM vendas WHERE status_pagamento NOT LIKE '%Cancelado%'")
        vendas = cursor.fetchall()
        
        total_vendido = 0.0
        for v in vendas:
            try:
                data_v = datetime.strptime(v['data_hora'].split(' ')[0], "%d/%m/%Y")
                if data_v >= inicio_semana:
                    total_vendido += v['valor']
            except: pass
            
        cursor.execute("SELECT data, valor FROM financas WHERE categoria = 'Financas_Empresa'")
        gastos = cursor.fetchall()
        total_gasto = 0.0
        for g in gastos:
            try:
                data_g = datetime.strptime(g['data'], "%d/%m/%Y")
                if data_g >= inicio_semana:
                    total_gasto += g['valor']
            except: pass
            
        lucro = total_vendido - total_gasto
        
        msg = f"📊 *RESUMO DA SEMANA* 📊\n\n"
        msg += f"📈 Vendemos: R$ {total_vendido:.2f}\n".replace('.', ',')
        msg += f"📉 Gastamos: R$ {total_gasto:.2f}\n".replace('.', ',')
        msg += f"💰 *Lucro da Semana:* R$ {lucro:.2f}".replace('.', ',')
        return msg
