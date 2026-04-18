from datetime import datetime
import locale
import json
import os
import time
from threading import RLock
from flask import Flask, request, jsonify
import google.generativeai as genai
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from dotenv import load_dotenv

app = Flask(__name__)

load_dotenv()

# Configurações globais que vamos preencher depois
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
PLANILHA_ID = os.getenv("PLANILHA_ID")

# Configurando o cérebro (Gemini)
genai.configure(api_key=GEMINI_API_KEY)

# Configura o idioma para português para pegar o dia da semana correto
try:
	locale.setlocale(locale.LC_ALL, 'pt_BR.UTF-8')
except:
	pass # Caso o sistema não suporte, ele segue o padrão

# --- SISTEMA DE MEMÓRIA PERSISTENTE ---
ARQUIVO_HISTORICO = 'historico_conversas.json'

def carregar_historico():
	"""Carrega as conversas salvas no arquivo, se ele existir."""
	if os.path.exists(ARQUIVO_HISTORICO):
		try:
			with open(ARQUIVO_HISTORICO, 'r', encoding='utf-8') as f:
				return json.load(f)
		except Exception as e:
			print(f"⚠️ Erro ao ler histórico: {e}")
	return {}

def salvar_historico():
	"""Salva o dicionário de conversas no arquivo JSON."""
	try:
		with open(ARQUIVO_HISTORICO, 'w', encoding='utf-8') as f:
			json.dump(historico_conversas, f, ensure_ascii=False, indent=4)
	except Exception as e:
		print(f"❌ Erro ao salvar histórico: {e}")

# Inicia a memória carregando os dados do arquivo em vez de começar vazia
historico_conversas = carregar_historico()

def obter_contexto_data():
	agora = datetime.now()
	dia_semana = agora.strftime("%A")
	data_formatada = agora.strftime("%d/%m/%Y")
	return f"Hoje é {dia_semana}, dia {data_formatada}."

# --- CONFIGURAÇÕES DE TESTE E ADMIN ---
# A TRAVA: Coloque o seu número aqui. A IA SÓ vai processar mensagens vindas dele.
NUMERO_TESTE = os.getenv("NUMERO_TESTE")

NUMERO_ADMIN = "000"
ID_GRUPO_ADMIN = os.getenv("ID_GRUPO_ADMIN") 

# --- HORÁRIO DE FUNCIONAMENTO ---
HORA_ABRE = 8  # 08:00 da manhã
HORA_FECHA = 18 # 18:00 (6 da tarde)
# Mude para False quando for colocar o bot na confeitaria para valer!
MODO_CORUJA_TESTE = True

def verificar_loja_aberta():
	"""Checa se a hora atual está dentro do horário de funcionamento ou se o modo de teste está ativo."""
	if MODO_CORUJA_TESTE:
		return True
		
	hora_atual = datetime.now().hour
	return HORA_ABRE <= hora_atual < HORA_FECHA

# Criamos dois modelos separados, com instruções de sistema (System Instructions) diferentes
modelo_cliente = genai.GenerativeModel(
	'gemini-3.1-flash-lite-preview',
	system_instruction="""Você é o assistente de vendas de uma confeiteira.
	Sua função é atender o cliente, anotar o pedido e retornar EXCLUSIVAMENTE um objeto JSON válido, sem usar blocos de código Markdown (```json) e sem texto adicional.
	
	Regras para o JSON:
	- "acao": "registrar_venda" (se o pedido foi confirmado), "conversar", "cancelar_pedido", "cancelar_encomenda", "registrar_encomenda", "consultar_meu_extrato" ou "informar_pagamento".
	- "data_entrega": SE a ação for "registrar_encomenda", extraia o dia/data que o cliente quer receber o pedido (ex: "Sábado", "Amanhã", "Dia 15"). Se for venda normal, deixe vazio.
	- "pedido": Resumo do que foi pedido em texto.
	- "itens_vendidos": Uma lista EXATA dos itens para dar baixa no estoque. Ex: [{"item": "Rosca", "quantidade": 5}, {"item": "Bolo de fubá", "quantidade": 3}]. Se não houver venda confirmada, deixe [].
	- "valor_total": A soma total baseada nos preços do estoque.
	- "local": "APAE", "Superintendência" ou "Retirada".
	- "resposta_amigavel": A mensagem de texto para o cliente.
	"""
)

modelo_admin = genai.GenerativeModel(
	'gemini-3.1-flash-lite-preview',
	system_instruction="""Você é o assistente financeiro e de estoque da chefe. 
	Você NÃO conversa normalmente. Sua ÚNICA função é ler o que a chefe disse e transformar em um objeto JSON válido, sem usar blocos de código Markdown (```json) e sem texto adicional.
	
	Ações possíveis ("acao"):
	1. "registrar_financa": Para gastos, contas ou compras.
	2. "atualizar_estoque": Para quando ela disser o que produziu/vai ter no dia.
	3. "atualizar_pagamento": Para quando a chefe avisar que o cliente pagou o pedido.
	4. "conversar": Se for só uma pergunta ou bate-papo (ex: perguntar o que tem no estoque).
	5. "confirmar_encomenda": Para quando a chefe avisar que fechou o pedido de encomenda de um cliente e informar o valor total.
	6. "consultar_pedidos": Para quando a chefe pedir um resumo, relatório ou perguntar o que tem para entregar hoje.
	7. "consultar_extrato_cliente": Para quando a chefe perguntar o que um cliente específico comprou fiado, pedir a nota, fatura ou detalhe da dívida de alguém.
	8. "registrar_venda_manual": Para quando a chefe ditar uma venda que ela fez presencialmente (ou pelo zap) e pedir para anotar na conta/fiado de um cliente.
	9. "cancelar_venda_cliente": Para quando a chefe pedir para cancelar, anular ou CORRIGIR a venda de algum cliente.

	Regras para "cancelar_venda_cliente":
	- Retorne "nome_cliente" (apenas o nome da pessoa).
	- Retorne "resposta_amigavel" avisando que cancelou.
	- REGRA DE CORREÇÃO: Se a chefe pedir para "corrigir" ou "trocar" itens de uma venda, você NÃO pode fazer a troca no mesmo JSON. Você DEVE obrigatoriamente usar a ação "cancelar_venda_cliente" e na sua "resposta_amigavel" dizer: "Cancelei o pedido anterior inteiro pra não dar confusão! Agora pode me ditar o pedido correto do zero, por favor?"

	Regras para "registrar_venda_manual":
	- Retorne "nome_cliente" (o nome da pessoa).
	- Retorne "pedido" (resumo em texto do que foi comprado).
	- Retorne "valor_total" (o valor final da compra em número). Se ela não disser o valor, calcule usando os preços do ESTOQUE ATUAL.
	- Retorne "itens_vendidos" (lista EXATA dos itens para dar baixa no estoque, igual ao modo cliente. Se ela não especificar as quantidades, deixe []).
	- Retorne "resposta_amigavel" confirmando a ação.

	Regras para "consultar_extrato_cliente":
	- Retorne "nome_cliente" (apenas o nome da pessoa que a chefe quer ver a dívida).

	Regras para "consultar_pedidos":
	- Não precisa retornar nenhum outro dado além da "acao": "consultar_pedidos". O sistema vai gerar o relatório automaticamente.

	Regras para "confirmar_encomenda":
	- Retorne "nome_cliente" (apenas o nome da pessoa).
	- Retorne "valor_total" (apenas o número do valor final da encomenda).
	- Retorne "resposta_amigavel" confirmando.

	Regras para "conversar":
	- Se a chefe perguntar sobre o estoque, use a ação "conversar" e escreva na "resposta_amigavel" a lista de produtos baseada no ESTOQUE ATUAL fornecido no prompt.
	
	Regras para "atualizar_estoque":
	- Retorne uma chave "itens_estoque" contendo uma lista.
	- Cada item da lista deve ter: "item" (nome), "quantidade" (número) e "preco" (se não disser, coloque 0).
	
	Regras para "atualizar_pagamento":
	- Retorne "nome_cliente" (apenas o nome da pessoa).
	- Retorne "valor_pago" (apenas o número do valor que ela disse que o cliente pagou. Se ela não disser o valor, assuma que foi o valor total da dívida daquele pedido).
	- Retorne "resposta_amigavel" confirmando.

	Regras para "registrar_financa":
	- "categoria_aba": "Financas_Empresa" ou "Financas_Pessoal".
	- "tipo": "Entrada" ou "Saida".
	- "descricao": Resumo do gasto.
	- "valor": Apenas o número (ex: 45.50).
	- "resposta_amigavel": Confirmação amigável.
	"""
)

