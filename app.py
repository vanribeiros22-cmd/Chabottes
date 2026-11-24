import io
import os
from datetime import datetime, timedelta, timezone

from flask import (
    Flask,
    jsonify,
    render_template_string,
    request,
    send_file,
    session,
)
import pandas as pd
import requests

app = Flask(__name__)

# ==============================================================================
# 1. CONFIGURAÇÕES DA EVOLUTION API E DO ARQUIVO DE DADOS (PRODUÇÃO)
# ==============================================================================
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "sofia-oficial-cetrus-2026-chave-fixa")
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=30)

EVOLUTION_API_URL = os.environ.get(
    "EVOLUTION_API_URL", "https://evolution-api-production-db931012.up.railway.app"
).rstrip("/")
INSTANCE_NAME = os.environ.get("INSTANCE_NAME", "sofia-oficial").strip()
API_KEY = os.environ.get("API_KEY", "cetrus2026teste").strip()
EVOLUTION_API_KEY = os.environ.get("EVOLUTION_API_KEY", API_KEY).strip()
WEBHOOK_PUBLIC_URL = os.environ.get(
    "WEBHOOK_PUBLIC_URL", "https://chattest-production-37ef5795.up.railway.app/webhook"
).strip()

EXCEL_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "diretorio_academico.xlsx")
FUSO_BR = timezone(timedelta(hours=-3))

MENSAGEM_ENCERRAMENTO = (
    "Atendimento encerrado. Obrigado pelo contato! 💙\n\n"
    "Se precisar de algo, é só enviar uma nova mensagem a qualquer momento"
)

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


def converter_numero_seguro(valor):
    """Evita 'could not convert string to float' quando recebe texto livre."""
    if valor is None:
        return None
    texto = str(valor).strip().replace(",", ".")
    try:
        return float(texto)
    except (ValueError, TypeError):
        return None


def eh_mensagem_encerramento(texto):
    if not texto:
        return False
    limpo = " ".join(str(texto).replace("\\n", "\n").lower().split())
    alvo = " ".join(MENSAGEM_ENCERRAMENTO.lower().split())
    return (
        limpo == alvo
        or limpo.strip() == "#finalizar"
        or "atendimento encerrado. obrigado pelo contato!" in limpo
    )


def chave_autenticada(req):
    if session.get("sofia_autenticada") is True:
        session.permanent = True
        return True
    senha = (
        req.form.get("senha")
        or (req.get_json(silent=True) or {}).get("senha")
        or req.args.get("senha")
        or ""
    ).strip()
    if senha == API_KEY:
        session.permanent = True
        session["sofia_autenticada"] = True
        return True
    return False


def configurar_webhook_automatico():
    """
    Configura automaticamente o Webhook na Evolution API para a instância oficial,
    garantindo que MESSAGES_UPSERT e SEND_MESSAGE estejam ativos.
    """
    headers = {"Content-Type": "application/json", "apikey": EVOLUTION_API_KEY}
    payload_v2 = {
        "webhook": {
            "enabled": True,
            "url": WEBHOOK_PUBLIC_URL,
            "webhookByEvents": False,
            "webhookBase64": False,
            "events": ["MESSAGES_UPSERT", "SEND_MESSAGE"],
        }
    }
    try:
        r = requests.post(
            f"{EVOLUTION_API_URL}/webhook/set/{INSTANCE_NAME}",
            json=payload_v2,
            headers=headers,
            timeout=10,
        )
        if r.status_code in [200, 201]:
            return True, f"Webhook ativado com sucesso para {INSTANCE_NAME}!"

        payload_plano = {
            "enabled": True,
            "url": WEBHOOK_PUBLIC_URL,
            "webhookByEvents": False,
            "events": ["MESSAGES_UPSERT", "SEND_MESSAGE"],
        }
        r2 = requests.post(
            f"{EVOLUTION_API_URL}/webhook/set/{INSTANCE_NAME}",
            json=payload_plano,
            headers=headers,
            timeout=10,
        )
        if r2.status_code in [200, 201]:
            return True, f"Webhook ativado com sucesso para {INSTANCE_NAME}!"
        return False, f"Erro HTTP {r.status_code}: {r.text}"
    except Exception as e:
        return False, f"Falha de conexão com Evolution API: {str(e)}"


