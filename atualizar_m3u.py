#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Mega Canais -> Lista M3U (otimizada para SS IPTV)
=================================================

Fonte: https://www.megacanais.online/

Como o site funciona (visto no HAR enviado e nas paginas publicas):

  1. A home lista os canais como cards com a logo em /images/<canal>.png
     (ex.: images/premiere7.png) e o nome no atributo alt.
  2. Cada canal abre um player embutido:
         https://embedcanaisdetv.xyz/e/index.php?canal=<canal>
  3. Esse player carrega o HLS (.m3u8) em um CDN (cdn8embed.xyz) e o CDN
     recebe o pedido com Referer/Origin do player (playerembedcanais.site).

O script le a home, abre o player de cada canal, segue os iframes ate achar o
.m3u8 e monta a lista. Para canais cujo CDN exige Referer, a lista ganha as
linhas #EXTVLCOPT (players que nao suportam cabecalhos simplesmente ignoram).

Formas de leitura da home (a que funcionou por ultimo fica em fontes_site.json):

  A) home_cards .... <img src=".../images/<canal>.png" alt="Nome"> na home.
  B) home_script ... catalogo em JSON/JS dentro da pagina (plano B).

Se nenhuma funcionar, nada e publicado: a lista anterior fica intacta.

Regras de atualizacao (reconciliacao):
  - canal que toca  -> fica na lista.
  - canal que falha -> +1 falha; so e REMOVIDO apos 6 atualizacoes seguidas.
  - canal novo      -> entra automaticamente.
  - estado em estado_canais.json (commitado no repositorio).

Uso:
    python atualizar_m3u.py                  # gera + testa + lista final
    python atualizar_m3u.py --sem-testar     # nao valida os streams
    python atualizar_m3u.py --iniciar-estado # zera os contadores de falha
    python atualizar_m3u.py --sondar         # diagnostico do site (cole a saida
                                             # no chat se algo nao funcionar)
    python atualizar_m3u.py --sondar --canal premiere7

