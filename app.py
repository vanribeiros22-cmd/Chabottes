from flask import Flask, request, jsonify, send_file
import pandas as pd
import requests
import os
from datetime import datetime, timedelta, timezone

app = Flask(__name__)

# ==============================================================================
# 1. CONFIGURAÇÕES DA EVOLUTION API E DO ARQUIVO DE DADOS
# ==============================================================================
EVOLUTION_API_URL = "https://evolution-api-production-db931012.up.railway.app"
INSTANCE_NAME = "sofia-bot"
API_KEY = "cetrus2026teste"

EXCEL_FILE = "diretorio_academico.xlsx"
FUSO_BR = timezone(timedelta(hours=-3))

# Memória rápida em conjunto com o Excel para garantir zero atraso na pausa
pausa_memoria = set()


# ==============================================================================
# 2. FUNÇÕES AUXILIARES (NORMALIZAÇÃO, BLOQUEIO, PAUSA E MÉTRICAS)
# ==============================================================================
def normalizar_telefone(numero):
    """
    Padroniza o número de telefone (DDD + últimos 8 dígitos) para evitar
    falhas entre o número recebido e o enviado com/sem o 9º dígito.
    """
    apenas_digitos = "".join(filter(str.isdigit, str(numero)))
    if len(apenas_digitos) >= 10:
        return apenas_digitos[-10:]
    return apenas_digitos


def agora_brasil():
    """Retorna data e hora atual formatada no horário de Brasília."""
    return datetime.now(FUSO_BR).strftime("%d/%m/%Y %H:%M:%S")


def carregar_numeros_bloqueados():
    """Lê a aba 'TelefoneContatos_Bloqueados' e retorna os números normalizados."""
    try:
        if not os.path.exists(EXCEL_FILE):
            return []
        df_bloq = pd.read_excel(EXCEL_FILE, sheet_name="TelefoneContatos_Bloqueados")
        return [normalizar_telefone(num) for num in df_bloq["Telefone"].dropna().tolist()]
    except Exception as e:
        print(f"[ERRO] Falha ao carregar números bloqueados: {e}")
        return []


def gerenciar_estado_humano(numero_norm, acao="verificar"):
    """
    Salva e consulta na memória e na aba 'Controle_Alunos' do Excel se o aluno
    está em atendimento humano (bot pausado).
    """
    if not numero_norm:
        return False

    # Verifica primeiro na memória rápida
    if acao == "verificar" and numero_norm in pausa_memoria:
        return True

    try:
        if not os.path.exists(EXCEL_FILE):
            return numero_norm in pausa_memoria

        todas_abas = pd.read_excel(EXCEL_FILE, sheet_name=None)
        df_controle = todas_abas.get(
            "Controle_Alunos",
            pd.DataFrame(columns=["ID_Usuario", "Nome_Aluno", "Status", "Ultima_Interacao", "Etapa", "Humano"])
        )
        df_controle["ID_Usuario"] = df_controle["ID_Usuario"].astype(str)

        if acao == "verificar":
            reg = df_controle[df_controle["ID_Usuario"] == numero_norm]
            if not reg.empty:
                esta_humano = str(reg.iloc[-1]["Humano"]).strip().upper() == "SIM"
                if esta_humano:
                    pausa_memoria.add(numero_norm)
                return esta_humano
            return False

        elif acao in ["ativar", "desativar"]:
            if acao == "ativar":
                pausa_memoria.add(numero_norm)
                print(f"[PAUSA ATIVADA] Bot suspenso para o número: {numero_norm}")
            else:
                pausa_memoria.discard(numero_norm)
                print(f"[PAUSA REMOVIDA] Bot reativado para o número: {numero_norm}")

            status_humano = "SIM" if acao == "ativar" else "NAO"
            etapa_atual = "Atendimento Humano" if acao == "ativar" else "Finalizado"

            if numero_norm in df_controle["ID_Usuario"].values:
                df_controle.loc[df_controle["ID_Usuario"] == numero_norm, "Humano"] = status_humano
                df_controle.loc[df_controle["ID_Usuario"] == numero_norm, "Ultima_Interacao"] = agora_brasil()
                df_controle.loc[df_controle["ID_Usuario"] == numero_norm, "Etapa"] = etapa_atual
            else:
                nova_linha = pd.DataFrame([{
                    "ID_Usuario": numero_norm,
                    "Nome_Aluno": "Aluno WhatsApp",
                    "Status": "Ativo",
                    "Ultima_Interacao": agora_brasil(),
                    "Etapa": etapa_atual,
                    "Humano": status_humano
                }])
                df_controle = pd.concat([df_controle, nova_linha], ignore_index=True)

            todas_abas["Controle_Alunos"] = df_controle
            with pd.ExcelWriter(EXCEL_FILE, engine="openpyxl") as writer:
                for nome_aba, df_aba in todas_abas.items():
                    df_aba.to_excel(writer, sheet_name=nome_aba, index=False)
            return True

    except Exception as e:
        print(f"[ERRO] Falha ao gravar estado humano no Excel: {e}")
        return numero_norm in pausa_memoria