def buscar_contatos_whatsapp():
    """Consulta a Evolution API para listar contatos e chats sincronizados."""
    contatos_unicos = {}
    headers = {"Content-Type": "application/json", "apikey": EVOLUTION_API_KEY}

    try:
        resp_contacts = requests.post(
            f"{EVOLUTION_API_URL}/chat/findContacts/{INSTANCE_NAME}",
            json={},
            headers=headers,
            timeout=8,
        )
        if resp_contacts.status_code == 200:
            lista = resp_contacts.json()
            if isinstance(lista, list):
                for item in lista:
                    jid = item.get("remoteJid") or item.get("id") or ""
                    if "@s.whatsapp.net" in jid or "@lid" in jid:
                        num = jid.split("@")[0]
                        nome = (
                            item.get("pushName")
                            or item.get("name")
                            or item.get("verifiedName")
                            or num
                        )
                        contatos_unicos[num] = f"{nome} ({num})"
    except Exception as e:
        print(f"[AVISO] Erro findContacts: {e}")

    try:
        resp_chats = requests.post(
            f"{EVOLUTION_API_URL}/chat/findChats/{INSTANCE_NAME}",
            json={},
            headers=headers,
            timeout=8,
        )
        if resp_chats.status_code == 200:
            chats = resp_chats.json()
            if isinstance(chats, list):
                for chat in chats:
                    jid = chat.get("remoteJid") or chat.get("id") or ""
                    if "@s.whatsapp.net" in jid or "@lid" in jid:
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
            xls = pd.ExcelFile(EXCEL_FILE)
            if "Controle_Alunos" in xls.sheet_names:
                df_controle = pd.read_excel(EXCEL_FILE, sheet_name="Controle_Alunos")
                if not df_controle.empty and "Humano" in df_controle.columns:
                    ativos = df_controle[
                        df_controle["Humano"].astype(str).str.upper() == "SIM"
                    ]
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


def gerenciar_estado_humano(numero_norm, acao="verificar", nome_aluno="Aluno WhatsApp"):
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
            pd.DataFrame(
                columns=[
                    "ID_Usuario",
                    "Nome_Aluno",
                    "Status",
                    "Ultima_Interacao",
                    "Etapa",
                    "Humano",
                ]
            ),
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
                df_controle.loc[
                    df_controle["ID_Usuario"] == numero_norm, "Humano"
                ] = status_humano
                df_controle.loc[
                    df_controle["ID_Usuario"] == numero_norm, "Ultima_Interacao"
                ] = agora_brasil()
                df_controle.loc[
                    df_controle["ID_Usuario"] == numero_norm, "Etapa"
                ] = etapa_atual
            else:
                nova_linha = pd.DataFrame(
                    [
                        {
                            "ID_Usuario": numero_norm,
                            "Nome_Aluno": nome_aluno or "Aluno WhatsApp",
                            "Status": "Ativo",
                            "Ultima_Interacao": agora_brasil(),
                            "Etapa": etapa_atual,
                            "Humano": status_humano,
                        }
                    ]
                )
                df_controle = pd.concat([df_controle, nova_linha], ignore_index=True)

            todas_abas["Controle_Alunos"] = df_controle
            with pd.ExcelWriter(EXCEL_FILE, engine="openpyxl") as writer:
                for nome_aba, df_aba in todas_abas.items():
                    df_aba.to_excel(writer, sheet_name=nome_aba, index=False)
            return True

    except Exception as e:
        print(f"[ERRO] Falha ao gravar estado humano no Excel: {e}")
        return numero_norm in pausa_memoria


