#!/usr/bin/env python3
"""
Robô de monitoramento de preços — Black Friday 2026
Lava-louças Electrolux 14 serviços + Robô aspirador (Xiaomi S40 / Electrolux)

Varredura completa: busca cada modelo em Magalu, Mercado Livre, Amazon, Americanas,
KaBuM!, loja Electrolux, Zoom e Buscapé, e ainda confere páginas de produto
específicas (Casas Bahia, Extra, Fast Shop, Mi Brasil...).

Instalação:
  pip3 install requests beautifulsoup4
  pip3 install playwright && python3 -m playwright install chromium   # recomendado:
      # abre um navegador invisível para sites que bloqueiam robôs (Casas Bahia, Amazon...)

Uso:
  python3 robo_precos.py              # uma varredura e gera relatorio.html
  python3 robo_precos.py --loop 3     # repete a cada 3 horas (Ctrl+C para parar)

Notificações: defina NTFY_TOPICO (app ntfy) e/ou TELEGRAM_TOKEN + TELEGRAM_CHAT_ID.
"""
import argparse, json, os, re, sqlite3, statistics, time, html
from datetime import datetime, timedelta
from urllib.parse import quote, quote_plus, urljoin

import requests
from bs4 import BeautifulSoup

# ===================== CONFIGURAÇÃO =====================
# Lojas varridas pela busca. {q} = termo de busca do produto.
BUSCAS = {
    "Magalu":        lambda q: f"https://www.magazineluiza.com.br/busca/{quote(q)}/",
    "Mercado Livre": lambda q: f"https://lista.mercadolivre.com.br/{q.replace(' ', '-')}",
    "Amazon":        lambda q: f"https://www.amazon.com.br/s?k={quote_plus(q)}",
    "Americanas":    lambda q: f"https://www.americanas.com.br/busca/{q.replace(' ', '-')}",
    "KaBuM!":        lambda q: f"https://www.kabum.com.br/busca/{q.replace(' ', '-')}",
    "Electrolux":    lambda q: f"https://loja.electrolux.com.br/{quote(q)}",
    "Zoom":          lambda q: f"https://www.zoom.com.br/busca/{quote_plus(q)}",
    "Buscapé":       lambda q: f"https://www.buscape.com.br/busca/{quote_plus(q)}",
    "Época":         lambda q: f"https://www.epocacosmeticos.com.br/pesquisa?q={quote_plus(q)}",
    "Beleza na Web": lambda q: f"https://www.belezanaweb.com.br/busca/?q={quote_plus(q)}",
}
# Em quais lojas buscar cada tipo de produto (evita acessos inúteis e acelera a varredura)
LOJAS_ELETRO = ["Magalu", "Mercado Livre", "Amazon", "Americanas", "KaBuM!", "Electrolux", "Zoom", "Buscapé"]
LOJAS_CELULAR = ["Magalu", "Mercado Livre", "Amazon", "Americanas", "KaBuM!", "Zoom", "Buscapé"]
LOJAS_PERFUME = ["Magalu", "Mercado Livre", "Amazon", "Época", "Beleza na Web", "Zoom", "Buscapé"]
EXCLUIR_CELULAR = ["capa ", "capinha", "case ", "película", "pelicula", "carregador", "cabo ", "seminovo",
                   "vitrine", "excelente estado", "bom estado", "sem caixa", "vidro traseiro"]
EXCLUIR_PERFUME = ["elixir", "intense", "body lotion", "loção", "locao", "kit ", "miniatura",
                   "decant", "contratipo", "inspirad", "similar", "tester", "sem caixa",
                   "amostra", "desodorante", "refil"]
LOJA_NOME = {  # nomes bonitos para páginas de produto
    "www.casasbahia.com.br": "Casas Bahia", "www.extra.com.br": "Extra",
    "www.zoom.com.br": "Zoom", "www.buscape.com.br": "Buscapé",
    "www.kabum.com.br": "KaBuM!", "www.mibrasil.com.br": "Mi Brasil",
    "loja.electrolux.com.br": "Electrolux", "site.fastshop.com.br": "Fast Shop",
    "www.magazineluiza.com.br": "Magalu", "www.amazon.com.br": "Amazon",
    "www.extrafarma.com.br": "Extrafarma",
}
# Anúncios descartados na busca (usados, peças, acessórios).
EXCLUIR = ["usado", "recondicionado", "seminovo", "vitrine", "peça", "peças", "placa",
           "mangueira", "pressostato", "válvula", "valvula", "anel ved", "refil",
           "reposição", "reposicao", "compatível", "compativel", "acessório", "capa "]

