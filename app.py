import io
import os
import re
from datetime import datetime, timedelta
from functools import wraps
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

# ==========================================
# CONFIGURAÇÕES GERAIS E EVOLUTION API
# ==========================================
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "sofia-oficial-cetrus-2026-chave-fixa")
CHAVE_ACESSO_PADRAO = "cetrus2026teste"

WHATSAPP_API_URL = os.environ.get(
    "WHATSAPP_API_URL", "https://evolution-api-production-db931012.up.railway.app"
).rstrip("/")
WHATSAPP_API_KEY = os.environ.get("WHATSAPP_API_KEY", "").strip()
INSTANCIA_NOME = os.environ.get("WHATSAPP_INSTANCE", "sofia-oficial").strip()
STATUS_WHATSAPP = os.environ.get("STATUS_WHATSAPP", "open")

ARQUIVO_PLANILHA = os.path.join(os.path.dirname(__file__), "diretorio_academico.xlsx")

app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=30)
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

MENSAGEM_ENCERRAMENTO = (
    "Atendimento encerrado. Obrigado pelo contato! 💙\n\n"
    "Se precisar de algo, é só enviar uma nova mensagem a qualquer momento"
)

conversas = {}
metricas_log = []


# ==========================================
# NORMALIZAÇÃO E PROTEÇÃO CONTRA ERROS
# ==========================================

def normalizar_telefone(telefone: str) -> str:
    numeros = re.sub(r"\D", "", str(telefone or ""))
    if not numeros:
        return ""
    # Normaliza DDD + 8 dígitos (10 dígitos) ou DDD + 9 dígitos (11 dígitos) com DDI 55
    if len(numeros) in (10, 11):
        numeros = "55" + numeros
    return numeros


def normalizar_texto(texto: str) -> str:
    if not texto:
        return ""
    return str(texto).replace("\\n", "\n").strip()


def eh_mensagem_encerramento(texto: str) -> bool:
    limpo = re.sub(r"\s+", " ", normalizar_texto(texto)).lower()
    alvo = re.sub(r"\s+", " ", MENSAGEM_ENCERRAMENTO).lower()
    return (
        limpo == alvo
        or limpo == "#finalizar"
        or "atendimento encerrado. obrigado pelo contato!" in limpo
    )


def converter_numero_seguro(texto: str):
    """Evita 'could not convert string to float' em frases ou textos livres."""
    limpo = normalizar_texto(texto).replace(",", ".")
    try:
        return float(limpo)
    except (ValueError, TypeError):
        return None


# ==========================================
# PLANILHA EXCEL (CONTATOS, BLOQUEADOS E MÉTRICAS)
# ==========================================

def carregar_bloqueados_excel() -> set:
    bloqueados = set()
    if not os.path.exists(ARQUIVO_PLANILHA):
        return bloqueados
    try:
        xls = pd.ExcelFile(ARQUIVO_PLANILHA)
        if "TelefoneContatos_Bloqueados" in xls.sheet_names:
            df_bloq = pd.read_excel(ARQUIVO_PLANILHA, sheet_name="TelefoneContatos_Bloqueados")
            for _, row in df_bloq.iterrows():
                for val in row.values:
                    tel = normalizar_telefone(val)
                    if len(tel) >= 12:
                        bloqueados.add(tel)
    except Exception as exc:
        print(f"[AVISO BLOQUEADOS] {exc}")
    return bloqueados


def carregar_contatos_excel() -> list:
    contatos = []
    if os.path.exists(ARQUIVO_PLANILHA):
        try:
            df = pd.read_excel(ARQUIVO_PLANILHA)
            for _, row in df.iterrows():
                nome = str(
                    row.get("nome") or row.get("Nome") or row.get("aluno") or row.get("Aluno") or "Contato"
                ).strip()
                tel = normalizar_telefone(
                    row.get("telefone")
                    or row.get("Telefone")
                    or row.get("celular")
                    or row.get("Celular")
                    or row.get("numero")
                    or ""
                )
                if tel:
                    contatos.append({"nome": nome, "telefone": tel})
        except Exception as exc:
            print(f"[AVISO CONTATOS EXCEL] {exc}")

    if not contatos:
        contatos = [
            {"nome": "Mayara Cetrus Semi", "telefone": "5511999990001"},
            {"nome": "Aluno Teste Secretaria", "telefone": "5511957735761"},
        ]
    return contatos