def registrar_metrica(
    numero_aluno, categoria, processo, duvida, desfecho, resolvido="Sim"
):
    try:
        if not os.path.exists(EXCEL_FILE):
            return

        todas_abas = pd.read_excel(EXCEL_FILE, sheet_name=None)
        nome_aba_metricas = (
            "Metricas_Atendimentos "
            if "Metricas_Atendimentos " in todas_abas
            else "Metricas_Atendimentos"
        )

        df_met = todas_abas.get(
            nome_aba_metricas,
            pd.DataFrame(
                columns=[
                    "ID_Log",
                    "Data_Hora",
                    "ID_Usuario",
                    "Canal",
                    "Categoria",
                    "Processo",
                    "Duvida_Aluno",
                    "Desfecho",
                    "Resolvido",
                ]
            ),
        )

        novo_id = len(df_met) + 1
        nova_linha = pd.DataFrame(
            [
                {
                    "ID_Log": novo_id,
                    "Data_Hora": agora_brasil(),
                    "ID_Usuario": numero_aluno,
                    "Canal": "WhatsApp",
                    "Categoria": categoria,
                    "Processo": processo,
                    "Duvida_Aluno": duvida,
                    "Desfecho": desfecho,
                    "Resolvido": resolvido,
                }
            ]
        )

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
        num_val = converter_numero_seguro(row.get("Numero"))
        num_fmt = int(num_val) if num_val is not None else row.get("Numero")
        menu_texto += f"• *{num_fmt}* — {row.get('Opcao', '')}\n"
    menu_texto += (
        "\n-----------------------------------\n"
        "💡 *Como continuar:*\n"
        "• Digite *menu* para voltar ao início\n"
        "• Digite *0* para encerrar o atendimento"
    )
    return menu_texto


