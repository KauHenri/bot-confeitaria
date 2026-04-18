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

// === CÓDIGO DE TESTE: Roda a cada 1 minuto ===
// cron.schedule('* * * * *', () => {
// 	console.log('⏰ [TESTE RÁPIDO] O relógio virou! Enviando cardápio...');
// 	// Coloque o ID do grupo da APAE ou Superintendência que você descobriu
// 	enviarCardapioAutomatico('COLOQUE_O_ID_DO_GRUPO_AQUI@g.us'); 
// });

client.initialize();