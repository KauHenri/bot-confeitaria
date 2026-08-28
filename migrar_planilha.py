import gspread
from oauth2client.service_account import ServiceAccountCredentials
import os
from dotenv import load_dotenv
import sqlite3
import database
import json

load_dotenv()
PLANILHA_ID = os.getenv("PLANILHA_ID")

def conectar_planilha():
    escopos = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
    credenciais = ServiceAccountCredentials.from_json_keyfile_name('credenciais.json', escopos)
    cliente = gspread.authorize(credenciais)
    return cliente.open_by_key(PLANILHA_ID)

def limpar_valor(valor_str):
    if isinstance(valor_str, (int, float)):
        return float(valor_str)
    limpo = str(valor_str).replace("R$", "").strip()
    if "," in limpo:
        limpo = limpo.replace(".", "").replace(",", ".")
    try:
        return float(limpo)
    except ValueError:
        return 0.0

def migrar_dados():
    print("Iniciando migração da Planilha para o SQLite...")
    
    planilha = conectar_planilha()
    conn = sqlite3.connect('confeitaria.db')
    cursor = conn.cursor()
    
    # 1. Migrar Estoque
    try:
        print("Migrando Estoque...")
        aba = planilha.worksheet("Estoque")
        registros = aba.get_all_records()
        for linha in registros:
            item = linha.get('Item', '').strip()
            preco = limpar_valor(linha.get('Preco_Unitario', 0))
            disp_str = str(linha.get('Disponivel', '')).strip().lower()
            disponivel = 1 if disp_str in ['sim', '1', 'true', 'ok', 'tem'] else 0
            
            if item:
                cursor.execute("INSERT OR IGNORE INTO estoque (item, preco, disponivel) VALUES (?, ?, ?)", 
                               (item, preco, disponivel))
        print(f"{len(registros)} itens de estoque migrados.")
    except Exception as e:
        print(f"Erro no estoque: {e}")
        
    # 2. Migrar Clientes
    try:
        print("Migrando Clientes...")
        aba = planilha.worksheet("Clientes")
        registros = aba.get_all_records()
        for linha in registros:
            telefone = str(linha.get('Telefone', '')).strip()
            nome = str(linha.get('Nome', '')).strip()
            total_comp = limpar_valor(linha.get('Total_Comprado', 0))
            total_pago = limpar_valor(linha.get('Total_Pago', 0))
            saldo = limpar_valor(linha.get('Saldo_Devedor', 0))
            
            if telefone and nome:
                cursor.execute('''INSERT OR REPLACE INTO clientes 
                                  (telefone, nome, total_comprado, total_pago, saldo_devedor) 
                                  VALUES (?, ?, ?, ?, ?)''', 
                               (telefone, nome, total_comp, total_pago, saldo))
        print(f"{len(registros)} clientes migrados.")
    except Exception as e:
        print(f"Erro nos clientes: {e}")
        
    # 3. Migrar Vendas
    try:
        print("Migrando Vendas...")
        aba = planilha.worksheet("Vendas")
        dados = aba.get_all_values()
        for linha in dados[1:]:
            if len(linha) >= 7:
                data = linha[0]
                telefone = linha[1]
                nome = linha[2]
                pedido = linha[3]
                valor = limpar_valor(linha[4])
                local = linha[5]
                status = linha[6]
                itens_str = linha[7] if len(linha) > 7 else "[]"
                
                cursor.execute('''INSERT INTO vendas 
                                  (data_hora, telefone, nome_cliente, pedido, valor, local, status_pagamento, itens_str) 
                                  VALUES (?, ?, ?, ?, ?, ?, ?, ?)''', 
                               (data, telefone, nome, pedido, valor, local, status, itens_str))
        print(f"{len(dados)-1} vendas migradas.")
    except Exception as e:
        print(f"Erro nas vendas: {e}")
        
    # 4. Migrar Encomendas
    try:
        print("Migrando Encomendas...")
        aba = planilha.worksheet("Encomendas")
        dados = aba.get_all_values()
        for linha in dados[1:]:
            if len(linha) >= 6:
                data_hora = linha[0]
                data_entrega = linha[1]
                telefone = linha[2]
                nome = linha[3]
                pedido = linha[4]
                status = linha[5]
                
                cursor.execute('''INSERT INTO encomendas 
                                  (data_hora, data_entrega, telefone, nome_cliente, pedido, status) 
                                  VALUES (?, ?, ?, ?, ?, ?)''', 
                               (data_hora, data_entrega, telefone, nome, pedido, status))
        print(f"{len(dados)-1} encomendas migradas.")
    except Exception as e:
        print(f"Erro nas encomendas: {e}")
        
    # 5. Migrar Finanças (Empresa e Pessoal)
    try:
        print("Migrando Finanças...")
        abas = ["Financas_Empresa", "Financas_Pessoal"]
        total_fin = 0
        for aba_nome in abas:
            try:
                aba = planilha.worksheet(aba_nome)
                dados = aba.get_all_values()
                for linha in dados[1:]:
                    if len(linha) >= 4:
                        data = linha[0]
                        tipo = linha[1]
                        desc = linha[2]
                        valor = limpar_valor(linha[3])
                        
                        cursor.execute('''INSERT INTO financas 
                                          (data, tipo, descricao, valor, categoria) 
                                          VALUES (?, ?, ?, ?, ?)''', 
                                       (data, tipo, desc, valor, aba_nome))
                        total_fin += 1
            except Exception:
                pass
        print(f"{total_fin} registros financeiros migrados.")
    except Exception as e:
        print(f"Erro nas finanças: {e}")
        
    conn.commit()
    conn.close()
    print("✅ Migração concluída com sucesso! Banco SQLite pronto para uso.")

if __name__ == "__main__":
    migrar_dados()
