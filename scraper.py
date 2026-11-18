import os
import json
import requests
from datetime import datetime

# Configurações
STATE_FILE = "channels_state.json"
OUTPUT_FILE = "playlist.m3u"
MAX_FAILURES = 6  # Remove apenas após 6 atualizações falhas (6 x 6h = 36 horas)

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}

def save_state(state):
    with open(STATE_FILE, 'w', encoding='utf-8') as f:
        json.dump(state, f, indent=2, ensure_ascii=False)

def check_link_active(url):
    """Verifica se o link está respondendo (tocando) sem baixar o stream inteiro."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36",
        "Referer": "https://www.google.com/"
    }
    try:
        # GET com stream=True evita baixar o arquivo inteiro, checa apenas o cabeçalho HTTP
        response = requests.get(url, headers=headers, timeout=5, stream=True)
        if response.status_code in [200, 206]:
            return True
        return False
    except requests.exceptions.RequestException:
        return False
    finally:
        try:
            response.close()
        except:
            pass

def scrape_channels():
    """
    Coleta de canais. 
    NOTA: Substitua esta função pelo seu scraper real se encontrar uma fonte com links .m3u8 diretos.
    Para sites com botão "Carregar mais", recomenda-se usar a biblioteca 'playwright' (exemplo nos comentários).
    """
    # Dados simulados representando a estrutura de categorias do site solicitado
    # Inclui links de teste válidos (Mux) e um link inválido para testar a regra de 6 falhas.
    return [
        {"name": "ESPN Brasil", "category": "Esportes", "url": "https://test-streams.mux.dev/x36xhzz/x36xhzz.m3u8", "logo": "https://upload.wikimedia.org/wikipedia/commons/2/2f/ESPN_logo.svg"},
        {"name": "SporTV", "category": "Esportes", "url": "https://test-streams.mux.dev/test_001/stream.m3u8", "logo": "https://upload.wikimedia.org/wikipedia/pt/3/3f/Sportv_logo_2014.svg"},
        {"name": "Globo", "category": "Abertos", "url": "https://test-streams.mux.dev/x36xhzz/x36xhzz.m3u8", "logo": "https://upload.wikimedia.org/wikipedia/commons/3/3f/Logo_Globo_2021.svg"},
        {"name": "Canal Inativo (Teste de Remoção)", "category": "Filmes", "url": "http://link-que-vaifalhar.com/stream.m3u8", "logo": ""}
    ]

def main():
    state = load_state()
    scraped_channels = scrape_channels()
    current_urls = set()
    
    # 1. Processar canais encontrados na varredura
    for ch in scraped_channels:
        url = ch["url"]
        current_urls.add(url)
        
        if url not in state:
            # Novo canal encontrado: adiciona com contagem de falhas zero
            state[url] = {
                "name": ch["name"],
                "category": ch["category"],
                "logo": ch["logo"],
                "failure_count": 0,
                "last_seen": datetime.now().isoformat()
            }
        else:
            # Canal existente: atualiza nome/categoria/logo se houver mudanças
            state[url]["name"] = ch["name"]
            state[url]["category"] = ch["category"]
            state[url]["logo"] = ch["logo"]
            state[url]["last_seen"] = datetime.now().isoformat()

    # 2. Verificar status e atualizar contagem de falhas
    for url, data in list(state.items()):
        if url not in current_urls:
            # Canal não foi encontrado na varredura de hoje
            data["failure_count"] += 1
        else:
            # Canal foi encontrado, mas vamos testar se o link realmente responde (toca)
            is_active = check_link_active(url)
            if not is_active:
                data["failure_count"] += 1
            else:
                data["failure_count"] = 0 # Reseta falhas se estiver tocando

    # 3. Remover canais que excederam o limite de 6 falhas consecutivas
    urls_to_remove = [url for url, data in state.items() if data["failure_count"] >= MAX_FAILURES]
    for url in urls_to_remove:
        del state[url]
        
    save_state(state)
    generate_m3u(state)
    print(f"Processo concluído. {len(state)} canais ativos mantidos na playlist.")

def generate_m3u(state):
    """Gera a playlist M3U otimizada para SS IPTV."""
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        f.write('#EXTM3U url-tvg="" x-tvg-url=""\n')
        
        # Agrupar por categoria para melhor organização no SS IPTV
        categories = {}
        for url, data in state.items():
            cat = data.get("category", "Geral")
            if cat not in categories:
                categories[cat] = []
            categories[cat].append((url, data))
            
        for cat, channels in categories.items():
            for url, data in channels:
                logo = data.get("logo", "")
                name = data.get("name", "Canal Desconhecido")
                # Formato otimizado para SS IPTV (group-title cria as pastas/categorias)
                f.write(f'#EXTINF:-1 tvg-logo="{logo}" group-title="{cat}", {name}\n')
                f.write(f'{url}\n')

if __name__ == "__main__":
    main()

"""
EXEMPLO DE SCRAPER REAL COM PLAYWRIGHT (Para sites com botão "Carregar Mais"):
Instale: pip install playwright && playwright install chromium

from playwright.sync_api import sync_playwright

def scrape_channels_real():
    channels = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto("https://exemplo-de-site.com/categoria/esportes")
        
        # Clica no botão "Carregar mais" até não existir mais
        while True:
            try:
                page.wait_for_selector('.botao-carregar-mais', timeout=3000)
                page.click('.botao-carregar-mais')
                page.wait_for_timeout(2000) # Aguarda carregar
            except:
                break # Não há mais botões, sai do loop
                
        # Extrai os dados
        elements = page.query_selector_all('.item-do-canal')
        for el in elements:
            name = el.query_selector('.nome').inner_text()
            url = el.query_selector('a').get_attribute('href') 
            channels.append({"name": name, "category": "Esportes", "url": url, "logo": ""})
        browser.close()
    return channels
"""