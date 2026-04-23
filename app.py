from datetime import datetime, timedelta
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
from googleapiclient.discovery import build
from unidecode import unidecode

app = Flask(__name__)

load_dotenv()

# Configurações globais
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
PLANILHA_ID = os.getenv("PLANILHA_ID")

genai.configure(api_key=GEMINI_API_KEY)

try:
	locale.setlocale(locale.LC_ALL, 'pt_BR.UTF-8')
except:
	pass

# --- SISTEMA DE MEMÓRIA PERSISTENTE ---
ARQUIVO_HISTORICO = 'historico_conversas.json'

def carregar_historico():
	if os.path.exists(ARQUIVO_HISTORICO):
		try:
			with open(ARQUIVO_HISTORICO, 'r', encoding='utf-8') as f:
				return json.load(f)
		except Exception as e:
			print(f"⚠️ Erro ao ler histórico: {e}")
	return {}

def salvar_historico():
	try:
		with open(ARQUIVO_HISTORICO, 'w', encoding='utf-8') as f:
			json.dump(historico_conversas, f, ensure_ascii=False, indent=4)
	except Exception as e:
		print(f"❌ Erro ao salvar histórico: {e}")

historico_conversas = carregar_historico()

def obter_contexto_data():
	agora = datetime.now()
	dia_semana = agora.strftime("%A")
	data_formatada = agora.strftime("%d/%m/%Y")
	return f"Hoje é {dia_semana}, dia {data_formatada}."

# --- CONFIGURAÇÕES DE TESTE E ADMIN ---
NUMERO_TESTE = os.getenv("NUMERO_TESTE")
NUMERO_ADMIN = "000"
ID_GRUPO_ADMIN = os.getenv("ID_GRUPO_ADMIN") 

HORA_ABRE = 8
HORA_FECHA = 18
MODO_CORUJA_TESTE = True

def verificar_loja_aberta():
	if MODO_CORUJA_TESTE:
		return True
	hora_atual = datetime.now().hour
	return HORA_ABRE <= hora_atual < HORA_FECHA

def ler_status_loja():
	if os.path.exists('status_loja.txt'):
		with open('status_loja.txt', 'r', encoding='utf-8') as f:
			return f.read().strip().upper()
	return "ABERTO" # Padrão se o arquivo não existir

def salvar_status_loja(novo_status):
	with open('status_loja.txt', 'w', encoding='utf-8') as f:
		f.write(novo_status.upper())

# --- MENTES DA IA ATUALIZADAS ---
modelo_cliente = genai.GenerativeModel(
	'gemini-3.1-flash-lite-preview',
	system_instruction="""Você é o assistente de vendas de uma confeiteira.
	Sua função é atender o cliente, anotar o pedido e retornar EXCLUSIVAMENTE um objeto JSON válido.
	
	Regras para o JSON:
	- "acao": "registrar_venda" (se o pedido foi confirmado), "conversar", "cancelar_pedido", "cancelar_encomenda", "registrar_encomenda", "consultar_meu_extrato", "informar_pagamento" ou "ignorar".
	- "data_entrega": SE a ação for "registrar_encomenda", extraia a data. REGRA CRÍTICA DO CALENDÁRIO: NUNCA retorne palavras relativas ("amanhã", "segunda-feira", "semana que vem"). Use a 'data de hoje' fornecida no contexto para CALCULAR a data absoluta e retorne SEMPRE no formato DD/MM/AAAA.
	- "pedido": Resumo em texto do que foi pedido.
	- "itens_vendidos": Lista EXATA dos itens e quantidades pedidas. Ex: [{"item": "Rosca", "quantidade": 2}]. Serve apenas para a calculadora de preços e histórico da nota.
	- "valor_total": A soma calculada.
	- "local": "APAE", "Superintendência" ou "Retirada".
	- "resposta_amigavel": A mensagem de texto para o cliente.
	"""
)

modelo_admin = genai.GenerativeModel(
	'gemini-3.1-flash-lite-preview',
	system_instruction="""Você é o assistente financeiro e de estoque da chefe. 
	Você NÃO conversa normalmente. Retorne EXCLUSIVAMENTE um objeto JSON válido.
	
	Ações possíveis ("acao"):
	1. "registrar_financa": Para gastos, contas ou compras. Use também quando a chefe enviar a FOTO ou o PDF de um boleto/fatura. Aja como um leitor de documentos: extraia o nome da empresa (descricao), o valor a pagar (apenas números) e a data de vencimento.
	2. "atualizar_estoque": Para quando ela mudar o cardápio (adicionar, remover ou dizer que acabou).
	3. "atualizar_pagamento": Quando o cliente pagou.
	4. "conversar": Bate-papo normal ou dúvida dela.
	5. "confirmar_encomenda": Aprovar pedido futuro.
	6. "consultar_pedidos": Resumo das vendas do dia.
	7. "consultar_extrato_cliente": Fatura de um cliente.
	8. "registrar_venda_manual": Chefe ditou uma venda.
	9. "cancelar_venda_cliente": Chefe pediu para cancelar venda.
	10. "listar_devedores": Para quando a chefe perguntar "quem tá me devendo?", "lista de fiado", ou pedir o nome dos devedores.
	11. "alterar_status_loja": Para mudar o funcionamento. Se a chefe disser "estou saindo pra entrega", retorne "novo_status": "EM_ROTA". Se disser "voltei", "tô na loja" ou "abriu", retorne "novo_status": "ABERTO". Se disser "fechou", "hoje não vou fazer nada", "vou tirar folga hoje", "não vou trabalhar" ou "encerrado", retorne "novo_status": "FECHADO".
	12. "gerar_dre_mensal": Use OBRIGATORIAMENTE quando a chefe pedir "fechamento do mês", "quanto vendemos esse mês", "resumo mensal", "lucro" ou "balanço". Extraia o campo "mes_referencia" no formato "MM/AAAA". Use a data de hoje para calcular meses passados (ex: se hoje é abril e ela pede "mês passado", envie "03/2026"). Se ela não especificar, envie o mês atual.
	13. "remover_evento_agenda": Use quando a chefe pedir para apagar, deletar, remover ou desmarcar um compromisso na agenda (ex: "apaga o lembrete da internet", "desmarca o médico dia 20"). Extraia o "titulo" e a "data_vencimento".
	14. "agendar_compromisso": Use para remédios, consultas ou visitas. Extraia uma lista chamada "eventos" contendo objetos com: "titulo", "data_vencimento" (DD/MM/AAAA) e "hora_inicio" (HH:MM). Se for um remédio de 8 em 8 horas, calcule e gere TODOS os horários individuais até a data final mencionada.
	15. "anotar_lembrete_geral": Use para coisas que ela precisa lembrar, mas que não têm data/hora certa (ex: "comprar sabão", "chamar o eletricista"). Extraia apenas o campo tarefa.
	16. "importar_fiados_lote": Use quando a chefe mandar uma lista (por texto, áudio ou foto) de fiados antigos, OU quando ela pedir para adicionar uma "notinha" isolada de um cliente (ex: "adicione a notinha de Fulano no valor X"). Extraia os dados para uma lista chamada "lista_fiados".
	17. "analisar_compra_pessoal": Use quando a chefe disser que quer comprar algo pessoal (ex: "quero comprar uma blusa de 100", "tô pensando em comprar um sapato de 300"). Extraia o "item_desejado" e o "valor_item" (apenas números).
	18. "processar_nota_fiscal": Use EXCLUSIVAMENTE quando a chefe enviar a foto de uma nota fiscal ou cupom de supermercado.
	
	Regras para "processar_nota_fiscal":
	- Extraia o "supermercado" (nome do local).
	- OBRIGATÓRIO: Analise item por item. Ingredientes e embalagens vão para "itens_empresa". Itens de higiene, carnes, petiscos e uso doméstico vão para "itens_pessoais".
	- REGRA DE EXCEÇÃO (A PALAVRA DA CHEFE): Se a chefe enviar um texto ou áudio junto com a foto dando instruções (ex: "o leite dessa nota é pra casa", "metade do valor da farinha é pessoal"), a ordem dela é ABSOLUTA e anula a separação automática. Divida os valores exatamente como ela mandar.
	- Calcule "valor_empresa" e "valor_pessoal" (apenas números).
	- Na "resposta_amigavel", liste como você dividiu a conta de forma clara.

	Regras para "importar_fiados_lote":
	- Retorne a ação e crie a chave "lista_fiados" contendo um array de objetos.
	- Cada objeto deve ter "nome_cliente" e o "valor_total" (apenas números).
	- Exemplo: {"acao": "importar_fiados_lote", "lista_fiados": [{"nome_cliente": "Dona Maria", "valor_total": 45.50}]}

	Regras para "registrar_financa": 
	- Retorne obrigatoriamente "tipo" (ex: Saída, Gasto, Conta), "descricao" (o que está sendo pago), "valor" (apenas números) e "categoria_aba" (padrão: "Financas_Empresa").
	- FORMATO OBRIGATÓRIO DE DATA: Se a chefe mencionar uma data futura para pagar a conta, você DEVE criar a chave "data_vencimento" no JSON contendo a data calculada no formato exato "DD/MM/AAAA". 
	- Exemplo de JSON perfeito: {"acao": "registrar_financa", "tipo": "Conta", "descricao": "Internet", "valor": 80, "categoria_aba": "Financas_Empresa", "data_vencimento": "22/04/2026", "resposta_amigavel": "Anotado!"}
	
	Regras para "listar_devedores":
	- Retorne apenas a "acao": "listar_devedores". O sistema monta a lista.

	Regras para "atualizar_estoque":
	- Retorne uma chave "itens_estoque" contendo uma lista.
	- Cada item deve ter: "item" (nome), "disponivel" (booleano: true se ela disse que tem/fez, false se ela disse que acabou/não tem hoje) e "preco" (se ela informar, senão 0).
	- REGRA DE ATUALIZAÇÃO: Envie no JSON APENAS os itens que a chefe citou. Se ela disser "Acabou a rosca", envie APENAS a rosca com "disponivel": false. 
	- REGRA DE OURO DO "ACABOU TUDO": Use a varredura completa para listar todos como 'false' EXCLUSIVAMENTE se a chefe disser de forma global "vendeu tudo hoje", "zerou o estoque inteiro", "acabou o cardápio". CUIDADO com pegadinhas: se ela disser "vendeu tudo das roscas" ou "zerou as roscas", a palavra 'tudo' se refere APENAS à rosca, então atualize SÓ a rosca.
	- REGRA DE OURO DO ESTOQUE: A planilha só atualiza o que você enviar. Se a chefe usar palavras restritivas como "Hoje SÓ tem X", "Acabou tudo, só restou Y", você DEVE OBRIGATORIAMENTE varrer o ESTOQUE ATUAL e listar X como true, e listar explicitamente TODOS os outros produtos como false no JSON. Se você não os enviar como false, eles continuarão à venda indevidamente.
	- REGRA CONTRA ITENS FANTASMAS: NUNCA crie itens genéricos como "tudo", "todos", "os bolos", "os doces". Se a chefe usar palavras generalistas (ex: "hoje tem tudo", "todos os bolos estão disponíveis"), você DEVE olhar a lista de ESTOQUE ATUAL fornecida e retornar CADA item real e individual daquela categoria com "disponivel": true. O nome do item no JSON deve bater exatamente com o nome dos produtos já existentes na planilha.

	REGRA DE LIMITAÇÃO (ANTI-ALUCINAÇÃO):
	- NUNCA ofereça, prometa ou finja processar algo que não está na sua lista de Ações possíveis.
	- AÇÃO ÚNICA: O sistema só suporta UMA "acao" por vez. Se a chefe pedir para fazer várias coisas diferentes na mesma mensagem (ex: zerar estoque E anotar fiados para clientes diferentes), ESCOLHA apenas UMA ação para processar. Na sua "resposta_amigavel", avise a chefe amigavelmente: "Chefe, anotei X. Como sou um robô, preciso que você mande os outros comandos em mensagens separadas, um por vez!"

	DIFERENCIAÇÃO IMPORTANTE:
    - "registrar_financa": Use APENAS para contas, boletos, compras de insumos ou dívidas. (Ex: conta de luz, compra de farinha).
    - "agendar_compromisso": Use para compromissos de tempo que NÃO são necessariamente um gasto financeiro imediato (Ex: dentista, médico, reunião, entrega).
    
    Regras para "agendar_compromisso":
	- Extraia "titulo", "data_vencimento" (DD/MM/AAAA), "hora_inicio" (HH:MM) e "hora_fim" (HH:MM).
	- Se a chefe disser um intervalo (ex: "das 10 às 14"), preencha ambos os campos.
	- Se a chefe disser apenas um horário (ex: "às 14h"), preencha apenas "hora_inicio" e envie "hora_fim": null.
	- Se não houver hora mencionada, envie ambos como null.

	Demais regras seguem a lógica padrão (sempre retornando resposta_amigavel, valor_total, nome_cliente quando aplicável).
	"""
)

