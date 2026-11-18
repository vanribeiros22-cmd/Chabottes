from flask import Flask, request, jsonify, send_file, render_template_string, redirect, url_for
import pandas as pd
import requests
import os
from datetime import datetime, timedelta, timezone

app = Flask(__name__)

# ==============================================================================
# 1. CONFIGURAÇÕES DA EVOLUTION API E DO ARQUIVO DE DADOS (PRODUÇÃO)
# ==============================================================================
EVOLUTION_API_URL = "https://evolution-api-production-db931012.up.railway.app"
INSTANCE_NAME = "sofia-oficial"
API_KEY = "cetrus2026teste"
WEBHOOK_PUBLIC_URL = "https://chattest-production-37ef5795.up.railway.app/webhook"

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
    numero_str = str(numero).split("@")[0]
    apenas_digitos = "".join(filter(str.isdigit, numero_str))
    if len(apenas_digitos) in [10, 11]:
        return "55" + apenas_digitos
    return apenas_digitos


def agora_brasil():
    return datetime.now(FUSO_BR).strftime("%d/%m/%Y %H:%M:%S")


def configurar_webhook_automatico():
    """
    Configura automaticamente o Webhook na Evolution API para a instância oficial,
    garantindo que MESSAGES_UPSERT e SEND_MESSAGE estejam ativos.
    """
    headers = {"Content-Type": "application/json", "apikey": API_KEY}
    payload_v2 = {
        "webhook": {
            "enabled": True,
            "url": WEBHOOK_PUBLIC_URL,
            "webhookByEvents": False,
            "webhookBase64": False,
            "events": ["MESSAGES_UPSERT", "SEND_MESSAGE"]
        }
    }
    try:
        r = requests.post(
            f"{EVOLUTION_API_URL}/webhook/set/{INSTANCE_NAME}",
            json=payload_v2,
            headers=headers,
            timeout=10
        )
        if r.status_code in [200, 201]:
            return True, f"Webhook ativado com sucesso para {INSTANCE_NAME}!"

        # Fallback para formato plano caso a versão da API espere sem chave 'webhook'
        payload_plano = {
            "enabled": True,
            "url": WEBHOOK_PUBLIC_URL,
            "webhookByEvents": False,
            "events": ["MESSAGES_UPSERT", "SEND_MESSAGE"]
        }
        r2 = requests.post(
            f"{EVOLUTION_API_URL}/webhook/set/{INSTANCE_NAME}",
            json=payload_plano,
            headers=headers,
            timeout=10
        )
        if r2.status_code in [200, 201]:
            return True, f"Webhook ativado com sucesso para {INSTANCE_NAME}!"
        return False, f"Erro HTTP {r.status_code}: {r.text}"
    except Exception as e:
        return False, f"Falha de conexão com Evolution API: {str(e)}"


def buscar_contatos_whatsapp():
    """Consulta a Evolution API para listar contatos e chats sincronizados."""
    contatos_unicos = {}
    headers = {"Content-Type": "application/json", "apikey": API_KEY}

    try:
        resp_contacts = requests.post(
            f"{EVOLUTION_API_URL}/chat/findContacts/{INSTANCE_NAME}",
            json={},
            headers=headers,
            timeout=8
        )
        if resp_contacts.status_code == 200:
            lista = resp_contacts.json()
            if isinstance(lista, list):
                for item in lista:
                    jid = item.get("remoteJid") or item.get("id") or ""
                    if "@s.whatsapp.net" in jid:
                        num = jid.split("@")[0]
                        nome = item.get("pushName") or item.get("name") or item.get("verifiedName") or num
                        contatos_unicos[num] = f"{nome} ({num})"
    except Exception as e:
        print(f"[AVISO] Erro findContacts: {e}")

    try:
        resp_chats = requests.post(
            f"{EVOLUTION_API_URL}/chat/findChats/{INSTANCE_NAME}",
            json={},
            headers=headers,
            timeout=8
        )
        if resp_chats.status_code == 200:
            chats = resp_chats.json()
            if isinstance(chats, list):
                for chat in chats:
                    jid = chat.get("remoteJid") or chat.get("id") or ""
                    if "@s.whatsapp.net" in jid:
                        num = jid.split("@")[0]
                        if num not in contatos_unicos:
                            nome = chat.get("name") or chat.get("pushName") or num
                            contatos_unicos[num] = f"{nome} ({num})"
    except Exception as e:
        print(f"[AVISO] Erro findChats: {e}")

    lista_ordenada = [{"numero": k, "rotulo": v} for k, v in contatos_unicos.items()]
    lista_ordenada.sort(key=lambda x: x["rotulo"].lower())
    return lista_ordenada


