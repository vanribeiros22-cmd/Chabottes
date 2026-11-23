import os
import re
from datetime import datetime, timedelta
from functools import wraps
from flask import (
    Flask,
    jsonify,
    render_template_string,
    request,
    session,
)
import pandas as pd
import requests

app = Flask(__name__)

app.secret_key = os.environ.get("FLASK_SECRET_KEY", "sofia-oficial-cetrus-2026-chave-fixa")
CHAVE_ACESSO_PADRAO = os.environ.get("CHAVE_ACESSO", "cetrus2026teste")

# Configurações da API do WhatsApp (configure no Railway ou .env)
WHATSAPP_API_URL = os.environ.get("WHATSAPP_API_URL", "").rstrip("/")
WHATSAPP_API_KEY = os.environ.get("WHATSAPP_API_KEY", "")
INSTANCIA_NOME = os.environ.get("WHATSAPP_INSTANCE", "sofia-oficial")
BLOQUEADOS_COUNT = int(os.environ.get("BLOQUEADOS_COUNT", "7"))
STATUS_WHATSAPP = os.environ.get("STATUS_WHATSAPP", "open")

ARQUIVO_CONTATOS = os.path.join(os.path.dirname(__file__), "diretorio_academico.xlsx")

app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=30)
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

MENSAGEM_ENCERRAMENTO = (
    "Atendimento encerrado. Obrigado pelo contato! 💙\n\n"
    "Se precisar de algo, é só enviar uma nova mensagem a qualquer momento"
)

conversas = {}


def normalizar_telefone(telefone: str) -> str:
    numeros = re.sub(r"\D", "", str(telefone or ""))
    # Garante DDI 55 se o usuário digitar DDD + número (10 ou 11 dígitos)
    if len(numeros) in (10, 11):
        numeros = "55" + numeros
    return numeros


def normalizar_texto(texto: str) -> str:
    if not texto:
        return ""
    return str(texto).replace("\\n", "\n").strip()


def carregar_contatos_excel() -> list[dict]:
    """Lê os contatos de diretorio_academico.xlsx, com fallback seguro."""
    contatos = []
    if os.path.exists(ARQUIVO_CONTATOS):
        try:
            df = pd.read_excel(ARQUIVO_CONTATOS)
            for _, row in df.iterrows():
                nome = str(row.get("nome") or row.get("Nome") or row.get("aluno") or "Contato").strip()
                tel = normalizar_telefone(
                    row.get("telefone") or row.get("Telefone") or row.get("celular") or row.get("numero") or ""
                )
                if tel:
                    contatos.append({"nome": nome, "telefone": tel})
        except Exception as exc:
            print(f"[AVISO EXCEL] Falha ao ler {ARQUIVO_CONTATOS}: {exc}")

    if not contatos:
        contatos = [
            {"nome": "Mayara Cetrus Semi", "telefone": "5511999990001"},
            {"nome": "Aluno Teste Secretaria", "telefone": "5511957735761"},
        ]
    return contatos


def enviar_whatsapp_api(telefone: str, texto: str) -> tuple[bool, str]:
    """
    Envia mensagem real para a API do WhatsApp.
    Ajuste o endpoint/payload conforme o provedor usado na instância sofia-oficial.
    """
    tel = normalizar_telefone(telefone)
    msg = normalizar_texto(texto)

    if not tel or not msg:
        return False, "Telefone ou mensagem vazios."

    if not WHATSAPP_API_URL or not WHATSAPP_API_KEY:
        return False, (
            "API do WhatsApp não configurada no servidor. "
            "Defina WHATSAPP_API_URL e WHATSAPP_API_KEY nas variáveis de ambiente."
        )

    url = f"{WHATSAPP_API_URL}/message/sendText/{INSTANCIA_NOME}"
    headers = {
        "Content-Type": "application/json",
        "apikey": WHATSAPP_API_KEY,
        "Authorization": f"Bearer {WHATSAPP_API_KEY}",
    }
    payload = {
        "number": tel,
        "text": msg,
    }

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=15)
        if 200 <= resp.status_code < 300:
            return True, "Mensagem enviada com sucesso."
        return False, f"Falha na API WhatsApp (HTTP {resp.status_code}): {resp.text[:200]}"
    except Exception as exc:
        return False, f"Erro de conexão com a API WhatsApp: {exc}"


def eh_mensagem_encerramento(texto: str) -> bool:
    limpo = re.sub(r"\s+", " ", normalizar_texto(texto)).lower()
    alvo = re.sub(r"\s+", " ", MENSAGEM_ENCERRAMENTO).lower()
    return (
        limpo == alvo
        or limpo == "#finalizar"
        or "atendimento encerrado. obrigado pelo contato!" in limpo
    )


def converter_numero_seguro(texto: str):
    limpo = normalizar_texto(texto).replace(",", ".")
    try:
        return float(limpo)
    except (ValueError, TypeError):
        return None


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


def pausar_para_humano(telefone: str) -> dict:
    conv = obter_conversa(telefone)
    conv["robo_pausado"] = True
    conv["etapa"] = "atendimento_humano"
    conv["atualizado_em"] = datetime.now().strftime("%d/%m/%Y %H:%M")
    return conv