# padrao: expressão que precisa aparecer no anúncio (evita confundir S40 com S40C/S40 Pro).
# preco_min/preco_max: faixa plausível. alvo: seu preço-alvo.
# ref_6m (opcional): {"min": ..., "mediana": ...} tirados do Histórico de Preços do Zoom/Buscapé.
PRODUTOS = [
    # ---------- LAVA-LOUÇAS ELECTROLUX 14 SERVIÇOS ----------
    {"id": "LL14X", "categoria": "Lava-louças", "nome": "Electrolux LL14X Inox (recomendado)",
     "busca": "electrolux ll14x", "padrao": r"ll14x",
     "preco_min": 2500, "preco_max": 7000, "alvo": 3800,
     "ref_6m": None,  # ex.: {"min": 3599.00, "mediana": 4274.00}
     "urls": [
        "https://www.zoom.com.br/lava-loucas/lava-loucas-14-servicos-electrolux-ll14x-inox",
        "https://www.buscape.com.br/lava-loucas/lava-loucas-14-servicos-electrolux-ll14x-inox",
        "https://www.casasbahia.com.br/lava-loucas-electrolux-inox-com-14-servicos-07-programas-de-lavagem-e-funcao-higienizar-compras-ll14x/p/1518982555",
        "https://www.extra.com.br/lava-loucas-electrolux-inox-com-14-servicos-07-programas-de-lavagem-e-funcao-higienizar-compras-ll14x/p/1518982555",
     ]},
    {"id": "LL14P", "categoria": "Lava-louças", "nome": "Electrolux LL14P Preta",
     "busca": "electrolux ll14p", "padrao": r"ll14p",
     "preco_min": 2500, "preco_max": 7000, "alvo": 3500, "ref_6m": None,
     "urls": [
        "https://www.casasbahia.com.br/lava-loucas-electrolux-14-servicos-preta-experience-com-funcao-higienizar-compras-ll14p/p/1570748139",
        "https://www.extra.com.br/lava-loucas-electrolux-14-servicos-preta-experience-com-funcao-higienizar-compras-ll14p/p/1570748139",
     ]},
    {"id": "LL14B", "categoria": "Lava-louças", "nome": "Electrolux LL14B (custo-benefício)",
     "busca": "electrolux ll14b", "padrao": r"ll14b",
     "preco_min": 2000, "preco_max": 6000, "alvo": 2900, "ref_6m": None,
     "urls": ["https://www.zoom.com.br/lava-loucas/lava-loucas-electrolux-14-servicos-com-funcao-higienizar-compras-ll14b"]},
    {"id": "LP14E", "categoria": "Lava-louças", "nome": "Electrolux LP14E Preta",
     "busca": "electrolux lp14e", "padrao": r"lp14e",
     "preco_min": 2000, "preco_max": 6000, "alvo": 2900, "ref_6m": None,
     "urls": ["https://www.zoom.com.br/lava-loucas/lava-loucas-14-servicos-electrolux-lp14e"]},
    {"id": "LS14E", "categoria": "Lava-louças", "nome": "Electrolux LS14E Inox Lava & Seca",
     "busca": "electrolux ls14e", "padrao": r"\bls14e\b",
     "preco_min": 2000, "preco_max": 6500, "alvo": 3000, "ref_6m": None, "urls": []},
    {"id": "LV14X", "categoria": "Lava-louças", "nome": "Electrolux LV14X Inox",
     "busca": "electrolux lv14x", "padrao": r"lv14x",
     "preco_min": 2500, "preco_max": 9000, "alvo": 4000, "ref_6m": None,
     "urls": ["https://www.buscape.com.br/lava-loucas/lava-loucas-14-servicos-electrolux-lv14x-inox"]},

    # ---------- ROBÔ ASPIRADOR ----------
    {"id": "S40", "categoria": "Robô aspirador", "nome": "Xiaomi Robot Vacuum S40 (recomendado)",
     "busca": "xiaomi robot vacuum s40", "lojas": LOJAS_CELULAR, "padrao": r"\bs40(?!\s?(pro|c\b|e\b|ultra|max))",
     "preco_min": 800, "preco_max": 3000, "alvo": 1100,
     "ref_6m": None,  # ex.: {"min": 1150.00, "mediana": 1450.00}
     "urls": ["https://www.kabum.com.br/produto/1047130"]},
    {"id": "S40PRO", "categoria": "Robô aspirador", "nome": "Xiaomi Robot Vacuum S40 Pro",
     "busca": "xiaomi robot vacuum s40 pro", "lojas": LOJAS_CELULAR, "padrao": r"\bs40\s?pro",
     "preco_min": 1400, "preco_max": 5000, "alvo": 1900, "ref_6m": None,
     "urls": [
        "https://www.kabum.com.br/produto/1003959/robo-aspiador-xiaomi-robot-vacuum-s40-pro-gb",
        "https://www.mibrasil.com.br/robo-aspirador-de-po-com-mop-giratorio-s40-pro-xiaomi-prin-p264742",
     ]},

    # ---------- iPHONE (novo e lacrado; só 256GB e 512GB) ----------
    # Cada capacidade é um item próprio (alvo e histórico separados); a busca na
    # loja é feita uma vez só por modelo e reaproveitada pelas duas capacidades.
    {"id": "IP17_256", "categoria": "iPhone", "nome": "iPhone 17 256GB",
     "busca": "iphone 17", "lojas": LOJAS_CELULAR, "excluir": EXCLUIR_CELULAR,
     "padrao": r"iphone\s?17(?!\d)(?!\s?(pro|air|plus|e\b))(?=.*\b256\s?gb)",
     "preco_min": 4000, "preco_max": 9000, "alvo": 5100,
     # estimativa: menor ~R$ 5.299 (Buscapé, nov/26); preço normal ~R$ 6.000
     "ref_6m": {"min": 5299.00, "mediana": 6000.00},
     "urls": ["https://www.buscape.com.br/celular/celular-apple-iphone-17-256gb",
              "https://www.kabum.com.br/produto/1016330/iphone-17-256gb-preto"]},
    {"id": "IP17_512", "categoria": "iPhone", "nome": "iPhone 17 512GB",
     "busca": "iphone 17", "lojas": LOJAS_CELULAR, "excluir": EXCLUIR_CELULAR,
     "padrao": r"iphone\s?17(?!\d)(?!\s?(pro|air|plus|e\b))(?=.*\b512\s?gb)",
     "preco_min": 4800, "preco_max": 11000, "alvo": 6100, "ref_6m": None, "urls": []},
    {"id": "IP17PM_256", "categoria": "iPhone", "nome": "iPhone 17 Pro Max 256GB",
     "busca": "iphone 17 pro max", "lojas": LOJAS_CELULAR, "excluir": EXCLUIR_CELULAR,
     "padrao": r"iphone\s?17\s?pro\s?max(?=.*\b256\s?gb)",
     "preco_min": 6500, "preco_max": 15000, "alvo": 8000,
     # estimativa: menor histórico ~R$ 8.150 (jul/26); ofertas típicas R$ 8.300–9.500
     "ref_6m": {"min": 8150.00, "mediana": 8900.00},
     "urls": ["https://www.buscape.com.br/celular/celular-apple-iphone-17-pro-max-256gb"]},
    {"id": "IP16PM_256", "categoria": "iPhone", "nome": "iPhone 16 Pro Max 256GB",
     "busca": "iphone 16 pro max", "lojas": LOJAS_CELULAR,
     "excluir": EXCLUIR_CELULAR + ["usado"],
     "padrao": r"iphone\s?16\s?pro\s?max(?=.*\b256\s?gb)",
     "preco_min": 5000, "preco_max": 13000, "alvo": 6800, "ref_6m": None, "urls": []},
]