def conectar_planilha():
	"""
	Lê o arquivo credenciais.json e conecta no Google Sheets
	"""
	escopos = [
		'https://www.googleapis.com/auth/spreadsheets',
		'https://www.googleapis.com/auth/drive'
	]
	
	try:
		credenciais = ServiceAccountCredentials.from_json_keyfile_name('credenciais.json', escopos)
		cliente = gspread.authorize(credenciais)
		planilha = cliente.open_by_key(PLANILHA_ID)
		print("✅ Conectado ao Google Sheets com sucesso!")
		return planilha
	except Exception as e:
		print(f"❌ Erro ao conectar na planilha: {e}")
		return None

# Variável global para manter a planilha conectada
planilha_db = conectar_planilha()

# --- TRAVA DE CONCORRÊNCIA (MUTEX) ---
trava_planilha = RLock()

def obter_estoque_atual():
	"""Lê a aba Estoque e retorna uma lista em texto do que está disponível."""
	try:
		aba_estoque = planilha_db.worksheet("Estoque")
		# get_all_records() exige que a primeira linha da planilha tenha os cabeçalhos:
		# Item | Quantidade_Disponivel | Preco_Unitario
		registros = aba_estoque.get_all_records()
		
		if not registros:
			return "O estoque está vazio. Não temos produtos para vender hoje."
			
		texto_estoque = "Lista de produtos disponíveis hoje:\n"
		tem_produto = False
		
		for item in registros:
			nome = item.get('Item', '')
			preco = item.get('Preco_Unitario', 0)
			
			# Tenta converter a quantidade para número (ignora linhas vazias)
			try:
				qtd = int(item.get('Quantidade_Disponivel', 0))
			except ValueError:
				qtd = 0
				
			if qtd > 0:
				texto_estoque += f"- {nome}: {qtd} unidades (R$ {preco} cada)\n"
				tem_produto = True
				
		if not tem_produto:
			return "O estoque está zerado. Tudo já foi vendido hoje."
			
		return texto_estoque
	except Exception as e:
		print(f"Erro ao ler estoque: {e}")
		return "Erro ao verificar o estoque."
	
def calcular_total_seguro(itens_pedidos):
	"""O Python assume a calculadora para evitar erros matemáticos da IA."""
	try:
		aba_estoque = planilha_db.worksheet("Estoque")
		registros = aba_estoque.get_all_records()
		
		tabela_precos = {}
		for linha in registros:
			nome = str(linha.get("Item", "")).strip().lower()
			try:
				# Limpa o "R$" e a vírgula para virar número puro
				preco_str = str(linha.get("Preco_Unitario", "0")).replace("R$", "").replace(".", "").replace(",", ".").strip()
				preco = float(preco_str)
			except ValueError:
				preco = 0.0
			tabela_precos[nome] = preco
			
		valor_final = 0.0
		for item in itens_pedidos:
			nome_item = str(item.get("item", "")).strip().lower()
			qtd = int(item.get("quantidade", 0))
			preco_unit = tabela_precos.get(nome_item, 0.0)
			valor_final += (qtd * preco_unit)
			
		return valor_final
	except Exception as e:
		print(f"❌ Erro na calculadora do Python: {e}")
		return 0.0

def registrar_venda(telefone, nome_cliente, pedido, valor, local, itens_vendidos): # <-- Recebe os itens
	"""Salva a venda na aba 'Vendas'."""
	with trava_planilha:
		try:
			aba_vendas = planilha_db.worksheet("Vendas")
			data_hora = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
			
			try:
				valor_float = float(valor)
				valor_formatado = f"R$ {valor_float:.2f}".replace('.', ',')
			except (ValueError, TypeError):
				valor_formatado = valor 
				
			status_pagamento = "Pendente ⏳"
			
			# Converte a lista do Python de volta para um texto JSON seguro
			itens_str = json.dumps(itens_vendidos, ensure_ascii=False)
			
			# Salva com a 8ª coluna oculta
			aba_vendas.append_row([data_hora, telefone, nome_cliente, pedido, valor_formatado, local, status_pagamento, itens_str])
			print(f"✅ Venda registrada com sucesso!")
			time.sleep(1)
			return True
		except Exception as e:
			print(f"❌ Erro ao registrar venda: {e}")
			return False

def solicitar_encomenda(telefone, nome_cliente, pedido, data_entrega):
	"""Registra um pedido futuro em uma aba separada para a chefe avaliar."""
	with trava_planilha:
		try:
			aba_encomendas = planilha_db.worksheet("Encomendas")
			data_hoje = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
			
			status_aprovacao = "Aguardando Aprovação 🟡"
			
			aba_encomendas.append_row([data_hoje, data_entrega, telefone, nome_cliente, pedido, status_aprovacao])
			print(f"📦 Nova solicitação de encomenda para {data_entrega} registrada!")
			time.sleep(1)
			return True
		except Exception as e:
			print(f"❌ Erro ao registrar encomenda: {e}")
			return False

def confirmar_encomenda_admin(nome_buscado, valor_final):
	"""Chefe aprova a encomenda e o bot lança o valor no Livro Caixa."""
	with trava_planilha:
		try:
			aba_encomendas = planilha_db.worksheet("Encomendas")
			telefones = aba_encomendas.col_values(3) # Coluna 3 é Telefone
			nomes = aba_encomendas.col_values(4) # Coluna 4 é Cliente
			status_col = aba_encomendas.col_values(6) # Coluna 6 é Status
			
			# Busca de baixo para cima (a mais recente)
			for i in range(len(nomes) - 1, 0, -1):
				if nome_buscado.lower() in str(nomes[i]).lower() and "Aguardando" in str(status_col[i]):
					linha_real = i + 1
					telefone_cliente = telefones[i]
					nome_planilha = nomes[i]
					
					# 1. Muda o status na aba Encomendas
					aba_encomendas.update_cell(linha_real, 6, "Confirmada ✅")
					time.sleep(1)
					
					# 2. Lança o valor no Livro Caixa (Reaproveitando o código!)
					atualizar_compra_cliente(telefone_cliente, nome_planilha, valor_final)
					
					return True, f"Feito, chefe! A encomenda de {nome_planilha} foi confirmada e o valor de R$ {valor_final} já foi pro Livro Caixa."
					
			return False, f"Chefe, não achei nenhuma encomenda pendente para '{nome_buscado}'."
		except Exception as e:
			print(f"❌ Erro ao confirmar encomenda: {e}")
			return False, "Deu erro na planilha na hora de confirmar a encomenda."

def atualizar_status_pagamento(nome_buscado):
	"""Busca a venda mais recente do cliente e muda o status para Pago."""
	with trava_planilha:
		try:
			aba_vendas = planilha_db.worksheet("Vendas")
			# Pega todos os nomes (Coluna 3) e status (Coluna 7)
			nomes_clientes = aba_vendas.col_values(3) 
			status_coluna = aba_vendas.col_values(7)
			
			# Busca de baixo para cima (pega sempre o pedido mais recente do cliente)
			for i in range(len(nomes_clientes) - 1, 0, -1): 
				if nome_buscado.lower() in str(nomes_clientes[i]).lower():
					linha_real = i + 1
					
					# Se o status estiver pendente (ou se a célula estiver vazia por algum motivo)
					if len(status_coluna) < linha_real or "Pendente" in str(status_coluna[i]):
						aba_vendas.update_cell(linha_real, 7, "Pago ✅")
						print(f"✅ Pagamento de {nome_buscado} atualizado para Pago no Sheets!")
						time.sleep(1)
						return True, f"Prontinho, chefe! Dei baixa no pagamento de {nome_buscado}."
					else:
						return False, f"Chefe, o pedido mais recente de {nome_buscado} já estava marcado como Pago."
						
			return False, f"Não achei nenhum pedido pendente para {nome_buscado} na planilha."
		except Exception as e:
			print(f"❌ Erro ao dar baixa no pagamento: {e}")
			return False, "Deu um erro na planilha na hora de dar a baixa."

