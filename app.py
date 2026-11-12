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

# Lista de números bloqueados (pode ser carregada do Excel ou mantida aqui)
# Exemplo: números no formato internacional sem símbolos
NUMEROS_BLOQUEADOS = ["55119970345241", "5511915026540"]


def verificar_bloqueio(numero):
    """
    Verifica se o número do remetente consta na lista de bloqueados.
    """
    return numero in NUMEROS_BLOQUEADOS


def consultar_excel(pergunta):
    """
    Consulta o ficheiro Excel do diretório académico com base na pergunta do utilizador.
    """
    try:
        if not os.path.exists(EXCEL_FILE):
            return "⚠️ Erro: Ficheiro de diretório académico não encontrado no servidor."

        df = pd.read_excel(EXCEL_FILE)
        pergunta_lower = pergunta.lower()

        for index, row in df.iterrows():
            if any(str(val).lower() in pergunta_lower for val in row.values):
                return f"encontrei esta informação:\n" + "\n".join([f"- {col}: {row[col]}" for col in df.columns])

        return "Desculpa, não encontrei nenhuma informação correspondente no diretório académico. Podes tentar reformular a pergunta?"

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
            # Se o remoteJid terminar com '@g.us', é uma mensagem de grupo.
            # Decidimos ignorar mensagens de grupo para que a Sofia responda apenas no privado.
            if remote_jid.endswith("@g.us"):
                print(f"Mensagem de grupo ignorada: {remote_jid}")
                return jsonify({"status": "group_ignored"}), 200

            # Extrai o número limpo do remetente privado
            numero_limpo = remote_jid.split("@")[0]

            # --- 2. LÓGICA DE NÚMEROS BLOQUEADOS ---
            if verificar_bloqueio(numero_limpo):
                print(f"Mensagem bloqueada do número: {numero_limpo}")
                return jsonify({"status": "blocked_number"}), 200

            # Extrai o conteúdo da mensagem
            message_content = message_data.get("message", {})
            texto_mensagem = message_content.get("conversation") or message_content.get("extendedTextMessage", {}).get(
                "text", "")

            if texto_mensagem and numero_limpo:
                # Consulta o Excel e envia a resposta
                resposta_bot = consultar_excel(texto_mensagem)
                enviar_mensagem_whatsapp(numero_limpo, resposta_bot)

    except Exception as e:
        print(f"Erro no webhook: {e}")

    return jsonify({"status": "success"}), 200


if __name__ == "__main__":
    porta = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=porta)