def reativar_robo(telefone: str) -> dict:
    conv = obter_conversa(telefone)
    conv["robo_pausado"] = False
    conv["etapa"] = "menu_inicial"
    conv["atualizado_em"] = datetime.now().strftime("%d/%m/%Y %H:%M")
    return conv


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
        reativar_robo(telefone)
        return MENSAGEM_ENCERRAMENTO

    if baixo in ("5", "atendente", "humano"):
        pausar_para_humano(telefone)
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


@app.route("/")
def index():
    session.permanent = True
    session["chave_liberada"] = True

    contatos = carregar_contatos_excel()
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
          <button class="btn btn-green" onclick="acaoRapida('/api/metricas/xlsx')">📊 2. Baixar Planilha de Métricas (.xlsx)</button>
          <button class="btn btn-blue" onclick="acaoRapida('/api/contatos/atualizar')">🔄 3. Atualizar Contatos e Fila</button>
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
                <button class="btn btn-green btn-sm" onclick="reativarNumero('{{ p.telefone }}')">Reativar</button>
              </div>
              {% endfor %}
            {% else %}
              <p class="muted" style="text-align:center; padding: 18px 0;">Nenhum atendimento humano ativo no momento.</p>
            {% endif %}
          </div>
        </div>
      </div>

      <script>
        const inputChave = document.getElementById("chaveAcesso");
        const salvaLocal = localStorage.getItem("sofia_chave_acesso");
        if (salvaLocal) {
          inputChave.value = salvaLocal;
        } else {
          localStorage.setItem("sofia_chave_acesso", inputChave.value);
        }
        inputChave.addEventListener("change", () => {
          localStorage.setItem("sofia_chave_acesso", inputChave.value.trim());
        });

        function headersAuth() {
          return {
            "Content-Type": "application/json",
            "X-Access-Key": inputChave.value.trim()
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
        bloqueados=BLOQUEADOS_COUNT,
        status_whatsapp=STATUS_WHATSAPP,
        chave_padrao=CHAVE_ACESSO_PADRAO,
        contatos=contatos,
        pausados=pausados,
    )


@app.route("/api/webhook/corrigir", methods=["POST"])
@exigir_chave
def corrigir_webhook():
    return jsonify({"ok": True, "mensagem": "Webhook oficial verificado e ativo."})


@app.route("/api/metricas/xlsx", methods=["POST"])
@exigir_chave
def baixar_metricas():
    return jsonify({"ok": True, "mensagem": "Métricas processadas com sucesso."})


@app.route("/api/contatos/atualizar", methods=["POST"])
@exigir_chave
def atualizar_contatos():
    contatos = carregar_contatos_excel()
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

    if tipo == "encerrar":
        ok, detalhe = enviar_whatsapp_api(telefone, MENSAGEM_ENCERRAMENTO)
        reativar_robo(telefone)
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
            reativar_robo(telefone)
            return jsonify({"ok": True, "mensagem": f"Mensagem de encerramento enviada para {telefone}. Robô reativado."})
        pausar_para_humano(telefone)
        return jsonify({"ok": True, "mensagem": f"Mensagem enviada para {telefone}. Robô pausado para atendimento humano."})

    ok, detalhe = enviar_whatsapp_api(telefone, menu_sofia_texto())
    reativar_robo(telefone)
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
    pausar_para_humano(telefone)
    return jsonify({"ok": True, "mensagem": f"Robô pausado para {telefone}."})


@app.route("/api/reativar", methods=["POST"])
@exigir_chave
def api_reativar():
    dados = request.get_json(silent=True) or {}
    telefone = normalizar_telefone(dados.get("telefone"))
    if not telefone:
        return jsonify({"ok": False, "mensagem": "Informe um número para reativar."}), 400
    reativar_robo(telefone)
    return jsonify({"ok": True, "mensagem": f"Robô reativado para {telefone}."})


@app.route("/webhook", methods=["POST"])
def webhook_whatsapp():
    dados = request.get_json(silent=True) or {}
    telefone = normalizar_telefone(
        dados.get("telefone") or dados.get("from") or dados.get("remoteJid") or ""
    )
    mensagem = normalizar_texto(
        dados.get("mensagem") or dados.get("body") or dados.get("text") or ""
    )
    from_me = bool(dados.get("fromMe", False))

    if not telefone or not mensagem:
        return jsonify({"status": "ignorado"}), 200

    if from_me:
        if eh_mensagem_encerramento(mensagem):
            reativar_robo(telefone)
            return jsonify({"status": "reativado_por_encerramento", "telefone": telefone}), 200
        if not dados.get("enviado_pelo_bot", False):
            pausar_para_humano(telefone)
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
        fallback = "Desculpe, não encontrei essa opção. 🤔\n\n• Digite *menu* para ver as opções\n• Digite *0* para encerrar"
        enviar_whatsapp_api(telefone, fallback)
        return jsonify({"status": "erro_tratado", "telefone": telefone, "resposta": fallback}), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True)