def buscar_contatos_evolution() -> list:
    if not WHATSAPP_API_URL or not WHATSAPP_API_KEY:
        return []
    url = f"{WHATSAPP_API_URL}/chat/findContacts/{INSTANCIA_NOME}"
    headers = {"Content-Type": "application/json", "apikey": WHATSAPP_API_KEY}
    encontrados = []
    try:
        resp = requests.post(url, json={}, headers=headers, timeout=12)
        if 200 <= resp.status_code < 300:
            data = resp.json()
            lista = data if isinstance(data, list) else data.get("data") or data.get("contacts") or []
            for item in lista:
                if not isinstance(item, dict):
                    continue
                jid = str(item.get("remoteJid") or item.get("id") or "")
                if "@g.us" in jid:
                    continue
                tel = normalizar_telefone(jid.split("@")[0] or item.get("number") or "")
                nome = str(item.get("pushName") or item.get("name") or tel).strip()
                if tel:
                    encontrados.append({"nome": nome, "telefone": tel})
    except Exception as exc:
        print(f"[AVISO CONTATOS EVOLUTION] {exc}")
    return encontrados


def obter_lista_contatos_combinada() -> list:
    combinados = {}
    for c in carregar_contatos_excel():
        combinados[c["telefone"]] = c
    for c in buscar_contatos_evolution():
        if c["telefone"] not in combinados:
            combinados[c["telefone"]] = c
    return list(combinados.values())


def registrar_metrica(telefone: str, evento: str, detalhe: str = ""):
    metricas_log.append({
        "data_hora": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "telefone": normalizar_telefone(telefone),
        "evento": evento,
        "detalhe": detalhe,
    })


def gerar_arquivo_metricas_xlsx() -> io.BytesIO:
    output = io.BytesIO()
    bloqueados = sorted(list(carregar_bloqueados_excel()))
    df_metricas = pd.DataFrame(
        metricas_log if metricas_log else [{"data_hora": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "telefone": "-", "evento": "SEM_REGISTROS", "detalhe": "Nenhum atendimento registrado ainda."}]
    )
    df_bloq = pd.DataFrame({"telefone_bloqueado": bloqueados})

    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df_metricas.to_excel(writer, sheet_name="Metricas_Atendimentos", index=False)
        df_bloq.to_excel(writer, sheet_name="TelefoneContatos_Bloqueados", index=False)

    output.seek(0)
    return output


# ==========================================
# ESTADO DO ROBÔ E PAUSA HUMANA
# ==========================================

def obter_conversa(telefone: str) -> dict:
    tel = normalizar_telefone(telefone)
    if tel not in conversas:
        conversas[tel] = {
            "telefone": tel,
            "robo_pausado": False,
            "etapa": "menu_inicial",
            "atualizado_em": datetime.now().strftime("%d/%m/%Y %H:%M"),
        }
    return conversas[tel]


def pausar_para_humano(telefone: str, motivo: str = "intervencao_humana") -> dict:
    conv = obter_conversa(telefone)
    conv["robo_pausado"] = True
    conv["etapa"] = "atendimento_humano"
    conv["atualizado_em"] = datetime.now().strftime("%d/%m/%Y %H:%M")
    registrar_metrica(telefone, "PAUSADO_HUMANO", motivo)
    return conv


def reativar_robo(telefone: str, motivo: str = "encerramento_ou_reativacao") -> dict:
    conv = obter_conversa(telefone)
    conv["robo_pausado"] = False
    conv["etapa"] = "menu_inicial"
    conv["atualizado_em"] = datetime.now().strftime("%d/%m/%Y %H:%M")
    registrar_metrica(telefone, "REATIVADO_ROBO", motivo)
    return conv


