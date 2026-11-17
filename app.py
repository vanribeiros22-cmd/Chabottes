from flask import Flask, request, jsonify, send_file, render_template_string
import pandas as pd
import requests
import os
from datetime import datetime, timedelta, timezone

app = Flask(__name__)

# ==============================================================================
# 1. CONFIGURAÇÕES DA EVOLUTION API E DO ARQUIVO DE DADOS (PRODUÇÃO)
# ==============================================================================
EVOLUTION_API_URL = "https://evolution-api-production-db931012.up.railway.app"
INSTANCE_NAME = "sofia-oficial"  # <-- Confirma se este nome é IGUAL ao do painel Evolution!
API_KEY = "cetrus2026teste"

EXCEL_FILE = "diretorio_academico.xlsx"
FUSO_BR = timezone(timedelta(hours=-3))

pausa_memoria = set()


# ==============================================================================
# 2. FUNÇÕES AUXILIARES (NORMALIZAÇÃO, BLOQUEIO, PAUSA E MÉTRICAS)
# ==============================================================================
def normalizar_telefone(numero):
    if not numero:
        return ""
    numero_str = str(numero).split("@")[0]
    apenas_digitos = "".join(filter(str.isdigit, numero_str))
    if len(apenas_digitos) >= 10:
        return apenas_digitos[-10:]
    return apenas_digitos


def formatar_para_envio(numero):
    apenas_digitos = "".join(filter(str.isdigit, str(numero)))
    if len(apenas_digitos) in [10, 11]:
        return "55" + apenas_digitos
    return apenas_digitos


def agora_brasil():
    return datetime.now(FUSO_BR).strftime("%d/%m/%Y %H:%M:%S")


def carregar_numeros_bloqueados():
    try:
        if not os.path.exists(EXCEL_FILE):
            return []
        df_bloq = pd.read_excel(EXCEL_FILE, sheet_name="TelefoneContatos_Bloqueados")
        return [normalizar_telefone(num) for num in df_bloq["Telefone"].dropna().tolist()]
    except Exception as e:
        print(f"[ERRO] Falha ao carregar números bloqueados: {e}")
        return []


def gerenciar_estado_humano(numero_norm, acao="verificar"):
    if not numero_norm:
        return False

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
            else:
                pausa_memoria.discard(numero_norm)

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


def gerar_texto_menu_principal():
    df_menu = pd.read_excel(EXCEL_FILE, sheet_name="Menu_Principal")
    menu_texto = (
        "👋 Bem-vinda(o) ao atendimento acadêmico da Sofia!\n\n"
        "Escolha uma das opções abaixo digitando apenas o *número* correspondente:\n\n"
    )
    for _, row in df_menu.iterrows():
        menu_texto += f"• *{int(row['Numero'])}* - {row['Opcao']}\n"
    return menu_texto


def formatar_resposta(row):
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
# 3. MOTOR DE CONSULTA AO EXCEL
# ==============================================================================
def consultar_excel(pergunta, numero_aluno_norm):
    try:
        if not os.path.exists(EXCEL_FILE):
            return "⚠️ Erro: Arquivo de diretório acadêmico não encontrado no servidor."

        pergunta_limpa = pergunta.strip().lower()

        comandos_saida = ["0", "encerrar", "sair", "encerrar atendimento", "#finalizar"]
        if pergunta_limpa in comandos_saida:
            gerenciar_estado_humano(numero_aluno_norm, acao="desativar")
            registrar_metrica(numero_aluno_norm, "Encerramento", "Saída do Usuário", pergunta, "Atendimento Encerrado",
                              "Sim")
            return (
                "Atendimento encerrado. Obrigado pelo contato! 💙\n\n"
                "Se precisar de algo, é só enviar uma nova mensagem a qualquer momento."
            )

        comandos_menu = ["oi", "ola", "olá", "bom dia", "boa tarde", "boa noite", "menu", "ajuda"]
        if pergunta_limpa in comandos_menu:
            registrar_metrica(numero_aluno_norm, "Menu Principal", "Início / Boas-vindas", pergunta,
                              "Exibiu Menu Principal", "Sim")
            return gerar_texto_menu_principal()

        df_faq = pd.read_excel(EXCEL_FILE, sheet_name="FAQ_BaseNumero_Menu")

        if pergunta_limpa.isdigit() and len(pergunta_limpa) == 1:
            num_opcao = float(pergunta_limpa)

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

        for _, row in df_faq.iterrows():
            categoria = str(row.get("Categoria ", "")).lower()
            processo = str(row.get("Processo ", "")).lower()
            palavras_chave = str(row.get("Palavras_Chave", "")).lower()

            if (pergunta_limpa in palavras_chave) or (pergunta_limpa in processo) or (pergunta_limpa in categoria):
                registrar_metrica(numero_aluno_norm, row.get('Categoria ', ''), row.get('Processo ', ''), pergunta,
                                  "Busca Palavra-Chave", "Sim")
                return formatar_resposta(row)

        registrar_metrica(numero_aluno_norm, "Não Identificado", "Fallback", pergunta, "Opção não encontrada", "Não")
        return (
            "Desculpe, não encontrei essa opção no diretório acadêmico. 🤔\n\n"
            "• Digite *menu* para ver as opções disponíveis\n"
            "• Digite *0* para encerrar o atendimento"
        )

    except Exception as e:
        return f"Ocorreu um erro ao processar os dados acadêmicos: {str(e)}"


