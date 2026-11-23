import os
import re
from datetime import datetime, timedelta
from functools import wraps
from flask import Flask, request, jsonify, render_template_string, redirect, url_for, session

app = Flask(__name__)

# Chave secreta da sessão e senha padrão configurada
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "chave-secreta-cetrus-2026-persistente")
SENHA_ACESSO = os.environ.get("CHAVE_ACESSO", "cetrus2026teste")

# Mantém o login ativo por 30 dias para não pedir senha toda hora
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=30)
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

# Mensagem oficial de encerramento que reativa o robô automaticamente
MENSAGEM_ENCERRAMENTO = (
    "Atendimento encerrado. Obrigado pelo contato! 💙\n\n"
    "Se precisar de algo, é só enviar uma nova mensagem a qualquer momento"
)

# Armazenamento em memória das conversas (pode ser substituído por SQLAlchemy/SQLite)
conversas = {}


def obter_estado_conversa(telefone: str) -> dict:
    if telefone not in conversas:
        conversas[telefone] = {
            "telefone": telefone,
            "bot_ativo": True,
            "etapa": "menu_inicial",
            "atualizado_em": datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
        }
    return conversas[telefone]


def reativar_robo_e_resetar(telefone: str) -> dict:
    """Reativa o robô para o contato e reseta a etapa para o início."""
    estado = obter_estado_conversa(telefone)
    estado["bot_ativo"] = True
    estado["etapa"] = "menu_inicial"
    estado["atualizado_em"] = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    return estado


def pausar_robo(telefone: str) -> dict:
    """Pausa o robô quando há intervenção manual de um atendente."""
    estado = obter_estado_conversa(telefone)
    estado["bot_ativo"] = False
    estado["atualizado_em"] = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    return estado


def normalizar_texto(texto: str) -> str:
    if not texto:
        return ""
    # Corrige quebras de linha escapadas (\n) e remove espaços duplicados nas pontas
    return texto.replace("\\n", "\n").strip()


def eh_mensagem_encerramento(texto: str) -> bool:
    """Verifica se o texto enviado corresponde à mensagem oficial de encerramento."""
    texto_limpo = re.sub(r"\s+", " ", normalizar_texto(texto)).lower()
    alvo_limpo = re.sub(r"\s+", " ", MENSAGEM_ENCERRAMENTO).lower()
    return texto_limpo == alvo_limpo or "atendimento encerrado. obrigado pelo contato!" in texto_limpo


def converter_numero_seguro(texto: str):
    """
    Evita o erro 'could not convert string to float'.
    Retorna float/int apenas se a mensagem for estritamente numérica.
    """
    limpo = normalizar_texto(texto).replace(",", ".")
    try:
        return float(limpo)
    except (ValueError, TypeError):
        return None


# ==========================================
# AUTENTICAÇÃO PERSISTENTE (NÃO PEDE TODA HORA)
# ==========================================