def conectar_planilha():
	escopos = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive', 'https://www.googleapis.com/auth/calendar']
	try:
		credenciais = ServiceAccountCredentials.from_json_keyfile_name('credenciais.json', escopos)
		cliente = gspread.authorize(credenciais)
		planilha = cliente.open_by_key(PLANILHA_ID)
		print("✅ Conectado ao Google Sheets com sucesso!")
		return planilha
	except Exception as e:
		print(f"❌ Erro ao conectar na planilha: {e}")
		return None

planilha_db = conectar_planilha()
trava_planilha = RLock()
# --- SISTEMA DE CACHE DE PLANILHA (Aceleração) ---
cache_planilha = {
	"estoque": {"dados": "", "tempo": 0},
	"saldos": {}
}
TEMPO_CACHE = 30 # A memória dura 30 segundos

def obter_estoque_atual():
	global cache_planilha
	agora = time.time()
	
	# Se a informação tem menos de 30 segundos, pega direto da memória RAM (Instantâneo!)
	if (agora - cache_planilha["estoque"]["tempo"]) < TEMPO_CACHE:
		return cache_planilha["estoque"]["dados"]
		
	try:
		aba_estoque = planilha_db.worksheet("Estoque")
		registros = aba_estoque.get_all_records()
		
		if not registros:
			return "O cardápio está vazio no sistema."
			
		texto_estoque = "Lista de produtos disponíveis para hoje:\n"
		tem_produto = False
		
		for item in registros:
			nome = item.get('Item', '')
			preco_bruto = item.get('Preco_Unitario', 0)
			disponivel = str(item.get('Disponivel', '')).strip().lower()
			
			if disponivel in ['sim', '1', 'true', 'ok', 'tem']:
				try:
					if isinstance(preco_bruto, (int, float)):
						preco_num = float(preco_bruto)
					else:
						p_str = str(preco_bruto).replace("R$", "").strip()
						if "," in p_str:
							p_str = p_str.replace(".", "").replace(",", ".")
						preco_num = float(p_str)
					preco_fmt = f"{preco_num:.2f}".replace('.', ',')
				except Exception:
					preco_fmt = str(preco_bruto)
					
				texto_estoque += f"- {nome} (R$ {preco_fmt})\n"
				tem_produto = True
				
		if not tem_produto:
			resultado = "Não temos nenhum produto pronto no momento."
		else:
			resultado = texto_estoque
			
		# Salva a resposta no Cache para a próxima mensagem
		cache_planilha["estoque"]["dados"] = resultado
		cache_planilha["estoque"]["tempo"] = agora
		return resultado
		
	except Exception as e:
		print(f"Erro ao ler estoque: {e}")
		return "Erro ao verificar o cardápio."

def obter_cardapio_completo():
	try:
		aba_estoque = planilha_db.worksheet("Estoque")
		registros = aba_estoque.get_all_records()
		
		if not registros:
			return "O cardápio está vazio no sistema."
			
		texto_estoque = "CARDÁPIO COMPLETO (Todos os itens cadastrados no banco):\n"
		
		for item in registros:
			nome = item.get('Item', '')
			disp = item.get('Disponivel', '')
			texto_estoque += f"- {nome} (Status atual na planilha: {disp})\n"
			
		return texto_estoque
	except Exception as e:
		return "Erro ao ler o cardápio completo."

def verificar_disponibilidade(itens_pedidos):
	try:
		aba_estoque = planilha_db.worksheet("Estoque")
		registros = aba_estoque.get_all_records()
		
		itens_disponiveis = []
		for linha in registros:
			nome = str(linha.get("Item", "")).strip().lower()
			disp = str(linha.get("Disponivel", "")).strip().lower()
			if disp in ['sim', '1', 'true', 'ok', 'tem']:
				itens_disponiveis.append(nome)
				
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
	except Exception as e:
		return False, "Deu um probleminha ao conferir o cardápio."

def atualizar_estoque(itens):
	with trava_planilha:
		try:
			aba_estoque = planilha_db.worksheet("Estoque")
			registros = aba_estoque.get_all_records()
			
			for novo_item in itens:
				nome = novo_item.get("item", "")
				disponivel = "Sim" if novo_item.get("disponivel", True) else "Não"
				preco = novo_item.get("preco", 0)
				
				linha_existente = None
				for i, linha in enumerate(registros):
					if str(linha.get("Item", "")).lower() == str(nome).lower():
						linha_existente = i + 2
						break
				
				if linha_existente:
					aba_estoque.update_cell(linha_existente, 2, disponivel)
					if preco > 0:
						aba_estoque.update_cell(linha_existente, 3, preco)
				else:
					aba_estoque.append_row([nome, disponivel, preco])
					
			return True
		except Exception as e:
			print(f"❌ Erro ao atualizar estoque: {e}")
			return False

def listar_todos_devedores():
	with trava_planilha:
		try:
			aba_clientes = planilha_db.worksheet("Clientes")
			registros = aba_clientes.get_all_records()
			
			texto_devedores = "💸 *LISTA DE QUEM ESTÁ DEVENDO* 💸\n\n"
			tem_devedor = False
			valor_total_rua = 0.0
			
			for linha in registros:
				nome = str(linha.get("Nome", "")).strip()
				saldo_str = str(linha.get("Saldo_Devedor", "R$ 0,00"))
				
				try:
					saldo_float = float(saldo_str.replace("R$", "").replace(".", "").replace(",", ".").strip())
					if saldo_float > 0.01:
						texto_devedores += f"▫️ *{nome}*: {saldo_str}\n"
						valor_total_rua += saldo_float
						tem_devedor = True
				except ValueError:
					pass
					
			if not tem_devedor:
				return "Chefe, não tem ninguém devendo! Todo mundo com a conta em dia. 🎉"
				
			texto_devedores += f"\n💰 *Total na rua:* R$ {valor_total_rua:.2f}".replace('.', ',')
			return texto_devedores
			
		except Exception as e:
			print(f"❌ Erro ao listar devedores: {e}")
			return "Chefe, deu erro na hora de puxar a lista de devedores da planilha."

def calcular_total_seguro(itens_pedidos):
	try:
		aba_estoque = planilha_db.worksheet("Estoque")
		registros = aba_estoque.get_all_records()
		tabela_precos = {}
		for linha in registros:
			nome = str(linha.get("Item", "")).strip().lower()
			try:
				preco_bruto = linha.get("Preco_Unitario", 0)
				# Se o Google Sheets já mandar como número, só confia e usa!
				if isinstance(preco_bruto, (int, float)):
					preco = float(preco_bruto)
				else:
					# Se vier como texto sujo, limpa
					preco_str = str(preco_bruto).replace("R$", "").strip()
					if "," in preco_str:
						preco_str = preco_str.replace(".", "").replace(",", ".")
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
		return 0.0

def registrar_venda(telefone, nome_cliente, pedido, valor, local, itens_vendidos, status_pagamento="Pendente ⏳"):
	with trava_planilha:
		try:
			aba_vendas = planilha_db.worksheet("Vendas")
			data_hora = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
			try:
				valor_float = float(valor)
				valor_formatado = f"R$ {valor_float:.2f}".replace('.', ',')
			except:
				valor_formatado = valor 
			itens_str = json.dumps(itens_vendidos, ensure_ascii=False)
			aba_vendas.append_row([data_hora, telefone, nome_cliente, pedido, valor_formatado, local, status_pagamento, itens_str])
			time.sleep(1)
			return True
		except Exception as e:
			return False

def solicitar_encomenda(telefone, nome_cliente, pedido, data_entrega):
	with trava_planilha:
		try:
			aba_encomendas = planilha_db.worksheet("Encomendas")
			data_hoje = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
			aba_encomendas.append_row([data_hoje, data_entrega, telefone, nome_cliente, pedido, "Aguardando Aprovação 🟡"])
			time.sleep(1)
			return True
		except Exception as e:
			return False

def confirmar_encomenda_admin(nome_buscado, valor_final):
	with trava_planilha:
		try:
			aba_encomendas = planilha_db.worksheet("Encomendas")
			telefones = aba_encomendas.col_values(3)
			nomes = aba_encomendas.col_values(4)
			status_col = aba_encomendas.col_values(6)
			
			for i in range(len(nomes) - 1, 0, -1):
				if nome_buscado.lower() in str(nomes[i]).lower() and "Aguardando" in str(status_col[i]):
					linha_real = i + 1
					telefone_cliente = telefones[i]
					nome_planilha = nomes[i]
					aba_encomendas.update_cell(linha_real, 6, "Confirmada ✅")
					time.sleep(1)
					atualizar_compra_cliente(telefone_cliente, nome_planilha, valor_final)
					return True, f"Feito, chefe! A encomenda de {nome_planilha} foi confirmada e lançada no Livro Caixa."
			return False, f"Não achei encomenda pendente para '{nome_buscado}'."
		except Exception as e:
			return False, "Erro ao confirmar encomenda."