# ===================== PASSAGENS AÉREAS =====================
# Consulta o Google Flights pelo SerpApi (plano grátis: 250 buscas/mês, sem cartão).
# Crie a chave em serpapi.com e cadastre como segredo SERPAPI_KEY no GitHub.
# Cada linha abaixo = 1 busca por consulta. Preço = ida e volta, por adulto, em reais.
# alvo: None = usa o limite de baixo da "faixa típica" que o próprio Google calcula.
VOO_BASE = {"categoria": "Passagem aérea", "origem": "GIG,SDU", "destino": "SSA",
            "ida": "2027-01-31", "adultos": 1, "alvo": None}
VOOS = [
    dict(VOO_BASE, id="VOO_SSA_0902", volta="2027-02-09",
         nome="Rio (GIG/SDU) ⇄ Salvador · ida 31/01 · volta 09/02/27"),
    dict(VOO_BASE, id="VOO_SSA_1002", volta="2027-02-10",
         nome="Rio (GIG/SDU) ⇄ Salvador · ida 31/01 · volta 10/02/27"),
    dict(VOO_BASE, id="VOO_SSA_1102", volta="2027-02-11",
         nome="Rio (GIG/SDU) ⇄ Salvador · ida 31/01 · volta 11/02/27"),
]
VOOS_INTERVALO_H = 12   # consulta voos a cada 12h (3 buscas x 2/dia ≈ 180/mês, dentro do grátis)
VOOS_LIMITE_MES = 230   # trava de segurança: nunca passa disso no mês

INTERVALO_PADRAO_H = 3
JANELA_DIAS = 180            # compara com os últimos 6 meses
COMPRE_AGORA_DESCONTO = 0.15 # 15% abaixo da mediana de 6 meses + abaixo do mínimo de 6 meses
HIST_MIN_COLETAS = 20        # histórico próprio só vale como base de 6 meses com
HIST_MIN_DIAS = 3            # pelo menos 20 leituras ao longo de 3+ dias (ou ref_6m preenchido)
SUSPEITO = 0.55              # preço < 55% da mediana das outras lojas = leitura suspeita, descarta
TABELA = 1.5                 # preço > 1,5x a mediana das lojas = preço de tabela/marketplace, ignora
PAUSA_S = 1.5                # intervalo entre acessos (educação com os sites)
USAR_NAVEGADOR = True        # usa Playwright, se instalado, quando um site bloquear
DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "historico_precos.db")
RELATORIO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "relatorio.html")
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9",
}
# ========================================================


def brl(txt):
    """'R$ 3.239,10' -> 3239.10"""
    if isinstance(txt, (int, float)):
        return float(txt)
    s = re.sub(r"[^\d,\.]", "", str(txt))
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"\d{1,3}(\.\d{3})+", s):  # "1.349" do Mercado Livre = mil e pouco
        s = s.replace(".", "")
    try:
        return float(s)
    except ValueError:
        return None


def precos_jsonld(soup):
    out = []
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
        except Exception:
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            d = stack.pop()
            if isinstance(d, list):
                stack.extend(d); continue
            if not isinstance(d, dict):
                continue
            for k in ("price", "lowPrice"):
                if k in d:
                    out.append(brl(d[k]))
            for v in d.values():
                if isinstance(v, (dict, list)):
                    stack.append(v)
    return out


def extrair_preco(html_txt, pmin, pmax):
    soup = BeautifulSoup(html_txt, "html.parser")
    candidatos = precos_jsonld(soup)
    for sel in ['meta[property="product:price:amount"]', 'meta[itemprop="price"]',
                'meta[property="og:price:amount"]', '[itemprop="price"]']:
        for el in soup.select(sel):
            candidatos.append(brl(el.get("content") or el.get_text()))
    validos = [p for p in candidatos if p and pmin <= p <= pmax]
    if not validos:  # último recurso: qualquer "R$ x" na página dentro da faixa
        for m in re.findall(r"R\$\s*([\d\.]+,\d{2})", soup.get_text(" ")):
            p = brl(m)
            if p and pmin <= p <= pmax:
                validos.append(p)
    return min(validos) if validos else None


def reais(v, casas=2):
    """3300.5 -> 'R$ 3.300,50'"""
    t = f"{v:,.{casas}f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {t}"


def loja(url):
    return LOJA_NOME.get(url.split('/')[2], None) or re.sub(r"^(www\.|m\.)", "", url.split("/")[2]).split(".")[0].capitalize()


def db():
    con = sqlite3.connect(DB)
    con.execute("""CREATE TABLE IF NOT EXISTS precos(
        ts TEXT, produto TEXT, loja TEXT, url TEXT, preco REAL)""")
    con.execute("""CREATE TABLE IF NOT EXISTS alertas(
        ts TEXT, produto TEXT, url TEXT, nivel INTEGER, preco REAL)""")
    con.execute("""CREATE TABLE IF NOT EXISTS voos_info(
        ts TEXT, produto TEXT, tipico_min REAL, tipico_max REAL, hist_min REAL, nivel TEXT)""")
    con.execute("CREATE TABLE IF NOT EXISTS voos_uso(ts TEXT, produto TEXT)")
    return con


def base_6m(con, p):
    """Mínimo e mediana dos últimos 6 meses, usando o MELHOR preço de cada varredura
    (assim preços de tabela de uma loja não inflam a mediana) + se a base é confiável."""
    corte = (datetime.now() - timedelta(days=JANELA_DIAS)).isoformat()
    linhas = con.execute("""SELECT ts, MIN(preco) FROM precos WHERE produto=? AND ts>=?
                            GROUP BY ts""", (p["id"], corte)).fetchall()
    hist = [r[1] for r in linhas]
    dias = len({r[0][:10] for r in linhas})
    ref = p.get("ref_6m") or {}
    proprio_ok = len(hist) >= HIST_MIN_COLETAS and dias >= HIST_MIN_DIAS
    mins = [v for v in (min(hist) if hist else None, ref.get("min")) if v]
    minimo = min(mins) if mins else None
    if proprio_ok or (hist and not ref.get("mediana")):
        mediana, fonte = statistics.median(hist), f"{len(hist)} varreduras em {dias} dia(s)"
    else:
        mediana, fonte = ref.get("mediana"), "referência manual"
    confiavel = proprio_ok or bool(ref.get("mediana") and ref.get("min"))
    return minimo, mediana, fonte, confiavel