So biblioteca padrao do Python (sem pip). Usa ffprobe se estiver instalado.
"""

import argparse
import base64
import concurrent.futures
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import time
import unicodedata
from urllib.parse import urljoin, urlparse, quote

# ---------------------------------------------------------------------------
# Configuracao
# ---------------------------------------------------------------------------

BASE = os.environ.get("MEGA_BASE", "https://www.megacanais.online").rstrip("/")
HOME_URL = BASE + "/"
# Player embutido de cada canal ({canal} = nome do arquivo da logo, ex. premiere7)
EMBED_URL = os.environ.get("MEGA_EMBED", "https://embedcanaisdetv.xyz/e/index.php?canal={canal}")
CANAL_SONDA = os.environ.get("MEGA_CANAL_SONDA", "premiere7")

RAIZ = os.path.dirname(os.path.abspath(__file__))
ARQ_LISTA = os.path.join(RAIZ, "megacanais.m3u")
ARQ_LISTA_COMPLETA = os.path.join(RAIZ, "megacanais_completo.m3u")
ARQ_ESTADO = os.path.join(RAIZ, "estado_canais.json")
ARQ_RELATORIO = os.path.join(RAIZ, "relatorio.json")
ARQ_FONTES = os.path.join(RAIZ, "fontes_site.json")
ARQ_LOG = os.path.join(RAIZ, "log_ultima_execucao.txt")

LIMITE_FALHAS = int(os.environ.get("LIMITE_FALHAS", "6"))
TIMEOUT_HTTP = float(os.environ.get("TIMEOUT_HTTP", "20"))
TIMEOUT_PROBE = float(os.environ.get("TIMEOUT_PROBE", "25"))
WORKERS_HTTP = int(os.environ.get("WORKERS_HTTP", "24"))
WORKERS_PROBE = int(os.environ.get("WORKERS_PROBE", "10"))
WORKERS_API = int(os.environ.get("WORKERS_API", "12"))
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# Cada forma conhecida de ler a home. A ordem e a de tentativa inicial;
# depois disso vale a ordem salva em fontes_site.json.
FORMAS = ["home_cards", "home_script"]
NOMES_FORMAS = {
    "home_cards": "cards da home (<img> em /images/)",
    "home_script": "catalogo em JSON/JS dentro da pagina",
}

# A home agrupa os canais por blocos, sempre nesta ordem. Cada bloco comeca no
# canal abaixo; os canais novos herdam o bloco do canal que vem antes deles.
INICIO_DE_BLOCO = {
    "amazonprimevideo": "Esportes",
    "bandsp": "Abertos",
    "bandnews": "Notícias",
    "aee": "Filmes e Séries",
    "adultswim": "Variedades",
    "cartoonito": "Infantil",
    "cancaonova": "Religiosos",
}

SINONIMOS = {
    "noticias": "Notícias", "notícias": "Notícias", "news": "Notícias",
    "esportes": "Esportes", "esporte": "Esportes",
    "infantil": "Infantil", "kids": "Infantil", "desenhos": "Infantil",
    "documentarios": "Documentários", "documentários": "Documentários",
    "documentario": "Documentários",
    "filmes e series": "Filmes e Séries", "filmes e séries": "Filmes e Séries",
    "filmes": "Filmes e Séries", "series": "Filmes e Séries",
    "séries": "Filmes e Séries",
    "abertos": "Abertos", "aberto": "Abertos",
    "variedades": "Variedades", "variedade": "Variedades",
    "portugal": "Portugal", "pt": "Portugal",
    "religiosos": "Religiosos", "religiao": "Religiosos", "gospel": "Religiosos",
}

ORDEM_PADRAO = [
    "Esportes", "Abertos", "Notícias", "Filmes e Séries",
    "Variedades", "Infantil", "Documentários", "Religiosos", "Portugal",
]

LOG_LINHAS = []


def log(msg=""):
    print(msg, flush=True)
    LOG_LINHAS.append(str(msg))


# ---------------------------------------------------------------------------
# HTTP (sem dependencias externas)
# ---------------------------------------------------------------------------

import urllib.request as _url
import urllib.error as _urlerr
import gzip as _gzip
import zlib as _zlib


def http_get(url, timeout=None, referer=None, aceitar_erro=True, extra=None):
    """GET simples com UA de browser. Retorna (status, bytes, headers)."""
    timeout = timeout or TIMEOUT_HTTP
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/json,application/xhtml+xml,*/*;q=0.8",
        "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate",
        "Cache-Control": "no-cache",
    }
    if referer:
        headers["Referer"] = referer
    if extra:
        headers.update(extra)
    req = _url.Request(url, headers=headers)
    try:
        with _url.urlopen(req, timeout=timeout) as resp:
            dados = resp.read()
            enc = (resp.headers.get("Content-Encoding") or "").lower()
            if "gzip" in enc:
                dados = _gzip.decompress(dados)
            elif "deflate" in enc:
                try:
                    dados = _zlib.decompress(dados)
                except _zlib.error:
                    dados = _zlib.decompress(dados, -_zlib.MAX_WBITS)
            return resp.status, dados, dict(resp.headers)
    except _urlerr.HTTPError as e:
        corpo = b""
        try:
            corpo = e.read()
        except Exception:
            pass
        return e.code, corpo, dict(e.headers or {})
    except Exception as e:  # timeout, DNS, SSL...
        return 0, str(e).encode("utf-8", "ignore"), {}


def texto(url, tentativas=1, referer=None):
    ultimo = "?"
    for i in range(tentativas):
        status, dados, _ = http_get(url, referer=referer)
        if status == 200 and dados:
            for enc in ("utf-8", "latin-1"):
                try:
                    return dados.decode(enc)
                except UnicodeDecodeError:
                    continue
            return dados.decode("utf-8", "ignore")
        ultimo = f"HTTP {status}"
        if i + 1 < tentativas:
            time.sleep(2 + 2 * i)
    raise RuntimeError(f"Falha ao baixar {url} ({ultimo})")


def json_ou_none(url, tentativas=2):
    """Baixa e decodifica JSON. Devolve None se nao for JSON valido."""
    try:
        bruto = texto(url, tentativas)
        obj = json.loads(bruto)
        return obj if isinstance(obj, (dict, list)) else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Utilidades de texto
# ---------------------------------------------------------------------------

def limpar_entidades(s):
    if not s:
        return ""
    s = s.replace("&amp;", "&").replace("&quot;", '"').replace("&#39;", "'")
    s = s.replace("&apos;", "'").replace("&lt;", "<").replace("&gt;", ">")
    s = re.sub(r"&#x([0-9a-fA-F]+);", lambda m: chr(int(m.group(1), 16)), s)
    s = re.sub(r"&#(\d+);", lambda m: chr(int(m.group(1))), s)
    return s.strip()


def slug(s):
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower().strip()
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def normalizar_categoria(nome):
    nome = limpar_entidades(nome or "").strip()
    if not nome or slug(nome) in ("todos", "all", "todos os canais"):
        return None
    return SINONIMOS.get(slug(nome), nome)


def esc_m3u(s):
    return (s or "").replace("\r", " ").replace("\n", " ").strip()


def logo_util(link):
    """Descarta a logo generica do site (nao ajuda no SS IPTV)."""
    if not link:
        return ""
    link = urljoin(HOME_URL, limpar_entidades(link))
    if re.search(r"/(logo\.png)$", urlparse(link).path or ""):
        return ""
    return link


# ---------------------------------------------------------------------------
# Formas de leitura do site
# ---------------------------------------------------------------------------

def _attr(tag, nome):
    m = re.search(r'(?<![\w-])' + nome + r'\s*=\s*(?:"([^"]*)"|\'([^\']*)\')', tag, re.I | re.S)
    if not m:
        return ""
    return m.group(1) if m.group(1) is not None else (m.group(2) or "")


RE_IMG = re.compile(r"<img\b[^>]*>", re.I | re.S)
RE_IMG_CANAL = re.compile(r"/images/([A-Za-z0-9_-]+)\.(?:png|webp|jpe?g|svg)(?:\?|$)", re.I)
RE_ATTR_CANAL = re.compile(
    r"""(?:data-(?:canal|channel|slug)\s*=\s*["']([a-z][a-z0-9_-]*)["']|[?&]canal=([a-z][a-z0-9_-]*))""",
    re.I,
)
STEMS_IGNORAR = {"logo", "icon", "favicon", "og-default", "megacanais-icon",
                 "placeholder", "default", "escudo", "bg", "banner"}


def _atribuir_blocos(cards):
    """Da a cada card o bloco (categoria) da home, pela ordem de aparicao."""
    atual = "Esportes"
    for c in cards:
        atual = INICIO_DE_BLOCO.get(c["id"], atual)
        c["categorias"] = [atual]
    return cards


def _montar_card(canal, nome, logo):
    return {
        "id": canal,
        "nome": nome or canal,
        "logo": logo_util(logo),
        "link": "",
        "categorias": [],
        "player": EMBED_URL.format(canal=quote(canal)),
        "num": "",
        "ref": "",
    }


def forma_home_cards():
    """A) <img src=".../images/<canal>.png" alt="Nome"> na home."""
    html = texto(HOME_URL, tentativas=2)
    imgs = list(RE_IMG.finditer(html))
    cards, vistos = [], set()
    for i, m in enumerate(imgs):
        tag = m.group(0)
        src = limpar_entidades(_attr(tag, "src") or _attr(tag, "data-src"))
        mm = RE_IMG_CANAL.search(src)
        if not mm:
            continue
        stem = mm.group(1).lower()
        if stem in STEMS_IGNORAR:
            continue
        ini = imgs[i - 1].end() if i else 0
        fim = imgs[i + 1].start() if i + 1 < len(imgs) else len(html)
        trecho = html[max(ini, m.start() - 300):min(fim, m.end() + 300)]
        ma = RE_ATTR_CANAL.search(trecho)
        canal = (ma.group(1) or ma.group(2)).lower() if ma else stem
        if canal in vistos:
            continue
        vistos.add(canal)
        cards.append(_montar_card(canal, limpar_entidades(_attr(tag, "alt")), src))
    if len(cards) < 20:
        return None, f"a home trouxe só {len(cards)} cards de canal (esperado: dezenas)"
    cards = _atribuir_blocos(cards)
    return _finalizar_catalogo(cards)


def forma_home_script():
    """B) catalogo em JSON/JS dentro da pagina: "name":"X" ... images/x.png"""
    html = texto(HOME_URL, tentativas=2).replace("\\/", "/")
    cards, vistos = [], set()
    padrao = re.compile(
        r'"(?:name|nome|title)"\s*:\s*"([^"]{2,60})"[^{}]{0,300}?/images/([A-Za-z0-9_-]+)\.(?:png|webp|jpe?g)',
        re.I,
    )
    for nome, stem in padrao.findall(html):
        stem = stem.lower()
        if stem in STEMS_IGNORAR or stem in vistos:
            continue
        vistos.add(stem)
        cards.append(_montar_card(stem, limpar_entidades(nome), f"{BASE}/images/{stem}.png"))
    if len(cards) < 20:
        return None, f"não achei catálogo em JSON/JS na página ({len(cards)} itens)"
    return _finalizar_catalogo(_atribuir_blocos(cards))


def _finalizar_catalogo(cards):
    """Abre o player de cada canal e descobre o .m3u8 (em paralelo)."""
    def tarefa(c):
        return c, explorar_player(c["id"])["candidatos"]

    with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS_API) as ex:
        for c, cands in ex.map(tarefa, cards):
            c["candidatos"] = cands[:4]
            if cands:
                c["link"], c["ref"] = cands[0]

    if not any(c["link"] for c in cards):
        return None, ("os cards apareceram, mas nenhum player entregou um .m3u8 "
                      "(rode --sondar para ver o que o player devolve)")
    categorias = {}
    for c in cards:
        for n in c["categorias"]:
            categorias.setdefault(n, n)
    ordem = {n: i for i, n in enumerate(ORDEM_PADRAO)}
    categorias = dict(enumerate(sorted(categorias, key=lambda n: ordem.get(n, 99))))
    return (cards, categorias), ""