def verificar_saldo_cliente(telefone):
	"""Consulta se o cliente já tem alguma dívida anotada."""
	try:
		aba_clientes = planilha_db.worksheet("Clientes")
		registros = aba_clientes.get_all_records()
		for linha in registros:
			if str(linha.get("Telefone", "")) == str(telefone):
				return str(linha.get("Saldo_Devedor", "R$ 0,00"))
		return "R$ 0,00"
	except Exception:
		return "R$ 0,00"

def atualizar_compra_cliente(telefone, nome, valor_compra):
	"""Soma a nova compra na ficha do cliente no Livro Caixa."""
	with trava_planilha:
		try:
			aba_clientes = planilha_db.worksheet("Clientes")
			registros = aba_clientes.get_all_records()
			
			for i, linha in enumerate(registros):
				if str(linha.get("Telefone", "")) == str(telefone):
					linha_cliente = i + 2
					# Limpa a formatação de moeda para poder somar
					try:
						total_comp = float(str(linha.get("Total_Comprado", "0")).replace("R$", "").replace(".", "").replace(",", ".").strip() or 0)
						total_pago = float(str(linha.get("Total_Pago", "0")).replace("R$", "").replace(".", "").replace(",", ".").strip() or 0)
					except ValueError:
						total_comp = 0.0
						total_pago = 0.0
						
					novo_total_comp = total_comp + float(valor_compra)
					saldo_devedor = novo_total_comp - total_pago
					
					aba_clientes.update_cell(linha_cliente, 3, f"R$ {novo_total_comp:.2f}".replace('.', ','))
					aba_clientes.update_cell(linha_cliente, 5, f"R$ {saldo_devedor:.2f}".replace('.', ','))
					time.sleep(1)
					return True
					
			# Se o cliente for novo, cria a linha dele do zero
			valor_fmt = f"R$ {float(valor_compra):.2f}".replace('.', ',')
			aba_clientes.append_row([telefone, nome, valor_fmt, "R$ 0,00", valor_fmt])
			time.sleep(1)
			return True
		except Exception as e:
			print(f"❌ Erro ao atualizar Livro Caixa: {e}")
			return False

def registrar_pagamento_fiado(nome_buscado, valor_pago):
	"""A Chefe avisa que o cliente pagou uma parte ou o total da dívida."""
	with trava_planilha:
		try:
			aba_clientes = planilha_db.worksheet("Clientes")
			registros = aba_clientes.get_all_records()
			
			for i, linha in enumerate(registros):
				nome_planilha = str(linha.get("Nome", "")).strip().lower()
				if nome_buscado.lower() in nome_planilha:
					linha_cliente = i + 2
					try:
						total_comp = float(str(linha.get("Total_Comprado", "0")).replace("R$", "").replace(".", "").replace(",", ".").strip() or 0)
						total_pago = float(str(linha.get("Total_Pago", "0")).replace("R$", "").replace(".", "").replace(",", ".").strip() or 0)
					except ValueError:
						total_comp = 0.0
						total_pago = 0.0
						
					novo_total_pago = total_pago + float(valor_pago)
					saldo_devedor = total_comp - novo_total_pago
					
					pago_fmt = f"R$ {novo_total_pago:.2f}".replace('.', ',')
					saldo_fmt = f"R$ {saldo_devedor:.2f}".replace('.', ',')
					
					# Atualiza o Livro Caixa
					aba_clientes.update_cell(linha_cliente, 4, pago_fmt)
					aba_clientes.update_cell(linha_cliente, 5, saldo_fmt)
					time.sleep(1)
					
					# --- O GATILHO NOVO ENTRA AQUI ---
					# Se a dívida zerou (usamos <= 0.01 para evitar bugs de casas decimais do Python)
					if saldo_devedor <= 0.01: 
						try:
							aba_vendas = planilha_db.worksheet("Vendas")
							nomes_vendas = aba_vendas.col_values(3)
							status_vendas = aba_vendas.col_values(7)
							
							# Varre a aba Vendas de baixo para cima e dá baixa em TUDO que for desse cliente e estiver Pendente
							for v in range(len(nomes_vendas) - 1, 0, -1):
								if nome_buscado.lower() in str(nomes_vendas[v]).lower() and "Pendente" in str(status_vendas[v]):
									aba_vendas.update_cell(v + 1, 7, "Pago ✅")
							time.sleep(1)
						except Exception as e:
							print(f"⚠️ Aviso: Zerei a dívida, mas não consegui mudar o status na aba Vendas: {e}")
							
						return True, f"Pronto! O pagamento de R$ {valor_pago} quitou a dívida de {str(linha.get('Nome', ''))}. Saldo zerado e pedidos atualizados no relatório!"
					else:
						return True, f"Anotado! {str(linha.get('Nome', ''))} pagou R$ {valor_pago}. Ainda falta pagar {saldo_fmt}."
						
			return False, f"Não achei nenhum cliente chamado '{nome_buscado}' no nosso livro de fiados."
		except Exception as e:
			print(f"❌ Erro ao registrar fiado: {e}")
			return False, "Deu erro na planilha na hora de registrar o pagamento."

def gerar_extrato_fiado(busca, por_telefone=False):
	"""Gera um 'cupom fiscal' limpo puxando Vendas normais e Encomendas Confirmadas."""
	with trava_planilha:
		try:
			aba_clientes = planilha_db.worksheet("Clientes")
			registros_clientes = aba_clientes.get_all_records()

			saldo_total = "R$ 0,00"
			nome_cliente_real = busca
			telefone_real = busca if por_telefone else ""

			cliente_encontrado = False
			for cli in registros_clientes:
				nome_planilha = str(cli.get("Nome", ""))
				tel_planilha = str(cli.get("Telefone", ""))
				
				if (por_telefone and tel_planilha == str(busca)) or (not por_telefone and str(busca).lower() in nome_planilha.lower()):
					saldo_total = str(cli.get("Saldo_Devedor", "R$ 0,00"))
					nome_cliente_real = nome_planilha
					telefone_real = tel_planilha
					cliente_encontrado = True
					break

			if not cliente_encontrado:
				if por_telefone:
					return False, "Não achei nenhuma conta ou fiado no seu número."
				else:
					return False, f"Não achei nenhum registro de fiado para {busca}."

			# Verifica se a dívida tá zerada
			try:
				valor_saldo = float(saldo_total.replace("R$", "").replace(".", "").replace(",", ".").strip())
				if valor_saldo <= 0.01:
					if por_telefone:
						return True, "A sua conta está zerada! Não há nada pendente. ✅"
					else:
						return True, f"A conta de {nome_cliente_real} está zerada! Não há nada pendente. ✅"
			except ValueError:
				pass

			if por_telefone:
				extrato = "🧾 *SEU EXTRATO DE COMPRAS*\n\n"
			else:
				extrato = f"🧾 *EXTRATO DE COMPRAS - {nome_cliente_real}*\n\n"
				
			tem_pedidos = False

			# --- 1. BUSCA NA ABA VENDAS ---
			aba_vendas = planilha_db.worksheet("Vendas")
			dados_vendas = aba_vendas.get_all_values()

			for linha in reversed(dados_vendas[1:]):
				if len(linha) >= 7:
					data_hora = linha[0]
					data_curta = data_hora.split(" ")[0] 
					tel_venda = str(linha[1])
					valor = linha[4]
					status = str(linha[6])

					pedido_limpo = linha[3] 
					if len(linha) >= 8 and str(linha[7]).strip():
						try:
							itens_ocultos = json.loads(linha[7])
							lista_itens = [f"{item.get('quantidade', '')} {item.get('item', '')}" for item in itens_ocultos]
							if lista_itens:
								pedido_limpo = ", ".join(lista_itens)
						except json.JSONDecodeError:
							pass 

					if "Pendente" in status and tel_venda == telefone_real:
						extrato += f"▫️ {data_curta}: {pedido_limpo} -> {valor}\n"
						tem_pedidos = True

			# --- 2. BUSCA NA ABA ENCOMENDAS (NOVIDADE AQUI) ---
			try:
				aba_encomendas = planilha_db.worksheet("Encomendas")
				dados_enc = aba_encomendas.get_all_values()
				
				texto_encomendas = ""
				for linha in dados_enc[1:]:
					if len(linha) >= 6:
						data_pedido = linha[0].split(" ")[0]
						data_entrega = linha[1]
						tel_enc = str(linha[2])
						pedido_enc = linha[4]
						status_enc = str(linha[5])
						
						# Puxa só se a encomenda já tiver sido aprovada pela chefe e cobrar no saldo
						if "Confirmada" in status_enc and tel_enc == telefone_real:
							texto_encomendas += f"🎂 {data_pedido} (Entrega em: {data_entrega}) -> {pedido_enc}\n"
							tem_pedidos = True
							
				if texto_encomendas:
					extrato += "\n*Encomendas inclusas no seu saldo:*\n" + texto_encomendas
			except Exception as e:
				print(f"⚠️ Aviso: Não consegui puxar as encomendas pro extrato: {e}")

			if not tem_pedidos:
				extrato += "\n▫️ *Obs:* O saldo não está zerado, mas os itens detalhados podem já ter sido parcialmente pagos.\n"

			extrato += f"\n💰 *SALDO DEVEDOR TOTAL:* {saldo_total}"
			
			return True, extrato

		except Exception as e:
			print(f"❌ Erro ao gerar extrato: {e}")
			return False, "Deu erro na planilha na hora de puxar o extrato."