NIVEIS = {3: "COMPRE AGORA", 2: "Preço-alvo atingido", 1: "Novo mínimo"}


def classificar(p, preco, minimo, mediana, confiavel):
    """3 = COMPRE AGORA, 2 = alvo, 1 = novo mínimo, 0 = nada.
    COMPRE AGORA exige base de 6 meses confiável E as duas condições ao mesmo tempo:
    abaixo do menor preço de 6 meses e pelo menos 15% abaixo da mediana."""
    if (confiavel and minimo and mediana and preco < minimo
            and preco <= mediana * (1 - COMPRE_AGORA_DESCONTO)):
        return 3
    if preco <= p["alvo"]:
        return 2
    if minimo and preco < minimo * 0.97:
        return 1
    return 0


def deve_avisar(con, p, url, nivel, preco):
    """Evita repetir o mesmo alerta a cada coleta."""
    ult = con.execute("""SELECT ts,nivel,preco FROM alertas WHERE produto=? AND url=?
                         ORDER BY ts DESC LIMIT 1""", (p["id"], url)).fetchone()
    if not ult:
        return True
    ts, niv, pr = ult
    velho = datetime.fromisoformat(ts) < datetime.now() - timedelta(hours=24)
    return nivel > niv or preco < pr * 0.99 or velho


def ntfy(msg, nivel=2):
    """Push pelo app ntfy (Android/iOS). Defina NTFY_TOPICO com um nome único."""
    topico = os.getenv("NTFY_TOPICO")
    if not topico:
        return
    url = next((l for l in msg.splitlines() if l.startswith("http")), None)
    estilo = {3: ("🚨 COMPRE AGORA", 5, ["rotating_light", "moneybag"]),
              2: ("🎯 Preço-alvo atingido", 4, ["dart"]),
              1: ("📉 Novo mínimo", 3, ["chart_with_downwards_trend"])}[nivel]
    corpo = {"topic": topico, "title": estilo[0], "message": msg,
             "priority": estilo[1], "tags": estilo[2]}
    if url:
        corpo["click"] = url  # tocar na notificação abre a loja
        corpo["actions"] = [{"action": "view", "label": "Abrir oferta", "url": url}]
    try:
        requests.post("https://ntfy.sh/", json=corpo, timeout=15)
    except Exception as e:
        print("  ! falha no ntfy:", e)


def telegram(msg):
    tok, chat = os.getenv("TELEGRAM_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if not (tok and chat):
        return
    try:
        requests.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                      data={"chat_id": chat, "text": msg, "disable_web_page_preview": True},
                      timeout=15)
    except Exception as e:
        print("  ! falha no Telegram:", e)


def montar_msg(p, nivel, preco, url, minimo, mediana, nome_loja, confiavel):
    linhas = []
    if nivel == 3:
        linhas.append("🚨 COMPRE AGORA — oferta muito acima do esperado!")
    linhas.append(f"{p['nome']}: {reais(preco)} na {nome_loja}")
    if mediana:
        dif = (1 - preco / mediana) * 100
        linhas.append(f"{abs(dif):.0f}% {'abaixo' if dif >= 0 else 'acima'} da mediana "
                      f"de 6 meses ({reais(mediana)})")
    if minimo:
        linhas.append(f"Menor preço em 6 meses: {reais(minimo)}")
    if not confiavel:
        linhas.append("(histórico ainda curto: comparação de 6 meses em formação)")
    linhas.append(f"Seu alvo: {reais(p['alvo'], 0)}")
    linhas.append(url)
    return "\n".join(linhas)


# ------------------ acesso às páginas ------------------
_nav = {"pw": None, "browser": None, "ok": None}


def _abrir_navegador():
    if _nav["ok"] is None:
        try:
            from playwright.sync_api import sync_playwright
            _nav["pw"] = sync_playwright().start()
            _nav["browser"] = _nav["pw"].chromium.launch(headless=True)
            _nav["ok"] = True
        except Exception:
            _nav["ok"] = False
            print("  (dica: instale o Playwright para passar por sites que bloqueiam robôs)")
    return _nav["ok"]


def _fechar_navegador():
    if _nav["ok"]:
        _nav["browser"].close(); _nav["pw"].stop()
    _nav.update(pw=None, browser=None, ok=None)


_cache = {}  # páginas já baixadas nesta varredura (zerado a cada rodada)


def baixar(url, forcar_navegador=False):
    """Devolve (html, como), reaproveitando páginas já baixadas nesta varredura."""
    chave = (url, forcar_navegador)
    if chave not in _cache:
        _cache[chave] = _baixar(url, forcar_navegador)
        time.sleep(PAUSA_S)  # pausa só quando acessa o site de verdade
    return _cache[chave]


def _baixar(url, forcar_navegador=False):
    """Tenta requests; se bloquear, tenta o navegador."""
    status = None
    if not forcar_navegador:
        try:
            r = requests.get(url, headers=HEADERS, timeout=25)
            status = r.status_code
            if r.ok and len(r.text) > 5000:
                return r.text, "http"
        except Exception as e:
            status = type(e).__name__
    if USAR_NAVEGADOR and _abrir_navegador():
        try:
            pg = _nav["browser"].new_page(user_agent=HEADERS["User-Agent"], locale="pt-BR")
            pg.goto(url, timeout=45000, wait_until="domcontentloaded")
            pg.wait_for_timeout(4000)
            txt = pg.content(); pg.close()
            return txt, "navegador"
        except Exception as e:
            status = f"{status} / navegador: {type(e).__name__}"
    return None, f"bloqueado ({status})"


# ------------------ leitura de páginas de busca ------------------
RE_PRECO = re.compile(r"R\$\s*(\d{1,3}(?:\.\d{3})+(?:,\d{2})?|\d+,\d{2}|\d{3,5})")