# ==============================================================================
# 4. ROTAS WEB (/status, /iniciar, /metricas, /webhook)
# ==============================================================================
def enviar_mensagem_whatsapp(numero_destino, texto_resposta):
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
        print(f"[ENVIO API] Status: {response.status_code} | Destino: {numero_destino} | Resp: {response.text}")
        return response.json()
    except Exception as e:
        print(f"[ERRO] Falha ao enviar mensagem: {e}")
        return None


@app.route("/status", methods=["GET"])
def verificar_status():
    """Rota de diagnóstico para verificar a ligação entre o Flask e a Evolution API."""
    try:
        resp = requests.get(
            f"{EVOLUTION_API_URL}/instance/fetchInstances",
            headers={"apikey": API_KEY},
            timeout=10
        )
        return jsonify({
            "servidor_flask": "ONLINE",
            "instancia_configurada_no_codigo": INSTANCE_NAME,
            "excel_encontrado": os.path.exists(EXCEL_FILE),
            "instancias_na_evolution_api": resp.json()
        }), 200
    except Exception as e:
        return jsonify({"erro": str(e)}), 500


@app.route("/iniciar", methods=["GET", "POST"])
def iniciar_contato():
    resultado = ""
    if request.method == "POST":
        senha = request.form.get("senha", "")
        numero = request.form.get("numero", "")
        modo = request.form.get("modo", "menu")
        mensagem_custom = request.form.get("mensagem", "")

        if senha != API_KEY:
            resultado = "❌ Chave de acesso incorreta."
        elif not numero:
            resultado = "❌ Por favor, digite o número com DDD."
        else:
            num_envio = formatar_para_envio(numero)
            num_norm = normalizar_telefone(num_envio)

            if modo == "menu":
                gerenciar_estado_humano(num_norm, acao="desativar")
                texto_para_enviar = gerar_texto_menu_principal()
                registrar_metrica(num_norm, "Contato Ativo", "Disparo Menu Sofia", "Início Ativo", "Menu Enviado",
                                  "Sim")
            else:
                gerenciar_estado_humano(num_norm, acao="ativar")
                texto_para_enviar = mensagem_custom.strip() or "Olá! Tudo bem? Aqui é da equipe acadêmica, como posso ajudar?"
                registrar_metrica(num_norm, "Contato Ativo", "Disparo Humano", texto_para_enviar,
                                  "Atendimento Humano Iniciado", "Em andamento")

            resp = enviar_mensagem_whatsapp(num_envio, texto_para_enviar)
            resultado = f"Resultado do envio para {num_envio}: {resp}"

    html = """
    <!DOCTYPE html>
    <html lang="pt-BR">
    <head>
        <meta charset="UTF-8">
        <title>Iniciar Contato - Sofia Oficial</title>
        <style>
            body { font-family: Arial, sans-serif; background: #f4f6f9; padding: 40px; }
            .card { max-width: 480px; margin: auto; background: white; padding: 25px; border-radius: 10px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }
            h2 { color: #1e293b; margin-top: 0; }
            label { display: block; margin-top: 15px; font-weight: bold; color: #334155; }
            input, select, textarea { width: 100%; padding: 10px; margin-top: 5px; border: 1px solid #cbd5e1; border-radius: 6px; box-sizing: border-box; }
            button { margin-top: 20px; width: 100%; padding: 12px; background: #0f766e; color: white; border: none; border-radius: 6px; font-size: 16px; cursor: pointer; }
            .msg { margin-top: 15px; padding: 12px; border-radius: 6px; background: #e2e8f0; font-size: 13px; word-break: break-all; }
        </style>
    </head>
    <body>
        <div class="card">
            <h2>📲 Iniciar Contato (Sofia Oficial)</h2>
            <form method="POST">
                <label>Senha de Acesso (API Key):</label>
                <input type="password" name="senha" placeholder="Digite a chave de acesso" required>
                <label>Número do Aluno (com DDD):</label>
                <input type="text" name="numero" placeholder="Ex: 11988887777" required>
                <label>Como deseja iniciar?</label>
                <select name="modo">
                    <option value="menu">1. Enviar Menu Automático da Sofia</option>
                    <option value="humano">2. Mensagem Manual (Pausar o Robô)</option>
                </select>
                <label>Mensagem Personalizada (se escolheu opção 2):</label>
                <textarea name="mensagem" rows="3" placeholder="Olá! Aqui é da secretaria acadêmica..."></textarea>
                <button type="submit">Enviar Mensagem Agora</button>
            </form>
            {% if resultado %}
                <div class="msg">{{ resultado }}</div>
            {% endif %}
        </div>
    </body>
    </html>
    """
    return render_template_string(html, resultado=resultado)