def listar_atendimentos_pausados():
    """Retorna a lista de números que estão atualmente em atendimento humano."""
    pausados = set(pausa_memoria)
    try:
        if os.path.exists(EXCEL_FILE):
            df_controle = pd.read_excel(EXCEL_FILE, sheet_name="Controle_Alunos")
            if not df_controle.empty and "Humano" in df_controle.columns:
                ativos = df_controle[df_controle["Humano"].astype(str).str.upper() == "SIM"]
                for num in ativos["ID_Usuario"].dropna().tolist():
                    pausados.add(str(num))
    except Exception as e:
        print(f"[AVISO] Erro ao listar pausados: {e}")
    return sorted(list(pausados))


def carregar_numeros_bloqueados():
    try:
        if not os.path.exists(EXCEL_FILE):
            return []
        df_bloq = pd.read_excel(EXCEL_FILE, sheet_name="TelefoneContatos_Bloqueados")
        return [normalizar_telefone(num) for num in df_bloq["Telefone"].dropna().tolist()]
    except Exception as e:
        print(f"[ERRO] Falha ao carregar bloqueados: {e}")
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
# 4. ENVIO DE MENSAGENS, PAINEL ÚNICO (/) E WEBHOOK
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
        response = requests.post(url, json=payload, headers=headers, timeout=10)
        print(f"[ENVIO API] Status: {response.status_code} | Destino: {numero_destino} | Resp: {response.text}")
        return response.json()
    except Exception as e:
        print(f"[ERRO] Falha ao enviar mensagem: {e}")
        return None


