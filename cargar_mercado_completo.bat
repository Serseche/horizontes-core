@echo off
REM ============================================================
REM  HORIZONTES — Carga completa del mercado
REM  Doble click para ejecutar. Lee credenciales del .env.
REM ============================================================

cd /d "%~dp0"
call .venv\Scripts\activate.bat

echo.
echo  Cargando mercado USA...
python warmup_cache.py NVDA MSFT AAPL GOOGL AMZN META BRK-B JPM V XOM COST TSLA AMD INTC NFLX DIS WMT JNJ PG KO PEP UNH BAC GS MS PYPL ADBE CRM ORCL QCOM TXN AVGO MU SBUX MCD NKE

echo.
echo  Cargando ETFs...
python warmup_cache.py SPY QQQ VTI IWM GLD TLT VNQ EEM

echo.
echo  Cargando CEDEARs argentinos...
python warmup_cache.py AAPL.BA MSFT.BA GOOGL.BA AMZN.BA NVDA.BA META.BA TSLA.BA

echo.
echo  ============================================
echo   Mercado completo cargado. Abri el dashboard
echo   y busca cualquier ticker de la lista.
echo  ============================================
pause