# ---------------------------------------------------------------------------
# Player -> .m3u8
# ---------------------------------------------------------------------------

_URL_FIM = r"""[^\s"'<>\\)\]\}]"""
RE_M3U8 = re.compile(r"https?://" + _URL_FIM + r"+?\.m3u8(?:\?" + _URL_FIM + r"*)?", re.I)
RE_M3U8_REL = re.compile(r"""["']((?:\.{0,2}/)?[\w./%-]+\.m3u8(?:\?[^"'\s]*)?)["']""", re.I)
RE_IFRAME = re.compile(r"""<iframe\b[^>]*?\bsrc\s*=\s*["']([^"']+)["']""", re.I | re.S)
RE_URL_JS = re.compile(r"""(?:src|file|source|url|embed|player)\s*[:=]\s*["'](https?://[^"']+|//[^"']+)["']""", re.I)
RE_ARQUIVO = re.compile(r"\.(?:png|jpe?g|gif|webp|css|js|svg|ico|woff2?|ttf|mp4|ts)(?:\?|$)", re.I)


def _blobs_base64(txt):
    saida = []
    for b in re.findall(r"""atob\(\s*["']([A-Za-z0-9+/=_-]{16,})["']\s*\)""", txt):
        try:
            b = b.replace("-", "+").replace("_", "/")
            saida.append(base64.b64decode(b + "=" * (-len(b) % 4)).decode("utf-8", "ignore"))
        except Exception:
            pass
    return saida