def atualizar_status_pagamento(nome_buscado):
	with trava_planilha:
		try:
			aba_vendas = planilha_db.worksheet("Vendas")
			nomes = aba_vendas.col_values(3) 
			status_col = aba_vendas.col_values(7)
			for i in range(len(nomes) - 1, 0, -1): 
				if nome_buscado.lower() in str(nomes[i]).lower():
					linha_real = i + 1
					if len(status_col) < linha_real or "Pendente" in str(status_col[i]):
						aba_vendas.update_cell(linha_real, 7, "Pago ✅")
						time.sleep(1)
						return True, f"Prontinho! Baixa do pagamento de {nome_buscado} concluída."
					else:
						return False, f"O pedido mais recente de {nome_buscado} já estava Pago."
			return False, f"Não achei pedido pendente para {nome_buscado}."
		except Exception as e:
			return False, "Erro ao dar baixa no pagamento."

def verificar_saldo_cliente(telefone):
	global cache_planilha
	agora = time.time()
	
	# Se já pesquisamos esse cliente nos últimos 30 segundos, retorna da RAM
	if telefone in cache_planilha["saldos"] and (agora - cache_planilha["saldos"][telefone]["tempo"]) < TEMPO_CACHE:
		return cache_planilha["saldos"][telefone]["dados"]
		
	try:
		aba_clientes = planilha_db.worksheet("Clientes")
		registros = aba_clientes.get_all_records()
		saldo_encontrado = "R$ 0,00"
		
		for linha in registros:
			if str(linha.get("Telefone", "")) == str(telefone):
				saldo_encontrado = str(linha.get("Saldo_Devedor", "R$ 0,00"))
				break
				
		# Guarda o saldo desse cliente específico no Cache
		cache_planilha["saldos"][telefone] = {"dados": saldo_encontrado, "tempo": agora}
		return saldo_encontrado
	except Exception:
		return "R$ 0,00"

def atualizar_compra_cliente(telefone, nome, valor_compra):
	with trava_planilha:
		try:
			aba_clientes = planilha_db.worksheet("Clientes")
			registros = aba_clientes.get_all_records()
			for i, linha in enumerate(registros):
				if str(linha.get("Telefone", "")) == str(telefone):
					linha_cliente = i + 2
					try:
						total_comp = float(str(linha.get("Total_Comprado", "0")).replace("R$", "").replace(".", "").replace(",", ".").strip() or 0)
						total_pago = float(str(linha.get("Total_Pago", "0")).replace("R$", "").replace(".", "").replace(",", ".").strip() or 0)
					except:
						total_comp = total_pago = 0.0
					novo_total_comp = total_comp + float(valor_compra)
					saldo_devedor = novo_total_comp - total_pago
					aba_clientes.update_cell(linha_cliente, 3, f"R$ {novo_total_comp:.2f}".replace('.', ','))
					aba_clientes.update_cell(linha_cliente, 5, f"R$ {saldo_devedor:.2f}".replace('.', ','))
					time.sleep(1)
					return True
			valor_fmt = f"R$ {float(valor_compra):.2f}".replace('.', ',')
			aba_clientes.append_row([telefone, nome, valor_fmt, "R$ 0,00", valor_fmt])
			time.sleep(1)
			return True
		except Exception as e:
			return False

def registrar_pagamento_fiado(nome_buscado, valor_pago):
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
					except:
						total_comp = total_pago = 0.0
					novo_total_pago = total_pago + float(valor_pago)
					saldo_devedor = total_comp - novo_total_pago
					pago_fmt = f"R$ {novo_total_pago:.2f}".replace('.', ',')
					saldo_fmt = f"R$ {saldo_devedor:.2f}".replace('.', ',')
					aba_clientes.update_cell(linha_cliente, 4, pago_fmt)
					aba_clientes.update_cell(linha_cliente, 5, saldo_fmt)
					time.sleep(1)
					if saldo_devedor <= 0.01: 
						try:
							aba_vendas = planilha_db.worksheet("Vendas")
							nomes_vendas = aba_vendas.col_values(3)
							status_vendas = aba_vendas.col_values(7)
							for v in range(len(nomes_vendas) - 1, 0, -1):
								if nome_buscado.lower() in str(nomes_vendas[v]).lower() and "Pendente" in str(status_vendas[v]):
									aba_vendas.update_cell(v + 1, 7, "Pago ✅")
							time.sleep(1)
						except Exception as e:
							pass
						return True, f"Pronto! Pagamento de R$ {valor_pago} quitou a dívida de {str(linha.get('Nome', ''))}. Saldo zerado!"
					else:
						return True, f"Anotado! {str(linha.get('Nome', ''))} pagou R$ {valor_pago}. Restam {saldo_fmt}."
			return False, f"Cliente '{nome_buscado}' não encontrado."
		except Exception as e:
			return False, "Erro ao registrar pagamento."

def gerar_extrato_fiado(busca, por_telefone=False):
	with trava_planilha:
		try:
			aba_clientes = planilha_db.worksheet("Clientes")
			registros = aba_clientes.get_all_records()
			saldo_total = "R$ 0,00"
			total_pago = "R$ 0,00"
			total_comprado = "R$ 0,00"
			nome_cliente_real = busca
			telefone_real = busca if por_telefone else ""
			cliente_encontrado = False
			
			for cli in registros:
				nome_planilha = str(cli.get("Nome", ""))
				tel_planilha = str(cli.get("Telefone", ""))
				if (por_telefone and tel_planilha == str(busca)) or (not por_telefone and str(busca).lower() in nome_planilha.lower()):
					saldo_total = str(cli.get("Saldo_Devedor", "R$ 0,00"))
					total_pago = str(cli.get("Total_Pago", "R$ 0,00"))
					total_comprado = str(cli.get("Total_Comprado", "R$ 0,00"))
					nome_cliente_real = nome_planilha
					telefone_real = tel_planilha
					cliente_encontrado = True
					break
					
			if not cliente_encontrado:
				return False, "Registro não encontrado."
				
			try:
				valor_saldo = float(saldo_total.replace("R$", "").replace(".", "").replace(",", ".").strip())
				if valor_saldo <= 0.01:
					return True, f"A sua conta está zerada! ✅" if por_telefone else f"A conta de {nome_cliente_real} está zerada! ✅"
			except ValueError:
				pass
				
			# Proteção de privacidade
			if por_telefone:
				extrato = "🧾 *SEU EXTRATO DE COMPRAS*\n\n"
			else:
				extrato = f"🧾 *EXTRATO - {nome_cliente_real}*\n\n"
				
			tem_pedidos = False
			aba_vendas = planilha_db.worksheet("Vendas")
			dados_vendas = aba_vendas.get_all_values()
			
			ultimas_compras = []
			
			for linha in reversed(dados_vendas[1:]):
				if len(linha) >= 7:
					if "Pendente" in str(linha[6]) and str(linha[1]) == telefone_real:
						pedido_limpo = linha[3]
						if len(linha) >= 8 and str(linha[7]).strip():
							try:
								lista = [f"{item.get('quantidade', '')} {item.get('item', '')}" for item in json.loads(linha[7])]
								if lista: pedido_limpo = ", ".join(lista)
							except: pass
						ultimas_compras.append(f"▫️ {linha[0].split(' ')[0]}: {pedido_limpo} -> {linha[4]}")
						
			if ultimas_compras:
				tem_pedidos = True
				extrato += "*Últimas movimentações pendentes:*\n"
				# Mostra apenas as 10 últimas para não poluir a tela
				for compra in ultimas_compras[:10]:
					extrato += compra + "\n"
				if len(ultimas_compras) > 10:
					extrato += f"▫️ ... e mais {len(ultimas_compras) - 10} compras antigas.\n"
					
			try:
				aba_encomendas = planilha_db.worksheet("Encomendas")
				dados_enc = aba_encomendas.get_all_values()
				texto_enc = ""
				for linha in dados_enc[1:]:
					if len(linha) >= 6 and "Confirmada" in str(linha[5]) and str(linha[2]) == telefone_real:
						texto_enc += f"🎂 {linha[0].split(' ')[0]} (Entrega: {linha[1]}) -> {linha[4]}\n"
						tem_pedidos = True
				if texto_enc: extrato += "\n*Encomendas inclusas:*\n" + texto_enc
			except: pass
			
			# --- NOVO BLOCO DE RESUMO ---
			extrato += "\n📊 *RESUMO DA CONTA:*\n"
			extrato += f"🛒 Total Comprado (Histórico): {total_comprado}\n"
			extrato += f"✅ Valor Abatido/Pago: {total_pago}\n"
			extrato += f"💰 *SALDO DEVEDOR ATUAL:* {saldo_total}"
			
			return True, extrato
		except Exception as e:
			return False, "Erro ao puxar extrato."

def buscar_telefone_na_agenda(nome_buscado):
	try:
		if not os.path.exists('agenda.json'): return "erro", "Arquivo agenda não encontrado."
		with open('agenda.json', 'r', encoding='utf-8') as f: agenda = json.load(f)
		matches = [c for c in agenda if nome_buscado.lower() in str(c.get("nome", "")).strip().lower()]
		if len(matches) == 0: return "novo", "Desconhecido"
		if len(matches) == 1: return "sucesso", matches[0]["telefone"]
		return "duvida", ", ".join([c["nome"] for c in matches])
	except Exception as e:
		return "erro", "Erro na agenda"

def cancelar_ultimo_pedido(telefone, tipo_alvo="qualquer"):
	with trava_planilha:
		try:
			if tipo_alvo in ["venda", "qualquer"]:
				aba_vendas = planilha_db.worksheet("Vendas")
				telefones = aba_vendas.col_values(2)
				status_col = aba_vendas.col_values(7)
				for i in range(len(telefones) - 1, 0, -1):
					if telefones[i] == telefone and "Cancelado" not in str(status_col[i]):
						aba_vendas.update_cell(i + 1, 7, "Cancelado ❌")
						linha_dados = aba_vendas.row_values(i + 1)
						try:
							valor_cancelado = float(linha_dados[4].replace("R$", "").replace(".", "").replace(",", ".").strip())
							aba_clientes = planilha_db.worksheet("Clientes")
							registros = aba_clientes.get_all_records()
							for k, cli in enumerate(registros):
								if str(cli.get("Telefone", "")) == telefone:
									tc = float(str(cli.get("Total_Comprado", "0")).replace("R$", "").replace(".", "").replace(",", ".").strip() or 0)
									tp = float(str(cli.get("Total_Pago", "0")).replace("R$", "").replace(".", "").replace(",", ".").strip() or 0)
									nc = max(0, tc - valor_cancelado)
									aba_clientes.update_cell(k + 2, 3, f"R$ {nc:.2f}".replace('.', ','))
									aba_clientes.update_cell(k + 2, 5, f"R$ {(nc - tp):.2f}".replace('.', ','))
									break
						except: pass
						time.sleep(1)
						return True, "Prontinho! Pedido cancelado e valor retirado da conta."
			if tipo_alvo in ["encomenda", "qualquer"]:
				aba_encomendas = planilha_db.worksheet("Encomendas")
				telefones_enc = aba_encomendas.col_values(3)
				status_enc = aba_encomendas.col_values(6)
				for i in range(len(telefones_enc) - 1, 0, -1):
					if telefones_enc[i] == telefone and "Cancelada" not in str(status_enc[i]):
						aba_encomendas.update_cell(i + 1, 6, "Cancelada ❌")
						time.sleep(1)
						if "Confirmada" in str(status_enc[i]):
							return True, "Encomenda cancelada. Fale com a chefe sobre possíveis sinais pagos."
						return True, "Sua encomenda foi cancelada!"
			return False, "Nenhum pedido recente encontrado."
		except Exception as e:
			return False, "Erro ao cancelar."

