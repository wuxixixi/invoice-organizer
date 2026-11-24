@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ============================================
echo  打包 Windows 单文件 exe：发票整理助手
echo ============================================

python -m PyInstaller build.spec --noconfirm --clean
if errorlevel 1 (
    echo [失败] PyInstaller 打包失败，请查看上方日志。
    exit /b 1
)

echo.
echo [完成] 产物位置：dist\发票整理助手.exe
echo 运行时会把 data\（配置、数据库、日志、密钥文件）放在 exe 同目录。
pause
