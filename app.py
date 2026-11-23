import os
import re
from datetime import datetime, timedelta
from functools import wraps
from flask import (
    Flask,
    jsonify,
    redirect,
    render_template_string,
    request,
    session,
    url_for,
)

app = Flask(__name__)

app.secret_key = os.environ.get("FLASK_SECRET_KEY", "sofia-oficial-cetrus-2026-chave-fixa")
CHAVE_ACESSO_PADRAO = os.environ.get("CHAVE_ACESSO", "cetrus2026teste")

# Sessão persistente por 30 dias para evitar pedir senha repetidamente
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=30)
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

MENSAGEM_ENCERRAMENTO = (
    "Atendimento encerrado. Obrigado pelo contato! 💙\n\n"
    "Se precisar de algo, é só enviar uma nova mensagem a qualquer momento"
)

INSTANCIA_NOME = "sofia-oficial"
BLOQUEADOS_COUNT = 7
STATUS_WHATSAPP = "open"

# Contatos de exemplo (substitua pela sua fonte/API de contatos quando integrar)
CONTATOS_SALVOS = [
    {"nome": "Mayara Cetrus Semi", "telefone": "5511999990001"},
    {"nome": "Aluno Teste Secretaria", "telefone": "5511957735761"},
]

# Estado das conversas: telefone -> dict
# robô_pausado = True significa que está em "Atendimentos Humanos (Robô Pausado)"
conversas = {}


def normalizar_telefone(telefone: str) -> str:
    return re.sub(r"\D", "", str(telefone or ""))


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
    """Evita 'could not convert string to float' ao receber frases ou textos livres."""
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
    """Reativa o robô automaticamente e reseta a etapa para evitar erro de tipo."""
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
            return jsonify({"ok": False, "erro": "Chave de acesso inválida."}), 401
        return f(*args, **kwargs)

    return wrapper


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
        return (
            "Olá! Sou a *Sofia*, assistente acadêmica.\n\n"
            "1 - Provas e Avaliações\n"
            "2 - Calendário de Aulas\n"
            "3 - Secretaria Acadêmica\n"
            "5 - Falar com Atendente\n"
            "0 - Encerrar"
        )

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
    # Libera automaticamente a sessão com a chave padrão para não exigir redigitação
    session.permanent = True
    session["chave_liberada"] = True

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
        .btn-orange { background: #e67e2