def formatar_resposta(row):
    cat = row.get("Categoria ", "")
    proc = row.get("Processo ", "")
    resp = row.get("Instrucoes_Tecnicas", "Consulte os detalhes no portal.")
    link_arquivo = row.get("Link_Arquivo", "")

    texto_final = f"📌 *{cat} — {proc}*\n\n{resp}"

    if pd.notna(link_arquivo) and str(link_arquivo).strip() != "":
        texto_final += (
            f"\n\n📂 *Material / Documento Associado:*\n{str(link_arquivo).strip()}"
        )

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

        pergunta_limpa = str(pergunta or "").strip().lower()

        comandos_saida = ["0", "encerrar", "sair", "encerrar atendimento", "#finalizar"]
        if pergunta_limpa in comandos_saida or eh_mensagem_encerramento(pergunta_limpa):
            gerenciar_estado_humano(numero_aluno_norm, acao="desativar")
            registrar_metrica(
                numero_aluno_norm,
                "Encerramento",
                "Saída do Usuário",
                pergunta,
                "Atendimento Encerrado",
                "Sim",
            )
            return MENSAGEM_ENCERRAMENTO

        comandos_menu = [
            "oi",
            "ola",
            "olá",
            "bom dia",
            "boa tarde",
            "boa noite",
            "menu",
            "ajuda",
        ]
        if pergunta_limpa in comandos_menu:
            registrar_metrica(
                numero_aluno_norm,
                "Menu Principal",
                "Início / Boas-vindas",
                pergunta,
                "Exibiu Menu Principal",
                "Sim",
            )
            return gerar_texto_menu_principal()

        df_faq = pd.read_excel(EXCEL_FILE, sheet_name="FAQ_BaseNumero_Menu")

        if pergunta_limpa.isdigit() and len(pergunta_limpa) == 1:
            num_opcao = converter_numero_seguro(pergunta_limpa)

            if num_opcao == 5.0:
                gerenciar_estado_humano(numero_aluno_norm, acao="ativar")
                registrar_metrica(
                    numero_aluno_norm,
                    "Atendimento Humano",
                    "Transbordo Humano",
                    pergunta,
                    "Transferido para Atendente",
                    "Em andamento",
                )
                filtrado_5 = df_faq[df_faq["Numero_Menu"] == 5.0]
                msg_humano = "👩‍💻 *Transferindo para o Atendimento Humano...*\n\n"
                if not filtrado_5.empty:
                    msg_humano += str(
                        filtrado_5.iloc[0].get(
                            "Instrucoes_Tecnicas",
                            "Aguarde um momento, um de nossos atendentes já vai falar com você.",
                        )
                    )
                else:
                    msg_humano += (
                        "Aguarde um momento, um de nossos atendentes já vai falar com você."
                    )

                msg_humano += "\n\n_(O robô ficará pausado durante este atendimento)_"
                return msg_humano

            filtrado = df_faq[df_faq["Numero_Menu"] == num_opcao]

            if not filtrado.empty:
                if len(filtrado) == 1:
                    row_unica = filtrado.iloc[0]
                    registrar_metrica(
                        numero_aluno_norm,
                        row_unica.get("Categoria ", ""),
                        row_unica.get("Processo ", ""),
                        pergunta,
                        "Resposta Direta",
                        "Sim",
                    )
                    return formatar_resposta(row_unica)

                cat_nome = filtrado.iloc[0].get("Categoria ", "Atendimento")
                registrar_metrica(
                    numero_aluno_norm,
                    cat_nome,
                    "Listagem Submenu",
                    pergunta,
                    "Exibiu Submenus",
                    "Sim",
                )
                sub_texto = (
                    f"📌 *{cat_nome}*\n\n"
                    f"Digite o número da opção desejada (exemplo: *{int(num_opcao)}.1*):\n\n"
                )

                for _, row in filtrado.iterrows():
                    sub_val = converter_numero_seguro(row.get("Numero_Submenu", 1))
                    sub_num = int(sub_val) if sub_val is not None else 1
                    proc = row.get("Processo ", "")
                    sub_texto += f"• *{int(num_opcao)}.{sub_num}* — {proc}\n"

                sub_texto += (
                    "\n-----------------------------------\n"
                    "💡 *Outras opções:*\n"
                    "• Digite *menu* para voltar ao menu principal\n"
                    "• Digite *0* para encerrar o atendimento"
                )
                return sub_texto

        if "." in pergunta_limpa:
            partes = pergunta_limpa.split(".")
            menu_num = converter_numero_seguro(partes[0])
            sub_num = converter_numero_seguro(partes[-1])
            if menu_num is not None and sub_num is not None:
                if menu_num == 5.0:
                    gerenciar_estado_humano(numero_aluno_norm, acao="ativar")

                encontrado = df_faq[
                    (df_faq["Numero_Menu"] == menu_num)
                    & (df_faq["Numero_Submenu"] == sub_num)
                ]
                if not encontrado.empty:
                    row = encontrado.iloc[0]
                    registrar_metrica(
                        numero_aluno_norm,
                        row.get("Categoria ", ""),
                        row.get("Processo ", ""),
                        pergunta,
                        "Resposta Submenu",
                        "Sim",
                    )
                    return formatar_resposta(row)

        for _, row in df_faq.iterrows():
            categoria = str(row.get("Categoria ", "")).lower()
            processo = str(row.get("Processo ", "")).lower()
            palavras_chave = str(row.get("Palavras_Chave", "")).lower()

            if (
                (pergunta_limpa in palavras_chave)
                or (pergunta_limpa in processo)
                or (pergunta_limpa in categoria)
            ):
                registrar_metrica(
                    numero_aluno_norm,
                    row.get("Categoria ", ""),
                    row.get("Processo ", ""),
                    pergunta,
                    "Busca Palavra-Chave",
                    "Sim",
                )
                return formatar_resposta(row)

        registrar_metrica(
            numero_aluno_norm,
            "Não Identificado",
            "Fallback",
            pergunta,
            "Opção não encontrada",
            "Não",
        )
        return (
            "Desculpe, não encontrei essa opção no diretório acadêmico. 🤔\n\n"
            "• Digite *menu* para ver as opções disponíveis\n"
            "• Digite *0* para encerrar o atendimento"
        )

    except Exception:
        return (
            "Desculpe, não encontrei essa opção no diretório acadêmico. 🤔\n\n"
            "• Digite *menu* para ver as opções disponíveis\n"
            "• Digite *0* para encerrar o atendimento"
        )