def cancelar_pedido_admin(nome_buscado):
	with trava_planilha:
		try:
			aba_vendas = planilha_db.worksheet("Vendas")
			nomes_col = aba_vendas.col_values(3)
			status_col = aba_vendas.col_values(7)
			for i in range(len(nomes_col) - 1, 0, -1):
				if nome_buscado.lower() in str(nomes_col[i]).lower() and "Cancelado" not in str(status_col[i]):
					linha_real = i + 1
					linha_dados = aba_vendas.row_values(linha_real)
					aba_vendas.update_cell(linha_real, 7, "Cancelado ❌")
					try:
						valor_cancelado = float(linha_dados[4].replace("R$", "").replace(".", "").replace(",", ".").strip())
						aba_clientes = planilha_db.worksheet("Clientes")
						registros = aba_clientes.get_all_records()
						for k, cli in enumerate(registros):
							if nome_buscado.lower() in str(cli.get("Nome", "")).lower() or str(cli.get("Nome", "")).lower() in str(nomes_col[i]).lower():
								tc = float(str(cli.get("Total_Comprado", "0")).replace("R$", "").replace(".", "").replace(",", ".").strip() or 0)
								tp = float(str(cli.get("Total_Pago", "0")).replace("R$", "").replace(".", "").replace(",", ".").strip() or 0)
								nc = max(0, tc - valor_cancelado)
								aba_clientes.update_cell(k + 2, 3, f"R$ {nc:.2f}".replace('.', ','))
								aba_clientes.update_cell(k + 2, 5, f"R$ {(nc - tp):.2f}".replace('.', ','))
								break
					except: pass
					time.sleep(1)
					return True, f"Feito! Venda de '{nomes_col[i]}' cancelada."
			return False, f"Nenhuma venda recente para '{nome_buscado}'."
		except Exception as e:
			return False, "Erro ao cancelar."

def registrar_gasto_admin(tipo, descricao, valor, categoria_aba="Financas_Empresa"):
	with trava_planilha:
		try:
			aba_financas = planilha_db.worksheet(categoria_aba)
			data_atual = datetime.now().strftime("%d/%m/%Y")
			aba_financas.append_row([data_atual, tipo, descricao, valor])
			time.sleep(1)
			return True
		except Exception as e:
			return False

def relatorio_pedidos_admin():
	with trava_planilha:
		try:
			hoje = datetime.now().strftime("%d/%m/%Y")
			texto = f"📋 *RESUMO DE PEDIDOS - {hoje}*\n\n📦 *PRONTA ENTREGA:*\n"
			aba_vendas = planilha_db.worksheet("Vendas")
			dados_vendas = aba_vendas.get_all_values() 
			
			v_hoje = 0
			faturamento_vendas = 0.0
			pedidos_agrupados = {} # Dicionário para mesclar os pedidos
			
			for l in dados_vendas[1:]:
				# Adicionamos a trava 'and "Migração" not in str(l[5])' para pular as notinhas antigas
				if len(l) >= 7 and hoje in str(l[0]) and "Cancelado" not in str(l[6]) and "Migração" not in str(l[5]):
					cliente = str(l[2]).strip()
					pedido = str(l[3]).strip()
					local = str(l[5]).strip()
					
					try:
						valor_limpo = float(str(l[4]).replace("R$", "").replace(".", "").replace(",", ".").strip())
					except ValueError:
						valor_limpo = 0.0
						
					faturamento_vendas += valor_limpo
					v_hoje += 1
					
					# Se o cliente já comprou algo hoje, soma o valor e junta o texto
					if cliente in pedidos_agrupados:
						pedidos_agrupados[cliente]["pedido"] += f" + {pedido}"
						pedidos_agrupados[cliente]["valor"] += valor_limpo
					else:
						# Se é a primeira compra do dia, cria o registro
						pedidos_agrupados[cliente] = {
							"pedido": pedido, 
							"valor": valor_limpo, 
							"local": local
						}
						
			if v_hoje == 0: 
				texto += "Nenhum pedido finalizado hoje.\n"
			else:
				# Agora varremos o dicionário mesclado para montar o texto
				for cli, dados in pedidos_agrupados.items():
					valor_fmt = f"R$ {dados['valor']:.2f}".replace('.', ',')
					texto += f"▫️ *{cli}*: {dados['pedido']} ({valor_fmt} - {dados['local']})\n"
					
				texto += f"\n💰 *Faturamento do Dia (Pronta Entrega):* R$ {faturamento_vendas:.2f}".replace('.', ',') + "\n"

			texto += "\n🎂 *ENCOMENDAS ATIVAS:*\n"
			aba_encomendas = planilha_db.worksheet("Encomendas")
			dados_enc = aba_encomendas.get_all_values()
			e_ativas = 0
			for l in dados_enc[1:]:
				if len(l) >= 6 and "Cancelada" not in str(l[5]):
					if "Aguardando" in str(l[5]) or "Confirmada" in str(l[5]):
						texto += f"▫️ *{l[3]}* (Para {l[1]}): {l[4]} - {l[5]}\n"
						e_ativas += 1
						
			if e_ativas == 0: 
				texto += "Nenhuma encomenda pendente.\n"
				
			return True, texto
			
		except Exception as e:
			print(f"Erro no relatorio: {e}")
			return False, "Erro ao gerar relatório da chefe."

def gerar_relatorio_financeiro(mes_ano=None):
	with trava_planilha:
		try:
			# Se não vier mês nenhum, usa o mês atual
			if not mes_ano:
				mes_ano = datetime.now().strftime("%m/%Y")
			
			# Transforma "04/2026" em "Abril" para o título ficar bonito
			try:
				nome_mes = datetime.strptime(mes_ano, "%m/%Y").strftime("%B").capitalize()
			except:
				nome_mes = mes_ano
			
			# 1. Puxar Vendas (Entradas)
			aba_vendas = planilha_db.worksheet("Vendas")
			dados_vendas = aba_vendas.get_all_values()
			
			total_vendido = 0.0
			total_recebido = 0.0 # Vendas já pagas
			
			for l in dados_vendas[1:]:
				# AQUI ESTÁ A MAGIA: Filtramos usando o 'mes_ano' (ex: "03/2026")
				if len(l) >= 7 and mes_ano in str(l[0]) and "Cancelado" not in str(l[6]):
					try:
						valor_limpo = str(l[4]).replace("R$", "").replace(".", "").replace(",", ".").strip()
						valor = float(valor_limpo)
						total_vendido += valor
						if "Pago" in str(l[6]):
							total_recebido += valor
					except ValueError:
						pass

			# 2. Puxar Gastos (Saídas)
			aba_financas = planilha_db.worksheet("Financas_Empresa")
			dados_financas = aba_financas.get_all_values()
			
			total_gasto = 0.0
			
			for l in dados_financas[1:]:
				if len(l) >= 4 and mes_ano in str(l[0]):
					try:
						gasto_limpo = str(l[3]).replace("R$", "").replace(".", "").replace(",", ".").strip()
						total_gasto += float(gasto_limpo)
					except ValueError:
						pass
						
			# 3. Matemática do DRE (Demonstrativo de Resultados)
			lucro_liquido = total_vendido - total_gasto
			saldo_em_caixa = total_recebido - total_gasto # Dinheiro real que tá na mão
			
			# 4. Montar o Relatório Bonito
			relatorio = f"📊 *FECHAMENTO MENSAL - {nome_mes}*\n\n"
			relatorio += f"📈 *Total Vendido:* R$ {total_vendido:.2f}\n".replace('.', ',')
			relatorio += f"✅ *Total Recebido (Pix/Dinheiro):* R$ {total_recebido:.2f}\n".replace('.', ',')
			relatorio += f"⏳ *A Receber (Fiado):* R$ {(total_vendido - total_recebido):.2f}\n\n".replace('.', ',')
			
			relatorio += f"📉 *Despesas/Insumos:* R$ {total_gasto:.2f}\n".replace('.', ',')
			relatorio += "------------------------\n"
			
			if lucro_liquido > 0:
				relatorio += f"💰 *LUCRO LÍQUIDO:* R$ {lucro_liquido:.2f} 🥳\n\n".replace('.', ',')
				
				# --- REGRA DOS POTES (10/90) ---
				caixa_empresa = lucro_liquido * 0.10
				pro_labore = lucro_liquido * 0.90
				
				relatorio += "🍯 *DIVISÃO DO LUCRO (Regra 10/90):*\n"
				relatorio += f"🏢 *Caixa da Empresa (10%):* R$ {caixa_empresa:.2f} (Para repor estoque e crescer)\n".replace('.', ',')
				relatorio += f"👩‍🍳 *Seu Pró-Labore (90%):* R$ {pro_labore:.2f} (Seu salário livre!)\n\n".replace('.', ',')
			else:
				relatorio += f"⚠️ *PREJUÍZO/EMPATE:* R$ {lucro_liquido:.2f} 🛑\n".replace('.', ',')
				relatorio += "🍯 *DIVISÃO DO LUCRO:* Sem lucro livre para divisão neste mês ainda.\n\n"
				
			relatorio += f"🏦 *Saldo Real no Caixa (Recebido - Gasto):* R$ {saldo_em_caixa:.2f}".replace('.', ',')
			
			return True, relatorio
			
		except Exception as e:
			print(f"Erro no DRE: {e}")
			return False, "Chefe, não consegui calcular o balanço. Verifique se a aba 'Financas_Empresa' está no formato correto."

