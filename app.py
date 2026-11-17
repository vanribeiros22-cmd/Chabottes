from flask import Flask, request, jsonify
import pandas as pd
import requests
import os

app = Flask(__name__)

# ==============================================================================
# 1. CONFIGURAÇÕES DA EVOLUTION API E DO ARQUIVO DE DADOS
# ==============================================================================
EVOLUTION_API_URL = "https://evolution-api-production-db931012.up.railway.app"
INSTANCE_NAME = "sofia-bot"
API_KEY = "cetrus2026teste"

EXCEL_FILE = "diretorio_academico.xlsx"


# ==============================================================================
# 2. FUNÇÕES AUXILIARES (BLOQUEIO E FORMATAÇÃO)
# ==============================================================================
def carregar_numeros_bloqueados():
    """
    Lê a aba 'TelefoneContatos_Bloqueados' do Excel e retorna uma lista
    com os números que o bot deve ignorar.
    """
    try:
        if not os.path.exists(EXCEL_FILE):
            return []
        df_bloq = pd.read_excel(EXCEL_FILE, sheet_name="TelefoneContatos_Bloqueados")
        return [str(num).strip() for num in df_bloq["Telefone"].dropna().tolist()]
    except Exception as e:
        print(f"Erro ao carregar números bloqueados: {e}")
        return []


def formatar_resposta(row):
    """
    Monta a mensagem final da resposta com título, instruções, link (se houver)
    e opções claras de navegação no rodapé.
    """
    cat = row.get('Categoria ', '')
    proc = row.get('Processo ', '')
    resp = row.get('Instrucoes_Tecnicas', 'Consulte os detalhes no portal.')
    link_arquivo = row.get('Link_Arquivo', '')

    texto_final = f"📌 *{cat} — {proc}*\n\n{resp}"

    # Se existir um link na coluna Link_Arquivo, anexa à mensagem
    if pd.notna(link_arquivo) and str(link_arquivo).strip() != "":
        texto_final += f"\n\n📂 *Material / Documento Associado:*\n{str(link_arquivo).strip()}"

    # Adiciona orientação clara ao final de cada resposta
    texto_final += (
        "\n\n-----------------------------------\n"
        "💡 *Como continuar:*\n"
        "• Digite *menu* para voltar ao início\n"
        "• Digite *0* para encerrar o atendimento"
    )
    return texto_final