# ==============================================================================
# 4. ENVIO DE MENSAGENS, PAINEL ÚNICO (/) E WEBHOOK
# ==============================================================================

def enviar_mensagem_whatsapp(numero_destino, texto_resposta):
    url = f"{EVOLUTION_API_URL}/message/sendText/{INSTANCE_NAME}"
    headers = {
        "Content-Type": "application/json",
        "apikey": EVOLUTION_API_KEY,
    }
    payload = {
        "number": numero_destino,
        "text": texto_resposta,
    }

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=10)
        print(
            f"[ENVIO API] Status: {response.status_code} | Destino: {numero_destino} | Resp: {response.text}"
        )
        return response.json()
    except Exception as e:
        print(f"[ERRO] Falha ao enviar mensagem: {e}")
        return None


def extrair_dados_webhook(dados):
    data = dados.get("data") if isinstance(dados.get("data"), dict) else dados
    if isinstance(data, list) and data:
        data = data[0]

    key = data.get("key") or {}
    msg_obj = data.get("message") or {}

    remote_jid = str(
        key.get("remoteJid")
        or key.get("remoteJidAlt")
        or data.get("remoteJid")
        or dados.get("remoteJid")
        or ""
    )

    if "@g.us" in remote_jid or "status@broadcast" in remote_jid:
        return "", "", "", "", False

    parte_numero = remote_jid.split("@")[0] if "@" in remote_jid else remote_jid
    numero_bruto = parte_numero or str(
        data.get("senderPn")
        or dados.get("telefone")
        or dados.get("number")
        or ""
    )
    numero_norm = normalizar_telefone(numero_bruto)
    numero_envio = formatar_para_envio(numero_bruto)
    nome_push = str(data.get("pushName") or dados.get("pushName") or "Aluno Novo").strip()
    from_me = bool(key.get("fromMe", dados.get("fromMe", False)))

    texto = (
        msg_obj.get("conversation")
        or (msg_obj.get("extendedTextMessage") or {}).get("text")
        or (msg_obj.get("buttonsResponseMessage") or {}).get("selectedDisplayText")
        or (msg_obj.get("listResponseMessage") or {}).get("title")
        or dados.get("mensagem")
        or dados.get("body")
        or dados.get("text")
        or ""
    )
    return numero_norm, numero_envio, nome_push, str(texto).strip(), from_me