def buscar_telefone_na_agenda(nome_buscado):
	"""Lê o cache da agenda. Retorna o telefone ou uma lista de dúvidas."""
	try:
		if not os.path.exists('agenda.json'):
			return "erro", "Arquivo agenda.json não encontrado. Reinicie o Node.js."

		with open('agenda.json', 'r', encoding='utf-8') as f:
			agenda = json.load(f)

		matches = []
		# Procura todo mundo que tem o nome buscado (ignorando maiúsculas)
		for contato in agenda:
			nome_contato = str(contato.get("nome", "")).strip()
			if nome_buscado.lower() in nome_contato.lower():
				matches.append(contato)

		if len(matches) == 0:
			return "novo", "Adicionado pela Chefe"
			
		if len(matches) == 1:
			# Achou exatamente uma pessoa! Perfeito.
			return "sucesso", matches[0]["telefone"]
			
		if len(matches) > 1:
			# EITA! Tem mais de uma pessoa. Monta a lista de nomes para a Chefe desempatar.
			lista_nomes = [c["nome"] for c in matches]
			nomes_formatados = ", ".join(lista_nomes)
			return "duvida", nomes_formatados

	except Exception as e:
		print(f"Erro ao ler agenda: {e}")
		return "erro", "Adicionado pela Chefe"

def cancelar_ultimo_pedido(telefone, tipo_alvo="qualquer"):
	"""Cancela a venda ou encomenda, dependendo do que a IA identificar."""
	with trava_planilha:
		try:
			# --- 1. TENTA CANCELAR VENDA NORMAL ---
			if tipo_alvo in ["venda", "qualquer"]:
				aba_vendas = planilha_db.worksheet("Vendas")
				telefones_col = aba_vendas.col_values(2)
				status_col = aba_vendas.col_values(7)
				
				for i in range(len(telefones_col) - 1, 0, -1):
					if telefones_col[i] == telefone and "Cancelado" not in str(status_col[i]):
						linha_real = i + 1
						linha_dados = aba_vendas.row_values(linha_real)
						
						aba_vendas.update_cell(linha_real, 7, "Cancelado ❌")
						
						if len(linha_dados) >= 8:
							try:
								itens_devolvidos = json.loads(linha_dados[7])
								aba_estoque = planilha_db.worksheet("Estoque")
								registros_est = aba_estoque.get_all_records()
								
								for item in itens_devolvidos:
									for j, reg in enumerate(registros_est):
										if str(reg.get("Item", "")).lower() == str(item.get("item", "")).lower():
											qtd_atual = int(reg.get("Quantidade_Disponivel", 0) or 0)
											aba_estoque.update_cell(j + 2, 2, qtd_atual + int(item.get("quantidade", 0)))
											break
							except Exception as e:
								print(f"⚠️ Aviso: Não consegui devolver pro estoque: {e}")
								
						try:
							valor_cancelado = float(linha_dados[4].replace("R$", "").replace(".", "").replace(",", ".").strip())
							aba_clientes = planilha_db.worksheet("Clientes")
							registros_cli = aba_clientes.get_all_records()
							
							for k, cli in enumerate(registros_cli):
								if str(cli.get("Telefone", "")) == telefone:
									total_comp = float(str(cli.get("Total_Comprado", "0")).replace("R$", "").replace(".", "").replace(",", ".").strip() or 0)
									total_pago = float(str(cli.get("Total_Pago", "0")).replace("R$", "").replace(".", "").replace(",", ".").strip() or 0)
									novo_comp = max(0, total_comp - valor_cancelado)
									novo_saldo = novo_comp - total_pago
									aba_clientes.update_cell(k + 2, 3, f"R$ {novo_comp:.2f}".replace('.', ','))
									aba_clientes.update_cell(k + 2, 5, f"R$ {novo_saldo:.2f}".replace('.', ','))
									break
						except Exception as e:
							print(f"⚠️ Aviso: Não consegui abater do fiado: {e}")
							
						time.sleep(1)
						return True, "Prontinho! Cancelei o seu pedido de pronta entrega, o estoque foi devolvido e o valor retirado da sua conta."

			# --- 2. TENTA CANCELAR ENCOMENDA FUTURA ---
			if tipo_alvo in ["encomenda", "qualquer"]:
				aba_encomendas = planilha_db.worksheet("Encomendas")
				telefones_enc = aba_encomendas.col_values(3)
				status_enc = aba_encomendas.col_values(6)
				
				for i in range(len(telefones_enc) - 1, 0, -1):
					status_atual = str(status_enc[i])
					if telefones_enc[i] == telefone and "Cancelada" not in status_atual:
						linha_real = i + 1
						
						aba_encomendas.update_cell(linha_real, 6, "Cancelada ❌")
						time.sleep(1)
						
						if "Confirmada" in status_atual:
							# Aqui a gente precisaria abater do fiado também, mas por enquanto avisamos a chefe.
							return True, "Cancelei a sua encomenda para o evento! Como a chefe já tinha confirmado o valor antes, se você pagou algum sinal, por favor mande uma mensagem pra ela, tá bom?"
						else:
							return True, "Sua encomenda futura foi cancelada com sucesso!"

			return False, "Não achei nenhum pedido ou encomenda recente sua no sistema para cancelar."
		except Exception as e:
			print(f"❌ Erro ao cancelar pedido: {e}")
			return False, "Tive um probleminha no sistema para cancelar. Vou chamar a chefe!"

def cancelar_pedido_admin(nome_buscado):
	"""Cancela a última venda de um cliente específico pelo nome (usado pela Chefe)."""
	with trava_planilha:
		try:
			aba_vendas = planilha_db.worksheet("Vendas")
			nomes_col = aba_vendas.col_values(3) # Coluna 3: Cliente
			status_col = aba_vendas.col_values(7) # Coluna 7: Status
			
			# Varre de baixo para cima procurando a pessoa
			for i in range(len(nomes_col) - 1, 0, -1):
				if nome_buscado.lower() in str(nomes_col[i]).lower() and "Cancelado" not in str(status_col[i]):
					linha_real = i + 1
					linha_dados = aba_vendas.row_values(linha_real)
					
					# 1. Muda status para Cancelado
					aba_vendas.update_cell(linha_real, 7, "Cancelado ❌")
					
					# 2. Devolve para o Estoque
					if len(linha_dados) >= 8:
						try:
							itens_devolvidos = json.loads(linha_dados[7])
							aba_estoque = planilha_db.worksheet("Estoque")
							registros_est = aba_estoque.get_all_records()
							for item in itens_devolvidos:
								for j, reg in enumerate(registros_est):
									if str(reg.get("Item", "")).lower() == str(item.get("item", "")).lower():
										qtd_atual = int(reg.get("Quantidade_Disponivel", 0) or 0)
										aba_estoque.update_cell(j + 2, 2, qtd_atual + int(item.get("quantidade", 0)))
										break
						except Exception as e:
							print(f"⚠️ Aviso: Não consegui devolver pro estoque: {e}")

					# 3. Abate do Livro Caixa (procura pelo nome)
					try:
						valor_cancelado = float(linha_dados[4].replace("R$", "").replace(".", "").replace(",", ".").strip())
						aba_clientes = planilha_db.worksheet("Clientes")
						registros_cli = aba_clientes.get_all_records()
						
						for k, cli in enumerate(registros_cli):
							nome_planilha = str(cli.get("Nome", "")).lower()
							if nome_buscado.lower() in nome_planilha or nome_planilha in str(nomes_col[i]).lower():
								total_comp = float(str(cli.get("Total_Comprado", "0")).replace("R$", "").replace(".", "").replace(",", ".").strip() or 0)
								total_pago = float(str(cli.get("Total_Pago", "0")).replace("R$", "").replace(".", "").replace(",", ".").strip() or 0)
								novo_comp = max(0, total_comp - valor_cancelado)
								novo_saldo = novo_comp - total_pago
								aba_clientes.update_cell(k + 2, 3, f"R$ {novo_comp:.2f}".replace('.', ','))
								aba_clientes.update_cell(k + 2, 5, f"R$ {novo_saldo:.2f}".replace('.', ','))
								break
					except Exception as e:
						print(f"⚠️ Aviso: Não consegui abater do fiado: {e}")
						
					time.sleep(1)
					return True, f"Feito chefe! Cancelei a última venda de '{nomes_col[i]}' e o estoque/caixa foram ajustados."
					
			return False, f"Chefe, não achei nenhuma venda recente para cancelar no nome de '{nome_buscado}'."
		except Exception as e:
			print(f"❌ Erro ao cancelar como admin: {e}")
			return False, "Deu erro na planilha na hora de cancelar."