# ==============================================================================
# 3. MOTOR DE CONSULTA AO EXCEL (MENUS, SUBMENUS E ENCERRAMENTO)
# ==============================================================================
def consultar_excel(pergunta):
    """
    Processa a mensagem recebida do aluno e decide qual resposta enviar:
    encerramento, menu principal, lista de submenus ou resposta específica.
    """
    try:
        if not os.path.exists(EXCEL_FILE):
            return "⚠️ Erro: Arquivo de diretório acadêmico não encontrado no servidor."

        pergunta_limpa = pergunta.strip().lower()

        # --- PASSO A: COMANDO DE ENCERRAMENTO GLOBAL ---
        comandos_saida = ["0", "encerrar", "sair", "encerrar atendimento"]
        if pergunta_limpa in comandos_saida:
            return (
                "Atendimento encerrado. Obrigado pelo contato! 💙\n\n"
                "Se precisar de algo, é só enviar uma nova mensagem a qualquer momento."
            )

        # --- PASSO B: SAUDAÇÃO OU MENU PRINCIPAL ---
        comandos_menu = ["oi", "ola", "olá", "bom dia", "boa tarde", "boa noite", "menu", "ajuda"]
        if pergunta_limpa in comandos_menu:
            df_menu = pd.read_excel(EXCEL_FILE, sheet_name="Menu_Principal")
            menu_texto = (
                "👋 Bem-vinda(o) ao atendimento acadêmico da Sofia!\n\n"
                "Escolha uma das opções abaixo digitando apenas o *número* correspondente:\n\n"
            )
            for _, row in df_menu.iterrows():
                menu_texto += f"• *{int(row['Numero'])}* - {row['Opcao']}\n"
            return menu_texto

        df_faq = pd.read_excel(EXCEL_FILE, sheet_name="FAQ_BaseNumero_Menu")

        # --- PASSO C: ESCOLHA DE UMA OPÇÃO DO MENU PRINCIPAL (1 a 6) ---
        if pergunta_limpa.isdigit() and len(pergunta_limpa) == 1:
            num_opcao = float(pergunta_limpa)
            filtrado = df_faq[df_faq['Numero_Menu'] == num_opcao]

            if not filtrado.empty:
                # Se a opção tiver apenas 1 item (ex: Opção 5 - Atendimento Humano), envia direto
                if len(filtrado) == 1:
                    return formatar_resposta(filtrado.iloc[0])

                # Se tiver vários submenus, lista todos com instruções bem visíveis
                cat_nome = filtrado.iloc[0].get('Categoria ', 'Atendimento')
                sub_texto = (
                    f"📌 *{cat_nome}*\n\n"
                    f"Digite o número da opção desejada (exemplo: *{int(num_opcao)}.1*):\n\n"
                )

                for _, row in filtrado.iterrows():
                    sub_num = int(row.get('Numero_Submenu', 1))
                    proc = row.get('Processo ', '')
                    sub_texto += f"• *{int(num_opcao)}.{sub_num}* — {proc}\n"

                sub_texto += (
                    "\n-----------------------------------\n"
                    "💡 *Outras opções:*\n"
                    "• Digite *menu* para voltar ao menu principal\n"
                    "• Digite *0* para encerrar o atendimento"
                )
                return sub_texto

        # --- PASSO D: ESCOLHA DE UM SUBMENU ESPECÍFICO (ex: 1.1, 2.3, 6.4) ---
        if "." in pergunta_limpa:
            try:
                partes = pergunta_limpa.split(".")
                menu_num = float(partes[0])
                sub_num = float(partes[-1])

                encontrado = df_faq[(df_faq['Numero_Menu'] == menu_num) & (df_faq['Numero_Submenu'] == sub_num)]
                if not encontrado.empty:
                    return formatar_resposta(encontrado.iloc[0])
            except Exception as e:
                print(f"Erro ao processar submenu: {e}")

        # --- PASSO E: BUSCA POR PALAVRAS-CHAVE DIGITADAS ---
        for _, row in df_faq.iterrows():
            categoria = str(row.get("Categoria ", "")).lower()
            processo = str(row.get("Processo ", "")).lower()
            palavras_chave = str(row.get("Palavras_Chave", "")).lower()

            if (pergunta_limpa in palavras_chave) or (pergunta_limpa in processo) or (pergunta_limpa in categoria):
                return formatar_resposta(row)

        # --- PASSO F: MENSAGEM QUANDO NÃO ENTENDE O QUE FOI DIGITADO ---
        return (
            "Desculpe, não encontrei essa opção no diretório acadêmico. 🤔\n\n"
            "• Digite *menu* para ver as opções disponíveis\n"
            "• Digite *0* para encerrar o atendimento"
        )

    except Exception as e:
        return f"Ocorreu um erro ao processar os dados acadêmicos: {str(e)}"


# ==============================================================================
# 4. ENVIO DE MENSAGENS E ROTA DO WEBHOOK
# ==============================================================================
def enviar_mensagem_whatsapp(numero_destino, texto_resposta):
    """
    Envia o texto formatado de volta para o WhatsApp do aluno via Evolution API.
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
    Recebe as notificações de novas mensagens da Evolution API.
    """
    dados = request.json

    try:
        evento = dados.get("event", "")
        if evento == "messages.upsert":
            message_data = dados.get("data", {})
            remote_jid = message_data.get("key", {}).get("remoteJid", "")

            # Ignora mensagens enviadas pelo próprio bot
            if message_data.get("key", {}).get("fromMe", False):
                return jsonify({"status": "ignored"}), 200

            # Ignora mensagens de grupos (@g.us)
            if remote_jid.endswith("@g.us"):
                return jsonify({"status": "group_ignored"}), 200

            numero_limpo = remote_jid.split("@")[0]

            # Ignora contatos presentes na aba de bloqueados do Excel
            numeros_bloqueados = carregar_numeros_bloqueados()
            if numero_limpo in numeros_bloqueados:
                return jsonify({"status": "blocked_number"}), 200

            # Extrai o texto da mensagem
            message_content = message_data.get("message", {})
            texto_mensagem = (
                    message_content.get("conversation")
                    or message_content.get("extendedTextMessage", {}).get("text", "")
            )

            if texto_mensagem and numero_limpo:
                resposta_bot = consultar_excel(texto_mensagem)
                enviar_mensagem_whatsapp(numero_limpo, resposta_bot)

    except Exception as e:
        print(f"Erro no webhook: {e}")

    return jsonify({"status": "success"}), 200


if __name__ == "__main__":
    porta = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=porta)