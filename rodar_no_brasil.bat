@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
title Mega Canais -> M3U

echo ======================================================================
echo  Mega Canais -^> M3U   ^|  gerando a lista com o seu IP
echo ======================================================================
echo.

where python >nul 2>&1
if errorlevel 1 (
  echo [ERRO] Python nao encontrado. Instale em https://python.org
  echo        e marque a opcao "Add Python to PATH".
  pause
  exit /b 1
)

rem ffprobe (opcional, mas melhora o teste): procure na pasta C:\ffmpeg
if exist "C:\ffmpeg\bin\ffprobe.exe" set "PATH=C:\ffmpeg\bin;%PATH%"
where ffprobe >nul 2>&1
if errorlevel 1 (
  echo [AVISO] ffprobe nao encontrado - o teste sera mais simples.
  echo         Instale o ffmpeg em C:\ffmpeg para um teste completo.
  echo.
) else (
  echo ffprobe encontrado: teste de video completo ativado.
  echo.
)

echo Gerando e testando a lista... isso leva cerca de 1-2 minutos.
echo.
python atualizar_m3u.py
set CODIGO=%ERRORLEVEL%
echo.

if %CODIGO% NEQ 0 (
  echo [ERRO] A atualizacao terminou com problema. Veja a mensagem acima.
  echo        A lista anterior, se existir, foi mantida.
  pause
  exit /b %CODIGO%
)

where git >nul 2>&1
if errorlevel 1 (
  echo Lista gerada com sucesso: megacanais.m3u
  echo [AVISO] Git nao encontrado - nada foi enviado ao GitHub.
  pause
  exit /b 0
)

if not exist ".git" (
  echo Lista gerada com sucesso: megacanais.m3u
  echo [AVISO] Esta pasta nao e um repositorio Git - nada foi enviado.
  pause
  exit /b 0
)

echo Enviando a lista para o GitHub...
git add -A -- megacanais.m3u megacanais_completo.m3u estado_canais.json relatorio.json fontes_site.json log_ultima_execucao.txt diagnostico_player.txt
git diff --cached --quiet
if not errorlevel 1 (
  echo Nada mudou na lista.
  echo.
  pause
  exit /b 0
)
git commit -m "Lista atualizada (execucao local, %DATE% %TIME%)"
git pull --no-rebase -X ours --no-edit
git push
echo.
echo Pronto! Lista publicada no GitHub.
pause