# ==========================================
# EVOLUTION API (ENVIO E WEBHOOK)
# ==========================================

def extrair_dados_evolution(dados: dict) -> tuple:
    if "data" in dados and isinstance(dados["data"], dict):
        data = dados["data"]
        key = data.get("key") or {}
        msg_obj = data.get("message") or {}

        remote_jid = str(key.get("remoteJid") or "")
        if "@g.us" in remote_jid or "status@broadcast" in remote_jid:
            return "", "", False

        telefone = normalizar_telefone(remote_jid.split("@")[0])
        from_me = bool(key.get("fromMe", False))

        texto = (
            msg_obj.get("conversation")
            or (msg_obj.get("extendedTextMessage") or {}).get("text")
            or (msg_obj.get("buttonsResponseMessage") or {}).get("selectedDisplayText")
            or (msg_obj.get("listResponseMessage") or {}).get("title")
            or ""
        )
        return telefone, normalizar_texto(texto), from_me

    telefone = normalizar_telefone(
        dados.get("telefone") or dados.get("from") or dados.get("remoteJid") or ""
    )
    mensagem = normalizar_texto(
        dados.get("mensagem") or dados.get("body") or dados.get("text") or ""
    )
    from_me = bool(dados.get("fromMe", False))
    return telefone, mensagem, from_me


def enviar_whatsapp_api(telefone: str, texto: str) -> tuple:
    tel = normalizar_telefone(telefone)
    msg = normalizar_texto(texto)

    if not tel or not msg:
        return False, "Telefone ou mensagem vazios."

    if not WHATSAPP_API_URL or not WHATSAPP_API_KEY:
        return False, (
            "Defina WHATSAPP_API_KEY nas variáveis do Railway antes de enviar ou configurar o webhook."
        )

    url = f"{WHATSAPP_API_URL}/message/sendText/{INSTANCIA_NOME}"
    headers = {"Content-Type": "application/json", "apikey": WHATSAPP_API_KEY}
    payload = {"number": tel, "text": msg}

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=15)
        if 200 <= resp.status_code < 300:
            registrar_metrica(tel, "MENSAGEM_ENVIADA", msg[:80])
            return True, f"Mensagem enviada com sucesso para {tel}."
        return False, f"Evolution API retornou HTTP {resp.status_code}: {resp.text[:180]}"
    except Exception as exc:
        return False, f"Erro ao conectar na Evolution API: {exc}"


# ==========================================
# AUTENTICAÇÃO PERSISTENTE
# ==========================================

def chave_valida(req) -> bool:
    if session.get("chave_liberada") is True:
        session.permanent = True
        return True

    chave = (
        req.headers.get("X-Access-Key")
        or req.form.get("chave_acesso")
        or (req.get_json(silent=True) or {}).get("chave_acesso")
        or req.args.get("chave_acesso")
        or ""
    ).strip()

    if chave == CHAVE_ACESSO_PADRAO:
        session.permanent = True
        session["chave_liberada"] = True
        return True
    return False