def registrar_metrica(numero_aluno, categoria, processo, duvida, desfecho, resolvido="Sim"):
    """
    Grava automaticamente cada interação na aba 'Metricas_Atendimentos ' do Excel.
    """
    try:
        if not os.path.exists(EXCEL_FILE):
            return

        todas_abas = pd.read_excel(EXCEL_FILE, sheet_name=None)
        nome_aba_metricas = "Metricas_Atendimentos " if "Metricas_Atendimentos " in todas_abas else "Metricas_Atendimentos"

        df_met = todas_abas.get(
            nome_aba_metricas,
            pd.DataFrame(columns=["ID_Log", "Data_Hora", "ID_Usuario", "Canal", "Categoria", "Processo", "Duvida_Aluno",
                                  "Desfecho", "Resolvido"])
        )

        novo_id = len(df_met) + 1
        nova_linha = pd.DataFrame([{
            "ID_Log": novo_id,
            "Data_Hora": agora_brasil(),
            "ID_Usuario": numero_aluno,
            "Canal": "WhatsApp",
            "Categoria": categoria,
            "Processo": processo,
            "Duvida_Aluno": duvida,
            "Desfecho": desfecho,
            "Resolvido": resolvido
        }])

        todas_abas[nome_aba_metricas] = pd.concat([df_met, nova_linha], ignore_index=True)

        with pd.ExcelWriter(EXCEL_FILE, engine="openpyxl") as writer:
            for nome_aba, df_aba in todas_abas.items():
                df_aba.to_excel(writer, sheet_name=nome_aba, index=False)

    except Exception as e:
        print(f"[ERRO] Falha ao registrar métrica: {e}")


def formatar_resposta(row):
    """Monta a mensagem final da resposta com título, instruções e rodapé."""
    cat = row.get('Categoria ', '')
    proc = row.get('Processo ', '')
    resp = row.get('Instrucoes_Tecnicas', 'Consulte os detalhes no portal.')
    link_arquivo = row.get('Link_Arquivo', '')

    texto_final = f"📌 *{cat} — {proc}*\n\n{resp}"

    if pd.notna(link_arquivo) and str(link_arquivo).strip() != "":
        texto_final += f"\n\n📂 *Material / Documento Associado:*\n{str(link_arquivo).strip()}"

    texto_final += (
        "\n\n-----------------------------------\n"
        "💡 *Como continuar:*\n"
        "• Digite *menu* para voltar ao início\n"
        "• Digite *0* para encerrar o atendimento"
    )
    return texto_final