def registrar_gasto_admin(tipo, descricao, valor, categoria_aba="Financas_Empresa"):
	"""Salva despesas da empresa ou pessoais na aba correspondente."""
	with trava_planilha: # <-- Tranca a catraca
		try:
			aba_financas = planilha_db.worksheet(categoria_aba)
			data_atual = datetime.now().strftime("%d/%m/%Y")
			
			# O tipo pode ser "Entrada" ou "Saída"
			aba_financas.append_row([data_atual, tipo, descricao, valor])
			print(f"✅ Registro salvo com sucesso na aba {categoria_aba}!")
			time.sleep(1)
			return True
		except Exception as e:
			print(f"❌ Erro ao registrar finanças: {e}")
			return False

def atualizar_estoque(itens):
	"""Atualiza a aba 'Estoque'. Recebe uma lista de dicionários da IA."""
	with trava_planilha: # <-- Tranca a catraca
		try:
			aba_estoque = planilha_db.worksheet("Estoque")
			registros = aba_estoque.get_all_records()
			
			for novo_item in itens:
				nome = novo_item.get("item", "")
				qtd = novo_item.get("quantidade", 0)
				preco = novo_item.get("preco", 0)
				
				# Procura se o item já existe para não duplicar
				linha_existente = None
				for i, linha in enumerate(registros):
					if str(linha.get("Item", "")).lower() == str(nome).lower():
						linha_existente = i + 2 # +2 porque o index começa em 0 e a linha 1 é o cabeçalho
						break
				
				if linha_existente:
					# Atualiza a quantidade
					aba_estoque.update_cell(linha_existente, 2, qtd)
					# Só atualiza o preço se a chefe informar um novo, senão mantém o antigo
					if preco > 0:
						aba_estoque.update_cell(linha_existente, 3, preco)
				else:
					# Se for um item novo, adiciona no final
					aba_estoque.append_row([nome, qtd, preco])
					
			print("✅ Estoque atualizado com sucesso no Sheets!")
			time.sleep(1)
			return True
		except Exception as e:
			print(f"❌ Erro ao atualizar estoque: {e}")
			return False

def baixar_estoque(itens_vendidos):
	"""Subtrai os itens vendidos da aba 'Estoque'."""
	with trava_planilha: # <-- Tranca a catraca
		try:
			aba_estoque = planilha_db.worksheet("Estoque")
			registros = aba_estoque.get_all_records()
			
			for item_vendido in itens_vendidos:
				nome_vendido = item_vendido.get("item", "")
				qtd_vendida = item_vendido.get("quantidade", 0)
				
				for i, linha in enumerate(registros):
					# Procura o item exato na planilha
					if str(linha.get("Item", "")).lower() == str(nome_vendido).lower():
						linha_existente = i + 2 # +2 porque o cabeçalho é a linha 1 e index começa em 0
						
						# Calcula a nova quantidade
						try:
							qtd_atual = int(linha.get("Quantidade_Disponivel", 0))
						except ValueError:
							qtd_atual = 0
							
						nova_qtd = max(0, qtd_atual - qtd_vendida) # Garante que não fique negativo
						
						# Atualiza a célula de quantidade na planilha
						aba_estoque.update_cell(linha_existente, 2, nova_qtd)
						break # Já achou e atualizou, vai pro próximo item vendido
						
			print("✅ Baixa no estoque realizada com sucesso!")
			time.sleep(1) # Pausa estratégica
			return True
		except Exception as e:
			print(f"❌ Erro ao dar baixa no estoque: {e}")
			return False

def verificar_disponibilidade(itens_pedidos):
	"""Verifica se há estoque suficiente ANTES de fechar a venda."""
	try:
		aba_estoque = planilha_db.worksheet("Estoque")
		registros = aba_estoque.get_all_records()
		
		# Cria um "dicionário" (memória rápida) com o que temos na planilha
		estoque_dict = {}
		for linha in registros:
			nome = str(linha.get("Item", "")).strip().lower()
			try:
				qtd = int(linha.get("Quantidade_Disponivel", 0))
			except ValueError:
				qtd = 0
			estoque_dict[nome] = qtd
			
		# Verifica se a IA tentou vender mais do que devia
		for pedido in itens_pedidos:
			nome_pedido = str(pedido.get("item", "")).strip().lower()
			qtd_pedida = int(pedido.get("quantidade", 0))
			
			qtd_disponivel = estoque_dict.get(nome_pedido, 0)
			
			if qtd_pedida > qtd_disponivel:
				nome_bonito = str(pedido.get("item", "")).title()
				return False, f"Poxa, só temos {qtd_disponivel} unidades de {nome_bonito} no momento. Posso ajustar o seu pedido para essa quantidade?"
				
		return True, ""
	except Exception as e:
		print(f"❌ Erro ao validar disponibilidade: {e}")
		return False, "Deu um probleminha ao conferir o estoque. Pode tentar de novo?"

def relatorio_pedidos_admin():
	"""Gera um texto bonitinho com os pedidos do dia e TODAS as encomendas pendentes/confirmadas."""
	with trava_planilha:
		try:
			hoje = datetime.now().strftime("%d/%m/%Y")
			texto_relatorio = f"📋 *RESUMO DE PEDIDOS - {hoje}*\n\n"

			# --- 1. PRONTA ENTREGA DE HOJE (Aba Vendas) ---
			aba_vendas = planilha_db.worksheet("Vendas")
			dados_vendas = aba_vendas.get_all_values() 
			
			texto_relatorio += "📦 *PRONTA ENTREGA (Hoje):*\n"
			vendas_hoje = 0
			
			for linha in dados_vendas[1:]:
				if len(linha) >= 7 and hoje in str(linha[0]) and "Cancelado" not in str(linha[6]):
					nome = linha[2]
					pedido = linha[3]
					local = linha[5]
					texto_relatorio += f"▫️ *{nome}*: {pedido} ({local})\n"
					vendas_hoje += 1
					
			if vendas_hoje == 0:
				texto_relatorio += "Nenhum pedido de pronta entrega anotado hoje.\n"

			# --- 2. ENCOMENDAS (Aba Encomendas) ---
			aba_encomendas = planilha_db.worksheet("Encomendas")
			dados_enc = aba_encomendas.get_all_values()
			
			texto_relatorio += "\n🎂 *ENCOMENDAS (Pendentes e Futuras):*\n"
			enc_ativas = 0
			
			for linha in dados_enc[1:]:
				if len(linha) >= 6 and "Cancelada" not in str(linha[5]):
					data_entrega = linha[1]
					nome = linha[3]
					pedido = linha[4]
					status_encomenda = linha[5]
					
					# Mudança aqui: Mostra tudo que não foi cancelado (Aguardando ou Confirmada)
					if "Aguardando" in status_encomenda or "Confirmada" in status_encomenda:
						texto_relatorio += f"▫️ *{nome}* (Para: {data_entrega}): {pedido} - {status_encomenda}\n"
						enc_ativas += 1
						
			if enc_ativas == 0:
				texto_relatorio += "Nenhuma encomenda ativa no momento.\n"
				
			return True, texto_relatorio
		except Exception as e:
			print(f"❌ Erro ao gerar relatório: {e}")
			return False, "Chefe, deu um probleminha na hora de ler a planilha. Tente de novo!"