def exigir_chave(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not chave_valida(request):
            return jsonify({"ok": False, "mensagem": "Chave de acesso inválida."}), 401
        return f(*args, **kwargs)

    return wrapper


# ==========================================
# FLUXO DE RESPOSTAS DA SOFIA
# ==========================================

def menu_sofia_texto() -> str:
    return (
        "Olá! Sou a *Sofia*, assistente acadêmica.\n\n"
        "1 - Provas e Avaliações\n"
        "2 - Calendário de Aulas\n"
        "3 - Secretaria Acadêmica\n"
        "5 - Falar com Atendente\n"
        "0 - Encerrar"
    )


def responder_bot(telefone: str, mensagem: str) -> str:
    conv = obter_conversa(telefone)
    texto = normalizar_texto(mensagem)
    baixo = texto.lower()

    if baixo in ("0", "encerrar", "#finalizar"):
        reativar_robo(telefone, "comando_encerrar")
        return MENSAGEM_ENCERRAMENTO

    if baixo in ("5", "atendente", "humano"):
        pausar_para_humano(telefone, "aluno_digitou_5")
        return "Encaminhando para atendimento humano. Aguarde um instante."

    if baixo in ("menu", "oi", "olá", "ola", "bom dia", "boa tarde", "boa noite"):
        conv["etapa"] = "menu_inicial"
        return menu_sofia_texto()

    opcao = converter_numero_seguro(texto)
    if opcao == 1:
        conv["etapa"] = "menu_inicial"
        return "As provas individuais são liberadas conforme o calendário de cada disciplina."
    if opcao == 2:
        conv["etapa"] = "menu_inicial"
        return "Confira os dias e horários das aulas no portal acadêmico."
    if opcao == 3:
        conv["etapa"] = "menu_inicial"
        return "A secretaria acadêmica atende pelos canais oficiais da instituição."

    return (
        "Desculpe, não encontrei essa opção. 🤔\n\n"
        "• Digite *menu* para ver as opções\n"
        "• Digite *0* para encerrar"
    )


# ==========================================
# ROTAS DO PAINEL E WEBHOOK
# ==========================================

@app.route("/")
def index():
    session.permanent = True
    session["chave_liberada"] = True

    contatos = obter_lista_contatos_combinada()
    bloqueados_qtd = len(carregar_bloqueados_excel()) or 7
    pausados = [c for c in conversas.values() if c["robo_pausado"]]

    html = """
    <!DOCTYPE html>
    <html lang="pt-br">
    <head>
      <meta charset="UTF-8">
      <meta name="viewport" content="width=device-width, initial-scale=1.0">
      <title>Painel Central — Sofia Oficial</title>
      <style>
        * { box-sizing: border-box; font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif; }
        body { margin: 0; background: #f4f6f8; color: #1f2937; padding: 24px; }
        .container { max-width: 1180px; margin: 0 auto; }
        .header-banner {
          background: #13795b; color: #fff; border-radius: 10px; padding: 20px 24px;
          display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 12px;
        }
        .header-banner h1 { margin: 0; font-size: 1.5rem; }
        .header-sub { margin-top: 6px; font-size: 0.9rem; opacity: 0.95; }
        .status-badge {
          background: rgba(255,255,255,0.18); padding: 6px 12px; border-radius: 999px; font-size: 0.85rem;
        }
        .key-box {
          background: #fffbeb; border: 1px solid #fde68a; border-radius: 8px;
          padding: 14px 18px; margin-top: 16px; display: flex; align-items: center; gap: 12px; flex-wrap: wrap;
        }
        .key-box input {
          padding: 8px 12px; border: 1px solid #d1d5db; border-radius: 6px; min-width: 240px;
        }
        .toolbar {
          display: grid; grid-template-columns: repeat(3, 1fr); gap: 14px; margin-top: 16px;
        }
        .btn {
          border: none; border-radius: 7px; padding: 12px 16px; color: #fff;
          font-weight: 600; cursor: pointer; font-size: 0.95rem; width: 100%;
        }
        .btn-orange { background: #e67e22; }
        .btn-green { background: #198754; }
        .btn-blue { background: #0d6efd; }
        .btn-teal { background: #0f766e; }
        .btn-sm { padding: 6px 10px; font-size: 0.82rem; width: auto; }
        .grid-2 {
          display: grid; grid-template-columns: 1fr 1fr; gap: 18px; margin-top: 20px; align-items: start;
        }
        .card {
          background: #fff; border-radius: 10px; padding: 20px; box-shadow: 0 1px 4px rgba(0,0,0,0.06);
        }
        .card h2 { margin: 0 0 14px 0; font-size: 1.15rem; }
        .field { margin-bottom: 12px; }
        .field label { display: block; font-size: 0.85rem; font-weight: 600; margin-bottom: 5px; }
        .field input, .field select, .field textarea {
          width: 100%; padding: 9px 11px; border: 1px solid #d1d5db; border-radius: 6px; font-size: 0.92rem;
        }
        .pause-row { display: flex; gap: 8px; margin-bottom: 14px; }
        .pause-row input { flex: 1; padding: 9px 11px; border: 1px solid #d1d5db; border-radius: 6px; }
        .muted { color: #6b7280; font-size: 0.88rem; }
        .item-pausado {
          display: flex; justify-content: space-between; align-items: center;
          padding: 10px 12px; border: 1px solid #e5e7eb; border-radius: 7px; margin-bottom: 8px;
        }
        .feedback { margin-top: 12px; font-size: 0.92rem; font-weight: 600; color: #0f766e; }
        @media (max-width: 850px) {
          .toolbar, .grid-2 { grid-template-columns: 1fr; }
        }
      </style>
    </head>
    <body>
      <div class="container">
        <div class="header-banner">
          <div>
            <h1>🤖 Painel Central — Sofia Oficial</h1>
            <div class="header-sub">
              Instância: <strong>{{ instancia }}</strong> | Bloqueados na lista: <strong>{{ bloqueados }}</strong>
            </div>
          </div>
          <div class="status-badge">Status WhatsApp: <strong>{{ status_whatsapp }}</strong></div>
        </div>

        <div class="key-box">
          <span>🔑 <strong>Chave de Acesso:</strong></span>
          <input id="chaveAcesso" type="text" value="{{ chave_padrao }}" placeholder="Digite cetrus2026teste para liberar">
          <span class="muted">Salva automaticamente por 30 dias (sem precisar redigitar).</span>
        </div>

        <div class="toolbar">
          <button class="btn btn-orange" onclick="acaoRapida('/api/webhook/corrigir')">🔌 1. Ativar/Corrigir Webhook Oficial</button>
          <button class="btn btn-green" onclick="baixarMetricas()">📥 2. Baixar Planilha de Métricas (.xlsx)</button>
          <button class="btn btn-blue" onclick="atualizarContatos()">🔄 3. Atualizar Contatos e Fila</button>
        </div>

        <div id="feedback" class="feedback"></div>

        <div class="grid-2">
          <div class="card">
            <h2>📲 Iniciar Contato com Aluno</h2>
            <div class="field">
              <label>Buscar nos Contatos Salvos ({{ contatos|length }}):</label>
              <input id="buscaContato" type="text" placeholder="🔍 Digite nome ou número..." oninput="filtrarContatos()">
            </div>
            <div class="field">
              <select id="selectContato" onchange="selecionarContato()">
                <option value="">-- Selecione um contato do WhatsApp --</option>
                {% for c in contatos %}
                <option value="{{ c.telefone }}">{{ c.nome }} ({{ c.telefone }})</option>
                {% endfor %}
              </select>
            </div>
            <div class="field">
              <label>OU Digite um Número Novo (com DDD):</label>
              <input id="numeroNovo" type="text" placeholder="Ex: 11988887777">
            </div>
            <div class="field">
              <label>Tipo de Início:</label>
              <select id="tipoInicio">
                <option value="menu_sofia">Enviar Menu Automático da Sofia</option>
                <option value="mensagem_manual">Enviar Mensagem Manual</option>
                <option value="encerrar">Enviar Mensagem de Encerramento (Reativa Robô)</option>
              </select>
            </div>
            <div class="field">
              <label>Mensagem Manual (opcional se escolheu opção 2):</label>
              <textarea id="mensagemManual" rows="3" placeholder="Olá! Aqui é da secretaria acadêmica..."></textarea>
            </div>
            <button class="btn btn-teal" onclick="enviarContato()">🚀 Enviar Mensagem Agora</button>
          </div>

          <div class="card">
            <h2>🧑‍💻 Atendimentos Humanos (Robô Pausado)</h2>
            <p class="muted">
              Quando um aluno digita <strong>5</strong> ou o atendente envia mensagem, o número aparece aqui e o robô fica em silêncio até clicar em <strong>Reativar</strong>, enviar <strong>#finalizar</strong> ou enviar a mensagem de encerramento no WhatsApp.
            </p>
            <div class="pause-row">
              <input id="numeroPausar" type="text" placeholder="Pausar número (DDD + Num)">
              <button class="btn btn-orange btn-sm" onclick="pausarNumero()">⏸ Pausar</button>
            </div>

            {% if pausados %}
              {% for p in pausados %}
              <div class="item-pausado">
                <div>
                  <strong>{{ p.telefone }}</strong><br>
                  <span class="muted">Atualizado: {{ p.atualizado_em }}</span>
                </div>
                <button class="btn btn-green btn-sm" onclick="reativarNumero('{{ p.telefone }}')">▶️ Devolver ao Robô</button>
              </div>
              {% endfor %}
            {% else %}
              <p class="muted" style="text-align:center; padding: 18px 0;">Nenhum atendimento humano ativo no momento.</p>
            {% endif %}
          </div>
        </div>
      </div>

      <script>
        const CHAVE_FIXA = "{{ chave_padrao }}";
        const inputChave = document.getElementById("chaveAcesso");
        const salvaLocal = localStorage.getItem("sofia_chave_acesso");

        if (salvaLocal && salvaLocal !== "CHAVE_ACESSO" && salvaLocal.trim() !== "") {
          inputChave.value = salvaLocal;
        } else {
          inputChave.value = CHAVE_FIXA;
          localStorage.setItem("sofia_chave_acesso", CHAVE_FIXA);
        }

        inputChave.addEventListener("change", () => {
          const val = inputChave.value.trim() || CHAVE_FIXA;
          inputChave.value = val;
          localStorage.setItem("sofia_chave_acesso", val);
        });

        function headersAuth() {
          return {
            "Content-Type": "application/json",
            "X-Access-Key": (inputChave.value.trim() || CHAVE_FIXA)
          };
        }

        function setFeedback(msg, isErro = false) {
          const el = document.getElementById("feedback");
          el.style.color = isErro ? "#dc2626" : "#0f766e";
          el.textContent = msg;
        }

        function filtrarContatos() {
          const termo = document.getElementById("buscaContato").value.toLowerCase();
          const select = document.getElementById("selectContato");
          for (const opt of select.options) {
            if (!opt.value) continue;
            opt.hidden = !opt.text.toLowerCase().includes(termo);
          }
        }

        function selecionarContato() {
          const val = document.getElementById("selectContato").value;
          if (val) document.getElementById("numeroNovo").value = val;
        }

        async function acaoRapida(url) {
          const res = await fetch(url, { method: "POST", headers: headersAuth() });
          const data = await res.json();
          setFeedback(data.mensagem || "Ação executada.", !data.ok);
        }

        async function baixarMetricas() {
          const chave = encodeURIComponent(inputChave.value.trim() || CHAVE_FIXA);
          window.location.href = `/api/metricas/xlsx?chave_acesso=${chave}`;
          setFeedback("Download da planilha de métricas iniciado.");
        }

        async function atualizarContatos() {
          const res = await fetch("/api/contatos/atualizar", { method: "POST", headers: headersAuth() });
          const data = await res.json();
          setFeedback(data.mensagem || "Contatos atualizados.", !data.ok);
          if (data.ok) setTimeout(() => location.reload(), 700);
        }

        async function enviarContato() {
          const telefone = document.getElementById("numeroNovo").value.trim();
          const tipo = document.getElementById("tipoInicio").value;
          const mensagem = document.getElementById("mensagemManual").value;
          const res = await fetch("/api/iniciar-contato", {
            method: "POST",
            headers: headersAuth(),
            body: JSON.stringify({ telefone, tipo, mensagem })
          });
          const data = await res.json();
          setFeedback(data.mensagem || "Enviado.", !data.ok);
          if (data.ok) {
            setTimeout(() => location.reload(), 900);
          }
        }

        async function pausarNumero() {
          const telefone = document.getElementById("numeroPausar").value.trim();
          const res = await fetch("/api/pausar", {
            method: "POST",
            headers: headersAuth(),
            body: JSON.stringify({ telefone })
          });
          const data = await res.json();
          setFeedback(data.mensagem || "Pausado.", !data.ok);
          if (data.ok) setTimeout(() => location.reload(), 500);
        }

        async function reativarNumero(telefone) {
          const res = await fetch("/api/reativar", {
            method: "POST",
            headers: headersAuth(),
            body: JSON.stringify({ telefone })
          });
          const data = await res.json();
          setFeedback(data.mensagem || "Reativado.", !data.ok);
          if (data.ok) setTimeout(() => location.reload(), 500);
        }
      </script>
    </body>
    </html>
    """
    return render_template_string(
        html,
        instancia=INSTANCIA_NOME,
        bloqueados=bloqueados_qtd,
        status_whatsapp=STATUS_WHATSAPP,
        chave_padrao=CHAVE_ACESSO_PADRAO,
        contatos=contatos,
        pausados=pausados,
    )


@app.route("/api/webhook/corrigir", methods=["POST"])
@exigir_chave
def corrigir_webhook():
    if not WHATSAPP_API_URL or not WHATSAPP_API_KEY:
        return jsonify({
            "ok": False,
            "mensagem": "Defina WHATSAPP_API_KEY nas variáveis do Railway antes de configurar o webhook."
        }), 400

    webhook_url = request.url_root.rstrip("/") + "/webhook"
    url = f"{WHATSAPP_API_URL}/webhook/set/{INSTANCIA_NOME}"
    headers = {"Content-Type": "application/json", "apikey": WHATSAPP_API_KEY}
    payload = {
        "webhook": {
            "enabled": True,
            "url": webhook_url,
            "webhookByEvents": False,
            "events": ["MESSAGES_UPSERT", "SEND_MESSAGE"],
        }
    }
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=15)
        if 200 <= resp.status_code < 300:
            registrar_metrica("-", "WEBHOOK_ATIVADO", webhook_url)
            return jsonify({"ok": True, "mensagem": f"Webhook oficial ativado em {webhook_url}."})
        return jsonify({
            "ok": False,
            "mensagem": f"Evolution API retornou HTTP {resp.status_code}: {resp.text[:180]}"
        }), 500
    except Exception as exc:
        return jsonify({"ok": False, "mensagem": f"Falha de conexão com Evolution API: {exc}"}), 500