RE_FAIXA = re.compile(r"(até|acima de|a partir de|abaixo de|de)\s*R\$\s*[\d\.,]+(\s*(a|até)\s*R\$\s*[\d\.,]+)?", re.I)
RE_BUSCA = re.compile(r"/busca|/s\?|[?&](k|q|ft|query)=|lista\.mercadolivre", re.I)


def ofertas_busca(html_txt, p, base_url):
    """Acha cada anúncio (cartão com link de produto) que cita o modelo.
    Devolve (menor_preço, link_direto_do_anúncio) ou (None, None)."""
    soup = BeautifulSoup(html_txt, "html.parser")
    padrao = re.compile(p["padrao"], re.I)
    vistos, achados = set(), []
    for no in soup.find_all(string=padrao):
        if no.parent.name in ("script", "style", "noscript", "title", "meta", "option", "label"):
            continue
        # o título do anúncio precisa estar dentro (ou ao lado) de um link de produto
        link = no.find_parent("a", href=True)
        card = no.parent
        for _ in range(8):  # sobe até o bloco que também tem preço
            if card.parent is None or "R$" in card.get_text(" "):
                break
            card = card.parent
        if link is None:
            link = card.find("a", href=True)
        if link is None:
            continue
        href = urljoin(base_url, link["href"])
        if RE_BUSCA.search(href) or href.rstrip("/") == base_url.rstrip("/"):
            continue  # link de filtro/busca, não de produto
        texto = card.get_text(" ", strip=True)
        if id(card) in vistos or "R$" not in texto or len(texto) > 1200:
            continue
        vistos.add(id(card))
        baixo = texto.lower()
        if any(x in baixo for x in EXCLUIR + p.get("excluir", [])) or not padrao.search(baixo):
            continue
        limpo = RE_FAIXA.sub(" ", texto)  # tira filtros tipo "até R$ 2.500"
        limpo = re.sub(r"\d+\s*x\s*(de\s*)?R\$\s*[\d\.,]+", " ", limpo, flags=re.I)  # parcelas
        vals = [brl(m) for m in RE_PRECO.findall(limpo)]
        vals = [v for v in vals if v and p["preco_min"] < v <= p["preco_max"]]
        if vals:
            achados.append((min(vals), href))
    if achados:
        return min(achados)
    return ofertas_json(soup, p, base_url)


CHAVES_TITULO = {"title", "name", "text", "productname", "nome", "titulo", "displayname"}
CHAVES_PRECO = {"price", "bestprice", "value", "amount", "saleprice", "pricevalue",
                "sellingprice", "currentprice", "current_price", "pixprice", "finalprice"}
CHAVES_URL = {"url", "permalink", "path", "link", "href", "producturl", "canonical"}
BLOQUEIO = ["captcha", "account-verification", "are you a robot", "não sou um robô",
            "verifique que você", "access denied", "acesso negado", "px-captcha",
            "request unsuccessful", "unusual traffic"]


def _jsons_da_pagina(soup):
    """Dados que a loja embute na página (__NEXT_DATA__, estado inicial, ld+json...)."""
    for tag in soup.find_all("script"):
        txt = (tag.string or tag.get_text() or "").strip()
        if len(txt) < 50 or "{" not in txt:
            continue
        ini = txt.find("{") if not txt.startswith("[") else 0
        fim = max(txt.rfind("}"), txt.rfind("]")) + 1
        try:
            yield json.loads(txt[ini:fim])
        except Exception:
            continue


def _precos_em(o, p, prof=0):
    out = []
    if prof > 4:
        return out
    if isinstance(o, dict):
        for k, v in o.items():
            if k.lower() in CHAVES_PRECO and isinstance(v, (int, float, str)) and not isinstance(v, bool):
                x = brl(v)
                if x and p["preco_min"] < x <= p["preco_max"]:
                    out.append(x)
            elif isinstance(v, (dict, list)):
                out += _precos_em(v, p, prof + 1)
    elif isinstance(o, list):
        for v in o[:30]:
            out += _precos_em(v, p, prof + 1)
    return out


def _url_em(o, prof=0):
    if prof > 4:
        return None
    if isinstance(o, dict):
        for k, v in o.items():
            if (k.lower() in CHAVES_URL and isinstance(v, str) and len(v) > 8
                    and " " not in v and not RE_IMAGEM.search(v)):
                return v
        for v in o.values():
            if isinstance(v, (dict, list)):
                u = _url_em(v, prof + 1)
                if u:
                    return u
    elif isinstance(o, list):
        for v in o[:30]:
            u = _url_em(v, prof + 1)
            if u:
                return u
    return None


RE_IMAGEM = re.compile(r"\.(jpe?g|png|webp|gif|svg|avif)(\?|$)|mlstatic|/images?/|/img/", re.I)


def _links_em(o, prof=0, achados=None):
    """Links de produto distintos dentro de um bloco (ignora imagens)."""
    achados = set() if achados is None else achados
    if prof > 6 or len(achados) > 1:
        return achados
    if isinstance(o, dict):
        for k, v in o.items():
            if (k.lower() in CHAVES_URL and isinstance(v, str) and len(v) > 8
                    and " " not in v and not RE_IMAGEM.search(v)):
                achados.add(v.split("?")[0].rstrip("/"))
            elif isinstance(v, (dict, list)):
                _links_em(v, prof + 1, achados)
    elif isinstance(o, list):
        for v in o:
            if isinstance(v, (dict, list)):
                _links_em(v, prof + 1, achados)
    return achados


