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
INSTANCE_NAME = "sofia-oficial"
API_KEY = "cetrus2026teste"
WEBHOOK_PUBLIC_URL = "https://chattest-production-37ef5795.up.railway.app/webhook"

EXCEL_FILE = "diretorio_academico.xlsx"
FUSO_BR = timezone(timedelta(hours=-3))

pausa_memoria = set()


# ==============================================================================
# 2. FUNÇÕES AUXILIARES (NORMALIZAÇÃO, BLOQUEIO, PAUSA E HISTÓRICO)
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
    headers = {"Content-Type": "application/json", "apikey": API_KEY}
    payload = {
        "enabled": True,
        "url": WEBHOOK_PUBLIC_URL,
        "webhookByEvents": False,
        "events": ["MESSAGES_UPSERT", "SEND_MESSAGE"]
    }
    try:
        r = requests.post(f"{EVOLUTION_API_URL}/webhook/set/{INSTANCE_NAME}", json=payload, headers=headers, timeout=10)
        if r.status_code in [200, 201]:
            return True, "Webhook ativado com sucesso!"
        return False, f"Erro HTTP {r.status_code}: {r.text}"
    except Exception as e:
        return False, f"Falha de conexão: {str(e)}"


def buscar_contatos_whatsapp():
    contatos_unicos = {}
    headers = {"Content-Type": "application/json", "apikey": API_KEY}
    try:
        resp_contacts = requests.post(f"{EVOLUTION_API_URL}/chat/findContacts/{INSTANCE_NAME}", json={},
                                      headers=headers, timeout=8)
        if resp_contacts.status_code == 200:
            for item in resp_contacts.json() if isinstance(resp_contacts.json(), list) else []:
                jid = item.get("remoteJid") or item.get("id") or ""
                if "@s.whatsapp.net" in jid:
                    num = jid.split("@")[0]
                    nome = item.get("pushName") or item.get("name") or num
                    contatos_unicos[num] = f"{nome} ({num})"
    except Exception as e:
        print(f"[AVISO] findContacts: {e}")

    try:
        resp_chats = requests.post(f"{EVOLUTION_API_URL}/chat/findChats/{INSTANCE_NAME}", json={}, headers=headers,
                                   timeout=8)
        if resp_chats.status_code == 200:
            for chat in resp_chats.json() if isinstance(resp_chats.json(), list) else []:
                jid = chat.get("remoteJid") or chat.get("id") or ""
                if "@s.whatsapp.net" in jid:
                    num = jid.split("@")[0]
                    if num not in contatos_unicos:
                        nome = chat.get("name") or chat.get("pushName") or num
                        contatos_unicos[num] = f"{nome} ({num})"
    except Exception as e:
        print(f"[AVISO] findChats: {e}")

    lista_ordenada = [{"numero": k, "rotulo": v} for k, v in contatos_unicos.items()]
    lista_ordenada.sort(key=lambda x: x["rotulo"].lower())
    return lista_ordenada


def buscar_mensagens_recentes():
    """Busca as últimas conversas/mensagens gravadas nas métricas do Excel para exibição rápida."""
    try:
        if not os.path.exists(EXCEL_FILE):
            return []
        todas_abas = pd.read_excel(EXCEL_FILE, sheet_name=None)
        nome_aba = "Metricas_Atendimentos " if "Metricas_Atendimentos " in todas_abas else "Metricas_Atendimentos"
        df = todas_abas.get(nome_aba, pd.DataFrame())
        if df.empty:
            return []
        # Retorna as últimas 10 interações em ordem decrescente
        ultimas = df.tail(10).to_dict(orient="records")
        ultimas.reverse()
        return ultimas
    except Exception as e:
        print(f"[AVISO] Erro ao buscar historico: {e}")
        return []


def listar_atendimentos_pausados():
    pausados = set(pausa_memoria)
    try:
        if os.path.exists(EXCEL_FILE):
            df_controle = pd.read_excel(EXCEL_FILE, sheet_name="Controle_Alunos")
            if not df_controle.empty and "Humano" in df_controle.columns:
                ativos = df_controle[df_controle["Humano"].astype(str).str.upper() == "SIM"]
                for num in ativos["ID_Usuario"].dropna().tolist():
                    pausados.add(str(num))
    except Exception as e:
        print(f"[AVISO] Erro pausados: {e}")
    return sorted(list(pausados))