# ==============================================================================
# 3. MOTOR DE CONSULTA AO EXCEL (MENUS, SUBMENUS, PAUSA E ENCERRAMENTO)
# ==============================================================================
def consultar_excel(pergunta, numero_aluno_norm):
    """Processa a mensagem do aluno, registra métricas e retorna a resposta."""
    try:
        if not os.path.exists(EXCEL_FILE):
            return "⚠️ Erro: Arquivo de diretório acadêmico não encontrado no servidor."

        pergunta_limpa = pergunta.strip().lower()

        # --- PASSO A: COMANDO DE ENCERRAMENTO GLOBAL ---
        comandos_saida = ["0", "encerrar", "sair", "encerrar atendimento", "#finalizar"]
        if pergunta_limpa in comandos_saida:
            gerenciar_estado_humano(numero_aluno_norm, acao="desativar")
            registrar_metrica(numero_aluno_norm, "Encerramento", "Saída do Usuário", pergunta, "Atendimento Encerrado",
                              "Sim")
            return (
                "Atendimento encerrado. Obrigado pelo contato! 💙\n\n"
                "Se precisar de algo, é só enviar uma nova mensagem a qualquer momento."
            )

        # --- PASSO B: SAUDAÇÃO OU MENU PRINCIPAL ---
        comandos_menu = ["oi", "ola", "olá", "bom dia", "boa tarde", "boa noite", "menu", "ajuda"]
        if pergunta_limpa in comandos_menu:
            df_menu = pd.read_excel(EXCEL_FILE, sheet_name="Menu_Principal")
            registrar_metrica(numero_aluno_norm, "Menu Principal", "Início / Boas-vindas", pergunta,
                              "Exibiu Menu Principal", "Sim")
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

            # Opção 5: Transferência para Atendimento Humano (Ativa Pausa no Excel)
            if num_opcao == 5.0:
                gerenciar_estado_humano(numero_aluno_norm, acao="ativar")
                registrar_metrica(numero_aluno_norm, "Atendimento Humano", "Transbordo Humano", pergunta,
                                  "Transferido para Atendente", "Em andamento")
                filtrado_5 = df_faq[df_faq['Numero_Menu'] == 5.0]
                msg_humano = "👩‍💻 *Transferindo para o Atendimento Humano...*\n\n"
                if not filtrado_5.empty:
                    msg_humano += str(filtrado_5.iloc[0].get('Instrucoes_Tecnicas',
                                                             'Aguarde um momento, um de nossos atendentes já vai falar com você.'))
                else:
                    msg_humano += "Aguarde um momento, um de nossos atendentes já vai falar com você."

                msg_humano += "\n\n_(O robô ficará pausado durante este atendimento)_"
                return msg_humano

            filtrado = df_faq[df_faq['Numero_Menu'] == num_opcao]

            if not filtrado.empty:
                if len(filtrado) == 1:
                    row_unica = filtrado.iloc[0]
                    registrar_metrica(numero_aluno_norm, row_unica.get('Categoria ', ''),
                                      row_unica.get('Processo ', ''), pergunta, "Resposta Direta", "Sim")
                    return formatar_resposta(row_unica)

                cat_nome = filtrado.iloc[0].get('Categoria ', 'Atendimento')
                registrar_metrica(numero_aluno_norm, cat_nome, "Listagem Submenu", pergunta, "Exibiu Submenus", "Sim")
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

                if menu_num == 5.0:
                    gerenciar_estado_humano(numero_aluno_norm, acao="ativar")

                encontrado = df_faq[(df_faq['Numero_Menu'] == menu_num) & (df_faq['Numero_Submenu'] == sub_num)]
                if not encontrado.empty:
                    row = encontrado.iloc[0]
                    registrar_metrica(numero_aluno_norm, row.get('Categoria ', ''), row.get('Processo ', ''), pergunta,
                                      "Resposta Submenu", "Sim")
                    return formatar_resposta(row)
            except Exception as e:
                print(f"[ERRO] Falha ao processar submenu: {e}")

        # --- PASSO E: BUSCA POR PALAVRAS-CHAVE DIGITADAS ---
        for _, row in df_faq.iterrows():
            categoria = str(row.get("Categoria ", "")).lower()
            processo = str(row.get("Processo ", "")).lower()
            palavras_chave = str(row.get("Palavras_Chave", "")).lower()

            if (pergunta_limpa in palavras_chave) or (pergunta_limpa in processo) or (pergunta_limpa in categoria):
                registrar_metrica(numero_aluno_norm, row.get('Categoria ', ''), row.get('Processo ', ''), pergunta,
                                  "Busca Palavra-Chave", "Sim")
                return formatar_resposta(row)

        # --- PASSO F: MENSAGEM QUANDO NÃO ENTENDE ---
        registrar_metrica(numero_aluno_norm, "Não Identificado", "Fallback", pergunta, "Opção não encontrada", "Não")
        return (
            "Desculpe, não encontrei essa opção no diretório acadêmico. 🤔\n\n"
            "• Digite *menu* para ver as opções disponíveis\n"
            "• Digite *0* para encerrar o atendimento"
        )

    except Exception as e:
        return f"Ocorreu um erro ao processar os dados acadêmicos: {str(e)}"


# ==============================================================================
# 4. ENVIO DE MENSAGENS, DOWNLOAD DE MÉTRICAS E ROTA DO WEBHOOK
# ==============================================================================
def enviar_mensagem_whatsapp(numero_destino, texto_resposta):
    """Envia o texto formatado de volta para o WhatsApp via Evolution API."""
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
        print(f"[ERRO] Falha ao enviar mensagem: {e}")
        return None


