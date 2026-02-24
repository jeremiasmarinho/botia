@echo off
echo ============================================
echo   Parando VMware NAT Service...
echo ============================================
net stop "VMware NAT Service"
echo.
echo ============================================
echo   Desabilitando VMware NAT Service (auto)
echo ============================================
sc config "VMware NAT Service" start=demand
echo.
echo ============================================
echo   Status:
echo ============================================
sc query "VMware NAT Service" | findstr STATE
echo.
echo ============================================
echo   PRONTO! Agora:
echo   1. Feche o LDPlayer completamente
echo   2. Reabra o LDPlayer
echo   3. Abra o PPPoker e teste a conexao
echo ============================================
pause
