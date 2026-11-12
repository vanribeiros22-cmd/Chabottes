import os
import re
import pandas as pd
import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

ARQUIVO_EXCEL = "Diretorio_Academico_n8n.xlsx"

# ---------------------------------------------------------
# CARREGAMENTO E TRATAMENTO DA PLANILHA EXCEL
# ---------------------------------------------------------
print("=== CARREGANDO BANCO DE DADOS EXCEL ===")
try:
    df_menu = pd.read_excel(ARQUIVO_EXCEL, sheet_name="Menu_Principal")
    df_faq = pd.read_excel(ARQUIVO_EXCEL, sheet_name="FAQ_BaseNumero_Menu")

    # Carregamento seguro da aba de contatos bloqueados
    try:
        df_bloqueados = pd.read_excel(ARQUIVO_EXCEL, sheet_name="Contatos_Bloqueados")
        df_bloqueados.columns = df_bloqueados.columns.str.strip()
    except Exception:
        df_bloqueados = pd.DataFrame(columns=["Telefone"])

    df_menu.columns = df_menu.columns.str.strip()
    df_faq.columns = df_faq.columns.str.strip()

    # Tratamento da coluna numérica do FAQ para evitar erros de tipo
    col_faq_num = "Numero_Menu"
    if col_faq_num in df_faq.columns:
        df_faq[col_faq_num] = df_faq[col_faq_num].astype(str).str.strip()
        df_faq[col_faq_num] = df_faq[col_faq_num].str.replace(r"\.0$", "", regex=True)
        df_faq[col_faq_num] = df_faq[col_faq_num].str.replace("'", "").str.replace('"', "")

    print("Abas carregadas com sucesso!")
except Exception as e:
    print(f"❌ Erro ao ler a planilha: {e}")
    df_menu = pd.DataFrame()
    df_faq = pd.DataFrame()
    df_bloqueados = pd.DataFrame()

# Dicionário para controlar o estado da conversa de cada usuário
status_usuarios = {}


def obter_telefones_bloqueados():
    """Lê dinamicamente os números bloqueados da aba Contatos_Bloqueados."""
    if not df_bloqueados.empty and "Telefone" in df_bloqueados.columns:
        return (
            df_bloqueados["Telefone"]
            .astype(str)
            .str.replace(r"\.0$", "", regex=True)
            .str.strip()
            .tolist()
        )
    return []


def limpar_texto(texto):
    """Remove quebras de linha em excesso e espaços duplicados."""
    if not isinstance(texto, str):
        return str(texto)
    texto_ajustado = re.sub(r"\n{3,}", "\n\n", texto)
    texto_ajustado = re.sub(r"[ \t]{2,}", " ", texto_ajustado)
    return texto_ajustado.strip()


def processar_resposta(telefone, texto_mensagem):
    texto_limpo = str(texto_mensagem).strip().replace(" ", "").replace("'", "").replace('"', "")
    estado_atual = status_usuarios.get(telefone, "novo")

    # Se a conversa foi transferida para um humano, o bot silencia
    if estado_atual == "humano":
        return None

    # Se o atendimento foi encerrado, o bot só acorda se receber saudação explícita
    palavras_saudacao = ["menu", "oi", "olá", "ola", "bomdia", "boatarde", "boanoite", "reiniciar", "iniciar"]
    if estado_atual == "encerrado" and texto_limpo.lower() not in palavras_saudacao:
        return None

    # 1. Menu Principal / Reativação
    if texto_limpo.lower() in palavras_saudacao:
        status_usuarios[telefone] = "ativo"
        try:
            texto_menu = (
                "Olá! Sou a Sofia, assistente virtual do Diretório Acadêmico da Faculdade Cetrus. ✨\n\n"
                "Escolha uma opção digitando o número correspondente:\n\n"
            )
            col_num = "Numero" if "Numero" in df_menu.columns else df_menu.columns[0]
            col_opc = "Opcao" if "Opcao" in df_menu.columns else df_menu.columns[1]

            for _, linha in df_menu.iterrows():
                texto_menu += f"*{linha[col_num]}* - {linha[col_opc]}\n"
            return {"tipo": "texto", "conteudo": texto_menu}
        except Exception as e:
            return {"tipo": "texto", "conteudo": f"Erro ao gerar menu: {e}"}

    # 2. Transferência para Atendimento Humano
    if texto_limpo in ["5", "humano", "atendente"]:
        status_usuarios[telefone] = "humano"
        print(f"📊 [MÉTRICA] O usuário {telefone} foi direcionado para Atendimento Humano.")
        return {
            "tipo": "texto",
            "conteudo": "Entendido! O atendimento automático foi pausado. Um atendente humano assumirá esta conversa em breve.",
        }

    # 3. Encerramento Formal
    if texto_limpo in ["0", "sair", "encerrar", "fim"]:
        status_usuarios[telefone] = "encerrado"
        return {
            "tipo": "texto",
            "conteudo": (
                "Atendimento finalizado com sucesso! ✅\n\n"
                "Caso precise de algo mais no futuro, envie *Menu* a qualquer momento para iniciar uma nova conversa. Até logo!"
            ),
        }

    # 4. Navegação por Submenus e Mídias
    try:
        col_faq_num = "Numero_Menu"

        # Submenu (Ex: usuário digita "1")
        if texto_limpo.isdigit() and len(texto_limpo) == 1:
            opcao_pai = texto_limpo
            sub_faq = df_faq[df_faq[col_faq_num].str.startswith(f"{opcao_pai}.")]

            if not sub_faq.empty:
                texto_submenu = "*Submenu - Escolha o assunto desejado:*\n\n"
                for _, linha in sub_faq.iterrows():
                    num_sub = linha[col_faq_num]
                    processo = linha.get("Processo", "Informação")
                    texto_submenu += f"*{num_sub}* - {processo}\n"

                texto_submenu += f"\nDigite o número completo (ex: *{opcao_pai}.1*) para ver os detalhes."
                return {"tipo": "texto", "conteudo": texto_submenu}

        # Resposta Final com suporte a Mídia (Ex: usuário digita "1.1")
        sub_faq_exato = df_faq[df_faq[col_faq_num] == texto_limpo]

        if not sub_faq_exato.empty:
            linha = sub_faq_exato.iloc[0]
            processo = linha.get("Processo", "Informação")
            instrucoes = linha.get("Instrucoes_Tecnicas", linha.get("Instrucoes_tecnicas", "Sem instruções"))
            link_arquivo = linha.get("Link_Arquivo", "")

            # Verifica se existe link de arquivo válido na planilha
            if pd.notna(link_arquivo) and str(link_arquivo).strip().startswith("http"):
                url_limpa = str(link_arquivo).strip()
                is_pdf = url_limpa.lower().endswith(".pdf")
                tipo_midia = "document" if is_pdf else "image"
                extensao = ".pdf" if is_pdf else ".jpg"

                return {
                    "tipo": "midia",
                    "mediatype": tipo_midia,
                    "url": url_limpa,
                    "fileName": f"{processo}{extensao}",
                    "caption": f"🔹 *{processo}*:\n\n{limpar_texto(instrucoes)}",
                }

            return {"tipo": "texto", "conteudo": f"🔹 *{processo}*:\n\n{limpar_texto(instrucoes)}"}

    except Exception as e:
        print(f"Erro ao processar busca: {e}")

    return {
        "tipo": "texto",
        "conteudo": "Opção não reconhecida. Digite *Menu* para visualizar as opções disponíveis.",
    }