@app.route("/metricas", methods=["GET"])
def baixar_metricas():
    if os.path.exists(EXCEL_FILE):
        return send_file(
            EXCEL_FILE,
            as_attachment=True,
            download_name=f"relatorio_sofia_{datetime.now(FUSO_BR).strftime('%Y%m%d_%H%M')}.xlsx"
        )
    return jsonify({"erro": "Planilha não encontrada no servidor."}), 404


@app.route("/webhook", methods=["POST"])
def webhook():
    dados = request.json

    try:
        evento = dados.get("event", "").lower()

        if evento in ["messages.upsert", "send.message"]:
            raw_data = dados.get("data", {})
            message_data = raw_data[0] if isinstance(raw_data, list) and len(raw_data) > 0 else raw_data

            key = message_data.get("key", {})
            remote_jid = key.get("remoteJid", "")
            remote_jid_alt = key.get("remoteJidAlt", "")

            from_me = True if evento == "send.message" else key.get("fromMe", False)

            if remote_jid.endswith("@g.us"):
                return jsonify({"status": "group_ignored"}), 200

            jid_real = remote_jid_alt if ("@lid" in remote_jid and remote_jid_alt) else remote_jid
            numero_bruto = jid_real.split("@")[0]
            numero_norm = normalizar_telefone(numero_bruto)

            message_content = message_data.get("message", {})
            texto_mensagem = (
                    message_content.get("conversation")
                    or message_content.get("extendedTextMessage", {}).get("text", "")
            )

            print(
                f"[WEBHOOK] Evento: {evento} | DeMim: {from_me} | JID: {jid_real} | Norm: {numero_norm} | Texto: {texto_mensagem}")

            if not texto_mensagem or not numero_norm:
                return jsonify({"status": "ignored_empty"}), 200

            if from_me:
                if "#finalizar" in texto_mensagem.lower():
                    gerenciar_estado_humano(numero_norm, acao="desativar")
                    registrar_metrica(numero_norm, "Atendimento Humano", "Finalização Atendente", "#finalizar",
                                      "Atendimento Humano Concluído", "Sim")
                    enviar_mensagem_whatsapp(
                        jid_real,
                        "Atendimento humano finalizado. Obrigado pelo contato! 💙\n\nSe precisar de algo novamente, basta enviar *oi* ou *menu*."
                    )
                elif not texto_mensagem.startswith(
                        ("👋", "📌", "👩‍💻", "Desculpe,", "Atendimento encerrado", "Atendimento humano")):
                    gerenciar_estado_humano(numero_norm, acao="ativar")
                return jsonify({"status": "from_me_processed"}), 200

            numeros_bloqueados = carregar_numeros_bloqueados()
            if numero_norm in numeros_bloqueados:
                return jsonify({"status": "blocked_number"}), 200

            if gerenciar_estado_humano(numero_norm, acao="verificar"):
                if texto_mensagem.strip().lower() in ["0", "#finalizar"]:
                    gerenciar_estado_humano(numero_norm, acao="desativar")
                    registrar_metrica(numero_norm, "Atendimento Humano", "Encerrado pelo Aluno", texto_mensagem,
                                      "Encerrado pelo Aluno", "Sim")
                    enviar_mensagem_whatsapp(
                        jid_real,
                        "Atendimento encerrado. Obrigado pelo contato! 💙\n\nSe precisar de algo, é só enviar uma nova mensagem a qualquer momento."
                    )
                return jsonify({"status": "paused_for_human"}), 200

            resposta_bot = consultar_excel(texto_mensagem, numero_norm)
            enviar_mensagem_whatsapp(jid_real, resposta_bot)

    except Exception as e:
        print(f"[ERRO] Falha crítica no webhook: {e}")

    return jsonify({"status": "success"}), 200


if __name__ == "__main__":
    porta = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=porta)