@app.route("/metricas", methods=["GET"])
def baixar_metricas():
    """
    Rota para baixar a planilha Excel atualizada com todas as métricas
    e controle de atendimento registrados até o momento.
    """
    if os.path.exists(EXCEL_FILE):
        return send_file(
            EXCEL_FILE,
            as_attachment=True,
            download_name=f"relatorio_sofia_{datetime.now(FUSO_BR).strftime('%Y%m%d_%H%M')}.xlsx"
        )
    return jsonify({"erro": "Planilha não encontrada no servidor."}), 404


@app.route("/webhook", methods=["POST"])
def webhook():
    """Recebe as notificações de novas mensagens da Evolution API."""
    dados = request.json

    try:
        evento = dados.get("event", "")

        # Escuta tanto mensagens recebidas/enviadas (messages.upsert) quanto envios diretos (send.message)
        if evento in ["messages.upsert", "send.message"]:
            message_data = dados.get("data", {})
            key = message_data.get("key", {})
            remote_jid = key.get("remoteJid", "")

            # Se for send.message, significa que foi enviado pelo próprio aparelho da instituição
            from_me = True if evento == "send.message" else key.get("fromMe", False)

            if remote_jid.endswith("@g.us"):
                return jsonify({"status": "group_ignored"}), 200

            numero_bruto = remote_jid.split("@")[0]
            numero_norm = normalizar_telefone(numero_bruto)

            message_content = message_data.get("message", {})
            texto_mensagem = (
                    message_content.get("conversation")
                    or message_content.get("extendedTextMessage", {}).get("text", "")
            )

            print(f"[WEBHOOK] Evento: {evento} | DeMim: {from_me} | Num: {numero_norm} | Texto: {texto_mensagem}")

            if not texto_mensagem:
                return jsonify({"status": "no_text"}), 200

            # --- VERIFICAÇÃO DO ATENDENTE HUMANO (MENSAGEM ENVIADA PELO APARELHO DA INSTITUIÇÃO) ---
            if from_me:
                # 1. Se o atendente enviar "#finalizar", reativa o robô e encerra!
                if "#finalizar" in texto_mensagem.lower():
                    gerenciar_estado_humano(numero_norm, acao="desativar")
                    registrar_metrica(numero_norm, "Atendimento Humano", "Finalização Atendente", "#finalizar",
                                      "Atendimento Humano Concluído", "Sim")
                    enviar_mensagem_whatsapp(
                        numero_bruto,
                        "Atendimento humano finalizado. Obrigado pelo contato! 💙\n\nSe precisar de algo novamente, basta enviar *oi* ou *menu*."
                    )
                # 2. Se o atendente digitar manualmente uma mensagem (que não seja gerada pelo próprio robô),
                # coloca o chat em pausa automaticamente para o robô não atrapalhar!
                elif not texto_mensagem.startswith(
                        ("👋", "📌", "👩‍💻", "Desculpe,", "Atendimento encerrado", "Atendimento humano")):
                    gerenciar_estado_humano(numero_norm, acao="ativar")
                return jsonify({"status": "from_me_processed"}), 200

            # Ignora contatos presentes na aba de bloqueados
            numeros_bloqueados = carregar_numeros_bloqueados()
            if numero_norm in numeros_bloqueados:
                return jsonify({"status": "blocked_number"}), 200

            # --- VERIFICAÇÃO DE PAUSA (SE ESTIVER COM HUMANO, O ROBÔ FICA EM SILÊNCIO) ---
            if gerenciar_estado_humano(numero_norm, acao="verificar"):
                if texto_mensagem.strip().lower() in ["0", "#finalizar"]:
                    gerenciar_estado_humano(numero_norm, acao="desativar")
                    registrar_metrica(numero_norm, "Atendimento Humano", "Encerrado pelo Aluno", texto_mensagem,
                                      "Encerrado pelo Aluno", "Sim")
                    enviar_mensagem_whatsapp(
                        numero_bruto,
                        "Atendimento encerrado. Obrigado pelo contato! 💙\n\nSe precisar de algo, é só enviar uma nova mensagem a qualquer momento."
                    )
                else:
                    print(f"[SILÊNCIO] Bot pausado para {numero_norm} (em atendimento humano).")
                return jsonify({"status": "paused_for_human"}), 200

            # Processa fluxo normal do bot
            resposta_bot = consultar_excel(texto_mensagem, numero_norm)
            enviar_mensagem_whatsapp(numero_bruto, resposta_bot)

    except Exception as e:
        print(f"[ERRO] Falha crítica no webhook: {e}")

    return jsonify({"status": "success"}), 200


if __name__ == "__main__":
    porta = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=porta)