def ofertas_json(soup, p, base_url):
    """Acha anúncios do modelo nos dados JSON da página (Magalu, Mercado Livre...)."""
    padrao = re.compile(p["padrao"], re.I)
    excluir = EXCLUIR + p.get("excluir", [])
    achados = []

    def andar(o, pais):
        if isinstance(o, dict):
            titulo = next((v for k, v in o.items() if isinstance(v, str)
                           and k.lower() in CHAVES_TITULO and padrao.search(v)), None)
            if titulo and not any(x in titulo.lower() for x in excluir):
                # sobe do título até o bloco do anúncio que tem preço (sem pegar a lista toda)
                for anc in [o] + pais[::-1][:5]:
                    # para ao chegar num bloco com mais de um anúncio (lista de resultados)
                    if len(json.dumps(anc, ensure_ascii=False)) > 8000 or len(_links_em(anc)) > 1:
                        break
                    precos = _precos_em(anc, p)
                    if precos:
                        cadeia = [o] + pais[::-1][:8]
                        pos = next(i for i, a in enumerate(cadeia) if a is anc)
                        u = None
                        for a2 in cadeia[pos:pos + 4]:  # link pode estar um nível acima
                            if len(json.dumps(a2, ensure_ascii=False)) > 8000 or len(_links_em(a2)) > 1:
                                break
                            u = _url_em(a2)
                            if u:
                                break
                        if u and u.startswith("www."):
                            u = "https://" + u
                        href = urljoin(base_url, u) if u else None
                        if href and not RE_BUSCA.search(href):
                            achados.append((min(precos), href))
                        break
            for v in o.values():
                if isinstance(v, (dict, list)):
                    andar(v, pais + [o])
        elif isinstance(o, list):
            for v in o:
                if isinstance(v, (dict, list)):
                    andar(v, pais + [o])

    for dados in _jsons_da_pagina(soup):
        try:
            andar(dados, [])
        except RecursionError:
            continue
    return min(achados) if achados else (None, None)


def diagnostico(html_txt, p):
    """Explica, no log, por que uma página de busca não deu preço."""
    baixo = html_txt.lower()
    if any(b in baixo for b in BLOQUEIO):
        return "bloqueado (verificação anti-robô)"
    soup = BeautifulSoup(html_txt, "html.parser")
    titulo = (soup.title.get_text(" ", strip=True) if soup.title else "")[:40]
    tem = re.search(p["padrao"], html_txt, re.I) is not None
    if tem:
        return f"modelo aparece, preço não lido · {len(html_txt)//1000}KB · '{titulo}'"
    return f"sem anúncio do modelo · {len(html_txt)//1000}KB · '{titulo}'"


def fontes(p):
    """Lista (nome_da_loja, url, tipo) de tudo que será varrido para o produto."""
    lojas = p.get("lojas", LOJAS_ELETRO)
    out = [(nome, BUSCAS[nome](p["busca"]), "busca") for nome in lojas]
    out += [(loja(u), u, "produto") for u in p["urls"]]
    return out


# ------------------ varredura ------------------
# ------------------ passagens aéreas (Google Flights via SerpApi) ------------------
NIVEL_PT = {"low": "baixo", "typical": "típico", "high": "alto"}


def info_voo(con, p):
    """Última 'faixa típica' e histórico que o Google informou para essa busca."""
    return con.execute("""SELECT tipico_min, tipico_max, hist_min, nivel FROM voos_info
                          WHERE produto=? ORDER BY ts DESC LIMIT 1""", (p["id"],)).fetchone()


def alvo_de(con, p):
    if p.get("alvo"):
        return p["alvo"]
    inf = info_voo(con, p) if p in VOOS else None
    return inf[0] if inf and inf[0] else None


def base_item(con, p):
    """Base de comparação: para voos, junta o histórico do robô com o do Google."""
    minimo, mediana, fonte, confiavel = base_6m(con, p)
    if p in VOOS:
        inf = info_voo(con, p)
        if inf and inf[0] and inf[1]:
            mediana = (inf[0] + inf[1]) / 2
            fonte, confiavel = f"faixa típica do Google {reais(inf[0], 0)}–{reais(inf[1], 0)}", True
        if inf and inf[2]:
            minimo = min(x for x in (minimo, inf[2]) if x)
    return minimo, mediana, fonte, confiavel


def _uso_voos(con):
    mes = datetime.now().strftime("%Y-%m")
    usados = con.execute("SELECT COUNT(*) FROM voos_uso WHERE ts LIKE ?", (mes + "%",)).fetchone()[0]
    ultimo = con.execute("SELECT MAX(ts) FROM voos_uso").fetchone()[0]
    return usados, ultimo


def buscar_voo(p, chave):
    """Uma busca de ida e volta. Devolve dict com o voo mais barato + insights do Google."""
    r = requests.get("https://serpapi.com/search.json", timeout=60, params={
        "engine": "google_flights", "api_key": chave, "type": 1,
        "departure_id": p["origem"], "arrival_id": p["destino"],
        "outbound_date": p["ida"], "return_date": p["volta"],
        "adults": p.get("adultos", 1), "currency": "BRL", "hl": "pt", "gl": "br"})
    d = r.json()
    if d.get("error"):
        raise RuntimeError(d["error"])
    opcoes = [o for o in d.get("best_flights", []) + d.get("other_flights", []) if o.get("price")]
    ins = d.get("price_insights", {}) or {}
    hist = [x[1] for x in ins.get("price_history", []) if isinstance(x, list) and len(x) > 1]
    faixa = ins.get("typical_price_range") or [None, None]
    link = (d.get("search_metadata") or {}).get("google_flights_url") or "https://www.google.com/travel/flights"
    if not opcoes:
        return {"preco": None, "faixa": faixa, "hist_min": min(hist) if hist else None,
                "nivel": ins.get("price_level"), "link": link}
    o = min(opcoes, key=lambda x: x["price"])
    trechos = o.get("flights", [])
    cias = sorted({t.get("airline", "?") for t in trechos})
    paradas = len(trechos) - 1
    saida = trechos[0].get("departure_airport", {}) if trechos else {}
    hora = (saida.get("time") or "")[-5:]
    desc = (f"{'/'.join(cias)} · {'direto' if paradas == 0 else f'{paradas} parada(s)'} · "
            f"sai do {saida.get('id', '?')} {hora}")
    return {"preco": float(o["price"]), "desc": desc, "faixa": faixa, "link": link,
            "hist_min": min(hist) if hist else None, "nivel": ins.get("price_level")}