RE_SCRIPT_SRC = re.compile(r"""<script\b[^>]*?\bsrc\s*=\s*["']([^"']+)["']""", re.I | re.S)
CDNS_COMUNS = ("cdnjs.cloudflare.com", "cdn.jsdelivr.net", "unpkg.com", "googleapis.com",
               "gstatic.com", "jquery.com", "googletagmanager.com", "google-analytics.com",
               "cloudflareinsights.com", "fontawesome", "bootstrapcdn")


def explorar_player(canal, max_paginas=8):
    """Segue embed -> iframes -> scripts e junta tudo o que achar.

    Devolve dict: candidatos [(m3u8, origem_da_pagina)], paginas [(url, status,
    content_type, texto)], passos [str].
    """
    candidatos, paginas, passos = [], [], []
    fila = [(EMBED_URL.format(canal=quote(canal)), HOME_URL, False)]
    vistos = set()
    while fila and len(vistos) < max_paginas:
        url, ref, eh_script = fila.pop(0)
        if url in vistos:
            continue
        vistos.add(url)
        status, dados, hdrs = http_get(url, referer=ref)
        ctype = (hdrs.get("Content-Type") or hdrs.get("content-type") or "")
        passos.append(f"HTTP {status} {url}")
        corpo = dados.decode("utf-8", "ignore") if dados else ""
        paginas.append((url, status, ctype, corpo))
        if status != 200 or not corpo:
            continue
        html = corpo.replace("\\/", "/")
        textos = [html] + _blobs_base64(html)
        p = urlparse(url)
        origem = f"{p.scheme}://{p.netloc}/"
        for t in textos:
            for u in RE_M3U8.findall(t):
                if (u, origem) not in candidatos:
                    candidatos.append((u, origem))
            for u in RE_M3U8_REL.findall(t):
                u = urljoin(url, u)
                if (u, origem) not in candidatos:
                    candidatos.append((u, origem))
        if eh_script:
            continue
        for t in textos:
            for u in RE_IFRAME.findall(t) + RE_URL_JS.findall(t):
                u = urljoin(url, limpar_entidades(u))
                if u.startswith("http") and not RE_ARQUIVO.search(u):
                    fila.append((u, url, False))
        if not candidatos:
            # o .m3u8 pode ser montado por um .js da propria pagina
            for u in RE_SCRIPT_SRC.findall(html):
                u = urljoin(url, limpar_entidades(u))
                if u.startswith("http") and not any(d in u for d in CDNS_COMUNS):
                    fila.append((u, url, True))
    return {"candidatos": candidatos, "paginas": paginas, "passos": passos}


def resolver_canal(canal, max_paginas=8):
    """Compatibilidade: (primeiro_m3u8, origem, passos)."""
    r = explorar_player(canal, max_paginas)
    if r["candidatos"]:
        return r["candidatos"][0][0], r["candidatos"][0][1], r["passos"]
    return "", "", r["passos"]


def combinacoes_referer(origem):
    """Referers a testar: o da pagina que trazia o link, o do player, o do site, nenhum."""
    opcoes = [origem, "https://playerembedcanais.site/", HOME_URL, ""]
    vistos, saida = set(), []
    for o in opcoes:
        if o not in vistos:
            vistos.add(o)
            saida.append(o)
    return saida


def cabecalhos_do_link(canal):
    ref = canal.get("ref") or ""
    if not ref:
        return {}
    return {"Referer": ref, "Origin": ref.rstrip("/")}


# ---------------------------------------------------------------------------
# Memoria das formas do site
# ---------------------------------------------------------------------------

