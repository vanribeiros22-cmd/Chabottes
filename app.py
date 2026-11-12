from flask import Flask, request, jsonify
import pandas as pd
import requests
import os

app = Flask(__name__)

# Configurações da Evolution API
EVOLUTION_API_URL = "https://evolution-api-production-db931012.up.railway.app"
INSTANCE_NAME = "sofia-bot"
API_KEY = "cetrus2026teste"

EXCEL_FILE = "diretorio_academico.xlsx"


def carregar_numeros_bloqueados():
    """
    Carrega a lista de números bloqueados diretamente da aba 'TelefoneContatos_Bloqueados' do Excel.
    """
    try:
        if not os.path.exists(EXCEL_FILE):
            return []
        df_bloq = pd.read_excel(EXCEL_FILE, sheet_name="TelefoneContatos_Bloqueados")
        return [str(num).strip() for num in df_bloq["Telefone"].dropna().tolist()]
    except Exception as e:
        print(f"Erro ao carregar números bloqueados: {e}")
        return []


def consultar_excel(pergunta):
    """
    Consulta o Excel de forma inteligente, lidando com menus, opções numéricas e FAQs.
    """
    try:
        if not os.path.exists(EXCEL_FILE):
            return "⚠️ Erro: Ficheiro de diretório académico não encontrado no servidor."

        pergunta_limpa = pergunta.strip().lower()

        # 1. Palavras de saudação, menu ou encerramento que retornam ao Menu Principal
        comandos_menu = ["oi", "ola", "olá", "bom dia", "boa tarde", "boa noite", "menu", "ajuda", "0", "encerrar",
                         "sair", "encerrar atendimento"]
        if pergunta_limpa in comandos_menu:
            df_menu = pd.read_excel(EXCEL_FILE, sheet_name="Menu_Principal")
            menu_texto = "👋 Atendimento encerrado/reiniciado com sucesso!\n\nBem-vinda(o) ao atendimento académico da Sofia. Escolha uma das opções abaixo digitando o número ou o assunto:\n\n"
            for _, row in df_menu.iterrows():
                menu_texto += f"• *{row['Numero']}* - {row['Opcao']}\n"
            return menu_texto

        # 2. Se o utilizador enviou diretamente um número de opção (ex: "1", "2", "3", "4")
        df_faq = pd.read_excel(EXCEL_FILE, sheet_name="FAQ_BaseNumero_Menu")

        if pergunta_limpa.isdigit():
            num_opcao = float(pergunta_limpa)
            filtrado = df_faq[df_faq['Numero_Menu'] == num_opcao]
            if not filtrado.empty:
                resposta_acumulada = f"📌 *Opção {pergunta_limpa} — Conteúdos Disponíveis:*\n\n"
                for _, row in filtrado.iterrows():
                    proc = row.get('Processo ', '')
                    resp = row.get('Instrucoes_Tecnicas', '')
                    resposta_acumulada += f"🔹 *{proc}*\n{resp}\n\n-------------------\n\n"
                return resposta_acumulada

        # 3. Procura textual por palavras-chave nas FAQs
        for _, row in df_faq.iterrows():
            categoria = str(row.get("Categoria ", "")).lower()
            processo = str(row.get("Processo ", "")).lower()
            palavras_chave = str(row.get("Palavras_Chave", "")).lower()

            if (pergunta_limpa in palavras_chave) or (pergunta_limpa in processo) or (pergunta_limpa in categoria):
                cat_nome = row.get("Categoria ", "Informação")
                proc_nome = row.get("Processo ", "")
                instrucoes = row.get("Instrucoes_Tecnicas", "Consulte os detalhes no portal académico.")
                return f"📌 *{cat_nome} — {proc_nome}*\n\n{instrucoes}"

        return "Desculpa, não encontrei nenhuma informação correspondente no diretório académico. Podes enviar 'menu' para ver as opções disponíveis ou reformular a pergunta?"

    except Exception as e:
        return f"Ocorreu um erro ao processar os dados académicos: {str(e)}"


def enviar_mensagem_whatsapp(numero_destino, texto_resposta):
    """
    Envia a resposta de volta para o WhatsApp utilizando a Evolution API.
    """
    url = f"{EVOLUTION_API_URL}/message/sendText/{INSTANCE_NAME}"
    headers = {
        "Content-Type": "application/json",
        "apikey": API_KEY
    }
    payload = {
        "number": numero_destino,
        "text": texto_resposta
    }

    try:
        response = requests.post(url, json=payload, headers=headers)
        return response.json()
    except Exception as e:
        print(f"Erro ao enviar mensagem: {e}")
        return None


@app.route("/webhook", methods=["POST"])
def webhook():
    """
    Rota principal que recebe os eventos do WhatsApp via Evolution API.
    """
    dados = request.json

    try:
        evento = dados.get("event", "")
        if evento == "messages.upsert":
            message_data = dados.get("data", {})
            remote_jid = message_data.get("key", {}).get("remoteJid", "")

            # Ignora mensagens enviadas pelo próprio bot
            from_me = message_data.get("key", {}).get("fromMe", False)
            if from_me:
                return jsonify({"status": "ignored"}), 200

            # --- 1. LÓGICA DE GRUPOS ---
            if remote_jid.endswith("@g.us"):
                print(f"Mensagem de grupo ignorada: {remote_jid}")
                return jsonify({"status": "group_ignored"}), 200

            numero_limpo = remote_jid.split("@")[0]

            # --- 2. LÓGICA DE NÚMEROS BLOQUEADOS ---
            numeros_bloqueados = carregar_numeros_bloqueados()
            if numero_limpo in numeros_bloqueados:
                print(f"Mensagem bloqueada do número: {numero_limpo}")
                return jsonify({"status": "blocked_number"}), 200

            # Extrai o conteúdo da mensagem
            message_content = message_data.get("message", {})
            texto_mensagem = message_content.get("conversation") or message_content.get("extendedTextMessage", {}).get(
                "text", "")

            if texto_mensagem and numero_limpo:
                resposta_bot = consultar_excel(texto_mensagem)
                enviar_mensagem_whatsapp(numero_limpo, resposta_bot)

    except Exception as e:
        print(f"Erro no webhook: {e}")

    return jsonify({"status": "success"}), 200


if __name__ == "__main__":
    porta = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=porta)