def coletar_voos(con, agora, alertas):
    """Consulta os voos respeitando o intervalo e o limite mensal do plano grátis."""
    if not VOOS:
        return 0
    chave = os.getenv("SERPAPI_KEY")
    print("\n  ✈️  Passagens aéreas")
    if not chave:
        print("    (pulando: cadastre o segredo SERPAPI_KEY para monitorar voos)")
        return 0
    ativos = [v for v in VOOS if v["ida"] >= datetime.now().strftime("%Y-%m-%d")]
    usados, ultimo = _uso_voos(con)
    if ultimo and datetime.fromisoformat(ultimo) > datetime.now() - timedelta(hours=VOOS_INTERVALO_H):
        prox = datetime.fromisoformat(ultimo) + timedelta(hours=VOOS_INTERVALO_H)
        print(f"    próxima consulta de voos às {prox:%d/%m %H:%M} "
              f"({usados}/{VOOS_LIMITE_MES} buscas usadas no mês)")
        return 0
    if usados + len(ativos) > VOOS_LIMITE_MES:
        print(f"    limite do mês atingido ({usados}/{VOOS_LIMITE_MES}); volta no dia 1º")
        return 0
    ok = 0
    for p in ativos:
        try:
            v = buscar_voo(p, chave)
        except Exception as e:
            print(f"    {p['nome'][-18:]:18} erro: {e}")
            continue
        finally:
            con.execute("INSERT INTO voos_uso VALUES(?,?)", (agora, p["id"]))
        f0, f1 = (v["faixa"] + [None, None])[:2]
        con.execute("INSERT INTO voos_info VALUES(?,?,?,?,?,?)",
                    (agora, p["id"], f0, f1, v["hist_min"], v["nivel"]))
        if not v["preco"]:
            print(f"    {p['nome'][-18:]:18} sem voos encontrados")
            continue
        minimo, mediana, _, confiavel = base_item(con, p)  # já com a faixa nova do Google
        ok += 1
        nivel_g = NIVEL_PT.get(v["nivel"], "?")
        print(f"    volta {p['volta'][8:]}/{p['volta'][5:7]}  {reais(v['preco']):>12}  "
              f"{v['desc']} · Google: preço {nivel_g}")
        con.execute("INSERT INTO precos VALUES(?,?,?,?,?)",
                    (agora, p["id"], v["desc"], v["link"], v["preco"]))
        p_ef = dict(p, alvo=alvo_de(con, p) or v["preco"] * 0.85)
        nivel = classificar(p_ef, v["preco"], minimo, mediana, confiavel)
        if nivel and deve_avisar(con, p, v["link"], nivel, v["preco"]):
            con.execute("INSERT INTO alertas VALUES(?,?,?,?,?)",
                        (agora, p["id"], v["link"], nivel, v["preco"]))
            msg = montar_msg(p_ef, nivel, v["preco"], v["link"], minimo, mediana,
                             v["desc"], confiavel)
            msg = msg.replace(f"\n{v['link']}", f"\nGoogle Flights: preço {nivel_g} para a data\n{v['link']}")
            msg = (msg.replace("da mediana de 6 meses", "do preço típico (Google)")
                      .replace("Menor preço em 6 meses", "Menor preço já registrado")
                      .replace(": R$", "\nR$", 1).replace(" na ", " · ", 1))
            alertas.append((nivel, msg))
    con.commit()
    usados, _ = _uso_voos(con)
    print(f"    ({usados}/{VOOS_LIMITE_MES} buscas de voo usadas no mês)")
    return ok


def coletar():
    con, agora = db(), datetime.now().isoformat(timespec="minutes")
    alertas, ok, falhas = [], 0, 0
    _cache.clear()
    for p in PRODUTOS:
        minimo, mediana, _, confiavel = base_6m(con, p)  # base ANTES dos preços novos
        print(f"\n  {p['nome']}")
        lidos = []  # (loja, link_direto, preco, como)
        for nome, url, tipo in fontes(p):
            txt, como = baixar(url)
            preco, link = None, url
            for tentativa in (1, 2):
                if txt:
                    if tipo == "busca":
                        preco, link = ofertas_busca(txt, p, url)
                    else:
                        preco, link = extrair_preco(txt, p["preco_min"], p["preco_max"]), url
                if preco or tentativa == 2 or como != "http" or not USAR_NAVEGADOR:
                    break
                txt, como = baixar(url, forcar_navegador=True)  # preço via JavaScript
            if preco:
                lidos.append((nome, link, preco, como))
            else:
                falhas += 1
                if not txt:
                    motivo = como
                elif tipo == "busca":
                    motivo = diagnostico(txt, p)
                else:
                    motivo = "sem preço na página (sem estoque?)"
                print(f"    {nome:14} {'—':>13}  ({motivo})")

        # uma leitura por loja (busca + página de produto da mesma loja contam uma vez)
        por_loja = {}
        for x in lidos:
            if x[0] not in por_loja or x[2] < por_loja[x[0]][2]:
                por_loja[x[0]] = x
        lidos = list(por_loja.values())

        # descarta leituras fora da curva
        if len(lidos) >= 3:
            med_rodada = statistics.median(x[2] for x in lidos)
            for x in [x for x in lidos if x[2] < med_rodada * SUSPEITO]:
                print(f"    {x[0]:14} {reais(x[2]):>13}  (descartado: suspeito, mediana "
                      f"das lojas {reais(med_rodada)})")
                lidos.remove(x); falhas += 1
            for x in [x for x in lidos if x[2] > med_rodada * TABELA]:
                print(f"    {x[0]:14} {reais(x[2]):>13}  (ignorado: preço de tabela/"
                      f"marketplace, mediana das lojas {reais(med_rodada)})")
                lidos.remove(x)

        for nome, link, preco, como in lidos:
            ok += 1
            print(f"    {nome:14} {reais(preco):>13}  ({como})")
            con.execute("INSERT INTO precos VALUES(?,?,?,?,?)", (agora, p["id"], nome, link, preco))
            nivel = classificar(p, preco, minimo, mediana, confiavel)
            if nivel and deve_avisar(con, p, link, nivel, preco):
                con.execute("INSERT INTO alertas VALUES(?,?,?,?,?)",
                            (agora, p["id"], link, nivel, preco))
                alertas.append((nivel, montar_msg(p, nivel, preco, link, minimo, mediana,
                                                  nome, confiavel)))
        con.commit()
    _fechar_navegador()
    _cache.clear()
    ok += coletar_voos(con, agora, alertas)
    print(f"\n  {ok} preços lidos, {falhas} fontes sem preço")
    for nivel, msg in sorted(alertas, key=lambda a: -a[0]):  # COMPRE AGORA primeiro
        print("\n" + msg); telegram(msg); ntfy(msg, nivel)
    gerar_relatorio(con, agora)


