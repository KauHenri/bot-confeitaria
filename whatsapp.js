const { Client, LocalAuth } = require('whatsapp-web.js');
const qrcode = require('qrcode-terminal');
const axios = require('axios');
const fs = require('fs');

require('dotenv').config();

const client = new Client({
	authStrategy: new LocalAuth(),
	puppeteer: { args: ['--no-sandbox', '--disable-setuid-sandbox'] }
});

let contatosCache = {};

// Normalização avançada: remove o 9 se existir e limpa o sufixo
function normalizarID(id) {
	if (!id) return "";
	let num = id.split('@')[0];
	if (num.startsWith('55') && num.length === 13) {
		num = num.slice(0, 4) + num.slice(5);
	}
	return num;
}

client.on('qr', (qr) => {
	qrcode.generate(qr, { small: true });
});

client.on('ready', async () => {
	console.log('✅ Sistema Online e pronto para operar!');
	
	// --- O SEU SISTEMA DE CACHE DE AGENDA AQUI ---
	console.log('⏳ Carregando agenda de contatos reais para o Cache...');
	try {
		const contatos = await client.getContacts();
		
		// Filtro de Ouro: Apenas pessoas salvas na agenda e sem as duplicações
		const contatosUteis = contatos.filter(c => 
			c.isUser && 
			c.isMyContact && // <-- O SEGREDO ESTÁ AQUI: Só pega quem está salvo de verdade na agenda
			c.id && 
			!c.id._serialized.includes('@lid') // <-- Arranca os IDs fantasmas duplicados
		);
		
		const agendaFormatada = contatosUteis.map(c => ({
			nome: c.name || c.pushname || "Sem Nome",
			telefone: c.id._serialized
		}));

		// Salva no arquivo JSON
		fs.writeFileSync('agenda.json', JSON.stringify(agendaFormatada, null, 2));
		console.log(`📁 Cache limpo criado! Caiu de 4500 para ${agendaFormatada.length} contatos reais salvos no agenda.json`);
	} catch (error) {
		console.error('❌ Erro ao criar cache da agenda:', error);
	}

	// Código temporário para descobrir os IDs dos grupos
	// try {
	// 	const chats = await client.getChats();
	// 	const grupos = chats.filter(chat => chat.isGroup);
		
	// 	console.log('\n--- IDs DOS SEUS GRUPOS ---');
	// 	grupos.forEach(g => {
	// 		console.log(`📌 Grupo: ${g.name} | ID: ${g.id._serialized}`);
	// 	});
	// 	console.log('---------------------------\n');
		
	// } catch (e) {
	// 	console.error("❌ Erro ao buscar grupos:", e);
	// }
});

// --- SISTEMA DE AUTO-CURA (RECONEXÃO PM2) ---
client.on('disconnected', (reason) => {
	console.log('❌ O WhatsApp Web foi desconectado pelo celular ou pela Meta!');
	console.log('Motivo da queda:', reason);
	console.log('🔄 Forçando encerramento para o PM2 reiniciar o sistema...');
	
	// O código 1 diz ao Linux que o programa falhou.
	// O PM2 vai ver isso e reiniciar o whatsapp.js automaticamente em 1 segundo.
	process.exit(1); 
});

// Tratamento de erros do Puppeteer (Evita que o Chrome congele de madrugada)
client.on('auth_failure', msg => {
	console.error('❌ Falha na autenticação (Sessão inválida):', msg);
	process.exit(1);
});