def conectar_agenda():
	escopos = ['https://www.googleapis.com/auth/calendar', 'https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
	try:
		credenciais = ServiceAccountCredentials.from_json_keyfile_name('credenciais.json', escopos)
		servico = build('calendar', 'v3', credentials=credenciais)
		print("📅 Conectado ao Google Agenda com sucesso!")
		return servico
	except Exception as e:
		print(f"❌ Erro ao conectar na agenda: {e}")
		return None

agenda_service = conectar_agenda()

def criar_evento_agenda(titulo, data_entrega, descricao, hora_inicio=None, hora_fim=None):
	try:
		CALENDARIO_ID = os.getenv("CALENDARIO_ID")
		data_iso = datetime.strptime(data_entrega, "%d/%m/%Y").strftime("%Y-%m-%d")
		
		if hora_inicio:
			inicio = f"{data_iso}T{hora_inicio}:00"
			
			# Lógica Dinâmica: Se a IA enviar o horário de término, usamos. 
			# Se não enviar, mantemos o padrão de somar 1 hora ao início.
			if hora_fim:
				fim = f"{data_iso}T{hora_fim}:00"
			else:
				h_ini = int(hora_inicio.split(':')[0])
				fim = f"{data_iso}T{h_ini + 1:02d}:{hora_inicio.split(':')[1]}:00"
			
			corpo_horario = {
				'start': {'dateTime': inicio, 'timeZone': 'America/Sao_Paulo'},
				'end': {'dateTime': fim, 'timeZone': 'America/Sao_Paulo'},
			}
		else:
			# Evento de dia inteiro (para contas/gastos)
			corpo_horario = {
				'start': {'date': data_iso, 'timeZone': 'America/Sao_Paulo'},
				'end': {'date': data_iso, 'timeZone': 'America/Sao_Paulo'},
			}

		evento = {
			'summary': titulo,
			'description': descricao,
			**corpo_horario,
			'reminders': {'useDefault': True}
		}
		
		agenda_service.events().insert(calendarId=CALENDARIO_ID, body=evento).execute()
		return True
	except Exception as e:
		print(f"Erro ao criar evento: {e}")
		return False

def deletar_evento_agenda(titulo_busca, data_vencimento):
	"""Remove um evento da agenda baseado no título e na data."""
	try:
		CALENDARIO_ID = os.getenv("CALENDARIO_ID")
		
		# Define o intervalo do dia para a busca
		data_inicio = datetime.strptime(data_vencimento, "%d/%m/%Y").replace(hour=0, minute=0).isoformat() + 'Z'
		data_fim = datetime.strptime(data_vencimento, "%d/%m/%Y").replace(hour=23, minute=59).isoformat() + 'Z'
		
		# Busca os eventos daquele dia
		eventos_result = agenda_service.events().list(
			calendarId=CALENDARIO_ID, timeMin=data_inicio, timeMax=data_fim,
			singleEvents=True
		).execute()
		
		eventos = eventos_result.get('items', [])
		
		if not eventos:
			return False, "Não encontrei nenhum compromisso nessa data para apagar."

		# Procura o evento pelo título
		for ev in eventos:
			if unidecode(titulo_busca.lower()) in unidecode(ev.get('summary', '').lower()):
				agenda_service.events().delete(calendarId=CALENDARIO_ID, eventId=ev['id']).execute()
				return True, f"Pronto, chefe! O compromisso '{ev['summary']}' foi removido da sua agenda."
				
		return False, f"Achei a data, mas nenhum evento com o nome '{titulo_busca}'."
	except Exception as e:
		print(f"Erro ao deletar: {e}")
		return False, "Erro técnico ao acessar a agenda."

def listar_compromissos_dia(data_str=None):
	"""Busca todos os eventos de um dia específico na agenda da chefe."""
	try:
		# Puxamos o e-mail real da variável de ambiente
		CALENDARIO_ID = os.getenv("CALENDARIO_ID")
		
		if not data_str:
			data_str = datetime.now().strftime("%d/%m/%Y")
		
		data_inicio = datetime.strptime(data_str, "%d/%m/%Y").replace(hour=0, minute=0).isoformat() + 'Z'
		data_fim = datetime.strptime(data_str, "%d/%m/%Y").replace(hour=23, minute=59).isoformat() + 'Z'
		
		# Mudança aqui: trocamos 'primary' por CALENDARIO_ID
		eventos_result = agenda_service.events().list(
			calendarId=CALENDARIO_ID, timeMin=data_inicio, timeMax=data_fim,
			singleEvents=True, orderBy='startTime'
		).execute()
		
		eventos = eventos_result.get('items', [])
		return eventos
	except Exception as e:
		print(f"Erro ao listar agenda: {e}")
		return []

def registrar_tarefa_lista(tarefa):
	with trava_planilha:
		try:
			aba_tarefas = planilha_db.worksheet("Tarefas")
			data_hoje = datetime.now().strftime("%d/%m/%Y")
			# Status padrão 'Pendente' para ela dar baixa depois
			aba_tarefas.append_row([data_hoje, tarefa, "Pendente ⬜"])
			return True
		except Exception:
			return False

def zerar_estoque_completo():
	"""Define 'Não' para todos os itens da planilha de Estoque."""
	with trava_planilha:
		try:
			aba_estoque = planilha_db.worksheet("Estoque")
			registros = aba_estoque.get_all_records()
			
			for i, linha in enumerate(registros):
				# Coluna 2 é a coluna 'Disponivel'
				aba_estoque.update_cell(i + 2, 2, "Não")
			
			return True
		except Exception as e:
			print(f"Erro ao zerar estoque: {e}")
			return False

def calcular_preco_em_doces(item_desejado, valor_item):
	with trava_planilha:
		try:
			aba_estoque = planilha_db.worksheet("Estoque")
			registros = aba_estoque.get_all_records()
			
			produto_ref = None
			preco_ref = 0.0
			
			# Tenta achar o Bolo de Fubá como base psicológica
			for l in registros:
				if "bolo de fubá (maior)" in str(l.get("Item", "")).lower():
					try:
						preco_bruto = l.get("Preco_Unitario", 0)
						if isinstance(preco_bruto, (int, float)):
							preco_ref = float(preco_bruto)
						else:
							preco_ref = float(str(preco_bruto).replace("R$", "").replace(".", "").replace(",", ".").strip())
						produto_ref = str(l.get("Item", ""))
						break
					except: pass
					
			# Se não achar o bolo, pega o primeiro item com preço válido
			if not produto_ref:
				for l in registros:
					try:
						preco_bruto = l.get("Preco_Unitario", 0)
						if isinstance(preco_bruto, (int, float)):
							preco_teste = float(preco_bruto)
						else:
							preco_teste = float(str(preco_bruto).replace("R$", "").replace(".", "").replace(",", ".").strip())
							
						if preco_teste > 0:
							preco_ref = preco_teste
							produto_ref = str(l.get("Item", ""))
							break
					except: pass
					
			if preco_ref > 0:
				# A regra dos 20%: Faturamento total precisa ser 5x o valor desejado
				faturamento_necessario = valor_item * 5
				qtd_real = int(faturamento_necessario / preco_ref)
				
				msg = f"🤔 *Análise de Compra: {item_desejado.title()}*\n\n"
				msg += f"Chefe, esse item custa R$ {valor_item:.2f}.\n\n".replace('.', ',')
				msg += f"Pela nossa *Regra dos Potes*, para você colocar esse valor limpo no bolso sem tirar o dinheiro de repor ingredientes da empresa, a confeitaria precisa faturar R$ {faturamento_necessario:.2f}!\n\n".replace('.', ',')
				msg += f"🥵 Na prática, você vai precisar assar e vender **{qtd_real} {produto_ref}s** só para pagar isso.\n\n"
				msg += "Vale a pena o esforço ou deixamos para o mês que vem? 😅"
				
				return True, msg
			else:
				return False, "Chefe, não consegui calcular o suor porque não achei o preço dos produtos."
		except Exception as e:
			print(f"Erro no calculo de doces: {e}")
			return False, "Erro ao calcular o preço em doces."

def registrar_nota_fiscal(supermercado, valor_empresa, valor_pessoal, itens_empresa):
	with trava_planilha:
		try:
			data_atual = datetime.now().strftime("%d/%m/%Y")
			
			# 1. Salva gasto da empresa
			if valor_empresa > 0:
				aba_financas = planilha_db.worksheet("Financas_Empresa")
				aba_financas.append_row([data_atual, "Saída", f"Insumos - {supermercado}", valor_empresa])
				
			# 2. Salva gasto pessoal (O Python vai criar a linha na aba Financas_Pessoal)
			if valor_pessoal > 0:
				try:
					aba_pessoal = planilha_db.worksheet("Financas_Pessoal")
					aba_pessoal.append_row([data_atual, "Saída", f"Supermercado - {supermercado}", valor_pessoal])
				except Exception as e:
					print(f"⚠️ Aba 'Financas_Pessoal' não encontrada: {e}")
			
			# 3. Alimenta a inteligência de mercado apenas com itens da empresa
			if itens_empresa:
				aba_precos = planilha_db.worksheet("Historico_Precos")
				linhas_para_adicionar = []
				
				for item in itens_empresa:
					nome = item.get("item", "")
					qtd = item.get("quantidade", "")
					preco = item.get("preco_unitario", 0)
					if nome and preco > 0: # Evita salvar linhas vazias se a nota estiver ruim
						linhas_para_adicionar.append([data_atual, supermercado, nome, str(qtd), preco])
					
				if linhas_para_adicionar:
					# Salva tudo de uma vez para ficar rápido
					aba_precos.append_rows(linhas_para_adicionar)
					
			time.sleep(1)
			return True
		except Exception as e:
			print(f"❌ Erro ao processar nota fiscal na planilha: {e}")
			return False

@app.route('/webhook', methods=['POST'])
def receber_mensagem():
	try:
		dados_completos = request.json
		
		if not dados_completos or 'data' not in dados_completos:
			return jsonify({"erro": "Dados inválidos"}), 400
			
		dados = dados_completos['data']
		chat_id = dados['key']['remoteJid'] 
		numero = dados['key'].get('participant', chat_id) 

		print(f"👀 [DEBUG] Mensagem recebida do número: '{numero}'")

		if numero not in NUMERO_TESTE:
			print(f"🔒 [DEBUG] Bloqueado! O número recebido não bate com o NUMERO_TESTE: '{NUMERO_TESTE}'")
			return jsonify({"status": "ignorado"}), 200
		
		nome_enviado = dados.get('pushName')
		nome_cliente = nome_enviado if nome_enviado else numero.split('@')[0]
		contexto_grupo = dados.get('groupContext', {})
		is_group = contexto_grupo.get('isGroup', False)
		nome_grupo = contexto_grupo.get('groupName', 'Privado')
		mensagem = dados['message'].get('conversation', '')
		media_info = dados.get('media', {})
		media_data = media_info.get('data')
		media_mime = media_info.get('mimeType')
		
		# --- 🎭 INÍCIO DO MODO CAMUFLAGEM (MOCK) 🎭 ---
		if numero in NUMERO_TESTE and mensagem.lower().startswith("simular "):
			try:
				# Divide a string em duas partes: "Simular Nome" e "A mensagem de fato"
				partes = mensagem.split(":", 1)
				if len(partes) == 2:
					# Extrai o nome tirando a palavra "simular "
					nome_falso = partes[0].lower().replace("simular ", "").title().strip()
					
					# Substitui as variáveis originais pelas falsas
					nome_cliente = nome_falso
					# Cria um número de telefone fake único para essa pessoa
					numero = f"553800000000_{nome_falso.lower().replace(' ', '_')}@c.us" 
					# A mensagem real que vai para a IA
					mensagem = partes[1].strip()
					
					print(f"🎭 [MODO TESTE] Kauã camuflado como: {nome_cliente}")
			except Exception as e:
				print(f"Erro na camuflagem: {e}")
		# --- FIM DO MODO CAMUFLAGEM ---

		chave_historico = f"{chat_id}_{numero}"
		info_tempo = obter_contexto_data()
		onde_estamos = f"Estamos conversando no grupo '{nome_grupo}'." if is_group else "Estamos em uma conversa no Privado."
		
		if chave_historico not in historico_conversas:
			historico_conversas[chave_historico] = []
			
		texto_historico = mensagem if mensagem else f"[Mídia enviada: {media_mime}]"
		
		historico_conversas[chave_historico].append(f"{nome_cliente}: {texto_historico}")
			
		historico_conversas[chave_historico] = historico_conversas[chave_historico][-20:]
		salvar_historico() 

		contexto_completo = "\n".join(historico_conversas[chave_historico][-5:])
		
		print(f"\n--- Nova Mensagem de {nome_cliente} ({numero}) ---")
		print(f"Local: {nome_grupo} | Texto: {mensagem}")
		
		resposta_para_whatsapp = ""
		notificacao_para_admin = ""
		resposta_privada = ""
		
		# --- MODO CHEFE (ADMINISTRADOR) ---
		if numero == NUMERO_ADMIN or chat_id == ID_GRUPO_ADMIN:
			print("👑 Processando comando da chefe...")
			estoque_completo = obter_cardapio_completo()
			
			prompt_chefe = f"""
			{info_tempo}
			ESTOQUE ATUAL NA PLANILHA:
			{estoque_completo}

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
				txt_limpo = resposta_ia.text.strip()
				if txt_limpo.startswith('```json'):
					txt_limpo = txt_limpo[7:]
				if txt_limpo.startswith('```'):
					txt_limpo = txt_limpo[3:]
				if txt_limpo.endswith('```'):
					txt_limpo = txt_limpo[:-3]
					
				dados_extraidos = json.loads(txt_limpo.strip())
				
				acao = dados_extraidos.get("acao")
				
				if acao == "registrar_financa":
					# Usamos .get() com valores padrão para evitar o KeyError
					tipo_gasto = dados_extraidos.get("tipo", "Despesa")
					desc_gasto = dados_extraidos.get("descricao", "Conta")
					valor_gasto = dados_extraidos.get("valor", 0)
					aba_destino = dados_extraidos.get("categoria_aba", "Financas_Empresa")
					data_vencimento = dados_extraidos.get("data_vencimento", "")

					sucesso = registrar_gasto_admin(
						tipo=tipo_gasto,
						descricao=desc_gasto,
						valor=valor_gasto,
						categoria_aba=aba_destino
					)
					
					if sucesso:
						resposta_para_whatsapp = dados_extraidos.get("resposta_amigavel", f"Anotado, chefe! Lancei a conta de {desc_gasto} no valor de R$ {valor_gasto}.")
						
						# --- NOVA INTEGRAÇÃO: AGENDA PARA CONTAS ---
						if data_vencimento:
							sucesso_agenda = criar_evento_agenda(
								titulo=f"💸 Pagar: {desc_gasto}",
								data_entrega=data_vencimento,
								descricao=f"Valor: R$ {valor_gasto}\nTipo: {tipo_gasto}"
							)
							if sucesso_agenda:
								resposta_para_whatsapp += f"\n\n📅 Também já coloquei um lembrete na sua Google Agenda para o dia {data_vencimento}!"
							else:
								resposta_para_whatsapp += f"\n\n⚠️ Tentei salvar na Agenda para o dia {data_vencimento}, mas ocorreu um erro."
					else:
						resposta_para_whatsapp = "Chefe, entendi o gasto, mas a planilha não aceitou o registro. Verifique se as colunas estão certas!"

				elif acao == "atualizar_estoque":
					itens = dados_extraidos.get("itens_estoque", [])
					sucesso = atualizar_estoque(itens)
					resposta_para_whatsapp = dados_extraidos["resposta_amigavel"] if sucesso else "Chefe, deu um problema ao salvar os itens no estoque. Tente de novo!"

				elif acao == "listar_devedores":
					resposta_para_whatsapp = listar_todos_devedores()

				elif acao == "atualizar_pagamento":
					cliente_pagou = dados_extraidos.get("nome_cliente", "")
					valor_pago = dados_extraidos.get("valor_pago", 0)
					
					if cliente_pagou:
						if valor_pago > 0:
							sucesso, msg_retorno = registrar_pagamento_fiado(cliente_pagou, valor_pago)
						else:
							sucesso, msg_retorno = atualizar_status_pagamento(cliente_pagou)
							
						resposta_para_whatsapp = msg_retorno
					else:
						resposta_para_whatsapp = "Chefe, não entendi de quem foi o Pix. Pode repetir?"

				elif acao == "confirmar_encomenda":
					cliente_alvo = dados_extraidos.get("nome_cliente", "")
					valor_final = dados_extraidos.get("valor_total", 0)
					
					if cliente_alvo and valor_final > 0:
						sucesso, msg = confirmar_encomenda_admin(cliente_alvo, valor_final)
						if sucesso:
							# --- INTEGRAÇÃO COM A AGENDA ---
							# Aqui pegamos os detalhes do pedido que já estão na planilha
							sucesso_agenda = criar_evento_agenda(
								titulo=f"🎂 Encomenda: {cliente_alvo}",
								data_entrega=dados_extraidos.get("data_entrega", ""), # A IA extrai a data absoluta
								descricao=f"Valor: R$ {valor_final}\nPedido: {dados_extraidos.get('pedido', '')}"
							)
							if sucesso_agenda:
								resposta_para_whatsapp = msg + "\n\n📅 Também já salvei na sua Google Agenda com um lembrete!"
							else:
								resposta_para_whatsapp = msg + "\n\n⚠️ Avisei na planilha, mas tive um erro ao acessar sua Agenda."
						else:
							resposta_para_whatsapp = msg

				elif acao == "consultar_pedidos":
					sucesso, relatorio = relatorio_pedidos_admin()
					resposta_para_whatsapp = relatorio

				elif acao == "consultar_extrato_cliente":
					cliente_alvo = dados_extraidos.get("nome_cliente", "")
					if cliente_alvo:
						sucesso, extrato = gerar_extrato_fiado(cliente_alvo, por_telefone=False)
						resposta_para_whatsapp = extrato
					else:
						resposta_para_whatsapp = "Chefe, de quem você quer ver o extrato? Faltou o nome!"

				elif acao == "registrar_venda_manual":
					cliente_alvo = dados_extraidos.get("nome_cliente", "")
					pedido_texto = dados_extraidos.get("pedido", "")
					itens = dados_extraidos.get("itens_vendidos", [])
					
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
								valor=valor_venda, 
								local="Balcão/Presencial",
								itens_vendidos=itens
							)
							
							if sucesso_venda:
								atualizar_compra_cliente(tel_cliente, cliente_alvo, valor_venda)
								resposta_para_whatsapp = dados_extraidos.get("resposta_amigavel", f"Prontinho, chefe! Venda lançada para {cliente_alvo} no valor de R$ {valor_venda:.2f}.")
							else:
								resposta_para_whatsapp = "Chefe, a planilha deu um erro e recusou a gravação. Tente de novo!"
					else:
						resposta_para_whatsapp = "Chefe, não entendi direito o nome do cliente ou o valor final. Pode repetir?"

				elif acao == "cancelar_venda_cliente":
					cliente_alvo = dados_extraidos.get("nome_cliente", "")
					if cliente_alvo:
						sucesso, msg_retorno = cancelar_pedido_admin(cliente_alvo)
						resposta_para_whatsapp = dados_extraidos.get("resposta_amigavel", msg_retorno) if sucesso else msg_retorno
					else:
						resposta_para_whatsapp = "Chefe, de quem você quer cancelar a venda? Faltou o nome!"

				elif acao == "alterar_status_loja":
					novo_status = dados_extraidos.get("novo_status", "ABERTO")
					salvar_status_loja(novo_status)
					resposta_para_whatsapp = dados_extraidos.get("resposta_amigavel", f"Feito, chefe! A confeitaria agora está no modo: {novo_status}")

				elif acao == "gerar_dre_mensal":
					mes_ref = dados_extraidos.get("mes_referencia")
					sucesso, dre = gerar_relatorio_financeiro(mes_ref)
					resposta_para_whatsapp = dre

				elif acao == "remover_evento_agenda":
					titulo_alvo = dados_extraidos.get("titulo", "")
					data_alvo = dados_extraidos.get("data_vencimento", "")
					
					if titulo_alvo and data_alvo:
						sucesso, msg = deletar_evento_agenda(titulo_alvo, data_alvo)
						resposta_para_whatsapp = msg
					else:
						resposta_para_whatsapp = "Chefe, preciso que você me fale o nome do compromisso e a data para eu conseguir apagar da agenda."

				elif acao == "agendar_compromisso":
					eventos_lista = dados_extraidos.get("eventos", [])
					if not eventos_lista:
						# Fallback para o formato antigo de um único evento
						eventos_lista = [dados_extraidos]
					
					agendados = 0
					for ev in eventos_lista:
						titulo = ev.get("titulo", "Compromisso")
						data = ev.get("data_vencimento")
						hora = ev.get("hora_inicio")
						if data and hora:
							sucesso = criar_evento_agenda(titulo, data, "Agendado via Assistente", hora)
							if sucesso: agendados += 1
					
					if agendados > 0:
						resposta_para_whatsapp = f"Pronto! Agendei os {agendados} horários na sua agenda para não esquecer."
					else:
						resposta_para_whatsapp = "Não consegui marcar na agenda. Verifique se me passou as datas certas."

				elif acao == "anotar_lembrete_geral":
					item_tarefa = dados_extraidos.get("tarefa", "")
					if item_tarefa:
						sucesso = registrar_tarefa_lista(item_tarefa)
						resposta_para_whatsapp = f"Anotado na sua lista de tarefas, chefe: '{item_tarefa}'!" if sucesso else "Erro ao salvar na lista."

				elif acao == "importar_fiados_lote":
					lista_fiados = dados_extraidos.get("lista_fiados", [])
					
					if not lista_fiados:
						resposta_para_whatsapp = "Chefe, olhei a foto mas não consegui identificar nenhum nome ou valor claro. Pode tentar tirar uma foto mais de perto ou com mais luz?"
					else:
						resultados = []
						for fiado in lista_fiados:
							nome_alvo = fiado.get("nome_cliente", "")
							try:
								valor_bruto = fiado.get("valor_total", 0)
								# Se a IA já enviou um número puro, apenas usamos
								if isinstance(valor_bruto, (int, float)):
									valor_divida = float(valor_bruto)
								else:
									# Se a IA enviou como texto (ex: "R$ 27,00"), limpamos com segurança
									v_str = str(valor_bruto).replace("R$", "").strip()
									if "," in v_str:
										v_str = v_str.replace(".", "").replace(",", ".")
									valor_divida = float(v_str)
							except Exception as e:
								print(f"Erro na conversão do valor: {e}")
								valor_divida = 0.0
								
							if nome_alvo and valor_divida > 0:
								# Procura o contato do caderno na agenda do celular
								status_busca, resultado_busca = buscar_telefone_na_agenda(nome_alvo)
								
								if status_busca == "sucesso":
									telefone_real = resultado_busca
									# Lança como se fosse uma venda fiada antiga
									sucesso_venda = registrar_venda(
										telefone=telefone_real,
										nome_cliente=nome_alvo,
										pedido="Importação de caderno antigo (Foto)",
										valor=valor_divida,
										local="Migração de Dados",
										itens_vendidos=[],
										status_pagamento="Pendente ⏳"
									)
									if sucesso_venda:
										atualizar_compra_cliente(telefone_real, nome_alvo, valor_divida)
										resultados.append(f"✅ *{nome_alvo}*: Adicionado (R$ {valor_divida:.2f})")
									else:
										resultados.append(f"❌ *{nome_alvo}*: Falha ao salvar na planilha.")
								elif status_busca == "duvida":
									resultados.append(f"⚠️ *{nome_alvo}*: Achei vários contatos com esse nome. Lance manualmente.")
								else:
									resultados.append(f"❌ *{nome_alvo}*: Não encontrei esse nome na agenda do celular.")
									
						resposta_para_whatsapp = "📸 *LEITURA DO CADERNO CONCLUÍDA:*\n\n" + "\n".join(resultados)
						resposta_para_whatsapp += "\n\nSe alguém ficou de fora, verifique se o nome no papel está escrito igual ao nome salvo nos contatos!"

				elif acao == "analisar_compra_pessoal":
					item = dados_extraidos.get("item_desejado", "compra")
					try:
						valor = float(dados_extraidos.get("valor_item", 0))
					except:
						valor = 0.0
						
					if valor > 0:
						sucesso, msg = calcular_preco_em_doces(item, valor)
						resposta_para_whatsapp = msg
					else:
						resposta_para_whatsapp = "Chefe, não entendi o valor exato. Quanto custa isso que você quer comprar?"

				elif acao == "processar_nota_fiscal":
					mercado = dados_extraidos.get("supermercado", "Supermercado")
					valor_empresa = float(dados_extraidos.get("valor_empresa", 0))
					valor_pessoal = float(dados_extraidos.get("valor_pessoal", 0))
					itens_empresa = dados_extraidos.get("itens_empresa", [])
					
					if (valor_empresa + valor_pessoal) > 0:
						sucesso = registrar_nota_fiscal(mercado, valor_empresa, valor_pessoal, itens_empresa)
						resposta_para_whatsapp = dados_extraidos.get("resposta_amigavel", f"Nota processada! Gastos divididos e salvos na planilha.") if sucesso else "Chefe, li a nota, mas a planilha falhou ao salvar o histórico."
					else:
						resposta_para_whatsapp = "Chefe, a foto ficou um pouco embaçada e não consegui ler os valores de forma segura. Pode tentar mandar com mais foco?"
						
				else:
					resposta_para_whatsapp = dados_extraidos.get("resposta_amigavel", "Anotado!")
					
			except json.JSONDecodeError:
				resposta_para_whatsapp = "Chefe, me confundi aqui. Pode falar de novo de um jeito mais simples?"
				
		# --- MODO CLIENTE (VENDAS) ---
		else:
			print("👤 Processando pedido de cliente...")
			
			estoque_hoje = obter_estoque_atual()
			saldo_atual_cliente = verificar_saldo_cliente(numero) 

			#Lógica de Status Dinâmico
			status_manual = ler_status_loja()
			aviso_rota = ""

			if status_manual == "EM_ROTA":
				status_loja = "EM_ROTA (A chefe está na rua fazendo entregas)"
				aviso_rota = "\n⚠️ ATENÇÃO: A CHEFE ESTÁ NA RUA. VOCÊ DEVE AVISAR ISSO AO CLIENTE OBRIGATORIAMENTE."
			elif status_manual == "FECHADO" or not verificar_loja_aberta():
				status_loja = f"FECHADO (A confeitaria não está recebendo pedidos no momento)"
			else:
				status_loja = "ABERTO"
			
			prompt_venda = f"""
			Data: {info_tempo}
			{onde_estamos}
			Nome do contato no WhatsApp: {nome_cliente}.
			Saldo Devedor Anterior (Fiado): {saldo_atual_cliente}.
			STATUS DA CONFEITARIA NESTE EXATO MINUTO: {status_loja} {aviso_rota}
			
			CARDÁPIO REAL E ÚNICO PARA AGORA (IGNORE O HISTÓRICO):
			{estoque_hoje}
			
			Histórico da conversa:
			{contexto_completo}
			
			REGRAS RIGOROSAS DE VENDAS E LOGÍSTICA:
			1. PRONTA ENTREGA (Venda Imediata): Somente para a ação "registrar_venda", você deve seguir rigorosamente o CARDÁPIO DE HOJE. Para ENCOMENDAS, ignore essa restrição. NUNCA venda ou ofereça um produto que não está no CARDÁPIO DE HOJE acima.
			2. Se o cliente pedir algo que não tem no cardápio, diga educadamente que não temos esse item hoje e informe apenas o que temos.
			3. ESTOQUE VAZIO É ABSOLUTO: Se o CARDÁPIO DE HOJE disser que está vazio ou que não há produtos, avise o cliente que não temos nada no momento. É ESTRITAMENTE PROIBIDO ler o histórico da conversa para tentar listar produtos antigos como se fossem "opções futuras". Apenas responda que o estoque de hoje já foi zerado e peça para aguardar o cardápio do próximo dia.
			4. Use os preços do cardápio para calcular o "valor_total".
			5. ENTREGAS (USO INTERNO): Para preencher o campo "local" no JSON, baseie-se APENAS no local onde a conversa está acontecendo. Se o cabeçalho disser que estamos no grupo "Superintendência", o local é "Superintendência". Se for no grupo "APAE", o local é "APAE". Se for em uma conversa no "Privado" ou grupo desconhecido, o local é "Retirada". Nunca use o dia da semana para deduzir o local.
			6. IMPORTANTE: NUNCA mencione nem cobre o cliente proativamente sobre o "Saldo Devedor Anterior". Só informe se o cliente EXPLICITAMENTE perguntar.
			7. Cancelamentos/Trocas: Se o cliente quiser cancelar um lanche de AGORA, use "cancelar_pedido". Se ele disser para cancelar uma ENCOMENDA FUTURA, use "cancelar_encomenda".
			8. ENCOMENDAS: Se o cliente pedir uma "encomenda" ou um item que exija preparo (mesmo que ele queira para mais tarde no próprio dia de HOJE), NÃO recuse. IGNORE o cardápio de pronta entrega, use a ação "conversar" para alinhar os detalhes e, quando tiver tudo, use "registrar_encomenda".
			9. HORÁRIO E STATUS (ATENÇÃO): 
			  - Se o STATUS for "FECHADO", não registre vendas ou encomendas.
			  - Se o STATUS for "EM_ROTA" e for uma Venda Imediata ("registrar_venda"), inclua o aviso da chefe na rua. 
			  - Se for uma ENCOMENDA ("registrar_encomenda"), NÃO precisa do aviso de "chefe na rua", pois encomendas são para horários futuros e serão aprovadas depois.
			10. ÁUDIOS E MENSAGENS INCOMPLETAS: Use APENAS a ação "conversar" e responda pedindo para repetir se a mensagem for confusa.
			11. EXTRATO DE FIADO E CONFERÊNCIA: Se o cliente perguntar o que está devendo, pedir a conta, use IMEDIATAMENTE a ação "consultar_meu_extrato".
			12. CORREÇÕES E ACRÉSCIMOS: 
			  - Se o cliente pedir mais itens (ex: "quero também uma rosca"), use "registrar_venda" APENAS para os itens novos. 
			  - Se o cliente usar palavras de correção como "na verdade", "mudei de ideia", "não é mais X, é Y", ou diminuir a quantidade do que acabou de pedir, você DEVE OBRIGATORIAMENTE retornar a ação "cancelar_pedido" primeiro para limpar o erro. Após o cancelamento ser processado, o cliente pedirá novamente ou você anotará o novo valor em uma mensagem separada. NUNCA registre uma nova venda de um item que o cliente está tentando corrigir sem cancelar a anterior antes.
			13. FORMATAÇÃO DO MENU: Formate o cardápio como uma lista visual com emojis (ex: 🍰, 🥖).
			14. PRIVACIDADE DE CONTATO: NUNCA chame o cliente pelo "Nome do contato no WhatsApp" na sua "resposta_amigavel".
			15. REGISTRO INSTANTÂNEO (BIPE DIRETO): Assim que o cliente pedir um item, use IMEDIATAMENTE a ação "registrar_venda".
			16. MENSAGENS DE "OK" OU "CONFIRMO": Mensagens de concordância sem itens novos DEVEM usar APENAS a ação "conversar".
			17. AVISO DE PAGAMENTO: Se o cliente enviar um comprovante ou afirmar que pagou, use IMEDIATAMENTE a ação "informar_pagamento".
			18. COMPRA JÁ PAGA NA HORA: Se o cliente fizer um pedido e NA MESMA MENSAGEM já disser que pagou (Pix, dinheiro, etc), use a ação "registrar_venda" e adicione no JSON o campo "forma_pagamento": "pago_agora". Se ele não disser nada sobre pagamento, o padrão é "forma_pagamento": "fiado".
			19. CONVERSA PARALELA EM GRUPOS: Como você está operando em um grupo do WhatsApp, os clientes podem conversar entre si (ex: "Bom dia, vizinha", "Hoje vai chover"). Se a mensagem for CLARAMENTE uma conversa entre terceiros, que não seja um pedido, nem uma dúvida sobre o cardápio, nem direcionada à confeitaria, retorne ESTRITAMENTE a ação "ignorar". O bot ficará em silêncio absoluto para não ser inconveniente.
			20. MENSAGENS EM GRUPOS (APAE/Superintendência): Se a conversa estiver acontecendo em um grupo, sua "resposta_amigavel" DEVE ser extremamente curta, objetiva e direta. Confirme o pedido e o valor total, mas NUNCA pergunte sobre a forma de pagamento e NUNCA fale sobre "retirar" ou "buscar" (pois a chefe já faz a entrega presencial nesses locais). 
			Exemplo PERFEITO para grupos: "Anotado! 3 bolos de mandioca, total R$ 24,00."

			Gere o JSON:
			"""
			
			conteudo_ia = [prompt_venda]
			
			if media_data and media_mime:
				conteudo_ia.append({
					"mime_type": media_mime,
					"data": media_data
				})
				
			resposta_ia = modelo_cliente.generate_content(conteudo_ia)
			
			try:
				txt_limpo = resposta_ia.text.strip()
				if txt_limpo.startswith('```json'):
					txt_limpo = txt_limpo[7:]
				if txt_limpo.startswith('```'):
					txt_limpo = txt_limpo[3:]
				if txt_limpo.endswith('```'):
					txt_limpo = txt_limpo[:-3]
					
				dados_extraidos = json.loads(txt_limpo.strip())
				
				acao = dados_extraidos.get("acao")
				
				if acao == "registrar_venda":
					itens_vendidos = dados_extraidos.get("itens_vendidos", [])
					valor_correto = calcular_total_seguro(itens_vendidos) if itens_vendidos else float(dados_extraidos.get("valor_total", 0))
					
					pode_vender = True
					msg_erro = ""
					
					if itens_vendidos:
						pode_vender, msg_erro = verificar_disponibilidade(itens_vendidos)
						
					if not pode_vender:
						resposta_para_whatsapp = msg_erro
					else:
						# --- NOVA LÓGICA DE PAGAMENTO ---
						forma_pag = dados_extraidos.get("forma_pagamento", "fiado")
						status_planilha = "Pago ✅" if forma_pag == "pago_agora" else "Pendente ⏳"
						
						sucesso_venda = registrar_venda(
							telefone=numero,
							nome_cliente=dados_extraidos.get("nome_cliente", nome_cliente),
							pedido=dados_extraidos.get("pedido", ""),
							valor=valor_correto, 
							local=dados_extraidos.get("local", ""),
							itens_vendidos=itens_vendidos,
							status_pagamento=status_planilha
						)
						
						# Só joga no Livro Caixa (Aba Clientes) se for fiado!
						if sucesso_venda and forma_pag == "fiado":
							atualizar_compra_cliente(numero, dados_extraidos.get("nome_cliente", nome_cliente), valor_correto) 
							
						resposta_para_whatsapp = dados_extraidos["resposta_amigavel"] if sucesso_venda else "Tive um probleminha para anotar no sistema, mas já aviso a chefe do seu pedido!"
						
						if abs(valor_correto - float(dados_extraidos.get("valor_total", 0))) > 0.1: 
							resposta_para_whatsapp += f"\n\n*(Correção automática: o valor exato dos itens é R$ {valor_correto:.2f})*"
                            
						status_manual = ler_status_loja()
						if status_manual == "EM_ROTA" and sucesso_venda:
							itens_texto = dados_extraidos.get("pedido", "itens")
							notificacao_para_admin = f"⚠️ *CLIENTE NA FILA DE ESPERA* ⚠️\n\n👤 *De:* {nome_cliente} ({numero.split('@')[0]})\n📦 *Pedido:* {itens_texto}\n\nSe você ainda tiver esses itens na cesta, responda o cliente no privado ou no grupo para confirmar a entrega!"
				
				elif acao == "cancelar_pedido":
					sucesso, msg_retorno = cancelar_ultimo_pedido(numero)
					resposta_para_whatsapp = dados_extraidos.get("resposta_amigavel", msg_retorno) if sucesso else msg_retorno

				elif acao == "cancelar_encomenda":
					sucesso, msg_retorno = cancelar_ultimo_pedido(numero, tipo_alvo="encomenda")
					resposta_para_whatsapp = dados_extraidos.get("resposta_amigavel", msg_retorno) if sucesso else msg_retorno
					
				elif acao == "registrar_encomenda":
					data_entrega = dados_extraidos.get("data_entrega", "A combinar")
					pedido_texto = dados_extraidos.get("pedido", "")
					
					sucesso = solicitar_encomenda(
						telefone=numero,
						nome_cliente=dados_extraidos.get("nome_cliente", nome_cliente),
						pedido=pedido_texto,
						data_entrega=data_entrega
					)
					
					if sucesso:
						resposta_para_whatsapp = dados_extraidos.get("resposta_amigavel", f"Tudo anotado! Como é uma encomenda para {data_entrega}, eu vou passar os detalhes para a chefe avaliar. Ela te chama rapidinho para confirmar o valor e fechar o pedido, tá bom?")
						
						notificacao_para_admin = f"⚠️ *NOVA ENCOMENDA PARA APROVAR* ⚠️\n\n👤 *Cliente:* {nome_cliente}\n📅 *Para:* {data_entrega}\n📝 *Pedido:* {pedido_texto}\n\nPara confirmar, responda aqui mesmo: _'Confirma a encomenda de {nome_cliente} por X reais'_."
					else:
						resposta_para_whatsapp = "Tive um probleminha para anotar a encomenda no sistema, mas já vou chamar a chefe para te atender!"

				elif acao == "consultar_meu_extrato":
					sucesso, extrato = gerar_extrato_fiado(numero, por_telefone=True)
					
					if is_group:
						resposta_para_whatsapp = "Te enviei o seu extrato no privado!"
						resposta_privada = f"Oi! Como você pediu lá no grupo, puxei o seu caderninho digital aqui pra gente conferir:\n\n{extrato}"
					else:
						resposta_para_whatsapp = f"Claro, peguei aqui o seu caderninho digital!\n\n{extrato}"

				elif acao == "informar_pagamento":
					resposta_para_whatsapp = dados_extraidos.get("resposta_amigavel", "Obrigado! Já enviei o aviso para a chefe conferir o Pix e dar baixa no seu saldo.")
					
					notificacao_para_admin = f"💸 *AVISO DE PAGAMENTO* 💸\n\nO cliente *{nome_cliente}* ({numero.split('@')[0]}) acabou de avisar que fez um pagamento/Pix.\n\nPor favor, confira a conta bancária. Se o dinheiro caiu, responda aqui mesmo:\n_'Atualizar pagamento de {nome_cliente} valor X'_"

				elif acao == "ignorar":
					resposta_para_whatsapp = ""
					print(f"🔇 IA detectou conversa paralela de {nome_cliente}. Silenciando bot.")
					
				else:
					resposta_para_whatsapp = dados_extraidos.get("resposta_amigavel", "Posso te ajudar com o seu pedido?")
					
			except json.JSONDecodeError as e:
				print(f"❌ Erro crítico de JSON! A IA respondeu: {resposta_ia.text}")
				resposta_para_whatsapp = "Desculpe, não entendi direito. Pode repetir seu pedido?"
		
		if resposta_para_whatsapp:
			historico_conversas[chave_historico].append(f"Assistente: {resposta_para_whatsapp}")
			salvar_historico() 
			
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

@app.route('/briefing_matinal', methods=['GET'])
def briefing_matinal():
	eventos = listar_compromissos_dia()
	
	# --- NOVA BUSCA: TAREFAS PENDENTES ---
	tarefas_texto = ""
	try:
		aba_t = planilha_db.worksheet("Tarefas")
		registros = aba_t.get_all_records()
		pendentes = [r['Tarefa'] for r in registros if "Pendente" in r.get('Status', '')]
		if pendentes:
			tarefas_texto = "\n📝 *LEMBRETES E TAREFAS:* \n" + "\n".join([f"▫️ {t}" for t in pendentes])
	except: pass

	if not eventos and not tarefas_texto:
		return jsonify({"mensagem": "Bom dia, chefe! ☀️ Hoje a agenda e a lista de tarefas estão limpas. Dia de focar em novas produções!"})
	
	resumo = "☀️ *BOM DIA, CHEFE! Sua Agenda de Hoje:* ☀️\n\n"
	for ev in eventos:
		start = ev.get('start', {})
		if 'dateTime' in start:
			horario = start['dateTime'][11:16]
			resumo += f"📌 *[{horario}]* {ev['summary']}\n"
		else:
			resumo += f"📌 *[Dia Todo]* {ev['summary']}\n"
		resumo += f"📝 {ev.get('description', 'Sem detalhes')}\n\n"
	
	resumo += tarefas_texto + "\n\nJá quer que eu separe as etiquetas dos pedidos?"
	return jsonify({"mensagem": resumo})

@app.route('/estoque_automatico', methods=['GET'])
def estoque_automatico():
	"""Rota para o Node.js buscar o texto do cardápio."""
	cardapio = obter_estoque_atual()
	
	# Se estiver zerado, manda o texto puro para o Node barrar o envio
	if "vazio" in cardapio or "Não temos nenhum produto" in cardapio:
		return jsonify({"cardapio": cardapio}), 200
		
	# Se tiver produtos, monta a mensagem de Bom Dia!
	msg_completa = f"☀️ *Bom dia, pessoal!* ☀️\n\n🌟 *CARDÁPIO DE HOJE* 🌟\n\n{cardapio}\nFicou com vontade? É só me pedir por aqui! 😋"
	return jsonify({"cardapio": msg_completa}), 200

@app.route('/conferir_final_rota', methods=['GET'])
def conferir_final_rota():
	"""Verifica se ainda há itens no estoque e gera a pergunta para a chefe."""
	try:
		status_atual = ler_status_loja()
		if status_atual != "EM_ROTA":
			return jsonify({"ignorar": True})

		# Força o status para FECHADO para evitar novas vendas enquanto ela não confirma
		salvar_status_loja("FECHADO")
		
		# Verifica o que ainda consta como 'Sim' na planilha
		estoque_teorico = obter_estoque_atual()
		
		if "Não temos nenhum produto" in estoque_teorico:
			return jsonify({"mensagem": "Chefe, já deu o horário! Como o estoque já estava zerado, encerrei o expediente por aqui. Bom descanso! 🏠"})
		
		msg = "Chefe, vi que você ainda está em rota, mas já são 16:30! 🕒\n\n"
		msg += "Pelo meu controle, ainda constam estes itens:\n"
		msg += estoque_teorico
		msg += "\n*Sobrou algo disso ou posso zerar tudo para amanhã?*"
		
		return jsonify({"mensagem": msg})
	except Exception as e:
		return jsonify({"erro": str(e)}), 500

@app.route('/gatilho_seguranca_18h', methods=['POST'])
def gatilho_seguranca_18h():
	"""Zera tudo automaticamente para garantir que o dia seguinte comece limpo."""
	sucesso = zerar_estoque_completo()
	salvar_status_loja("FECHADO")
	return jsonify({"mensagem": "🚨 *Gatilho de Segurança:* Estoque zerado e loja fechada automaticamente." if sucesso else "Erro no gatilho."})

@app.route('/radar_vencimentos', methods=['GET'])
def radar_vencimentos():
	try:
		# Calcula a data de daqui a exatos 2 dias
		daqui_a_2_dias = datetime.now() + timedelta(days=2)
		data_alvo_str = daqui_a_2_dias.strftime("%d/%m/%Y")
		
		# Puxa os compromissos da agenda para aquele dia
		eventos = listar_compromissos_dia(data_alvo_str)
		
		contas_vencendo = []
		for ev in eventos:
			titulo = ev.get('summary', '')
			# Filtra apenas os eventos que são contas a pagar
			if "💸 Pagar" in titulo:
				descricao = ev.get('description', '')
				# Limpa o título para a mensagem ficar elegante
				nome_conta = titulo.replace("💸 Pagar: ", "")
				contas_vencendo.append(f"▫️ *{nome_conta}*\n   {descricao}")
				
		# Se não encontrar nenhuma conta, devolve "ignorar" para manter o silêncio
		if not contas_vencendo:
			return jsonify({"ignorar": True})
			
		msg = f"⚠️ *RADAR DE CONTAS ATIVADO!* ⚠️\n\nChefe, estou a passar para avisar que as seguintes contas vencem daqui a 2 dias ({data_alvo_str}):\n\n"
		msg += "\n\n".join(contas_vencendo)
		msg += "\n\nJá efetuou o pagamento? Se sim, basta avisar-me para eu dar baixa e retirar a conta da agenda!"
		
		return jsonify({"mensagem": msg})
	except Exception as e:
		return jsonify({"erro": str(e)}), 500

@app.route('/abrir_loja_automatico', methods=['POST'])
def abrir_loja_automatico():
	salvar_status_loja("ABERTO")
	return jsonify({"mensagem": "Loja aberta com sucesso"}), 200

if __name__ == '__main__':
	print("Servidor rodando...")
	app.run(port=5000, debug=True)