def carregar_fontes():
    try:
        with open(ARQ_FONTES, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {"versao": 1, "formas": {}, "ultima_que_funcionou": None, "historico": []}


def salvar_fontes(mem):
    mem["atualizado_em"] = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        with open(ARQ_FONTES, "w", encoding="utf-8") as f:
            json.dump(mem, f, ensure_ascii=False, indent=1)
    except Exception as e:
        log(f"  [aviso] não consegui salvar a memória das formas do site: {e}")


def ordem_tentativa(mem):
    """Ultima forma que funcionou primeiro; as demais na ordem padrao."""
    ultima = mem.get("ultima_que_funcionou")
    ordem = [f for f in FORMAS if f != ultima]
    if ultima in FORMAS:
        ordem.insert(0, ultima)
    return ordem


def registrar_sucesso(mem, forma, canais, categorias):
    registro = {
        "ultima_vez": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "canais": len(canais),
        "categorias": len(categorias),
        "exemplo": {"id": canais[0]["id"], "nome": canais[0]["nome"],
                    "link": canais[0]["link"][:160]} if canais else {},
        "assinatura": assinatura_site(canais),
    }
    mem.setdefault("formas", {})[forma] = registro
    hist = [h for h in mem.get("historico") or [] if h.get("forma") != forma]
    hist.insert(0, {"forma": forma, **{k: registro[k] for k in ("ultima_vez", "canais")}})
    mem["historico"] = hist[:20]
    if mem.get("ultima_que_funcionou") != forma:
        log(f"      [memória] forma de leitura atualizada para: {NOMES_FORMAS.get(forma, forma)}")
    mem["ultima_que_funcionou"] = forma


def assinatura_site(canais):
    """Marca rapida do catalogo (quantidade + hosts) para notar mudancas."""
    hosts = {}
    for c in canais:
        h = urlparse(c.get("link") or "").netloc
        if h:
            hosts[h] = hosts.get(h, 0) + 1
    top = sorted(hosts.items(), key=lambda kv: -kv[1])[:5]
    return {"total": len(canais), "hosts": dict(top)}


# ---------------------------------------------------------------------------
# Coleta
# ---------------------------------------------------------------------------

def coletar_catalogo(mem):
    ordem = ordem_tentativa(mem)
    log("[1/5] Lendo o catalogo do site...")
    log(f"      ordem de tentativa: {', '.join(ordem)}")
    problemas = []
    for forma in ordem:
        funcao = {"home_cards": forma_home_cards,
                  "home_script": forma_home_script}[forma]
        try:
            resultado, motivo = funcao()
        except Exception as e:
            resultado, motivo = None, f"{type(e).__name__}: {e}"
        if resultado:
            canais, categorias = resultado
            com_link = sum(1 for c in canais if c["link"].startswith("http"))
            log(f"      OK pela forma '{forma}' "
                f"({NOMES_FORMAS.get(forma, forma)}): "
                f"{len(canais)} canais, {com_link} com link")
            registrar_sucesso(mem, forma, canais, categorias)
            log(f"      categorias: {', '.join(categorias.values()) or '(nenhuma)'}")
            return canais, categorias
        log(f"      forma '{forma}' não serve: {motivo}")
        problemas.append(f"{forma}: {motivo}")

    raise RuntimeError(
        "Nenhuma das formas conhecidas do site funcionou.\n"
        + "\n".join("        - " + p for p in problemas)
        + "\n      A lista anterior foi preservada. Rode `python atualizar_m3u.py --sondar` "
          "para ver o que o site está devolvendo agora."
    )


# ---------------------------------------------------------------------------
# Teste de reproducao
# ---------------------------------------------------------------------------

def _ffprobe():
    return shutil.which("ffprobe")


def testar_http(url, cab=None):
    """Confere se o link responde e, se for HLS, se a lista de segmentos existe."""
    status, dados, _ = http_get(url, extra=cab)
    if status == 0:
        return False, "HTTP sem resposta"
    if status != 200:
        return False, f"HTTP {status}"
    if url.lower().split("?")[0].endswith(".m3u8") or b"#EXTM3U" in dados[:300]:
        if b"#EXTM3U" not in dados[:2000]:
            return False, "resposta não é HLS"
        return True, "HLS ok"
    return True, "HTTP 200"


def testar_video(url, cab=None):
    """Baixa um trecho real do stream e confirma que o video decodifica."""
    exe = _ffprobe()
    if not exe:
        return testar_http(url, cab)
    cmd = [exe, "-v", "error", "-rw_timeout", "20000000",
           "-analyzeduration", "6000000", "-probesize", "3000000",
           "-user_agent", USER_AGENT]
    if cab:
        cmd += ["-headers", "".join(f"{k}: {v}\r\n" for k, v in cab.items())]
    cmd += ["-select_streams", "v:0", "-show_entries", "stream=codec_type",
            "-of", "csv=p=0", url]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT_PROBE)
        saida = (p.stdout or "").strip().lower()
        if "video" in saida:
            return True, "video ok"
        if p.returncode == 0 and saida:
            return True, "stream ok"
        err = (p.stderr or "").strip().splitlines()
        return False, (err[-1][:80] if err else "sem video decodificavel")
    except subprocess.TimeoutExpired:
        return False, "tempo esgotado ao testar"
    except Exception as e:
        return False, str(e)[:80]