def gerar_relatorio(con, rodada=None):
    rodada = rodada or (con.execute("SELECT MAX(ts) FROM precos").fetchone()[0] or "")
    css = """body{font-family:system-ui;max-width:960px;margin:auto;padding:16px;
    background:#fafafa;color:#222}.tw{overflow-x:auto}table{width:100%;border-collapse:collapse;margin-bottom:8px}
    td,th{padding:8px;border-bottom:1px solid #ddd;text-align:left;font-size:14px;vertical-align:top}
    .ok{color:#0a7d32;font-weight:600}.bad{color:#b3261e}details{margin:2px 0 4px}
    summary{cursor:pointer;font-size:13px;color:#555}details td{font-size:13px;padding:4px 8px}
    .ca{background:#b3261e;color:#fff;padding:2px 6px;border-radius:4px;font-size:12px;font-weight:700}
    @media(prefers-color-scheme:dark){body{background:#151515;color:#eee}summary{color:#aaa}
    td,th{border-color:#333}a{color:#8ab4f8}.ok{color:#5dd47f}}"""
    h = [f"<html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width'>"
         f"<title>Monitor de preços</title><style>{css}</style></head><body>",
         f"<h1>Monitor de preços</h1><p>Última varredura: {rodada.replace('T', ' ')}</p>"]
    cat_atual = None
    for p in PRODUTOS + VOOS:
        ts_item = rodada
        if p in VOOS:  # voos são consultados a cada 12h: mostra a última consulta
            ts_item = con.execute("SELECT MAX(ts) FROM precos WHERE produto=?",
                                  (p["id"],)).fetchone()[0] or ""
        lojas = con.execute("""SELECT loja,url,preco FROM precos WHERE produto=? AND ts=?
                               ORDER BY preco""", (p["id"], ts_item)).fetchall()
        if p["categoria"] != cat_atual:
            if cat_atual:
                h.append("</div>")
            cat_atual = p["categoria"]
            h.append(f"<h2>{cat_atual}</h2><div class='tw'>")
        if not lojas:
            h.append(f"<p>{html.escape(p['nome'])}: sem preço nesta varredura</p>")
            continue
        loja_, url, atual = lojas[0]
        minimo, mediana, fonte, confiavel = base_item(con, p)
        alvo = alvo_de(con, p) or atual
        mn, med = minimo or atual, mediana or atual
        var = (atual / med - 1) * 100
        # o mínimo de 6m já inclui o preço atual: "igual ao mínimo" = é o novo mínimo
        excepcional = (confiavel and atual <= mn and atual <= med * (1 - COMPRE_AGORA_DESCONTO))
        selo = " <span class='ca'>COMPRE AGORA</span>" if excepcional else ""
        h.append("<table><tr><th>Modelo</th><th>Melhor agora</th><th>Mín. 6 meses</th>"
                 "<th>vs. mediana 6m</th></tr>")
        h.append(f"<tr><td><b>{html.escape(p['nome'])}</b>{selo}<br><small>alvo {reais(alvo, 0)}{' (faixa típica do Google)' if p in VOOS and not p.get('alvo') else ''}{' · consultado ' + ts_item[8:10] + '/' + ts_item[5:7] + ' ' + ts_item[11:16] if p in VOOS else ''}</small></td>"
                 f"<td class='{'ok' if atual <= alvo else ''}'><a href='{url}'>{reais(atual)}</a> · {loja_}</td>"
                 f"<td>{reais(mn)}</td>"
                 f"<td class='{'ok' if var < -3 else 'bad' if var > 3 else ''}'>{var:+.1f}%"
                 f"<br><small>base: {fonte}</small></td></tr></table>")
        linhas = "".join(f"<tr><td>{l}</td><td><a href='{u}'>{reais(v)}</a></td></tr>"
                         for l, u, v in lojas)
        h.append(f"<details><summary>Ver todas as {len(lojas)} lojas</summary>"
                 f"<table>{linhas}</table></details>")
    h.append("</div><p><small>Preços de busca podem ser à vista/Pix. “vs. mediana 6m” "
             "mostra se o desconto é real. COMPRE AGORA = abaixo do menor preço de 6 meses E "
             "15%+ abaixo da mediana, com histórico confiável (ref_6m ou 20+ leituras em 3+ dias).</small></p></body></html>")
    with open(RELATORIO, "w", encoding="utf-8") as f:
        f.write("\n".join(h))
    print(f"\nRelatório: {RELATORIO}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--loop", type=float, nargs="?", const=INTERVALO_PADRAO_H,
                    help="repetir a cada N horas")
    a = ap.parse_args()
    if not (os.getenv("NTFY_TOPICO") or os.getenv("TELEGRAM_TOKEN")):
        print("Aviso: nenhuma notificação configurada (NTFY_TOPICO ou TELEGRAM_TOKEN).")
    while True:
        print(f"\n== Varredura {datetime.now():%d/%m %H:%M} ==")
        coletar()
        if not a.loop:
            break
        prox = datetime.now() + timedelta(hours=a.loop)
        print(f"\n⏳ Próxima varredura às {prox:%H:%M} ({prox:%d/%m}). "
              f"O robô está aguardando — não travou. Ctrl+C para parar.")
        # confere o relógio real a cada 30s: se o Mac dormir e perder o horário,
        # a varredura roda assim que ele acordar (time.sleep sozinho "congela" no repouso)
        while datetime.now() < prox:
            time.sleep(30)
        atraso = (datetime.now() - prox).total_seconds() / 60
        if atraso > 5:
            print(f"\n⚠️  Varredura das {prox:%H:%M} atrasou {atraso:.0f} min "
                  f"(o computador provavelmente estava em repouso).")