# Rota para Keep-Alive (Mantém o app acordado no Render gratuitamente)
@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "active", "bot": "Sofia-Testes"}), 200


# Rota principal do Webhook
@app.route("/webhook", methods=["POST"])
def webhook():
    payload = request.json
    if not payload:
        return jsonify({"status": "no_payload"}), 200

    data = payload.get("data", {})
    if not data and "message" in payload:
        data = payload

    # Ignora mensagens enviadas pelo próprio robô
    if data.get("key", {}).get("fromMe") is True:
        return jsonify({"status": "ignored_self"}), 200

    remote_jid = data.get("key", {}).get("remoteJid", payload.get("sender", ""))

    # REQUISITO 1: Ignora qualquer grupo do WhatsApp (@g.us)
    if "@g.us" in remote_jid:
        return jsonify({"status": "ignored_group"}), 200

    telefone_aluno = remote_jid.split("@")[0] if "@" in remote_jid else remote_jid

    # REQUISITO 2: Ignora números cadastrados na aba de bloqueio da planilha
    if telefone_aluno in obter_telefones_bloqueados():
        return jsonify({"status": "ignored_blocked_contact"}), 200

    # Extração segura do texto enviado pelo usuário
    message_obj = data.get("message", {})
    texto_aluno = ""
    if isinstance(message_obj, dict):
        texto_aluno = message_obj.get("conversation", "")
        if not texto_aluno:
            texto_aluno = message_obj.get("extendedTextMessage", {}).get("text", "")
    if not texto_aluno:
        texto_aluno = data.get("message", {}).get("text", "")
    if not texto_aluno:
        texto_aluno = data.get("body", payload.get("body", ""))

    if not texto_aluno:
        return jsonify({"status": "texto_nao_encontrado"}), 200

    resposta = processar_resposta(telefone_aluno, texto_aluno)
    if not resposta:
        return jsonify({"status": "sem_resposta"}), 200

    url_evolution = os.environ.get("URL_EVOLUTION")
    api_key_evolution = os.environ.get("API_KEY_EVOLUTION")
    instance_name = payload.get("instance", "instancia_cetrus")
    if isinstance(instance_name, dict):
        instance_name = instance_name.get("instanceName", "instancia_cetrus")

    if not (url_evolution and api_key_evolution):
        return jsonify({"status": "credentials_missing"}), 500

    headers = {"Content-Type": "application/json", "apikey": api_key_evolution}

    # REQUISITO 3: Disparo de Mídia (PDF / Fotos) via Evolution API
    if resposta.get("tipo") == "midia":
        url_envio = f"{url_evolution}/message/sendMedia/{instance_name}"
        body = {
            "number": str(telefone_aluno),
            "mediaMessage": {
                "mediatype": resposta.get("mediatype", "document"),
                "fileName": resposta.get("fileName", "Documento.pdf"),
                "caption": resposta.get("caption", ""),
                "media": resposta.get("url"),
            },
        }
        try:
            requests.post(url_envio, json=body, headers=headers, timeout=15)
        except Exception as e:
            print(f"Erro ao enviar mídia: {e}")

    # Disparo de Texto Simples
    elif resposta.get("tipo") == "texto":
        url_envio = f"{url_evolution}/message/sendText/{instance_name}"
        body = {"number": str(telefone_aluno), "text": str(resposta.get("conteudo")), "delay": 300}
        try:
            requests.post(url_envio, json=body, headers=headers, timeout=10)
        except Exception as e:
            print(f"Erro ao enviar texto: {e}")

    return jsonify({"status": "success", "msg_processada": texto_aluno}), 200


if __name__ == "__main__":
    porta = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=porta)