def testar_canal(canal):
    cands = canal.get("candidatos") or ([(canal["link"], canal.get("ref", ""))] if canal.get("link") else [])
    if not cands:
        return False, "sem link (o player não entregou .m3u8)"
    ultimo = "?"
    for url, origem in cands:
        for ref in combinacoes_referer(origem):
            cab = {"Referer": ref, "Origin": ref.rstrip("/")} if ref else {}
            ok, motivo = testar_http(url, cab)
            if ok:
                ok, motivo = testar_video(url, cab)
            if ok:
                canal["link"], canal["ref"] = url, ref
                if ref:
                    st, _, _ = http_get(url)  # toca sem Referer?
                    canal["precisa_referer"] = st != 200
                return True, motivo
            ultimo = f"{motivo} [{url[:70]}]"
    return False, ultimo


# ---------------------------------------------------------------------------
# Estado / reconciliacao
# ---------------------------------------------------------------------------

def hoje():
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M")


def carregar_estado():
    try:
        with open(ARQ_ESTADO, encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict) and "canais" in d:
            return d
    except Exception:
        pass
    return {"versao": 1, "atualizacoes": 0, "limite_falhas": LIMITE_FALHAS, "canais": {}}


def salvar_json(caminho, obj):
    with open(caminho, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)


def agrupar(canais, ordem_site):
    grupos = {}
    for c in canais:
        cats = c.get("categorias") or []
        especificas = [x for x in cats if x in ordem_site] or cats
        cat = (especificas or ["Outros"])[0]
        grupos.setdefault(cat, []).append(c)
    ordem = [c for c in ordem_site if c in grupos]
    for cat in grupos:
        if cat not in ordem and cat != "Outros":
            ordem.append(cat)
    if "Outros" in grupos:
        ordem.append("Outros")
    return ordem, grupos


def gerar_m3u(canais, categorias_site, caminho, completo=False):
    canais = [c for c in canais if (c.get("link") or "").startswith("http")]
    ordem, grupos = agrupar(canais, categorias_site)
    linhas = ["#EXTM3U"]
    for cat in ordem:
        for c in grupos[cat]:
            nome = esc_m3u(c["nome"]) or c["id"]
            attrs = [f'tvg-id="{esc_m3u(c["id"])}"', f'tvg-name="{nome.lower()}"']
            if c.get("logo"):
                attrs.append(f'tvg-logo="{esc_m3u(c["logo"])}"')
            attrs.append(f'group-title="{esc_m3u(cat)}"')
            if completo:
                attrs.append('content-type="video"')
            linhas.append(f"#EXTINF:-1 {' '.join(attrs)},{nome}")
            if c.get("ref"):
                # o CDN do player espera o Referer do site que o embute
                linhas.append(f"#EXTVLCOPT:http-referrer={c['ref']}")
                linhas.append(f"#EXTVLCOPT:http-user-agent={USER_AGENT}")
            linhas.append(c["link"])
    with open(caminho, "w", encoding="utf-8") as f:
        f.write("\n".join(linhas) + "\n")
    return len(canais)


ARQ_DIAG = os.path.join(RAIZ, "diagnostico_player.txt")