def carregar_numeros_bloqueados():
    try:
        if not os.path.exists(EXCEL_FILE):
            return []
        df_bloq = pd.read_excel(EXCEL_FILE, sheet_name="TelefoneContatos_Bloqueados")
        return [normalizar_telefone(num) for num in df_bloq["Telefone"].dropna().tolist()]
    except Exception:
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
        df_controle = todas_abas.get("Controle_Alunos", pd.DataFrame(
            columns=["ID_Usuario", "Nome_Aluno", "Status", "Ultima_Interacao", "Etapa", "Humano"]))
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
                    "ID_Usuario": numero_norm, "Nome_Aluno": "Aluno WhatsApp",
                    "Status": "Ativo", "Ultima_Interacao": agora_brasil(),
                    "Etapa": etapa_atual, "Humano": status_humano
                }])
                df_controle = pd.concat([df_controle, nova_linha], ignore_index=True)

            todas_abas["Controle_Alunos"] = df_controle
            with pd.ExcelWriter(EXCEL_FILE, engine="openpyxl") as writer:
                for k, v in todas_abas.items():
                    v.to_excel(writer, sheet_name=k, index=False)
            return True
    except Exception as e:
        print(f"[ERRO] Estado humano: {e}")
        return numero_norm in pausa_memoria


def registrar_metrica(numero_aluno, categoria, processo, duvida, desfecho, resolvido="Sim"):
    try:
        if not os.path.exists(EXCEL_FILE):
            return
        todas_abas = pd.read_excel(EXCEL_FILE, sheet_name=None)
        nome_aba = "Metricas_Atendimentos " if "Metricas_Atendimentos " in todas_abas else "Metricas_Atendimentos"
        df_met = todas_abas.get(nome_aba, pd.DataFrame(
            columns=["ID_Log", "Data_Hora", "ID_Usuario", "Canal", "Categoria", "Processo", "Duvida_Aluno", "Desfecho",
                     "Resolvido"]))

        novo_id = len(df_met) + 1
        nova_linha = pd.DataFrame([{
            "ID_Log": novo_id, "Data_Hora": agora_brasil(), "ID_Usuario": numero_aluno,
            "Canal": "WhatsApp", "Categoria": categoria, "Processo": processo,
            "Duvida_Aluno": duvida, "Desfecho": desfecho, "Resolvido": resolvido
        }])
        todas_abas[nome_aba] = pd.concat([df_met, nova_linha], ignore_index=True)
        with pd.ExcelWriter(EXCEL_FILE, engine="openpyxl") as writer:
            for k, v in todas_abas.items():
                v.to_excel(writer, sheet_name=k, index=False)
    except Exception as e:
        print(f"[ERRO] Metrica: {e}")


def gerar_texto_menu_principal():
    df_menu = pd.read_excel(EXCEL_FILE, sheet_name="Menu_Principal")
    menu_texto = "👋 Bem-vinda(o) ao atendimento acadêmico da Sofia!\n\nEscolha uma das opções abaixo digitando apenas o *número* correspondente:\n\n"
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
    texto_final += "\n\n-----------------------------------\n💡 *Como continuar:*\n• Digite *menu* para voltar ao início\n• Digite *0* para encerrar o atendimento"
    return texto_final