def login_obrigatorio(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        # Permite acesso direto se já estiver logado na sessão OU se enviar header/token
        token_header = request.headers.get("X-Access-Key") or request.args.get("chave")
        if session.get("autenticado") or token_header == SENHA_ACESSO:
            session["autenticado"] = True
            session.permanent = True
            return f(*args, **kwargs)
        return redirect(url_for("login", next=request.path))
    return decorated


@app.route("/login", methods=["GET", "POST"])
def login():
    if session.get("autenticado"):
        return redirect(url_for("painel"))

    erro = None
    if request.method == "POST":
        senha_digitada = request.form.get("senha", "").strip()
        if senha_digitada == SENHA_ACESSO:
            session.permanent = True
            session["autenticado"] = True
            destino = request.args.get("next") or url_for("painel")
            return redirect(destino)
        erro = "Chave de acesso incorreta."

    html_login = """
    <!DOCTYPE html>
    <html lang="pt-br">
    <head>
        <meta charset="UTF-8">
        <title>Acesso - Chatbot Acadêmico</title>
        <style>
            body { font-family: sans-serif; background: #f4f6f9; display: flex; justify-content: center; align-items: center; height: 100vh; }
            .card { background: white; padding: 2rem; border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); width: 320px; }
            input, button { width: 100%; padding: 10px; margin-top: 10px; box-sizing: border-box; }
            button { background: #0d6efd; color: white; border: none; border-radius: 4px; cursor: pointer; }
            .erro { color: #dc3545; font-size: 0.9rem; margin-top: 8px; }
        </style>
    </head>
    <body>
        <div class="card">
            <h3>Painel do Chatbot</h3>
            <p style="font-size: 0.85rem; color: #555;">O login ficará salvo por 30 dias neste navegador.</p>
            <form method="POST">
                <input type="password" name="senha" placeholder="Digite a chave de acesso" required autofocus>
                <button type="submit">Entrar e Manter Conectado</button>
            </form>
            {% if erro %}<div class="erro">{{ erro }}</div>{% endif %}
        </div>
    </body>
    </html>
    """
    return render_template_string(html_login, erro=erro)


# ==========================================
# LÓGICA DO CHATBOT E WEBHOOK WHATSAPP
# ==========================================

def gerar_resposta_bot(telefone: str, mensagem_recebida: str) -> str:
    estado = obter_estado_conversa(telefone)
    texto = normalizar_texto(mensagem_recebida)
    texto_lower = texto.lower()

    # Comando de encerramento (opção 0 ou palavra sair/encerrar)
    if texto_lower in ("0", "encerrar", "sair"):
        reativar_robo_e_resetar(telefone)
        return MENSAGEM_ENCERRAMENTO

    # Comando de menu
    if texto_lower in ("menu", "oi", "olá", "ola", "bom dia", "boa tarde", "boa noite"):
        estado["etapa"] = "aguardando_opcao"
        return (
            "Olá! Bem-vindo ao atendimento acadêmico. 👋\n\n"
            "Escolha uma opção:\n"
            "1 - Provas e Avaliações\n"
            "2 - Calendário de Aulas\n"
            "3 - Secretaria / Falar com Atendente\n"
            "0 - Encerrar atendimento"
        )

    # Leitura segura de opção numérica (nunca quebra com float/int em texto livre)
    numero_opcao = converter_numero_seguro(texto)

    if numero_opcao == 1:
        estado["etapa"] = "aguardando_opcao"
        return (
            "📚 *Provas e Avaliações*\n"
            "As avaliações individuais são liberadas conforme o cronograma de cada disciplina no portal do aluno.\n\n"
            "• Digite *menu* para voltar às opções\n"
            "• Digite *0* para encerrar"
        )
    elif numero_opcao == 2:
        estado["etapa"] = "aguardando_opcao"
        return (
            "📅 *Calendário de Aulas*\n"
            "Consulte o cronograma atualizado da sua turma diretamente no ambiente virtual.\n\n"
            "• Digite *menu* para voltar às opções\n"
            "• Digite *0* para encerrar"
        )
    elif numero_opcao == 3:
        pausar_robo(telefone)
        return (
            "👩‍💻 *Atendimento Humano*\n"
            "Transferimos sua conversa para nossa equipe. Aguarde um instante que já vamos te responder!\n\n"
            "• Digite *0* caso queira encerrar"
        )

    # Resposta padrão amigável para qualquer texto que não seja opção válida
    return (
        "Desculpe, não encontrei essa opção. 🤔\n\n"
        "• Digite *menu* para ver as opções\n"
        "• Digite *0* para encerrar"
    )


@app.route("/webhook", methods=["POST"])
def webhook():
    """
    Recebe eventos do WhatsApp (mensagens recebidas do aluno e mensagens enviadas manualmente).
    Adapte as chaves do JSON conforme sua API (Evolution API, Z-API, WPPConnect, Meta, Twilio).
    """
    dados = request.get_json(silent=True) or {}

    telefone = str(dados.get("telefone") or dados.get("from") or dados.get("remoteJid") or "").strip()
    mensagem = str(dados.get("mensagem") or dados.get("body") or dados.get("text") or "").strip()
    from_me = bool(dados.get("fromMe", False))

    if not telefone or not mensagem:
        return jsonify({"status": "ignorado", "motivo": "telefone ou mensagem vazios"}), 200

    # 1. MENSAGEM ENVIADA PELO ATENDENTE / SISTEMA (fromMe == True)
    if from_me:
        # Se enviou a mensagem oficial de encerramento, reativa o robô automaticamente!
        if eh_mensagem_encerramento(mensagem):
            estado = reativar_robo_e_resetar(telefone)
            return jsonify({
                "status": "robo_reativado_automaticamente",
                "telefone": telefone,
                "bot_ativo": estado["bot_ativo"]
            }), 200

        # Se for uma mensagem manual comum do atendente (e não resposta automática do bot), pausa o robô
        if not dados.get("enviado_pelo_bot", False):
            estado = pausar_robo(telefone)
            return jsonify({
                "status": "robo_pausado_atendimento_humano",
                "telefone": telefone,
                "bot_ativo": estado["bot_ativo"]
            }), 200

        return jsonify({"status": "mensagem_bot_registrada"}), 200

    # 2. MENSAGEM RECEBIDA DO ALUNO (fromMe == False)
    estado = obter_estado_conversa(telefone)

    # Permite que o aluno encerre e volte ao bot mesmo se estava pausado, caso digite 0
    if normalize_cmd := normalizar_texto(mensagem).lower() in ("0", "encerrar"):
        resposta = gerar_resposta_bot(telefone, mensagem)
        return jsonify({"status": "encerrado", "telefone": telefone, "resposta": resposta}), 200

    # Se o robô estiver pausado em atendimento humano, não interfere
    if not estado["bot_ativo"]:
        return jsonify({
            "status": "ignorado_robo_pausado",
            "telefone": telefone
        }), 200

    # Processa com o robô de forma segura (sem erro de float)
    try:
        resposta = gerar_resposta_bot(telefone, mensagem)
        return jsonify({
            "status": "respondido",
            "telefone": telefone,
            "resposta": resposta
        }), 200
    except Exception as e:
        # Fallback de segurança: nunca envia stacktrace técnico para o WhatsApp do aluno
        print(f"[ERRO INTERNO BOT] {telefone}: {e}")
        return jsonify({
            "status": "erro_tratado",
            "telefone": telefone,
            "resposta": "Desculpe, não encontrei essa opção. 🤔\n\n• Digite *menu* para ver as opções\n• Digite *0* para encerrar"
        }), 200


# ==========================================
# PAINEL DE CONTROLE MANUAL (CASO PRECISE)
# ==========================================

@app.route("/")
@login_obrigatorio
def painel():
    html_painel = """
    <!DOCTYPE html>
    <html lang="pt-br">
    <head>
        <meta charset="UTF-8">
        <title>Painel Chatbot Acadêmico</title>
        <style>
            body { font-family: sans-serif; padding: 20px; background: #f4f6f9; }
            table { width: 100%; border-collapse: collapse; background: white; }
            th, td { padding: 12px; border: 1px solid #ddd; text-align: left; }
            th { background: #0d6efd; color: white; }
            .ativo { color: green; font-weight: bold; }
            .pausado { color: red; font-weight: bold; }
            button { padding: 6px 12px; cursor: pointer; }
        </style>
    </head>
    <body>
        <h2>Controle de Atendimentos - Chatbot</h2>
        <p><strong>Regra ativa:</strong> Ao enviar <em>"Atendimento encerrado. Obrigado pelo contato! 💙..."</em>, o contato volta automaticamente para o robô.</p>
        <table>
            <tr>
                <th>Telefone</th>
                <th>Status do Robô</th>
                <th>Etapa</th>
                <th>Última Atualização</th>
                <th>Ação</th>
            </tr>
            {% for tel, info in conversas.items() %}
            <tr>
                <td>{{ tel }}</td>
                <td class="{{ 'ativo' if info.bot_ativo else 'pausado' }}">
                    {{ 'ATIVO' if info.bot_ativo else 'PAUSADO (Humano)' }}
                </td>
                <td>{{ info.etapa }}</td>
                <td>{{ info.atualizado_em }}</td>
                <td>
                    <form method="POST" action="{{ url_for('reativar_manual', telefone=tel) }}">
                        <button type="submit">Reativar Robô Agora</button>
                    </form>
                </td>
            </tr>
            {% else %}
            <tr><td colspan="5">Nenhuma conversa registrada ainda.</td></tr>
            {% endfor %}
        </table>
    </body>
    </html>
    """
    return render_template_string(html_painel, conversas=conversas)


@app.route("/reativar/<telefone>", methods=["POST"])
@login_obrigatorio
def reativar_manual(telefone):
    reativar_robo_e_resetar(telefone)
    return redirect(url_for("painel"))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True)