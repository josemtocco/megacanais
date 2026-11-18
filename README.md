# Gerador de Playlist M3U Otimizada para SS IPTV

Este projeto gera e mantém automaticamente uma playlist M3U, com as seguintes características:
- ✅ **Atualização Automática**: Executa a cada 6 horas via GitHub Actions.
- ✅ **Tolerância a Falhas**: Um canal só é removido após **6 verificações consecutivas falhas** (totalizando 36 horas de inatividade), evitando remoções por oscilações temporárias de rede.
- ✅ **Validação Real de Links**: Testa se o link responde (HTTP 200/206) antes de considerá-lo ativo.
- ✅ **Otimizado para SS IPTV**: Usa `group-title` para separar as categorias exatamente como no site de origem e `tvg-logo` para as capas.
- ✅ **Acréscimo Automático**: Novos canais encontrados são adicionados automaticamente com contagem de falhas zerada.

## Como usar no SS IPTV
1. Obtenha o **Raw Link** do arquivo `playlist.m3u` deste repositório (clique no arquivo -> botão "Raw" -> copie a URL).
2. No SS IPTV, vá em **Configurações** -> **Geral** -> **Adicionar** (ou Carregar).
3. Cole a URL e dê um nome (ex: "Minha Lista").
4. Recarregue a lista na aba "Conteúdo".

## Nota Técnica sobre Fontes de Dados
O script utiliza links de teste públicos válidos para demonstrar a funcionalidade de validação. Para adaptar a um site específico, modifique a função `scrape_channels()` no arquivo `scraper.py`.