def consultar_excel(pergunta, numero_aluno_norm):
    try:
        if not os.path.exists(EXCEL_FILE):
            return "⚠️ Erro: Arquivo de diretório acadêmico não encontrado."
        pergunta_limpa = pergunta.strip().lower()

        if pergunta_limpa in ["0", "encerrar", "sair", "#finalizar"]:
            gerenciar_estado_humano(numero_aluno_norm, acao="desativar")
            registrar_metrica(numero_aluno_norm, "Encerramento", "Saída do Usuário", pergunta, "Atendimento Encerrado",
                              "Sim")
            return "Atendimento encerrado. Obrigado pelo contato! 💙\n\nSe precisar de algo, é só enviar uma nova mensagem."

        if pergunta_limpa in ["oi", "ola", "olá", "menu", "ajuda"]:
            registrar_metrica(numero_aluno_norm, "Menu Principal", "Início", pergunta, "Exibiu Menu", "Sim")
            return gerar_texto_menu_principal()

        df_faq = pd.read_excel(EXCEL_FILE, sheet_name="FAQ_BaseNumero_Menu")

        if pergunta_limpa.isdigit() and len(pergunta_limpa) == 1:
            num_opcao = float(pergunta_limpa)
            if num_opcao == 5.0:
                gerenciar_estado_humano(numero_aluno_norm, acao="ativar")
                registrar_metrica(numero_aluno_norm, "Atendimento Humano", "Transbordo", pergunta,
                                  "Transferido para Humano", "Em andamento")
                filtrado_5 = df_faq[df_faq['Numero_Menu'] == 5.0]
                msg = "👩‍💻 *Transferindo para o Atendimento Humano...*\n\nAguarde um momento, já vamos falar com você.\n\n_(O robô ficará pausado durante este atendimento)_"
                return msg

            filtrado = df_faq[df_faq['Numero_Menu'] == num_opcao]
            if not filtrado.empty:
                if len(filtrado) == 1:
                    row = filtrado.iloc[0]
                    registrar_metrica(numero_aluno_norm, row.get('Categoria ', ''), row.get('Processo ', ''), pergunta,
                                      "Resposta Direta", "Sim")
                    return formatar_resposta(row)
                cat_name = filtrado.iloc[0].get('Categoria ', 'Atendimento')
                registrar_metrica(numero_aluno_norm, cat_name, "Submenu", pergunta, "Exibiu Submenus", "Sim")
                sub_texto = f"📌 *{cat_name}*\n\nDigite o número da opção (ex: *{int(num_opcao)}.1*):\n\n"
                for _, row in filtrado.iterrows():
                    sub_texto += f"• *{int(num_opcao)}.{int(row.get('Numero_Submenu', 1))}* — {row.get('Processo ', '')}\n"
                return sub_texto

        if "." in pergunta_limpa:
            partes = pergunta_limpa.split(".")
            menu_num, sub_num = float(partes[0]), float(partes[-1])
            if menu_num == 5.0:
                gerenciar_estado_humano(numero_aluno_norm, acao="ativar")
            encontrado = df_faq[(df_faq['Numero_Menu'] == menu_num) & (df_faq['Numero_Submenu'] == sub_num)]
            if not encontrado.empty:
                row = encontrado.iloc[0]
                registrar_metrica(numero_aluno_norm, row.get('Categoria ', ''), row.get('Processo ', ''), pergunta,
                                  "Submenu", "Sim")
                return formatar_resposta(row)

        for _, row in df_faq.iterrows():
            if pergunta_limpa in str(row.get("Palavras_Chave", "")).lower() or pergunta_limpa in str(
                    row.get("Processo ", "")).lower():
                registrar_metrica(numero_aluno_norm, row.get('Categoria ', ''), row.get('Processo ', ''), pergunta,
                                  "Busca", "Sim")
                return formatar_resposta(row)

        registrar_metrica(numero_aluno_norm, "Não Identificado", "Fallback", pergunta, "Não encontrado", "Não")
        return "Desculpe, não encontrei essa opção. 🤔\n\n• Digite *menu* para ver as opções\n• Digite *0* para encerrar"
    except Exception as e:
        return f"Erro ao processar: {str(e)}"


def enviar_mensagem_whatsapp(numero_destino, texto_resposta):
    url = f"{EVOLUTION_API_URL}/message/sendText/{INSTANCE_NAME}"
    headers = {"Content-Type": "application/json", "apikey": API_KEY}
    payload = {"number": numero_destino, "text": texto_resposta}
    try:
        r = requests.post(url, json=payload, headers=headers, timeout=10)
        return r.json()
    except Exception as e:
        print(f"[ERRO] Envio WhatsApp: {e}")
        return None