@app.route("/api/metricas/xlsx", methods=["GET", "POST"])
@exigir_chave
def baixar_metricas():
    arquivo = gerar_arquivo_metricas_xlsx()
    return send_file(
        arquivo,
        as_attachment=True,
        download_name="metricas_sofia_oficial.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@app.route("/api/contatos/atualizar", methods=["POST"])
@exigir_chave
def atualizar_contatos():
    contatos = obter_lista_contatos_combinada()
    return jsonify({"ok": True, "mensagem": f"Contatos e fila atualizados ({len(contatos)} contatos)."})


@app.route("/api/iniciar-contato", methods=["POST"])
@exigir_chave
def iniciar_contato():
    dados = request.get_json(silent=True) or {}
    telefone = normalizar_telefone(dados.get("telefone"))
    tipo = dados.get("tipo", "menu_sofia")
    mensagem_manual = normalizar_texto(dados.get("mensagem", ""))

    if not telefone:
        return jsonify({"ok": False, "mensagem": "Informe um número com DDD."}), 400

    bloqueados = carregar_bloqueados_excel()
    if telefone in bloqueados:
        registrar_metrica(telefone, "BLOQUEADO_LISTA", "Tentativa de envio a número bloqueado")
        return jsonify({"ok": False, "mensagem": f"Número {telefone} está na lista de bloqueados."}), 400

    if tipo == "encerrar":
        ok, detalhe = enviar_whatsapp_api(telefone, MENSAGEM_ENCERRAMENTO)
        reativar_robo(telefone, "envio_encerramento_painel")
        if not ok:
            return jsonify({"ok": False, "mensagem": detalhe}), 500
        return jsonify({"ok": True, "mensagem": f"Encerramento enviado para {telefone}. Robô reativado automaticamente."})

    if tipo == "mensagem_manual":
        if not mensagem_manual:
            return jsonify({"ok": False, "mensagem": "Digite a mensagem manual antes de enviar."}), 400
        ok, detalhe = enviar_whatsapp_api(telefone, mensagem_manual)
        if not ok:
            return jsonify({"ok": False, "mensagem": detalhe}), 500
        if eh_mensagem_encerramento(mensagem_manual):
            reativar_robo(telefone, "mensagem_manual_encerramento")
            return jsonify({"ok": True, "mensagem": f"Mensagem de encerramento enviada para {telefone}. Robô reativado."})
        pausar_para_humano(telefone, "mensagem_manual_painel")
        return jsonify({"ok": True, "mensagem": f"Mensagem enviada para {telefone}. Robô pausado para atendimento humano."})

    ok, detalhe = enviar_whatsapp_api(telefone, menu_sofia_texto())
    reativar_robo(telefone, "menu_sofia_painel")
    if not ok:
        return jsonify({"ok": False, "mensagem": detalhe}), 500
    return jsonify({"ok": True, "mensagem": f"Menu da Sofia enviado para {telefone}."})


@app.route("/api/pausar", methods=["POST"])
@exigir_chave
def api_pausar():
    dados = request.get_json(silent=True) or {}
    telefone = normalizar_telefone(dados.get("telefone"))
    if not telefone:
        return jsonify({"ok": False, "mensagem": "Informe um número para pausar."}), 400
    pausar_para_humano(telefone, "pausa_manual_painel")
    return jsonify({"ok": True, "mensagem": f"Robô pausado para {telefone}."})


@app.route("/api/reativar", methods=["POST"])
@exigir_chave
def api_reativar():
    dados = request.get_json(silent=True) or {}
    telefone = normalizar_telefone(dados.get("telefone"))
    if not telefone:
        return jsonify({"ok": False, "mensagem": "Informe um número para reativar."}), 400
    reativar_robo(telefone, "reativacao_manual_painel")
    return jsonify({"ok": True, "mensagem": f"Robô reativado para {telefone}."})


@app.route("/webhook", methods=["POST"])
def webhook_whatsapp():
    dados = request.get_json(silent=True) or {}
    telefone, mensagem, from_me = extrair_dados_evolution(dados)

    if not telefone or not mensagem:
        return jsonify({"status": "ignorado"}), 200

    bloqueados = carregar_bloqueados_excel()
    if telefone in bloqueados:
        registrar_metrica(telefone, "BLOQUEADO_WEBHOOK", mensagem[:60])
        return jsonify({"status": "bloqueado"}), 200

    if from_me:
        if eh_mensagem_encerramento(mensagem):
            reativar_robo(telefone, "fromMe_encerramento")
            return jsonify({"status": "reativado_por_encerramento", "telefone": telefone}), 200
        if not dados.get("enviado_pelo_bot", False):
            pausar_para_humano(telefone, "fromMe_atendente")
            return jsonify({"status": "pausado_por_humano", "telefone": telefone}), 200
        return jsonify({"status": "mensagem_bot"}), 200

    conv = obter_conversa(telefone)
    if mensagem.lower() in ("#finalizar", "0", "encerrar"):
        resposta = responder_bot(telefone, mensagem)
        enviar_whatsapp_api(telefone, resposta)
        return jsonify({"status": "encerrado", "telefone": telefone, "resposta": resposta}), 200

    if conv["robo_pausado"]:
        return jsonify({"status": "robô_pausado", "telefone": telefone}), 200

    try:
        resposta = responder_bot(telefone, mensagem)
        enviar_whatsapp_api(telefone, resposta)
        return jsonify({"status": "respondido", "telefone": telefone, "resposta": resposta}), 200
    except Exception as exc:
        print(f"[ERRO BOT] {telefone}: {exc}")
        fallback = (
            "Desculpe, não encontrei essa opção. 🤔\n\n"
            "• Digite *menu* para ver as opções\n"
            "• Digite *0* para encerrar"
        )
        enviar_whatsapp_api(telefone, fallback)
        return jsonify({"status": "erro_tratado", "telefone": telefone, "resposta": fallback}), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True)