@app.route("/", methods=["GET", "POST"])
@app.route("/iniciar", methods=["GET", "POST"])
def painel_central():
    """
    Painel Único com botões para todas as funções do sistema:
    - Ativar Webhook Oficial
    - Baixar Planilha de Métricas
    - Iniciar Contato com Contatos do WhatsApp ou Número Manual
    - Gerenciar Fila de Atendimento Humano (Pausar / Reativar)
    """
    alerta = ""
    tipo_alerta = "info"

    if request.method == "POST":
        acao = request.form.get("acao", "")
        senha = request.form.get("senha", "")

        if senha != API_KEY:
            alerta = "❌ Senha incorreta! Digite a chave de acesso correta."
            tipo_alerta = "erro"
        else:
            # AÇÃO 1: ATIVAR WEBHOOK DA INSTÂNCIA OFICIAL
            if acao == "ativar_webhook":
                ok, msg = configurar_webhook_automatico()
                alerta = f"✅ {msg}" if ok else f"❌ {msg}"
                tipo_alerta = "ok" if ok else "erro"

            # AÇÃO 2: INICIAR CONTATO COM ALUNO
            elif acao == "iniciar_contato":
                contato_salvo = request.form.get("contato_salvo", "").strip()
                numero_manual = request.form.get("numero_manual", "").strip()
                modo = request.form.get("modo", "menu")
                mensagem_custom = request.form.get("mensagem", "").strip()

                numero_final = numero_manual if numero_manual else contato_salvo
                if not numero_final:
                    alerta = "❌ Selecione um contato da lista ou digite um número."
                    tipo_alerta = "erro"
                else:
                    num_envio = formatar_para_envio(numero_final)
                    num_norm = normalizar_telefone(num_envio)

                    if modo == "menu":
                        gerenciar_estado_humano(num_norm, acao="desativar")
                        texto_envio = gerar_texto_menu_principal()
                        registrar_metrica(num_norm, "Contato Ativo", "Disparo Menu Sofia", "Início Ativo",
                                          "Menu Enviado", "Sim")
                    else:
                        gerenciar_estado_humano(num_norm, acao="ativar")
                        texto_envio = mensagem_custom or "Olá! Tudo bem? Aqui é da equipe acadêmica, como posso ajudar?"
                        registrar_metrica(num_norm, "Contato Ativo", "Disparo Humano", texto_envio,
                                          "Atendimento Humano Iniciado", "Em andamento")

                    resp = enviar_mensagem_whatsapp(num_envio, texto_envio)
                    if resp and ("key" in str(resp) or "PENDING" in str(resp) or "status" in str(resp)):
                        alerta = f"✅ Mensagem enviada para {num_envio}!"
                        tipo_alerta = "ok"
                    else:
                        alerta = f"⚠️ Resposta da API: {resp}"
                        tipo_alerta = "erro"

            # AÇÃO 3: PAUSAR OU REATIVAR UM NÚMERO MANUALMENTE
            elif acao == "reativar_robo":
                num_target = request.form.get("num_target", "")
                gerenciar_estado_humano(num_target, acao="desativar")
                alerta = f"✅ Robô reativado para o contato {num_target}!"
                tipo_alerta = "ok"

            elif acao == "pausar_robo":
                num_pausar = normalizar_telefone(request.form.get("num_pausar", ""))
                if num_pausar:
                    gerenciar_estado_humano(num_pausar, acao="ativar")
                    alerta = f"⏸️ Robô pausado (modo atendimento humano) para {num_pausar}."
                    tipo_alerta = "ok"

    # Carrega dados atualizados para exibir na tela
    contatos_lista = buscar_contatos_whatsapp()
    pausados_lista = listar_atendimentos_pausados()
    bloqueados_lista = carregar_numeros_bloqueados()

    # Verifica estado atual da instância
    status_conexao = "Desconhecido"
    try:
        r_st = requests.get(f"{EVOLUTION_API_URL}/instance/connectionState/{INSTANCE_NAME}",
                            headers={"apikey": API_KEY}, timeout=5)
        if r_st.status_code == 200:
            status_conexao = r_st.json().get("instance", {}).get("state", "open")
    except Exception:
        status_conexao = "Verifique Evolution API"

    html = """
    <!DOCTYPE html>
    <html lang="pt-BR">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Painel Central - Sofia Oficial</title>
        <style>
            body { font-family: 'Segoe UI', Arial, sans-serif; background: #f1f5f9; margin: 0; padding: 20px; color: #1e293b; }
            .container { max-width: 960px; margin: auto; }
            .header { background: #0f766e; color: white; padding: 20px 25px; border-radius: 12px; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 15px; }
            .header h1 { margin: 0; font-size: 22px; }
            .badge { background: rgba(255,255,255,0.2); padding: 6px 12px; border-radius: 20px; font-size: 13px; }
            .toolbar { display: flex; gap: 10px; flex-wrap: wrap; margin: 20px 0; }
            .btn { padding: 11px 18px; border: none; border-radius: 8px; font-weight: bold; cursor: pointer; text-decoration: none; font-size: 14px; display: inline-flex; align-items: center; gap: 6px; }
            .btn-primary { background: #0284c7; color: white; }
            .btn-primary:hover { background: #0369a1; }
            .btn-success { background: #16a34a; color: white; }
            .btn-success:hover { background: #15803d; }
            .btn-warning { background: #d97706; color: white; }
            .btn-warning:hover { background: #b45309; }
            .btn-teal { background: #0f766e; color: white; width: 100%; justify-content: center; font-size: 15px; margin-top: 15px; }
            .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
            @media (max-width: 768px) { .grid { grid-template-columns: 1fr; } }
            .card { background: white; padding: 22px; border-radius: 12px; box-shadow: 0 2px 8px rgba(0,0,0,0.06); }
            .card h2 { margin-top: 0; font-size: 18px; color: #0f766e; border-bottom: 1px solid #e2e8f0; padding-bottom: 10px; }
            label { display: block; margin-top: 12px; font-weight: 600; font-size: 13px; color: #475569; }
            input, select, textarea { width: 100%; padding: 10px; margin-top: 5px; border: 1px solid #cbd5e1; border-radius: 6px; box-sizing: border-box; font-size: 14px; }
            .alerta { padding: 14px; border-radius: 8px; margin-bottom: 20px; font-weight: bold; }
            .alerta.ok { background: #dcfce7; color: #166534; border: 1px solid #86efac; }
            .alerta.erro { background: #fee2e2; color: #991b1b; border: 1px solid #fca5a5; }
            .lista-item { display: flex; justify-content: space-between; align-items: center; padding: 10px; border-bottom: 1px solid #f1f5f9; }
            .senha-box { background: #fffbeb; border: 1px solid #fde68a; padding: 12px 18px; border-radius: 8px; margin-bottom: 15px; display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
            .senha-box input { max-width: 240px; margin-top: 0; }
        </style>
        <script>
            function filtrarContatos() {
                var input = document.getElementById("filtro").value.toLowerCase();
                var select = document.getElementById("contato_salvo");
                for (var i = 0; i < select.options.length; i++) {
                    var txt = select.options[i].text.toLowerCase();
                    select.options[i].style.display = txt.includes(input) ? "" : "none";
                }
            }
            function sincronizarSenha(val) {
                var campos = document.getElementsByClassName("campo-senha-oculto");
                for (var i = 0; i < campos.length; i++) {
                    campos[i].value = val;
                }
            }
        </script>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <div>
                    <h1>🤖 Painel Central — Sofia Oficial</h1>
                    <small>Instância: <b>{{ instancia }}</b> | Bloqueados na lista: <b>{{ total_bloqueados }}</b></small>
                </div>
                <div class="badge">Status WhatsApp: <b>{{ status_conexao }}</b></div>
            </div>

            <!-- CAMPO ÚNICO DE SENHA PARA TODOS OS BOTÕES -->
            <div class="senha-box" style="margin-top: 15px;">
                <label style="margin:0;">🔑 Chave de Acesso:</label>
                <input type="password" id="senha_mestra" onkeyup="sincronizarSenha(this.value)" placeholder="Digite cetrus2026teste para liberar ações">
                <small style="color:#92400e;">Digite uma vez acima para usar qualquer botão abaixo.</small>
            </div>

            <!-- BARRA DE BOTÕES RÁPIDOS -->
            <div class="toolbar">
                <form method="POST" style="margin:0;">
                    <input type="hidden" name="acao" value="ativar_webhook">
                    <input type="hidden" name="senha" class="campo-senha-oculto">
                    <button type="submit" class="btn btn-warning">🔌 1. Ativar/Corrigir Webhook Oficial</button>
                </form>

                <a href="/metricas" class="btn btn-success">📥 2. Baixar Planilha de Métricas (.xlsx)</a>

                <a href="/" class="btn btn-primary">🔄 3. Atualizar Contatos e Fila</a>
            </div>

            {% if alerta %}
                <div class="alerta {{ tipo_alerta }}">{{ alerta }}</div>
            {% endif %}

            <div class="grid">
                <!-- COLUNA 1: INICIAR CONTATO -->
                <div class="card">
                    <h2>📲 Iniciar Contato com Aluno</h2>
                    <form method="POST">
                        <input type="hidden" name="acao" value="iniciar_contato">
                        <input type="hidden" name="senha" class="campo-senha-oculto">

                        <label>Buscar nos Contatos Salvos ({{ total_contatos }}):</label>
                        <input type="text" id="filtro" onkeyup="filtrarContatos()" placeholder="🔍 Digite nome ou número...">
                        <select name="contato_salvo" id="contato_salvo">
                            <option value="">-- Selecione um contato do WhatsApp --</option>
                            {% for c in contatos %}
                                <option value="{{ c.numero }}">{{ c.rotulo }}</option>
                            {% endfor %}
                        </select>

                        <label>OU Digite um Número Novo (com DDD):</label>
                        <input type="text" name="numero_manual" placeholder="Ex: 11988887777">

                        <label>Tipo de Início:</label>
                        <select name="modo">
                            <option value="menu">Enviar Menu Automático da Sofia</option>
                            <option value="humano">Enviar Mensagem Manual (Pausar Robô)</option>
                        </select>

                        <label>Mensagem Manual (opcional se escolheu opção 2):</label>
                        <textarea name="mensagem" rows="2" placeholder="Olá! Aqui é da secretaria acadêmica..."></textarea>

                        <button type="submit" class="btn btn-teal">🚀 Enviar Mensagem Agora</button>
                    </form>
                </div>

                <!-- COLUNA 2: ATENDIMENTOS HUMANOS EM ESPERA -->
                <div class="card">
                    <h2>👩‍💻 Atendimentos Humanos (Robô Pausado)</h2>
                    <p style="font-size:13px; color:#64748b;">
                        Quando um aluno digita <b>5</b> ou o atendente envia mensagem, o número aparece aqui e o robô fica em silêncio até clicar em <b>Reativar</b> (ou enviar <b>#finalizar</b> no WhatsApp).
                    </p>

                    <form method="POST" style="display:flex; gap:8px; margin-bottom:15px;">
                        <input type="hidden" name="acao" value="pausar_robo">
                        <input type="hidden" name="senha" class="campo-senha-oculto">
                        <input type="text" name="num_pausar" placeholder="Pausar número (DDD + Num)" style="margin:0;">
                        <button type="submit" class="btn btn-warning" style="white-space:nowrap;">⏸️ Pausar</button>
                    </form>

                    {% if pausados %}
                        {% for num in pausados %}
                            <div class="lista-item">
                                <span>📱 <b>{{ num }}</b> (Em atendimento humano)</span>
                                <form method="POST" style="margin:0;">
                                    <input type="hidden" name="acao" value="reativar_robo">
                                    <input type="hidden" name="senha" class="campo-senha-oculto">
                                    <input type="hidden" name="num_target" value="{{ num }}">
                                    <button type="submit" class="btn btn-success" style="padding:6px 12px; font-size:12px;">▶️ Devolver ao Robô</button>
                                </form>
                            </div>
                        {% endfor %}
                    {% else %}
                        <p style="text-align:center; color:#94a3b8; padding:20px 0;">Nenhum atendimento humano ativo no momento.</p>
                    {% endif %}
                </div>
            </div>
        </div>
    </body>
    </html>
    """
    return render_template_string(
        html,
        alerta=alerta,
        tipo_alerta=tipo_alerta,
        instancia=INSTANCE_NAME,
        status_conexao=status_conexao,
        contatos=contatos_lista,
        total_contatos=len(contatos_lista),
        pausados=pausados_lista,
        total_bloqueados=len(bloqueados_lista)
    )


@app.route("/metricas", methods=["GET"])
def baixar_metricas():
    if os.path.exists(EXCEL_FILE):
        return send_file(
            EXCEL_FILE,
            as_attachment=True,
            download_name=f"relatorio_sofia_{datetime.now(FUSO_BR).strftime('%Y%m%d_%H%M')}.xlsx"
        )
    return jsonify({"erro": "Planilha não encontrada no servidor."}), 404


@app.route("/status", methods=["GET"])
def verificar_status():
    try:
        resp = requests.get(f"{EVOLUTION_API_URL}/instance/fetchInstances", headers={"apikey": API_KEY}, timeout=10)
        return jsonify({
            "servidor_flask": "ONLINE",
            "instancia_configurada": INSTANCE_NAME,
            "excel_encontrado": os.path.exists(EXCEL_FILE),
            "instancias": resp.json()
        }), 200
    except Exception as e:
        return jsonify({"erro": str(e)}), 500


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