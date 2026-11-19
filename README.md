# Mega Canais → Lista M3U (otimizada para SS IPTV)

Gera, a cada 6 horas, uma lista M3U com os canais de **megacanais.online**,
agrupados nos mesmos blocos da home do site.

> Os arquivos agora se chamam `megacanais.m3u` e `megacanais_completo.m3u`.
> No SS IPTV, aponte para o link **raw** do `megacanais.m3u`.

---

## Como o script lê o site

1. **Home** (`https://www.megacanais.online/`): cada canal é um card com a logo
   em `/images/<canal>.png` (ex.: `premiere7`) e o nome no `alt`.
2. **Player** de cada canal: `https://embedcanaisdetv.xyz/e/index.php?canal=<canal>`.
   O script abre essa página, segue os iframes e procura o `.m3u8`.
3. O CDN do vídeo (`cdn8embed.xyz`) recebeu, no HAR, `Referer`/`Origin` do
   player. Por isso o teste de cada canal usa esses cabecalhos, e a lista
   ganha `#EXTVLCOPT:http-referrer=` nos canais que precisam (players que não
   suportam cabeçalhos ignoram a linha).

Blocos da home (um canal novo herda o bloco do canal que vem antes dele):
Esportes · Abertos · Notícias · Filmes e Séries · Variedades · Infantil · Religiosos.

A forma de leitura que funcionou fica em `fontes_site.json`. Se a home mudar,
o script tenta a outra forma (`home_script`); se nenhuma funcionar, **nada é
publicado** e a lista anterior fica intacta.

---

## Regras de atualização

1. **Canal tocando** → continua na lista.
2. **Canal com falha** (ou sem `.m3u8`) → ganha +1 falha e permanece na lista
   (canal sem link não é escrito no arquivo, mas o contador continua).
3. **Só é removido após 6 atualizações consecutivas com falha.**
4. Se voltar a tocar antes disso, o contador **zera**.
5. **Canal novo** no site → entra automaticamente.

---

## Diagnóstico

```
python atualizar_m3u.py --sondar
python atualizar_m3u.py --sondar --canal premiere7
```

Mostra o status da home, quantos cards foram achados e, para um canal, cada
página seguida até o `.m3u8`, e se o link toca **com e sem** Referer.
`relatorio.json` traz `precisam_referer`: quantos canais dão 403 sem Referer
(esses podem não tocar em players que não enviam cabeçalhos).

---

## Arquivos

| Arquivo | O que é |
|---|---|
| `megacanais.m3u` | **a lista que você usa** (canais tocando + os em falha com link) |
| `megacanais_completo.m3u` | todos os canais com link, sem filtro |
| `estado_canais.json` | contador de falhas e histórico de cada canal |
| `fontes_site.json` | memória da forma de leitura que funcionou |
| `relatorio.json` | resumo da última execução |
| `log_ultima_execucao.txt` | o log completo da última execução |

---

## Como rodar

- **Windows:** `rodar_no_brasil.bat` (usa o seu IP e publica no GitHub se a pasta for um repositório).
- **GitHub Actions:** roda sozinho a cada 6 horas ou pelo botão **Run workflow**.
  Se o site (que usa Cloudflare) bloquear o IP da nuvem, rode o `.bat` no Brasil.

Só usa a biblioteca padrão do Python. Usa `ffprobe` (ffmpeg) se existir, para o
teste de vídeo completo.
