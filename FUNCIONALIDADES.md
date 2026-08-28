# 🍰 Manual de Funcionalidades — Assistente de Confeitaria

Este documento reúne todas as capacidades, comandos, integrações e arquitetura do bot assistente inteligente para a confeitaria.

---

## 👑 1. Modo Administradora (Comandos da Chefe)
> **Como usar:** Envie mensagens no grupo de administração cadastrado ou diretamente no privado configurado como `NUMERO_ADMIN`.

### 📦 Gestão de Cardápio e Estoque
* **Definir Produtos Disponíveis no Dia:**
  * *Exemplo:* `"Hoje vamos vender 10 pedaços de Bolo de Cenoura a 8 reais e 4 Roscas Recheadas a 25 reais."`
  * *Efeito:* O bot ativa os produtos para pronta entrega e atualiza os preços e quantidades no banco de dados.
* **Consultar Estoque e Preços:**
  * *Exemplo:* `"O que temos disponível hoje?"` ou `"Qual o preço do bolo de laranja?"`
* **Atualizar ou Desativar Produtos:**
  * *Exemplo:* `"Acabou o pão de queijo"` ou `"Desativa a rosca de coco por hoje."`

### 💰 Controle Financeiro (Entradas & Saídas)
* **Registro de Vendas Manuais (Balcão / Fora do WhatsApp):**
  * *Exemplo:* `"Registrar venda de 2 bolos de pote para João no dinheiro."`
* **Registro de Despesas e Contas:**
  * *Exemplo:* `"Anote uma despesa de R$ 85,00 com leite condensado e farinha."`
  * *Exemplo com vencimento:* `"Conta de luz no valor de R$ 190,00 vence dia 10."`
* **Dar Baixa em Pagamentos (Pix / Dinheiro):**
  * *Exemplo:* `"Atualizar pagamento de Maria no valor de R$ 50,00."`
  * *Efeito:* O saldo devedor do cliente é reduzido imediatamente no caderninho digital.

### 📓 Caderninho Digital de Clientes ("Fiado" / Saldo Devedor)
* **Consultar Extrato do Cliente:**
  * *Exemplo:* `"Quanto a Ana Luísa está devendo?"` ou `"Puxe o extrato do João."`
* **Histórico de Compras:**
  * Visualiza todos os pedidos e valores acumulados de cada cliente.

### 📅 Gestão de Compromissos (Google Calendar)
* **Agendar Encomendas e Entregas:**
  * *Exemplo:* `"Agendar entrega de bolo de aniversário para Dona Lurdes dia 15 às 16h."`
  * *Efeito:* Cria automaticamente o evento no Google Agenda conectado.
* **Consultar Agenda do Dia:**
  * *Exemplo:* `"Quais os compromissos e encomendas para hoje?"`

### ☀️ Briefing Matinal Automático
* **Disparo Diário (APScheduler):**
  * Todo início de manhã (configurado para as 07:30), o bot compila e envia um relatório com:
    1. Compromissos e entregas do Google Agenda para o dia.
    2. Contas a pagar que vencem hoje ou estão pendentes.

---

## 👤 2. Modo Cliente (Vendas & Atendimento Automático)
> **Como funciona:** O bot atende clientes em conversas privadas ou em grupos de pedidos.

* **🛍️ Consulta de Cardápio em Tempo Real:**
  * Informa educadamente somente os itens marcados como **disponíveis para hoje** com seus respectivos valores.
  * Caso o estoque diário esteja zerado, avisa com simpatia e orienta a aguardar a próxima fornada/cardápio.
* **🧾 Montagem e Fechamento de Pedidos:**
  * Calcula a soma total dos itens escolhidos, confirma o pedido e dá baixa automática nas unidades do banco de dados.
* **💳 Consulta de Saldo do Caderninho no Privado:**
  * Caso o cliente pergunte no grupo quanto deve, o bot responde no privado para preservar a privacidade do cliente.
* **💸 Notificação de Pagamento:**
  * Se o cliente avisar que realizou um Pix ou transferência, o bot agradece ao cliente e envia um alerta imediato no grupo da chefe para conferência bancária.
* **🔇 Filtro de Silenciamento em Grupos:**
  * Detecta quando os membros estão apenas conversando entre si (conversa paralela) e permanece em silêncio para não atrapalhar o grupo.

---

## 🧠 3. Inteligência Artificial Multimodal (Google Gemini)
* **🎙️ Compreensão de Mensagens de Áudio:**
  * Transcreve e compreende comandos de voz enviados pela chefe ou pedidos falados por clientes.
* **📸 Leitura de Imagens e Comprovantes:**
  * Analisa fotos de recibos, comprovantes Pix, anotações de papel ou fotos de produtos.
* **🧠 Memória Contextual Persistente:**
  * Mantém o histórico recente da conversa salvo em arquivo e banco de dados para entender referências do tipo *"quero mais dois desses"*.

---

## ⚙️ 4. Infraestrutura e Arquitetura Técnica

| Componente | Tecnologia | Função |
| :--- | :--- | :--- |
| **Banco de Dados** | SQLite (`confeitaria.db`) | Armazena produtos, clientes, vendas, despesas e tarefas de forma relacional e concorrente. |
| **WhatsApp Gateway** | Evolution API v2 (Docker / Host Network) | Conexão estável com WhatsApp Baileys na porta 8080. |
| **Servidor Web** | Python Flask + Gunicorn | API que recebe os webhooks e processa as solicitações. |
| **Processador Assíncrono** | `threading.Thread` | Responde ao webhook em milissegundos e processa a IA em segundo plano, evitando timeouts. |
| **Deduplicação de Mensagens** | Memória Cache (`deque`) | Descarta mensagens com mesmo ID recebidas repetidamente do WhatsApp. |
| **Gerenciador de Processos** | PM2 (Linux Oracle Cloud) | Mantém o bot online 24/7 com reinício automático em caso de falha. |
| **Agendador de Tarefas** | APScheduler Nativo | Executa o envio do briefing matinal no fuso `America/Sao_Paulo`. |
| **Modo Camuflagem/Teste** | Sintaxe `simular <nome>: <texto>` | Permite que os administradores simulem qualquer cliente ou a chefe durante testes. |

---

## 🚀 Comandos Rápidos de Manutenção no Servidor

* **Ver Logs em Tempo Real:**
  ```bash
  pm2 logs bot-confeitaria
  ```
* **Reiniciar o Bot:**
  ```bash
  pm2 restart bot-confeitaria
  ```
* **Verificar Status da Evolution API:**
  ```bash
  sudo docker ps
  sudo docker logs --tail 30 evolution-api
  ```