@app.route('/webhook', methods=['POST'])
def receber_mensagem():
	try:
		dados_completos = request.json
		
		if not dados_completos or 'data' not in dados_completos:
			return jsonify({"erro": "Dados inválidos"}), 400
			
		dados = dados_completos['data']
		mensagem = dados['message'].get('conversation', '')
		chat_id = dados['key']['remoteJid'] # Onde responder (@g.us ou @c.us)
		numero = dados['key'].get('participant', chat_id) # Quem mandou a mensagem (@c.us)
		chave_historico = f"{chat_id}_{numero}"

		print(f"👀 [DEBUG] Mensagem recebida do número: '{numero}'")

		# ==========================================
		# 🔒 TRAVA DE SEGURANÇA PARA TESTES 🔒
		# Se a mensagem não for do seu número, ignora silenciosamente
		# ==========================================
		if numero not in NUMERO_TESTE:
			print(f"🔒 [DEBUG] Bloqueado! O número recebido não bate com o NUMERO_TESTE: '{NUMERO_TESTE}'")
			return jsonify({"status": "ignorado"}), 200
		
		# --- NOVAS VARIÁVEIS DO WHATSAPP ---
		nome_enviado = dados.get('pushName')
		# Se o Node enviou o nome, usamos ele. Se não (estritamente), usamos o número.
		nome_cliente = nome_enviado if nome_enviado else numero.split('@')[0]
		contexto_grupo = dados.get('groupContext', {})
		is_group = contexto_grupo.get('isGroup', False)
		nome_grupo = contexto_grupo.get('groupName', 'Privado')
		mensagem = dados['message'].get('conversation', '')
		media_info = dados.get('media', {})
		media_data = media_info.get('data')
		media_mime = media_info.get('mimeType')
		
		# --- CONSTRUÇÃO DO CONTEXTO (MEMÓRIA E TEMPO) ---
		info_tempo = obter_contexto_data()
		onde_estamos = f"Estamos conversando no grupo '{nome_grupo}'." if is_group else "Estamos em uma conversa no Privado."
		
		# Inicia a memória do cliente se ele for novo
		if chave_historico not in historico_conversas:
			historico_conversas[chave_historico] = []
			
		# Define o que vai ser escrito no histórico
		texto_historico = mensagem if mensagem else f"[Mídia enviada: {media_mime}]"
		
		# Adiciona a mensagem atual ao histórico ANTES de salvar
		historico_conversas[chave_historico].append(f"{nome_cliente}: {texto_historico}")
			
		# Limita a memória a 20 mensagens para não pesar o arquivo
		historico_conversas[chave_historico] = historico_conversas[chave_historico][-20:]
		salvar_historico() # <-- Salva no arquivo imediatamente

		# Pega apenas as últimas 5 mensagens para não estourar o limite de leitura
		contexto_completo = "\n".join(historico_conversas[chave_historico][-5:])
		
		print(f"\n--- Nova Mensagem de {nome_cliente} ({numero}) ---")
		print(f"Local: {nome_grupo} | Texto: {mensagem}")
		
		resposta_para_whatsapp = ""
		notificacao_para_admin = ""
		resposta_privada = ""
		
		# --- MODO CHEFE (ADMINISTRADOR) ---
		if numero == NUMERO_ADMIN or chat_id == ID_GRUPO_ADMIN:
			print("👑 Processando comando da chefe...")
			# Puxamos o estoque da planilha para a chefe também não ficar cega
			estoque_hoje = obter_estoque_atual()
			
			prompt_chefe = f"""
			{info_tempo}
			ESTOQUE ATUAL NA PLANILHA:
			{estoque_hoje}

			Histórico da conversa:
			{contexto_completo}
			
			Chefe diz: {mensagem}
			"""

			conteudo_ia_chefe = [prompt_chefe]
			
			if media_data and media_mime:
				conteudo_ia_chefe.append({
					"mime_type": media_mime,
					"data": media_data
				})
				
			resposta_ia = modelo_admin.generate_content(conteudo_ia_chefe)
			
			try:
				dados_extraidos = json.loads(resposta_ia.text.strip())
				
				if dados_extraidos.get("acao") == "registrar_financa":
					sucesso = registrar_gasto_admin(
						tipo=dados_extraidos["tipo"],
						descricao=dados_extraidos["descricao"],
						valor=dados_extraidos["valor"],
						categoria_aba=dados_extraidos["categoria_aba"]
					)
					resposta_para_whatsapp = dados_extraidos["resposta_amigavel"] if sucesso else "Chefe, entendi o gasto, mas a planilha não quis salvar. Tente de novo!"
					
				elif dados_extraidos.get("acao") == "atualizar_estoque":
					itens = dados_extraidos.get("itens_estoque", [])
					sucesso = atualizar_estoque(itens)
					resposta_para_whatsapp = dados_extraidos["resposta_amigavel"] if sucesso else "Chefe, deu um problema ao salvar os itens no estoque. Tente de novo!"

				elif dados_extraidos.get("acao") == "atualizar_pagamento":
					cliente_pagou = dados_extraidos.get("nome_cliente", "")
					valor_pago = dados_extraidos.get("valor_pago", 0)
					
					if cliente_pagou:
						if valor_pago > 0:
							# Lida com o pagamento no Livro Caixa (Fiado/Parcial)
							sucesso, msg_retorno = registrar_pagamento_fiado(cliente_pagou, valor_pago)
						else:
							# Lida com a baixa simples (como fizemos antes) se não houver valor
							sucesso, msg_retorno = atualizar_status_pagamento(cliente_pagou)
							
						resposta_para_whatsapp = msg_retorno
					else:
						resposta_para_whatsapp = "Chefe, não entendi de quem foi o Pix. Pode repetir?"

				elif dados_extraidos.get("acao") == "confirmar_encomenda":
					cliente_alvo = dados_extraidos.get("nome_cliente", "")
					valor_encomenda = dados_extraidos.get("valor_total", 0)
					
					if cliente_alvo and valor_encomenda > 0:
						sucesso, msg_retorno = confirmar_encomenda_admin(cliente_alvo, valor_encomenda)
						resposta_para_whatsapp = msg_retorno
					else:
						resposta_para_whatsapp = "Chefe, faltou me dizer o nome do cliente ou o valor da encomenda pra eu confirmar. Pode repetir?"

				elif dados_extraidos.get("acao") == "consultar_pedidos":
					sucesso, relatorio = relatorio_pedidos_admin()
					resposta_para_whatsapp = relatorio

				elif dados_extraidos.get("acao") == "consultar_extrato_cliente":
					cliente_alvo = dados_extraidos.get("nome_cliente", "")
					if cliente_alvo:
						sucesso, extrato = gerar_extrato_fiado(cliente_alvo, por_telefone=False)
						resposta_para_whatsapp = extrato
					else:
						resposta_para_whatsapp = "Chefe, de quem você quer ver o extrato? Faltou o nome!"

				elif dados_extraidos.get("acao") == "registrar_venda_manual":
					cliente_alvo = dados_extraidos.get("nome_cliente", "")
					pedido_texto = dados_extraidos.get("pedido", "")
					itens = dados_extraidos.get("itens_vendidos", [])
					
					# --- A MATEMÁTICA AGORA É DO PYTHON ---
					# Se tiver itens listados, o Python calcula. Se não, usa o da IA de fallback.
					valor_venda = calcular_total_seguro(itens) if itens else float(dados_extraidos.get("valor_total", 0))
					
					if cliente_alvo and valor_venda > 0:
						status_busca, resultado_busca = buscar_telefone_na_agenda(cliente_alvo)
						
						if status_busca == "duvida":
							resposta_para_whatsapp = f"Chefe, segurei a venda porque achei várias pessoas com esse nome: *{resultado_busca}*.\n\nPode mandar o pedido de novo falando o nome inteiro da pessoa certa?"
						else:
							tel_cliente = resultado_busca
							
							sucesso_venda = registrar_venda(
								telefone=tel_cliente,
								nome_cliente=cliente_alvo,
								pedido=pedido_texto,
								valor=valor_venda, # <-- USA O VALOR DO PYTHON
								local="Balcão/Presencial",
								itens_vendidos=itens
							)
							
							if itens:
								baixar_estoque(itens)
								
							atualizar_compra_cliente(tel_cliente, cliente_alvo, valor_venda)
							
							# O Python mesmo escreve a mensagem da chefe, garantindo o valor certo na tela
							resposta_para_whatsapp = f"Prontinho, chefe! Venda registrada no valor exato de R$ {valor_venda:.2f} e o estoque foi atualizado."
					else:
						resposta_para_whatsapp = "Chefe, não entendi direito o nome do cliente ou o valor final. Pode repetir?"

				elif dados_extraidos.get("acao") == "cancelar_venda_cliente":
					cliente_alvo = dados_extraidos.get("nome_cliente", "")
					if cliente_alvo:
						sucesso, msg_retorno = cancelar_pedido_admin(cliente_alvo)
						resposta_para_whatsapp = dados_extraidos.get("resposta_amigavel", msg_retorno) if sucesso else msg_retorno
					else:
						resposta_para_whatsapp = "Chefe, de quem você quer cancelar a venda? Faltou o nome!"

				else:
					resposta_para_whatsapp = dados_extraidos.get("resposta_amigavel", "Anotado!")
					
			except json.JSONDecodeError:
				resposta_para_whatsapp = "Chefe, me confundi aqui. Pode falar de novo de um jeito mais simples?"
				
		# --- MODO CLIENTE (VENDAS) ---
		else:
			print("👤 Processando pedido de cliente...")
			
			estoque_hoje = obter_estoque_atual()
			saldo_atual_cliente = verificar_saldo_cliente(numero) 
			loja_aberta = verificar_loja_aberta()
			status_loja = "ABERTO" if loja_aberta else f"FECHADO (Nosso horário é das {HORA_ABRE}h às {HORA_FECHA}h)"
			
			prompt_venda = f"""
			{info_tempo}
			{onde_estamos}
			Nome do contato no WhatsApp: {nome_cliente}.
			Saldo Devedor Anterior (Fiado): {saldo_atual_cliente}.
			STATUS DA CONFEITARIA NESTE EXATO MINUTO: {status_loja}
			
			ESTOQUE ATUALIZADO DE HOJE:
			{estoque_hoje}
			
			Histórico da conversa:
			{contexto_completo}
			
			REGRAS RIGOROSAS DE VENDAS E LOGÍSTICA:
			1. NUNCA venda ou ofereça um produto que não está na lista de ESTOQUE ATUALIZADO acima.
			2. Se o cliente pedir algo que não tem no estoque, diga educadamente que não temos esse item hoje e informe apenas o que temos.
			3. Se o ESTOQUE ATUALIZADO disser que está vazio, avise o cliente que não temos nada para hoje.
			4. Use os preços do estoque para calcular o "valor_total".
			5. ENTREGAS (USO INTERNO): Para preencher o campo "local" no JSON, saiba que APAE é Ter/Qui e Superintendência é Seg/Qua. Fora desses dias ou no privado, o local é "Retirada". IMPORTANTE: NUNCA explique ou cite esses dias e locais de entrega para o cliente na sua "resposta_amigavel". Os clientes já sabem disso. Apenas defina a variável no JSON silenciosamente ou diga que "o pedido está anotado".5. ENTREGAS (USO INTERNO): Para preencher o campo "local" no JSON, saiba que APAE é Ter/Qui e Superintendência é Seg/Qua. Fora desses dias ou no privado, o local é "Retirada". IMPORTANTE: NUNCA explique ou cite esses dias e locais de entrega para o cliente na sua "resposta_amigavel". Os clientes já sabem disso. Apenas defina a variável no JSON silenciosamente ou diga que "o pedido está anotado".
			6. IMPORTANTE: NUNCA mencione nem cobre o cliente proativamente sobre o "Saldo Devedor Anterior". Só informe esse valor se o cliente EXPLICITAMENTE perguntar sobre dívidas, saldos ou pedir para somar contas antigas.
			7. Cancelamentos/Trocas: Se o cliente quiser cancelar um lanche de AGORA, use "cancelar_pedido". Se ele disser para cancelar um BOLO DE ANIVERSÁRIO ou ENCOMENDA FUTURA, use "cancelar_encomenda". Se quiser trocar (ex: "cancela o bolo e manda um pão"), primeiro faça a ação de cancelar e pergunte se pode anotar o novo, nunca os dois juntos.
			8. ENCOMENDAS FUTURAS: Se o cliente pedir algo para um dia que NÃO SEJA HOJE, você age como um entrevistador e usa a ação "conversar" até ter todos os detalhes (Sabor, Peso/Tamanho, Data). SOMENTE com tudo em mãos, use a ação "registrar_encomenda". IMPORTANTE: Toda encomenda é EXCLUSIVAMENTE para Retirada no local. Não fazemos entrega de encomendas. Na "resposta_amigavel", avise que o pedido foi para a chefe avaliar e que ele deve vir buscar no dia.
			9. HORÁRIO DE FUNCIONAMENTO: Se o STATUS DA CONFEITARIA for "FECHADO", você é ESTRITAMENTE PROIBIDO de usar as ações "registrar_venda" ou "registrar_encomenda". Use a ação "conversar" para avisar amigavelmente que já encerramos as atividades por hoje, informe o nosso horário de funcionamento e peça para o cliente mandar mensagem amanhã.
			10. ÁUDIOS E MENSAGENS INCOMPLETAS: Se o cliente mandar uma mensagem confusa, cortada, ou que parece um áudio interrompido (ex: "ah não, pera", "hã...", "esquece", ou apenas ruídos), NUNCA tente adivinhar o pedido. Use APENAS a ação "conversar" e responda com algo como "Opa, acho que cortou! Pode repetir?" ou "Tudo bem, me avise quando decidir!".
			11. EXTRATO DE FIADO E CONFERÊNCIA: Se o cliente perguntar o que está devendo, pedir a conta, OU disser um valor e pedir para conferir se a conta dele está certa (ex: "deu 50 né?", "vê se a minha conta é isso mesmo"), use IMEDIATAMENTE a ação "consultar_meu_extrato". A sua "resposta_amigavel" pode ser apenas "Vou puxar o seu caderninho para a gente conferir!", pois o sistema anexará o extrato completo logo abaixo.
			12. ACRÉSCIMOS DE PEDIDOS: O sistema funciona como um bipe de supermercado. Se o cliente pedir "2 roscas" (mensagem 1) e depois mandar "agora mais 5 bolos" (mensagem 2), a sua ação "registrar_venda" da mensagem 2 DEVE conter APENAS os 5 bolos novos, e o "valor_total" será apenas o valor destes 5 bolos. NUNCA repita no JSON itens que você já registrou no passado. Na sua "resposta_amigavel", você pode (e deve) somar mentalmente e falar o valor acumulado do carrinho para o cliente, mas os dados JSON são EXCLUSIVOS do item recém-adicionado.
			13. FORMATAÇÃO DO MENU: Sempre que você for listar os produtos disponíveis para o cliente (seja porque ele perguntou o cardápio ou pediu algo que não tem), você DEVE formatar a "resposta_amigavel" como uma lista visual com quebras de linha (um item abaixo do outro) e usar emojis (ex: 🍰, 🥖). NUNCA escreva os itens disponíveis grudados em um texto corrido.
			14. PRIVACIDADE DE CONTATO: NUNCA chame o cliente pelo "Nome do contato no WhatsApp" na sua "resposta_amigavel". Trate o cliente de forma educada e impessoal (ex: diga apenas "Oi!", "Perfeito!", "Tudo anotado!"), pois o nome salvo na nossa agenda não deve ser exposto no chat.
			15. REGISTRO INSTANTÂNEO (BIPE DIRETO): Clientes de WhatsApp têm pressa e não gostam de burocracia. NUNCA pergunte "Posso confirmar?" ou "Algo mais?". Assim que o cliente pedir um item (ex: "quero 2 bolos"), use IMEDIATAMENTE a ação "registrar_venda". A venda já é fechada e salva na planilha na mesma hora.
			16. MENSAGENS DE "OK" OU "CONFIRMO": Se o cliente mandar mensagens como "ok", "confirme", "pode fechar", "tá bom", "só isso" ou "obrigado", e NÃO pedir NENHUM doce novo nessa frase, você DEVE usar APENAS a ação "conversar" para agradecer ou se despedir. NUNCA use "registrar_venda" em mensagens de concordância, para não duplicar o pedido anterior na planilha.
			17. AVISO DE PAGAMENTO: Se o cliente enviar um comprovante, disser que fez um Pix, ou afirmar que pagou/transferiu algum valor, use IMEDIATAMENTE a ação "informar_pagamento". Na "resposta_amigavel", agradeça e diga que o pagamento foi enviado para a chefe conferir e dar baixa.

			Gere o JSON:
			"""
			
			# Empacota o texto e a mídia (se existir) para o Gemini
			conteudo_ia = [prompt_venda]
			
			if media_data and media_mime:
				conteudo_ia.append({
					"mime_type": media_mime,
					"data": media_data
				})
				
			resposta_ia = modelo_cliente.generate_content(conteudo_ia)
			
			try:
				# --- LIMPEZA DO TEXTO DA IA (Evita o erro de JSON) ---
				texto_limpo = resposta_ia.text.strip()
				if texto_limpo.startswith('```json'):
					texto_limpo = texto_limpo[7:]
				if texto_limpo.startswith('```'):
					texto_limpo = texto_limpo[3:]
				if texto_limpo.endswith('```'):
					texto_limpo = texto_limpo[:-3]
					
				dados_extraidos = json.loads(texto_limpo.strip())
				
				if dados_extraidos.get("acao") == "registrar_venda":
					itens_vendidos = dados_extraidos.get("itens_vendidos", [])
					
					# --- A MATEMÁTICA AGORA É DO PYTHON ---
					valor_correto = calcular_total_seguro(itens_vendidos) if itens_vendidos else float(dados_extraidos.get("valor_total", 0))
					
					pode_vender = True
					msg_erro = ""
					
					if itens_vendidos:
						pode_vender, msg_erro = verificar_disponibilidade(itens_vendidos)
						
					if not pode_vender:
						resposta_para_whatsapp = msg_erro
					else:
						sucesso_venda = registrar_venda(
							telefone=numero,
							nome_cliente=dados_extraidos.get("nome_cliente", nome_cliente),
							pedido=dados_extraidos.get("pedido", ""),
							valor=valor_correto, # <-- USA O VALOR DO PYTHON AQUI
							local=dados_extraidos.get("local", ""),
							itens_vendidos=itens_vendidos
						)
						
						if itens_vendidos:
							baixar_estoque(itens_vendidos)
						
						atualizar_compra_cliente(numero, dados_extraidos.get("nome_cliente", nome_cliente), valor_correto) # <-- E AQUI
							
						resposta_para_whatsapp = dados_extraidos["resposta_amigavel"] if sucesso_venda else "Tive um probleminha para anotar no sistema, mas já aviso a chefe do seu pedido!"
						
						# --- O TOQUE DE MESTRE: SE A IA ERROU A CONTA, O PYTHON AVISA O CLIENTE ---
						valor_ai = float(dados_extraidos.get("valor_total", 0))
						if abs(valor_correto - valor_ai) > 0.1: # Se a diferença for mais de 10 centavos
							resposta_para_whatsapp += f"\n\n*(Correção automática: o valor exato dos itens é R$ {valor_correto:.2f})*"
				
				elif dados_extraidos.get("acao") == "cancelar_pedido":
					sucesso, msg_retorno = cancelar_ultimo_pedido(numero)
					# Usa a mensagem amigável da IA se for um sucesso, ou o erro técnico se falhar
					resposta_para_whatsapp = dados_extraidos.get("resposta_amigavel", msg_retorno) if sucesso else msg_retorno

				elif dados_extraidos.get("acao") == "cancelar_encomenda":
					# O bot manda o Python procurar SOMENTE na aba Encomendas
					sucesso, msg_retorno = cancelar_ultimo_pedido(numero, tipo_alvo="encomenda")
					resposta_para_whatsapp = dados_extraidos.get("resposta_amigavel", msg_retorno) if sucesso else msg_retorno
					
				elif dados_extraidos.get("acao") == "registrar_encomenda":
					data_entrega = dados_extraidos.get("data_entrega", "A combinar")
					pedido_texto = dados_extraidos.get("pedido", "")
					
					sucesso = solicitar_encomenda(
						telefone=numero,
						nome_cliente=dados_extraidos.get("nome_cliente", nome_cliente),
						pedido=pedido_texto,
						data_entrega=data_entrega
					)
					
					if sucesso:
						# Força a resposta segura para o cliente
						resposta_para_whatsapp = dados_extraidos.get("resposta_amigavel", f"Tudo anotado! Como é uma encomenda para {data_entrega}, eu vou passar os detalhes para a chefe avaliar. Ela te chama rapidinho para confirmar o valor e fechar o pedido, tá bom?")
						
						# --- O ALERTA PARA A CHEFE AQUI ---
						notificacao_para_admin = f"⚠️ *NOVA ENCOMENDA PARA APROVAR* ⚠️\n\n👤 *Cliente:* {nome_cliente}\n📅 *Para:* {data_entrega}\n📝 *Pedido:* {pedido_texto}\n\nPara confirmar, responda aqui mesmo: _'Confirma a encomenda de {nome_cliente} por X reais'_."
					else:
						resposta_para_whatsapp = "Tive um probleminha para anotar a encomenda no sistema, mas já vou chamar a chefe para te atender!"

				elif dados_extraidos.get("acao") == "consultar_meu_extrato":
					# Busca pelo número do próprio cliente, garantindo privacidade
					sucesso, extrato = gerar_extrato_fiado(numero, por_telefone=True)
					
					if is_group:
						# Se ele cometeu o deslize de pedir no grupo, o bot protege ele!
						resposta_para_whatsapp = "Te enviei o seu extrato no privado!"
						resposta_privada = f"Oi! Como você pediu lá no grupo, puxei o seu caderninho digital aqui pra gente conferir:\n\n{extrato}"
					else:
						# Se ele já está no privado, flui normalmente
						resposta_para_whatsapp = f"Claro, peguei aqui o seu caderninho digital!\n\n{extrato}"

				elif dados_extraidos.get("acao") == "informar_pagamento":
					# A IA apenas responde educadamente ao cliente
					resposta_para_whatsapp = dados_extraidos.get("resposta_amigavel", "Obrigado! Já enviei o aviso para a chefe conferir o Pix e dar baixa no seu saldo.")
					
					# E o Python dispara a notificação no grupo da Chefe!
					notificacao_para_admin = f"💸 *AVISO DE PAGAMENTO* 💸\n\nO cliente *{nome_cliente}* ({numero.split('@')[0]}) acabou de avisar que fez um pagamento/Pix.\n\nPor favor, confira a conta bancária. Se o dinheiro caiu, responda aqui mesmo:\n_'Atualizar pagamento de {nome_cliente} valor X'_"
					
				else:
					# Se for só bate-papo (acao == "conversar"), ele pega a resposta amigável e envia
					resposta_para_whatsapp = dados_extraidos.get("resposta_amigavel", "Posso te ajudar com o seu pedido?")
					
			except json.JSONDecodeError as e:
				print(f"❌ Erro crítico de JSON! A IA respondeu: {resposta_ia.text}")
				resposta_para_whatsapp = "Desculpe, não entendi direito. Pode repetir seu pedido?"
		
		# Salva a resposta da IA no histórico para ela lembrar do que acabou de falar
		if resposta_para_whatsapp:
			historico_conversas[chave_historico].append(f"Assistente: {resposta_para_whatsapp}")
			salvar_historico() # <-- Salva no arquivo imediatamente
			
		print(f"Mensagem que seria enviada: {resposta_para_whatsapp}")
		return jsonify({
			"status": "processado", 
			"resposta": resposta_para_whatsapp, 
			"resposta_privada": resposta_privada,
			"notificacao_admin": notificacao_para_admin
		}), 200
		
	except Exception as e:
		print(f"Erro no webhook: {e}")
		return jsonify({"erro": "Erro interno"}), 500

@app.route('/estoque_automatico', methods=['GET'])
def estoque_automatico():
	"""Rota simples para o Node.js buscar o texto do cardápio pronto."""
	cardapio = obter_estoque_atual()
	msg_completa = f"🌟 *CARDÁPIO DE HOJE* 🌟\n\n{cardapio}\n\nFicou com vontade? É só me pedir por aqui! 😋"
	return jsonify({"cardapio": msg_completa}), 200

if __name__ == '__main__':
	print("Servidor rodando...")
	app.run(port=5000, debug=True)