client.on('message', async msg => {
	if (msg.from === 'status@broadcast') return;

	const chat = await msg.getChat();
	const numeroRemetenteOriginal = msg.author || (msg.id && msg.id.participant) || msg.from;
	let numeroFinal = numeroRemetenteOriginal;
	let nomeFinal = null;

	try {
		const contact = await msg.getContact();
		// --- DESMASCARANDO O @LID ---
		// Se o ID for uma máscara @lid, tentamos pegar o ID real (@c.us)
		if (numeroFinal.includes('@lid')) {
			if (contact.id && contact.id._serialized.includes('@c.us')) {
				numeroFinal = contact.id._serialized;
			} else if (contact.number) {
				// Fallback: monta o ID usando o número puro do contato
				numeroFinal = contact.number.includes('@') ? contact.number : contact.number + '@c.us'	;
			}
		}

		// Identificação do nome (Agenda > Perfil > Número)
		if (contact.name) {
			nomeFinal = contact.name;
		} else if (contact.pushname) {
			nomeFinal = contact.pushname;
		}
	} catch (e) {
		console.log("Falha na captura detalhada do contato.");
	}

	// Se nada funcionou para o nome, usamos o número desmascarado
	if (!nomeFinal) {
		nomeFinal = numeroFinal.split('@')[0];
	}

	// --- PROCESSAMENTO DE MÍDIA (ÁUDIO E IMAGEM) ---
	let mediaData = null;
	let mediaMime = null;

	if (msg.hasMedia) {
		try {
			const media = await msg.downloadMedia();
			if (media) {
				mediaData = media.data; // O arquivo em formato base64
				mediaMime = media.mimetype; // O tipo (ex: audio/ogg ou image/jpeg)
				console.log(`📎 Mídia recebida: ${mediaMime}`);
			}
		} catch (e) {
			console.log("❌ Erro ao baixar mídia:", e);
		}
	}

	// Captura o texto da mensagem (ou a legenda da foto)
	const textoMensagem = msg.body || "";

	console.log(`🔍 [IDENTIFICAÇÃO] Original: ${numeroRemetenteOriginal} | Final: ${numeroFinal} | Nome: ${nomeFinal}`);

	const payload = {
		data: {
			message: { conversation: textoMensagem },
			media: {
				data: mediaData,
				mimeType: mediaMime
			},
			key: { 
				remoteJid: msg.from,
				participant: numeroFinal // Enviamos o número real para o Python
			},
			pushName: nomeFinal,
			groupContext: {
				isGroup: chat.isGroup,
				groupName: chat.isGroup ? chat.name : "Privado"
			}
		}
	};

	try {
		const respostaPython = await axios.post('http://localhost:5000/webhook', payload);
		// Responde o cliente na conversa original
		if (respostaPython.data.resposta) {
			await msg.reply(respostaPython.data.resposta);
		}
		
		// Se o Python mandar uma mensagem sigilosa, o Node envia direto pro número da pessoa
		if (respostaPython.data.resposta_privada) {
			await client.sendMessage(numeroFinal, respostaPython.data.resposta_privada);
			console.log(`🔒 Mensagem enviada no privado para: ${numeroFinal}`);
		}

		// Se o Python mandou um alerta para a chefe, joga no Grupo Admin!
		if (respostaPython.data.notificacao_admin) {
			await client.sendMessage(ID_GRUPO_ADMIN, respostaPython.data.notificacao_admin);
			console.log(`📢 Alerta enviado para a Sala de Controle!`);
		}
		
	} catch (error) {
		console.error('Erro na ponte:', error.message);
	}
});

const cron = require('node-cron');

const ID_GRUPO_APAE = process.env.ID_GRUPO_APAE; 
const ID_GRUPO_SUPERINTENDENCIA = process.env.ID_GRUPO_SUPERINTENDENCIA;
const ID_GRUPO_ADMIN = process.env.ID_GRUPO_ADMIN;

// --- FUNÇÃO PARA PEGAR O CARDÁPIO DO PYTHON ---
async function enviarCardapioAutomatico(targetJID) {
	try {
		// O Node "pergunta" pro Python o que tem no estoque
		const response = await axios.get('http://localhost:5000/estoque_automatico');
		if (response.data.cardapio) {
			await client.sendMessage(targetJID, response.data.cardapio);
			console.log(`🚀 Cardápio enviado com sucesso para: ${targetJID}`);
		}
	} catch (error) {
		console.error('❌ Erro ao buscar cardápio para envio automático:', error.message);
	}
}

// --- FUNÇÃO PARA PEGAR O BRIEFING DA AGENDA NO PYTHON ---
async function enviarBriefingMatinal(targetJID) {
	try {
		// O Node chama a rota que criamos no app.py
		const response = await axios.get('http://localhost:5000/briefing_matinal');
		if (response.data.mensagem) {
			await client.sendMessage(targetJID, response.data.mensagem);
			console.log(`☀️ Briefing matinal enviado com sucesso para: ${targetJID}`);
		}
	} catch (error) {
		console.error('❌ Erro ao buscar briefing matinal no Python:', error.message);
	}
}