@app.route("/", methods=["GET", "POST"])
@app.route("/iniciar", methods=["GET", "POST"])
def painel_central():
    session.permanent = True
    session["sofia_autenticada"] = True

    alerta = ""
    tipo_alerta = "info"

    if request.method == "POST":
        acao = request.form.get("acao", "")
        if not chave_autenticada(request):
            alerta = "❌ Senha incorreta! Digite a chave de acesso correta."
            tipo_alerta = "erro"
        else:
            if acao == "ativar_webhook":
                ok, msg = configurar_webhook_automatico()
                alerta = f"✅ {msg}" if ok else f"❌ {msg}"
                tipo_alerta = "ok" if ok else "erro"

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
                        registrar_metrica(
                            num_norm,
                            "Contato Ativo",
                            "Disparo Menu Sofia",
                            "Início Ativo",
                            "Menu Enviado",
                            "Sim",
                        )
                    elif modo == "encerrar":
                        texto_envio = mensagem_custom or MENSAGEM_ENCERRAMENTO
                        gerenciar_estado_humano(num_norm, acao="desativar")
                        registrar_metrica(
                            num_norm,
                            "Encerramento",
                            "Disparo Encerramento",
                            texto_envio,
                            "Robô Reativado",
                            "Sim",
                        )
                    else:
                        if eh_mensagem_encerramento(mensagem_custom):
                            gerenciar_estado_humano(num_norm, acao="desativar")
                            texto_envio = mensagem_custom
                        else:
                            gerenciar_estado_humano(num_norm, acao="ativar")
                            texto_envio = (
                                mensagem_custom
                                or "Olá! Tudo bem? Aqui é da equipe acadêmica, como posso ajudar?"
                            )
                        registrar_metrica(
                            num_norm,
                            "Contato Ativo",
                            "Disparo Humano",
                            texto_envio,
                            "Atendimento Humano Iniciado",
                            "Em andamento",
                        )

                    resp = enviar_mensagem_whatsapp(num_envio, texto_envio)
                    if resp and (
                        "key" in str(resp) or "PENDING" in str(resp) or "status" in str(resp)
                    ):
                        alerta = f"✅ Mensagem enviada para {num_envio}!"
                        tipo_alerta = "ok"
                    else:
                        alerta = f"⚠️ Resposta da API: {resp}"
                        tipo_alerta = "erro"

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

    contatos_lista = buscar_contatos_whatsapp()
    pausados_lista = listar_atendimentos_pausados()
    bloqueados_lista = carregar_numeros_bloqueados()

    status_conexao = "Desconhecido"
    try:
        r_st = requests.get(
            f"{EVOLUTION_API_URL}/instance/connectionState/{INSTANCE_NAME}",
            headers={"apikey": EVOLUTION_API_KEY},
            timeout=5,
        )
        if r_st.status_code == 200:
            status_conexao = r_st.json().get("instance", {}).get("state", "open")
    except Exception:
        status_conexao = "open"

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
            const CHAVE_PADRAO = "{{ chave_padrao }}";
            function filtrarContatos() {
                var input = document.getElementById("filtro").value.toLowerCase();
                var select = document.getElementById("contato_salvo");
                for (var i = 0; i < select.options.length; i++) {
                    var txt = select.options[i].text.toLowerCase();
                    select.options[i].style.display = txt.includes(input) ? "" : "none";
                }
            }
            function sincronizarSenha(val) {
                var limpa = (val || CHAVE_PADRAO).trim();
                localStorage.setItem("sofia_chave_acesso", limpa);
                var campos = document.getElementsByClassName("campo-senha-oculto");
                for (var i = 0; i < campos.length; i++) {
                    campos[i].value = limpa;
                }
            }
            window.addEventListener("DOMContentLoaded", function() {
                var salva = localStorage.getItem("sofia_chave_acesso");
                var valorFinal = (salva && salva !== "CHAVE_ACESSO") ? salva : CHAVE_PADRAO;
                var inputSenha = document.getElementById("senha_mestra");
                if (inputSenha) {
                    inputSenha.value = valorFinal;
                }
                sincronizarSenha(valorFinal);
            });
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

            <div class="senha-box" style="margin-top: 15px;">
                <label style="margin:0;">🔑 Chave de Acesso:</label>
                <input type="text" id="senha_mestra" onkeyup="sincronizarSenha(this.value)" placeholder="Digite cetrus2026teste para liberar ações">
                <small style="color:#92400e;">Salva automaticamente por 30 dias (sem precisar redigitar).</small>
            </div>

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
                            <option value="encerrar">Enviar Mensagem de Encerramento (Reativa Robô)</option>
                        </select>

                        <label>Mensagem Manual (opcional):</label>
                        <textarea name="mensagem" rows="3" placeholder="Olá! Aqui é da secretaria acadêmica..."></textarea>

                        <button type="submit" class="btn btn-teal">🚀 Enviar Mensagem Agora</button>
                    </form>
                </div>

                <div class="card">
                    <h2>🧑‍💻 Atendimentos Humanos (Robô Pausado)</h2>
                    <p style="font-size: 13px; color: #64748b; margin-top: 0;">
                        Quando um aluno digita <b>5</b> ou o atendente envia mensagem, o número aparece aqui e o robô fica em silêncio até clicar em <b>Reativar</b>, enviar <b>#finalizar</b> ou enviar a mensagem de encerramento no WhatsApp.
                    </p>

                    <form method="POST" style="display:flex; gap:8px; margin-bottom:15px;">
                        <input type="hidden" name="acao" value="pausar_robo">
                        <input type="hidden" name="senha" class="campo-senha-oculto">
                        <input type="text" name="num_pausar" placeholder="Pausar número (DDD + Num)" style="margin-top:0;">
                        <button type="submit" class="btn btn-warning" style="white-space:nowrap;">⏸ Pausar</button>
                    </form>

                    {% if pausados %}
                        {% for p in pausados %}
                            <div class="lista-item">
                                <span><b>{{ p }}</b></span>
                                <form method="POST" style="margin:0;">
                                    <input type="hidden" name="acao" value="reativar_robo">
                                    <input type="hidden" name="senha" class="campo-senha-oculto">
                                    <input type="hidden" name="num_target" value="{{ p }}">
                                    <button type="submit" class="btn btn-success" style="padding:6px 12px; font-size:12px;">▶️ Devolver ao Robô</button>
                                </form>
                            </div>
                        {% endfor %}
                    {% else %}
                        <p style="text-align:center; color:#94a3b8; padding: 20px 0;">Nenhum atendimento humano ativo no momento.</p>
                    {% endif %}
                </div>
            </div>
        </div>
    </body>
    </html>
    """
    return render_template_string(
        html,
        instancia=INSTANCE_NAME,
        total_bloqueados=len(bloqueados_lista),
        status_conexao=status_conexao,
        chave_padrao=API_KEY,
        total_contatos=len(contatos_lista),
        contatos=contatos_lista,
        pausados=pausados_lista,
    )


@app.route("/metricas", methods=["GET"])
def baixar_metricas():
    if not os.path.exists(EXCEL_FILE):
        return jsonify({"erro": "Arquivo Excel não encontrado."}), 404
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        todas_abas = pd.read_excel(EXCEL_FILE, sheet_name=None)
        for nome_aba, df_aba in todas_abas.items():
            df_aba.to_excel(writer, sheet_name=nome_aba, index=False)
    output.seek(0)
    return send_file(
        output,
        as_attachment=True,
        download_name="metricas_sofia_oficial.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@app.route("/webhook", methods=["POST"])
def webhook():
    dados = request.get_json(silent=True) or {}
    numero_norm, numero_envio, nome_push, texto, from_me = extrair_dados_webhook(dados)

    if not numero_norm or not numero_envio or not texto:
        return jsonify({"status": "ignorado", "motivo": "sem_numero_ou_texto"}), 200

    # Alunos novos entram livremente; apenas números bloqueados são ignorados
    bloqueados = carregar_numeros_bloqueados()
    if numero_norm in bloqueados:
        return jsonify({"status": "bloqueado"}), 200

    if from_me:
        if eh_mensagem_encerramento(texto):
            gerenciar_estado_humano(numero_norm, acao="desativar", nome_aluno=nome_push)
            return jsonify({"status": "reativado_por_encerramento"}), 200
        if not dados.get("enviado_pelo_bot", False):
            gerenciar_estado_humano(numero_norm, acao="ativar", nome_aluno=nome_push)
            return jsonify({"status": "pausado_por_humano"}), 200
        return jsonify({"status": "mensagem_bot"}), 200

    if gerenciar_estado_humano(numero_norm, acao="verificar", nome_aluno=nome_push) and not eh_mensagem_encerramento(texto):
        return jsonify({"status": "robô_pausado"}), 200

    resposta = consultar_excel(texto, numero_norm)
    enviar_mensagem_whatsapp(numero_envio, resposta)
    return jsonify({"status": "ok", "numero": numero_norm}), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True)