def escrever_diagnostico(canais_ids, titulo=""):
    """Grava em diagnostico_player.txt TUDO o que os players devolvem.

    Serve para entender por que os links nao tocam: paginas seguidas, HTML do
    player, scripts, candidatos a .m3u8 e a resposta de cada um.
    """
    out = [f"DIAGNOSTICO DOS PLAYERS {titulo}  {dt.datetime.now().strftime('%d/%m/%Y %H:%M:%S')}", ""]
    for cid in canais_ids:
        out.append("=" * 78)
        out.append(f"CANAL: {cid}")
        r = explorar_player(cid)
        for url, status, ctype, corpo in r["paginas"]:
            out.append("-" * 78)
            out.append(f"PAGINA: {url}\nstatus={status} tipo={ctype} tamanho={len(corpo)}")
            out.append(corpo[:7000] + ("\n...[cortado]" if len(corpo) > 7000 else ""))
        out.append("-" * 78)
        out.append(f"CANDIDATOS .m3u8: {len(r['candidatos'])}")
        for url, origem in r["candidatos"][:4]:
            out.append(f"* {url}   (pagina de origem: {origem})")
            for ref in combinacoes_referer(origem):
                cab = {"Referer": ref, "Origin": ref.rstrip("/")} if ref else {}
                st, corpo, hd = http_get(url, extra=cab)
                amostra = corpo[:160].decode("utf-8", "ignore").replace("\n", " | ")
                out.append(f"    referer={ref or '(nenhum)'} -> HTTP {st} :: {amostra}")
    with open(ARQ_DIAG, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")
    return ARQ_DIAG


def sondar(canal, mem):
    """Diagnostico: o que a home e o player de UM canal devolvem agora."""
    log(f"[home] {HOME_URL}")
    status, dados, hdrs = http_get(HOME_URL)
    log(f"  HTTP {status}, {len(dados)} bytes, server={hdrs.get('Server') or hdrs.get('server') or '?'}")
    if status != 200:
        log("  (a home não abriu: se for 403/503 é bloqueio/Cloudflare para este IP)")
    for forma, fn in (("home_cards", forma_home_cards_so_leitura),):
        try:
            cards, motivo = fn()
        except Exception as e:
            cards, motivo = None, f"{type(e).__name__}: {e}"
        log(f"  [{'OK' if cards else 'FALHA'}] {forma}: "
            f"{len(cards) if cards else motivo} cards")
        if cards:
            log("  primeiros cards: " + ", ".join(f"{c['id']}={c['nome']}" for c in cards[:6]))
    log(f"[player] canal '{canal}': {EMBED_URL.format(canal=quote(canal))}")
    link, origem, passos = resolver_canal(canal)
    for p in passos:
        log("  " + p)
    if not link:
        log("  [FALHA] nenhum .m3u8 encontrado nas páginas acima")
        log("  -> cole esta saída no chat para ajustar o leitor do player")
    escrever_diagnostico([canal], "(--sondar)")
    log(f"  diagnóstico completo gravado em: {os.path.basename(ARQ_DIAG)} (envie este arquivo no chat)")
    if not link:
        return
    log(f"  [OK] m3u8: {link}")
    log(f"  origem (Referer/Origin): {origem}")
    s1, _, _ = http_get(link)
    s2, corpo, _ = http_get(link, extra={"Referer": origem, "Origin": origem.rstrip("/")})
    log(f"  sem Referer: HTTP {s1} | com Referer: HTTP {s2}")
    log(f"memória atual: {mem.get('ultima_que_funcionou') or '(vazia)'}")


def forma_home_cards_so_leitura():
    """Le a home sem abrir os players (rapido, para o --sondar)."""
    html = texto(HOME_URL, tentativas=2)
    cards, vistos = [], set()
    for m in RE_IMG.finditer(html):
        mm = RE_IMG_CANAL.search(limpar_entidades(_attr(m.group(0), "src") or _attr(m.group(0), "data-src")))
        if not mm or mm.group(1).lower() in STEMS_IGNORAR or mm.group(1).lower() in vistos:
            continue
        vistos.add(mm.group(1).lower())
        cards.append({"id": mm.group(1).lower(), "nome": limpar_entidades(_attr(m.group(0), "alt"))})
    return (cards or None), f"{len(cards)}"


def main():
    global LIMITE_FALHAS
    ap = argparse.ArgumentParser(description="Mega Canais -> M3U")
    ap.add_argument("--sem-testar", action="store_true")
    ap.add_argument("--iniciar-estado", action="store_true")
    ap.add_argument("--sondar", action="store_true", help="só diagnostica o site")
    ap.add_argument("--canal", default=CANAL_SONDA, help="canal usado no --sondar")
    ap.add_argument("--limite-falhas", type=int, default=LIMITE_FALHAS)
    args = ap.parse_args()

    LIMITE_FALHAS = args.limite_falhas

    log("=" * 70)
    log(f" Mega Canais -> M3U  |  {dt.datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
    log("=" * 70)

    mem = carregar_fontes()

    if args.sondar:
        sondar(args.canal, mem)
        return 0

    estado = carregar_estado()
    if args.iniciar_estado:
        for reg in estado["canais"].values():
            reg["falhas"] = 0
        log("[i] contadores de falha zerados")
    estado["limite_falhas"] = LIMITE_FALHAS

    try:
        canais, categorias = coletar_catalogo(mem)
    except RuntimeError as e:
        salvar_fontes(mem)
        log()
        log(f"[ERRO] {e}")
        log("A lista anterior (se existir) foi preservada.")
        with open(ARQ_LOG, "w", encoding="utf-8") as f:
            f.write("\n".join(LOG_LINHAS) + "\n")
        return 1

    salvar_fontes(mem)

    # ---- teste de reproducao -------------------------------------------------
    em_falha, removidos, novos = [], [], []
    if args.sem_testar:
        log("[4/5] Teste de transmissão ignorado (--sem-testar)")
        resultados = {c["id"]: (True, "nao testado") for c in canais}
    else:
        log(f"[4/5] Validando transmissões (atualização #{estado.get('atualizacoes', 0) + 1})...")
        log(f"      ffprobe disponível: {'sim' if _ffprobe() else 'não'}")
        resultados = {}
        feito = [0]
        total = len(canais)

        def tarefa(c):
            r = testar_canal(c)
            feito[0] += 1
            if feito[0] % 50 == 0 or feito[0] == total:
                log(f"      {feito[0]}/{total} testados")
            return c["id"], r

        with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS_PROBE) as ex:
            for cid, r in ex.map(tarefa, canais):
                resultados[cid] = r

    # ---- reconciliacao -------------------------------------------------------
    log("[5/5] Reconciliando com a lista anterior...")
    estado["atualizacoes"] = estado.get("atualizacoes", 0) + 1
    registros = estado["canais"]
    ids_site = {c["id"] for c in canais}
    na_lista = []

    for c in canais:
        ok, motivo = resultados.get(c["id"], (False, "não testado"))
        reg = registros.get(c["id"])
        if reg is None:
            reg = {"nome": c["nome"], "falhas": 0, "primeira_vez": hoje(),
                   "historico": []}
            registros[c["id"]] = reg
            novos.append(c["nome"])
        reg["nome"] = c["nome"]
        reg["link"] = c["link"]
        if ok:
            if reg.get("falhas"):
                reg["historico"].append({"em": hoje(), "evento": "voltou a tocar"})
            reg["falhas"] = 0
            reg["ultima_ok"] = hoje()
            c["valor"] = c["id"]
            c["_motivo"] = motivo
            na_lista.append(c)
        else:
            reg["falhas"] = reg.get("falhas", 0) + 1
            reg["ultima_falha"] = hoje()
            reg["historico"].append({"em": hoje(), "evento": "falha", "motivo": motivo})
            reg["historico"] = reg["historico"][-10:]
            em_falha.append({"nome": c["nome"], "motivo": motivo})
            if reg["falhas"] >= LIMITE_FALHAS:
                removidos.append({"nome": c["nome"], "falhas": reg["falhas"]})
            else:
                c["_motivo"] = motivo
                na_lista.append(c)

    fora = [i for i in registros if i not in ids_site]
    for cid in fora:
        del registros[cid]

    # ---- arquivos ------------------------------------------------------------
    ordem_site = [c for c in ORDEM_PADRAO]
    n_lista = gerar_m3u(na_lista, ordem_site, ARQ_LISTA)
    todos = canais
    gerar_m3u(todos, ordem_site, ARQ_LISTA_COMPLETA, completo=True)

    ordem, grupos = agrupar(na_lista, ordem_site)
    relatorio = {
        "gerado_em": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "atualizacao": estado["atualizacoes"],
        "limite_falhas": LIMITE_FALHAS,
        "forma_site": mem.get("ultima_que_funcionou"),
        "catalogo_site": len(canais),
        "com_link": sum(1 for c in canais if c["link"].startswith("http")),
        "precisam_referer": sum(1 for c in canais if c.get("precisa_referer")),
        "na_lista": n_lista,
        "tocando": len(na_lista) - len(em_falha),
        "em_falha": len(em_falha),
        "removidos": len(removidos),
        "novos": len(novos),
        "fora_do_site": len(fora),
        "por_categoria": {k: len(grupos[k]) for k in ordem},
        "detalhes_falha": sorted(em_falha, key=lambda x: x["nome"])[:300],
        "removidos_lista": removidos,
        "assinatura_site": assinatura_site(canais),
    }
    salvar_json(ARQ_RELATORIO, relatorio)
    salvar_json(ARQ_ESTADO, estado)

    log("-" * 70)
    log(f"Forma usada no site ......: {NOMES_FORMAS.get(mem.get('ultima_que_funcionou'), '?')}")
    log(f"Canais no site ............: {len(canais)}")
    log(f"Com link direto ...........: {relatorio['com_link']}")
    log(f"Exigem Referer (sem ele 403): {relatorio['precisam_referer']}")
    log(f"TOCANDO (na lista) ........: {relatorio['tocando']}")
    log(f"Em falha (mantidos) .......: {len(em_falha)}  (removidos após {LIMITE_FALHAS} falhas)")
    log(f"Removidos nesta rodada ....: {len(removidos)}")
    log(f"Novos canais ..............: {len(novos)}")
    log(f"Fora do site (limpo) ......: {len(fora)}")
    log(f"Total na lista ............: {n_lista} canais")
    if em_falha:
        motivos = {}
        for f_ in em_falha:
            chave = re.sub(r"\[http[^\]]*\]", "", f_["motivo"]).strip()[:70]
            motivos[chave] = motivos.get(chave, 0) + 1
        log("Motivos das falhas (mais comuns):")
        for m_, n_ in sorted(motivos.items(), key=lambda kv: -kv[1])[:5]:
            log(f"   {n_:>4}x  {m_}")
    if canais and relatorio["tocando"] < max(3, len(canais) // 5):
        amostra = [c["id"] for c in canais if c["id"] == CANAL_SONDA][:1]
        amostra += [c["id"] for c in canais if c["id"] not in amostra][:2]
        escrever_diagnostico(amostra, "(automatico: poucos canais tocando)")
        log(f"Poucos canais tocando -> gravei {os.path.basename(ARQ_DIAG)} com o que os players devolvem.")
    else:
        with open(ARQ_DIAG, "w", encoding="utf-8") as f_:
            f_.write("Sem diagnóstico: os canais estão tocando normalmente.\n")
    log("-" * 70)
    log("Canais por categoria:")
    for cat in ordem:
        log(f"   {cat:<22}{len(grupos[cat]):>4}")
    log("-" * 70)
    log(f"Arquivo: {os.path.basename(ARQ_LISTA)}  ({n_lista} canais)")

    with open(ARQ_LOG, "w", encoding="utf-8") as f:
        f.write("\n".join(LOG_LINHAS) + "\n")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        log("\n[!] interrompido")
        sys.exit(130)