// --- CONFIGURAÇÃO DOS HORÁRIOS (CRON) ---

// Segundas e Quartas às 08:00 (Superintendência)
// Lógica: minuto(0) hora(8) dia(*) mes(*) dia_semana(1,3)
cron.schedule('0 10 * * 1,3', () => {
	console.log('⏰ Hora da Superintendência! Enviando cardápio...');
	enviarCardapioAutomatico(ID_GRUPO_SUPERINTENDENCIA);
});

// Terças e Quintas às 08:00 (APAE)
// Lógica: minuto(0) hora(8) dia(*) mes(*) dia_semana(2,4)
cron.schedule('0 10 * * 2,4,5', () => {
	console.log('⏰ Hora da APAE! Enviando cardápio...');
	enviarCardapioAutomatico(ID_GRUPO_APAE);
});

// Briefing Matinal - Todo dia às 07:00 da manhã
// Lógica: minuto(0) hora(7) dia(*) mes(*) dia_semana(*)
cron.schedule('0 7 * * *', () => {
	console.log('⏰ Hora do lembrete matinal! Consultando a agenda da chefe...');
	
	enviarBriefingMatinal(ID_GRUPO_ADMIN); 
});

// // === CÓDIGO DE TESTE: Roda a cada 1 minuto ===
// cron.schedule('* * * * *', () => {
// 	console.log('⏰ [TESTE RÁPIDO] O relógio virou! Enviando cardápio...');
// 	// Coloque o ID do grupo da APAE ou Superintendência que você descobriu
// 	enviarCardapioAutomatico(ID_GRUPO_SUPERINTENDENCIA); 
// });

// // TESTE: Enviar briefing agora (ajuste os minutos conforme o horário atual)
// cron.schedule('* * * * *', () => { 
// 	console.log('⏰ [TESTE] Rodando briefing matinal agora...');
// 	enviarBriefingMatinal(ID_GRUPO_ADMIN);
// });

// --- 16:30: CONFERÊNCIA DE SOBRAS ---
cron.schedule('30 16 * * *', async () => {
	console.log('⏰ Hora de conferir as sobras com a chefe...');
	try {
		const response = await axios.get('http://localhost:5000/conferir_final_rota');
		if (response.data.mensagem) {
			await client.sendMessage(ID_GRUPO_ADMIN, response.data.mensagem);
		}
	} catch (error) {
		console.error('Erro no cron das 16:30:', error.message);
	}
});

// --- 18:00: GATILHO DE SEGURANÇA (LIMPEZA TOTAL) ---
cron.schedule('0 18 * * *', async () => {
	console.log('🚨 Executando limpeza automática de segurança...');
	try {
		const response = await axios.post('http://localhost:5000/gatilho_seguranca_18h');
		if (response.data.mensagem) {
			await client.sendMessage(ID_GRUPO_ADMIN, response.data.mensagem);
		}
	} catch (error) {
		console.error('Erro no gatilho das 18h:', error.message);
	}
});

// --- 09:00: RADAR DE CONTAS (VENCIMENTOS EM 48H) ---
// Lógica: minuto(0) hora(9) dia(*) mes(*) dia_semana(*)
cron.schedule('0 9 * * *', async () => {
	console.log('⏰ A executar o Radar de Contas (Aviso de 48h)...');
	try {
		const response = await axios.get('http://localhost:5000/radar_vencimentos');
		if (response.data.mensagem) {
			await client.sendMessage(ID_GRUPO_ADMIN, response.data.mensagem);
		}
	} catch (error) {
		console.error('Erro no cron do radar de contas:', error.message);
	}
});

// --- SISTEMA ANTI-ZUMBI (REINÍCIO DIÁRIO PREVENTIVO) ---
// Todo dia às 06:50 da manhã, força o robô a reiniciar para limpar o cache da madrugada
cron.schedule('50 6 * * *', () => {
	console.log('🔄 [ANTI-ZUMBI] Executando reinício diário preventivo da conexão...');
	// O código 1 avisa o PM2 que o processo fechou. O PM2 reabre ele novinho em 1 segundo.
	process.exit(1);
});

client.initialize();