# ==============================================================================
# 4. PAINEL ÚNICO (/) COM HISTÓRICO EM TEMPO REAL
# ==============================================================================
@app.route("/", methods=["GET", "POST"])
def painel_central():
    alerta = ""
    tipo_alerta = "info"

    if request.method == "POST":
        acao = request.form.get("acao", "")
        senha = request.form.get("senha", "")

        if senha != API_KEY:
            alerta = "❌ Senha incorreta!"
            tipo_alerta = "erro"
        else:
            if acao == "ativar_webhook":
                ok, msg = configurar_webhook_automatico()
                alerta, tipo_alerta = (f"✅ {msg}", "ok") if ok else (f"❌ {msg}", "erro")
            elif acao == "iniciar_contato":
                contato_salvo = request.form.get("contato_salvo", "").strip()
                numero_manual = request.form.get("numero_manual", "").strip()
                modo = request.form.get("modo", "menu")
                mensagem_custom = request.form.get("mensagem", "").strip()

                numero_final = numero_manual if numero_manual else contato_salvo
                if not numero_final:
                    alerta, tipo_alerta = "❌ Selecione um contato ou digite um número.", "erro"
                else:
                    num_envio = formatar_para_envio(numero_final)
                    num_norm = normalizar_telefone(num_envio)
                    if modo == "menu":
                        gerenciar_estado_humano(num_norm, acao="desativar")
                        texto_envio = gerar_texto_menu_principal()
                        registrar_metrica(num_norm, "Contato Ativo", "Disparo Menu", "Início Ativo", "Menu Enviado",
                                          "Sim")
                    else:
                        gerenciar_estado_humano(num_norm, acao="ativar")
                        texto_envio = mensagem_custom or "Olá! Tudo bem? Aqui é da secretaria acadêmica."
                        registrar_metrica(num_norm, "Contato Ativo", "Disparo Humano", texto_envio, "Humano Iniciado",
                                          "Em andamento")

                    resp = enviar_mensagem_whatsapp(num_envio, texto_envio)
                    alerta, tipo_alerta = (f"✅ Mensagem enviada para {num_envio}!", "ok") if resp else (
                        "⚠️ Falha ao enviar.", "erro")
            elif acao == "reativar_robo":
                num_target = request.form.get("num_target", "")
                gerenciar_estado_humano(num_target, acao="desativar")
                alerta, tipo_alerta = f"✅ Robô reativado para {num_target}!", "ok"
            elif acao == "pausar_robo":
                num_pausar = normalizar_telefone(request.form.get("num_pausar", ""))
                if num_pausar:
                    gerenciar_estado_humano(num_pausar, acao="ativar")
                    alerta, tipo_alerta = f"⏸️ Robô pausado para {num_pausar}.", "ok"

    contatos_lista = buscar_contatos_whatsapp()
    pausados_lista = listar_atendimentos_pausados()
    bloqueados_lista = carregar_numeros_bloqueados()
    historico_recente = buscar_mensagens_recentes()

    status_conexao = "open"
    try:
        r_st = requests.get(f"{EVOLUTION_API_URL}/instance/connectionState/{INSTANCE_NAME}",
                            headers={"apikey": API_KEY}, timeout=5)
        if r_st.status_code == 200:
            status_conexao = r_st.json().get("instance", {}).get("state", "open")
    except Exception:
        pass

    html = """
    <!DOCTYPE html>
    <html lang="pt-BR">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Painel Central — Sofia Oficial</title>
        <style>
            body { font-family: 'Segoe UI', Arial, sans-serif; background: #f1f5f9; margin: 0; padding: 20px; color: #1e293b; }
            .container { max-width: 1050px; margin: auto; }
            .header { background: #0f766e; color: white; padding: 20px 25px; border-radius: 12px; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 15px; }
            .header h1 { margin: 0; font-size: 22px; }
            .badge { background: rgba(255,255,255,0.2); padding: 6px 12px; border-radius: 20px; font-size: 13px; }
            .toolbar { display: flex; gap: 10px; flex-wrap: wrap; margin: 20px 0; }
            .btn { padding: 11px 18px; border: none; border-radius: 8px; font-weight: bold; cursor: pointer; text-decoration: none; font-size: 14px; display: inline-flex; align-items: center; gap: 6px; }
            .btn-primary { background: #0284c7; color: white; }
            .btn-success { background: #16a34a; color: white; }
            .btn-warning { background: #d97706; color: white; }
            .btn-teal { background: #0f766e; color: white; width: 100%; justify-content: center; font-size: 15px; margin-top: 15px; }
            .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; margin-bottom: 20px; }
            @media (max-width: 768px) { .grid { grid-template-columns: 1fr; } }
            .card { background: white; padding: 22px; border-radius: 12px; box-shadow: 0 2px 8px rgba(0,0,0,0.06); }
            .card h2 { margin-top: 0; font-size: 18px; color: #0f766e; border-bottom: 1px solid #e2e8f0; padding-bottom: 10px; }
            label { display: block; margin-top: 12px; font-weight: 600; font-size: 13px; color: #475569; }
            input, select, textarea { width: 100%; padding: 10px; margin-top: 5px; border: 1px solid #cbd5e1; border-radius: 6px; box-sizing: border-box; font-size: 14px; }
            .alerta { padding: 14px; border-radius: 8px; margin-bottom: 20px; font-weight: bold; }
            .alerta.ok { background: #dcfce7; color: #166534; border: 1px solid #86efac; }
            .alerta.erro { background: #fee2e2; color: #991b1b; border: 1px solid #fca5a5; }
            .lista-item { display: flex; justify-content: space-between; align-items: center; padding: 10px; border-bottom: 1px solid #f1f5f9; font-size: 13px; }
            .senha-box { background: #fffbeb; border: 1px solid #fde68a; padding: 12px 18px; border-radius: 8px; margin-bottom: 15px; display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
            .senha-box input { max-width: 240px; margin-top: 0; }
            table { width: 100%; border-collapse: collapse; font-size: 12px; margin-top: 10px; }
            th, td { padding: 8px 10px; text-align: left; border-bottom: 1px solid #e2e8f0; }
            th { background: #f8fafc; color: #475569; }
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
                for (var i = 0; i < campos.length; i++) { campos[i].value = val; }
            }
        </script>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <div>
                    <h1>🤖 Painel Central — Sofia Oficial</h1>
                    <small>Instância: <b>{{ instancia }}</b> | Bloqueados: <b>{{ total_bloqueados }}</b></small>
                </div>
                <div class="badge">Status WhatsApp: <b>{{ status_conexao }}</b></div>
            </div>

            <div class="senha-box" style="margin-top: 15px;">
                <label style="margin:0;">🔑 Chave de Acesso:</label>
                <input type="password" id="senha_mestra" onkeyup="sincronizarSenha(this.value)" placeholder="Digite cetrus2026teste">
                <small style="color:#92400e;">Digite acima para liberar os botões do painel.</small>
            </div>

            <div class="toolbar">
                <form method="POST" style="margin:0;">
                    <input type="hidden" name="acao" value="ativar_webhook">
                    <input type="hidden" name="senha" class="campo-senha-oculto">
                    <button type="submit" class="btn btn-warning">🔌 1. Ativar Webhook Oficial</button>
                </form>
                <a href="/metricas" class="btn btn-success">📥 2. Baixar Planilha (.xlsx)</a>
                <a href="/" class="btn btn-primary">🔄 3. Atualizar Dados</a>
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

                        <label>Contatos Sincronizados ({{ total_contatos }}):</label>
                        <input type="text" id="filtro" onkeyup="filtrarContatos()" placeholder="🔍 Filtrar por nome ou número...">
                        <select name="contato_salvo" id="contato_salvo">
                            <option value="">-- Selecione um contato --</option>
                            {% for c in contatos %}
                                <option value="{{ c.numero }}">{{ c.rotulo }}</option>
                            {% endfor %}
                        </select>

                        <label>OU Número Manual (com DDD):</label>
                        <input type="text" name="numero_manual" placeholder="Ex: 11988887777">

                        <label>Tipo de Envio:</label>
                        <select name="modo">
                            <option value="menu">Enviar Menu Automático da Sofia</option>
                            <option value="humano">Mensagem Manual (Pausar Robô)</option>
                        </select>

                        <label>Mensagem Personalizada:</label>
                        <textarea name="mensagem" rows="2" placeholder="Olá! Aqui é da secretaria..."></textarea>

                        <button type="submit" class="btn btn-teal">🚀 Enviar Mensagem</button>
                    </form>
                </div>

                <!-- COLUNA 2: ATENDIMENTO HUMANO / PAUSADOS -->
                <div class="card">
                    <h2>👩‍💻 Atendimentos Humanos</h2>
                    <p style="font-size:12px; color:#64748b;">Alunos em atendimento humano (robô em silêncio).</p>

                    <form method="POST" style="display:flex; gap:8px; margin-bottom:15px;">
                        <input type="hidden" name="acao" value="pausar_robo">
                        <input type="hidden" name="senha" class="campo-senha-oculto">
                        <input type="text" name="num_pausar" placeholder="Pausar (DDD + Num)" style="margin:0;">
                        <button type="submit" class="btn btn-warning" style="white-space:nowrap;">⏸️ Pausar</button>
                    </form>

                    {% if pausados %}
                        {% for num in pausados %}
                            <div class="lista-item">
                                <span>📱 <b>{{ num }}</b></span>
                                <form method="POST" style="margin:0;">
                                    <input type="hidden" name="acao" value="reativar_robo">
                                    <input type="hidden" name="senha" class="campo-senha-oculto">
                                    <input type="hidden" name="num_target" value="{{ num }}">
                                    <button type="submit" class="btn btn-success" style="padding:4px 10px; font-size:11px;">▶️ Devolver ao Robô</button>
                                </form>
                            </div>
                        {% endfor %}
                    {% else %}
                        <p style="text-align:center; color:#94a3b8; padding:15px 0; font-size:13px;">Nenhum atendimento pausado.</p>
                    {% endif %}
                </div>
            </div>

            <!-- BLOCO INFERIOR: HISTÓRICO DE MENSAGENS RECENTES -->
            <div class="card" style="margin-bottom: 30px;">
                <h2>💬 Acompanhamento das Últimas Interações (Histórico do Bot)</h2>
                {% if historico %}
                    <table>
                        <thead>
                            <tr>
                                <th>Data/Hora</th>
                                <th>Aluno (ID)</th>
                                <th>Categoria / Ação</th>
                                <th>Mensagem / Dúvida</th>
                                <th>Desfecho</th>
                            </tr>
                        </thead>
                        <tbody>
                            {% for h in historico %}
                                <tr>
                                    <td>{{ h.Data_Hora }}</td>
                                    <td><b>{{ h.ID_Usuario }}</b></td>
                                    <td>{{ h.Categoria }}</td>
                                    <td>{{ h.Duvida_Aluno }}</td>
                                    <td><span style="color: #0f766e; font-weight:600;">{{ h.Desfecho }}</span></td>
                                </tr>
                            {% endfor %}
                        </tbody>
                    </table>
                {% else %}
                    <p style="text-align:center; color:#94a3b8; padding:15px 0;">Nenhuma interação registada ainda. Envie uma mensagem para testar!</p>
                {% endif %}
            </div>
        </div>
    </body>
    </html>
    """
    return render_template_string(
        html, alerta=alerta, tipo_alerta=tipo_alerta, instancia=INSTANCE_NAME,
        status_conexao=status_conexao, contatos=contatos_lista, total_contatos=len(contatos_lista),
        pausados=pausados_lista, total_bloqueados=len(bloqueados_lista), historico=historico_recente
    )


@app.route("/metricas", methods=["GET"])
def baixar_metricas():
    if os.path.exists(EXCEL_FILE):
        return send_file(EXCEL_FILE, as_attachment=True,
                         download_name=f"relatorio_sofia_{datetime.now(FUSO_BR).strftime('%Y%m%d_%H%M')}.xlsx")
    return jsonify({"erro": "Planilha não encontrada."}), 404


@app.route("/status", methods=["GET"])
def verificar_status():
    try:
        resp = requests.get(f"{EVOLUTION_API_URL}/instance/fetchInstances", headers={"apikey": API_KEY}, timeout=10)
        return jsonify({"servidor": "ONLINE", "instancia": INSTANCE_NAME, "instancias": resp.json()}), 200
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
            texto_mensagem = message_content.get("conversation") or message_content.get("extendedTextMessage", {}).get(
                "text", "")

            if not texto_mensagem or not numero_norm:
                return jsonify({"status": "ignored_empty"}), 200

            if from_me:
                if "#finalizar" in texto_mensagem.lower():
                    gerenciar_estado_humano(numero_norm, acao="desativar")
                    registrar_metrica(numero_norm, "Atendimento Humano", "Finalização", "#finalizar", "Concluído",
                                      "Sim")
                    enviar_mensagem_whatsapp(jid_real,
                                             "Atendimento humano finalizado. Obrigado! 💙\n\nEnvie *oi* ou *menu* para reiniciar.")
                elif not texto_mensagem.startswith(("👋", "📌", "👩‍💻", "Desculpe,", "Atendimento encerrado")):
                    gerenciar_estado_humano(numero_norm, acao="ativar")
                return jsonify({"status": "from_me_processed"}), 200

            if numero_norm in carregar_numeros_bloqueados():
                return jsonify({"status": "blocked"}), 200

            if gerenciar_estado_humano(numero_norm, acao="verificar"):
                if texto_mensagem.strip().lower() in ["0", "#finalizar"]:
                    gerenciar_estado_humano(numero_norm, acao="desativar")
                    registrar_metrica(numero_norm, "Atendimento Humano", "Encerrado Aluno", texto_mensagem,
                                      "Encerrado pelo Aluno", "Sim")
                    enviar_mensagem_whatsapp(jid_real, "Atendimento encerrado. Obrigado pelo contato! 💙")
                return jsonify({"status": "paused_for_human"}), 200

            resposta_bot = consultar_excel(texto_mensagem, numero_norm)
            enviar_mensagem_whatsapp(jid_real, resposta_bot)
    except Exception as e:
        print(f"[ERRO WEBHOOK]: {e}")
    return jsonify({"status": "success"}), 200


if __name__ == "__main__":
    porta = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=porta)